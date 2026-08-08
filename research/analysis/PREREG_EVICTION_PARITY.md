# Pre-registration: eviction-parity and severity rerun (V-series, adjudicates the audited cost-accounting confound)

Registered 2026-08-08 (session 35). Committed and pushed **before** any
campaign run; the push event is the timestamp anchor, mirrored to
`OSF_REGISTRATION.md` prospectively per DEFENSE_QA #17.

## Question (and provenance of the finding)

A four-perspective validity audit of the whole artifact (session 35)
found that the published cost headline carries an accounting asymmetry
that is not a property of control.

`eval/harness/systems.py` sets `lru_eviction=True` on sixteen baseline
arms — `hpa`, `keda`, `firm`, `static`, `gptcache`, `vtc_replica`,
`concurrency`, `learned_*`, and the isocost variants — and on no headline
`jcac` arm. That flag multiplies the baseline's inference spend by
`lru_miss_cost_factor()` = **1.4581** at `simulate.py:355`. The factor is
the geometric mean of LRU-over-cost-aware cost-per-request at the two
capacities in `research/results/eviction_comparison.csv` (1.3165 and
1.6150).

Three separate problems compound, all verifiable from the committed CSV:

1. **It is charged to controllers that cannot evict differently.**
   `hpa`, `keda`, `firm`, `vtc_replica` and `concurrency` never move
   `cache_mb` (`baselines.py:82,105` carry `state.cache_mb` forward), so
   they hold the initial 128 MB for the entire run. They are billed 45.8%
   more per cache miss for a policy difference their configuration cannot
   express.

2. **LRU is the most favorable of four available comparators.** In the
   same CSV, **GDSF beats the proposed cost-aware policy at both
   capacities** (0.00018774 vs 0.00019321 at 2000 entries; 0.00038729 vs
   0.00039309 at 400). Recomputing the factor against GDSF gives
   **0.978** — it would run *against* the proposal. ARC gives 1.337.
   The published number uses the single comparator that maximises the
   proposal's advantage.

3. **The proposed policy's overhead is never charged.** The same CSV
   records `overhead_p99_us` = **1104.9 / 1053.6 µs** for
   `costaware_w28`, against **0.0** for both `lru` and `arc`. The
   simulator charges baselines a cost penalty for eviction and charges
   the proposal nothing for the latency its alternative costs.

A second, independent finding bears on the condition under which the
cost claim is stated. `model.py:513,517` saturates the scored violation
at 1.0 (`min(1.0, over)`) while cost is unbounded, and `tier="none"` is
priced at $0.0 (`model.py:50`) with a 30 s latency (`model.py:58`). A
total AI outage and a 2× SLO miss therefore score identically. The
per-step unbounded severity `excess` is already computed
(`model.py:390,514,518`) and is **discarded at aggregation** — it appears
nowhere in `simulate.py`'s result. Measured `tier="none"` step-share on
the headline slice is 6.3%, and 34.4% on `joint_stress`.

This experiment measures whether the published record's conclusions
survive removing the accounting asymmetry and reporting severity
unbounded. **It does not re-run and does not edit any published
campaign** (R1/R4): `matrix_v2` and every committed record stand exactly
as they are, and this campaign is adjudicated against them.

## Design (frozen)

One factor family changes: **how eviction is priced, and what severity is
reported.** Controllers, forecasters, lattices, seeds, workloads, mixes,
cluster sizes and reps are untouched.

**Arms (frozen).** Six, in three matched pairs:

| arm | eviction pricing | cache posture | purpose |
|---|---|---|---|
| `jcac` | none (as published) | free, controller-chosen | the published proposal, unchanged |
| `hpa` | LRU ×1.4581 (as published) | fixed 128 MB | the published comparator, unchanged |
| `keda` | LRU ×1.4581 (as published) | fixed 128 MB | the published comparator, unchanged |
| `hpa_fair` | **none** | **fixed 512 MB** | reactive replica control with a competently pre-sized cache |
| `keda_fair` | **none** | **fixed 512 MB** | as above, event-driven |
| `jcac_evictcharged` | **cost-aware overhead charged** | free, controller-chosen | the proposal paying its own measured 1104.9 µs p99 |

