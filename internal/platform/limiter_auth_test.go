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

// Final check-up (2026-10-13): the control plane keyed every credentialed
// request on a tenant path to "tenant:<id>" BEFORE authentication, so a
// caller with no valid credential, knowing only a tenant id, could spend that
// tenant's whole budget with bogus keys and lock it out. These tests pin the
// two-stage shape the AI gateway already has: failed authentication spends a
// per-source budget, and only a credential valid for the tenant spends the
// tenant's.

type limiterFixture struct {
	t       *testing.T
	handler http.Handler
	keys    map[string]string // tenant id -> full-scope secret
}

func newLimiterFixture(t *testing.T, burst int, tenants ...string) *limiterFixture {
	t.Helper()
	ctx := context.Background()
	store := tenant.NewStore()
	keys := map[string]string{}
	for _, id := range tenants {
		if _, err := store.CreateTenant(ctx, tenant.Tenant{ID: id, Name: id}); err != nil {
			t.Fatal(err)
		}
		key, err := store.CreateAPIKey(ctx, id, "k", tenant.ScopeFull)
		if err != nil {
			t.Fatal(err)
		}
		keys[id] = key.Secret
	}
	server := NewServer(slog.New(slog.NewTextHandler(io.Discard, nil)), store, telemetry.NewStore(100),
		Config{AdminKey: "admin-test", RateLimit: RateLimitConfig{RequestsPerMinute: 1, Burst: burst}})
	return &limiterFixture{t: t, handler: server.Handler(), keys: keys}
}

func (f *limiterFixture) do(method, path, source string, headers map[string]string, body []byte) *httptest.ResponseRecorder {
	f.t.Helper()
	req := httptest.NewRequest(method, path, bytes.NewReader(body))
	req.RemoteAddr = source + ":40000"
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	for name, value := range headers {
		req.Header.Set(name, value)
	}
	rec := httptest.NewRecorder()
	f.handler.ServeHTTP(rec, req)
	return rec
}

func (f *limiterFixture) listProjects(tenantID, source string, headers map[string]string) int {
	return f.do(http.MethodGet, "/v1/tenants/"+tenantID+"/projects", source, headers, nil).Code
}

func apiKey(secret string) map[string]string { return map[string]string{"X-PolyForge-API-Key": secret} }

const (
	attacker = "203.0.113.9"
	victimIP = "198.51.100.7"
)

func TestBogusKeysCannotSpendAnotherTenantsBudget(t *testing.T) {
	f := newLimiterFixture(t, 3, "victim")
	var codes []int
	for i := 0; i < 10; i++ {
		// A fresh bogus key each time, so no per-key bucket ever fills.
		codes = append(codes, f.listProjects("victim", attacker, apiKey("pf_live_bogus"+string(rune('a'+i)))))
	}
	for i, code := range codes {
		want := http.StatusUnauthorized
		if i >= 3 {
			want = http.StatusTooManyRequests // the attacker's own failure budget
		}
		if code != want {
			t.Fatalf("bogus request %d: got %d, want %d (all: %v)", i+1, code, want, codes)
		}
	}
	for i := 0; i < 3; i++ {
		if code := f.listProjects("victim", victimIP, apiKey(f.keys["victim"])); code != http.StatusOK {
			t.Fatalf("victim request %d after the flood: got %d, want 200", i+1, code)
		}
	}
}

func TestAnotherTenantsValidKeyCannotSpendTheBudget(t *testing.T) {
	f := newLimiterFixture(t, 3, "victim", "mallory")
	for i := 0; i < 5; i++ {
		if code := f.listProjects("victim", attacker, apiKey(f.keys["mallory"])); code != http.StatusUnauthorized &&
			code != http.StatusTooManyRequests {
			t.Fatalf("cross-tenant key request %d: got %d, want 401 or 429", i+1, code)
		}
	}
	for i := 0; i < 3; i++ {
		if code := f.listProjects("victim", victimIP, apiKey(f.keys["victim"])); code != http.StatusOK {
			t.Fatalf("victim request %d: got %d, want 200", i+1, code)
		}
	}
}

