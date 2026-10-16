"""The answer-quality model (2026-10-16): sourced, bounded, reporting-only
unless priced, and the arms that price it do what they claim."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR))

from harness import systems, workloads  # noqa: E402  (also puts research/jcac_sim on sys.path)
import baselines  # noqa: E402
import model  # noqa: E402
import simulate  # noqa: E402
from controller import JCACController, Weights  # noqa: E402

GPU = {"small": 1.0e-4, "mid": 1.475e-4, "large": 1.518e-4}


def test_tier_quality_is_the_mean_of_the_reported_benchmarks():
    assert model.TIER_QUALITY["small"] == pytest.approx(312.0 / 11 / 100)
    assert model.TIER_QUALITY["mid"] == pytest.approx(603.2 / 11 / 100)
    assert model.TIER_QUALITY["large"] == pytest.approx(705.4 / 11 / 100)
    assert model.TIER_QUALITY["none"] < model.TIER_QUALITY["small"] < model.TIER_QUALITY["mid"] \
        < model.TIER_QUALITY["large"]
    assert model.quality_shortfall("large") == 0.0 and model.quality_shortfall("none") == 1.0


def test_quality_loss_is_bounded_ordered_and_zero_without_ai():
    config = model.TenantConfig("a")
    ai = model.Demand(rps={"chat": 4.0, "crud_read": 4.0})
    losses = [model.evaluate_step(config, model.TenantState(replicas=4, cache_mb=256, tier=t), ai)
              .quality_loss for t in ("none", "small", "mid", "large")]
    assert all(0.0 <= x <= 1.0 for x in losses)
    assert losses == sorted(losses, reverse=True)
    crud = model.Demand(rps={"crud_read": 5.0})
    assert model.evaluate_step(config, model.TenantState(), crud).quality_loss == 0.0


def test_quality_weight_is_validated():
    configs = simulate.default_configs(["a"])
    with pytest.raises(ValueError):
        JCACController(configs, quality_weight=-1.0)
    with pytest.raises(ValueError):
        JCACController(configs, weights=Weights(beta=0.0), quality_weight=1.0)


def test_pricing_quality_moves_plans_to_better_answers():
    model.set_economy(tier_cost_usd_per_req=GPU)
    try:
        tids, buckets, configs, limits = workloads.build("ai_cacheable", "uniform", "medium", 3, 40)
        base = {"anchor_moves": True, "converge_sweeps": True}
        blind = simulate.run("jcac", tids, buckets, configs=configs, limits=limits, collect_rows=False,
                             controller_params=base, jitter_seed=3)
        priced = simulate.run("jcac", tids, buckets, configs=configs, limits=limits, collect_rows=False,
                              controller_params={**base, "quality_weight": 2.0}, jitter_seed=3)
    finally:
        model.set_economy()
    assert priced.mean_quality_loss < blind.mean_quality_loss


def test_quality_router_picks_the_locally_best_tier():
    config = model.TenantConfig("a")
    state = model.TenantState(replicas=6, cache_mb=256, tier="small")
    demand = model.Demand(rps={"chat": 2.0})
    model.set_economy(tier_cost_usd_per_req=GPU)
    try:
        pick = baselines.quality_aware_tier(config, state, demand, quality_weight=2.0)

        def local(tier):
            m = model.evaluate_step(config, model.TenantState(replicas=6, cache_mb=256, tier=tier), demand)
            return m.cost_usd / 0.01 + 2.0 * m.violation + 2.0 * m.quality_loss

        assert pick == min(("small", "mid", "large"), key=local)
    finally:
        model.set_economy()


def test_fixed_tier_comparators_serve_on_their_tier():
    configs = simulate.default_configs(["a", "b"])
    hpa = baselines.HPAController(configs, target_rho=0.1, tier="large")
    plans = hpa.plan({t: model.TenantState() for t in configs},
                     {t: model.Demand(rps={"chat": 1.0}) for t in configs})
    assert all(s.tier == "large" for s in plans.values())
    assert baselines.HPAController(configs).tier is None  # published behaviour


def test_quality_arms_are_registered_as_specified():
    for arm in ("jcac_quality", "jcac_quality_decoupled", "jcac_quality_knobwise"):
        assert systems.SYSTEMS[arm].params["quality_weight"] == 2.0
        assert systems.SYSTEMS[arm].params["enforce_budget"] is False  # equal constraints
    assert "enforce_budget" not in systems.SYSTEMS["jcac_quality_budget"].params
    assert systems.SYSTEMS["jcac_quality_decoupled"].params["knob_search"] == "decoupled"
    assert systems.SYSTEMS["layered_quality_capped"].controller == "layered_quality_capped"
