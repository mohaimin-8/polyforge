# Pre-registration: layered tier rule (V-series, adjudicates the −joint-control ablation)

Registered 2026-08-13 (session 38). Committed and pushed **before** any
implementation of the new arms exists and before any campaign run; the push
event is the timestamp anchor.

## Question (and provenance of the finding)

`RESULTS_MASTER.md` reports **−joint control +2884% cost** as an ablation
result. `eval/harness/systems.py:455` defines that ablation as
`jcac_nojoint = SystemSpec("layered")`, so the number is a property of the
`layered` baseline's tier controller, not of joint control as such.

That controller has an **absorbing state**, verified by reading and by
measurement:

- `research/jcac_sim/baselines.py:155-168` (`LayeredController._tier_rule`)
  and `:303-316` (the same rule, duplicated in `GPTCacheController`) order
  tiers **by name** — `["small", "mid", "large"]` — escalate one position on
  `ai_p95_ms > target`, and de-escalate only below `0.3 × target`.
- `research/jcac_sim/model.py:53-57`: for `agent` traffic the measured tier
  latencies are **non-monotonic in that name order** — small 6000 ms,
  mid 2500 ms, large 3500 ms. `large` is both **slower than `mid`** and
  **10× dearer**.
- The standard-class AI target is `2500 × 2.5 = 6250 ms`, so the
  de-escalation threshold is `0.3 × 6250 = 1875 ms`. With `P95_FACTOR = 1.4`
  the *minimum achievable* p95 is 8400 ms at `small`, 3500 ms at `mid` and
  4900 ms at `large`. **Every one exceeds 1875 ms**, so once a tenant carries
  AI load the rule can never step down from any tier. It ratchets
  small → mid → large and stays.

Measured on one cell (`agentic`, `uniform`, `medium`, seed 42, 120 steps,
8 tenants), which is the verification this prereg's design rests on:

| arm | tier histogram (tenant-steps) | total cost |
|---|---|---:|
| `layered` | `mid` 110, `large` **850** | $206.04 |
| `gptcache` | `mid` 110, `large` **850** | $205.79 |
| `jcac` | `small` 722, `none` 238 | **$1.97** |

88.5% of tenant-steps sit in the latched 100×-price tier. A baseline with an
absorbing state at the most expensive tier is not a competent implementation,
and an ablation delta computed against it measures the latch, not the
contribution.

**This does not re-score or edit any published record.** `RESULTS.md` and its
ablation table stand exactly as committed (R1/R4); the published `layered`
and `gptcache` arms are untouched and keep replaying bit-for-bit.

## Design (frozen)

One factor changes: **how the layer-local tier controller chooses a tier.**
Cells, steps, reps, mixes, cluster size, demand construction, seeds, cache
rule, replica rule and every other mechanism are inherited unchanged from
`ablations.yaml`.

**Arms (frozen).**

| arm | tier rule | purpose |
|---|---|---|
| `jcac` | joint | the reference, unchanged |
| `jcac_nojoint` (= `layered`) | published, name-ordered | unchanged; it replicates |
| `gptcache` | published, name-ordered | unchanged; it replicates |
| **`jcac_nojoint_v2`** (= `layered_v2`) | **latency-ranked, projection-gated** | the competent −joint-control comparator |
| **`gptcache_v2`** | **latency-ranked, projection-gated** | the competent cache-everything comparator |

**The new rule (frozen, stated completely).** For the tenant's dominant AI
kind — the AI kind with the highest arrival rate this step, ties broken by
`AI_KINDS` order:

1. Rank the admissible tiers by **measured** `TIER_BASE_LATENCY_MS[kind]`,
   ascending. Never by name.
2. Let `p95` be the projected AI p95 at the current state and tier, from
   `evaluate_step` — the same projection the published rule already calls.
3. **Escalate** if `p95 > target`: move one rank toward faster, if a faster
   rank exists.
4. Otherwise **de-escalate** to the next *cheaper* tier by price order
   (`small` < `mid` < `large`) if its projected p95 at the current state is
   `≤ MARGIN × target`, with **`MARGIN = 0.8`** frozen here.
5. Otherwise hold.

`MARGIN = 0.8` rather than the published `0.3` is part of the one factor: 0.3
is the proximate cause of the latch, but the load-bearing defect is the name
ordering, and both belong to "how the rule chooses". The margin exists to
give hysteresis, not to hit a number; it is frozen before any run and is not
tuned afterwards.

Both new arms default to the published rule everywhere they are not selected,
so **every committed campaign replays bit-identically (R4)**.

**Cells (frozen).** `ablations.yaml`'s design exactly: 5 workloads
(`crud_bursty`, `crud_steady`, `ai_cacheable`, `ai_uncacheable`, `agentic`)
× 4 mixes (`uniform`, `premium_heavy`, `besteffort_heavy`, `whale`) ×
`medium` × 5 reps × 5 systems = 500 runs. New output DB; `ablations.duckdb`
is never reopened.

**Metrics (frozen).** The published ablation set, plus `tier_step_share` per
tier so the latch's disappearance is visible rather than inferred.

## Hypotheses (frozen)

Scored over the 100 matched cells (workload × mix × rep), Holm family of
**two** (`stats.holm_bonferroni`, α = 0.05), one-sided paired Wilcoxon as in
WP1/WP15, with the paired t reported alongside.

**LF-H1 (primary).** The −joint-control cost delta computed against
`jcac_nojoint_v2` is **smaller than +2884%**. *Directional prediction: yes,
and by a wide margin. This hypothesis is nearly trivial to pass; it is
registered anyway because the honest replacement number is the deliverable
and must be reported whatever it is.*

**LF-H2 (primary).** `jcac`'s composite `J` is lower than `gptcache_v2`'s.
*Directional prediction: **unknown**. The published −94.7% vs `gptcache` is
substantially the latch; with the latch removed this may fail, and the
response is fixed below.*

## Outcome handling and stopping rule

The campaign runs **once** over the frozen arms and cells. No arm, cell,
metric, margin or test is added or altered after the first result is seen.

- **LF-H1 PASSES** — `RESULTS_MASTER.md`'s ablation row is restated with the
  honest number, and the +2884% figure is **withdrawn** as a measure of joint
  control, disclosed as a property of the published baseline's absorbing
  state. The published record is not edited; it is superseded.
- **LF-H1 FAILS** — the latch was not the cause; the ablation delta stands
  and the mechanism claim in this prereg is wrong, reported as such.
- **LF-H2 PASSES** — the joint-control advantage over a cache-everything
  posture survives a competent comparator, and is restated at the new,
  smaller margin. It does **not** restore −94.7%.
- **LF-H2 FAILS** — jcac does not beat a competently-tiered GPTCache posture
  on J. That is the finding, it is the headline of the record, and the
  gptcache comparison is **withdrawn** from the contribution list exactly as
  `PREREG_BUDGET_PARITY` pre-committed for its own cost claim. No arm is
  retuned in response.

`reproduce.py` must re-derive every previously committed record
byte-identically after this work lands (R4). The new record joins the gate.