func TestAnotherTenantsValidTokenCannotSpendTheBudget(t *testing.T) {
	f := newLimiterFixture(t, 3, "victim", "mallory")
	rec := f.do(http.MethodPost, "/v1/auth/token", attacker, apiKey(f.keys["mallory"]), []byte(`{"tenant_id":"mallory"}`))
	if rec.Code != http.StatusOK {
		t.Fatalf("token issue: got %d: %s", rec.Code, rec.Body.String())
	}
	var pair struct {
		AccessToken string `json:"access_token"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &pair); err != nil || pair.AccessToken == "" {
		t.Fatalf("token issue: no access_token in %s", rec.Body.String())
	}
	bearer := map[string]string{"Authorization": "Bearer " + pair.AccessToken}
	for i := 0; i < 5; i++ {
		if code := f.listProjects("victim", attacker, bearer); code == http.StatusOK {
			t.Fatalf("cross-tenant token request %d was admitted", i+1)
		}
	}
	for i := 0; i < 3; i++ {
		if code := f.listProjects("victim", victimIP, apiKey(f.keys["victim"])); code != http.StatusOK {
			t.Fatalf("victim request %d: got %d, want 200", i+1, code)
		}
	}
}

func TestSuccessesDoNotSpendTheSourceFailureBudget(t *testing.T) {
	// One load generator driving two tenants: six successes from one address
	// with a failure budget of three must all pass, because a success gives
	// its reservation back.
	f := newLimiterFixture(t, 3, "alpha", "beta")
	for _, id := range []string{"alpha", "alpha", "alpha", "beta", "beta", "beta"} {
		if code := f.listProjects(id, victimIP, apiKey(f.keys[id])); code != http.StatusOK {
			t.Fatalf("%s from one source: got %d, want 200", id, code)
		}
	}
}

func TestValidKeysStillShareTheTenantBudget(t *testing.T) {
	// W13: a tenant cannot dodge its budget by minting keys or changing address.
	f := newLimiterFixture(t, 3, "alpha")
	ctxKey, err := func() (string, error) {
		rec := f.do(http.MethodPost, "/v1/tenants/alpha/api-keys", victimIP, apiKey(f.keys["alpha"]),
			[]byte(`{"name":"second","scope":"full"}`))
		var out struct {
			Secret string `json:"secret"`
		}
		return out.Secret, json.Unmarshal(rec.Body.Bytes(), &out)
	}()
	if err != nil || ctxKey == "" {
		t.Fatalf("second key: %v", err)
	}
	// The key creation above spent one; two more succeed, then 429.
	if code := f.listProjects("alpha", "192.0.2.1", apiKey(ctxKey)); code != http.StatusOK {
		t.Fatalf("second key, new address: got %d", code)
	}
	if code := f.listProjects("alpha", "192.0.2.2", apiKey(f.keys["alpha"])); code != http.StatusOK {
		t.Fatalf("first key, third address: got %d", code)
	}
	if code := f.listProjects("alpha", "192.0.2.3", apiKey(ctxKey)); code != http.StatusTooManyRequests {
		t.Fatalf("budget spent: got %d, want 429", code)
	}
}

func TestAdminKeyGuessingIsBudgetedPerSource(t *testing.T) {
	f := newLimiterFixture(t, 3, "alpha")
	guess := func(source string, i int) int {
		return f.do(http.MethodPost, "/v1/tenants/alpha/isolation", source,
			map[string]string{"X-PolyForge-Admin-Key": "guess" + string(rune('a'+i))}, []byte(`{"mode":"bridge"}`)).Code
	}
	for i := 0; i < 3; i++ {
		if code := guess(attacker, i); code != http.StatusUnauthorized {
			t.Fatalf("guess %d: got %d, want 401", i+1, code)
		}
	}
	if code := guess(attacker, 3); code != http.StatusTooManyRequests {
		t.Fatalf("guess 4 from the same source: got %d, want 429", code)
	}
	if code := guess(victimIP, 4); code != http.StatusUnauthorized {
		t.Fatalf("another source is not blocked by the attacker's budget: got %d, want 401", code)
	}
}
