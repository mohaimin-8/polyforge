# Pre-registration — v3 overload matrix (SLO segment, second attempt in a new regime)

Dated 2026-07-11 (session 14). Committed **before** any `matrix_v3_overload` run
executes, per V2_README ground rule 1. This document freezes the systems, the cell
definitions (with the arithmetic they were derived from), the hypotheses, the analysis,
and the stopping rule. The v2 H1 FAIL (`RESULTS_V2.md`) stands verbatim and is not
being re-litigated: v2's stopping rule forbade re-running or re-tuning `jcac_v2` on the
v2 matrix, and nothing here does either. v3 asks a *different, pre-stated* question in
a *new demand regime* with an *already-committed, untouched* system.

## §1 Motivation — why the v2 SLO null was structural, and what regime is untested

Every controller in the harness — PolyForge included — moves replicas through the same
±2-per-interval clamp (`DELTA_REPLICAS` in `controller.py`, the ±2 clamp in
`baselines.py`; `apply_action` bounds are shared). In the v1/v2 matrix, per-tenant
demand never needs more than ~2–3 replicas (e.g. `crud_bursty` peaks at
2.5 × 56 = 140 wu/s ⇒ 2 replicas at ρ ≤ 0.85): **every burst onset is reachable within
one actuation interval**, so a controller that merely reacts within one interval ties
on SLO, and the only degree of freedom left is cost — which PolyForge already wins
(RESULTS.md, RESULTS_V2.md). That is the mechanical reading of the v2 H1 null:
"parity at −43…−48% cost" is what joint control looks like in a regime where
reaction time never binds.

The untested regime — named as the SLO lever in V2_README's target scoreboard
("+ overload cells") but never implemented (`grep -ri overload eval/` is empty before
this commit) — is demand whose burst-onset replica climb **exceeds** the clamp's
one-interval reach. There, a reactive policy is under-provisioned for ≥ 2 scored
intervals *per burst*, every cycle, by construction; only a controller that provisions
*before* the onset avoids it. PolyForge has carried exactly that capability, committed
and untuned, since session 11: `jcac_seasonal` (`forecast_method: "seasonal"`, online
period detection with trend fallback below autocorrelation 0.4, scan range lags 8–48).
The Phase 3a trace ablation (FORECAST_TRACE.md) already showed the mechanism on real
periodicity (−44.8% one-step RMSE vs trend). v3 measures whether that forecasting
advantage converts into an *SLO segment win at equal-or-lower cost* under the shared
actuation clamp.

## §2 Systems (frozen; nothing new, nothing tuned)

| system | spec | status |
|---|---|---|
| `jcac_seasonal` | **the pre-registered treatment**, named here in advance | committed session 11 (`eval/harness/systems.py`), parameters untouched |
| `jcac` | v1 headline (trend forecast) — the mechanism control | immutable v1 system |
| `hpa` | tuned `target_rho=0.3` (TUNING.md) | unchanged |
| `keda` | tuned `rps_per_replica=2.0` (TUNING.md) | unchanged |
| `firm` | tuned lr=0.1, ε=0.1, w_slo=4 (TUNING.md) | unchanged |

No parameter of any system is modified for v3. Baseline tuning is **not** redone: the
tuned values were selected on the composite objective over a tuning slice at the
SLO-generous end of the vendor envelope (TUNING.md), which remains their best
reasonable posture in an overload regime (more replicas, sooner). `static`/`gptcache`
are omitted: their v3 behavior is fully determined and already characterized (pin at
max / cache-max posture); the v2 iso-cost gate told that story, and 480 additional
runs would add no information about the SLO mechanism under test.

Engine semantics are identical to the v1/v2 headline: `transition_costs: false`,
`interference: false`. This is deliberately **conservative against the treatment** —
replica startup lag (the realism knob) punishes reactive scaling and would flatter
proactive control; we leave it off so any win is attributable to forecasting alone
under the mildest assumptions.

## §3 Cell definitions and their derivations (frozen before any run)

Constants below were derived from the committed model constants
(`model.py`: REPLICA_CAPACITY_WU=100, congestion 1/(1−ρ) with saturation at ρ=0.95,
P95_FACTOR=1.4, SLO 150 ms CRUD / 2500 ms AI × class factor) — **not** from trial
runs. They are pinned by unit tests (`eval/tests/test_harness.py::TestV3OverloadCells`)
and MUST NOT be adjusted after any matrix or smoke outcome is observed.

**`flash_crud`** — crud_read 60 + crud_write 10 = 80 wu/s at scale 1; shape `flash`:
6.0× for 5 steps of every 16, else 0.5×.
Burst 480 wu/s ⇒ 6 replicas at ρ ≤ 0.85; trough 40 wu/s ⇒ 1. Onset climb ≥ 4-5
replicas > 2× the ±2 clamp ⇒ any reactive policy spends ≥ 2 scored intervals in
overload per cycle. No AI traffic: **no tier or cache confound** — against HPA this
class isolates pure reaction-time physics. Predicted side-finding: tuned KEDA
(2 rps/replica) degenerates to a max-pinned posture here (trough 35 rps already
demands 18 replicas), i.e. it ties SLO by permanent overspend — the honest
iso-cost story from v2, recurring.

