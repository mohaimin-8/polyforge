"""The width-settable planner: width 2 with cache radius 1 is the published
lattice exactly, and other widths move as far as they say."""

import dataclasses
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (HERE, REPO / "research" / "jcac_sim", REPO / "eval"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import simulate  # noqa: E402
from harness import config as hconfig  # noqa: E402
from harness import sim_backend, workloads  # noqa: E402
from model import CACHE_LEVELS_MB, TenantState  # noqa: E402
from width import WidthController  # noqa: E402


def _run(system="jcac_level_ratchet", workload="agentic", mix="whale", size="large", steps=20):
    spec = hconfig.load(REPO / "eval" / "experiments" / "matrix_level2.yaml")
    run = next(r for r in hconfig.expand(spec)
               if (r.system, r.workload, r.tenant_mix, r.cluster_size, r.rep) == (system, workload, mix, size, 0))
    return dataclasses.replace(run, steps=steps)


def _execute(run, cls, **extra):
    """Run with simulate's planner class replaced by `cls`, extra params added."""
    from harness.systems import SYSTEMS, SystemSpec

    spec = SYSTEMS[run.system]
    SYSTEMS["x_width_test"] = SystemSpec(spec.controller, params={**spec.params, **extra})
    original = simulate.JCACController
    simulate.JCACController = cls
    try:
        return sim_backend.execute(dataclasses.replace(run, system="x_width_test"))
    finally:
        simulate.JCACController = original
        del SYSTEMS["x_width_test"]


def test_width_two_cache_one_is_the_published_lattice():
    run = _run()
    plain = sim_backend.execute(run)
    widened = _execute(run, WidthController, replica_radius=2, cache_radius=1)
    assert widened["metrics"] == plain["metrics"]


@pytest.mark.parametrize("r,c", [(1, 1), (4, 2), (16, 5)])
def test_moves_stay_inside_the_requested_radii(r, c):
    tenant_ids, buckets, configs, limits = workloads.build("joint_stress", "whale", "large", 5, 25)
    ctl = WidthController(configs, limits=limits, anchor_moves=True, converge_sweeps=True,
                          replica_radius=r, cache_radius=c)
    states = {tid: TenantState() for tid in tenant_ids}
    widest = 0
    for demands in buckets:
        plans = ctl.plan(states, demands)
        for tid, p in plans.items():
            dr = abs(p.state.replicas - states[tid].replicas)
            dc = abs(CACHE_LEVELS_MB.index(p.state.cache_mb) - CACHE_LEVELS_MB.index(states[tid].cache_mb))
            fallback = p.state.tier == "none" and p.state.cache_mb == 0
            if not fallback:
                assert dr <= r and dc <= c
            widest = max(widest, dr)
        states = {tid: p.state for tid, p in plans.items()}
    assert widest >= min(r, 2)


def test_bad_radii_are_refused():
    _, _, configs, limits = workloads.build("joint_stress", "whale", "large", 5, 3)
    with pytest.raises(ValueError):
        WidthController(configs, limits=limits, replica_radius=0)
    with pytest.raises(ValueError):
        WidthController(configs, limits=limits, cache_radius=0)
