# Pre-registration — risk-aware (quantile) MPC and the cost/violation frontier

Registered 2026-07-22 (session 29). Committed and pushed **before any run**;
the push is the timestamp anchor, OSF mirror prospective.

## §0 What this is, and what it is *not*

The published controller plans against a **point** demand forecast: every
candidate configuration is scored at the *expected* demand
(`Forecast.horizon()` → `JCACController._project`). A point forecast is right
on average, which means realized demand lands **above** plan roughly half the
time — the structural mechanism behind the project's disclosed
attainment-for-spend trade. Risk-aware MPC replaces the point forecast with a
**quantile** of the controller's own realized forecast errors: plan for the
demand you would exceed only (1−q) of the time.

**This does not reopen v2 H1 or v3 H1′.** Those are pre-registered *nulls for
the base controller* and they stand exactly as measured; the v3 stopping rule
that closed confirmatory SLO attempts for the base system is untouched. This
is a **new mechanism** under its own pre-registration, and its results are a
journal-track contribution, not a thesis retrofit. No number in any closed
campaign is revised by it.

The motivation is also a named open limitation of our own (session 23,
limitation (b)): *the win is conditional on operator SLO-tolerance*. Today
that tolerance is an implicit consequence of planning at the mean. This
campaign makes it an **explicit, tunable knob** — and sweeping it produces a
frontier rather than a single operating point.

## §1 The mechanism (frozen; implemented before this push)

`Forecast` records the one-step error of its own base forecaster each
interval (`residuals`, rolling window 48, per request kind; `_last_pred` is
always the *un-inflated* prediction so padding cannot feed itself).
`horizon(risk_quantile=q)` inflates each kind by the **empirical q-quantile**
of those residuals — distribution-free, no normality assumption — floored at
zero so the knob may only ever *add* headroom, never plan below the forecast.
Below `RESIDUAL_MIN` = 8 samples the quantile is noise and no inflation is
applied (cold start is the published behavior).

`JCACController(risk_quantile=...)` threads it through; **`None` is the
default and the published path is arithmetically unchanged.** Verified before
this push: 20 committed `jcac_anchored` cells from `matrix_learned` replay
**bit-identically, max drift 0.00e+00** over all six metrics. Every closed
campaign still reproduces.

Orthogonal to `adaptive_capacity`, which corrects *model optimism*
(realized-vs-projected capacity); this corrects *demand variance*. They
compose; neither substitutes for the other.

Unit-tested (`test_jcac.py::RiskAwareForecastTests`): residuals recorded from
forecast error, default horizon unchanged for `None`/`0`, inflation
non-negative and monotone in q, cold start does not inflate, residuals
survive a snapshot round-trip, and a risk-aware controller never provisions
*less* than the point-forecast controller from the same state.

## §2 Matrix (frozen)

`eval/experiments/matrix_risk.yaml`: sim backend, 120 steps, **systems
[jcac_anchored, jcac_q70, jcac_q80, jcac_q90, jcac_q95, hpa, keda,
concurrency]** × 5 workload classes × 4 tenant mixes × 3 cluster sizes × 5
reps = **2,400 runs**, standard validation (row counts, no dupes/NULLs).
`jcac_anchored` is the point-forecast end of the frontier and the quotable
controller; hpa/keda/concurrency are the tuned reactive arms (W34 grids
already committed; nothing is re-tuned here). Blocked-factorial pairing on
(workload, tenant_mix, cluster_size, rep) — 300 matched cells per arm.

The headline matrix is the substrate. The **overload (v3) regime is where the
trade binds hardest** and is named here as the declared follow-up; it is
deliberately *not* run in this campaign, and no overload claim may be made
from these cells.

## §3 Hypotheses

- **RQ-H1 (confirmatory — the knob is a real control).** Across the five
  frontier arms ordered `[jcac_anchored, q70, q80, q90, q95]`, mean violation
  is **monotone non-increasing** and mean cost **monotone non-decreasing**,
  with Spearman |ρ| ≥ 0.9 in each declared direction. If the knob does not
  move the system monotonically along the trade, it is noise and the
  mechanism fails — this gate exists to be failable.
- **RQ-H2 (confirmatory — dominance at the declared operating point).** At
  the **pre-declared q = 0.90**, `jcac_q90` attains **strictly lower violation
  than `jcac_anchored`** (paired t over 300 matched cells, p < 0.01, mean
  diff < 0) **while still costing strictly less than every tuned reactive arm**
  (`hpa`, `keda`, `concurrency`; each paired, p < 0.01, mean diff < 0). That
  is the substantive claim: we can buy attainment *and remain the cheap
  system*. Both conjuncts must hold.
- **RQ-D1 (descriptive, no gate).** The full cost/violation frontier: per-arm
  means with 95% bootstrap CIs, per-workload-class breakdown, and which
  baseline operating points are Pareto-dominated by the frontier. Reported
  with a figure (`fig_risk_frontier`).

**Falsifiers, pre-committed.** If RQ-H1 fails, the residual-quantile knob does
not steer the system and the mechanism is published as a **null** — reported
next to the wins, in the tradition of the γ-term. If RQ-H2 fails because cost
rises above a reactive arm before violation falls below `jcac_anchored`, then
attainment cannot be bought cheaply even with an explicit risk knob, and the
project's existing honest boundary (*attainment costs money*) is **confirmed
for our own controller** and published as such. Neither outcome is hidden and
neither revises a closed campaign.

## §4 Stopping rule

One matrix execution (crash retries only, retries: 2), one analysis pass
(`analysis_risk.py` → `RESULTS_RISK.md`). The quantile grid {0.70, 0.80,
0.90, 0.95} and the declared operating point q = 0.90 are frozen here: no
widening, no adding quantiles after seeing a frontier, no re-declaring the
operating point post hoc. Baseline tuning is inherited from W34 and is not
revisited. SLO confirmatory attempts for the **base** controller remain closed
(v3 rule); RQ-H1/H2 concern the new mechanism only.
