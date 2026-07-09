package agent_test

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"polyforge/internal/ai/agent"
	"polyforge/internal/ai/gateway"
	"polyforge/internal/tenant"
)

// scriptedProvider replays a fixed sequence of responses and captures every
// request, so tests can assert what the loop fed back to the model.
type scriptedProvider struct {
	responses []gateway.ChatResponse
	requests  []gateway.ChatRequest
}

func (p *scriptedProvider) Chat(_ context.Context, req gateway.ChatRequest) (gateway.ChatResponse, error) {
	p.requests = append(p.requests, req)
	if len(p.responses) == 0 {
		return gateway.ChatResponse{Message: gateway.Message{Role: "assistant", Content: "out of script"}}, nil
	}
	next := p.responses[0]
	p.responses = p.responses[1:]
	return next, nil
}

func (p *scriptedProvider) StreamChat(ctx context.Context, req gateway.ChatRequest, _ func(string) error) (gateway.ChatResponse, error) {
	return p.Chat(ctx, req)
}

func toolCallResponse(calls ...gateway.ToolCall) gateway.ChatResponse {
	return gateway.ChatResponse{
		FinishReason: "tool_calls",
		Message:      gateway.Message{Role: "assistant", ToolCalls: calls},
	}
}

func finalResponse(content string) gateway.ChatResponse {
	return gateway.ChatResponse{
		FinishReason: "stop",
		Message:      gateway.Message{Role: "assistant", Content: content},
	}
}

func TestRunExecutesToolsAndFeedsResultsBack(t *testing.T) {
	store := tenant.NewStore()
	ctx := t.Context()
	if _, _, err := store.ProvisionTenant(ctx, tenant.Tenant{ID: "acme", Name: "Acme"}, "bootstrap"); err != nil {
		t.Fatalf("provision: %v", err)
	}
	if _, err := store.CreateProject(ctx, tenant.Project{ID: "web", TenantID: "acme", Name: "Web Shop"}); err != nil {
		t.Fatalf("create project: %v", err)
	}

	provider := &scriptedProvider{responses: []gateway.ChatResponse{
		toolCallResponse(
			gateway.ToolCall{ID: "c1", Name: "polyforge_calc", Arguments: json.RawMessage(`{"expression":"(2+3)*4"}`)},
			gateway.ToolCall{ID: "c2", Name: "polyforge_query", Arguments: json.RawMessage(`{"query":"web"}`)},
		),
		finalResponse("You have 1 project and the answer is 20."),
	}}
	runner := agent.NewRunner(provider, []agent.Tool{
		agent.CalcTool{},
		&agent.QueryTool{Repo: store},
	}, nil, 4)

	result, err := runner.Run(ctx, "acme", "compute (2+3)*4 and list my web projects")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if result.Output != "You have 1 project and the answer is 20." {
		t.Fatalf("output = %q", result.Output)
	}
	if result.LLMCalls != 2 || result.ToolCalls != 2 {
		t.Fatalf("llm=%d tool=%d, want 2/2", result.LLMCalls, result.ToolCalls)
	}

	// The second LLM request must contain the tool results keyed by call ID.
	second := provider.requests[1]
	var calcResult, queryResult string
	for _, m := range second.Messages {
		switch m.ToolCallID {
		case "c1":
			calcResult = m.Content
		case "c2":
			queryResult = m.Content
		}
	}
	if calcResult != "20" {
		t.Fatalf("calc tool result = %q, want 20", calcResult)
	}
	if !strings.Contains(queryResult, "Web Shop") {
		t.Fatalf("query tool result = %q, want the Web Shop project", queryResult)
	}

	// Tool specs must be advertised on every LLM call.
	if len(second.Tools) != 2 {
		t.Fatalf("tools advertised on second call = %d, want 2", len(second.Tools))
	}
}

func TestRunSurfacesUnknownToolToModelAndRecovers(t *testing.T) {
	provider := &scriptedProvider{responses: []gateway.ChatResponse{
		toolCallResponse(gateway.ToolCall{ID: "c1", Name: "no_such_tool", Arguments: json.RawMessage(`{}`)}),
		finalResponse("recovered without the tool"),
	}}
	runner := agent.NewRunner(provider, []agent.Tool{agent.CalcTool{}}, nil, 4)

	result, err := runner.Run(t.Context(), "acme", "use a tool that does not exist")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if result.Output != "recovered without the tool" {
		t.Fatalf("output = %q", result.Output)
	}
	second := provider.requests[1]
	var toolMsg string
	for _, m := range second.Messages {
		if m.ToolCallID == "c1" {
			toolMsg = m.Content
		}
	}
	if !strings.Contains(toolMsg, "unknown tool") {
		t.Fatalf("model was not told about the unknown tool: %q", toolMsg)
	}
}

func TestRunFailsWhenItCannotConverge(t *testing.T) {
	loop := toolCallResponse(gateway.ToolCall{ID: "c1", Name: "polyforge_calc", Arguments: json.RawMessage(`{"expression":"1"}`)})
	provider := &scriptedProvider{responses: []gateway.ChatResponse{loop, loop, loop}}
	runner := agent.NewRunner(provider, []agent.Tool{agent.CalcTool{}}, nil, 3)

	if _, err := runner.Run(t.Context(), "acme", "loop forever"); err == nil {
		t.Fatal("a non-converging agent must return an error")
	}
}

func TestCalcTool(t *testing.T) {
	cases := []struct {
		expr string
		want string
	}{
		{"2+3*4", "14"},
		{"(2+3)*4", "20"},
		{"2^3^2", "512"},
		{"-4+2", "-2"},
		{"10/4", "2.5"},
		{" 1.5 * 2 ", "3"},
	}
	for _, tc := range cases {
		args, _ := json.Marshal(map[string]string{"expression": tc.expr})
		got, err := agent.CalcTool{}.Call(t.Context(), "acme", args)
		if err != nil {
			t.Fatalf("%s: %v", tc.expr, err)
		}
		if got != tc.want {
			t.Errorf("%s = %s, want %s", tc.expr, got, tc.want)
		}
	}

	for _, bad := range []string{"1/0", "2+", "hello", "(1", "1)2"} {
		args, _ := json.Marshal(map[string]string{"expression": bad})
		if _, err := (agent.CalcTool{}).Call(t.Context(), "acme", args); err == nil {
			t.Errorf("%q should fail", bad)
		}
	}
}

func TestSearchToolUsesInjectedEngine(t *testing.T) {
	tool := &agent.SearchTool{Search: func(_ context.Context, query string) ([]agent.SearchResult, error) {
		if query != "polyforge thesis" {
			t.Errorf("query = %q", query)
		}
		return []agent.SearchResult{{Title: "PolyForge", URL: "https://example.org"}}, nil
	}}
	args, _ := json.Marshal(map[string]string{"query": "polyforge thesis"})
	out, err := tool.Call(t.Context(), "acme", args)
	if err != nil {
		t.Fatalf("call: %v", err)
	}
	if !strings.Contains(out, "example.org") {
		t.Fatalf("output = %q", out)
	}

	unconfigured := &agent.SearchTool{}
	if _, err := unconfigured.Call(t.Context(), "acme", args); err == nil {
		t.Fatal("unconfigured search must error")
	}
}
