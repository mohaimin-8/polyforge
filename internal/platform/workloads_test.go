package platform

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"math/rand"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"polyforge/internal/classifier"
	"polyforge/internal/classifier/online"
	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

func workloadFixture(t *testing.T) *httptest.Server {
	t.Helper()
	ctx := context.Background()
	tenantStore := tenant.NewStore()
	telemetryStore := telemetry.NewStore(1000)
	if _, err := tenantStore.CreateTenant(ctx, tenant.Tenant{ID: "acme", Name: "Acme"}); err != nil {
		t.Fatal(err)
	}
	rng := rand.New(rand.NewSource(5))
	for _, event := range classifier.SyntheticWindow(rng, classifier.AICacheable) {
		event.TenantID = "acme"
		if _, err := telemetryStore.Add(ctx, event); err != nil {
			t.Fatal(err)
		}
	}
	registry := online.NewRegistry(0)
	poller := online.NewPoller(tenantStore, telemetryStore, online.RulePredictor{}, nil, nil, registry,
		slog.New(slog.NewTextHandler(io.Discard, nil)), online.Config{})
	poller.Tick(ctx)

	server := httptest.NewServer(NewServer(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		tenantStore,
		telemetryStore,
		Config{AdminKey: "admin-test", Workloads: registry},
	).Handler())
	t.Cleanup(server.Close)
	return server
}

func adminGet(t *testing.T, server *httptest.Server, path, key string) *http.Response {
	t.Helper()
	req, err := http.NewRequest(http.MethodGet, server.URL+path, nil)
	if err != nil {
		t.Fatal(err)
	}
	if key != "" {
		req.Header.Set("X-PolyForge-Admin-Key", key)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = resp.Body.Close() })
	return resp
}

func TestAdminWorkloadsRequiresAdminKey(t *testing.T) {
	server := workloadFixture(t)
	if resp := adminGet(t, server, "/v1/admin/workloads", ""); resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("no key status = %d, want 401", resp.StatusCode)
	}
	if resp := adminGet(t, server, "/v1/admin/workloads", "wrong"); resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("wrong key status = %d, want 401", resp.StatusCode)
	}
}

func TestAdminWorkloadsServesSnapshotAndHistory(t *testing.T) {
	server := workloadFixture(t)
	resp := adminGet(t, server, "/v1/admin/workloads", "admin-test")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("snapshot status = %d", resp.StatusCode)
	}
	var snapshot struct {
		Tenants []online.TenantWorkload `json:"tenants"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&snapshot); err != nil {
		t.Fatal(err)
	}
	if len(snapshot.Tenants) != 1 || snapshot.Tenants[0].TenantID != "acme" {
		t.Fatalf("snapshot = %+v", snapshot)
	}
	if snapshot.Tenants[0].Label == classifier.Unknown {
		t.Fatal("classified tenant reported unknown")
	}

	history := adminGet(t, server, "/v1/admin/workloads/acme/history", "admin-test")
	if history.StatusCode != http.StatusOK {
		t.Fatalf("history status = %d", history.StatusCode)
	}
	var payload struct {
		Samples []online.Sample `json:"samples"`
	}
	if err := json.NewDecoder(history.Body).Decode(&payload); err != nil {
		t.Fatal(err)
	}
	if len(payload.Samples) != 1 {
		t.Fatalf("history samples = %d, want 1", len(payload.Samples))
	}
}

func TestWorkloadsDashboardServesShellWithoutAuth(t *testing.T) {
	server := workloadFixture(t)
	resp := adminGet(t, server, "/admin/workloads", "")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("dashboard status = %d", resp.StatusCode)
	}
	body, _ := io.ReadAll(resp.Body)
	if !strings.Contains(string(body), "live workload classes") {
		t.Fatal("dashboard shell missing")
	}
	if !strings.Contains(resp.Header.Get("Content-Security-Policy"), "connect-src 'self'") {
		t.Fatal("dashboard must carry a CSP")
	}
}

func TestAdminWorkloadsWithoutClassifierReturns503(t *testing.T) {
	server := httptest.NewServer(NewServer(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		tenant.NewStore(),
		telemetry.NewStore(10),
		Config{AdminKey: "admin-test"},
	).Handler())
	defer server.Close()
	resp := adminGet(t, server, "/v1/admin/workloads", "admin-test")
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503", resp.StatusCode)
	}
}
