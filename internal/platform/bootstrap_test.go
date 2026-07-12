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

// A fresh tenant's first API key cannot authenticate itself into existence;
// the admin key must be able to mint it (the bootstrap the eval harness and
// any new deployment rely on).
func TestAdminKeyBootstrapsFirstAPIKey(t *testing.T) {
	ctx := context.Background()
	tenantStore := tenant.NewStore()
	_, _ = tenantStore.CreateTenant(ctx, tenant.Tenant{ID: "alpha", Name: "Alpha"})

	server := httptest.NewServer(NewServer(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		tenantStore,
		telemetry.NewStore(100),
		Config{AdminKey: "admin-test"},
	).Handler())
	defer server.Close()

	mint := func(t *testing.T, adminKey string) *http.Response {
		t.Helper()
		req, err := http.NewRequest(http.MethodPost,
			server.URL+"/v1/tenants/alpha/api-keys",
			strings.NewReader(`{"name": "bootstrap", "scope": "full"}`))
		if err != nil {
			t.Fatal(err)
		}
		if adminKey != "" {
			req.Header.Set("X-PolyForge-Admin-Key", adminKey)
		}
		req.Header.Set("Content-Type", "application/json")
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = resp.Body.Close() })
		return resp
	}

	// No credential at all: rejected (the pre-existing behavior).
	if resp := mint(t, ""); resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("expected 401 with no credential, got %d", resp.StatusCode)
	}
	// Wrong admin key: rejected.
	if resp := mint(t, "not-the-admin-key"); resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("expected 401 for a wrong admin key, got %d", resp.StatusCode)
	}
	// Correct admin key mints the tenant's first key, secret included once.
	resp := mint(t, "admin-test")
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("expected 201 for the admin bootstrap, got %d", resp.StatusCode)
	}
	var key struct {
		Secret string `json:"secret"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&key); err != nil {
		t.Fatal(err)
	}
	if key.Secret == "" {
		t.Fatal("expected the minted key's secret in the response")
	}
	// The minted key authenticates as the tenant.
	if _, err := tenantStore.AuthenticateAPIKey(ctx, "alpha", key.Secret); err != nil {
		t.Fatalf("minted key does not authenticate: %v", err)
	}
}
