# Related work — capability & performance comparison (Phase 9)

Drafted 2026-07-12 (session 15) from a fresh literature sweep. Reading rules
(V2_README ground rule 4): **numbers from other papers are their numbers on their
substrate** (real GPU fleets, their traces, their baselines); PolyForge's numbers are
sim-backend decision quality on its own matrix/traces. No table here is a
head-to-head cell; the comparison is *shape against shape*. Where a published system
is directly reproducible at simulator scale, PolyForge carries it as a tuned in-matrix
baseline instead (HPA, KEDA, FIRM-replica, GPTCache-LRU, static, iso-cost variants).

## 1. Capability matrix

| Capability | SageServe (POMACS '25) | Chiron '25 | Aladdin '24 | K8s SLO-autoscaler '25 | VTC (OSDI '24) + Equinox/D²LPM/Justitia '25 | GPTCache / SCALM / InstCache / MeanCache | **PolyForge** |
|---|---|---|---|---|---|---|---|
| Replica/GPU autoscaling | ✔ forecast-aware | ✔ hierarchical | ✔ joint placement+scaling | ✔ SLO-driven HPA | — | — | ✔ MPC, forecast-pluggable |
| Semantic cache control | — | — | — | — | — | ✔ (cache is the system) | ✔ size + cost-aware eviction, jointly |
| Model-tier selection | — | — | — | — | — | — | ✔ per-tenant, jointly |
| **Joint cross-layer optimization** | — | — | placement+scaling only | — | — | — | **✔ all three knobs, one objective** |
| Multi-tenant fairness in objective | — | — | — | — | ✔ (scheduler-level) | — | ✔ (γ·(1−Jain), γ honestly nulled) |
| Per-tenant dollar/budget accounting | GPU-hours | GPU count | GPU cost | infra cost | — | API-call savings | ✔ USD/tenant + Budget CRD |
| Cache side-channel security eval | — | — | — | — | — | — (audit lit. separate) | ✔ attack + defense frontier (fig. 17) |
| Real-trace demand evaluation | ✔ 10M req production | ✔ production traces | ✔ real workloads | ✔ testbed | ✔ real serving | ✔ LMSYS et al. | ✔ BurstGPT v2.0 replay (10.63M req) |
| Live GPU-cluster substrate | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | — (sim; Phase 7 pending, ranking-only claim) |
| Pre-registered hypotheses, published nulls | — | — | — | — | — | — | **✔ 4 preregs pushed before runs; 3 nulls published** |
| Theory | — | — | queueing-based | — | ✔ 2× service bound | — | Props 1–2 (local optimum + calibration contraction; scope stated) |

The joint-control column is the thesis: no surveyed system co-optimizes replicas,
semantic cache, and model tier against one multi-tenant objective. The nearest
neighbors optimize *within* one layer (Aladdin: placement+scaling; "A Tale of Two
Scales": horizontal+vertical; INFaaS / joint-allocation heuristics: model variant +
provisioning — no cache, no per-tenant fairness).

## 2. Performance, shape against shape

**Cost / efficiency.** Published: Aladdin up to −71% serving cost; Chiron up to +70%
GPU efficiency; SageServe ~−25% GPU-hours on 10M production requests; the 2025
SLO-driven K8s autoscaler −18% infra cost vs tuned K8s baselines. PolyForge, on its
substrate against *tuned* HPA/KEDA/FIRM/static/GPTCache: **wins cost against every
baseline** (v2 matrix: −43%/−48% vs HPA/KEDA, p<1e-33, |d_z| 0.8–1.1; up to 12×/30×
vs pinned postures at iso-cost accounting), and on real BurstGPT demand shape the
paired point estimate is **−76%** vs all three scalers (first sample n=16
underpowered at p≈0.07–0.11; pre-registered second sample n=96 in
`PREREG_TRACE2.md`). Same order as the published "up to" claims, with means always
reported next to them.

**SLO.** Published: Chiron "up to +90% attainment"; OptScaler −36…70% violations;
the K8s framework −31% violation duration. PolyForge's pre-registered attempts to
*beat* tuned reactive scalers on raw violation failed twice and say so
(RESULTS_V2/V3): tuned-generous reactive scaling buys attainment at 3.2–3.4× spend.
PolyForge's earned SLO shape: **parity attainment at −43…−48% cost; violation wins
where reaction time structurally binds** (spike regime: −0.037 violation vs HPA,
p=5e-5, at −46% cost); composite J wins vs every baseline (p ≤ 1e-11). Note the
published numbers are against *default or non-SLO-tuned* baselines; PolyForge's are
against baselines tuned on the paper's own objective — a deliberately higher bar
(TUNING.md).

**Cache.** Published: GPTCache 61.6–68.8% hit on its benchmark; InstCache 51.34% on
LMSYS; SCALM +63% hit vs GPTCache; MeanCache federated user-side caching. PolyForge,
**measured on the same LMSYS-Chat-1M dataset** (200k-turn seeded sample, MiniLM,
exact NN; `SEMANTIC_CACHE.md`): adaptive semantic hit rate **48.8% at cosine 0.70 /
29.6% at 0.85** — same order as InstCache's 51.34% under a different mechanism
(they pre-populate predicted instructions; we cache observed prompts) — with
**+93% over a fixed cache at 10% of the working set**, and the metric none of the
cache-only papers can produce: **dollar-weighted savings per tenant tier**
($0.296/1k queries at mid-tier). In-matrix: +83% hit vs fixed sizing (0.106 vs
0.058). Plus the side-channel result that shared semantic caches leak membership at
AUC 0.88 and per-tenant partitioning restores chance at 24% latency cost, dominating
padding/TTL-jitter defenses.

**Fairness.** Published: VTC establishes the service-fairness definition and a 2×
bound, Equinox cuts worst-case service gaps −42% vs VTC, D²LPM adds locality (2.87×
throughput vs VTC). PolyForge: Jain 0.969 vs FIRM's 0.929 (p=7e-44, d_z=0.95) at 39%
lower cost; the γ-ablation null under injected interference is published rather than
hidden. Gap: no VTC-style baseline in the matrix yet — being closed (see §3).

**Forecasting.** No surveyed system publishes a forecaster decomposition; SageServe
uses forecasting but does not ablate it. PolyForge measures it twice: in-loop
(Holt −16.3% violation vs trend at equal cost) and on the real 10.63M-request trace
(Holt −26.9% one-step RMSE; the synthetic stand-in's seasonal advantage **did not
transfer** and is published as such — `FORECAST_TRACE_REAL.md`).

## 3. Gap analysis ("leakings") and status

| Gap vs literature | Severity | Status |
|---|---|---|
| Sim substrate vs their live GPU fleets | High (framing) | Mitigated and shrinking: real-trace demand (10.63M req) drives the sim; ranking-only claims. **Phase 6 CPU half DONE (session 16b,** `research/calibration/CALIBRATION.md`**):** measured llama-server congestion supports g(ρ)=1/(1−ρ) to first order (fitted a=0.86; R² of a=1: 0.857) and the deviation is conservative against lean (JCAC-like) operation. GPU tier table scripted (user-run T4). Phase 7 live kind run is now one command in a Codespace (eval-export landed; chart toggles + replay endpoint remain, documented) |
| No VTC-style empirical baseline | Medium | **Closing now**: token-fair water-filling controller (`vtc_replica`), tuned per TUNING.md, pre-registered comparison under interference injection |
| Real-LMSYS cache headline | ~~Medium~~ | **CLOSED (session 16c):** measured on the gated dataset — 29.6% @ 0.85 / 48.8% @ 0.70, +93% vs 10%-fixed, $-weighted savings attached; pre-run feasibility amendment committed before download (31c73c7) |
| p95 not p99 | Low | Deliberate deviation, documented. First measured distribution exists (session 16b): p95/mean 1.59–2.41 on a real CPU inference server vs the sim's flat 1.4 — a tail underestimate that is symmetric across systems (all share the estimator). Full p99 fix still awaits per-tier GPU distributions (`kaggle_tier_bench.py`) |
| Real-demand cost significance | Low | First sample directional (n=16); pre-registered n=96 second sample running |
| Production scale (SageServe's 10M served requests) | Conceded | Out of scope per V2_README; demand-side 10.63M replay is the honest analog at decision level |
| Proven scheduling bounds (VTC 2×) | Conceded | Props 1–2 with stated scope; no stronger claim made |
| Locality/prefix-cache scheduling (D²LPM) | Not claimed | Different layer (kernel/batch scheduling); noted as future work |
| Single real demand trace (BurstGPT) | Conceded (scoped) | **Explicit descope (session 16):** `etl_azure_functions.py` / `etl_alibaba_v2018.py` stay in the tree as tooling, but no thesis claim uses them — Azure Functions and Alibaba 2018 are FaaS/VM traces, not LLM-serving demand, so replaying them would test a regime the thesis does not claim. Any future use requires a new pre-registration. `etl_lmsys_chat1m.py` is NOT descoped — it is the committed Phase B protocol |
| Objective weights are self-chosen | Low (mitigated) | Baselines tuned on the same J (TUNING.md); exploratory weight-sensitivity sweep (`SENSITIVITY_J.md`, declared before run): direction never flips in 425 perturbed cells across all 5 campaigns, 416/425 keep p<0.01; the 9 exceptions sit at w_v=4 (2× the pre-registered violation weight) and lose only significance, consistent with the disclosed violation trade |

**What no surveyed system has, PolyForge has:** joint three-knob cross-layer control,
per-tenant dollar accounting with budget guardrails, a security evaluation of the
cache layer with a defense frontier, and a fully pre-registered evaluation whose
nulls are published next to its wins. That last row is a methodological superiority
no performance number in this table can substitute for.

## Sources (verified 2026-07-12)

- SageServe (POMACS/SIGMETRICS '25): <https://dl.acm.org/doi/10.1145/3771576> · <https://arxiv.org/abs/2502.14617>
- Chiron: <https://arxiv.org/abs/2501.08090> — up to +90% SLO attainment, +70% GPU efficiency
- Aladdin: <https://arxiv.org/abs/2405.06856> — up to −71% serving cost
- SLO-driven cost-aware K8s autoscaling: <https://arxiv.org/abs/2512.23415> — −31% violation duration, −18% cost
- A Tale of Two Scales (joint H+V scaling IP): <https://arxiv.org/abs/2407.14843>
- Joint resource allocation for SLO-constrained LLM inference: <https://arxiv.org/abs/2604.07472>
- INFaaS (model-less serving): <https://arxiv.org/abs/1905.13348>
- VTC / Fairness in Serving LLMs (OSDI '24): <https://arxiv.org/abs/2401.00588> · <https://www.usenix.org/conference/osdi24/presentation/sheng>
- Equinox (holistic fair scheduling): <https://arxiv.org/abs/2508.16646> — −42% worst-case service gap vs VTC
- D²LPM / locality-aware fair scheduling: <https://arxiv.org/abs/2501.14312> — 2.87× throughput vs VTC
- Justitia: <https://arxiv.org/abs/2510.17015>
- GPTCache: <https://aclanthology.org/2023.nlposs-1.24/> — 61.6–68.8% hit
- SCALM: <https://arxiv.org/abs/2406.00025> — +63% hit vs GPTCache
- InstCache: <https://arxiv.org/abs/2411.13820> — 51.34% on LMSYS
- MeanCache: <https://arxiv.org/abs/2403.02694>
- Semantic caching, offline→online adaptation: <https://arxiv.org/abs/2508.07675>
- Faro (SLO-aware on-prem inference clusters): <https://arxiv.org/abs/2409.19488>
- BurstGPT: <https://arxiv.org/abs/2401.17644> · prompt-cache audit: <https://arxiv.org/abs/2502.07776>
