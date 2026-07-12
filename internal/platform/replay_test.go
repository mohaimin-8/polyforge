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

func TestReplayWorkloadBurnsRecordsAndRejects(t *testing.T) {
	ctx := context.Background()
	tenantStore := tenant.NewStore()
	telemetryStore := telemetry.NewStore(100)
	_, _ = tenantStore.CreateTenant(ctx, tenant.Tenant{ID: "alpha", Name: "Alpha"})
	_, _ = tenantStore.CreateTenant(ctx, tenant.Tenant{ID: "bravo", Name: "Bravo"})
	alphaKey, _ := tenantStore.CreateAPIKey(ctx, "alpha", "alpha", tenant.ScopeFull)
	bravoKey, _ := tenantStore.CreateAPIKey(ctx, "bravo", "bravo", tenant.ScopeFull)

	server := httptest.NewServer(NewServer(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		tenantStore,
		telemetryStore,
		Config{AdminKey: "admin-test"},
	).Handler())
	defer server.Close()

	post := func(t *testing.T, secret, body string) *http.Response {
		t.Helper()
		req, err := http.NewRequest(http.MethodPost,
			server.URL+"/v1/tenants/alpha/workloads/replay", strings.NewReader(body))
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("X-PolyForge-API-Key", secret)
		req.Header.Set("Content-Type", "application/json")
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = resp.Body.Close() })
		return resp
	}

	// An AI kind burns its work units and lands in telemetry with a tier.
	resp := post(t, alphaKey.Secret, `{"kind": "chat", "run": "smoke"}`)
	if resp.StatusCode != http.StatusAccepted {
		t.Fatalf("expected 202, got %d", resp.StatusCode)
	}
	var out struct {
		LatencyMS float64 `json:"latency_ms"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatal(err)
	}
	// chat = 20 wu at 1 ms CPU per wu: wall time is at least the burn.
	if out.LatencyMS < 20 {
		t.Fatalf("expected >=20ms burned for chat, got %vms", out.LatencyMS)
	}

	// A CRUD kind records without a model tier.
	if resp := post(t, alphaKey.Secret, `{"kind": "crud_read", "run": "smoke"}`); resp.StatusCode != http.StatusAccepted {
		t.Fatalf("expected 202 for crud_read, got %d", resp.StatusCode)
	}

	events, err := telemetryStore.RecentByTenant(ctx, "alpha", 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(events) != 2 {
		t.Fatalf("expected 2 recorded events, got %d", len(events))
	}
	byService := map[string]telemetry.Event{}
	for _, e := range events {
		byService[e.Service] = e
	}
	if byService["chat"].ModelTier != "small" || byService["chat"].LatencyMS < 20 {
		t.Fatalf("chat event mis-recorded: %+v", byService["chat"])
	}
	if byService["crud_read"].ModelTier != "" {
		t.Fatalf("crud event must carry no tier, got %+v", byService["crud_read"])
	}

	// Unknown kinds are rejected, foreign tenants are unauthorized.
	if resp := post(t, alphaKey.Secret, `{"kind": "mystery"}`); resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("expected 400 for unknown kind, got %d", resp.StatusCode)
	}
	if resp := post(t, bravoKey.Secret, `{"kind": "chat"}`); resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("expected 401 for a foreign key, got %d", resp.StatusCode)
	}
}
