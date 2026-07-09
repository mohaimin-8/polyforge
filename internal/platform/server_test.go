package platform

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"

	"go.opentelemetry.io/otel/propagation"
	oteltrace "go.opentelemetry.io/otel/trace"

	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

func TestTenantProjectEndpointsEnforceAPIKeyIsolation(t *testing.T) {
	ctx := context.Background()
	tenantStore := tenant.NewStore()
	telemetryStore := telemetry.NewStore(100)
	_, _ = tenantStore.CreateTenant(ctx, tenant.Tenant{ID: "alpha", Name: "Alpha"})
	_, _ = tenantStore.CreateTenant(ctx, tenant.Tenant{ID: "bravo", Name: "Bravo"})
	alphaKey, _ := tenantStore.CreateAPIKey(ctx, "alpha", "alpha", tenant.ScopeFull)
	bravoKey, _ := tenantStore.CreateAPIKey(ctx, "bravo", "bravo", tenant.ScopeFull)
	_, _ = tenantStore.CreateProject(ctx, tenant.Project{ID: "alpha-project", TenantID: "alpha", Name: "Alpha Project"})

	server := httptest.NewServer(NewServer(slog.New(slog.NewTextHandler(io.Discard, nil)), tenantStore, telemetryStore, Config{AdminKey: "admin-test"}).Handler())
	defer server.Close()

	t.Run("correct key can list projects", func(t *testing.T) {
		req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/tenants/alpha/projects", nil)
		req.Header.Set("X-PolyForge-API-Key", alphaKey.Secret)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer func() { _ = resp.Body.Close() }()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("expected 200, got %d", resp.StatusCode)
		}
	})

	t.Run("other tenant key is rejected", func(t *testing.T) {
		req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/tenants/alpha/projects", nil)
		req.Header.Set("X-PolyForge-API-Key", bravoKey.Secret)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer func() { _ = resp.Body.Close() }()
		if resp.StatusCode != http.StatusUnauthorized {
			t.Fatalf("expected 401, got %d", resp.StatusCode)
		}
	})
}

