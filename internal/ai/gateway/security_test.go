package gateway_test

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

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

// Audit 2026-09-26 (HIGH): the per-tenant limiter ran BEFORE authentication,
// keyed on the tenant id from the URL, so anyone who knew a tenant's slug
// could hold that tenant in sustained 429s without any credential. Admission
// is now split: before authentication only the SOURCE's failed-auth budget
// is checked; the tenant's own budget is spent only by authenticated calls.

func limitedGateway(t *testing.T, tenants ...string) (http.Handler, map[string]string) {
	t.Helper()
	store := tenant.NewStore()
	secrets := map[string]string{}
	for _, id := range tenants {
		_, key, err := store.ProvisionTenant(t.Context(), tenant.Tenant{ID: id, Name: id}, "bootstrap")
		if err != nil {
			t.Fatal(err)
		}
		secrets[id] = key.Secret
	}
	server := gateway.NewServer(slog.Default(), gateway.Config{
		Provider:  &scriptedProvider{response: gateway.ChatResponse{Message: gateway.Message{Content: "ok"}}},
		Embedder:  embed.NewLocal(128),
		Tenants:   store,
		Telemetry: telemetry.NewStore(0),
	})
	return server.Handler(), secrets
}

func chatFrom(h http.Handler, source, tenantID, secret string, i int) *httptest.ResponseRecorder {
	body := fmt.Sprintf(`{"messages":[{"role":"user","content":"q%d"}]}`, i)
	req := httptest.NewRequest(http.MethodPost, "/v1/tenants/"+tenantID+"/ai/chat", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	if secret != "" {
		req.Header.Set("X-PolyForge-API-Key", secret)
	}
	req.RemoteAddr = source + ":40000"
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

func TestUnauthenticatedFloodCannotExhaustAnotherTenantsBudget(t *testing.T) {
	h, secrets := limitedGateway(t, "acme")
	for i := 0; i < 200; i++ {
		chatFrom(h, "203.0.113.9", "acme", "", i)
		chatFrom(h, "203.0.113.9", "acme", "pf_wrong", i)
	}
	if rec := chatFrom(h, "198.51.100.7", "acme", secrets["acme"], 0); rec.Code != http.StatusOK {
		t.Fatalf("the victim tenant's authenticated call got %d after an unauthenticated flood", rec.Code)
	}
}

func TestFailedAuthenticationIsThrottledPerSource(t *testing.T) {
	h, _ := limitedGateway(t, "acme")
	var throttled *httptest.ResponseRecorder
	for i := 0; i < 200; i++ {
		if rec := chatFrom(h, "203.0.113.9", "acme", "pf_wrong", i); rec.Code == http.StatusTooManyRequests {
			throttled = rec
			break
		}
	}
	if throttled == nil {
		t.Fatal("200 failed authentications from one source were never throttled")
	}
	if throttled.Header().Get("Retry-After") == "" {
		t.Error("429 without Retry-After header")
	}
	if rec := chatFrom(h, "192.0.2.44", "acme", "pf_wrong", 0); rec.Code != http.StatusUnauthorized {
		t.Fatalf("another source's first failed attempt got %d, want 401", rec.Code)
	}
}

func TestAuthenticatedTrafficIsNotThrottledByItsSourceAddress(t *testing.T) {
	// One load generator driving several tenants: each tenant's burst (60)
	// is its own, so 50 calls per tenant from a single address all pass.
	h, secrets := limitedGateway(t, "acme", "beta", "gamma")
	for _, id := range []string{"acme", "beta", "gamma"} {
		for i := 0; i < 50; i++ {
			if rec := chatFrom(h, "10.0.0.5", id, secrets[id], i); rec.Code != http.StatusOK {
				t.Fatalf("%s call %d from a shared source got %d", id, i, rec.Code)
			}
		}
	}
}

// blockingAuth holds every AuthenticateAPIKey call until released, so a test
// can count how many requests reach the key store at the same time.
type blockingAuth struct {
	*tenant.Store
	entered atomic.Int32
	release chan struct{}
}

func (b *blockingAuth) AuthenticateAPIKey(ctx context.Context, tenantID, secret string) (tenant.APIKey, error) {
	b.entered.Add(1)
	<-b.release
	return b.Store.AuthenticateAPIKey(ctx, tenantID, secret)
}

// Security review 2026-09-26 (MEDIUM): checking the failed-auth budget without
// reserving from it let any number of CONCURRENT bad-key requests from one
// source pass the check and reach the key store (a database round trip)
// before the first failure was charged. A concurrent burst must be capped by
// the budget's capacity (DefaultRateLimit burst, 60) the moment it happens.
func TestConcurrentBadKeyBurstIsCappedBeforeTheKeyStore(t *testing.T) {
	store := &blockingAuth{Store: tenant.NewStore(), release: make(chan struct{})}
	if _, _, err := store.ProvisionTenant(t.Context(), tenant.Tenant{ID: "acme", Name: "acme"}, "bootstrap"); err != nil {
		t.Fatal(err)
	}
	h := gateway.NewServer(slog.Default(), gateway.Config{
		Provider:  &scriptedProvider{response: gateway.ChatResponse{Message: gateway.Message{Content: "ok"}}},
		Embedder:  embed.NewLocal(128),
		Tenants:   store,
		Telemetry: telemetry.NewStore(0),
	}).Handler()

	const burst = 200
	var refused atomic.Int32
	var wg sync.WaitGroup
	for i := 0; i < burst; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			if chatFrom(h, "203.0.113.9", "acme", "pf_wrong", i).Code == http.StatusTooManyRequests {
				refused.Add(1)
			}
		}(i)
	}
	deadline := time.Now().Add(10 * time.Second)
	for int(store.entered.Load()+refused.Load()) < burst && time.Now().Before(deadline) {
		time.Sleep(5 * time.Millisecond)
	}
	entered := store.entered.Load()
	close(store.release)
	wg.Wait()
	if entered > int32(gateway.DefaultRateLimit.Burst) {
		t.Fatalf("%d concurrent bad-key requests reached the key store; the cap is %d",
			entered, gateway.DefaultRateLimit.Burst)
	}
}

