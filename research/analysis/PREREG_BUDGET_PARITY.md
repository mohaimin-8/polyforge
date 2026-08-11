# Pre-registration: budget parity (V-series, adjudicates the third confound in the baseline comparison)

Registered 2026-08-12 (session 38). Committed and pushed **before** any
implementation exists, let alone any campaign run; the push event is the
timestamp anchor.

## Question (and provenance of the finding)

`RESULTS_TRACE_PARITY.md` (WP1) found, on BurstGPT, a cost advantage that
fails its own pre-registered test (TP-H1a) *and* a severity failure
(TP-H3a/b), and its regime split showed the two are one finding:
Spearman(cost diff, excess diff) = −0.826.

Session 38 established **why**, and it is not a tuning choice. Verified by
reading, file:line:

- `research/jcac_sim/controller.py:604` computes
  `budget_per_step = config.hourly_budget_usd * CONTROL_INTERVAL_S / 3600`.
- `research/jcac_sim/controller.py:647` — `if cost > budget_per_step:
  continue` — **rejects every over-budget candidate before the objective is
  evaluated.** It is a hard constraint, not a weighted term.
- `research/jcac_sim/baselines.py:97-105` (`HPAController.plan`) computes
  desired replicas from arrival rate, clamps the delta to ±2, and applies.
  **No budget term, no cost term, anywhere in the scaling path.** The same
  holds for every other baseline `plan()`; `hourly_budget_usd` appears in
  `baselines.py` only at :429, as a *fairness weight* inside VTC.

So the proposal is **the only arm subject to the budget constraint it was
built to honour.** Measured on BurstGPT window 57 (the worst-overshoot
window), the effect is not marginal:

| arm | cost | `mean_excess` | AI shed |
|---|---:|---:|---:|
| `jcac` | 11.93 | 4.8319 | 93.6% |
| `hpa_fair` | **574.14** | 0.1916 | 0.0% |

`hpa_fair` spends **48× more** because nothing stops it. jcac sheds to
`tier="none"` — its documented budget-safe response — because in a burst no
affordable candidate clears the SLO.

This also explains why raising the SLO weight cannot help, and that was
measured rather than argued: `PREREG_VIOLATION_PARITY`'s frozen β ladder is
**inert**, moving `mean_excess` from 4.8319 to 4.8314 across a 32× increase
in β (`f045e68`). A weight cannot buy what a filter forbids. That prereg
stays frozen with its premise falsified; its ladder is **not run**.

Two prior in-repo diagnoses of the same mechanism, never connected to the
baseline comparison: session 29's risk-MPC null ("the knob was fighting the
Budget CRD, not the demand") and `guarantee.py`'s module header ("what does
bind is the per-tenant budget against AI tier spend").

**The arms are therefore solving different problems.** jcac answers "best
SLO attainable within budget"; the reactive baselines answer "meet the SLO,
ignore money". A head-to-head cost and violation comparison between them
conflates a constraint with a policy. This experiment removes that
conflation.

**It does not re-score, re-run or edit WP1 or any published campaign**
(R1/R4). `RESULTS_TRACE_PARITY.md` and its FAIL verdicts stand exactly as
committed.

## Design (frozen)

One factor changes: **which arms the per-tenant budget filter applies to.**
Traces, windows, demand construction, seeds, tenants, cluster limits,
forecaster, cache/tier machinery, eviction accounting and the fair cache
posture are inherited unchanged from `PREREG_TRACE_PARITY.md`.

**Arms (frozen).** WP1's seven unchanged, plus three:

| arm | budget filter | purpose |
|---|---|---|
| `jcac`, `jcac_v2`, `hpa`, `keda`, `firm`, `hpa_fair`, `keda_fair` | as published | unchanged; they replicate |
| `hpa_budget` | **applied** (same `hourly_budget_usd`, same per-step rule) | what a reactive autoscaler under a real Budget CRD actually does — the like-for-like comparator |
| `keda_budget` | **applied** | as above, event-driven |
| `jcac_nobudget` | **lifted** | what the proposal does when allowed to spend like the baselines |

The two directions are frozen **in the same prereg on purpose**: they
bracket the truth. Capping the comparators asks "is jcac cheaper than a
reactive controller that must also respect the budget?"; lifting jcac's cap
asks "is jcac's overshoot an artefact of the constraint?". Either alone
invites the objection that the other direction was the fair one.

Both mechanisms default OFF, so every published arm replays bit-identically
(R4). The budget rule applied to a baseline is **the controller's own**,
verbatim — the same `hourly_budget_usd * CONTROL_INTERVAL_S / 3600` per-step
cap, rejecting any move whose projected billed cost exceeds it and holding
the previous state when none qualifies. It is not a re-implementation with
different semantics.

**Traces (frozen).** Both, scored separately, never pooled: BurstGPT
(96 × 6 h, seed base 2000), Azure (72 × 3 h, seed base 3000).

**Metrics (frozen).** WP1's set exactly, plus `total_tier_cost_usd`.

## Hypotheses (frozen)

Scored **per trace**, Holm family of **four** per trace
(`stats.holm_bonferroni`, α = 0.05), one-sided paired Wilcoxon over windows
as in WP1, with the paired t reported alongside.

