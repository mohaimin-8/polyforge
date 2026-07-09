package gateway_test

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"

	"polyforge/internal/ai/gateway"
)

func TestOpenAICompatChatParsesToolCalls(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/chat/completions" {
			t.Errorf("path = %s", r.URL.Path)
		}
		var req map[string]any
		_ = json.NewDecoder(r.Body).Decode(&req)
		if req["stream"] != false {
			t.Errorf("stream = %v, want false", req["stream"])
		}
		if _, hasTools := req["tools"]; !hasTools {
			t.Error("tools were not forwarded to the provider")
		}
		_, _ = w.Write([]byte(`{
			"model": "test-model",
			"choices": [{
				"finish_reason": "tool_calls",
				"message": {
					"role": "assistant",
					"content": "",
					"tool_calls": [{
						"id": "call_1",
						"type": "function",
						"function": {"name": "polyforge_calc", "arguments": "{\"expression\":\"2+2\"}"}
					}]
				}
			}],
			"usage": {"prompt_tokens": 10, "completion_tokens": 5}
		}`))
	}))
	defer server.Close()

	provider := gateway.NewOpenAICompat(server.URL+"/v1", "", "test-model")
	resp, err := provider.Chat(t.Context(), gateway.ChatRequest{
		Messages: []gateway.Message{{Role: "user", Content: "what is 2+2"}},
		Tools:    []gateway.ToolSpec{{Name: "polyforge_calc", Description: "calc", Schema: json.RawMessage(`{}`)}},
	})
	if err != nil {
		t.Fatalf("chat: %v", err)
	}
	if len(resp.Message.ToolCalls) != 1 || resp.Message.ToolCalls[0].Name != "polyforge_calc" {
		t.Fatalf("tool calls = %+v", resp.Message.ToolCalls)
	}
	if string(resp.Message.ToolCalls[0].Arguments) != `{"expression":"2+2"}` {
		t.Fatalf("arguments = %s", resp.Message.ToolCalls[0].Arguments)
	}
	if resp.PromptTokens != 10 || resp.CompletionTokens != 5 {
		t.Fatalf("usage not parsed: %+v", resp)
	}
}

func TestOpenAICompatStreamChatAssemblesDeltas(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req map[string]any
		_ = json.NewDecoder(r.Body).Decode(&req)
		if req["stream"] != true {
			t.Errorf("stream = %v, want true", req["stream"])
		}
		w.Header().Set("Content-Type", "text/event-stream")
		for _, chunk := range []string{"Hel", "lo ", "world"} {
			_, _ = fmt.Fprintf(w, "data: {\"model\":\"test-model\",\"choices\":[{\"delta\":{\"content\":%q}}]}\n\n", chunk)
		}
		_, _ = fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n")
		_, _ = fmt.Fprint(w, "data: [DONE]\n\n")
	}))
	defer server.Close()

	provider := gateway.NewOpenAICompat(server.URL, "", "test-model")
	var deltas []string
	resp, err := provider.StreamChat(t.Context(), gateway.ChatRequest{
		Messages: []gateway.Message{{Role: "user", Content: "hi"}},
	}, func(delta string) error {
		deltas = append(deltas, delta)
		return nil
	})
	if err != nil {
		t.Fatalf("stream chat: %v", err)
	}
	if resp.Message.Content != "Hello world" {
		t.Fatalf("assembled content = %q", resp.Message.Content)
	}
	if len(deltas) != 3 {
		t.Fatalf("received %d deltas, want 3", len(deltas))
	}
	if resp.FinishReason != "stop" {
		t.Fatalf("finish reason = %q", resp.FinishReason)
	}
}
