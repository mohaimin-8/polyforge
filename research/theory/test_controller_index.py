"""The index of the real planner: logging changes no decision, the index's
joint and per-knob choices are the planner's own searches, Theorem 1 holds on
every decision of real runs, and unsupported configurations are refused."""

import dataclasses
from pathlib import Path

import pytest

from controller import JCACController
from controller_index import IndexedController, analyse_plan, execute_indexed, tenant_objective
from harness import config as hconfig
from harness import sim_backend, workloads
from model import TenantState

REPO = Path(__file__).resolve().parents[2]
EPS = 1e-9

CELLS = [
    ("matrix_width_published.yaml", "jcac_joint_tracked", "ai_cacheable", "uniform", "small", None),
    ("matrix_width_coupled.yaml", "jcac_joint_wide_tracked", "joint_stress", "whale", "medium", 12),
    ("matrix_level2.yaml", "jcac_level_ratchet", "agentic", "whale", "medium", None),
]


def _run(yaml, system, workload, mix, size, rep=0):
    spec = hconfig.load(REPO / "eval" / "experiments" / yaml)
    return next(r for r in hconfig.expand(spec)
                if (r.system, r.workload, r.tenant_mix, r.cluster_size, r.rep)
                == (system, workload, mix, size, rep))


@pytest.mark.parametrize("cell", CELLS)
def test_logging_changes_no_decision_and_theorem1_holds(cell):
    *where, steps = cell
    run = _run(*where)
    if steps is not None:  # the wide lattice is slow; a shorter run of the same world
        run = dataclasses.replace(run, steps=steps)
    plain = sim_backend.execute(run)
    logged, rows = execute_indexed(run)
    assert logged["metrics"] == plain["metrics"]
    # 8 tenants, one plan per bucket; steps + 1 buckets, the last plan unscored.
    assert len(rows) == 8 * (run.steps + 1)
    assert all(r["n_feasible"] > 0 for r in rows)
    assert all(r["at_fixed_point"] for r in rows)
    checked = [r for r in rows if r["theorem1_applies"]]
    assert len(checked) > 0.9 * len(rows)
    for r in checked:
        assert r["loss"] >= -EPS
        assert r["loss"] <= r["bound"] + EPS
        assert r["bound"] <= r["index"] + EPS


def _short_controller(converge: bool) -> tuple[JCACController, list, list]:
    tenant_ids, buckets, configs, limits = workloads.build("joint_stress", "whale", "medium", 3, 30)
    ctl = JCACController(configs, limits=limits, anchor_moves=True, converge_sweeps=converge)
    return ctl, tenant_ids, buckets


@pytest.mark.parametrize("converge", [False, True])
def test_choices_are_the_planners_own_searches(converge):
    """Without the convergence rule the index's joint and repaired per-knob
    points ARE the planner's `_best_for_tenant` and
    `_best_for_tenant_decoupled` picks. With it the planner keeps its
    incumbent unless a candidate is strictly better, so the joint pick has
    the same objective value and the decoupled pick is the index's or an
    incumbent no worse than it."""
    ctl, tenant_ids, buckets = _short_controller(converge)
    states = {tid: TenantState() for tid in tenant_ids}
    compared = 0
    for demands in buckets[:30]:
        plans = ctl.plan(states, demands)
        chosen, horizons, origin, cost_horizons = ctl._last_plan_ctx
        with ctl._believed():
            for row in analyse_plan(ctl):
                tid = row["tenant"]
                phi, _, _ = tenant_objective(ctl, tid)
                joint = ctl._best_for_tenant(tid, chosen, horizons, origin, cost_horizons)
                dec = ctl._best_for_tenant_decoupled(tid, chosen, horizons, origin, cost_horizons)
                joint_pt = (joint.replicas, joint.cache_mb, joint.tier)
                dec_pt = (dec.replicas, dec.cache_mb, dec.tier)
                if row["joint"] is not None:
                    compared += 1
                    if converge:
                        assert phi(joint_pt) == pytest.approx(row["phi_joint"], abs=1e-12)
                    else:
                        assert joint_pt == row["joint"]
                if row["per_knob_repaired"] is not None:
                    current = chosen[tid]
                    cur_pt = (current.replicas, current.cache_mb, current.tier)
                    if converge and dec_pt == cur_pt and dec_pt != row["per_knob_repaired"]:
                        assert phi(cur_pt) <= row["phi_per_knob_repaired"] + 1e-12
                    else:
                        assert dec_pt == row["per_knob_repaired"]
        states = {tid: p.state for tid, p in plans.items()}
    assert compared >= 200


def test_unsupported_configurations_are_refused():
    _, _, configs, limits = workloads.build("joint_stress", "whale", "medium", 3, 5)
    with pytest.raises(ValueError, match="anchored"):
        IndexedController(configs, limits=limits)
    with pytest.raises(ValueError, match="dwell"):
        IndexedController(configs, limits=limits, anchor_moves=True, replica_dwell_steps=3)


def test_a_run_without_a_joint_planner_is_refused():
    run = dataclasses.replace(_run("matrix_level2.yaml", "hpa_level_capped", "agentic", "whale", "medium"),
                              steps=5)
    with pytest.raises(ValueError, match="no joint planner"):
        execute_indexed(run)