**BP-H1a (primary).** Against `hpa_budget` — a comparator under the same
budget constraint — jcac's `total_cost_usd` is lower. *Directional
prediction: unknown. This is the like-for-like cost comparison the thesis
has never actually run.*

**BP-H1b (primary).** As BP-H1a against `keda_budget`.

**BP-H2a (severity, the artefact test).** `jcac_nobudget`'s `mean_excess` is
non-inferior to `hpa_fair`'s at the 0.05 margin inherited from
`PREREG_EVICTION_PARITY`. *If this PASSES, WP1's severity FAIL is an
artefact of the constraint asymmetry rather than a control deficiency.*

**BP-H2b.** As BP-H2a against `keda_fair`.

**BP-H3 (descriptive, no test).** Decomposition: how much of WP1's cost gap
and how much of its severity gap each direction accounts for, reported per
trace with the shed rate (`tier_none_step_share`) for every arm.

## Outcome handling and stopping rule

The campaign runs **once**, over the frozen arms, traces and windows. No
arm, window, metric, margin or test is added or altered after the first
result is seen.

**Every outcome is publishable and the response to each is fixed now:**

- **BP-H1 FAILS** — jcac is not cheaper than a budget-respecting reactive
  controller. The cost contribution does not survive a like-for-like
  comparison and is **withdrawn** in the document that reports it, exactly
  as `PREREG_VIOLATION_PARITY` pre-committed. The paper's claim narrows to
  the joint-control mechanism, fairness, forecasting, security and the
  validity methodology.
- **BP-H1 PASSES** — the cost claim is restated as *cheaper than a reactive
  controller under the same budget constraint*, which is narrower and more
  defensible than the withdrawn −70%/−42%. It does **not** restore those
  headlines.
- **BP-H2 PASSES** — WP1's severity FAIL is reported as constraint-induced,
  and the paper must then state plainly that the constrained arm's low cost
  and its high overshoot are the same fact, never quoting one without the
  other.
- **BP-H2 FAILS** — the overshoot is a genuine control deficiency
  independent of budget, and is reported as the sharpest open weakness.

`reproduce.py` must re-derive every previously committed record
byte-identically after this work lands (R4). The new record joins the gate.

---

## Amendment 1 (2026-08-12, before any hypothesis was scored)

Disclosed under the project's amendment rule. **No campaign had been run and
no hypothesis scored**; both changes below come from the pre-scoring
mechanism check this prereg's design requires.

**1. The baseline budget rule as first frozen did not enforce a budget.**
It said "hold the previous state when the move is unaffordable". A baseline
proposes exactly one move, so holding stranded it at whatever expensive
state it had ratcheted to while demand was still cheap: `hpa_budget` came out
**2.5% under `hpa_fair`**, i.e. not constrained. The controller cannot get
stuck that way — it selects the best *affordable* candidate from a set and so
can step down. **Corrected rule:** take the inner baseline's move when
affordable; otherwise the affordable state closest to it within the
baselines' own ±2 clamp; otherwise the cheapest reachable state.

**2. The corrected rule changed almost nothing, and *that* is the finding.**
`hpa_budget` still costs 558.01 against `hpa_fair`'s 574.14. The cause is
structural, not a wrapper defect. At the per-tenant cap of **$0.013889 per
step**, measured on a standard tenant under 40 rps of chat:

| tier | replicas | tier spend | infra spend | total | affordable? |
|---|---:|---:|---:|---:|---|
| `none` | 1 | $0.000000 | $0.000140 | $0.000140 | **yes** |
| `none` | 10 | $0.000000 | $0.001340 | $0.001340 | **yes** |
| `small` | 1 | $0.030933 | $0.000140 | $0.031074 | no (2.2×) |
| `mid` | 1 | $0.309333 | $0.000140 | $0.309474 | no (22×) |
| `large` | 1 | $3.093333 | $0.000140 | $3.093474 | no (223×) |

Infra spend spans $0.00014–$0.00134 across the entire replica range; tier
spend spans $0–$3.09. **Tier dominates cost by three orders of magnitude, so
the replica knob cannot move affordability at all.** Under AI load the only
configuration inside the budget is `tier="none"` — shedding.

**Consequence for BP-H1, stated before scoring.** A replica-only reactive
controller **cannot satisfy the per-tenant budget under AI load by any
scaling decision available to it.** BP-H1 is therefore not a cost
comparison; it is a **feasibility** result, and it is reported as one: the
budget constraint is satisfiable only by a controller holding the tier knob.
The cost figures for `hpa_budget`/`keda_budget` are still reported, with the
disclosure that those arms are over budget in essentially every AI-loaded
step and so are *not* budget-respecting comparators — no such comparator
exists in the replica-only class.

This also completes WP1's explanation. jcac is not "efficient" on BurstGPT;
it is the only arm that *can* meet the constraint, the only way to meet it is
to shed AI, and shedding is what produces the overshoot that failed TP-H3.
The cost advantage and the severity failure are the same mechanism, and that
mechanism is the tier knob's price, not control quality.

BP-H2 is unaffected by this amendment and stays as frozen.
