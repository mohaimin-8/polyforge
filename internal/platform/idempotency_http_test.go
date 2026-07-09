package platform

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"

	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

// TestIdempotencyKeyMakesWritesRepeatSafe is the W14 gate: retrying a write
// with the same Idempotency-Key replays the original response instead of
// executing the write twice.
func TestIdempotencyKeyMakesWritesRepeatSafe(t *testing.T) {
	ctx := context.Background()
	tenantStore := tenant.NewStore()
	_, _ = tenantStore.CreateTenant(ctx, tenant.Tenant{ID: "alpha", Name: "Alpha"})
	key, _ := tenantStore.CreateAPIKey(ctx, "alpha", "writer", tenant.ScopeFull)

	server := httptest.NewServer(NewServer(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		tenantStore,
		telemetry.NewStore(100),
		Config{AdminKey: "admin-test"},
	).Handler())
	defer server.Close()

	post := func(idempotencyKey, body string) (*http.Response, string) {
		req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/tenants/alpha/projects", bytes.NewBufferString(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("X-PolyForge-API-Key", key.Secret)
		if idempotencyKey != "" {
			req.Header.Set("Idempotency-Key", idempotencyKey)
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer func() { _ = resp.Body.Close() }()
		payload, _ := io.ReadAll(resp.Body)
		return resp, string(payload)
	}

	first, firstBody := post("op-123", `{"id":"proj-1","name":"Project One"}`)
	if first.StatusCode != http.StatusCreated {
		t.Fatalf("expected 201, got %d: %s", first.StatusCode, firstBody)
	}
	if first.Header.Get("Idempotency-Replayed") != "" {
		t.Fatal("first execution must not be marked replayed")
	}

	// The retry replays: same status, byte-identical body, marked replayed,
	// and crucially the write did not run twice (no 409 from the repo).
	second, secondBody := post("op-123", `{"id":"proj-1","name":"Project One"}`)
	if second.StatusCode != http.StatusCreated {
		t.Fatalf("replay must return the original 201, got %d: %s", second.StatusCode, secondBody)
	}
	if second.Header.Get("Idempotency-Replayed") != "true" {
		t.Fatal("replay must be marked with Idempotency-Replayed")
	}
	if secondBody != firstBody {
		t.Fatalf("replayed body diverged:\nfirst:  %s\nsecond: %s", firstBody, secondBody)
	}

	// Exactly one project exists.
	var page struct {
		Items []tenant.Project `json:"items"`
	}
	req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/tenants/alpha/projects", nil)
	req.Header.Set("X-PolyForge-API-Key", key.Secret)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = resp.Body.Close() }()
	if err := json.NewDecoder(resp.Body).Decode(&page); err != nil {
		t.Fatal(err)
	}
	if len(page.Items) != 1 {
		t.Fatalf("the write must have executed exactly once, found %d projects", len(page.Items))
	}

	// The same key with a different body is a different operation: it
	// executes for real and surfaces the repo's duplicate-ID conflict
	// rather than replaying the wrong cached response.
	third, thirdBody := post("op-123", `{"id":"proj-1","name":"Different Payload"}`)
	if third.StatusCode != http.StatusConflict {
		t.Fatalf("expected 409 for reused key with new body, got %d: %s", third.StatusCode, thirdBody)
	}
	if third.Header.Get("Idempotency-Replayed") != "" {
		t.Fatal("a different payload must not be served from the cache")
	}

	// Without the header nothing is cached; the duplicate write hits the
	// repository and conflicts as before.
	fourth, _ := post("", `{"id":"proj-1","name":"Project One"}`)
	if fourth.StatusCode != http.StatusConflict {
		t.Fatalf("expected 409 without idempotency header, got %d", fourth.StatusCode)
	}
}
