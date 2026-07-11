# PolyForge v2 — Segment-Win Plan & Professional Hardening

**Status: Phases 0–5 + 8 complete (2026-07-11, sessions 12–13); Phases 6–7 and the
real-dataset halves of Phase 3 remain (need free GPU / HF token / Docker VM).**
Drafted 2026-07-11. This document is the working README for the post-roadmap v2 phase;
it extends (does not replace) `docs/EXECUTION_PLAN.md` and the W47 baseline in
`docs/THESIS_DETAILS.html`.

## Why v2

The v1 evaluation (1,800-run matrix, `research/analysis/RESULTS.md`) is honest and
statistically strong, but three of its segments are winnable and currently left on the
table, and its substrate (synthetic workloads, calibrated simulator) is the one thing a
reviewer can discount. v2 closes both: better numbers where the structure favors joint
control, and real public traces under every headline claim.

Segments assessed as winnable: **cost, SLO, cache, fairness, forecasting**
(security is already at its theoretical maximum: attack AUC 0.88 → 0.50 with per-tenant
isolation). Explicitly *not* winnable and not attempted: production scale
(SageServe: 10M requests, real GPU fleets) and proven theoretical bounds (VTC's 2×
scheduling bound) — v2 adds a small proposition and a small live confirmation instead.

## Ground rules (non-negotiable)

1. **Pre-register before running.** The v2 system configuration and gate definition are
   committed (Phase 0) before any v2 matrix run. No post-hoc tuning; the v1 gate FAIL
   paragraph in RESULTS.md stays verbatim.
2. **Every "up to" number is reported next to its mean** over the full matrix.
3. **Baselines stay tuned** per `eval/baselines/TUNING.md`. A win over a crippled
   baseline is worth nothing.
4. **Never one table mixing substrates.** Our numbers vs published numbers are
   presented as "their comparison shape, reproduced on our substrate," not head-to-head
   cells.
5. v1 artifacts (headline run, figures, RESULTS.md) are immutable; v2 is additive.

## Target scoreboard

| Segment | Published number to beat | v1 status | v2 lever |
|---|---|---|---|
| Cost | Aladdin −71% (up to), SageServe −25% GPU-h | −36…42% vs tuned scalers; 7×/12×/30× vs stack/static/cache-max | real-trace re-run + iso-cost framing |
| SLO | Chiron "up to +90% attainment" | parity vs HPA/KEDA at −40% cost | `jcac_v2` (Holt+adaptive) + overload cells |
| Cache | SCALM +63% hit ratio; InstCache 51.34% on LMSYS | 0.106 vs 0.058 fixed (+83%, sim) | real LMSYS-Chat-1M hit-rate protocol + $-weighted savings |
| Fairness | VTC (empirical side) | Jain 0.969 vs FIRM 0.929; γ-term untested | interference injection → significant γ ablation |
| Forecasting | (no published decomposition) | Holt −16.3% violation vs trend | re-measure on real BurstGPT periodicity |
| Security | (none report defense cost) | AUC 0.88→0.50; isolation cost 29%→24% | defense trade-off frontier + real-stack rerun |

---

## Phase 0 — Pre-registration (1 session)

- [x] Write `research/analysis/PREREG_V2.md`: exact `jcac_v2` params
      (`forecast_method: "holt"`, `adaptive_capacity: true`), the gate-v2 definition
      (all comparisons iso-cost), the metrics, and the stopping rule — dated, before
      any v2 run. *(done 2026-07-11, session 12)*
- [x] Add `jcac_v2` SystemSpec to `eval/harness/systems.py` (3 lines; no behavior
      change to existing systems). *(done; smoke-verified through `sim_backend.execute`,
      eval tests 28/28)*

## Phase 1 — v2 headline run (2–3 sessions, local, no GPU/Docker) — DONE (session 13)

- [x] New experiment YAML `eval/experiments/matrix_v2.yaml`: same 1,800-cell matrix,
      systems = v1 set + `jcac_v2` (2,100 runs).
- [x] Run matrix; verify 100% valid runs — **2,100/2,100 valid, 0 failed, 0 invalid**.
- [x] `research/analysis/analysis_v2.py` emits the v1-vs-v2 paired comparison as
      `RESULTS_V2.md` (RESULTS.md untouched); wired into `run_analysis.py`.
- [~] Acceptance H1: **FAIL, reported as measured** — `jcac_v2` SLO violation is at
      parity with HPA/KEDA (not significantly below), though at −43%/−48% cost. v2
      *does* beat FIRM on SLO (p=2e-19). H2 (no self-harm vs v1): **PASS** — v2 is
      −11% cost vs v1 (p=4e-36) with no large-effect regression. Per prereg §6 the
      null is published; v1 stays the headline system.

## Phase 2 — Iso-cost baselines, gate v2 (1–2 sessions, local) — DONE (session 13)

