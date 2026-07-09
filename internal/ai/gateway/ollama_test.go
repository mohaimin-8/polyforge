package gateway_test

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"

	"polyforge/internal/ai/gateway"
)

// fakeOllama serves the native /api/chat protocol: NDJSON deltas when
// streaming, a single object otherwise, with eval counts on the final chunk.
func fakeOllama(t *testing.T, deltas []string) *httptest.Server {
	t.Helper()
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/chat" {
			http.NotFound(w, r)
			return
		}
		var req struct {
			Model  string `json:"model"`
			Stream bool   `json:"stream"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/x-ndjson")
		if !req.Stream {
			full := ""
			for _, d := range deltas {
				full += d
			}
			_, _ = fmt.Fprintf(w, `{"model":%q,"message":{"role":"assistant","content":%q},"done":true,"done_reason":"stop","prompt_eval_count":12,"eval_count":34}`+"\n", req.Model, full)
			return
		}
		for _, d := range deltas {
			_, _ = fmt.Fprintf(w, `{"model":%q,"message":{"role":"assistant","content":%q},"done":false}`+"\n", req.Model, d)
		}
		_, _ = fmt.Fprintf(w, `{"model":%q,"message":{"role":"assistant","content":""},"done":true,"done_reason":"stop","prompt_eval_count":12,"eval_count":34}`+"\n", req.Model)
	}))
	t.Cleanup(ts.Close)
	return ts
}

func TestOllamaChat(t *testing.T) {
	ts := fakeOllama(t, []string{"hello", " world"})
	provider := gateway.NewOllama(ts.URL, "llama3.2")

	resp, err := provider.Chat(t.Context(), gateway.ChatRequest{
		Messages: []gateway.Message{{Role: "user", Content: "hi"}},
	})
	if err != nil {
		t.Fatalf("chat: %v", err)
	}
	if resp.Message.Content != "hello world" {
		t.Fatalf("content = %q", resp.Message.Content)
	}
	if resp.PromptTokens != 12 || resp.CompletionTokens != 34 {
		t.Fatalf("token counts = %d/%d, want 12/34", resp.PromptTokens, resp.CompletionTokens)
	}
	if resp.FinishReason != "stop" {
		t.Fatalf("finish reason = %q", resp.FinishReason)
	}
}

func TestOllamaStreamChat(t *testing.T) {
	ts := fakeOllama(t, []string{"a", "b", "c"})
	provider := gateway.NewOllama(ts.URL, "llama3.2")

	var deltas []string
	resp, err := provider.StreamChat(t.Context(), gateway.ChatRequest{
		Messages: []gateway.Message{{Role: "user", Content: "hi"}},
	}, func(d string) error {
		deltas = append(deltas, d)
		return nil
	})
	if err != nil {
		t.Fatalf("stream chat: %v", err)
	}
	if len(deltas) != 3 {
		t.Fatalf("deltas = %v, want 3 fragments", deltas)
	}
	if resp.Message.Content != "abc" {
		t.Fatalf("assembled content = %q", resp.Message.Content)
	}
	if resp.CompletionTokens != 34 {
		t.Fatalf("completion tokens = %d, want 34 from final chunk", resp.CompletionTokens)
	}
}

func TestOllamaSurfacesServerError(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, "model not found", http.StatusNotFound)
	}))
	t.Cleanup(ts.Close)
	provider := gateway.NewOllama(ts.URL, "nope")

	_, err := provider.Chat(t.Context(), gateway.ChatRequest{
		Messages: []gateway.Message{{Role: "user", Content: "hi"}},
	})
	if err == nil {
		t.Fatal("expected an error for a 404 backend")
	}
}
