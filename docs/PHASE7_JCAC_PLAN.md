# Phase 7 remainder — the jcac live arm and the ordinal figure

Written 2026-07-13 (session 16e), after the hpa arm was verified live
(`ok: true`, physics-exact metrics — SESSION_LOG 16d). Updated 2026-07-13
(session 17): **the wiring below is done in code and tested locally**;
what remains is the live execution itself.

**STATUS 2026-07-16 (session 19): EXECUTED.** `--jcac-smoke` passed on the
first attempt (1/1 valid, zero bug tail — the two desk-found fixes held);
`--full` completed 12/12 valid on the Codespace. The frozen analysis ran
once: **ordinal verdict DISAGREE in both cells (live parity between the
arms; see `research/analysis/PHASE7_ORDINAL.md`)** — published as
measured, mechanisms analyzed in the thesis §6.10 and DEFENSE_QA §14.
Nothing below remains open; kept for the protocol record.

## Done in code (session 17) — was steps 1–5

1. ~~Build + side-load both images~~ — `scripts/phase7_kind_run.sh` builds
   all three images; `command_plan` side-loads operator + planner for the
   jcac arm only.
2. ~~Install the operator chart~~ — `operator_install_plan()` in
   `eval/harness/cluster_backend.py`: local repos/tags, `fullnameOverride`,
   `features.url` at the control-plane Service, planner ceilings mirrored
   from `workloads.CLUSTER_SIZES` (small = 24 replicas / 2048 MB).
3. ~~Per-tenant CRs~~ — `operator_crs()` emits Policy + Budget **before**
   Tenant (ensureDefaults leaves existing objects alone, so the eval spec
   wins the race against the default per-tenant-gateway Policy). All
   numbers mirror the sim: initial state 2 replicas / 128 MB / small,
   per-tenant ceiling `replica_max`, budgets from the run's own configs.
4. ~~Demand plumbing~~ — the features endpoint now accepts the platform
   admin key (that endpoint only; unit-tested), `FeatureDemandSource`
   gained an `AdminKey` header mode (the old bearer path expected a JWT —
   a live-session-killing mismatch found at the desk), and the operator
   chart mounts the key from a Secret the harness creates.
5. ~~Actuation~~ — `PolicyReconciler` already scaled the target
   Deployment, but with last-writer-wins semantics: 8 pool tenants
   targeting the shared eval Deployment would have pinned it at ONE
   tenant's share (~2 replicas vs ~16 needed) and invalidated the arm.
   Policies naming the same target now **sum their clamped contributions**
   (unit-tested, including deletion via sibling re-enqueue). The gate is
   also executable now: `command_plan` ends the operator install with
   `kubectl wait --for=condition=Applied` on every Policy — a run in
   which the operator never actuated fails before k6 sends a request.

Capacity parity became a cell property while wiring this: every arm gets
`replicaCount = tenants × 2` (sim initial world) and a total-replica
ceiling of `CLUSTER_SIZES[size].limits_replicas` (hpa via
`autoscaling.hpa.maxReplicas`, jcac via the planner limits). Committed
under test so the ordinal comparison cannot hand one arm more capacity.

## The live session that remains (one Codespace sitting)

1. `bash scripts/phase7_kind_run.sh --jcac-smoke` — first live jcac run
   (`eval/experiments/phase7_jcac_smoke.yaml`, 1 run, 5 min of load).
   Expect a bug tail like the hpa smoke's five; commit each fix.
2. `bash scripts/phase7_kind_run.sh --full` — the ordinal slice
   (12 runs ≈ 5 h wall) or a declared reduced slice (2×1×3 ≈ 2.5 h).
3. **Figure**: paired jcac-vs-hpa live J/cost/violation vs the sim's
   ranking on the same cells — ordinal agreement only (ground rule 4).
   Caption text, frozen now so the disclosure cannot be forgotten in a
   late-night figure session (DEFENSE_QA §2 points here):

   > Live ordinal check on a kind cluster: does the simulator's
   > jcac-vs-HPA ranking on J/cost/violation reproduce under a real
   > scheduler, real HPA, and real load? Absolutes are not comparable
   > across substrates and are not compared. The live data plane burns
   > fixed CPU per request kind, so the cache-size and model-tier knobs
   > are inert here: this figure validates the replica-control
   > projection of the joint controller. The cache knob's realism is
   > carried by the LMSYS protocol (SEMANTIC_CACHE.md,
   > CACHE_PRECISION.md), the tier knob's by the GPU tier bench
   > (TIER_BENCH.md).

Known open decisions for that session, so nothing is re-derived live:

- The planner request weights are the operator's committed defaults
  (α=1, β=2, γ=0.5) — identical to the chart's planner-args defaults, so
  there is no divergence, but do not "tune" either side mid-session.
- The hpa smoke (16d) ran with chart-default replicaCount=2/max=10; the
  parity values above supersede that for the measured slice. The smoke
  remains valid as a pipeline proof, not as a figure input.

## Honesty gate (unchanged, now mechanical)

`jcac` must not be recorded live unless the operator actually actuated —
the `kubectl wait --for=condition=Applied` step enforces this before any
load is generated. The hpa arm stays the only live-verified system until
the jcac smoke passes.
