package gateway_test

// Tests for the per-tenant knob surface (PREREG_WAVE4_LIVE_PLANE.md
// §Substrate 2-3): these are the in-process versions of the WL-H2
// knob-liveness assertions — the tier knob must move routing and metering,
// the cache knob must move hit behavior — so an inert knob fails here at
// the desk before it can waste a live run.

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"polyforge/internal/ai/embed"
	"polyforge/internal/ai/gateway"
	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

const testAdminKey = "test-admin-key"

func newKnobsServer(t *testing.T, tiers map[string]gateway.Provider) (*httptest.Server, tenant.APIKey, *telemetry.Store) {
	t.Helper()
	store := tenant.NewStore()
	_, key, err := store.ProvisionTenant(t.Context(), tenant.Tenant{ID: "acme", Name: "Acme"}, "bootstrap")
	if err != nil {
		t.Fatalf("provision: %v", err)
	}
	events := telemetry.NewStore(0)
	server := gateway.NewServer(slog.Default(), gateway.Config{
		Provider:      &scriptedProvider{response: gateway.ChatResponse{Model: "default", Message: gateway.Message{Role: "assistant", Content: "default answer"}}},
		Embedder:      embed.NewLocal(128),
		Tenants:       store,
		Telemetry:     events,
		TierProviders: tiers,
		AdminKey:      testAdminKey,
	})
	ts := httptest.NewServer(server.Handler())
	t.Cleanup(ts.Close)
	return ts, key, events
}

func putKnobs(t *testing.T, base, adminKey, tenantID string, body any) *http.Response {
	t.Helper()
	raw, _ := json.Marshal(body)
	req, _ := http.NewRequest(http.MethodPut, base+"/admin/tenants/"+tenantID+"/knobs", strings.NewReader(string(raw)))
	req.Header.Set("Content-Type", "application/json")
	if adminKey != "" {
		req.Header.Set("X-PolyForge-Admin-Key", adminKey)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("put knobs: %v", err)
	}
	return resp
}

func TestKnobsRequireAdminKey(t *testing.T) {
	ts, _, _ := newKnobsServer(t, nil)
	resp := putKnobs(t, ts.URL, "wrong-key", "acme", gateway.TenantKnobs{CacheSizeMB: 1})
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", resp.StatusCode)
	}
}

func TestKnobsSurfaceDisabledWithoutAdminKey(t *testing.T) {
	store := tenant.NewStore()
	server := gateway.NewServer(slog.Default(), gateway.Config{
		Provider: &scriptedProvider{},
		Embedder: embed.NewLocal(128),
		Tenants:  store,
	})
	ts := httptest.NewServer(server.Handler())
	t.Cleanup(ts.Close)
	resp := putKnobs(t, ts.URL, "any", "acme", gateway.TenantKnobs{})
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusNotImplemented {
		t.Fatalf("status = %d, want 501 when no admin key is configured", resp.StatusCode)
	}
}

func TestKnobsRejectUnknownTier(t *testing.T) {
	ts, _, _ := newKnobsServer(t, map[string]gateway.Provider{
		"small": &scriptedProvider{},
	})
	resp := putKnobs(t, ts.URL, testAdminKey, "acme", gateway.TenantKnobs{ModelTier: "huge", CacheSizeMB: 1})
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400 for a tier with no backend (silent fallback would be an inert knob)", resp.StatusCode)
	}
}

// TestTierKnobMovesRoutingAndTelemetry is the tier half of the WL-H2 gate:
// switching the knob must change the backend actually consulted, the
// response headers, and the tier recorded per event.
func TestTierKnobMovesRoutingAndTelemetry(t *testing.T) {
	small := &scriptedProvider{response: gateway.ChatResponse{Model: "small-model", Message: gateway.Message{Role: "assistant", Content: "small answer"}}}
	mid := &scriptedProvider{response: gateway.ChatResponse{Model: "mid-model", Message: gateway.Message{Role: "assistant", Content: "mid answer"}}}
	ts, key, events := newKnobsServer(t, map[string]gateway.Provider{"small": small, "mid": mid})

	// cache_size_mb=0 disables the cache so every request hits a backend.
	if resp := putKnobs(t, ts.URL, testAdminKey, "acme", gateway.TenantKnobs{ModelTier: "small", CacheSizeMB: 0}); resp.StatusCode != http.StatusOK {
		t.Fatalf("put small knobs: %d", resp.StatusCode)
	}
	first := postJSON(t, ts.URL+"/v1/tenants/acme/ai/chat", key.Secret, map[string]any{
		"messages": []map[string]string{{"role": "user", "content": "prompt one"}},
	})
	defer func() { _ = first.Body.Close() }()
	if got := first.Header.Get("X-PolyForge-Tier"); got != "small" {
		t.Fatalf("tier header = %q, want small", got)
	}
	if got := first.Header.Get("X-PolyForge-Backend"); got != "tier:small" {
		t.Fatalf("backend header = %q, want tier:small", got)
	}
	if small.calls != 1 || mid.calls != 0 {
		t.Fatalf("backend calls small=%d mid=%d, want 1/0", small.calls, mid.calls)
	}

	if resp := putKnobs(t, ts.URL, testAdminKey, "acme", gateway.TenantKnobs{ModelTier: "mid", CacheSizeMB: 0}); resp.StatusCode != http.StatusOK {
		t.Fatalf("put mid knobs: %d", resp.StatusCode)
	}
	second := postJSON(t, ts.URL+"/v1/tenants/acme/ai/chat", key.Secret, map[string]any{
		"messages": []map[string]string{{"role": "user", "content": "prompt two"}},
	})
	defer func() { _ = second.Body.Close() }()
	if got := second.Header.Get("X-PolyForge-Tier"); got != "mid" {
		t.Fatalf("tier header after switch = %q, want mid", got)
	}
	if small.calls != 1 || mid.calls != 1 {
		t.Fatalf("backend calls small=%d mid=%d, want 1/1", small.calls, mid.calls)
	}

	// Telemetry must carry the tier actually hit, per request.
	recent, err := events.RecentByTenant(context.Background(), "acme", 10)
	if err != nil {
		t.Fatalf("read telemetry: %v", err)
	}
	if len(recent) != 2 {
		t.Fatalf("expected 2 events, got %d", len(recent))
	}
	tiers := map[string]bool{}
	for _, e := range recent {
		tiers[e.ModelTier] = true
	}
	if !tiers["small"] || !tiers["mid"] {
		t.Fatalf("telemetry tiers = %v, want both small and mid", tiers)
	}
}

