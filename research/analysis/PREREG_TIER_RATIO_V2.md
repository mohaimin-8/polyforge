# Pre-registration: tier-price ratio sensitivity at the CORRECTED GPU corner (session 44)

Registered 2026-09-08 (session 44). Committed and pushed **before** any run of
the experiment it registers; the push event is the timestamp anchor, as for
every prior prereg in this repository.

This is a **new pre-registration with exactly one changed factor** — the
tier-price vector — in the pattern of `PLANNER_CELLS_DEALIAS.md` and
`PREREG_RISK_BUDGET.md`. It supersedes nothing: `PREREG_TIER_RATIO.md`, its
frozen grid, and `RESULTS_TIER_RATIO.md` stand exactly as executed. What
follows re-asks their question at the corner they *intended* to test.

## Why this exists

`PREREG_TIER_RATIO.md` asked whether the headline cost result survives when
the economy moves to "the measured self-hosting corner", and fixed that
corner at **1 : 1.516 : 16.64** from `TIER_BENCH.md`.

**That corner is not the GPU corner.** Its `large` coordinate came from a run
in which a 16 GB card could not hold Qwen2.5-7B at fp16 (~15 GB) and
`device_map="auto"` spilled layers to host memory. The 20665.1 ms behind
16.64 measured the CPU offload, not the tier. Session 44 re-ran the identical
protocol on `GPU T4 x2` (2 x 16 GB) with **`modules offloaded to cpu/disk: 0`
for all three tiers** and measured **1 : 1.475 : 1.518**
(`research/calibration/tier_bench_t4.csv`; see `TIER_BENCH.md`).

The mid coordinate reproduces (1.516 → 1.475). The large coordinate collapses
by a factor of **10.96**. Two consequences motivate this file:

1. The **decision** reading (`matrix_gpu_econ.yaml`) priced `large` at
   `1.664e-3`, roughly **11x** the corrected price.
2. The **accounting** reading (`breakeven_tier.py`) froze
   `R_LARGE = [5.0, 10.0, 16.6, 33.0, 100.0]`. The corrected coordinate,
   1.518, is **3.3x below the smallest value in that grid**, and the frozen
   `R_MID` minimum of 1.52 also excludes 1.475. The grid never contained the
   true corner and, being frozen, cannot be extended in place.

## Design (frozen)

Two readings, mirroring the originals.

### A. Accounting reading — `breakeven_tier_v2.py`

Re-prices committed per-run tier spend by exact algebra with decisions frozen.
It **imports `load_rows` and `reprice` from the frozen `breakeven_tier.py`**
rather than reimplementing them, so the arithmetic is identical by
construction and only the grid differs. `breakeven_tier.py` is not modified.

Frozen grid — a strict **superset** of the v1 grid, so every v1 point remains
evaluable and v1's finding is re-derivable inside it:

```
R_MID   = [1.475, 1.52, 3.0, 5.0, 10.0]              (5)
R_LARGE = [1.518, 2.5, 5.0, 10.0, 16.6, 33.0, 100.0] (7)
LEVEL   = [0.2, 1.0, 5.0]                            (3)
                                            105 price vectors
PUBLISHED           = (10.0, 100.0, 1.0)
GPU_CORNER_CORRECTED = (1.475, 1.518, 1.0)
```

### B. Decision reading — `eval/experiments/matrix_gpu_econ_v2.yaml`

An exact mirror of `matrix_gpu_econ.yaml` (itself an exact mirror of the v1
headline `full.yaml`: 6 systems x 5 workload classes x 4 tenant mixes x 3
cluster sizes x 5 reps = 1,800 sim runs, steps=120, `timeseries_reps: 1`,
`retries: 2`) with **one** change, the economy block.

**Price derivation, fixed here.** `small` anchors at the published small-tier
price (1.0e-4 USD/req). `mid` and `large` scale by the corrected measured
per-request ratios from `tier_bench_t4.csv` (mean ms 2089.8 / 3081.5 /
3171.5), 4 significant digits as in v1: 3081.5/2089.8 = **1.475**,
3171.5/2089.8 = **1.518**. `none` stays 0.