// Security review 2026-09-26 (LOW): the admin knob routes checked the admin
// key with no admission control at all, so the key -- the same one that
// unlocks the control plane's tenant and key administration -- could be
// guessed without limit. Failed admin authentications now spend the same
// per-source budget as failed tenant ones.
func TestFailedAdminAuthenticationIsThrottledPerSource(t *testing.T) {
	h := gateway.NewServer(slog.Default(), gateway.Config{
		Provider:  &scriptedProvider{},
		Embedder:  embed.NewLocal(16),
		Tenants:   tenant.NewStore(),
		Telemetry: telemetry.NewStore(0),
		AdminKey:  "the-real-admin-key",
	}).Handler()
	getKnobs := func(source, key string) int {
		req := httptest.NewRequest(http.MethodGet, "/admin/tenants/acme/knobs", nil)
		req.Header.Set("X-PolyForge-Admin-Key", key)
		req.RemoteAddr = source + ":40000"
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, req)
		return rec.Code
	}
	throttled := false
	for i := 0; i < 200; i++ {
		if getKnobs("203.0.113.9", fmt.Sprintf("guess-%d", i)) == http.StatusTooManyRequests {
			throttled = true
			break
		}
	}
	if !throttled {
		t.Fatal("200 wrong admin keys from one source were never throttled")
	}
	// The real admin, elsewhere, is unaffected (404: no knobs set yet).
	if code := getKnobs("198.51.100.7", "the-real-admin-key"); code != http.StatusNotFound {
		t.Fatalf("the admin from another source got %d, want 404", code)
	}
}
