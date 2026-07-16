# Pre-registration: live chaos + p99 on the kind cluster (Wave 3, closes DEFENSE_QA #19 chaos-live half and #11 p99)

Registered 2026-07-16 (session 20). Committed and pushed **before** the run;
push event is the timestamp anchor; OSF mirror prospective. **Execution
deferred to a live Codespace session.** The simulated chaos campaign
(PREREG_CHAOS_SIM.md, executed session 20) established fallback behavior in
the model; this closes the live half DEFENSE_QA #19 names ("no live chaos
campaign — planner crash mid-burst, apiserver throttling — has run") and the
p99 question of DEFENSE_QA #11 in one live sitting on the same cluster.

## Substrate (frozen)

- The Phase 7 kind cluster and the jcac live arm exactly as run in session
  19 (operator installed, actuation gate, `phase7_jcac_smoke.yaml` cell
  geometry), so this campaign reuses a proven-good live harness and adds
  only the fault injections and the p99 export.
- Load: the `crud_bursty` and `ai_cacheable` cells already validated live in
  session 19 (the two cells with a published ordinal reading), so the
  chaos deltas attach to a measured baseline.

## Part A — live chaos (frozen)

Two faults, injected by kubectl against the running cluster mid-run, jcac
arm:

1. **Planner crash mid-burst:** `kubectl delete pod` on the planner
   Deployment's pod at a pre-declared wall-time offset (T = 8 min into the
   ~22.5 min run, inside a burst window by the cell's known shape); the
   Deployment reschedules on its own. Measures the real last-known-good hold
   and recovery, the live analogue of `chaos_planner_outage`.
2. **apiserver throttling:** apply a restrictive `--max-requests-inflight`
   analog via a transient admission delay (documented kubectl/APF setting)
   for a 60 s window at T = 14 min; the operator's reconcile loop is starved
   exactly as the sim's frozen-planner models.

Metric: the same `eval-export` document, plus a per-10 s-step violation
series read from the control plane so the recovery curve is measurable.
No new hypothesis is pre-committed for live chaos beyond **LC-H1
(qualitative, pre-committed):** the cluster returns to `mean_violation`
within CHAOS_TOL = 0.05 of its pre-fault level within 5 minutes of each
fault clearing. Quantitative J deltas are reported as measured (n is small
by the nature of a live run — this is a demonstration that the designed
fallback works over the wire, not a powered comparison, and is scoped as
such in the thesis).

## Part B — p99 (frozen)

`control-plane eval-export` currently emits exact p95 over per-request
latencies (`evalexport.go`); extend it to emit p99 by the identical
order-statistic path (a one-field addition, `AIP99MS`/`CrudP99MS`, wired the
same way as p95 — implemented and unit-tested at the desk **before** the
live run so the live session only reads it). DEFENSE_QA #11's honest point
stands: the *sim* is a p95 estimator by construction (`P95_FACTOR`), so p99
is a **live-only** number and is reported only for the live cluster, never
back-fitted into the sim tables.

- **P99-H1 (descriptive, no pass/fail):** report live p95 and p99 side by
  side per family per arm for the two cells; state the p99/p95 ratio. The
  claim this supports is narrow and honest — "on the live cluster the SLO
  story is unchanged when read at p99" holds only if the p99 violation
  verdict matches the p95 verdict; if it does not, that divergence is the
  finding and goes into the thesis verbatim.

## Outcome handling and stopping rule

Results to `RESULTS_LIVE_CHAOS_P99.md` as measured. The p99 export code
lands and is unit-tested this session (desk half); the live half runs once
on the provisioned cluster, no widening, faults and offsets fixed by this
push. Codespace stopped after data pull (quota), session-19 precedent.
