# Pre-registration: what the composite-objective results rest on (JN; 2026-10-16)

Registered 2026-10-16. Committed and pushed **before** any run of
`eval/experiments/matrix_jointness.yaml`; the push event is the timestamp
anchor, checked by `scripts/check_prereg_timing.py`. Scorer:
`analysis_jointness.py`, committed with this file, tested on synthetic
matrices (`test_jointness.py`), not edited after the run. It imports its test
from the frozen `analysis_fair_j.py`, so this campaign is judged by exactly the
rule `PREREG_FAIR_J` froze.

## Why this exists

After the audit, the surviving composite-objective (J) claims were: a tie with
fair and re-tuned HPA (FJ-H1, RT-H1), and a 53–60% win over the layered stack
of independent per-knob controllers (FJ-H3, RT-H3), read as evidence that the
three knobs must be controlled jointly. A final review (2026-10-16) found four
things that claim and the tie had not separated:

1. **Coordination.** No arm ever removed coordination between one tenant's
   knobs while keeping everything else — objective, forecaster, plant model,
   budget filter, cluster caps, move clamps. The layered stack differs from
   the treatment in all of those at once.
2. **The comparators.** The layered stack's tier layer ranks tiers by latency
   and never by price; the reactive comparators ignore the cluster replica
   and cache caps the treatment obeys (re-tuned HPA was over the small
   cluster's replica cap on every step of a probe); and no "best static"
   lower bound was ever run in the post-audit matrices.
3. **Shedding.** Only the treatment can shed AI to tier `none` (7–18% of
   tenant-steps on AI workloads); every comparator serves.
4. **The planned term.** The treatment plans on unbounded `log1p(excess)`;
   J scores the capped `violation`.

## What was already seen (disclosed)

This protocol is not blind. Before it was written:

- **Records:** FAIR_J, RETUNED, SENSITIVITY_FAIR (all results known).
- **Exploratory probes by review agents, on the RETUNED campaign's seeds**
  (60 design points x 5 reps), with first-draft code:
  - a layered stack with the tier pinned to `small`, replica layer at the
    RT-H3 comparator's target: ΔJ (treatment − it) +0.0058, CI [−0.019,
    +0.033];
  - a cost-aware layered stack (cheapest tier meeting the SLO): −0.244;
  - a static equal-share, cheapest-tier configuration: −0.022, CI [−0.041,
    −0.004];
  - re-tuned HPA with the caps enforced but NOT re-tuned under them: −0.0105,
    CI [−0.029, +0.007];
  - the first-draft decoupled arm: −0.063 — later shown to be mostly an
    artefact of shedding whenever one knob's 1-D set was empty, the defect
    the registered arm fixes.
- **A controller-review probe** (8 stress cells x 60 steps, one seed): the
  repaired decoupled arm within about 1.6% J of the joint arm; the tier
  floor with the clamped fallback shed 0%.
- **A simulator-review probe** (36 AI design points x 5 reps, RETUNED seeds,
  first-draft tier floor without the clamped fallback): it still shed 9.2%
  and was worse by ΔJ +0.157.
- **A smoke run** of every registered arm, 2 cells x 40 steps, one seed.
- **The re-tuning** of the four new comparators (below) on `tune.py`'s slice,
  whose seeds are disjoint from every campaign.

No run of any arm on this campaign's seeds exists.

## Design (frozen)

`eval/experiments/matrix_jointness.yaml`:
- simulator backend, published economy and model forms;
- 120 steps, 5 reps, `shared_seeds: true`;
- a new experiment name, so all seeds are fresh;
- the headline cells: workloads `crud_bursty`, `crud_steady`, `ai_cacheable`,
  `ai_uncacheable`, `agentic`; mixes `uniform`, `premium_heavy`,
  `besteffort_heavy`, `whale`; cluster sizes `small`, `medium`, `large`.

That is 60 design points x 5 reps x 12 arms = 3,600 runs.

| arm | role |
|---|---|
| `jcac_converged` | **treatment** (as in PREREG_FAIR_J / PREREG_RETUNED) |
| `jcac_decoupled` | JN-C1: identical, but each knob best-responded on its own 1-D set at the anchored incumbent (an infeasible set holds its knob), the winners combined, and a move/hold arbiter repairing a combination that breaks the budget or a cap |
| `jcac_knobwise` | JN-C2: identical, but one knob at a time to a fixed point — the strongest search that never scores a two-knob move |
| `layered_small_tuned` | JN-B1: the layered stack with no tier layer (cheapest tier always) |
| `layered_costaware_tuned` | JN-B2: the layered stack with a cost-aware tier layer |
| `static_share` | JN-B3: equal cluster share per tenant, cheapest tier, never adapts |
| `hpa_fair_capped` | JN-B4: `hpa_fair` inside the cluster caps (`baselines.CapEnforced`) |
| `keda_fair_capped` | JN-B5: `keda_fair` inside the cluster caps |
| `jcac_tier_floor` | descriptive: the treatment that never sheds AI (tier floor `small` + clamped fallback) |
| `jcac_violation` | descriptive: the treatment planning on capped `violation` |
| `jcac_nojoint_v2_retuned` | descriptive: RT-H3's comparator, replicated on fresh seeds |
| `hpa_fair_retuned` | descriptive: RT-H1's comparator, replicated; ignores the caps |

