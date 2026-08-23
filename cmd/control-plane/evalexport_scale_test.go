package main

// Does the server-side export survive soak volume, and finish inside the
// deadline the command grants it?
//
// WHY THIS EXISTS. The in-memory path was never measured at scale before being
// run for 24 hours -- four times. It OOM-killed the control-plane pod at ~952k
// events, and the failure was invisible until the record came out empty. The
// replacement must not be adopted on the same evidence the original had, which
// was none: "it worked on a 10-minute smoke" is precisely the reasoning that
// produced four invalid sittings.
//
// So this probe writes a soak's worth of rows and times the export. It is
// opt-in because it inserts a million rows and takes tens of seconds, which
// does not belong in `go test ./...`:
//
//	POLYFORGE_TEST_POSTGRES_SCALE=1 go test ./cmd/control-plane/ -run Scale -v
//
// The number it prints is the one that decides whether the 24 h sitting can be
// exported at all, so it belongs in the record, not in a memory.

import (
	"context"
	"fmt"
	"math/rand"
	"os"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"

	postgresstore "polyforge/internal/storage/postgres"
	"polyforge/internal/tenant"
)

// evalScaleEvents is a fifth of a 24 h sitting (attempt 4 recorded ~25M events
// over 86,705 s). Sized so the probe stays under a minute while still being
// five times the volume that killed the in-memory path.
const evalScaleEvents = 5_000_000

// evalScaleBudget is whatever the command itself grants the export, read from
// the same constant so the probe and the deadline cannot drift apart. A run
// that cannot be exported inside it is a run with no metrics, which is the
// state attempt 4 ended in.
const evalScaleBudget = evalExportTimeout

// evalScaleCeiling is a stated throughput floor. The budget gate alone stops
// discriminating once the budget is generous, and a probe that cannot fail is
// the defect this whole work package exists to remove. Measured 11.6 s per
// million on the local fixture; 30 leaves 2.6x for slower hardware while still
// catching an order-of-magnitude regression.
const evalScaleCeiling = 30.0

func TestEvalExportPostgresScalesToSoakVolume(t *testing.T) {
	if os.Getenv("POLYFORGE_TEST_POSTGRES_SCALE") == "" {
		t.Skip("set POLYFORGE_TEST_POSTGRES_SCALE=1 to run the soak-volume probe")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Minute)
	defer cancel()

	adminURL, appURL := evalParityDatabase(t, ctx)
	store, err := postgresstore.Open(ctx, postgresstore.Config{AdminURL: adminURL, AppURL: appURL})
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	defer store.Close()
	if err := store.Migrate(ctx); err != nil {
		t.Fatalf("migrate: %v", err)
	}

	const tenants = 16
	ids := make([]string, 0, tenants)
	for i := 0; i < tenants; i++ {
		id := fmt.Sprintf("pf-scale-%02d", i)
		plan := []string{"premium", "standard", "best-effort"}[i%3]
		if _, err := store.CreateTenant(ctx, tenant.Tenant{ID: id, Name: id, Plan: plan}); err != nil {
			t.Fatalf("create tenant %s: %v", id, err)
		}
		ids = append(ids, id)
	}

	loaded := time.Now()
	evalScaleLoad(t, ctx, adminURL, ids, evalScaleEvents)
	t.Logf("loaded %d events in %s", evalScaleEvents, time.Since(loaded).Round(time.Millisecond))

	t.Setenv("POLYFORGE_POSTGRES_APP_URL", appURL)
	budget, cancelBudget := context.WithTimeout(ctx, evalScaleBudget)
	defer cancelBudget()

	// 3600 is the soak's bucket width: 24 hourly windows, the granularity
	// SK-H3 is frozen on.
	started := time.Now()
	doc, err := evalAggregatePostgres(budget, adminURL, 3600)
	elapsed := time.Since(started)
	if err != nil {
		t.Fatalf("export of %d events failed after %s: %v", evalScaleEvents, elapsed.Round(time.Millisecond), err)
	}
	if doc.NEvents != evalScaleEvents {
		t.Fatalf("export scored %d events, want %d", doc.NEvents, evalScaleEvents)
	}
	if len(doc.Buckets) == 0 {
		t.Fatal("export produced no buckets: the soak would again have no time-resolved data")
	}

	perMillion := elapsed.Seconds() / (float64(evalScaleEvents) / 1e6)
	t.Logf("exported %d events in %s (%.1f s per million, %d buckets); "+
		"a 25M-event sitting extrapolates to ~%.0f s against a %s budget",
		evalScaleEvents, elapsed.Round(time.Millisecond), perMillion, len(doc.Buckets),
		perMillion*25, evalScaleBudget)

	// The extrapolation is the actual gate. Passing at 5M while implying a
	// 24 h run cannot be exported is a pass that means nothing.
	if projected := time.Duration(perMillion*25) * time.Second; projected > evalScaleBudget {
		t.Fatalf("a 25M-event sitting projects to %s, over the %s the command allows: "+
			"the soak would produce no metrics", projected.Round(time.Second), evalScaleBudget)
	}
	if perMillion > evalScaleCeiling {
		t.Fatalf("export cost %.1f s per million events, over the stated %.1f: "+
			"the aggregation has regressed even though it still fits the budget",
			perMillion, evalScaleCeiling)
	}
}

// evalScaleLoad writes events through COPY. Store.Add is a transaction per row
// and would take hours at this volume; the rows only have to be representative,
// not individually meaningful.
func evalScaleLoad(t *testing.T, ctx context.Context, adminURL string, ids []string, n int) {
	t.Helper()
	conn, err := pgx.Connect(ctx, adminURL)
	if err != nil {
		t.Fatalf("connect for COPY: %v", err)
	}
	defer func() { _ = conn.Close(context.Background()) }()

	base := time.Date(2026, 8, 23, 0, 0, 0, 0, time.UTC)
	// 24 h of wall clock spread across the rows, so the hourly buckets the
	// soak reads are actually populated.
	span := 24 * time.Hour
	rng := rand.New(rand.NewSource(20260823))
	tiers := []string{"", "", "", "small", "mid", "large"}

	row := 0
	src := pgx.CopyFromFunc(func() ([]any, error) {
		if row >= n {
			return nil, nil
		}
		i := row
		row++
		tier := tiers[i%len(tiers)]
		// A heavy tail, so the percentile is computed over a real distribution
		// rather than a constant that any implementation gets right.
		latency := 5.0 + rng.ExpFloat64()*20
		if i%1000 == 0 {
			latency += 900
		}
		return []any{
			ids[i%len(ids)],
			"api",
			base.Add(time.Duration(float64(i) / float64(n) * float64(span))),
			1.0,
			int64(512),
			latency,
			i%7 == 0,
			0.5,
			tier,
			0,
		}, nil
	})
	if _, err := conn.CopyFrom(ctx, pgx.Identifier{"telemetry_events"}, []string{
		"tenant_id", "service", "timestamp", "rps_window", "payload_bytes",
		"latency_ms", "cache_hit", "embedding_density", "model_tier", "child_spans",
	}, src); err != nil {
		t.Fatalf("COPY %d events: %v", n, err)
	}
}
