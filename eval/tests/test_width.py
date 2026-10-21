"""Actuation width and the coordination diagnostic (PREREG_WIDTH): opt-in,
reporting-only where it says so, and the wide lattice really is wider."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR))

from harness import systems, workloads  # noqa: E402  (also puts research/jcac_sim on sys.path)
import model  # noqa: E402
import simulate  # noqa: E402
from controller import JCACController  # noqa: E402

CONVERGED = {"anchor_moves": True, "converge_sweeps": True}


def _run(params: dict, wl: str = "agentic", size: str = "small", seed: int = 9, steps: int = 40):
    tids, buckets, configs, limits = workloads.build(wl, "uniform", size, seed, steps)
    return simulate.run("jcac", tids, buckets, configs=configs, limits=limits, collect_rows=False,
                        controller_params=dict(params), jitter_seed=seed)


def test_options_are_validated():
    configs = simulate.default_configs(["a"])
    with pytest.raises(ValueError):
        JCACController(configs, lattice="huge", **CONVERGED)
    with pytest.raises(ValueError):
        JCACController(configs, track_coordination=True, knob_search="decoupled", **CONVERGED)


@pytest.mark.parametrize("lattice", ["published", "wide"])
def test_tracking_reports_without_changing_a_single_decision(lattice):
    for wl in ("agentic", "joint_stress"):
        plain = _run({**CONVERGED, "lattice": lattice}, wl=wl)
        tracked = _run({**CONVERGED, "lattice": lattice, "track_coordination": True}, wl=wl)
        assert plain.summary() == tracked.summary()
        assert plain.coordination_disagreement_share is None
        assert 0.0 <= tracked.coordination_disagreement_share <= 1.0


def test_the_wide_lattice_reaches_further_in_one_interval():
    configs = simulate.default_configs(["a"])
    narrow = JCACController(configs, **CONVERGED)
    wide = JCACController(configs, lattice="wide", **CONVERGED)
    base = model.TenantState(replicas=10, cache_mb=128, tier="small")
    reach = lambda ctl: ({s.replicas for s in ctl._lattice("a", base)},  # noqa: E731
                         {s.cache_mb for s in ctl._lattice("a", base)})
    n_rep, n_cache = reach(narrow)
    w_rep, w_cache = reach(wide)
    assert max(n_rep) - min(n_rep) <= 4 and len(n_cache) == 3
    assert max(w_rep) - min(w_rep) > 4 and w_cache == set(model.CACHE_LEVELS_MB)
    assert n_rep < w_rep and n_cache < w_cache


def test_width_arms_are_registered_as_specified():
    assert systems.SYSTEMS["jcac_joint_wide_tracked"].params["lattice"] == "wide"
    assert systems.SYSTEMS["jcac_decoupled_wide"].params["knob_search"] == "decoupled"
    assert "lattice" not in systems.SYSTEMS["jcac_joint_tracked"].params
