# ADR 0007: AI gateway — wire protocol, embeddings, semantic cache, agent loop

Status: accepted · 2026-07-09 · implemented (W17-W19 MVP scope)

## Context

M5 adds the AI workload side of the platform: embeddings + vector search
(W17), an LLM gateway with a semantic cache (W18), and a tool-use agent
loop (W19). These generate the `ai-cacheable`, `ai-uncacheable`, and
`agentic-multistep` workload classes the JCAC classifier must observe, so
what matters most is that AI traffic produces classifiable telemetry — not
that any particular model host is wired in.

## Decisions

1. **OpenAI wire protocol as the provider abstraction.** The gateway and
   agent speak `/chat/completions` and `/embeddings` — the de-facto
   standard also served by Ollama and vLLM. Switching model hosts is a
   base-URL change. A `Provider` interface fronts it so tests use scripted
   providers with zero network.
2. **Two embedders behind one interface.** `embed.OpenAI` for real
   deployments; `embed.Local`, a deterministic hashed character-n-gram
   embedder (feature hashing, signed buckets, L2-normalized), for offline
   verification. Local captures lexical similarity only — good enough to
   prove the cache/search machinery, and honest about what it is not.
3. **Exact in-memory vector index now, pgvector as the scale path.**
   Brute-force cosine over per-tenant maps is the recall=1.0 baseline the
   HNSW variant must be benchmarked against (the W17 benchmark exists as
   `BenchmarkSearch10k`). Tenant isolation is structural: one map per
   tenant, no WHERE clause to get wrong. pgvector + HNSW is deferred until
   a CI image with the extension is chosen; it changes recall/latency, not
   the API.
4. **Semantic cache fails closed on similarity, open on errors.** Hit
   threshold 0.95 cosine over the whole conversation text: serving a
   similar-but-different question a stale answer is a correctness bug, so
   the threshold errs toward misses. An embedder failure degrades to a
   cache miss, never a failed chat.
5. **Agent loop = OpenAI tool-calling with per-call spans.** Tools are
   JSON-schema functions (`polyforge_query`, `polyforge_calc`,
   `polyforge_search`); tool failures are returned to the model as tool
   output so it can recover; the loop caps at 8 steps. `agent.run`,
   `llm.call`, and `tool.call` spans form the causal chain, and the
   gateway records `child_spans = llm_calls + tool_calls` in telemetry —
   the exact feature the classifier's agentic-multistep detector reads.
6. **The gateway shares the control plane's database.** Tenant API keys
   authenticate unchanged (same scopes: read for search, full for chat,
   index, and agent), and AI telemetry lands in `telemetry_events` where
   the classifier already looks.

## Rejected

- **Provider SDKs** (openai-go, langchaingo): heavy dependencies for what
  is ~200 lines of wire code; the SDKs also hide streaming details the
  gateway must own.
- **Caching inside the provider adapter**: the cache is tenant-policy
  surface (the JCAC controller will tune per-tenant cache size), so it
  belongs in the gateway where tenancy is known.
- **Real-embedding tests in CI**: network and nondeterminism in the gate;
  the local embedder keeps every cache/search assertion exact.

## Consequences

- `cmd/ai-gateway` is the second service binary (`:8081` default),
  defaulting to Ollama's local endpoint and the local embedder.
- The classifier can now be driven end-to-end with real AI traffic shapes
  (W26+ needs this).
- pgvector adoption is an isolated follow-up: implement `vector.Index`
  over Postgres, benchmark against the exact baseline, swap by config.
