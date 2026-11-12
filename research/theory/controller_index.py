"""The interaction index of the simulator's joint planner, per decision.

Applies `interaction_index.analyse_table` to every tenant of a
`controller.JCACController` plan: the box is the planner's own move lattice
around the interval-start state, the objective is the planner's own
candidate score with every other tenant held at its planned state (the
quantity `JCACController.best_response_gaps` already uses), and the anchor is
the state the planner's decoupled search holds the other knobs at.

The decision analysed is each tenant's best response AT THE FINAL PLAN. It is
the planner's own last decision exactly when the plan is a fixed point of the
sweeps (`converge_sweeps` on and converged); rows say so in `at_fixed_point`.
Ties: x^J and x^D take the first minimiser in lattice order, as the planner
does without its convergence rule; with the rule the planner keeps its
incumbent on a tie, which changes the point but not phi(x^J).

`IndexedController` records one row per tenant after every plan. It only
reads the controller and its within-cycle projection memo (which the next
cycle clears), so a run with it makes the same decisions as a run without it;
`test_controller_index.py` checks that on real runs. It refuses
configurations where the analysed box would not be the decision's box:
unanchored moves (the box would be centred on the final choice) and replica
dwell (the dwell state has already advanced when the analysis runs).
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

_SIM = Path(__file__).resolve().parents[1] / "jcac_sim"
if str(_SIM) not in sys.path:
    sys.path.insert(0, str(_SIM))

import model  # noqa: E402
from controller import JCACController  # noqa: E402
from model import TIERS, TenantState, apply_action  # noqa: E402

from interaction_index import analyse_table, remainder_table  # noqa: E402

EPS = 1e-9


def tenant_box(ctl: JCACController, tid: str, base: TenantState) -> list[list]:
    """The knob value lists of `tid`'s lattice around `base`: the replica
    counts the lattice reaches (clamped, dwell-blocked moves removed), the
    cache levels one interval may reach, and every tier."""
    config = ctl.configs[tid]
    replicas = sorted({
        apply_action(config, base, delta, base.cache_mb, base.tier).replicas
        for delta in ctl._delta_replicas
        if not (delta and ctl._dwell_blocks(tid, delta))
    })
    return [replicas, list(ctl._cache_candidates(base.cache_mb)), list(TIERS)]


def _repaired_per_knob(table, per_knob, anchor):
    """The decoupled arm's repair (controller._best_for_tenant_decoupled):
    when the combination of per-knob winners is infeasible, the best
    feasible subset of the moves, each knob either moved or held."""
    best, best_x = None, None
    for moved in itertools.product((True, False), repeat=len(anchor)):
        x = tuple(w if mv else a for w, a, mv in zip(per_knob, anchor, moved))
        s = table.phi[x]
        if s is not None and (best is None or s < best):
            best, best_x = s, x
    return best_x, best


def tenant_objective(ctl: JCACController, tid: str):
    """The objective of `tid`'s decision at the last plan: a function of a
    (replicas, cache MB, tier) point returning the planner's candidate score
    with every other tenant at its plan, or None if the point is infeasible
    or outside the tenant's bounds. Also returns the decision's base state
    and the decoupled search's anchor. Call inside `ctl._believed()`."""
    chosen, horizons, origin, cost_horizons = ctl._last_plan_ctx
    config = ctl.configs[tid]
    base = chosen[tid] if origin is None else origin[tid]
    anchor_state = ctl._admitted_anchor(tid, base)
    budget = config.hourly_budget_usd * model.CONTROL_INTERVAL_S / 3600.0
    others = ctl._others(tid, chosen, horizons, cost_horizons)

    def phi(x):
        r, c, t = x
        if not config.knob_admits(c, t):
            return None
        return ctl._candidate_score(tid, TenantState(replicas=r, cache_mb=c, tier=t),
                                    base, others, horizons, cost_horizons, budget)

    return phi, base, (anchor_state.replicas, anchor_state.cache_mb, anchor_state.tier)


def analyse_plan(ctl: JCACController) -> list[dict]:
    """One row per tenant of the controller's last plan."""
    if ctl._last_plan_ctx is None:
        return []
    chosen = ctl._last_plan_ctx[0]
    rows = []
    with ctl._believed():
        for tid in sorted(chosen):
            phi, base, anchor = tenant_objective(ctl, tid)
            box = tenant_box(ctl, tid, base)
            table = remainder_table(box, phi, anchor)
            a = analyse_table(box, table)
            rep_x, rep_phi = (a.per_knob, a.phi_per_knob)
            if a.phi_per_knob is None:
                rep_x, rep_phi = _repaired_per_knob(table, a.per_knob, anchor)
            rows.append({
                "cycle": ctl._cycle, "tenant": tid,
                "at_fixed_point": bool(ctl.converge_sweeps and ctl.last_converged),
                "anchor": anchor, "per_knob": a.per_knob, "joint": a.joint,
                "per_knob_repaired": rep_x,
                "phi_joint": a.phi_joint, "phi_per_knob_repaired": rep_phi,
                "n_points": a.n_points, "n_feasible": a.n_feasible, "n_remainder": a.n_remainder,
                "anchor_feasible": a.phi_anchor is not None,
                "per_knob_feasible": a.phi_per_knob is not None,
                "theorem1_applies": a.theorem1_applies,
                "disagree": a.disagree,
                "disagree_with_loss": bool(a.disagree and a.loss is not None and a.loss > EPS),
                "loss": a.loss, "bound": a.bound,
                "index": a.oscillation, "quantile_index": a.quantile_index,
                "loss_repaired": (rep_phi - a.phi_joint
                                  if rep_phi is not None and a.phi_joint is not None else None),
                # the per-knob combination broke a constraint and a subset was kept
                "repaired": bool(a.phi_per_knob is None and rep_x is not None),
                # no subset was feasible: the planner would fall back
                "per_knob_fallback": rep_x is None,
                "joint_moves_knobs": (sum(j != b for j, b in zip(a.joint, anchor))
                                      if a.joint is not None else None),
            })
    return rows


class IndexedController(JCACController):
    """A JCACController that records `analyse_plan` after every plan.

    `last` holds the most recently built instance of this process, which is
    how a runner reaches the controller that `simulate.run` builds inside.
    """

    last: "IndexedController | None" = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.anchor_moves:
            raise ValueError("the index needs anchored moves: unanchored, the analysed box "
                             "would be centred on the final choice, not the decision's start")
        if self.replica_dwell_steps:
            raise ValueError("the index does not support replica dwell: the dwell state has "
                             "advanced past the plan when the analysis runs")
        self.index_rows: list[dict] = []
        IndexedController.last = self

    def plan(self, states, demands, interference=None):
        plans = super().plan(states, demands, interference)
        self.index_rows.extend(analyse_plan(self))
        return plans


def execute_indexed(run) -> tuple[dict, list[dict]]:
    """`harness.sim_backend.execute(run)` with the planner replaced by
    `IndexedController`; returns the result and the index rows. Raises if the
    run built no planner (a baseline arm), so rows can never be stale."""
    import simulate
    from harness import sim_backend

    original = simulate.JCACController
    IndexedController.last = None
    simulate.JCACController = IndexedController
    try:
        result = sim_backend.execute(run)
    finally:
        simulate.JCACController = original
    built = IndexedController.last
    IndexedController.last = None
    if built is None:
        raise ValueError(f"run {run.run_id} ({run.system}) built no joint planner")
    return result, built.index_rows
