# Pre-registration: cost at violation parity on real demand (V-series, tests the claim WP1 never tested)

Registered 2026-08-12 (session 38). Committed and pushed **before** any
implementation exists, let alone any campaign run; the push event is the
timestamp anchor.

## Question (and provenance of the finding)

`RESULTS_TRACE_PARITY.md` (WP1, session 37) scored jcac against fairly
configured reactive comparators on two real traces and found, on BurstGPT,
a cost advantage that **fails its own pre-registered Wilcoxon test**
(TP-H1a p=0.0133 vs Holm 0.01250) together with a severity failure
(`mean_excess` 0.5134 vs `hpa_fair`'s 0.008818, TP-H3a/b FAIL).

The record's own regime split shows these are one finding, not two:
Spearman(cost diff, excess diff) = **−0.826**; in the 41 windows where
jcac is cheaper the baseline spends **12.9×** more and **98%** breach the
0.05 severity margin, while in the 55 windows where jcac is dearer the
excess difference is +0.0004 and **0%** breach it.

**WP1 therefore compared two arms sitting at very different points on the
cost/SLO frontier** — `hpa_fair` at 0.0088 mean excess, jcac at 0.5134, a
58× gap — and read off a cost difference. The thesis's actual claim is
*cost **at violation parity***, which that comparison does not test.

This is not a defect in the controller. `controller.py:551` already
optimises `log1p(excess)` — unbounded by deliberate choice, documented in
its own docstring — so the controller sees deep overload. It is the
**weighting**: the objective is
`α·cost/COST_SCALE + β·log1p(excess) + γ·fairness` with α=1.0, **β=2.0**,
so cost enters linearly and overshoot logarithmically. Deep in a burst the
marginal objective value of removing overshoot falls below the marginal
cost of the replicas that would remove it, and the controller correctly
banks the money. That is a point on a frontier, chosen by β.

This experiment asks the question WP1 left open: **swept to a weighting
that matches the fair comparator's violation, is jcac still cheaper?**

**It does not re-score, re-run or edit WP1 or any published campaign**
(R1/R4). `RESULTS_TRACE_PARITY.md` and its FAIL verdicts stand exactly as
committed; this is a new question with its own arms and its own record.

## Design (frozen)

One factor changes: **β, the SLO weight in the controller's objective.**
Traces, windows, demand construction, seeds, tenants, cluster limits,
forecaster, cache and tier machinery, eviction accounting and the fair
comparators are all inherited unchanged from `PREREG_TRACE_PARITY.md`.

**Arms (frozen).** The WP1 arms unchanged, plus a β sweep on jcac:

| arm | β | purpose |
|---|---|---|
| `jcac` | 2.0 (published) | the published operating point, unchanged |
| `jcac_b4` / `jcac_b8` / `jcac_b16` / `jcac_b32` / `jcac_b64` | 4 / 8 / 16 / 32 / 64 | the sweep — one factor, geometric ladder fixed now |
| `hpa_fair` / `keda_fair` | — | the fair comparators, unchanged from WP1 |

The ladder is geometric and fixed in advance. **No β outside this set is
run, and the set is not extended after seeing a result.** β enters through
`Weights(beta=…)`, which already exists; the only wiring needed is that
`SystemSpec` can carry it (today `trace_matrix.run_one` forwards `gamma`
only). That field defaults to `None`, so every published arm replays
bit-identically (R4).

**Traces (frozen).** Both, scored separately, never pooled: BurstGPT
(96 × 6 h, seed base 2000) and Azure (72 × 3 h, seed base 3000), exactly
the protocols WP1 used.

**Parity rule (frozen, and this is the crux).** For each trace and each
comparator, the **matched arm** is the *lowest*-β arm in the ladder whose
mean `mean_excess` over windows is **≤ the comparator's**. Lowest-β rather
than nearest, because β only buys attainment with money: taking the first
arm that clears the bar is the most conservative choice available and
cannot be gamed by picking the ladder rung that flatters cost. If **no**
arm in the ladder reaches the comparator's excess, the parity comparison
is **not available** on that trace and is reported as such — not
substituted with the closest one.

**Metrics (frozen).** WP1's set exactly: `total_cost_usd`,
`mean_violation`, `mean_excess`, `tier_none_step_share`, `mean_jain`,
`cache_hit_rate`, `total_tier_cost_usd`, `J`.

## Hypotheses (frozen)

Scored **per trace**, Holm family of **four** per trace
(`stats.holm_bonferroni`, α = 0.05). One-sided paired Wilcoxon over
windows, matching WP1's gate; the paired t is reported alongside, as there.

**VP-H1a (primary).** At violation parity, the matched jcac arm's
`total_cost_usd` is below `hpa_fair`'s. *Directional prediction: unknown.
This is the experiment. If it fails, the honest conclusion is that the
published cost advantage was an artefact of comparing unmatched operating
points, and the thesis's cost claim does not survive on that trace.*

**VP-H1b (primary).** As VP-H1a against `keda_fair`.

**VP-H2a (severity actually matched).** The matched arm's `mean_excess` is
non-inferior to `hpa_fair`'s at the 0.05 margin inherited from
`PREREG_EVICTION_PARITY` — i.e. the parity the design claims to establish
is real and not an artefact of averaging.

**VP-H2b.** As VP-H2a against `keda_fair`.

**VP-H3 (descriptive, no test).** The price of attainment: cost and
`mean_excess` for every rung, reported as a frontier per trace, with the
β needed to reach parity stated. This is the record's most quotable
content whichever way VP-H1 lands.

## Outcome handling and stopping rule

The campaign runs **once**, over the frozen ladder, arms, traces and
windows. No β, arm, window, metric, margin, parity rule or test is added or
altered after the first result is seen.

**A FAIL of VP-H1 is a publishable outcome and is reported as the headline
finding of the record.** Pre-committed response, fixed now: if jcac is not
cheaper at matched violation on either trace, the cost contribution is
withdrawn in the same document that reports it, and the paper's claim
narrows to the joint-control mechanism, the fairness and forecasting
segments, the security result, and the methodology — with the cost frontier
reported as a characterisation rather than a win. **The published records
are not edited; they are superseded in the open.**

Symmetrically, a PASS does **not** restore the −70% / −42% headlines. Those
were measured against unfairly configured comparators and remain withdrawn
by `RESULTS_TRACE_PARITY.md`. The most a PASS can establish is *cost
advantage at matched severity against a fair comparator*, at whatever
magnitude it lands.

`reproduce.py` must re-derive every previously committed record
byte-identically after this work lands (R4). The new record joins the gate.
