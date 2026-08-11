# Pre-registration: trace-replay eviction parity (V-series, adjudicates the −70% / −42% real-demand headlines)

Registered 2026-08-11 (session 37). Committed and pushed **before** any
campaign run; the push event is the timestamp anchor, mirrored to
`OSF_REGISTRATION.md` prospectively per DEFENSE_QA #17. The branch
`v-series-validity-remediation` was pushed to `origin` immediately before
this file was written, so the anchor for this prereg is a real push event
and not a commit hash (the session-35 weakness, disclosed in
`docs/REMAINING_WORK.md`, is not repeated here).

## Question (and provenance of the finding)

`RESULTS_EVICTION_PARITY.md` (session 35) established that the *synthetic*
cost headline was substantially an artifact of eviction accounting: against
`hpa_fair` — a comparator that pays no LRU charge and is pre-sized at
512 MB — the published −36.0% became **+0.8% (EP-H1a FAIL)**. That
adjudication covered the `matrix_v2` campaign only.

**The two strongest real-data claims in the artifact have never been
re-scored fairly.** Both are produced by the same code path:

- `research/analysis/trace_matrix.py:126` (`run_one`) passes
  `miss_cost_factor=lru_miss_cost_factor()` — **1.4581** — to
  `simulate.run` for every arm whose `SystemSpec` sets `lru_eviction=True`,
  and `1.0` otherwise. Verified by reading, session 37.
- `run_one` **never threads `spec.static_cache_mb`** into
  `simulate.run(initial_cache_mb=…)`, unlike `eval/harness/sim_backend.py:139`
  which does. Consequently the fair arms cannot even enter this substrate as
  written: `hpa_fair` and `keda_fair` would silently run at the default
  128 MB and be indistinguishable from the published arms except for the
  charge. This wiring gap is why the trace headlines were never adjudicated.
- `trace_matrix2.py` and `trace_matrix_azure.py` both import and call this
  same `run_one`, so both inherit both properties.

The consequence is visible in the published records themselves. In
`RESULTS_TRACE2.md` and `RESULTS_TRACE_AZURE.md` the reactive baselines all
report `cache_hit_rate` = **0.1133**, identical to four decimal places on
two different traces, because `baselines.py` carries `state.cache_mb`
forward unchanged and they are therefore pinned at the initial 128 MB for
the whole run. PolyForge reports 0.1703 (BurstGPT) and 0.2182 (Azure). The
baselines are thus **cache-starved and simultaneously billed 45.8% extra on
the tier spend that their starvation causes**, while the proposal is billed
neither penalty.

The published deltas at stake, computed from the committed records:

| trace | record | n | jcac | hpa | keda | firm | headline vs hpa |
|---|---|---:|---:|---:|---:|---:|---:|
| BurstGPT v2.0 | `RESULTS_TRACE2.md` | 96 windows | 32.58 | 110.2 | 109.8 | 112.4 | **−70.4%** |
| Azure LLM 2024 | `RESULTS_TRACE_AZURE.md` | 72 windows | 34.46 | 59.92 | 59.37 | 59.97 | **−42.5%** |

This experiment measures whether those two conclusions survive removing the
accounting asymmetry, on the same windows, seeds and pairing.

**It does not re-run and does not edit any published campaign** (R1/R4).
`RESULTS_TRACE.md`, `RESULTS_TRACE2.md`, `RESULTS_TRACE_AZURE.md` and their
committed CSVs stand exactly as published; this campaign writes to its own
output paths and is adjudicated against them.

## Design (frozen)

One factor family changes: **how eviction is priced, and what cache posture
the reactive comparator is deployed with.** Traces, windows, window
construction, demand scale, jitter seeds, tenants, cluster limits,
controllers, tuned parameters and the composite objective are untouched and
are imported from the committed code.

**Arms (frozen).** Seven per trace: the five published arms **unchanged**,
plus the two fair comparators.

| arm | eviction pricing | cache posture | purpose |
|---|---|---|---|
| `jcac` | none (as published) | free, controller-chosen | the published proposal, unchanged |
| `jcac_v2` | none (as published) | free, controller-chosen | published Holt variant, unchanged |
| `hpa` | LRU ×1.4581 (as published) | pinned 128 MB | published comparator, unchanged |
| `keda` | LRU ×1.4581 (as published) | pinned 128 MB | published comparator, unchanged |
| `firm` | LRU ×1.4581 (as published) | pinned 128 MB | published comparator, unchanged |
| `hpa_fair` | **none** | **pinned 512 MB** | reactive replica control, competently pre-sized |
| `keda_fair` | **none** | **pinned 512 MB** | as above, event-driven |

`hpa_fair` / `keda_fair` are the registry entries already committed in
`eval/harness/systems.py:237,243` for `PREREG_EVICTION_PARITY`; they are
adopted here **without modification**, and 512 MB is inherited from that
prereg rather than chosen now. They differ from published `hpa` / `keda`
in exactly two attributes (`lru_eviction`, `static_cache_mb`) and in
nothing else — same controller, same tuned params, same seeds.

Re-running the five published arms alongside the two new ones is
deliberate: it makes every comparison within this record internally
paired, it supplies the two new severity metrics for the published arms
(which the committed CSVs lack), and it constitutes an **independent
replication check** — the five published arms must reproduce their
committed per-window rows on the shared columns. Any divergence is a
finding and is reported as one.

