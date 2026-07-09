package gateway_test

import (
	"encoding/json"
	"log/slog"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"polyforge/internal/ai/embed"
	"polyforge/internal/ai/gateway"
	"polyforge/internal/tenant"
)

func namedProvider(content string) *scriptedProvider {
	return &scriptedProvider{response: gateway.ChatResponse{
		Model:        content,
		FinishReason: "stop",
		Message:      gateway.Message{Role: "assistant", Content: content},
	}}
}

func policyRouter(t *testing.T) (*gateway.Router, map[string]*scriptedProvider) {
	t.Helper()
	providers := map[string]*scriptedProvider{
		"local":   namedProvider("from-local"),
		"fast":    namedProvider("from-fast"),
		"quality": namedProvider("from-quality"),
	}
	router, err := gateway.NewRouter(gateway.RouterConfig{
		Default: "fast",
		Rules: []gateway.RoutingRule{
			{Plan: "free", Backend: "local"},
			{Plan: "premium", Backend: "quality"},
			{MinPromptChars: 4000, Backend: "local"}, // long prompts stay cheap
		},
		DailyBudgetUSD: 1.0,
	},
		gateway.Backend{Name: "local", Provider: providers["local"], CostPer1MTokens: 0},
		gateway.Backend{Name: "fast", Provider: providers["fast"], CostPer1MTokens: 0.6},
		gateway.Backend{Name: "quality", Provider: providers["quality"], CostPer1MTokens: 5.0},
	)
	if err != nil {
		t.Fatalf("new router: %v", err)
	}
	return router, providers
}

func TestRouterRoutesByPlanPromptLengthAndDefault(t *testing.T) {
	router, _ := policyRouter(t)

	cases := []struct {
		plan        string
		promptChars int
		want        string
		reason      string
	}{
		{"free", 100, "local", "rule-0"},
		{"premium", 100, "quality", "rule-1"},
		{"standard", 100, "fast", "default"},
		{"standard", 5000, "local", "rule-2"},
	}
	for _, tc := range cases {
		got := router.Route("t1", tc.plan, tc.promptChars)
		if got.Backend.Name != tc.want || got.Reason != tc.reason {
			t.Errorf("Route(plan=%s, chars=%d) = %s/%s, want %s/%s",
				tc.plan, tc.promptChars, got.Backend.Name, got.Reason, tc.want, tc.reason)
		}
	}
}

func TestRouterBudgetExhaustionFallsBackToCheapest(t *testing.T) {
	router, _ := policyRouter(t)

	// Premium tenant starts on the quality backend.
	if d := router.Route("acme", "premium", 10); d.Backend.Name != "quality" {
		t.Fatalf("pre-budget backend = %s, want quality", d.Backend.Name)
	}
	// Burn through the $1 daily budget at $5/1M tokens.
	router.RecordUsage("acme", "quality", 150_000, 150_000)
	if spent := router.SpentToday("acme"); spent < 1.0 {
		t.Fatalf("spent = %f, want >= 1.0", spent)
	}
	if d := router.Route("acme", "premium", 10); d.Backend.Name != "local" || d.Reason != "budget-exhausted" {
		t.Fatalf("post-budget decision = %s/%s, want local/budget-exhausted", d.Backend.Name, d.Reason)
	}
	// Budgets are per-tenant: another tenant is unaffected.
	if d := router.Route("other", "premium", 10); d.Backend.Name != "quality" {
		t.Fatalf("other tenant backend = %s, want quality", d.Backend.Name)
	}
}

func TestRouterRejectsBadConfig(t *testing.T) {
	provider := namedProvider("x")
	if _, err := gateway.NewRouter(gateway.RouterConfig{Default: "ghost"},
		gateway.Backend{Name: "a", Provider: provider}); err == nil {
		t.Fatal("expected error for unknown default backend")
	}
	if _, err := gateway.NewRouter(gateway.RouterConfig{
		Rules: []gateway.RoutingRule{{Backend: "ghost"}},
	}, gateway.Backend{Name: "a", Provider: provider}); err == nil {
		t.Fatal("expected error for rule referencing unknown backend")
	}
}

// TestChatRoutesFreeTenantToLocalBackend is the W20 verification gate:
// the /chat endpoint routes by tenant plan and says so in headers.
func TestChatRoutesFreeTenantToLocalBackend(t *testing.T) {
	router, providers := policyRouter(t)

	store := tenant.NewStore()
	_, freeKey, err := store.ProvisionTenant(t.Context(), tenant.Tenant{ID: "freeco", Name: "FreeCo", Plan: "free"}, "k")
	if err != nil {
		t.Fatalf("provision freeco: %v", err)
	}
	_, premiumKey, err := store.ProvisionTenant(t.Context(), tenant.Tenant{ID: "bigco", Name: "BigCo", Plan: "premium"}, "k")
	if err != nil {
		t.Fatalf("provision bigco: %v", err)
	}

	server := gateway.NewServer(slog.Default(), gateway.Config{
		Provider: namedProvider("from-static"),
		Router:   router,
		Embedder: embed.NewLocal(128),
		Tenants:  store,
	})
	ts := httptest.NewServer(server.Handler())
	t.Cleanup(ts.Close)

	body := func(prompt string) map[string]any {
		return map[string]any{"messages": []map[string]string{{"role": "user", "content": prompt}}}
	}

	resp := postJSON(t, ts.URL+"/v1/tenants/freeco/ai/chat", freeKey.Secret, body("hello from freeco"))
	defer func() { _ = resp.Body.Close() }()
	if got := resp.Header.Get("X-PolyForge-Backend"); got != "local" {
		t.Fatalf("free tenant backend header = %q, want local", got)
	}
	var out gateway.ChatResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if out.Message.Content != "from-local" {
		t.Fatalf("free tenant answer = %q, want from-local", out.Message.Content)
	}
	if providers["local"].calls != 1 {
		t.Fatalf("local provider calls = %d, want 1", providers["local"].calls)
	}

	resp2 := postJSON(t, ts.URL+"/v1/tenants/bigco/ai/chat", premiumKey.Secret, body("hello from bigco"))
	defer func() { _ = resp2.Body.Close() }()
	if got := resp2.Header.Get("X-PolyForge-Backend"); got != "quality" {
		t.Fatalf("premium tenant backend header = %q, want quality", got)
	}
}

func TestLoadRouterFileBuildsRouter(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "routing.json")
	config := `{
		"default": "local",
		"daily_budget_usd": 2,
		"backends": [
			{"name": "local", "kind": "ollama", "base_url": "http://127.0.0.1:11434", "model": "llama3.2"},
			{"name": "quality", "kind": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini", "api_key_env": "OPENAI_API_KEY", "cost_per_1m_tokens": 0.45}
		],
		"rules": [{"plan": "premium", "backend": "quality"}]
	}`
	if err := os.WriteFile(path, []byte(config), 0o644); err != nil {
		t.Fatalf("write config: %v", err)
	}

	file, err := gateway.LoadRouterFile(path)
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	router, err := file.Build()
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	if d := router.Route("t", "premium", 10); d.Backend.Name != "quality" {
		t.Fatalf("premium routes to %s, want quality", d.Backend.Name)
	}
	if d := router.Route("t", "free", 10); d.Backend.Name != "local" {
		t.Fatalf("free routes to %s, want local (default)", d.Backend.Name)
	}
}
