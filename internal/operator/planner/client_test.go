package planner

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func planRequest() Request {
	return Request{
		Weights: Weights{Alpha: 1, Beta: 2, Gamma: 0.5},
		Limits:  Limits{CacheMB: 4096, Replicas: 60},
		Tenants: []TenantInput{{
			TenantID: "acme",
			SLOClass: "premium",
			// A zero budget legitimately means "spend nothing" and sheds
			// the tenant to its floor — always send a real budget.
			HourlyBudgetUSD: 5.0,
			ReplicaMin:      1,
			ReplicaMax:      10,
			FairnessWeight:  0.5,
			State:           State{Replicas: 2, CacheMB: 128, Tier: "small"},
			Demand:          Demand{RPS: map[string]float64{"chat": 2.0}, CrudBaseMs: 50},
		}},
	}
}

func TestHTTPClientRoundTrip(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/plan" {
			t.Errorf("unexpected path %s", r.URL.Path)
		}
		var req Request
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Fatal(err)
		}
		if req.Tenants[0].TenantID != "acme" {
			t.Errorf("tenant = %q", req.Tenants[0].TenantID)
		}
		_ = json.NewEncoder(w).Encode(Response{
			Solver: "jcac-lattice-v1",
			Plans:  map[string]Plan{"acme": {Replicas: 3, CacheMB: 256, Tier: "mid"}},
		})
	}))
	defer server.Close()

	client := NewHTTPClient(server.URL, time.Second)
	resp, err := client.Plan(context.Background(), planRequest())
	if err != nil {
		t.Fatal(err)
	}
	if resp.Plans["acme"].Replicas != 3 || resp.Plans["acme"].Tier != "mid" {
		t.Errorf("plan = %+v", resp.Plans["acme"])
	}
}

func TestHTTPClientRejectsMalformedPlans(t *testing.T) {
	cases := map[string]Response{
		"unknown tier":       {Plans: map[string]Plan{"acme": {Replicas: 1, Tier: "colossal"}}},
		"negative resources": {Plans: map[string]Plan{"acme": {Replicas: -1, Tier: "small"}}},
		"unrequested tenant": {Plans: map[string]Plan{"acme": {Replicas: 1, Tier: "small"}, "ghost": {Replicas: 1, Tier: "small"}}},
		"omitted tenant":     {Plans: map[string]Plan{}},
	}
	for name, resp := range cases {
		t.Run(name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				_ = json.NewEncoder(w).Encode(resp)
			}))
			defer server.Close()
			client := NewHTTPClient(server.URL, time.Second)
			if _, err := client.Plan(context.Background(), planRequest()); err == nil {
				t.Error("malformed plan accepted")
			}
		})
	}
}

func TestHTTPClientSurfacesServerErrors(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, `{"error":"solver exploded"}`, http.StatusInternalServerError)
	}))
	defer server.Close()
	client := NewHTTPClient(server.URL, time.Second)
	if _, err := client.Plan(context.Background(), planRequest()); err == nil {
		t.Error("500 accepted as a plan")
	}
}

func TestHTTPClientTimesOut(t *testing.T) {
	block := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		<-block
	}))
	defer func() { close(block); server.Close() }()

	client := NewHTTPClient(server.URL, 50*time.Millisecond)
	start := time.Now()
	_, err := client.Plan(context.Background(), planRequest())
	if err == nil {
		t.Fatal("expected timeout error")
	}
	if elapsed := time.Since(start); elapsed > 2*time.Second {
		t.Errorf("timeout took %v", elapsed)
	}
}

func TestFeatureDemandSourceMapsRows(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/tenants/acme/telemetry/features" {
			t.Errorf("unexpected path %s", r.URL.Path)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer tok" {
			t.Errorf("authorization = %q", got)
		}
		_, _ = w.Write([]byte(`{"items": [
			{"service": "chat", "avg_rps_window": 2.0, "avg_latency_ms": 900},
			{"service": "checkout", "avg_rps_window": 6.0, "avg_latency_ms": 40}
		]}`))
	}))
	defer server.Close()

	source := NewFeatureDemandSource(server.URL, "tok")
	demand, ok, err := source.TenantDemand(context.Background(), "acme")
	if err != nil || !ok {
		t.Fatalf("ok=%v err=%v", ok, err)
	}
	if demand.RPS["chat"] != 2.0 {
		t.Errorf("chat rps = %v", demand.RPS["chat"])
	}
	if demand.RPS["crud_read"] != 6.0 {
		t.Errorf("unknown service should fold into crud_read, got %v", demand.RPS)
	}
	want := (900*2.0 + 40*6.0) / 8.0
	if demand.CrudBaseMs != want {
		t.Errorf("crud base = %v, want %v", demand.CrudBaseMs, want)
	}
}

// The platform admin key rides its own header and displaces the bearer
// token — a bearer that is not a JWT would be rejected by the control
// plane, so sending both would just mask a misconfiguration.
func TestFeatureDemandSourceAdminKeyHeader(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("X-PolyForge-Admin-Key"); got != "platform-admin" {
			t.Errorf("admin key header = %q", got)
		}
		if got := r.Header.Get("Authorization"); got != "" {
			t.Errorf("bearer header should be absent when the admin key is set, got %q", got)
		}
		_, _ = w.Write([]byte(`{"items": [{"service": "chat", "avg_rps_window": 1.0, "avg_latency_ms": 100}]}`))
	}))
	defer server.Close()

	source := NewFeatureDemandSource(server.URL, "tok")
	source.AdminKey = "platform-admin"
	if _, ok, err := source.TenantDemand(context.Background(), "acme"); err != nil || !ok {
		t.Fatalf("ok=%v err=%v", ok, err)
	}
}

func TestFeatureDemandSourceNoTelemetry(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"items": []}`))
	}))
	defer server.Close()
	source := NewFeatureDemandSource(server.URL, "")
	_, ok, err := source.TenantDemand(context.Background(), "acme")
	if err != nil {
		t.Fatal(err)
	}
	if ok {
		t.Error("empty feature set should report no demand")
	}
}
