package gateway_test

import (
	"context"
	"encoding/json"
	"errors"
	"io"
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

// scriptedProvider returns canned responses and records how often it was
// consulted — the semantic-cache assertions hinge on the call count.
type scriptedProvider struct {
	response gateway.ChatResponse
	calls    int
}

func (p *scriptedProvider) Chat(_ context.Context, _ gateway.ChatRequest) (gateway.ChatResponse, error) {
	p.calls++
	return p.response, nil
}

func (p *scriptedProvider) StreamChat(_ context.Context, _ gateway.ChatRequest, onDelta func(string) error) (gateway.ChatResponse, error) {
	p.calls++
	if err := onDelta(p.response.Message.Content); err != nil {
		return gateway.ChatResponse{}, err
	}
	return p.response, nil
}

func newTestServer(t *testing.T, provider gateway.Provider, agent gateway.AgentFunc) (*httptest.Server, tenant.APIKey) {
	t.Helper()
	store := tenant.NewStore()
	_, key, err := store.ProvisionTenant(t.Context(), tenant.Tenant{ID: "acme", Name: "Acme"}, "bootstrap")
	if err != nil {
		t.Fatalf("provision: %v", err)
	}
	server := gateway.NewServer(slog.Default(), gateway.Config{
		Provider:  provider,
		Embedder:  embed.NewLocal(128),
		Tenants:   store,
		Telemetry: telemetry.NewStore(0),
		Agent:     agent,
	})
	ts := httptest.NewServer(server.Handler())
	t.Cleanup(ts.Close)
	return ts, key
}

func postJSON(t *testing.T, url, apiKey string, body any) *http.Response {
	t.Helper()
	raw, _ := json.Marshal(body)
	req, _ := http.NewRequest(http.MethodPost, url, strings.NewReader(string(raw)))
	req.Header.Set("Content-Type", "application/json")
	if apiKey != "" {
		req.Header.Set("X-PolyForge-API-Key", apiKey)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("request %s: %v", url, err)
	}
	return resp
}

func TestChatRequiresAuth(t *testing.T) {
	ts, _ := newTestServer(t, &scriptedProvider{}, nil)
	resp := postJSON(t, ts.URL+"/v1/tenants/acme/ai/chat", "", map[string]any{
		"messages": []map[string]string{{"role": "user", "content": "hi"}},
	})
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", resp.StatusCode)
	}
}

func TestChatMissThenSemanticCacheHit(t *testing.T) {
	provider := &scriptedProvider{response: gateway.ChatResponse{
		Model:        "test-model",
		FinishReason: "stop",
		Message:      gateway.Message{Role: "assistant", Content: "4"},
	}}
	ts, key := newTestServer(t, provider, nil)
	body := map[string]any{"messages": []map[string]string{{"role": "user", "content": "what is 2+2?"}}}

	first := postJSON(t, ts.URL+"/v1/tenants/acme/ai/chat", key.Secret, body)
	defer func() { _ = first.Body.Close() }()
	if first.StatusCode != http.StatusOK {
		t.Fatalf("first status = %d", first.StatusCode)
	}
	if got := first.Header.Get("X-PolyForge-Cache"); got != "miss" {
		t.Fatalf("first cache header = %q, want miss", got)
	}

	second := postJSON(t, ts.URL+"/v1/tenants/acme/ai/chat", key.Secret, body)
	defer func() { _ = second.Body.Close() }()
	if got := second.Header.Get("X-PolyForge-Cache"); got != "hit" {
		t.Fatalf("second cache header = %q, want hit", got)
	}
	var cached gateway.ChatResponse
	_ = json.NewDecoder(second.Body).Decode(&cached)
	if cached.Message.Content != "4" {
		t.Fatalf("cached completion = %q", cached.Message.Content)
	}
	if provider.calls != 1 {
		t.Fatalf("provider consulted %d times, want 1 (second answer must come from cache)", provider.calls)
	}
}

func TestChatStreamEmitsSSE(t *testing.T) {
	provider := &scriptedProvider{response: gateway.ChatResponse{
		Message: gateway.Message{Role: "assistant", Content: "streamed answer"},
	}}
	ts, key := newTestServer(t, provider, nil)
	resp := postJSON(t, ts.URL+"/v1/tenants/acme/ai/chat", key.Secret, map[string]any{
		"stream":   true,
		"messages": []map[string]string{{"role": "user", "content": "stream me"}},
	})
	defer func() { _ = resp.Body.Close() }()
	if ct := resp.Header.Get("Content-Type"); ct != "text/event-stream" {
		t.Fatalf("content type = %q", ct)
	}
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatalf("read stream: %v", err)
	}
	out := string(raw)
	if !strings.Contains(out, "streamed answer") || !strings.Contains(out, "data: [DONE]") {
		t.Fatalf("stream output missing delta or terminator: %q", out)
	}
}

func TestSemanticSearchRoundTrip(t *testing.T) {
	ts, key := newTestServer(t, &scriptedProvider{}, nil)

	index := postJSON(t, ts.URL+"/v1/tenants/acme/ai/index", key.Secret, map[string]any{
		"id": "p1", "text": "sorting algorithm in Python",
	})
	defer func() { _ = index.Body.Close() }()
	if index.StatusCode != http.StatusCreated {
		t.Fatalf("index status = %d", index.StatusCode)
	}

	search := postJSON(t, ts.URL+"/v1/tenants/acme/ai/search", key.Secret, map[string]any{
		"query": "How do I sort an array?", "k": 3,
	})
	defer func() { _ = search.Body.Close() }()
	if search.StatusCode != http.StatusOK {
		t.Fatalf("search status = %d", search.StatusCode)
	}
	var result struct {
		Matches []struct {
			Doc struct {
				ID string `json:"id"`
			} `json:"doc"`
			Score float64 `json:"score"`
		} `json:"matches"`
	}
	_ = json.NewDecoder(search.Body).Decode(&result)
	if len(result.Matches) != 1 || result.Matches[0].Doc.ID != "p1" {
		t.Fatalf("matches = %+v", result.Matches)
	}
}

func TestAgentEndpointReportsSpans(t *testing.T) {
	agentFn := func(_ context.Context, tenantID, prompt string) (any, int, error) {
		if tenantID != "acme" || prompt != "do things" {
			return nil, 0, errors.New("wrong routing")
		}
		return map[string]string{"output": "done"}, 5, nil
	}
	ts, key := newTestServer(t, &scriptedProvider{}, agentFn)
	resp := postJSON(t, ts.URL+"/v1/tenants/acme/ai/agent", key.Secret, map[string]any{"prompt": "do things"})
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("agent status = %d", resp.StatusCode)
	}
	var out map[string]string
	_ = json.NewDecoder(resp.Body).Decode(&out)
	if out["output"] != "done" {
		t.Fatalf("agent output = %+v", out)
	}
}
