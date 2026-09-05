package main

// Parity between the two `eval-export` scoring paths.
//
// WHY THIS EXISTS. eval-export now has two implementations of the same
// measurement: the in-memory one, which pulls every telemetry row into Go, and
// the PostgreSQL one, which reduces the rows server-side. The second exists
// because the first cannot run at soak scale -- it OOM-killed the control-plane
// pod at ~952k events inside its 256 Mi limit (exit 137), which is almost
// certainly why WP14 attempt 4 produced no eval-export.json and therefore no
// metrics at all.
//
// But every committed live record was scored by the in-memory path. A soak
// scored by a *different estimator* is not comparable to the records it is
// meant to extend, and the disagreement would be invisible: both paths return a
// well-formed document full of plausible numbers. So the replacement is only
// usable if it is numerically identical, and that is what this test pins --
// byte-identical JSON over a seeded store, not "close enough".
//
// The fixture creates its own database. eval-export scores the WHOLE store by
// definition (the eval cluster is provisioned per run, so the store IS the
// run), so a parity test cannot share one with the other integration tests:
// CI runs packages in parallel against a single PostgreSQL service, and a
// tenant created by a concurrent test would land in these aggregates.

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"

	postgresstore "polyforge/internal/storage/postgres"
	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

// evalParityDatabase creates a throwaway database and returns admin/app URLs
// bound to it, dropped when the test ends.
func evalParityDatabase(t *testing.T, ctx context.Context) (string, string) {
	t.Helper()
	adminURL := os.Getenv("POLYFORGE_TEST_POSTGRES_ADMIN_URL")
	appURL := os.Getenv("POLYFORGE_TEST_POSTGRES_APP_URL")
	if adminURL == "" || appURL == "" {
		// Same rule as internal/storage/postgres: a skip prints `ok`, and this
		// is the parity check between the SQLite and PostgreSQL export paths -
		// the export that could not handle a 24 h sitting until WP14 found it.
		// Where the fixture is supposed to exist, its absence is a failure.
		if os.Getenv("POLYFORGE_REQUIRE_POSTGRES") != "" {
			t.Fatal("POLYFORGE_REQUIRE_POSTGRES is set but the PostgreSQL " +
				"integration URLs are not configured: export parity would " +
				"report ok without being checked at all")
		}
		t.Skip("PostgreSQL integration URLs are not configured")
	}

	name := fmt.Sprintf("pf_evalparity_%d", time.Now().UnixNano())
	maint, err := pgx.Connect(ctx, evalParityRewriteDB(t, adminURL, "postgres"))
	if err != nil {
		t.Fatalf("connect to the maintenance database: %v", err)
	}
	t.Cleanup(func() { _ = maint.Close(context.Background()) })

	quoted := pgx.Identifier{name}.Sanitize()
	if _, err := maint.Exec(ctx, `CREATE DATABASE `+quoted); err != nil {
		// Deliberately fatal rather than skipped: the URLs ARE configured, so
		// this is a fixture that needs updating, not an absent PostgreSQL. A
		// skip here would read as a pass and leave the two scoring paths
		// uncompared, which is the exact failure this test exists to prevent.
		t.Fatalf("create the parity database (the test role needs CREATEDB -- "+
			"re-run scripts/pg-test-up.sh): %v", err)
	}
	t.Cleanup(func() {
		_, _ = maint.Exec(context.Background(), `DROP DATABASE `+quoted+` WITH (FORCE)`)
	})
	return evalParityRewriteDB(t, adminURL, name), evalParityRewriteDB(t, appURL, name)
}

func evalParityRewriteDB(t *testing.T, raw, database string) string {
	t.Helper()
	u, err := url.Parse(raw)
	if err != nil {
		t.Fatalf("parse %q: %v", raw, err)
	}
	u.Path = "/" + database
	return u.String()
}