**Re-tuning (frozen; committed with this file).** `eval/baselines/tune_arms.py
--arms` tuned the four new comparator arms AS RUN, per cluster size, on
`tune.py`'s slice, seeds and objective, merging into `tuned_arms.yaml` without
touching its three PREREG_RETUNED entries. Grids `RHO_WIDE` / `RPS_WIDE`
(the cost-aware stack's optimum sat on the old 0.8 edge); no optimum is on a
grid edge:

| arm | small | medium | large |
|---|---|---|---|
| `layered_costaware_tuned` | ρ = 0.8 | ρ = 0.8 | ρ = 0.8 |
| `layered_small_tuned` | ρ = 0.05 | ρ = 0.075 | ρ = 0.1 |
| `hpa_fair_capped` | ρ = 0.1 | ρ = 0.05 | ρ = 0.075 |
| `keda_fair_capped` | 0.5 rps | 0.25 rps | 0.15 rps |

`static_share` has no parameter. The treatment and its variants are not
tuned: their weights are the objective.

**Guard metrics (reported for every arm, never tested):** AI-shed share;
tenant-steps over budget; nominal moves outside the ±2-replica /
one-cache-level clamps; for the JCAC arms, plans from the infeasible-lattice
fallback, plans the decoupled arbiter repaired, and cycles that hit the sweep
cap.

## Tests (frozen)

The `PREREG_FAIR_J` rule, imported from its scorer:
- The design point is the unit: reps are averaged, and a design point is
  dropped listwise if any arm lacks a rep there.
- A win needs BOTH of these:
  - the one-sided Wilcoxon p is at or below its Holm threshold;
  - the 95% bootstrap CI of the mean J difference (10,000 design-point
    resamples, seed 20260926) lies entirely below 0.

- **Confirmatory, JN-C1, JN-C2, JN-B1..B5:** `jcac_converged` against each
  arm in the table. One Holm family of 7, α = 0.05.
- **Coordination equivalence (registered reading, JN-C1-EQ, JN-C2-EQ):** the
  search arms are called EQUIVALENT to the treatment when the 95% bootstrap CI
  of the mean ΔJ lies strictly inside ±0.025. The margin is the smallest
  difference this design can detect (about 0.025–0.030 J at n = 60, power
  0.8), about 6% of the treatment's J in RETUNED; two one-sided tests at 2.5%
  each.
- **SLO guard:** per confirmatory comparator, `mean_excess` non-inferiority
  of the treatment (one-sided 95% bootstrap upper bound < 0.05).
- **Direction (a registered reading, not a test):** a comparison whose CI lies
  entirely above 0 is reported as "comparator better". A FAIL is never called
  a loss unless that holds.
- **Descriptive:** per-arm means and guard metrics; per-metric differences
  (cost, violation, excess, Jain) with CIs; never-shedding and planned-term
  contrasts; RT-H1 and RT-H3 replicated on fresh seeds; what the caps cost
  HPA; per cluster size; per workload; the AI workloads alone.

## Predictions

Informed by the probes above.

- **JN-C1, JN-C2 (coordination): FAIL, and EQUIVALENT.** The repaired
  decoupled arm and the knob-wise search land within the margin of the joint
  search.
- **JN-B1 (no tier layer): FAIL**, no difference shown.
- **JN-B2 (cost-aware tier layer): PASS.** Its tuned J (~0.70 on the slice)
  is far above the treatment's.
- **JN-B3 (static share): PASS**, with a small margin, low confidence.
- **JN-B4, JN-B5 (capped reactive): FAIL**, a tie overall; "treatment
  better" on `large`.
- **Descriptive:** never shedding costs the treatment J; RT-H3 replicates;
  RT-H1 replicates as a tie.

## What each outcome means (committed before the run)

- **JN-C1/JN-C2 FAIL and EQUIVALENT:** coordination between one tenant's
  knobs buys nothing measurable under J on this plant. The layered-stack win
  is then NOT evidence that the knobs must be controlled jointly; it is the
  sum of the other differences (objective, forecaster, model, budget, caps,
  shedding, the comparator's tier rule), and no jointness claim is made.
- **JN-C1/JN-C2 PASS:** coordination between knobs adds value at the design
  point unit, and the size is quoted with its CI.
- **Neither PASS nor EQUIVALENT:** coordination is unresolved at this power;
  no claim either way.
- **JN-B1 FAIL:** a layered stack with a price-aware (here: absent) tier
  layer matches the treatment, so the RT-H3 margin measured the registered
  stack's tier rule. **JN-B1 PASS:** the margin survives a competent tier
  layer.
- **JN-B3 FAIL:** control does not beat a static equal-share configuration
  on this matrix; no claim of a value-of-control under J is made. **PASS:**
  the margin over static is quoted with its CI.
- **JN-B4/JN-B5:** with the caps equal, whether the treatment beats reactive
  replica control. A FAIL keeps "a tie with reactive control" as the claim.
- **The per-size split** decides where any claim may be scoped; a claim that
  holds only on some sizes is stated for those sizes only.

## Stopping rule

Same as `PREREG_FAIR_J`:
- one sitting;
- no re-run, widening, re-tuning, or change of arms after the first run;
- the harness's two transient-error retries are allowed;
- a run still failing is recorded, and its design point is dropped for every
  arm;
- a scorer defect found after the run is fixed in a new scorer and a new
  record.

`tuned_arms.yaml` is not regenerated after the first run.
