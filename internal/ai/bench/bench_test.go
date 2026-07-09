package bench_test

import (
	"context"
	"strings"
	"testing"
	"time"

	"polyforge/internal/ai/bench"
	"polyforge/internal/ai/gateway"
)

// slowProvider streams a fixed answer with a measurable delay before the
// first delta, so TTFT and total time are distinguishable in assertions.
type slowProvider struct {
	content string
	tokens  int
	delay   time.Duration
	fail    bool
}

func (p *slowProvider) Chat(_ context.Context, _ gateway.ChatRequest) (gateway.ChatResponse, error) {
	return p.respond()
}

func (p *slowProvider) StreamChat(_ context.Context, _ gateway.ChatRequest, onDelta func(string) error) (gateway.ChatResponse, error) {
	if p.fail {
		return gateway.ChatResponse{}, context.DeadlineExceeded
	}
	time.Sleep(p.delay)
	for _, chunk := range strings.SplitAfter(p.content, " ") {
		if err := onDelta(chunk); err != nil {
			return gateway.ChatResponse{}, err
		}
		time.Sleep(time.Millisecond)
	}
	return p.respond()
}

func (p *slowProvider) respond() (gateway.ChatResponse, error) {
	return gateway.ChatResponse{
		Model:            "bench-model",
		FinishReason:     "stop",
		PromptTokens:     10,
		CompletionTokens: p.tokens,
		Message:          gateway.Message{Role: "assistant", Content: p.content},
	}, nil
}

func TestRunMeasuresTTFTThroughputAndCost(t *testing.T) {
	backends := []gateway.Backend{
		{Name: "cheap", Provider: &slowProvider{content: "a b c d", tokens: 40, delay: 20 * time.Millisecond}, CostPer1MTokens: 0},
		{Name: "paid", Provider: &slowProvider{content: "a b c d", tokens: 40, delay: 5 * time.Millisecond}, CostPer1MTokens: 2.0},
	}
	prompts := []bench.Prompt{{ID: "p1", Text: "hello"}}

	results := bench.Run(t.Context(), backends, prompts, 2)
	if len(results) != 4 {
		t.Fatalf("results = %d, want 2 backends x 1 prompt x 2 runs", len(results))
	}
	for _, r := range results {
		if r.Err != "" {
			t.Fatalf("unexpected error: %s", r.Err)
		}
		if r.TTFTMs <= 0 || r.TotalMs < r.TTFTMs {
			t.Fatalf("timings inconsistent: ttft=%f total=%f", r.TTFTMs, r.TotalMs)
		}
		if r.CompletionTokens != 40 {
			t.Fatalf("completion tokens = %d, want 40 from usage", r.CompletionTokens)
		}
		if r.TokensPerSec <= 0 {
			t.Fatalf("tokens/sec = %f, want > 0", r.TokensPerSec)
		}
	}
	// Cost attribution: only the paid backend accrues estimated cost.
	for _, r := range results {
		paid := r.Backend == "paid"
		if paid && r.EstCostUSD <= 0 {
			t.Fatalf("paid backend cost = %f, want > 0", r.EstCostUSD)
		}
		if !paid && r.EstCostUSD != 0 {
			t.Fatalf("cheap backend cost = %f, want 0", r.EstCostUSD)
		}
	}
}

func TestRunRecordsFailuresWithoutAborting(t *testing.T) {
	backends := []gateway.Backend{
		{Name: "down", Provider: &slowProvider{fail: true}},
		{Name: "up", Provider: &slowProvider{content: "ok", tokens: 2}},
	}
	results := bench.Run(t.Context(), backends, []bench.Prompt{{ID: "p", Text: "x"}}, 1)
	if len(results) != 2 {
		t.Fatalf("results = %d, want 2", len(results))
	}
	if results[0].Err == "" {
		t.Fatal("down backend should record an error")
	}
	if results[1].Err != "" {
		t.Fatalf("up backend errored: %s", results[1].Err)
	}
}

func TestWriteCSVAndMarkdown(t *testing.T) {
	backends := []gateway.Backend{
		{Name: "one", Provider: &slowProvider{content: "hello world", tokens: 5}, CostPer1MTokens: 1},
	}
	results := bench.Run(t.Context(), backends, bench.DefaultPrompts(), 1)

	var sb strings.Builder
	if err := bench.WriteCSV(&sb, results); err != nil {
		t.Fatalf("write csv: %v", err)
	}
	lines := strings.Split(strings.TrimSpace(sb.String()), "\n")
	if len(lines) != 1+len(results) {
		t.Fatalf("csv lines = %d, want header + %d rows", len(lines), len(results))
	}
	if !strings.HasPrefix(lines[0], "backend,model,prompt_id") {
		t.Fatalf("csv header = %q", lines[0])
	}

	table := bench.Markdown(results)
	if !strings.Contains(table, "| one |") {
		t.Fatalf("markdown table missing backend row:\n%s", table)
	}
	if !strings.Contains(table, "Median TTFT") {
		t.Fatalf("markdown table missing header:\n%s", table)
	}
}
