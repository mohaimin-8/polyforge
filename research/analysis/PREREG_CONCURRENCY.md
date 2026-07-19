# Pre-registration — concurrency/queue-depth autoscaler baseline (the 2026 serving-stack arm)

Registered 2026-07-19 (session 27). Committed and pushed **before** the tuning
sweep or any matrix run; the push is the timestamp anchor; OSF mirror
prospective. DEFENSE_QA #22 asks the sharpest modern-baseline question the
matrix leaves open: the tuned reactive arms (HPA on utilization, KEDA on rps)
are the *2020* reactive stack, while the 2026 LLM-serving stack (Knative KPA,
AIBrix pod autoscaler, llm-d workload autoscaling, Gateway API Inference
Extension) scales on **in-flight requests / queue depth**. Until now the
answer was argument (`RELATED_WORK.md` §4). This campaign makes it a
measurement.

## §1 The baseline (frozen; implemented before this push)

`concurrency` (`research/jcac_sim/baselines.py::ConcurrencyController`,
registered in `harness/systems.py` with LRU eviction accounting like every
caching-agnostic baseline): the KPA rule `desired = ceil(L_total / c_t)` on
the in-flight-work signal `L_total = ρ·g(ρ)·replicas` (Little's law under
the model's own congestion curve — superlinear near saturation, unlike HPA's
linear ρ), immediate scale-up, scale-down only after `stable_intervals`
consecutive below-target readings (the KPA stable-window analog). Cache and
tier fixed: it is a replica autoscaler, like the vendor systems it mirrors.
Unit-tested: panic scale-up, ≥-HPA aggression near saturation, stable-window
scale-down (`test_jcac.py`).

## §2 Tuning (frozen grid, W34 protocol verbatim)

`python baselines/tune.py --only concurrency` on the committed tuning slice
(3 classes × 2 mixes × 2 reps, medium cluster, disjoint seeds, scored on the
paper's own J):

- `target_concurrency` ∈ {0.5, 1.0, 1.5, 2.5, 4.0} — under the published
  a = 1 congestion form, c_t = ρ·g(ρ) at steady state, so this spans the
  same vendor-sane ρ* envelope (0.33–0.80) as the utilization grids; the
  degenerate always-provision end stays represented by `static`, per
  `TUNING.md`'s documented bounding rule.
- `stable_intervals` ∈ {1, 3, 6} (10–60 s stable window at 10 s intervals;
  Knative's default stable window is 60 s).

Winner merges into `tuned.yaml`; the sweep CSV commits to
`grids/concurrency.csv`. Committed W34 rows are untouched (the vtc_replica
`--only` precedent).

## §3 Matrix (frozen)

`eval/experiments/matrix_concurrency.yaml`: sim backend, 120 steps,
**systems [jcac_anchored, concurrency, hpa]** × 5 workload classes × 4
tenant mixes × 3 cluster sizes × 5 reps = **900 runs**, standard validation
(row counts, no dupes/NULLs). `jcac_anchored` is the quotable controller
(RESULTS_MOVE_CLAMP); `hpa` rides along for the descriptive sanity reading
only. Blocked-factorial pairing on (workload, mix, size, rep), the same
matched-cells design as every matrix campaign.

## §4 Hypotheses

- **CQ-H1 (confirmatory):** `jcac_anchored` beats tuned `concurrency` on
  composite J (paired t over the 300 matched cells, p < 0.01, mean diff
  < 0). A J win accompanied by a violation regression at p < 0.01 and
  |d_z| ≥ 0.5 is **PASS-with-disclosure**, stated prominently (the standing
  trade rule).
- **CQ-H2 (confirmatory):** `jcac_anchored` beats tuned `concurrency` on
  cost (paired t, p < 0.01, mean diff < 0).
- **CQ-D1 (descriptive, no gate):** `concurrency` vs tuned `hpa` on
  J/cost/violation, aggregate and per class — where the modern in-flight
  signal helps over utilization, if anywhere. No pass/fail; this reading
  exists so the new arm's strength is itself on the record.

**Falsifier, pre-committed:** if tuned `concurrency` matches or beats
`jcac_anchored` on J (CQ-H1 fails), that result headlines the thesis
limitations: the 2026 reactive stack would have closed the joint-control
gap without joint control, and DEFENSE_QA #22's answer becomes the
baseline's win, published as measured.

## §5 Stopping rule

One tuning sweep over the frozen grid, one 900-run matrix execution (crash
retries only, retries: 2), one analysis pass
(`analysis_concurrency.py` → `RESULTS_CONCURRENCY.md`). No grid widening,
no re-tuning after any matrix number is seen, no second matrix. SLO
confirmatory attempts remain closed (v3 stopping rule): CQ-H1/H2 are J and
cost gates only; violation readings ride as disclosure or description.
