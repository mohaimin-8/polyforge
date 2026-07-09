# Hybrid Workload Taxonomy (W26 — paper §3)

Five classes partition tenant workload behavior. Each is defined by
quantitative criteria over a 15-minute telemetry window; the 12-feature
vector below is the measurable form of those criteria, and
`internal/classifier` is the single implementation of both.

## Classes — formal criteria

| Class | Defining criteria (window statistics) | Control implication (M8) |
|---|---|---|
| **CRUD-bursty** | RPS coefficient of variation > 0.9 · mean payload < 5 KB · cache-hit ≈ 0 · no model-tier traffic | scale replicas fast, no cache budget |
| **CRUD-steady** | RPS CV ≤ 0.9 · mean payload < 5 KB · cache-hit ≈ 0 | steady replicas, minimal headroom |
| **AI-cacheable** | model-tier traffic present · mean embedding density ≥ 0.5 or high-density fraction ≥ 0.5 · observed cache-hit ≥ 0.3 | grow semantic-cache budget, prefer cheap tier |
| **AI-uncacheable** | model-tier traffic present · embedding density < 0.5 · cache-hit < 0.1 | shrink cache budget, route by cost/quality |
| **agentic-multistep** | mean child spans ≥ 3 (≥ 3 downstream calls within the request's 10s window) | latency budget dominates; pin quality tier |

Windows mixing classes (real tenants do) are labeled by majority
composition; the synthetic generator reproduces this with up to 30%
cross-class contamination per window.

## The 12 features

| # | Feature | Why it discriminates |
|---|---|---|
| 0 | `avg_rps` | scale separates infra-heavy from chat tenants |
| 1 | `rps_cv` | burstiness, scale-free (CV, not variance — comparable across tenant sizes) |
| 2 | `log_avg_payload_bytes` | CRUD (~KB) vs prompts (~10KB); log tames the tail |
| 3 | `payload_cv` | uniform CRUD bodies vs wildly varying prompts |
| 4 | `avg_latency_ms` | ms CRUD vs seconds-scale inference |
| 5 | `p95_latency_ms` | agent chains live in the tail |
| 6 | `cache_hit_rate` | the cacheable/uncacheable split, observed |
| 7 | `avg_embedding_density` | semantic repetitiveness of recent prompts |
| 8 | `high_density_fraction` | robust to a few dense outliers (fraction ≥ 0.5) |
| 9 | `avg_child_spans` | the agentic criterion, directly |
| 10 | `multistep_fraction` | fraction of requests fanning out ≥ 3 spans |
| 11 | `ai_request_fraction` | fraction with a model tier — the CRUD/AI axis |

## Model selection (status: synthetic supervision)

Candidates run through one evaluation path (`classifier.Evaluate`) on a
held-out seeded synthetic set (500 windows, balanced, contaminated):

| Model | Accuracy | Macro-F1 | Inference |
|---|---|---|---|
| W19 rule baseline | 0.888 | 0.885 | ~0.1 µs |
| **softmax LR (Go), selected** | 1.000 | 1.000 | ~0.7 µs |

Reproduce: `go run ./cmd/classifier-train -out .` (artifacts:
`artifacts/classifier-model.json`, `research/results/model_selection.csv`;
same seeds → identical bytes).

Read the numbers honestly: window-majority labels over aggregate features
remain nearly linearly separable, so a perfect synthetic score says the
*pipeline* works — features, trainer, evaluation, artifact loading — and
that the fixed-threshold rules break under mixed workloads while a learned
boundary does not. It says nothing yet about real traffic.

**Deferred to the real-trace phase** (needs W25b downloads + ClickHouse):
XGBoost and small-LSTM comparison, the n=500 hand-labeled validation set
with Cohen's κ, and the publication confusion matrix. Tracked as the W26
follow-up in `docs/EXECUTION_PLAN.md`; decision rationale in ADR 0012.

## Labeling protocol (for the real-trace run)

1. Replay each trace through PolyForge (`cmd/replay`) with the mirror on.
2. Aggregate 15-minute windows per tenant from ClickHouse.
3. Auto-label by source: Azure HTTP → CRUD-{bursty,steady} by measured
   RPS CV; LMSYS → AI-{cacheable,uncacheable} by semantic-cache outcome;
   agent-loop driven → agentic-multistep.
4. Hand-label 500 windows blind to the auto-labels; report agreement and
   Cohen's κ (gate: ≥ 92%, κ > 0.85).
