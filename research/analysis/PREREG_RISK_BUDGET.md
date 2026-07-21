# Pre-registration — budget-corrected risk MPC (the RESULTS_RISK follow-up)

Registered 2026-07-22 (session 29). Committed and pushed **before any run**;
the push is the timestamp anchor, OSF mirror prospective.

## §0 Lineage and discipline

`RESULTS_RISK.md` published both gates of the risk-aware quantile mechanism as
**FAIL**, with a diagnosis: the quantile inflation was applied to the whole
projection, so it also inflated *projected tier spend*, tripped the per-tenant
budget filter, and drove the controller into its shed fallback
(`tier="none"`) — cheap, and a total SLO miss. The knob was fighting the
Budget CRD, not the demand. That null stands as measured and is not re-run.

This campaign is the disciplined follow-up in the
`PREREG_PLANNER_CELLS_DEALIAS` tradition: a **new pre-registration with
exactly one changed factor** — capacity is sized against the risk-inflated
demand, but **cost is projected and the budget checked against the point
forecast**, because a tenant is billed for the demand that *arrives*, not the
demand it was provisioned against. Nothing else changes: same quantile grid,
same matrix shape, same baselines, same declared operating point q = 0.90.

## §1 The mechanism (frozen; implemented before this push)

`JCACController(risk_quantile=q, risk_cost_at_point=True)`: the candidate
search projects the SLO/violation term on the quantile-inflated horizon and
the cost term (including the `cost > budget_per_step` guardrail and the
reported `projected_cost_usd`) on the point-forecast horizon.
`risk_cost_at_point` defaults **False**, so the published null arms
(`jcac_q70..q95`) and every earlier campaign replay bit-identically —
verified before this push: base-controller cells (`matrix_learned`,
`jcac_anchored`) and null cells (`matrix_risk`, `jcac_q90`/`jcac_q95`) all
replay at **max drift 0.00e+00**. The flag without a quantile is inert
(unit-tested).

Probe evidence motivating the follow-up (exploratory, fixes no number): on the
diagnosis cell (`ai_cacheable/uniform/medium`, seed 4242) the correction
removes the shed entirely (0 events vs 2/4 uncorrected) at +0.7% cost; on a
violating cell (`agentic/uniform`, seed 7777) violation falls 0.2215 → 0.2096
→ 0.2051 across q ∈ {point, 0.90, 0.95} with cost rising modestly — the
frontier finally runs in the designed direction.

Unit tests (`test_jcac.py::RiskBudgetCorrectionTests`): default flag off (the
null path), corrected never sheds more than point-forecast (the defect class,
by construction), corrected provisions at least as much, flag-without-quantile
inert. Suite 85/85 green.

## §2 Matrix (frozen)

`eval/experiments/matrix_risk_budget.yaml`: sim backend, 120 steps, **systems
[jcac_anchored, jcac_q70c, jcac_q80c, jcac_q90c, jcac_q95c, hpa, keda,
concurrency]** × 5 workload classes × 4 tenant mixes × 3 cluster sizes × 5
reps = **2,400 runs**, standard validation. Blocked-factorial pairing on
(workload, tenant_mix, cluster_size, rep) — 300 matched cells per arm. The
quantile grid and the operating point are inherited frozen from
PREREG_RISK_MPC; nothing is re-tuned.

## §3 Hypotheses

- **RB-H1 (confirmatory — the corrected knob works).** At the pre-declared
  q = 0.90, `jcac_q90c` attains **strictly lower violation than
  `jcac_anchored`** (paired t over 300 matched cells, p < 0.01, mean
  diff < 0). This is the exact reading the uncorrected mechanism failed with
  the sign reversed; the correction must deliver it or the mechanism is dead.
- **RB-H2 (confirmatory — a real frontier).** Across
  `[jcac_anchored, q70c, q80c, q90c, q95c]`, mean violation is monotone
  **non-increasing** and mean cost monotone **non-decreasing**
  (Spearman ρ ≤ −0.9 and ρ ≥ +0.9 respectively) — the RQ-H1 reading, retried
  under the one changed factor.
- **RB-H3 (confirmatory — strict Pareto domination of the reactive stack).**
  At q = 0.90, `jcac_q90c` has **both** strictly lower violation **and**
  strictly lower cost than **each** of tuned `hpa`, `keda`, and `concurrency`
  (six paired tests, each p < 0.01, mean diff < 0; all six conjuncts must
  hold). This is the strong claim: one controller, less violation *and* less
  money than the entire tuned reactive stack, simultaneously — the reading
  neither the base controller (violation parity, v2) nor any single-knob
  system has ever produced.
- **RB-D1 (descriptive, no gate).** The corrected frontier with 95% bootstrap
  CIs next to the published null frontier (same axes, fig19); per-class
  breakdown; which baseline operating points are strictly dominated and by
  which arms; ΔJ per arm vs `jcac_anchored`.

**Falsifiers, pre-committed.** If RB-H1 fails, the mechanism is dead even
without the budget confound and is published as a second null — the idea
retires. If RB-H2 fails, the knob is not a controllable frontier and no
frontier claim is made. If RB-H3 fails on any conjunct, the strict-domination
claim is **not** made and the campaign reports which conjunct broke and at
what margin; partial dominance is reported as partial, never rounded up.
No gate may be traded for another post hoc.

## §4 Stopping rule

One matrix execution (crash retries only, retries: 2), one analysis pass
(`analysis_risk_budget.py` → `RESULTS_RISK_BUDGET.md`). Grid, operating
point, and correction frozen here; no quantile widening, no second correction,
no re-tuning of baselines, no re-run of the published null. If this campaign
fails, the risk mechanism line ends with two published nulls and the base
controller's numbers stand unchanged everywhere.