**Traces and protocols (frozen, inherited verbatim).**

| trace | protocol source | windows | window | seed base | tenants |
|---|---|---:|---|---:|---:|
| BurstGPT v2.0 | `PREREG_TRACE2.md` §2 | 96 (48/segment) | 6 h | 2000 | 8 |
| Azure LLM 2024 | `PREREG_TRACE_AZURE.md` §2 | 72 (tiling) | 3 h | 3000 | 8 |

Demand construction, the scale factor `k`, pseudo-tenantization and the
per-window jitter draw are called through the committed functions, not
reimplemented. Every arm sees identical jittered demand within a window
(exact pairing), as in the published runs.

**The two traces are replications, not a pooled family.** They are scored
separately, with their own Holm family each. Nothing is pooled across
traces at any point.

**Metrics (frozen).** The published set — `total_cost_usd`,
`mean_violation`, `violation_step_share`, `mean_jain`, `cache_hit_rate`,
`J` — plus three that the engine computes and this substrate currently
discards:

- `mean_excess` — traffic-weighted, **unbounded** SLO overshoot
  (`mean_violation` saturates at 1.0, so a 2× miss and a shed AI service
  score identically).
- `tier_none_step_share` — fraction of tenant-steps served at `tier="none"`.
- `total_tier_cost_usd` — the **unscaled** tier spend, so the eviction
  sensitivity band below can be recomputed exactly rather than asserted.

All three are reporting-only, enter no controller objective, and are
`0.0`-defaulted, so every published arm replays bit-identically (R4).

**Eviction sensitivity band (frozen).** Because `miss_cost_factor` scales
only `m.cost_tier_usd` (`simulate.py:380`), total cost at any comparator
factor *f* is exactly `infra + f × total_tier_cost_usd`. The headline delta
is therefore reported at all four policies in the committed
`eviction_comparison.csv` — none 1.0, GDSF 0.9785, ARC 1.3369, LRU 1.4581,
via `harness.systems.eviction_sensitivity_band()` — as a **band on the
delta itself**, computed from measured quantities. No new measurement is
taken to produce the band.

## Hypotheses (frozen)

Scored **per trace**, with a Holm family of **four** per trace
(`stats.holm_bonferroni`, α = 0.05). TP-H2 is descriptive and enters no
family.

**TP-H1a (primary, cost).** On `total_cost_usd`, `jcac` is cheaper than
`hpa_fair`: one-sided paired Wilcoxon over the trace's windows,
alternative `less`. *Directional prediction: the −70% / −42% shrink
materially. Whether any advantage survives is unknown — that is the point
of the experiment.*

**TP-H1b (primary, cost).** As TP-H1a against `keda_fair`.

**TP-H3a (severity).** On `mean_excess`, `jcac` is **non-inferior** to
`hpa_fair` at a margin of **0.05** — the margin frozen by
`PREREG_EVICTION_PARITY` and inherited unchanged. One-sided paired test of
`diff − margin < 0`.

**TP-H3b (severity).** As TP-H3a against `keda_fair`.

**TP-H2 (accounting share, descriptive — no test).** On each trace, the
relative cost delta against the published comparator (`jcac` vs `hpa`,
which carries the 1.4581× charge and the 128 MB pin) differs from the
delta against `hpa_fair` by **more than 10 percentage points**.
*Directional prediction: yes, on both traces.*

**Test choice, stated in advance.** The decision rule is the one-sided
paired **Wilcoxon** signed-rank test, matching `PREREG_EVICTION_PARITY`:
per-window cost differences are not symmetric, and the V-series standard is
the signed-rank test. This is a **deliberate deviation** from
`PREREG_TRACE2.md` §3, which froze a paired *t* at α = 0.01. To keep the
comparison to the published record apples-to-apples, the paired *t*
statistic and its p-value are **also reported for every comparison**, and
whether each comparison clears the published α = 0.01 is stated. The
Wilcoxon is the gate; the *t* is reported so no one has to take the change
of test on trust. `d_z` is reported for all.

## Outcome handling and stopping rule

The campaign runs **once**, over the frozen arms, traces and windows. No
arm, window, metric, margin, test or decision rule is added or altered
after the first result is seen. Every hypothesis is reported PASS or FAIL
with its statistic, whichever direction it lands, in
`RESULTS_TRACE_PARITY.md`.

**A FAIL of TP-H1 on either or both traces is a publishable outcome and is
reported as the headline finding of the record**, exactly as
`RESULTS_EVICTION_PARITY.md` reported EP-H1a's FAIL. The pre-committed
response, fixed now rather than after seeing the numbers
(`docs/PUBLICATION_ROADMAP.md` §7): the scoreboard's Cost row is restated
in `RESULTS_MASTER.md` as parity-at-lower-severity on real demand, and the
manuscript's claim leans on severity, the `keda_fair` margin and fairness.
**The published records are not edited; they are superseded in the open.**

If the five published arms fail to reproduce their committed per-window
rows, the campaign is halted and the divergence is diagnosed and reported
before any hypothesis is scored — a replication failure would invalidate
the substrate, not merely this comparison.

`reproduce.py` must re-derive every previously committed record
byte-identically after this work lands (R4). The new record joins the
reproduction gate.