// TestCacheKnobMovesHitBehavior is the cache half of the WL-H2 gate: with a
// budget the repeated prompt hits (short-circuiting the backend); at budget
// zero everything misses; shrinking back to zero evicts what was stored.
func TestCacheKnobMovesHitBehavior(t *testing.T) {
	small := &scriptedProvider{response: gateway.ChatResponse{Model: "small-model", Message: gateway.Message{Role: "assistant", Content: "cached answer"}}}
	ts, key, events := newKnobsServer(t, map[string]gateway.Provider{"small": small})
	body := map[string]any{"messages": []map[string]string{{"role": "user", "content": "repeat me"}}}
	chat := func() string {
		resp := postJSON(t, ts.URL+"/v1/tenants/acme/ai/chat", key.Secret, body)
		defer func() { _ = resp.Body.Close() }()
		return resp.Header.Get("X-PolyForge-Cache")
	}

	// Budget on: miss then hit, backend consulted exactly once.
	if resp := putKnobs(t, ts.URL, testAdminKey, "acme", gateway.TenantKnobs{ModelTier: "small", CacheSizeMB: 8}); resp.StatusCode != http.StatusOK {
		t.Fatalf("put knobs: %d", resp.StatusCode)
	}
	if got := chat(); got != "miss" {
		t.Fatalf("first request = %q, want miss", got)
	}
	if got := chat(); got != "hit" {
		t.Fatalf("second request = %q, want hit", got)
	}
	if small.calls != 1 {
		t.Fatalf("backend consulted %d times, want 1 (hit must short-circuit)", small.calls)
	}

	// Budget zero: the stored entry is evicted and admission is disabled,
	// so the same prompt misses again and keeps missing.
	if resp := putKnobs(t, ts.URL, testAdminKey, "acme", gateway.TenantKnobs{ModelTier: "small", CacheSizeMB: 0}); resp.StatusCode != http.StatusOK {
		t.Fatalf("put zero knobs: %d", resp.StatusCode)
	}
	if got := chat(); got != "miss" {
		t.Fatalf("request after budget zero = %q, want miss (entry must be evicted)", got)
	}
	if got := chat(); got != "miss" {
		t.Fatalf("repeat at budget zero = %q, want miss (admission must be disabled)", got)
	}
	if small.calls != 3 {
		t.Fatalf("backend consulted %d times, want 3", small.calls)
	}

	// The hit event carries the knob tier (AI family) but is flagged as a
	// hit, which is what makes eval-export charge it nothing.
	recent, err := events.RecentByTenant(context.Background(), "acme", 10)
	if err != nil {
		t.Fatalf("read telemetry: %v", err)
	}
	hits := 0
	for _, e := range recent {
		if e.CacheHit {
			hits++
			if e.ModelTier != "small" {
				t.Fatalf("hit event tier = %q, want small (stays in the AI family)", e.ModelTier)
			}
		}
	}
	if hits != 1 {
		t.Fatalf("expected exactly 1 hit event, got %d", hits)
	}
}

func TestBuildTierProviders(t *testing.T) {
	providers, err := gateway.BuildTierProviders(`{
		"small": {"kind": "openai", "base_url": "http://127.0.0.1:9101/v1", "model": "qwen2.5-0.5b"},
		"mid": {"kind": "ollama", "base_url": "http://127.0.0.1:11434", "model": "qwen2.5-3b"}
	}`)
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	if len(providers) != 2 || providers["small"] == nil || providers["mid"] == nil {
		t.Fatalf("providers = %v", providers)
	}
	if _, err := gateway.BuildTierProviders(`{"x": {"kind": "grpc", "base_url": "u", "model": "m"}}`); err == nil {
		t.Fatal("unknown kind must be rejected")
	}
	if _, err := gateway.BuildTierProviders(`{"x": {"kind": "openai"}}`); err == nil {
		t.Fatal("missing base_url/model must be rejected")
	}
	if providers, err := gateway.BuildTierProviders(""); err != nil || providers != nil {
		t.Fatalf("empty input must be a valid no-tier deployment, got %v/%v", providers, err)
	}
}

