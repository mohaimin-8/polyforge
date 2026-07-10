# ADR 0014: JCAC planner — exact lattice search over HTTP, CVXPY/gRPC deferred

Status: accepted · 2026-07-10 · simulator + service + operator loop implemented (W30–W31)

## Context

W30–W31 call for the Joint Cross-layer Adaptive Controller: an MPC
formulation solved with CVXPY in a Python planner, exposed over gRPC, and
called by the operator every 10 s. Two prescribed tools do not fit the
problem or the environment:

1. **CVXPY.** The decision variables are integer (replicas) and categorical
   (model tier). That makes the problem mixed-integer; CVXPY expresses it
   only with a MIP backend (commercial, or heavy free ones), and its
   bundled convex solvers (ECOS/OSQP/Clarabel) cannot solve it at all.
   Relaxing tiers to continuous and rounding would silently change what is
   being optimized.
2. **gRPC.** No protoc toolchain on the dev machine, and generated stubs
   would be the repo's first codegen artifacts in Python.

## Decision

**Solver: exact enumeration over a move-blocked action lattice.** Per
tenant the lattice is ≤ 60 candidates (Δreplicas ∈ [-2,2] × one cache
level up/down × 4 tiers); with move blocking (candidate held across the
60 s horizon) the per-tenant solve is exact by exhaustion. Tenants couple
through the shared objective and cluster-wide constraints, handled by two
rounds of coordinate descent. Deterministic, microseconds per cycle, zero
dependencies. The objective uses `log1p(SLO excess)` so deep overload
keeps a gradient, plus a per-knob switch penalty for hysteresis. If a
future formulation outgrows the lattice (continuous cache, fine-grained
tiers), CVXPY-with-MIP is the escape hatch; the formulation in
`research/jcac_sim/README.md` is written to survive that swap.

**Transport: JSON over HTTP, isolated behind an interface.** The planner
(`services/planner`, stdlib-only) exposes `POST /v1/plan`; the operator
talks to it through the `planner.Client` Go interface. A gRPC transport is
a new implementation of that one interface — the reconciler, guardrails,
and audit path would not change. One planning call covers *all* tenants:
jointness is the point, so there is deliberately no per-tenant Plan RPC.

**Same code offline and online.** The service imports the controller from
`research/jcac_sim` — the simulator that produced the paper's Pareto
figures and the live planner are provably the same solver.

**Failure containment lives in the operator, not the planner.**
- The planner call has a 3 s timeout inside a 10 s control period.
- Any failure — unreachable, 5xx, malformed or incomplete plan (validated
  tenant-by-tenant before use) — engages the fallback: Policy specs are
  left untouched, so the **last good plan keeps running**; status flips to
  `fallback` once (not per cycle) so degradation is visible in kubectl.
- Applied plans are re-clamped to Policy replica bounds at apply time.
  The planner already respects bounds, but no plan source is trusted to.
- Every action (and every fallback) is published to the existing NATS
  JetStream audit stream (`polyforge.audit.operator.plan.<tenant>`) with
  input state, output action, and the planner's own $-impact estimate.

**Demand v1.** Live demand comes from the control plane's per-tenant
feature API; unknown service names fold into generic CRUD traffic. This is
deliberately coarse — the classifier's WorkloadProfile refines it when the
W32 loop closes. Tenants without telemetry are excluded from the joint
plan rather than planned against zeros.

## Verification

- Python: 7 planner service tests (core validation, forecast persistence
  across calls, interference throttling, HTTP 400-not-500 on garbage).
- Go: planner client tests (round trip, malformed-plan rejection, 500,
  timeout), PlanRunner tests (joint apply, bound clamping, fallback
  leaves spec untouched + single status flip, no-demand skip,
  interference forwarding), feature demand source mapping tests.
- Cross-language: `TestLivePlannerContract` runs the *real* Python service
  against the Go client (gated on `POLYFORGE_TEST_PLANNER_URL`, same
  skip-when-absent convention as the Docker-gated tests). Executed locally
  2026-07-10: a sustained surge scales replicas within three control
  cycles. It also caught a real contract bug: Go's zero-value
  `hourly_budget_usd: 0` means "spend nothing" to the planner — a zero
  budget sheds the tenant to its floor, so callers must always send one.

Not verified locally: the loop against a live API server + deployed
planner pod (no cluster on this machine; W33 harness environment).
