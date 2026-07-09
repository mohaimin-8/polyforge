# Inference Benchmark (roadmap W20)

Measures time-to-first-token (TTFT), decode throughput (tokens/sec), and
estimated cost per 1k output tokens for every backend the AI gateway can
route to. The raw CSV in `benchmarks/inference.csv` is the data behind
Table 1 of the paper.

## Methodology

- Every backend is driven through the same `gateway.Provider` streaming
  interface by `internal/ai/bench`, so all backends are measured with
  identical instrumentation — no provider-specific SDKs.
- **TTFT** = wall-clock time from request start to the first content delta.
  It includes network RTT and prefill; that is deliberate, because it is the
  latency a chat user perceives.
- **Tokens/sec** = completion tokens ÷ (total time − TTFT), i.e. decode
  throughput excluding prefill. Token counts come from the provider's
  reported usage (`eval_count` on Ollama's native API); if a provider
  reports no usage, tokens are estimated at 4 chars/token and the row is
  still recorded.
- **Cost** = (prompt + completion tokens) × the blended `cost_per_1m_tokens`
  configured in `deploy/routing.json`. Local Ollama is $0 marginal cost;
  cloud prices are config, not code, so a price change is a config edit.
- Three prompt sizes (short/medium/long, see `bench.DefaultPrompts`) map to
  the prompt-length regimes the router's rules distinguish. Each cell is
  run N times (default 3); the report uses median TTFT and mean throughput.

## Running it

```sh
# 1. Serve the local models (any one of):
ollama serve && ollama pull llama3.2:3b && ollama pull phi3.5   # native install
docker compose --profile ai up -d ollama && docker compose --profile ai run --rm ollama-init

# 2. Export keys for the cloud backends you want in the comparison:
export GROQ_API_KEY=... OPENAI_API_KEY=...

# 3. Run the harness:
go run ./cmd/llmbench -config deploy/routing.json -runs 5 -out benchmarks/inference.csv
```

The harness prints the aggregate markdown table (paste it below) and writes
the per-request CSV. Backends that are down produce error rows, not a
failed run, so a partial comparison is still valid data.

## Results

**Status: pending a live run.** This development machine has neither Ollama
nor Docker installed, so no numbers are published yet — publishing measured
numbers from a machine that cannot run the workload would be fabrication.
The harness itself is tested (`internal/ai/bench/bench_test.go`) against a
scripted provider with known timing characteristics.

| Backend | Runs | Errors | Median TTFT (ms) | Mean tokens/sec | Est. $/1k output tokens |
|---|---|---|---|---|---|
| _pending_ | – | – | – | – | – |

Acceptance gate from the roadmap, to check when the live run happens:
local Llama-3.2-3B sustains ≥ 30 tokens/sec on laptop CPU (≥ 80 on a
consumer GPU).

## Routing policy this feeds

`deploy/routing.json` routes `/chat` by tenant plan, prompt length, and a
per-tenant daily budget (`X-PolyForge-Backend` and
`X-PolyForge-Route-Reason` response headers expose each decision):

| Tenant plan | Backend | Rationale |
|---|---|---|
| free | `local` (Ollama) | zero marginal cost |
| standard | `fast` (Groq) | low TTFT at mid cost |
| premium | `quality` (OpenAI) | highest answer quality |
| any, prompt ≥ 8k chars | `local` | long prompts are the expensive regime |
| any, daily budget exhausted | cheapest backend | degrade quality, not availability |

This static rule table is the baseline the JCAC controller (M8) replaces
with a learned policy; the benchmark CSV supplies its cost/latency priors.
