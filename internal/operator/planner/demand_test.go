package planner

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
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

// Behind N load-balanced control-plane pods, each pod's in-process
// RateTracker sees about 1/N of a tenant's requests, and the feature API
// averages those per-pod rates. A 2026-09-28 kind run with 16 pods reported
// tenant t00's chat demand as 0.36 rps while it sent 4.1 rps. The shared
// store sees every pod's events, so count / window is the tenant's rate
// whatever the pod count. Opt-in: the default keeps the registered signal.
func TestRateFromCountIsIndependentOfPodCount(t *testing.T) {
	srv := featuresServer(t, []map[string]any{
		// 41 chat events in a 10 s window = 4.1 rps; each of 16 pods saw ~0.26.
		{"service": "chat", "event_count": 41, "avg_rps_window": 0.36, "avg_latency_ms": 20.0},
		{"service": "crud_read", "event_count": 50, "avg_rps_window": 0.42, "avg_latency_ms": 1.0},
	})
	defer srv.Close()

	src := NewFeatureDemandSource(srv.URL, "tok")
	src.RateFromCount = true
	demand, ok, err := src.TenantDemand(context.Background(), "t00")
	if err != nil || !ok {
		t.Fatalf("TenantDemand: ok=%v err=%v", ok, err)
	}
	if got := demand.RPS["chat"]; got != 4.1 {
		t.Errorf("chat rps = %v, want 4.1 (41 events / 10 s)", got)
	}
	if got := demand.RPS["crud_read"]; got != 5.0 {
		t.Errorf("crud_read rps = %v, want 5.0 (50 events / 10 s)", got)
	}
	// The latency weights follow the corrected rates: (20*4.1 + 1*5) / 9.1.
	if want := (20.0*4.1 + 1.0*5.0) / 9.1; diff(demand.CrudBaseMs, want) > 1e-9 {
		t.Errorf("CrudBaseMs = %v, want %v", demand.CrudBaseMs, want)
	}
}

func TestDefaultDemandStillAveragesPerPodRates(t *testing.T) {
	srv := featuresServer(t, []map[string]any{
		{"service": "chat", "event_count": 41, "avg_rps_window": 0.36, "avg_latency_ms": 20.0},
	})
	defer srv.Close()

	demand, _, err := NewFeatureDemandSource(srv.URL, "tok").TenantDemand(context.Background(), "t00")
	if err != nil {
		t.Fatal(err)
	}
	if got := demand.RPS["chat"]; got != 0.36 {
		t.Errorf("default chat rps = %v, want the registered avg_rps_window 0.36", got)
	}
}

func diff(a, b float64) float64 {
	if a > b {
		return a - b
	}
	return b - a
}

func TestParseDemandRate(t *testing.T) {
	for mode, want := range map[string]bool{"": false, "mean": false, "count": true} {
		got, err := ParseDemandRate(mode)
		if err != nil || got != want {
			t.Errorf("ParseDemandRate(%q) = %v, %v; want %v, nil", mode, got, err, want)
		}
	}
	// A typo must not silently fall back to the attenuated signal.
	if _, err := ParseDemandRate("counts"); err == nil {
		t.Error("ParseDemandRate(\"counts\") accepted an unknown mode")
	}
}

// The divisor is the window the server counted over, not the one the operator
// asked for: a slow feature call widens [since, until] and the count with it.
func TestRateFromCountDividesByTheServerReportedWindow(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"since": "2026-09-28T00:00:00Z",
			"until": "2026-09-28T00:00:12.5Z", // 12.5 s, not the requested 10 s
			"items": []map[string]any{{"service": "chat", "event_count": 50, "avg_rps_window": 0.3}},
		})
	}))
	defer srv.Close()

	src := NewFeatureDemandSource(srv.URL, "tok")
	src.RateFromCount = true
	demand, _, err := src.TenantDemand(context.Background(), "t00")
	if err != nil {
		t.Fatal(err)
	}
	if got := demand.RPS["chat"]; got != 4.0 {
		t.Errorf("chat rps = %v, want 4.0 (50 events / 12.5 s server window)", got)
	}
}

func TestRateFromCountFallsBackToTheConfiguredWindow(t *testing.T) {
	srv := featuresServer(t, []map[string]any{{"service": "chat", "event_count": 20}})
	defer srv.Close()

	src := NewFeatureDemandSource(srv.URL, "tok")
	src.RateFromCount = true
	src.Window = 5 * time.Second
	demand, _, err := src.TenantDemand(context.Background(), "t00")
	if err != nil {
		t.Fatal(err)
	}
	if got := demand.RPS["chat"]; got != 4.0 {
		t.Errorf("chat rps = %v, want 4.0 (20 events / 5 s configured window)", got)
	}
}