**`flash_ai`** — chat 5 + embed 3 + crud_read 10 (raw 125 wu/s at scale 1); shape
`flash`. Burst ⇒ ~7–8 replicas at the default cache, ~6–7 at a grown cache; trough ⇒ 1.
The LLM-serving version of the same physics, plus the cache/tier knobs PolyForge
actually manages. KEDA's rps proxy under-counts token-heavy work; HPA tracks work but
reacts late.

**`spike_agentic`** — agent 1.5 + embed 1 + crud_read 5 (70 wu/s at scale 1); shape
`spike`: 8.0× for 2 steps of every 12, else 0.6×. Spike ⇒ ~7 replicas; a 2-step spike
is **over before a reactive climb arrives**, every cycle — the sharpest possible
statement of the regime. Disclosure, stated now: HPA/KEDA serve agent traffic on their
fixed small tier (6000 ms base ⇒ 8400 ms at P95_FACTOR vs 6250 ms standard target),
so part of their violation on this class is the fixed-tier posture, not reaction time.
That is their real shipped behavior and counts for the segment claim (H1′), but the
forecast mechanism claim is carried by H2′, where both arms have all three knobs.

**`ramp_gentle`** — control cell: identical base and 0.5×–6.0× envelope as
`flash_crud`, shape `slowwave` (period 40 sinusoid). Steepest per-step demand change
≈ 35 wu ≈ 0.4 replicas ≪ the clamp ⇒ reactive controllers track it. **Predicted
outcome: statistical tie.** This cell exists so the claim has the reviewer-proof
shape "wins exactly where actuation binds, ties where it does not."

Matrix: 5 systems × 4 classes × 4 mixes × 3 sizes × 5 reps = **1,200 runs**, steps=120,
same blocked-factorial, pairing, seeds-from-run-id machinery as v1/v2
(`eval/experiments/matrix_v3_overload.yaml`, output `eval/results/raw_sim_v3.duckdb`).

Known dilutions, accepted and stated now rather than discovered later: (a) `whale`
mix at scale 4.0 pushes burst demand past every `replica_max` — those cells saturate
for all systems and pair out as ties; (b) `premium` tenants on `small` clusters
need more replicas than `replica_max=6` allows at burst — infeasible for everyone;
(c) `jcac_seasonal` runs its trend fallback until the detector locks (≈ 2 periods),
so run-mean violation *underestimates* the steady-state benefit. All three dilute the
measured effect **against** the treatment; none is excluded from the confirmatory
pooled test.

## §4 Hypotheses (confirmatory), declared before any run

**H1′ (the SLO segment):** pooled over all 240 matrix cells, paired by cell,
`jcac_seasonal` shows `mean_violation` significantly below **HPA** and below **KEDA**
(paired t, p < 0.01 each) **and** the paired `total_cost_usd` point estimate vs each
is ≤ 0. Identical bar to the v2 H1 that failed — a cost tie does not rescue a
violation loss and a violation win does not excuse spending more.

**H2′ (mechanism attribution):** paired by cell, `jcac_seasonal` shows
`mean_violation` significantly below `jcac` (p < 0.01), with **no** cost regression at
p < 0.01 and |d_z| ≥ 0.5. Both arms share every knob, weight, and guardrail; they
differ only in the forecaster — a PASS attributes the H1′ win to forecasting, not to
the joint knobs the baselines lack.

**Declared exploratory (E1–E4, no gating):** per-class breakdown of H1′/H2′ (E1;
prediction: wins concentrated in `flash_*`/`spike_*`, tie on `ramp_gentle`); KEDA
cost ratio on flash cells (E2; prediction: near-max pinning); violation *incidence*
alongside mean violation (E3); composite J vs all baselines (E4).

Failure is publishable: if H1′ or H2′ fails, RESULTS_V3.md reports it as measured and
the thesis SLO section keeps only the v2 iso-attainment framing ("parity attainment at
−43…−48% cost"). No third attempt inside this thesis.

## §5 Metrics and analysis

Metrics, cell keys, pairing, and thresholds identical to v1/v2 (`stats.py`: METRICS,
CELL_KEYS; paired-by-cell t-tests; Cohen's d_z; p < 0.01; |d_z| ≥ 0.5 for
"large-effect"). The analysis is executed by `research/analysis/analysis_v3.py`
(committed in the same pre-registration commit as this document, wired into
`run_analysis.py`) and writes `RESULTS_V3.md`. RESULTS.md and RESULTS_V2.md are
immutable; v3 numbers are reported alongside, never instead (ground rule 5). No
v1/v2/v3 table ever mixes substrates or matrices (ground rule 4).

## §6 Stopping rule

1. `smoke_v3.yaml` (10 runs, separate DB, disjoint experiment name) runs first and
   checks **mechanics only**: runs complete and are marked valid by the existing
   metric contract. Its metric values gate nothing, adjust nothing, and are deleted
   from consideration; if smoke exposes a *code defect* (crash, invalid marking), the
   defect is fixed and documented in RESULTS_V3.md — cell constants stay frozen
   regardless.
2. The 1,200-run matrix is expanded once and run once (runner retries are for crashed
   executions only, per its existing contract). No cell, constant, seed, or system
   may be added, removed, or altered after the first matrix run starts.
3. `analysis_v3.py` runs once when the matrix reports complete-and-valid.
   Both verdicts are published as measured.
4. Widening beyond this matrix (more reps, more classes) is permitted only as a
   *new* pre-registration in a new file; it cannot amend this one.
