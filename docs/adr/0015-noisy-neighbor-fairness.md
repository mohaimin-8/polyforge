# ADR 0015: Noisy-neighbor detection — peer-relative scoring into the planner's cost

Status: accepted · 2026-07-10 · detector + planner integration implemented (W32)

## Context

W32's research contribution: JCAC should account for inter-tenant
*interference*, not just per-tenant SLO. The plan calls for Pixie or
Cilium Hubble exposing eBPF metrics (CPU stalls, syscall storms, memory
pressure), a detector that flags tenants degrading their neighbors, and a
fairness penalty in the planner using Jain's index.

## Decision

**Detection is peer-relative, not absolute.** Each tenant's score sums its
signals' excess over the *cluster median* of that signal
(`value/median − 1`, floored at 0). A uniformly busy cluster therefore
flags nobody — noisy means louder than the neighbors, which is the only
definition that makes sense for interference. Signals mirror the eBPF
metric shapes: PSI CPU-stall share, syscall rate, memory-pressure events.

**Flagging has hysteresis in both directions.** Three consecutive noisy
windows (30 s at the control interval) set the flag; six clean windows
clear it. One loud window throttles nobody; one quiet window doesn't
un-throttle an abuser.

**The penalty rides the planner's existing objective.** A flagged tenant's
interference score reaches the planner via the plan request; candidates
that *expand* the noisy tenant (replicas, cache) pay
`γ · score · resource_share` in the objective, so the optimizer shrinks
the tenant toward its floor instead of feeding the interference. The
detector never acts directly on the cluster — mitigation flows through
the same guarded plan/apply path as every other action, and every
throttle is audited like any plan.

**Jain's index is the reported fairness measure**, computed twice by
design: in the planner over projected satisfactions (inside the
objective's γ term) and in the operator over each cycle's response,
exported as the `polyforge_operator_fairness_jain` Prometheus gauge.

## Deviations and deferrals

- **Pixie/Hubble not deployed.** No cluster on this machine. The detector
  consumes samples through an `Observe([]Sample)` interface; the
  Pixie adapter is a straight mapping onto that interface and belongs to
  the W33 harness environment where a cluster exists.
- **24-hour soak deferred** for the same reason, to the W33 harness.

## Verification

Go tests: flag raised on the 3rd noisy window and not before; uniformly
busy cluster flags nobody; flag clears only after the full clean streak
(the test initially quieted one signal and the detector correctly kept
the flag — mitigation means quiet on *all* signals); vanished tenants are
forgotten; closed-loop test chains the real detector into the PlanRunner
and asserts the score reaches the plan request and the Jain gauge updates.
Python test: a flagged tenant's plan never exceeds its unflagged
allocation. Live end-to-end (inject noisy pod → eBPF → throttle) needs
the cluster and is deferred with the Pixie adapter.
