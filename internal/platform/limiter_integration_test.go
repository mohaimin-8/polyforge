package platform

import (
	"context"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/redis/go-redis/v9"

	"polyforge/internal/limit"
	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

// TestRedisSlidingWindowLimitsPerTenant is the W13 middleware gate: the
// Redis-backed window is shared per tenant, so two different API keys of the
// same tenant spend one budget, and exhausting it yields 429 + Retry-After.
func TestRedisSlidingWindowLimitsPerTenant(t *testing.T) {
	redisServer := miniredis.RunT(t)
	client := redis.NewClient(&redis.Options{Addr: redisServer.Addr()})
	t.Cleanup(func() { _ = client.Close() })

	ctx := context.Background()
	tenantStore := tenant.NewStore()
	_, _ = tenantStore.CreateTenant(ctx, tenant.Tenant{ID: "alpha", Name: "Alpha"})
	keyOne, _ := tenantStore.CreateAPIKey(ctx, "alpha", "one", tenant.ScopeRead)
	keyTwo, _ := tenantStore.CreateAPIKey(ctx, "alpha", "two", tenant.ScopeRead)

	server := httptest.NewServer(NewServer(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		tenantStore,
		telemetry.NewStore(100),
		Config{
			AdminKey: "admin-test",
			Limiter:  limit.NewSlidingWindow(client, 3, time.Minute),
		},
	).Handler())
	defer server.Close()

	get := func(secret string) *http.Response {
		req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/tenants/alpha/projects", nil)
		req.Header.Set("X-PolyForge-API-Key", secret)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		_ = resp.Body.Close()
		return resp
	}

	// Two requests on key one, one on key two: budget of 3 is now spent.
	for i, secret := range []string{keyOne.Secret, keyOne.Secret, keyTwo.Secret} {
		if resp := get(secret); resp.StatusCode != http.StatusOK {
			t.Fatalf("request %d: expected 200, got %d", i+1, resp.StatusCode)
		}
	}
	resp := get(keyTwo.Secret)
	if resp.StatusCode != http.StatusTooManyRequests {
		t.Fatalf("expected 429 once the shared tenant budget is spent, got %d", resp.StatusCode)
	}
	if resp.Header.Get("Retry-After") == "" {
		t.Fatal("429 must carry Retry-After")
	}

	// Health stays exempt, and the limiter failing does not take the API
	// down: kill Redis and the request is admitted (fail-open).
	if resp := get(keyOne.Secret); resp.StatusCode != http.StatusTooManyRequests {
		t.Fatalf("budget should still be exhausted, got %d", resp.StatusCode)
	}
	redisServer.Close()
	if resp := get(keyOne.Secret); resp.StatusCode != http.StatusOK {
		t.Fatalf("expected fail-open 200 with Redis down, got %d", resp.StatusCode)
	}
}
