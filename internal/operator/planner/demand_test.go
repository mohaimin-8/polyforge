package planner

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// featuresServer serves one canned feature payload on the features endpoint.
func featuresServer(t *testing.T, rows []map[string]any) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"items": rows})
	}))
}

// The regression that matters: a tenant under real load must produce a
// non-zero demand. Every production emitter previously left RPSWindow unset,
// so the planner saw an idle cluster under any load, the hysteresis term won
// on every candidate, and the joint controller held its initial
// configuration for entire live runs while the reactive baseline moved.
func TestTenantDemandIsNonZeroForLiveTraffic(t *testing.T) {
	srv := featuresServer(t, []map[string]any{
		{"service": "chat", "avg_rps_window": 4.0, "avg_latency_ms": 300.0},
		{"service": "crud_read", "avg_rps_window": 12.0, "avg_latency_ms": 5.0},
	})
	defer srv.Close()

	src := NewFeatureDemandSource(srv.URL, "tok")
	demand, ok, err := src.TenantDemand(context.Background(), "acme")
	if err != nil || !ok {
		t.Fatalf("TenantDemand: ok=%v err=%v", ok, err)
	}

	var total float64
	for _, v := range demand.RPS {
		total += v
	}
	if total == 0 {
		t.Fatal("total demand is 0 under live traffic — the planner will freeze")
	}
	if demand.RPS["chat"] != 4.0 {
		t.Errorf("chat rps = %v, want 4.0", demand.RPS["chat"])
	}
	if demand.RPS["crud_read"] != 12.0 {
		t.Errorf("crud_read rps = %v, want 12.0", demand.RPS["crud_read"])
	}
}

// AI traffic must arrive as its own kind. The gateway used to record every
// AI request under the service name "ai-gateway", which is outside the
// taxonomy and so was folded into crud_read — one work unit instead of
// twenty, graded against the CRUD SLO target, with the tier's per-request
// price and the cacheable fraction both inapplicable.
func TestAIKindsAreNotFoldedIntoCrud(t *testing.T) {
	srv := featuresServer(t, []map[string]any{
		{"service": "chat", "avg_rps_window": 3.0, "avg_latency_ms": 400.0},
		{"service": "embed", "avg_rps_window": 2.0, "avg_latency_ms": 90.0},
		{"service": "agent", "avg_rps_window": 1.0, "avg_latency_ms": 2500.0},
	})
	defer srv.Close()

	demand, _, err := NewFeatureDemandSource(srv.URL, "tok").
		TenantDemand(context.Background(), "acme")
	if err != nil {
		t.Fatal(err)
	}
	for kind, want := range map[string]float64{"chat": 3.0, "embed": 2.0, "agent": 1.0} {
		if demand.RPS[kind] != want {
			t.Errorf("%s rps = %v, want %v", kind, demand.RPS[kind], want)
		}
	}
	if demand.RPS["crud_read"] != 0 {
		t.Errorf("crud_read = %v, want 0 — AI traffic must not be folded into CRUD",
			demand.RPS["crud_read"])
	}
}

// An unrecognised service still maps to crud_read by design, but that path
// must not silently absorb the AI kinds above. This pins the documented v1
// behaviour so a future taxonomy change is a deliberate one.
func TestUnknownServiceStillMapsToCrudRead(t *testing.T) {
	srv := featuresServer(t, []map[string]any{
		{"service": "billing-worker", "avg_rps_window": 7.0, "avg_latency_ms": 10.0},
	})
	defer srv.Close()

	demand, _, err := NewFeatureDemandSource(srv.URL, "tok").
		TenantDemand(context.Background(), "acme")
	if err != nil {
		t.Fatal(err)
	}
	if demand.RPS["crud_read"] != 7.0 {
		t.Errorf("unknown service rps = %v, want 7.0 under crud_read", demand.RPS["crud_read"])
	}
}

// The planner's headroom calibration reads realized p95 per kind from the
// same feature rows demand comes from. Two rows folding into one kind keep
// the worse p95; a row with no traffic or no p95 contributes nothing; a
// window with no p95 at all leaves the map absent (not zero).
func TestRealizedP95IsCarriedPerKindConservatively(t *testing.T) {
	srv := featuresServer(t, []map[string]any{
		{"service": "crud_read", "avg_rps_window": 10.0, "avg_latency_ms": 1.0, "p95_latency_ms": 1.2},
		{"service": "orders", "avg_rps_window": 5.0, "avg_latency_ms": 2.0, "p95_latency_ms": 3.4}, // folds into crud_read
		{"service": "chat", "avg_rps_window": 3.0, "avg_latency_ms": 400.0, "p95_latency_ms": 900.0},
		{"service": "embed", "avg_rps_window": 0.0, "avg_latency_ms": 0.0, "p95_latency_ms": 50.0}, // no traffic
	})
	defer srv.Close()

	demand, _, err := NewFeatureDemandSource(srv.URL, "tok").
		TenantDemand(context.Background(), "acme")
	if err != nil {
		t.Fatal(err)
	}
	if got := demand.RealizedP95Ms["crud_read"]; got != 3.4 {
		t.Errorf("crud_read realized p95 = %v, want 3.4 (the worse of the two rows)", got)
	}
	if got := demand.RealizedP95Ms["chat"]; got != 900.0 {
		t.Errorf("chat realized p95 = %v, want 900", got)
	}
	if _, ok := demand.RealizedP95Ms["embed"]; ok {
		t.Errorf("embed carried a p95 with no traffic")
	}

	quiet := featuresServer(t, []map[string]any{
		{"service": "crud_read", "avg_rps_window": 1.0, "avg_latency_ms": 1.0},
	})
	defer quiet.Close()
	demand, _, err = NewFeatureDemandSource(quiet.URL, "tok").
		TenantDemand(context.Background(), "acme")
	if err != nil {
		t.Fatal(err)
	}
	if demand.RealizedP95Ms != nil {
		t.Errorf("realized map should be absent when no row carries a p95, got %v", demand.RealizedP95Ms)
	}
}
