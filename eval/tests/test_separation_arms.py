"""The separation arms (2026-10-16): how one tenant's knobs are searched
(joint / decoupled / knobwise), the no-shed tier floor, and the guard
metrics. These pin what a controller review found wrong in the first draft
of the arms: a decoupled search that shed whenever one knob's set was empty,
a tier floor that still shed through the published fallback, and a
decoupled-plus-pin combination that could never leave tier "none".
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR))

from harness import systems, workloads  # noqa: E402  (also puts research/jcac_sim on sys.path)
from harness.sim_backend import apply_arm_bounds  # noqa: E402
import model  # noqa: E402
import simulate  # noqa: E402
from controller import JCACController  # noqa: E402

CELLS = [("agentic", "uniform", "small"), ("ai_cacheable", "whale", "medium"),
         ("joint_stress", "premium_heavy", "small")]
CONVERGED = {"anchor_moves": True, "converge_sweeps": True}


def _run(arm: str, wl: str, mix: str, size: str, steps: int = 40, seed: int = 5):
    spec = systems.SYSTEMS[arm]
    tids, buckets, configs, limits = workloads.build(wl, mix, size, seed, steps)
    configs = apply_arm_bounds(spec, configs)
    return simulate.run(spec.controller, tids, buckets, configs=configs, limits=limits,
                        collect_rows=False, controller_params=dict(spec.params), jitter_seed=seed)


def test_knob_search_is_validated():
    configs = simulate.default_configs(["a"])
    with pytest.raises(ValueError):
        JCACController(configs, knob_search="sideways", **CONVERGED)
    with pytest.raises(ValueError):
        JCACController(configs, knob_search="decoupled")  # needs anchored moves
    with pytest.raises(ValueError):
        JCACController(configs, objective_term="excess")


def test_tier_floor_is_validated_and_refuses_a_tier_pin():
    with pytest.raises(ValueError):
        systems.SystemSpec("jcac", tier_min="Small")
    with pytest.raises(ValueError):
        systems.SystemSpec("jcac", tier_min="small", knob_freeze=frozenset({"tier"}))


@pytest.mark.parametrize("wl,mix,size", CELLS)
def test_every_search_picks_inside_the_joint_lattice_and_never_beats_it(wl, mix, size, monkeypatch):
    """On every call, the decoupled and knobwise picks are members of the
    joint lattice and score no better than the joint best response."""
    seen = {"calls": 0}
    orig = {k: getattr(JCACController, k) for k in
            ("_best_for_tenant_decoupled", "_best_for_tenant_knobwise")}

    def checked(name):
        def wrapper(self, tid, chosen, horizons, origin=None, cost_horizons=None):
            pick = orig[name](self, tid, chosen, horizons, origin, cost_horizons)
            if self._plan_origin.get(tid) == "fallback":
                return pick
            base = chosen[tid] if origin is None else origin[tid]
            assert pick in set(self._lattice(tid, base)) or pick == chosen[tid]
            config = self.configs[tid]
            budget = config.hourly_budget_usd * model.CONTROL_INTERVAL_S / 3600.0
            others = self._others(tid, chosen, horizons, cost_horizons)
            scores = [s for c in self._lattice(tid, base)
                      if (s := self._candidate_score(tid, c, base, others, horizons,
                                                     cost_horizons, budget)) is not None]
            mine = self._candidate_score(tid, pick, base, others, horizons, cost_horizons, budget)
            if scores and mine is not None:
                assert min(scores) <= mine + 1e-12
            seen["calls"] += 1
            return pick
        return wrapper

    for name in orig:
        monkeypatch.setattr(JCACController, name, checked(name))
    for arm in ("jcac_decoupled", "jcac_knobwise"):
        _run(arm, wl, mix, size)
    assert seen["calls"] > 0


@pytest.mark.parametrize("wl,mix,size", CELLS)
def test_tier_floor_never_sheds_ai(wl, mix, size):
    assert _run("jcac_tier_floor", wl, mix, size).tier_none_step_share == 0.0


def test_search_arms_keep_inside_the_move_clamps():
    for arm in ("jcac_converged", "jcac_decoupled", "jcac_knobwise"):
        for wl, mix, size in CELLS:
            r = _run(arm, wl, mix, size)
            # The published fallback (replica_min, 0 MB, none) is the one move
            # allowed outside the clamps; with it unused, nothing may jump.
            if not r.plan_fallback_share:
                assert r.clamp_violation_step_share == 0.0, (arm, wl, r.clamp_violation_step_share)


def test_decoupled_under_a_tier_pin_is_not_absorbing():
    """A base the bounds reject (tier none under a small floor) must not
    empty every 1-D set and repeat the fallback forever."""
    tids, buckets, configs, limits = workloads.build("agentic", "uniform", "small", 5, 30)
    pinned = {t: replace(c, tier_min="small") for t, c in configs.items()}
    ctl = JCACController(pinned, limits=limits, knob_search="decoupled", clamped_fallback=True,
                         **CONVERGED)
    states = {t: model.TenantState(tier="none") for t in tids}
    for demands in buckets[:10]:
        plans = ctl.plan(states, demands)
        states = {t: p.state for t, p in plans.items()}
    assert all(s.tier != "none" for s in states.values())


def test_guard_metrics_are_reported_and_stay_out_of_the_summary():
    r = _run("jcac_decoupled", "agentic", "uniform", "small")
    for field in ("plan_fallback_share", "plan_repaired_share", "capped_cycle_share"):
        assert getattr(r, field) is not None
        assert field not in r.summary()
    assert 0.0 <= r.budget_overrun_step_share <= 1.0
    baseline = _run("hpa_fair_capped", "agentic", "uniform", "small")
    assert baseline.plan_fallback_share is None


def test_capped_baselines_stay_inside_the_cluster_caps():
    spec = systems.SYSTEMS["hpa_fair_capped"]
    tids, buckets, configs, limits = workloads.build("crud_bursty", "uniform", "small", 3, 40)
    ctl = simulate.baselines.make_baseline(spec.controller, configs,
                                           **systems.base_params(spec, "small"))
    ctl.set_limits(limits)
    states = {t: model.TenantState(cache_mb=512) for t in tids}
    for demands in buckets:
        states = ctl.plan(states, demands)
        assert sum(s.replicas for s in states.values()) <= limits.replicas
        assert sum(s.cache_mb for s in states.values()) <= limits.cache_mb


def test_static_share_never_breaks_a_tenant_cache_floor():
    tids, buckets, configs, limits = workloads.build("ai_cacheable", "uniform", "small", 3, 5)
    floored = {t: replace(c, cache_min=512) for t, c in configs.items()}  # share is 2048 / 8 = 256
    ctl = simulate.baselines.make_baseline("static_share", floored)
    ctl.set_limits(limits)
    plan = ctl.plan({t: model.TenantState() for t in tids}, buckets[0])
    assert all(s.cache_mb >= 512 and floored[t].knob_admits(s.cache_mb, s.tier) for t, s in plan.items())