```yaml
economy:
  tier_cost_small: 1.0e-4
  tier_cost_mid:   1.475e-4
  tier_cost_large: 1.518e-4
```

**Ratios are read from `tier_bench_t4.csv` and from no other file.** All three
tiers ran there under one identical configuration, which is what makes the
ratio apples-to-apples; `tier_bench_1gpu.csv` holds better *absolutes* for
small/mid but mixes placements and is therefore not a ratio source
(`TIER_BENCH.md`, "Where each tier row should now be read from").

**No baseline retuning**, disclosed, for the reason v1 gave: hpa/keda/firm/
vtc/static never choose tiers, and gptcache's tier rule is demand/latency
driven, not price driven. jcac re-decides through the shared model.

## Hypotheses (frozen)

Pairing and tests identical to the headline analysis: per-cell pairing on
(workload, tenant_mix, cluster_size, rep) = 300 pairs per baseline, one-sample
t on paired differences, alpha 0.01.

- **TR2-H1 (primary):** under the corrected GPU-corner economy, jcac's
  `total_cost_usd` beats **each** of hpa, keda, firm, static, gptcache
  (mean paired diff < 0, p < 0.01, all five).
- **TR2-H2 (secondary):** jcac's composite J (pre-registered form and
  weights) beats each of the five baselines (p < 0.01).

### Pre-committed prediction, and why it is not the safe one

**The corrected economy is very nearly tier-flat** — 1 : 1.475 : 1.518, a
total spread of 1.52x across all three tiers, against v1's 16.64x and the
published table's 100x. In a nearly tier-flat economy **tier choice is barely
a cost lever at all**, so the component of jcac's advantage that came from
routing small-heavy while reactive baselines tier up should **shrink sharply,
and could vanish**. This is the opposite of the comfortable prediction and it
is recorded before the run.

Concretely, the expectation is: **margins narrow substantially against v1's
accounting figures** (hpa −40.2%, keda −45.9%, firm −43.1%, static −60.0%,
gptcache −83.0%), with `gptcache` — whose v1 margin was the largest and most
tier-driven — narrowing most. Whatever cost advantage survives should be
attributable to the **replica/infrastructure economy**, which this change does
not touch, rather than to the tier economy.

**Falsifier, pre-committed:** if TR2-H1 fails against any baseline, the cost
claim's economy envelope is bounded at the corrected corner and that bound is
published as the finding. It is not re-run, not re-tuned, and the grid is not
widened. A TR2-H1 failure would mean the headline cost win depends on a
tier-price spread the measured hardware does not exhibit — which is exactly
the question DEFENSE_QA #15 exists to answer honestly, and it goes into the
thesis limitations verbatim.

## Outcome handling

- All outcomes published as measured in `RESULTS_TIER_RATIO_V2.md`, TR2-H1
  failure included and headlined if it occurs.
- The v1 records are **not** edited. `RESULTS_TIER_RATIO.md` and
  `RESULTS_TIER_WU.md` remain as executed; their titles quote the superseded
  corner and a disclosure note is added pointing here, in the same spirit as
  the amendment discipline used by `PREREG_WIRE_ATTACK.md`.
- Violation/fairness deltas reported descriptively; no new SLO hypothesis
  rides along.
- `RESULTS_TIER_WU.md`'s work-unit multipliers (1.516x / 16.64x) derive from
  the same superseded measurement. **Re-running that campaign is NOT
  registered here** and would need its own file; this prereg covers the price
  readings only, and the work-unit exposure is disclosed rather than closed.

## Stopping rule

One execution of the 105-vector accounting grid and one execution of the
1,800-run matrix (`raw_sim_gpu_econ_v2.duckdb`), harness retry policy as
configured, crash-resume allowed. The campaign is valid only at 1,800/1,800
valid runs. No widening, no second attempt, no post-hoc price points in this
campaign.