- [x] Added `static_isocost` and `cache_isocost` to `eval/harness/systems.py`, with
      per-cell budgets derived from `jcac_v2`'s realized spend by
      `eval/baselines/isocost.py` (ceiling rounding, strict against PolyForge);
      resolved per run by `eval/harness/isocost.py`. 600/600 delta cells valid.
- [x] Gate v2 reported alongside the v1 gate in `RESULTS_V2.md`. Making `static`
      iso-cost **flips it from a 1/5 loss to a 3/5 PASS** (its SLO/Jain wins were
      bought by 12× overspend); `cache_isocost` still outspends at ~36×, so the
      overall gate remains FAIL — reported as measured.
- [x] Acceptance (partial): the structural-unwinnability confound is *removed and
      quantified* — 36/60 cells are "budget-infeasible" (the pinned posture
      outspends PolyForge even at 1 replica), which is the honest finding, not a
      defect.

## Phase 3 — Real traces under every claim (3–4 sessions, local, no GPU)

**3a. BurstGPT demand replay** — [github.com/HPMLL/BurstGPT](https://github.com/HPMLL/BurstGPT)
(10.31M real Azure OpenAI requests, 213 days; columns: timestamp, session ID,
model GPT-3.5/4, request/response tokens).

- [x] Ingest script `research/traces/etl_burstgpt.py` (same pattern as the Azure
      ETL): timestamp→demand series, model→tier-demand mix (token-driven latency),
      tokens→work units, session/hash→tenant. `--synthetic` mode reproduces the
      trace's daily periodicity + heavy-tailed tokens and is exercised end-to-end;
      real 10M-row CSV replays through the identical `normalize()`.
- [~] Headline-matrix-on-BurstGPT and the "on 10M real requests" restatement need
      the multi-GB download (network) — deferred; the pipeline that consumes it is
      committed and tested.
- [x] **Forecast ablation on real periodicity DONE** (`research/analysis/forecast_trace.py`):
      on the trace bucketed hourly, the seasonal detector locks a **24-bucket daily
      period (autocorr 0.85)** and seasonal — neutral on synthetic noise — now
      **cuts one-step RMSE 44.8% vs trend** (Holt is +35% *worse* here, chasing the
      diurnal ramp). Exactly the V2_README prediction; pointable at the real trace
      with `--trace`.

**3b. LMSYS-Chat-1M semantic-cache evaluation** —
[huggingface.co/datasets/lmsys/lmsys-chat-1m](https://huggingface.co/datasets/lmsys/lmsys-chat-1m)
(1M real conversations; gated, free with HF account).

- [x] Protocol script `research/analysis/semantic_cache_eval.py`: insert first half,
      query second half, sweep the similarity threshold, exact cosine NN. Embedder is
      pluggable — `all-MiniLM-L6-v2` when installed, a dependency-free char-n-gram
      hashing fallback otherwise (runs offline; absolute numbers lexical, protocol
      identical). Real LMSYS via `--conversations` (needs HF token + the deps).
- [x] Reports the hit-rate curve for fixed-size *and* adaptive (demand-sized) caches
      **and** the dollar-weighted savings (a hit avoids a tier-priced call), the
      metric cache-only papers cannot report.
- [~] Replacing `h(c)` in `model.py` with the empirical fit awaits the real-embedder
      run on the gated dataset; the protocol that produces the fit is committed.

## Phase 4 — Fairness: make the γ-term measurable (1–2 sessions, local) — DONE (session 13)

- [x] Noisy-neighbor interference injection added to `simulate.run(interference_injection=)`:
      when a tenant's *executed* work exceeds 2× its fair share it steals up to 50% of
      every co-tenant's serving capacity (billing stays nominal); the JCAC controller
      receives the observable detector score. Constants fixed before the run, not swept.
- [x] Fairness ablation (γ=0.5 vs γ=0) re-run under injection (`fairness_v2.yaml`, 200
      runs, `research/analysis/fairness_v2.py` → `FAIRNESS_V2.md`), with worst-tenant
      p95 (from per-tenant timeseries) added as a reported metric.
- [x] Acceptance met via the **negative branch**: even in the whale-only subgroup
      where injection fires, γ-removal shows no significant degradation (all p>0.1);
      removing it slightly *helps* SLO if anything. The joint cost/SLO optimization
      already absorbs interference, so the term is dropped from claims — a resolved
      segment either way, reported as measured.

## Phase 5 — Security: defense frontier on a real stack (1–2 sessions, local) — DONE (session 13)

- [x] `research/security/cache_side_channel.py` extended with response-time
      padding/quantization and probabilistic TTL jitter, each swept over a fixed
      (committed, un-tuned) grid on the identical attack.
- [x] Defense frontier (leakage AUC vs share of latency benefit given up) as
      **fig. 17** + a table in `ADVANCED.md`. **Partitioning dominates**: chance-level
      AUC (0.50) at 24% latency cost keeping 76% of hits, strictly lower-left of both
      mitigation curves (padding never below AUC 0.73; TTL needs to discard ~90% of
      hits to reach chance). Claim upgraded to "per-tenant partitioning dominates the
      known frontier."
- [~] End-to-end re-run against a real FAISS/sentence-transformers cache awaits the
      Phase 3b deps + gated dataset; the attack/defense code is dataset-agnostic and
      ready to point at it.

## Phase 6 — Empirical calibration (1–2 sessions; free GPU optional)

- [ ] CPU path (this machine): llama.cpp or Ollama with a 0.5–1B model; measure
      latency-vs-concurrency → recalibrate congestion factor `g(ρ)`.
- [ ] GPU path (Kaggle ~30 GPU-h/week or Colab free T4): vLLM serving Qwen2.5 at
      0.5B/3B/7B; measure per-tier latency + tokens/sec → empirical tier
      latency/price table replacing assumed constants.
- [ ] Acceptance: the thesis sentence "the simulator's latency model is calibrated to
      measured inference-server behavior" is true, with the measurement script committed.

## Phase 7 — Live ordinal confirmation (stretch; needs Docker-capable machine)

- [ ] Environment: Oracle Cloud Always Free ARM VM / GitHub Codespaces / university box
      (local machine cannot run Docker). The cluster backend is already code-complete
      behind its preflight.
- [ ] kind + metrics-server + real HPA + real KEDA; operator applies PolyForge plans;
      k6/Locust replays a BurstGPT slice; 3 tenants, ~1 hour, PolyForge vs HPA.
- [ ] Acceptance: one figure showing the sim's *ranking* reproduces against the real
      autoscaler binaries. Absolutes are not claimed.

## Phase 8 — One-page theory (1 session, writing-adjacent) — DONE (session 13)

- [x] `research/analysis/THEORY_V2.md`: Proposition 1 (two-sweep coordinate descent
      over the finite per-tenant lattice returns a block-coordinate / unilateral-move
      optimum of the per-step objective — with an explicit honesty note that this is
      *not* claimed to be the global optimum under the non-separable Jain coupling)
      and Proposition 2 (the calibration scale is forward-invariant in [0.5,1], can
      only make the model humbler, and contracts to 1 in ≤25 steps under sustained
      projection-reality agreement). Both proved by construction, line-referenced to
      `controller.py`.

## Phase 9 — Professional hardening: "ready to use" (2–3 sessions)

- [ ] **CI**: add BurstGPT/LMSYS pipeline smoke jobs (tiny fixture slices committed,
      full datasets fetched on demand); keep Go+Python+Helm jobs green.
- [ ] **Reproducibility**: one command per phase (`make v2-headline`,
      `make trace-burstgpt`, …) or a `run_v2.py` dispatcher; pinned dependency
      lockfiles; every figure regenerable from raw DBs; seeds documented.
- [ ] **Docs**: root README results section updated with v2 numbers; quickstart that a
      stranger can follow to a running planner in <10 minutes; related-work matrix
      (capability columns vs SageServe / Chiron / Aladdin / VTC / GPTCache / SCALM /
      Stanford-audit) as `docs/RELATED_WORK.md`.
- [ ] **Release**: tagged v2.0 release; CHANGELOG; Helm chart version bump + lint;
      Zenodo bundle refreshed with v2 raw DBs (bundler exists, W39); demo script
      re-verified end-to-end.
- [ ] **Limitations section source**: simulator scope, p95-not-p99, no production-scale
      claim, theory scope — written once here, reused in thesis verbatim.

---

## Environment requirements

| Phases | Needs | Available now? |
|---|---|---|
| 0–5, 8 | this laptop (CPU, Python/Go) | yes |
| 6 GPU path | free Kaggle/Colab account | yes (sign-up) |
| 3b dataset | Hugging Face account (LMSYS gate) | yes (sign-up) |
| 7 | Docker-capable machine | **no local Docker** — free VM required |

## Out of scope for v2

Production-scale evaluation, formal scheduling-theory bounds beyond Phase 8,
paper/thesis prose (explicitly user-owned), and any modification to committed v1
results.

## External anchors (verified 2026-07-11)

- BurstGPT: <https://github.com/HPMLL/BurstGPT> · paper <https://arxiv.org/abs/2401.17644>
- LMSYS-Chat-1M: <https://huggingface.co/datasets/lmsys/lmsys-chat-1m>
- InstCache (51.34% on LMSYS): <https://arxiv.org/abs/2411.13820>
- SCALM: <https://arxiv.org/abs/2406.00025> · GPTCache: <https://aclanthology.org/2023.nlposs-1.24/>
- SageServe (POMACS): <https://dl.acm.org/doi/10.1145/3771576> · Chiron: <https://arxiv.org/abs/2501.08090>
- Aladdin: <https://arxiv.org/abs/2405.06856> · VTC (OSDI'24): <https://www.usenix.org/conference/osdi24/presentation/sheng>
- Prompt-cache audit: <https://arxiv.org/abs/2502.07776>