// evalParitySeed writes a deliberately awkward run into the store: the cases
// where two implementations of the same statistic drift apart.
func evalParitySeed(t *testing.T, ctx context.Context, store *postgresstore.Store) {
	t.Helper()
	// Epoch is a multiple of 3600, so 10 s steps and 60 s buckets both align to
	// the base and the offsets below mean what they say.
	base := time.Date(2026, 8, 23, 10, 0, 0, 0, time.UTC)

	for _, seed := range []tenant.Tenant{
		{ID: "pf-parity-premium", Name: "Premium", Plan: "premium"},
		{ID: "pf-parity-standard", Name: "Standard", Plan: "standard"},
		{ID: "pf-parity-besteffort", Name: "Best effort", Plan: "best-effort"},
		// No traffic: must count in n_tenants and stay out of Jain, in both.
		{ID: "pf-parity-silent", Name: "Silent", Plan: "standard"},
	} {
		if _, err := store.CreateTenant(ctx, seed); err != nil {
			t.Fatalf("create tenant %s: %v", seed.ID, err)
		}
	}

	add := func(id string, offsetMS int, latency float64, tier string, cacheHit bool) {
		t.Helper()
		event := telemetry.Event{
			TenantID:  id,
			Service:   "api",
			Timestamp: base.Add(time.Duration(offsetMS) * time.Millisecond),
			LatencyMS: latency,
			ModelTier: tier,
			CacheHit:  cacheHit,
		}
		if _, err := store.Add(ctx, event); err != nil {
			t.Fatalf("add event for %s at +%dms: %v", id, offsetMS, err)
		}
	}

	// premium: scale 1.0 -> crud 150 ms, ai 2500 ms.
	add("pf-parity-premium", 0, 150.0, "", false)         // exactly at target: NOT a violation
	add("pf-parity-premium", 500, 150.001, "", false)     // sub-second timestamp, same step
	add("pf-parity-premium", 9_999, 10, "", false)        // last millisecond of step 0
	add("pf-parity-premium", 10_000, 2500, "small", true) // AI at target, cache hit: no tier charge
	add("pf-parity-premium", 11_000, 2501, "mid", false)  // AI violation, priced tier
	add("pf-parity-premium", 60_000, 30_000, "large", false)
	add("pf-parity-premium", 119_900, 149.9999, "", false)

	// standard: scale 2.5 -> crud 375 ms, ai 6250 ms.
	add("pf-parity-standard", 20_000, 375, "", false)
	add("pf-parity-standard", 21_000, 376, "", false)
	add("pf-parity-standard", 65_000, 6250, "large", true)
	// Tier "none" is priced at $0 but must still appear in the histogram.
	add("pf-parity-standard", 185_000, 20_000, "none", false)
	add("pf-parity-standard", 186_000, 375.0000001, "", false)
	// Whitespace-only tier: TrimSpace and btrim must agree this is CRUD.
	add("pf-parity-standard", 187_000, 42, "  ", false)

	// best-effort: scale 8.0 -> crud 1200 ms. Twenty events in one step with
	// exactly one over target: 1 > 0.05*20 is false, so the step is NOT missed.
	// A `>=` on either side of the fence flips violation_step_share.
	for i := 0; i < 19; i++ {
		add("pf-parity-besteffort", 30_000+i*400, 100, "", false)
	}
	add("pf-parity-besteffort", 38_000, 1201, "", false)
	add("pf-parity-besteffort", 70_000, 0, "", false)
}

// evalParityInMemory reproduces what evalLoadStore does, then scores it the old
// way: read every row, aggregate in Go.
func evalParityInMemory(t *testing.T, ctx context.Context, store *postgresstore.Store,
	infraCost float64, bucketSeconds int) evalExportDoc {
	t.Helper()
	tenants, err := store.ListTenants(ctx)
	if err != nil {
		t.Fatalf("list tenants: %v", err)
	}
	events := make(map[string][]telemetry.Event, len(tenants))
	for _, tn := range tenants {
		rows, err := store.RecentByTenant(ctx, tn.ID, 5_000_000)
		if err != nil {
			t.Fatalf("read telemetry for %s: %v", tn.ID, err)
		}
		events[tn.ID] = rows
	}
	doc, err := computeEvalExport(tenants, events, infraCost, bucketSeconds)
	if err != nil {
		t.Fatalf("computeEvalExport: %v", err)
	}
	return doc
}

func evalParityJSON(t *testing.T, doc evalExportDoc) string {
	t.Helper()
	doc.GeneratedAtUTC = "" // wall clock, not a measurement
	payload, err := json.MarshalIndent(doc, "", "  ")
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	return string(payload)
}

