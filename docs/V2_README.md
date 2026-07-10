# PolyForge v2 — Segment-Win Plan & Professional Hardening

**Status: Phase 0 complete (2026-07-11, session 12); no v2 matrix cell has been run.**
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

## Phase 1 — v2 headline run (2–3 sessions, local, no GPU/Docker)

- [ ] New experiment YAML `eval/experiments/matrix_v2.yaml`: same 1,800-cell matrix,
      systems = v1 set + `jcac_v2`.
- [ ] Run matrix; verify 100% valid runs (existing retry/exclusion machinery).
- [ ] Extend `research/analysis/run_analysis.py` to emit v1-vs-v2 paired comparison;
      regenerate RESULTS tables as `RESULTS_V2.md` (RESULTS.md untouched).
- [ ] Acceptance: `jcac_v2` SLO violation significantly below HPA and KEDA
      (paired d_z, p < 0.01) at equal-or-lower cost — the segment currently at parity.

## Phase 2 — Iso-cost baselines, gate v2 (1–2 sessions, local)

- [ ] Add `static_isocost` (provisioned at PolyForge's realized mean spend) and
      `cache_isocost` (fixed cache at PolyForge's mean cache spend) to
      `eval/harness/systems.py`; tune per TUNING.md discipline.
- [ ] Run the delta matrix cells; report gate v2 (≥3/5 metrics, p<0.01, |d_z|≥0.5,
      all baselines iso-cost) alongside — never instead of — the v1 gate.
- [ ] Acceptance: no comparison in the thesis remains structurally unwinnable
      (i.e., no baseline wins a metric purely by outspending).

## Phase 3 — Real traces under every claim (3–4 sessions, local, no GPU)

**3a. BurstGPT demand replay** — [github.com/HPMLL/BurstGPT](https://github.com/HPMLL/BurstGPT)
(10.31M real Azure OpenAI requests, 213 days; columns: timestamp, session ID,
model GPT-3.5/4, request/response tokens).

- [ ] Ingest script under `research/traces/` (repo already normalizes Azure Functions;
      same pattern). Map: timestamp→demand series, session ID→cache locality,
      model column→real tier-demand mix, token counts→work units.
- [ ] Re-run the headline matrix on BurstGPT-derived workloads
      (`eval/experiments/matrix_burstgpt.yaml`).
- [ ] Re-run the forecast ablation on it — real daily/weekly periodicity is the regime
      where `seasonal` (neutral on synthetic noise) should activate.
- [ ] Acceptance: headline claims restated as "on 10M real Azure OpenAI requests";
      overload cells identified for the SLO "up to" number.

**3b. LMSYS-Chat-1M semantic-cache evaluation** —
[huggingface.co/datasets/lmsys/lmsys-chat-1m](https://huggingface.co/datasets/lmsys/lmsys-chat-1m)
(1M real conversations; gated, free with HF account).

- [ ] Standard protocol: embed with `sentence-transformers/all-MiniLM-L6-v2` (CPU-ok)
      + FAISS; insert first half, query second half, sweep similarity threshold.
- [ ] Measure hit-rate curve for (i) fixed-size cache, (ii) PolyForge adaptive sizing;
      report hit rate *and* dollar-weighted savings (hits avoid tier-priced calls —
      a metric cache-only papers cannot report). Published anchors: InstCache 51.34%,
      SCALM +63% vs GPTCache.
- [ ] Replace the calibrated `h(c)` hit-rate curve in `research/jcac_sim/model.py`
      with the empirical fit (behind a flag; v1 curve preserved).

## Phase 4 — Fairness: make the γ-term measurable (1–2 sessions, local)

- [ ] Add noisy-neighbor interference injection to the sim workloads (correlated
      latency inflation when a whale tenant bursts — the W32 signal shape the γ-term
      was designed for; RESULTS.md:137 documents why v1 couldn't test it).
- [ ] Re-run the fairness ablation (γ=0 vs full) under injection; add worst-tenant
      p95 as a reported metric.
- [ ] Acceptance: γ-ablation becomes significant (or the negative result is reported
      and the term is dropped from claims — either outcome is a resolved segment).

## Phase 5 — Security: defense frontier on a real stack (1–2 sessions, local)

- [ ] Extend `research/security/cache_side_channel.py` with two alternative defenses:
      response-time padding/quantization and probabilistic TTL jitter.
- [ ] Plot leakage (AUC) vs hit-rate cost for all three defenses; claim upgrades from
      "we have a defense" to "per-tenant partitioning dominates the known frontier."
- [ ] Re-run the timing attack end-to-end against the real FAISS/sentence-transformers
      cache from Phase 3b (first real-stack demonstration + defense).

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

## Phase 8 — One-page theory (1 session, writing-adjacent)

- [ ] Proposition + half-page proof: (i) with exact per-tenant enumeration, two-round
      coordinate descent returns a block-coordinate optimum of the per-step objective;
      (ii) the self-calibration scale is bounded in [0.5, 1.0] and contracts under
      projection-reality agreement. Both hold by construction of the current solver.

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
