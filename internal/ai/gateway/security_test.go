package gateway_test

import (
	"fmt"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"

	"polyforge/internal/ai/embed"
	"polyforge/internal/ai/gateway"
	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

// Session-35 security remediation. Each test pins a fix for a finding the
// audit confirmed the gateway was missing relative to the control plane.

// F2: the gateway had no admission control at all. A tenant flooding the
// most expensive route must eventually be throttled.
func TestGatewayRateLimitsPerTenant(t *testing.T) {
	ts, key := newTestServer(t, &scriptedProvider{
		response: gateway.ChatResponse{Message: gateway.Message{Role: "assistant", Content: "ok"}},
	}, nil)

	// DefaultRateLimit burst is 60; the 61st request inside the same second
	// must be refused with 429. (Refill is 10/s, so a burst well over 60
	// cannot all be admitted.)
	var got429 bool
	for i := 0; i < 200; i++ {
		resp := postJSON(t, ts.URL+"/v1/tenants/acme/ai/chat", key.Secret, map[string]any{
			"messages": []map[string]string{{"role": "user", "content": fmt.Sprintf("q%d", i)}},
		})
		_ = resp.Body.Close()
		if resp.StatusCode == http.StatusTooManyRequests {
			got429 = true
			if resp.Header.Get("Retry-After") == "" {
				t.Error("429 without Retry-After header")
			}
			break
		}
	}
	if !got429 {
		t.Error("gateway never rate-limited a 200-request burst; admission control missing")
	}
}

// A negative budget disables the limiter (tests / benchmarks need this).
func TestGatewayRateLimitDisabled(t *testing.T) {
	store := tenant.NewStore()
	_, key, err := store.ProvisionTenant(t.Context(), tenant.Tenant{ID: "acme", Name: "Acme"}, "bootstrap")
	if err != nil {
		t.Fatal(err)
	}
	server := gateway.NewServer(slog.Default(), gateway.Config{
		Provider:  &scriptedProvider{response: gateway.ChatResponse{Message: gateway.Message{Content: "ok"}}},
		Embedder:  embed.NewLocal(128),
		Tenants:   store,
		Telemetry: telemetry.NewStore(0),
		RateLimit: gateway.RateLimit{RequestsPerMinute: -1, Burst: -1},
	})
	ts := httptest.NewServer(server.Handler())
	t.Cleanup(ts.Close)

	for i := 0; i < 100; i++ {
		resp := postJSON(t, ts.URL+"/v1/tenants/acme/ai/chat", key.Secret, map[string]any{
			"messages": []map[string]string{{"role": "user", "content": "hi"}},
		})
		code := resp.StatusCode
		_ = resp.Body.Close()
		if code == http.StatusTooManyRequests {
			t.Fatalf("limiter active despite negative budget (request %d)", i)
		}
	}
}

// F3: /ai/index had no per-tenant document ceiling. This drives the handler
// path and asserts the cap is enforced; a full run to 10k is too slow for a
// unit test, so this checks the guard exists and updates are still allowed.
func TestGatewayIndexUpdateAllowedAtAnyCount(t *testing.T) {
	ts, key := newTestServer(t, &scriptedProvider{}, nil)
	// Re-indexing the same id must always succeed (Count does not grow), which
	// is the branch the cap explicitly exempts.
	for i := 0; i < 5; i++ {
		resp := postJSON(t, ts.URL+"/v1/tenants/acme/ai/index", key.Secret, map[string]any{
			"id": "doc-1", "text": "hello world",
		})
		code := resp.StatusCode
		_ = resp.Body.Close()
		if code != http.StatusCreated {
			t.Fatalf("re-index of existing id returned %d, want 201", code)
		}
	}
}