func TestEvalExportPostgresMatchesInMemoryScoring(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
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
	evalParitySeed(t, ctx, store)

	t.Setenv("POLYFORGE_POSTGRES_ADMIN_URL", adminURL)
	t.Setenv("POLYFORGE_POSTGRES_APP_URL", appURL)

	for _, bucketSeconds := range []int{0, 60} {
		t.Run(fmt.Sprintf("bucket_seconds_%d", bucketSeconds), func(t *testing.T) {
			const infraCost = 1.5
			memory := evalParityInMemory(t, ctx, store, infraCost, bucketSeconds)

			server, err := evalAggregatePostgres(ctx, adminURL, bucketSeconds)
			if err != nil {
				t.Fatalf("evalAggregatePostgres: %v", err)
			}
			// The command applies the injected infra term after aggregation;
			// mirror it here so the comparison covers the whole document.
			server.CostInfraUSD = infraCost
			server.TotalCostUSD = server.CostTierUSD + server.CostInfraUSD

			// Positive control. Without this the test passes just as happily on
			// two empty documents, which is how a seeding failure would turn a
			// parity assertion into a tautology.
			if memory.NEvents != 34 || memory.NTenants != 4 {
				t.Fatalf("seed did not land: got %d events over %d tenants, want 34/4",
					memory.NEvents, memory.NTenants)
			}
			if memory.MeanViolation <= 0 || memory.CostTierUSD <= 0 || memory.MeanJain >= 1.0 {
				t.Fatalf("seed is not discriminating: violation=%v tier_cost=%v jain=%v",
					memory.MeanViolation, memory.CostTierUSD, memory.MeanJain)
			}
			if bucketSeconds > 0 && len(memory.Buckets) != 3 {
				t.Fatalf("expected 3 non-empty 60 s buckets (the empty window absent, "+
					"not zero), got %d", len(memory.Buckets))
			}

			if got, want := evalParityJSON(t, server), evalParityJSON(t, memory); got != want {
				t.Fatalf("the two scoring paths disagree.\nserver-side:\n%s\n\nin-memory:\n%s", got, want)
			}
		})
	}
}

// The in-memory path fails the export when a tenant carries a plan with no SLO
// class, because scoring it at a default silently grades a premium tenant 2.5x
// too leniently. The server-side path must fail the same way: its JOIN would
// otherwise DROP that tenant's events, which is worse -- a smaller, cleaner,
// wrong answer.
func TestEvalExportPostgresRejectsUnknownPlanLikeInMemory(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
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
	evalParitySeed(t, ctx, store)

	// CreateTenant normalises an empty plan to "standard", so write the
	// unrecognised value the way a mis-provisioned tenant would carry it.
	if _, err := store.CreateTenant(ctx, tenant.Tenant{ID: "pf-parity-gold", Name: "Gold", Plan: "standard"}); err != nil {
		t.Fatalf("create tenant: %v", err)
	}
	if _, err := store.Add(ctx, telemetry.Event{
		TenantID: "pf-parity-gold", Service: "api",
		Timestamp: time.Date(2026, 8, 23, 10, 0, 5, 0, time.UTC), LatencyMS: 10,
	}); err != nil {
		t.Fatalf("add event: %v", err)
	}
	if err := evalParitySetPlan(ctx, adminURL, "pf-parity-gold", "gold"); err != nil {
		t.Fatalf("set unrecognised plan: %v", err)
	}

	tenants, err := store.ListTenants(ctx)
	if err != nil {
		t.Fatalf("list tenants: %v", err)
	}
	if _, err := computeEvalExport(tenants, map[string][]telemetry.Event{}, 0, 0); err == nil {
		t.Fatal("in-memory path accepted an unrecognised plan")
	}

	t.Setenv("POLYFORGE_POSTGRES_APP_URL", appURL)
	if _, err := evalAggregatePostgres(ctx, adminURL, 0); err == nil {
		t.Fatal("server-side path accepted an unrecognised plan: its JOIN would " +
			"silently drop the tenant's events instead of failing the export")
	}
}

func evalParitySetPlan(ctx context.Context, adminURL, tenantID, plan string) error {
	conn, err := pgx.Connect(ctx, adminURL)
	if err != nil {
		return err
	}
	defer func() { _ = conn.Close(context.Background()) }()
	_, err = conn.Exec(ctx, `UPDATE tenants SET plan = $1 WHERE id = $2`, plan, tenantID)
	return err
}