512 MB is fixed in advance as the level the published `jcac` itself
converges to on the AI cells (`RESULTS_MOVE_CLAMP.md` mean cache 352 MB,
modal posture 512) and is within every cluster size's per-tenant cache
budget. It is **not** tuned in this campaign and is not revisited after
seeing any result.

**Eviction sensitivity band (frozen).** The factor is reported at all
four policies in the committed CSV — LRU 1.4581, ARC 1.3369, GDSF
0.9785, and 1.0 (no charge) — as a band on the headline delta, not as a
single number. Derivation is `lru_miss_cost_factor()`'s existing geometric
mean, re-parameterised by comparator; no new measurement is taken.

**Cells (frozen).** The v2 headline matrix exactly: 5 workload classes ×
4 tenant mixes × 3 cluster sizes × 5 reps, 120 steps. Same seed
derivation, same blocked design.

**Metrics (frozen).** The published set, plus two that are computed today
and discarded:
- `mean_excess` — traffic-weighted, **unbounded** SLO overshoot
  (`StepMetrics.excess`, aggregated exactly as `mean_violation` is).
- `tier_none_step_share` — fraction of tenant-steps served at
  `tier="none"`, i.e. with AI shed to a 30 s outage.

Both are reported for **every** arm. Neither enters any controller's
objective; they are reporting-only, so R4 holds for every existing arm.

## Hypotheses (frozen)

**EP-H1 (primary, cost).** Against `hpa_fair`/`keda_fair` at violation
parity, `jcac`'s cost advantage is **> 0** with a paired one-sided
Wilcoxon over the 300 blocked cells, α = 0.05, and the point estimate is
reported with its 95% CI. *Directional prediction: the advantage
survives but is materially smaller than the published −44…−50%.*

**EP-H2 (accounting share).** The published `jcac`-vs-`hpa` delta
recomputed at factor 1.0 differs from the published delta by **more than
10 percentage points**. This quantifies how much of the headline is
eviction accounting rather than control. *Directional prediction: yes.*

**EP-H3 (severity).** On `mean_excess`, `jcac` is **non-inferior** to
`hpa_fair` at a margin of 0.05. *Directional prediction: this is the one
most likely to FAIL, because saturation currently hides the shedding
measured at 6.3–34.4% of steps.*

**EP-H4 (self-charge).** `jcac_evictcharged` retains a cost advantage
over `hpa_fair`. *Directional prediction: yes, small.*

Decision rules are fixed now: EP-H1 and EP-H4 are one-sided paired
Wilcoxon with Holm correction across the four hypotheses of this family;
EP-H2 is a descriptive threshold, no test; EP-H3 is a non-inferiority
test at the stated margin. `d_z` is reported for all.

## Outcome handling and stopping rule

The campaign runs **once**, over the frozen matrix. No arm, cell, metric,
margin or decision rule is added or altered after the first result is
seen. Every hypothesis is reported PASS or FAIL with its statistic,
whichever direction it lands, in `RESULTS_EVICTION_PARITY.md`.

**A FAIL of EP-H1 or EP-H3 is a publishable outcome and is reported as
the headline finding**, exactly as `RESULTS_RISK.md` reported its null
and `RESULTS_MOVE_CLAMP.md` reported its adjudication. If EP-H1 fails,
the contribution is restated around joint control at parity, the ablation
program, and the live plane, and the cost headline is withdrawn in the
same document that reports it. The published record is not edited; it is
superseded in the open.

`reproduce.py` must re-derive every previously committed record
byte-identically after this work lands (R4). The new record joins the
reproduction gate.