// TestTierPinIsExclusiveAcrossManyRequests is the regression test for WP8b's
// SUBSTRATE INADEQUATE verdict. The WL-H2 preflight found the tier pin
// ASYMMETRIC on a live cluster: pinning "small" served only ["small"], but
// pinning "mid" served ["mid","small"] across its eight probe requests, so
// routing_moved was false and WL-H1 was voided.
//
// TestTierKnobMovesRoutingAndTelemetry above sends ONE request per tier and
// passes, which is exactly why the leak survived: a single request cannot see
// an intermittent one. This sends the same number the probe does and asserts
// the pin holds for every one of them.
func TestTierPinIsExclusiveAcrossManyRequests(t *testing.T) {
	small := &scriptedProvider{response: gateway.ChatResponse{Model: "small-model", Message: gateway.Message{Role: "assistant", Content: "small answer"}}}
	mid := &scriptedProvider{response: gateway.ChatResponse{Model: "mid-model", Message: gateway.Message{Role: "assistant", Content: "mid answer"}}}
	ts, key, _ := newKnobsServer(t, map[string]gateway.Provider{"small": small, "mid": mid})

	const probes = 8
	for _, tier := range []string{"small", "mid"} {
		if resp := putKnobs(t, ts.URL, testAdminKey, "acme",
			gateway.TenantKnobs{ModelTier: tier, CacheSizeMB: 0}); resp.StatusCode != http.StatusOK {
			t.Fatalf("put %s knobs: %d", tier, resp.StatusCode)
		}
		seen := map[string]int{}
		for i := 0; i < probes; i++ {
			// Distinct prompts, mirroring the probe: a cache hit would
			// short-circuit routing and prove nothing either way.
			resp := postJSON(t, ts.URL+"/v1/tenants/acme/ai/chat", key.Secret, map[string]any{
				"messages": []map[string]string{{"role": "user", "content": fmt.Sprintf("tier:%s:%d", tier, i)}},
			})
			seen[resp.Header.Get("X-PolyForge-Tier")]++
			_ = resp.Body.Close()
		}
		if len(seen) != 1 || seen[tier] != probes {
			t.Fatalf("pinned to %q but served %v across %d requests; "+
				"the pin must be exclusive or WL-H2 voids the run", tier, seen, probes)
		}
	}
}

// An unpinned tenant in a tier-provider deployment is served by the default
// tier, never by the external default provider. Session 48: with no external
// provider configured, unpinned requests dialled a phantom Ollama and answered
// 502 -- 89 of 37,270 in the EC2 probe.
func TestUnpinnedTenantUsesDefaultTierNotExternalProvider(t *testing.T) {
	small := &scriptedProvider{response: gateway.ChatResponse{Model: "small-model", Message: gateway.Message{Role: "assistant", Content: "small"}}}
	mid := &scriptedProvider{response: gateway.ChatResponse{Model: "mid-model", Message: gateway.Message{Role: "assistant", Content: "mid"}}}
	ts, key, _ := newKnobsServer(t, map[string]gateway.Provider{"small": small, "mid": mid})

	// Never pinned: no knobs written for acme at all. Config.ModelTier is
	// unset in the fixture, so NewServer's default ("mid") applies.
	resp := postJSON(t, ts.URL+"/v1/tenants/acme/ai/chat", key.Secret, map[string]any{
		"messages": []map[string]string{{"role": "user", "content": "unpinned prompt"}},
	})
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	if got := resp.Header.Get("X-PolyForge-Backend"); got != "tier:mid" {
		t.Fatalf("backend header = %q, want tier:mid", got)
	}
	if got := resp.Header.Get("X-PolyForge-Route-Reason"); got != "default-tier" {
		t.Fatalf("route reason = %q, want default-tier", got)
	}
	if mid.calls != 1 || small.calls != 0 {
		t.Fatalf("backend calls mid=%d small=%d, want 1/0", mid.calls, small.calls)
	}

	// "none" -- the sim's unpinned value -- behaves the same.
	if r := putKnobs(t, ts.URL, testAdminKey, "acme", gateway.TenantKnobs{ModelTier: "none", CacheSizeMB: 0}); r.StatusCode != http.StatusOK {
		t.Fatalf("put none: %d", r.StatusCode)
	}
	again := postJSON(t, ts.URL+"/v1/tenants/acme/ai/chat", key.Secret, map[string]any{
		"messages": []map[string]string{{"role": "user", "content": "unpinned prompt two"}},
	})
	defer func() { _ = again.Body.Close() }()
	if got := again.Header.Get("X-PolyForge-Backend"); got != "tier:mid" || mid.calls != 2 {
		t.Fatalf("after none: backend=%q mid.calls=%d, want tier:mid / 2", got, mid.calls)
	}
}