func TestCreateTenantReturnsBootstrapAPIKey(t *testing.T) {
	tenantStore := tenant.NewStore()
	telemetryStore := telemetry.NewStore(100)
	server := httptest.NewServer(NewServer(slog.New(slog.NewTextHandler(io.Discard, nil)), tenantStore, telemetryStore, Config{AdminKey: "admin-test"}).Handler())
	defer server.Close()

	body := bytes.NewBufferString(`{"id":"new-tenant","name":"New Tenant"}`)
	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/tenants", body)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-PolyForge-Admin-Key", "admin-test")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("expected 201, got %d", resp.StatusCode)
	}

	var payload struct {
		Tenant tenant.Tenant `json:"tenant"`
		Key    tenant.APIKey `json:"bootstrap_api_key"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		t.Fatal(err)
	}
	if payload.Tenant.ID != "new-tenant" || payload.Key.Secret == "" {
		t.Fatalf("unexpected bootstrap response: %#v", payload)
	}
	if _, err := tenantStore.AuthenticateAPIKey(context.Background(), "new-tenant", payload.Key.Secret); err != nil {
		t.Fatalf("bootstrap key did not authenticate: %v", err)
	}
}

func TestProjectCRUDAndRequestErrorContract(t *testing.T) {
	ctx := context.Background()
	tenantStore := tenant.NewStore()
	_, _ = tenantStore.CreateTenant(ctx, tenant.Tenant{ID: "alpha", Name: "Alpha"})
	key, _ := tenantStore.CreateAPIKey(ctx, "alpha", "test", tenant.ScopeFull)
	server := httptest.NewServer(NewServer(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		tenantStore,
		telemetry.NewStore(100),
		Config{AdminKey: "admin-test"},
	).Handler())
	defer server.Close()

	t.Run("errors include stable code and request id", func(t *testing.T) {
		resp, err := http.Get(server.URL + "/v1/tenants/alpha/projects")
		if err != nil {
			t.Fatal(err)
		}
		defer func() { _ = resp.Body.Close() }()
		var payload errorEnvelope
		if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
			t.Fatal(err)
		}
		requestID := resp.Header.Get("X-Request-ID")
		if resp.StatusCode != http.StatusUnauthorized || payload.Error.Code != "unauthorized" || requestID == "" || payload.Error.RequestID != requestID {
			t.Fatalf("unexpected error contract: status=%d header=%q payload=%#v", resp.StatusCode, requestID, payload)
		}
	})

	do := func(method, path, body string) *http.Response {
		t.Helper()
		var reader io.Reader
		if body != "" {
			reader = bytes.NewBufferString(body)
		}
		req, err := http.NewRequest(method, server.URL+path, reader)
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("X-PolyForge-API-Key", key.Secret)
		if body != "" {
			req.Header.Set("Content-Type", "application/json")
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		return resp
	}

	resp := do(http.MethodPost, "/v1/tenants/alpha/projects", `{"id":"p1","name":"Original"}`)
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("create: expected 201, got %d", resp.StatusCode)
	}
	_ = resp.Body.Close()

	resp = do(http.MethodPatch, "/v1/tenants/alpha/projects/p1", `{"name":"Updated"}`)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("update: expected 200, got %d", resp.StatusCode)
	}
	var updated tenant.Project
	if err := json.NewDecoder(resp.Body).Decode(&updated); err != nil {
		t.Fatal(err)
	}
	_ = resp.Body.Close()
	if updated.Name != "Updated" || updated.UpdatedAt.IsZero() {
		t.Fatalf("unexpected updated project: %#v", updated)
	}

	resp = do(http.MethodGet, "/v1/tenants/alpha/projects?limit=1", "")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("list: expected 200, got %d", resp.StatusCode)
	}
	var page tenant.ProjectPage
	if err := json.NewDecoder(resp.Body).Decode(&page); err != nil {
		t.Fatal(err)
	}
	_ = resp.Body.Close()
	if len(page.Items) != 1 || page.Items[0].Name != "Updated" {
		t.Fatalf("unexpected project page: %#v", page)
	}

	resp = do(http.MethodDelete, "/v1/tenants/alpha/projects/p1", "")
	if resp.StatusCode != http.StatusNoContent {
		t.Fatalf("delete: expected 204, got %d", resp.StatusCode)
	}
	_ = resp.Body.Close()

	resp = do(http.MethodGet, "/v1/tenants/alpha/projects/p1", "")
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("get deleted: expected 404, got %d", resp.StatusCode)
	}
	_ = resp.Body.Close()
}

func TestAPIKeyRotationEndpointInvalidatesOldSecret(t *testing.T) {
	ctx := context.Background()
	tenantStore := tenant.NewStore()
	_, _ = tenantStore.CreateTenant(ctx, tenant.Tenant{ID: "alpha", Name: "Alpha"})
	oldKey, _ := tenantStore.CreateAPIKey(ctx, "alpha", "service", tenant.ScopeFull)
	server := httptest.NewServer(NewServer(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		tenantStore,
		telemetry.NewStore(100),
		Config{AdminKey: "admin-test"},
	).Handler())
	defer server.Close()

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/tenants/alpha/api-keys/"+oldKey.ID+"/rotate", nil)
	req.Header.Set("X-PolyForge-API-Key", oldKey.Secret)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("expected 201, got %d", resp.StatusCode)
	}
	var newKey tenant.APIKey
	if err := json.NewDecoder(resp.Body).Decode(&newKey); err != nil {
		t.Fatal(err)
	}
	if newKey.Secret == "" {
		t.Fatal("rotated key did not return its one-time secret")
	}
	if _, err := tenantStore.AuthenticateAPIKey(ctx, "alpha", oldKey.Secret); !errors.Is(err, tenant.ErrUnauthorized) {
		t.Fatalf("old key still authenticates: %v", err)
	}
	if _, err := tenantStore.AuthenticateAPIKey(ctx, "alpha", newKey.Secret); err != nil {
		t.Fatalf("new key does not authenticate: %v", err)
	}
}

func TestRateLimitReturns429AndRetryAfter(t *testing.T) {
	tenantStore := tenant.NewStore()
	server := httptest.NewServer(NewServer(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		tenantStore,
		telemetry.NewStore(100),
		Config{
			AdminKey: "admin-test",
			RateLimit: RateLimitConfig{
				RequestsPerMinute: 60,
				Burst:             1,
			},
		},
	).Handler())
	defer server.Close()

	do := func() *http.Response {
		t.Helper()
		req, err := http.NewRequest(http.MethodGet, server.URL+"/v1/tenants", nil)
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("X-PolyForge-Admin-Key", "admin-test")
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		return resp
	}

	resp := do()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("first request: expected 200, got %d", resp.StatusCode)
	}
	_ = resp.Body.Close()

	resp = do()
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusTooManyRequests {
		t.Fatalf("second request: expected 429, got %d", resp.StatusCode)
	}
	if resp.Header.Get("Retry-After") == "" {
		t.Fatal("rate-limited response did not include Retry-After")
	}
	var payload errorEnvelope
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		t.Fatal(err)
	}
	if payload.Error.Code != "rate_limited" || payload.Error.RequestID == "" {
		t.Fatalf("unexpected rate-limit envelope: %#v", payload)
	}
}

func TestRateLimitBypassesHealthz(t *testing.T) {
	server := httptest.NewServer(NewServer(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		tenant.NewStore(),
		telemetry.NewStore(100),
		Config{RateLimit: RateLimitConfig{RequestsPerMinute: 1, Burst: 1}},
	).Handler())
	defer server.Close()

	for i := 0; i < 3; i++ {
		resp, err := http.Get(server.URL + "/healthz")
		if err != nil {
			t.Fatal(err)
		}
		_ = resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("health request %d: expected 200, got %d", i+1, resp.StatusCode)
		}
	}
}

func TestTraceParentPropagation(t *testing.T) {
	server := httptest.NewServer(NewServer(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		tenant.NewStore(),
		telemetry.NewStore(100),
		Config{},
	).Handler())
	defer server.Close()

	incoming := "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
	req, err := http.NewRequest(http.MethodGet, server.URL+"/healthz", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("traceparent", incoming)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("expected 200, got %d", resp.StatusCode)
	}

	outgoing := resp.Header.Get("traceparent")
	sc, ok := parseTraceParentHeader(outgoing)
	if !ok {
		t.Fatalf("response traceparent is invalid: %q", outgoing)
	}
	if sc.TraceID().String() != "4bf92f3577b34da6a3ce929d0e0e4736" {
		t.Fatalf("trace id was not propagated: %q", outgoing)
	}
	if sc.SpanID().String() == "00f067aa0ba902b7" {
		t.Fatalf("server reused parent span id: %q", outgoing)
	}
	if !sc.IsSampled() {
		t.Fatalf("trace flags were not preserved: %q", outgoing)
	}
}

// parseTraceParentHeader decodes a W3C traceparent value using the OTel
// propagator, the way a downstream service would.
func parseTraceParentHeader(header string) (oteltrace.SpanContext, bool) {
	ctx := (propagation.TraceContext{}).Extract(context.Background(),
		propagation.MapCarrier{"traceparent": header})
	sc := oteltrace.SpanContextFromContext(ctx)
	return sc, sc.IsValid()
}

func TestInvalidTraceParentGetsReplaced(t *testing.T) {
	server := httptest.NewServer(NewServer(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		tenant.NewStore(),
		telemetry.NewStore(100),
		Config{},
	).Handler())
	defer server.Close()

	req, err := http.NewRequest(http.MethodGet, server.URL+"/healthz", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("traceparent", "00-00000000000000000000000000000000-0000000000000000-01")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	_ = resp.Body.Close()

	outgoing := resp.Header.Get("traceparent")
	sc, ok := parseTraceParentHeader(outgoing)
	if !ok {
		t.Fatalf("response traceparent is invalid: %q", outgoing)
	}
	if !sc.TraceID().IsValid() || !sc.SpanID().IsValid() {
		t.Fatalf("invalid trace identifiers were reused: %q", outgoing)
	}
}

func TestTelemetryFeaturesEndpointAggregatesPerService(t *testing.T) {
	ctx := context.Background()
	base := time.Now().UTC().Truncate(time.Second)

	tenantStore := tenant.NewStore()
	telemetryStore := telemetry.NewStore(100)
	_, _ = tenantStore.CreateTenant(ctx, tenant.Tenant{ID: "alpha", Name: "Alpha"})
	_, _ = tenantStore.CreateTenant(ctx, tenant.Tenant{ID: "bravo", Name: "Bravo"})
	alphaKey, _ := tenantStore.CreateAPIKey(ctx, "alpha", "alpha", tenant.ScopeFull)
	bravoKey, _ := tenantStore.CreateAPIKey(ctx, "bravo", "bravo", tenant.ScopeFull)

	events := []telemetry.Event{
		{TenantID: "alpha", Service: "api", Timestamp: base.Add(-10 * time.Minute), LatencyMS: 10, PayloadBytes: 100, CacheHit: true, ModelTier: "small"},
		{TenantID: "alpha", Service: "api", Timestamp: base.Add(-9 * time.Minute), LatencyMS: 20, PayloadBytes: 200, CacheHit: false, ModelTier: "small"},
		{TenantID: "alpha", Service: "worker", Timestamp: base.Add(-8 * time.Minute), LatencyMS: 100, PayloadBytes: 900, CacheHit: false, ModelTier: "mid"},
		{TenantID: "bravo", Service: "api", Timestamp: base.Add(-8 * time.Minute), LatencyMS: 999, PayloadBytes: 999, CacheHit: true, ModelTier: "large"},
	}
	for _, event := range events {
		if _, err := telemetryStore.Add(ctx, event); err != nil {
			t.Fatal(err)
		}
	}

	server := httptest.NewServer(NewServer(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		tenantStore,
		telemetryStore,
		Config{AdminKey: "admin-test"},
	).Handler())
	defer server.Close()

	getFeatures := func(t *testing.T, tenantID, secret string, params url.Values) (*http.Response, telemetry.FeatureSet) {
		t.Helper()
		target := server.URL + "/v1/tenants/" + tenantID + "/telemetry/features"
		if encoded := params.Encode(); encoded != "" {
			target += "?" + encoded
		}
		req, err := http.NewRequest(http.MethodGet, target, nil)
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("X-PolyForge-API-Key", secret)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer func() { _ = resp.Body.Close() }()
		var set telemetry.FeatureSet
		if resp.StatusCode == http.StatusOK {
			if err := json.NewDecoder(resp.Body).Decode(&set); err != nil {
				t.Fatal(err)
			}
		}
		return resp, set
	}

	t.Run("default window aggregates every service", func(t *testing.T) {
		resp, set := getFeatures(t, "alpha", alphaKey.Secret, url.Values{})
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("expected 200, got %d", resp.StatusCode)
		}
		if len(set.Items) != 2 {
			t.Fatalf("expected 2 service rows, got %d", len(set.Items))
		}
		api, worker := set.Items[0], set.Items[1]
		if api.Service != "api" || worker.Service != "worker" {
			t.Fatalf("expected rows sorted by service, got %q then %q", api.Service, worker.Service)
		}
		if api.EventCount != 2 {
			t.Fatalf("expected 2 api events, got %d", api.EventCount)
		}
		if api.AvgLatencyMS != 15 {
			t.Fatalf("expected avg latency 15, got %v", api.AvgLatencyMS)
		}
		if api.P95LatencyMS != 20 {
			t.Fatalf("expected p95 latency 20, got %v", api.P95LatencyMS)
		}
		if api.CacheHitRate != 0.5 {
			t.Fatalf("expected cache hit rate 0.5, got %v", api.CacheHitRate)
		}
		if api.AvgPayloadBytes != 150 {
			t.Fatalf("expected avg payload 150, got %v", api.AvgPayloadBytes)
		}
		if api.ModelTierCounts["small"] != 2 {
			t.Fatalf("expected 2 small-tier events, got %v", api.ModelTierCounts)
		}
	})

	t.Run("service filter narrows the result", func(t *testing.T) {
		resp, set := getFeatures(t, "alpha", alphaKey.Secret, url.Values{"service": {"worker"}})
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("expected 200, got %d", resp.StatusCode)
		}
		if len(set.Items) != 1 || set.Items[0].Service != "worker" {
			t.Fatalf("expected only the worker row, got %+v", set.Items)
		}
	})

	t.Run("since bound excludes older events", func(t *testing.T) {
		since := base.Add(-8*time.Minute - 30*time.Second).Format(time.RFC3339)
		resp, set := getFeatures(t, "alpha", alphaKey.Secret, url.Values{"since": {since}})
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("expected 200, got %d", resp.StatusCode)
		}
		if len(set.Items) != 1 || set.Items[0].Service != "worker" {
			t.Fatalf("expected only events at or after since, got %+v", set.Items)
		}
	})

	t.Run("features never cross tenant boundaries", func(t *testing.T) {
		resp, _ := getFeatures(t, "alpha", bravoKey.Secret, url.Values{})
		if resp.StatusCode != http.StatusUnauthorized {
			t.Fatalf("expected 401 for a foreign API key, got %d", resp.StatusCode)
		}
		_, set := getFeatures(t, "bravo", bravoKey.Secret, url.Values{})
		if len(set.Items) != 1 || set.Items[0].EventCount != 1 {
			t.Fatalf("bravo observed alpha telemetry: %+v", set.Items)
		}
	})

	t.Run("invalid bounds are rejected", func(t *testing.T) {
		cases := map[string]url.Values{
			"unparsable since": {"since": {"yesterday"}},
			"unparsable until": {"until": {"12345"}},
			"since after until": {
				"since": {base.Format(time.RFC3339)},
				"until": {base.Add(-time.Minute).Format(time.RFC3339)},
			},
		}
		for name, params := range cases {
			resp, _ := getFeatures(t, "alpha", alphaKey.Secret, params)
			if resp.StatusCode != http.StatusBadRequest {
				t.Fatalf("%s: expected 400, got %d", name, resp.StatusCode)
			}
		}
	})
}

// TestReadOnlyScopeIsEnforced is the roadmap's W7 verification gate:
// "API key with read-only scope returns 403 on POST".
func TestReadOnlyScopeIsEnforced(t *testing.T) {
	ctx := context.Background()
	tenantStore := tenant.NewStore()
	_, _ = tenantStore.CreateTenant(ctx, tenant.Tenant{ID: "alpha", Name: "Alpha"})
	fullKey, _ := tenantStore.CreateAPIKey(ctx, "alpha", "writer", tenant.ScopeFull)
	readKey, _ := tenantStore.CreateAPIKey(ctx, "alpha", "reader", tenant.ScopeRead)

	server := httptest.NewServer(NewServer(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		tenantStore,
		telemetry.NewStore(100),
		Config{AdminKey: "admin-test"},
	).Handler())
	defer server.Close()

	do := func(t *testing.T, method, path, secret, body string) *http.Response {
		t.Helper()
		var reader io.Reader
		if body != "" {
			reader = strings.NewReader(body)
		}
		req, err := http.NewRequest(method, server.URL+path, reader)
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("X-PolyForge-API-Key", secret)
		if body != "" {
			req.Header.Set("Content-Type", "application/json")
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		return resp
	}

	t.Run("read key gets 403 with forbidden code on POST", func(t *testing.T) {
		resp := do(t, http.MethodPost, "/v1/tenants/alpha/projects", readKey.Secret, `{"id":"p1","name":"X"}`)
		defer func() { _ = resp.Body.Close() }()
		if resp.StatusCode != http.StatusForbidden {
			t.Fatalf("expected 403, got %d", resp.StatusCode)
		}
		var envelope errorEnvelope
		if err := json.NewDecoder(resp.Body).Decode(&envelope); err != nil {
			t.Fatal(err)
		}
		if envelope.Error.Code != "forbidden" {
			t.Fatalf("expected forbidden code, got %q", envelope.Error.Code)
		}
	})

	t.Run("read key can read", func(t *testing.T) {
		for _, path := range []string{
			"/v1/tenants/alpha/projects",
			"/v1/tenants/alpha/api-keys",
			"/v1/tenants/alpha/telemetry/features",
			"/v1/tenants/alpha/workload-profile",
			"/v1/tenants/alpha/policy-recommendation",
		} {
			resp := do(t, http.MethodGet, path, readKey.Secret, "")
			_ = resp.Body.Close()
			if resp.StatusCode != http.StatusOK {
				t.Fatalf("GET %s with read scope: expected 200, got %d", path, resp.StatusCode)
			}
		}
	})

	t.Run("read key cannot mutate anywhere", func(t *testing.T) {
		telemetryBody := `{"tenant_id":"alpha","service":"api","latency_ms":10}`
		for _, attempt := range []struct{ method, path, body string }{
			{http.MethodPost, "/v1/tenants/alpha/projects", `{"id":"p2","name":"X"}`},
			{http.MethodPost, "/v1/tenants/alpha/api-keys", `{"name":"escalate"}`},
			{http.MethodDelete, "/v1/tenants/alpha/api-keys/" + readKey.ID, ""},
			{http.MethodPost, "/v1/tenants/alpha/api-keys/" + readKey.ID + "/rotate", ""},
			{http.MethodPost, "/v1/telemetry", telemetryBody},
		} {
			resp := do(t, attempt.method, attempt.path, readKey.Secret, attempt.body)
			_ = resp.Body.Close()
			if resp.StatusCode != http.StatusForbidden {
				t.Fatalf("%s %s with read scope: expected 403, got %d", attempt.method, attempt.path, resp.StatusCode)
			}
		}
	})

	t.Run("full key can mutate", func(t *testing.T) {
		resp := do(t, http.MethodPost, "/v1/tenants/alpha/projects", fullKey.Secret, `{"id":"p1","name":"X"}`)
		_ = resp.Body.Close()
		if resp.StatusCode != http.StatusCreated {
			t.Fatalf("expected 201 for full scope, got %d", resp.StatusCode)
		}
	})

	t.Run("scoped key creation round-trips and validates", func(t *testing.T) {
		resp := do(t, http.MethodPost, "/v1/tenants/alpha/api-keys", fullKey.Secret, `{"name":"observer","scope":"read"}`)
		defer func() { _ = resp.Body.Close() }()
		if resp.StatusCode != http.StatusCreated {
			t.Fatalf("expected 201, got %d", resp.StatusCode)
		}
		var created tenant.APIKey
		if err := json.NewDecoder(resp.Body).Decode(&created); err != nil {
			t.Fatal(err)
		}
		if created.Scope != tenant.ScopeRead {
			t.Fatalf("expected read scope on created key, got %q", created.Scope)
		}

		bad := do(t, http.MethodPost, "/v1/tenants/alpha/api-keys", fullKey.Secret, `{"name":"bad","scope":"admin"}`)
		_ = bad.Body.Close()
		if bad.StatusCode != http.StatusBadRequest {
			t.Fatalf("expected 400 for invalid scope, got %d", bad.StatusCode)
		}
	})
}

func TestRecoverPanicsReturnsStructured500(t *testing.T) {
	server := NewServer(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		tenant.NewStore(),
		telemetry.NewStore(10),
		Config{AdminKey: "admin-test"},
	)
	panicking := server.recoverPanics(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		panic("boom")
	}))
	// Run through the full chain so the request carries a request ID and the
	// panic response is recorded like any other request.
	handler := server.requestLog(panicking)

	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/v1/panic", nil))

	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500, got %d", rec.Code)
	}
	var envelope errorEnvelope
	if err := json.NewDecoder(rec.Body).Decode(&envelope); err != nil {
		t.Fatalf("panic response is not the structured error envelope: %v", err)
	}
	if envelope.Error.Code != "internal_error" {
		t.Fatalf("expected internal_error code, got %q", envelope.Error.Code)
	}
	if envelope.Error.RequestID == "" {
		t.Fatal("panic response must carry the request ID for correlation")
	}
	if strings.Contains(envelope.Error.Message, "boom") {
		t.Fatal("panic details must not leak to the client")
	}
}

func TestRecoverPanicsRethrowsAbortHandler(t *testing.T) {
	server := NewServer(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		tenant.NewStore(),
		telemetry.NewStore(10),
		Config{AdminKey: "admin-test"},
	)
	handler := server.recoverPanics(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		panic(http.ErrAbortHandler)
	}))

	defer func() {
		if recovered := recover(); !errors.Is(recovered.(error), http.ErrAbortHandler) {
			t.Fatalf("http.ErrAbortHandler must propagate, got %v", recovered)
		}
	}()
	handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/v1/abort", nil))
	t.Fatal("expected the abort sentinel to re-panic")
}

func TestMetricsEndpointExposesRequestAndTelemetryMetrics(t *testing.T) {
	ctx := context.Background()
	tenantStore := tenant.NewStore()
	_, _ = tenantStore.CreateTenant(ctx, tenant.Tenant{ID: "alpha", Name: "Alpha"})
	key, _ := tenantStore.CreateAPIKey(ctx, "alpha", "test", tenant.ScopeFull)
	server := httptest.NewServer(NewServer(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		tenantStore,
		telemetry.NewStore(100),
		Config{AdminKey: "admin-test"},
	).Handler())
	defer server.Close()

	req, err := http.NewRequest(http.MethodPost, server.URL+"/v1/telemetry", bytes.NewBufferString(`{"tenant_id":"alpha","service":"ai-gateway","rps_window":12,"payload_bytes":12000,"latency_ms":250,"cache_hit":true,"embedding_density":0.7,"model_tier":"mid","child_spans":1}`))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-PolyForge-API-Key", key.Secret)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusAccepted {
		t.Fatalf("expected telemetry 202, got %d", resp.StatusCode)
	}

	resp, err = http.Get(server.URL + "/metrics")
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("expected metrics 200, got %d", resp.StatusCode)
	}
	if got := resp.Header.Get("Content-Type"); !strings.HasPrefix(got, "text/plain") {
		t.Fatalf("unexpected metrics content type: %q", got)
	}
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	text := string(body)
	for _, want := range []string{
		`polyforge_http_requests_total{method="POST",route="/v1/telemetry",status="202"} 1`,
		`polyforge_telemetry_events_total{service="ai-gateway",model_tier="mid",cache_hit="true"} 1`,
		`polyforge_telemetry_latency_ms_bucket{service="ai-gateway",le="250"}`,
		`polyforge_telemetry_payload_bytes_bucket{service="ai-gateway",le="16384"}`,
	} {
		if !strings.Contains(text, want) {
			t.Fatalf("metrics output missing %q:\n%s", want, text)
		}
	}
}
