package platform

// What does `latency_ms` actually measure?
//
// WHY THIS EXISTS. Every live-plane latency number in WP14 -- SK-H1's p95
// deviation, SK-H3's hourly crud_p95 gate, eval-export's crud_p95_ms and
// crud_p99_ms -- is computed from the `latency_ms` this handler records. The
// name invites reading it as service time. It is not: replayWorkload starts
// its clock immediately before burnCPU and stops it immediately after, so the
// recorded figure is the duration of a CPU spin and nothing else. Request
// parse, authorisation, queueing ahead of the handler, the telemetry write and
// the response are all outside the measured window.
//
// That gap is not hypothetical. WP14 attempt 4 recorded a server-side p95 of
// 2.23 ms while k6 measured 1032 ms client-side, and the record reports the
// 464x discrepancy as a symptom of the pinned load path. It is also simply
// what this metric is: the two numbers were never measuring the same thing.
//
// The practical consequence is that a fault which adds NETWORK delay -- Stage
// B's `tc netem` control -- cannot move crud_p95 by construction, so SK-H1
// cannot be observed failing under it. A fault which starves CPU can, because
// burnCPU spins against a wall-clock deadline.
//
// This test does not argue that; it demonstrates it, so the property is pinned
// rather than rediscovered. If someone later widens the measured window, this
// test fails and forces the decision to be explicit -- which it must be, since
// every committed live record was scored under the narrow definition.

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

// slowTelemetry is a store whose write is slow, standing in for any delay
// outside the burn: a loaded PostgreSQL, a congested link, a netem qdisc.
type slowTelemetry struct {
	*telemetry.Store
	delay time.Duration
}

func (s slowTelemetry) Add(ctx context.Context, e telemetry.Event) (telemetry.Event, error) {
	time.Sleep(s.delay)
	return s.Store.Add(ctx, e)
}

func TestReplayLatencyMeasuresOnlyTheBurnNotTheRequest(t *testing.T) {
	const storeDelay = 250 * time.Millisecond

	ctx := context.Background()
	tenantStore := tenant.NewStore()
	if _, err := tenantStore.CreateTenant(ctx, tenant.Tenant{ID: "alpha", Name: "Alpha"}); err != nil {
		t.Fatal(err)
	}
	key, err := tenantStore.CreateAPIKey(ctx, "alpha", "alpha", tenant.ScopeFull)
	if err != nil {
		t.Fatal(err)
	}

	server := httptest.NewServer(NewServer(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		tenantStore,
		slowTelemetry{Store: telemetry.NewStore(100), delay: storeDelay},
		Config{AdminKey: "admin-test"},
	).Handler())
	defer server.Close()

	req, err := http.NewRequest(http.MethodPost,
		server.URL+"/v1/tenants/alpha/workloads/replay",
		strings.NewReader(`{"kind": "crud_read", "run": "semantics"}`))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("X-PolyForge-API-Key", key.Secret)
	req.Header.Set("Content-Type", "application/json")

	started := time.Now()
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = resp.Body.Close() }()
	observed := time.Since(started)
	if resp.StatusCode != http.StatusAccepted {
		t.Fatalf("expected 202, got %d", resp.StatusCode)
	}

	var out struct {
		LatencyMS float64 `json:"latency_ms"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatal(err)
	}
	recorded := time.Duration(out.LatencyMS * float64(time.Millisecond))

	// The client waited for the slow write. The recorded number did not.
	if observed < storeDelay {
		t.Fatalf("the injected delay did not reach the request: observed %s "+
			"for a %s store delay", observed.Round(time.Millisecond), storeDelay)
	}
	if recorded >= storeDelay {
		t.Fatalf("recorded latency %s absorbed the store delay: the measured "+
			"window is wider than burnCPU, which changes what every committed "+
			"live record means", recorded.Round(time.Millisecond))
	}
	// crud_read is 1 wu at 1 ms of CPU, so the burn is the whole of it.
	if gap := observed - recorded; gap < storeDelay {
		t.Fatalf("expected at least the %s store delay to fall outside the "+
			"measured window, got a gap of %s", storeDelay, gap.Round(time.Millisecond))
	}
	t.Logf("client observed %s; latency_ms recorded %s -- %s of real request "+
		"time is outside the metric that SK-H1 and SK-H3 are scored on",
		observed.Round(time.Millisecond), recorded.Round(time.Millisecond),
		(observed - recorded).Round(time.Millisecond))
}
