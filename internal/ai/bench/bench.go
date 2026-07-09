// Package bench measures inference latency and throughput across the
// gateway's backends (roadmap W20). Every backend is driven through the
// same streaming Provider interface, so TTFT and tokens/sec are measured
// identically for local Ollama, Groq, and OpenAI — the numbers land in
// benchmarks/inference.csv and are cited as Table 1 in the paper.
package bench

import (
	"context"
	"encoding/csv"
	"fmt"
	"io"
	"sort"
	"strconv"
	"strings"
	"time"

	"polyforge/internal/ai/gateway"
)

// Prompt is one benchmark case. Lengths are varied deliberately: TTFT grows
// with prompt size (prefill cost), throughput mostly does not.
type Prompt struct {
	ID   string
	Text string
}

// DefaultPrompts covers the three prompt-size regimes the router's
// length-based rules distinguish.
func DefaultPrompts() []Prompt {
	long := strings.Repeat("PolyForge is a multi-tenant platform for hybrid CRUD and AI workloads. ", 40)
	return []Prompt{
		{ID: "short", Text: "In one sentence, what is a service mesh?"},
		{ID: "medium", Text: "Explain the tradeoffs between shared-schema row-level security and schema-per-tenant isolation in a multi-tenant SaaS platform, in about 150 words."},
		{ID: "long", Text: long + "Summarize the platform described above in three bullet points."},
	}
}

// Result is one measured request.
type Result struct {
	Backend          string
	Model            string
	PromptID         string
	PromptChars      int
	TTFTMs           float64
	TotalMs          float64
	CompletionTokens int
	TokensPerSec     float64
	EstCostUSD       float64
	Err              string
}

// Run streams every prompt against every backend runsPerPrompt times.
// Failures are recorded, not fatal: a benchmark of three backends where one
// is down should still produce two columns of Table 1.
func Run(ctx context.Context, backends []gateway.Backend, prompts []Prompt, runsPerPrompt int) []Result {
	if runsPerPrompt < 1 {
		runsPerPrompt = 1
	}
	var results []Result
	for _, b := range backends {
		for _, p := range prompts {
			for run := 0; run < runsPerPrompt; run++ {
				results = append(results, measure(ctx, b, p))
			}
		}
	}
	return results
}

func measure(ctx context.Context, b gateway.Backend, p Prompt) Result {
	result := Result{Backend: b.Name, PromptID: p.ID, PromptChars: len(p.Text)}
	req := gateway.ChatRequest{Messages: []gateway.Message{{Role: "user", Content: p.Text}}}

	start := time.Now()
	var firstDelta time.Duration
	response, err := b.Provider.StreamChat(ctx, req, func(string) error {
		if firstDelta == 0 {
			firstDelta = time.Since(start)
		}
		return nil
	})
	total := time.Since(start)
	if err != nil {
		result.Err = err.Error()
		return result
	}

	result.Model = response.Model
	result.TTFTMs = float64(firstDelta.Microseconds()) / 1000
	result.TotalMs = float64(total.Microseconds()) / 1000
	result.CompletionTokens = response.CompletionTokens
	if result.CompletionTokens == 0 {
		// Provider reported no usage; ~4 chars/token is the standard rough
		// estimate and is flagged as such in the report methodology.
		result.CompletionTokens = len(response.Message.Content) / 4
	}
	if decode := total - firstDelta; decode > 0 && result.CompletionTokens > 0 {
		result.TokensPerSec = float64(result.CompletionTokens) / decode.Seconds()
	}
	result.EstCostUSD = float64(response.PromptTokens+response.CompletionTokens) / 1e6 * b.CostPer1MTokens
	return result
}

var csvHeader = []string{
	"backend", "model", "prompt_id", "prompt_chars",
	"ttft_ms", "total_ms", "completion_tokens", "tokens_per_sec", "est_cost_usd", "error",
}

// WriteCSV emits one row per measured request — the raw data behind Table 1.
func WriteCSV(w io.Writer, results []Result) error {
	cw := csv.NewWriter(w)
	if err := cw.Write(csvHeader); err != nil {
		return err
	}
	for _, r := range results {
		row := []string{
			r.Backend, r.Model, r.PromptID, strconv.Itoa(r.PromptChars),
			formatFloat(r.TTFTMs), formatFloat(r.TotalMs),
			strconv.Itoa(r.CompletionTokens), formatFloat(r.TokensPerSec),
			strconv.FormatFloat(r.EstCostUSD, 'f', 6, 64), r.Err,
		}
		if err := cw.Write(row); err != nil {
			return err
		}
	}
	cw.Flush()
	return cw.Error()
}

func formatFloat(v float64) string { return strconv.FormatFloat(v, 'f', 2, 64) }

// Markdown renders the per-backend aggregate table for INFERENCE_BENCH.md:
// median TTFT, mean throughput, and cost per 1k output tokens.
func Markdown(results []Result) string {
	type agg struct {
		ttfts    []float64
		tokRates []float64
		tokens   int
		cost     float64
		errs     int
		runs     int
	}
	byBackend := map[string]*agg{}
	var order []string
	for _, r := range results {
		a := byBackend[r.Backend]
		if a == nil {
			a = &agg{}
			byBackend[r.Backend] = a
			order = append(order, r.Backend)
		}
		a.runs++
		if r.Err != "" {
			a.errs++
			continue
		}
		a.ttfts = append(a.ttfts, r.TTFTMs)
		a.tokRates = append(a.tokRates, r.TokensPerSec)
		a.tokens += r.CompletionTokens
		a.cost += r.EstCostUSD
	}

	var sb strings.Builder
	sb.WriteString("| Backend | Runs | Errors | Median TTFT (ms) | Mean tokens/sec | Est. $/1k output tokens |\n")
	sb.WriteString("|---|---|---|---|---|---|\n")
	for _, name := range order {
		a := byBackend[name]
		costPer1K := 0.0
		if a.tokens > 0 {
			costPer1K = a.cost / float64(a.tokens) * 1000
		}
		fmt.Fprintf(&sb, "| %s | %d | %d | %.1f | %.1f | %.4f |\n",
			name, a.runs, a.errs, median(a.ttfts), mean(a.tokRates), costPer1K)
	}
	return sb.String()
}

func median(values []float64) float64 {
	if len(values) == 0 {
		return 0
	}
	sorted := append([]float64(nil), values...)
	sort.Float64s(sorted)
	mid := len(sorted) / 2
	if len(sorted)%2 == 0 {
		return (sorted[mid-1] + sorted[mid]) / 2
	}
	return sorted[mid]
}

func mean(values []float64) float64 {
	if len(values) == 0 {
		return 0
	}
	sum := 0.0
	for _, v := range values {
		sum += v
	}
	return sum / float64(len(values))
}
