package platform

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

func TestIsolationPromotionEndpoint(t *testing.T) {
	ctx := context.Background()
	tenantStore := tenant.NewStore()
	_, _ = tenantStore.CreateTenant(ctx, tenant.Tenant{ID: "acme", Name: "Acme"})
	apiKey, _ := tenantStore.CreateAPIKey(ctx, "acme", "k", tenant.ScopeFull)

	server := httptest.NewServer(NewServer(slog.New(slog.NewTextHandler(io.Discard, nil)),
		tenantStore, telemetry.NewStore(100), Config{AdminKey: "admin-test"}).Handler())
	defer server.Close()

	promote := func(mode, adminKey string) *http.Response {
		req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/tenants/acme/isolation",
			strings.NewReader(`{"mode":"`+mode+`"}`))
		req.Header.Set("Content-Type", "application/json")
		if adminKey != "" {
			req.Header.Set("X-PolyForge-Admin-Key", adminKey)
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		return resp
	}

	t.Run("requires the admin key, not a tenant key", func(t *testing.T) {
		resp := promote("bridge", "")
		defer func() { _ = resp.Body.Close() }()
		if resp.StatusCode != http.StatusUnauthorized {
			t.Fatalf("no-credentials status = %d, want 401", resp.StatusCode)
		}
		req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/tenants/acme/isolation",
			strings.NewReader(`{"mode":"bridge"}`))
		req.Header.Set("X-PolyForge-API-Key", apiKey.Secret)
		resp2, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer func() { _ = resp2.Body.Close() }()
		if resp2.StatusCode != http.StatusUnauthorized {
			t.Fatalf("tenant-key status = %d, want 401", resp2.StatusCode)
		}
	})

	t.Run("promotes pool to bridge", func(t *testing.T) {
		resp := promote("bridge", "admin-test")
		defer func() { _ = resp.Body.Close() }()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("status = %d, want 200", resp.StatusCode)
		}
		var updated tenant.Tenant
		if err := json.NewDecoder(resp.Body).Decode(&updated); err != nil {
			t.Fatal(err)
		}
		if updated.IsolationMode != tenant.IsolationBridge {
			t.Fatalf("mode = %q, want bridge", updated.IsolationMode)
		}
	})

	t.Run("rejects a downgrade with 409", func(t *testing.T) {
		resp := promote("pool", "admin-test")
		defer func() { _ = resp.Body.Close() }()
		if resp.StatusCode != http.StatusConflict {
			t.Fatalf("status = %d, want 409", resp.StatusCode)
		}
	})

	t.Run("rejects an unknown mode with 400", func(t *testing.T) {
		resp := promote("fortress", "admin-test")
		defer func() { _ = resp.Body.Close() }()
		if resp.StatusCode != http.StatusBadRequest {
			t.Fatalf("status = %d, want 400", resp.StatusCode)
		}
	})
}
