"""Level-playing-field campaign (PREREG_LEVEL, 2026-11-09).

Pins the opt-in pieces the campaign adds: the agent-latency robustness plant,
the planner's outage price, the gateway budget gate, the LEVEL arms and their
tuning, and the shed-count identity the scorer and the tuner share. Every
published arm stays bit-identical; that is the golden hashes' job
(test_solver_convergence.py), not this file's.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR))
sys.path.insert(0, str(EVAL_DIR / "baselines"))

from harness import config, sim_backend, workloads  # noqa: E402  (also puts research/jcac_sim on sys.path)
from harness.config import RunSpec  # noqa: E402
from harness.systems import SYSTEMS  # noqa: E402
import baselines  # noqa: E402
import controller  # noqa: E402
import model  # noqa: E402
import simulate  # noqa: E402

MONO = {"small": 937.5, "mid": 2500.0, "large": 6250.0}
LEVEL_YAMLS = ("matrix_level", "matrix_level_mono", "matrix_level_lag", "matrix_level_heldout",
               "matrix_level_trace")


# --- robustness plant --------------------------------------------------------
def test_agent_row_override_touches_agent_only_and_resets():
    model.set_model_form(agent_latency_ms=MONO)
    assert model.tier_base_latency_ms("agent", "small") == 937.5
    assert model.tier_base_latency_ms("chat", "small") == model.TIER_BASE_LATENCY_MS["chat"]["small"]
    model.set_model_form()
    assert model.tier_base_latency_ms("agent", "small") == 6000.0


def test_agent_row_override_is_validated():
    with pytest.raises(ValueError):
        model.set_model_form(agent_latency_ms={"small": 1.0, "mid": 2.0})
    with pytest.raises(ValueError):
        model.set_model_form(agent_latency_ms=MONO, tier_latency_ms=MONO)
    model.set_model_form()


def test_the_mono_campaign_runs_the_override():
    spec = config.load(EVAL_DIR / "experiments" / "matrix_level_mono.yaml")
    assert dict(spec.model_form) == {"agent_latency_small": 937.5, "agent_latency_mid": 2500,
                                     "agent_latency_large": 6250}
    run = next(iter(config.expand(spec)))
    sim_backend._apply_model_form(run.model_form)
    assert model.AGENT_LATENCY == MONO
    model.set_model_form()


# --- the planner's outage price ----------------------------------------------
def _planner(**kw):
    tids, buckets, configs, limits = workloads.build("agentic", "uniform", "small", 3, 8)
    ctl = controller.JCACController(configs, limits=limits, anchor_moves=True, converge_sweeps=True, **kw)
    return tids, buckets, configs, ctl


def test_shed_charge_equals_the_small_tier_spend_it_avoids():
    tids, buckets, configs, ctl = _planner(shed_price_tier="small")
    tid = tids[0]
    demand = buckets[0][tid]
    horizon = [demand] * 3
    shed = model.TenantState(replicas=3, cache_mb=256, tier="none")
    on_small = model.evaluate_step(configs[tid], replace(shed, tier="small"), demand)
    charge = ctl._shed_charge(tid, shed, horizon)
    assert charge == pytest.approx(on_small.cost_tier_usd, rel=1e-9)
    ctl_mid = _planner(shed_price_tier="mid")[3]
    assert ctl_mid._shed_charge(tid, shed, horizon) == pytest.approx(charge * 10.0, rel=1e-9)


def test_shed_price_must_name_a_tier():
    with pytest.raises(ValueError):
        _planner(shed_price_tier="none")


def test_the_outage_price_never_touches_the_budget_filter():
    """The charge is objective-only: a tenant whose every serving candidate is
    over budget still sheds (forced), with or without the price."""
    tids, buckets, configs, limits = workloads.build("agentic", "uniform", "small", 3, 8)
    poor = {t: replace(c, hourly_budget_usd=0.01) for t, c in configs.items()}
    for kw in ({}, {"shed_price_tier": "small"}):
        ctl = controller.JCACController(poor, limits=limits, anchor_moves=True, converge_sweeps=True, **kw)
        states = {t: model.TenantState(replicas=1, cache_mb=0, tier="small") for t in tids}
        plan = ctl.plan(states, buckets[0])
        assert all(entry.state.tier == "none" for entry in plan.values()), kw


# --- the gateway budget gate --------------------------------------------------
def test_the_gate_wraps_the_capped_cheapest_tier_stack():
    tids, _, configs, _ = workloads.build("ai_cacheable", "uniform", "small", 1, 4)
    arm = SYSTEMS["layered_small_level_gated"]
    ctl = baselines.make_baseline(arm.controller, configs, **sim_backend.base_params(arm, "small"))
    assert isinstance(ctl, baselines.CapEnforced)
    assert isinstance(ctl.inner, baselines.BudgetGated)
    assert isinstance(ctl.inner.inner, baselines.LayeredSmallController)


def test_the_gate_passes_affordable_steps_and_refuses_model_calls_otherwise():
    tids, buckets, configs, _ = workloads.build("agentic", "uniform", "small", 5, 8)
    inner = baselines.make_baseline("layered_small", configs, target_rho=0.1)
    gate = baselines.BudgetGated(inner)
    states = {t: model.TenantState(replicas=2, cache_mb=128, tier="small") for t in tids}
    want = inner.plan(states, buckets[0])
    got = gate.plan(states, buckets[0])
    for t in tids:
        cap = configs[t].hourly_budget_usd * model.CONTROL_INTERVAL_S / 3600.0
        billed = model.evaluate_step(configs[t], want[t], buckets[0][t]).cost_usd
        if billed <= cap:
            assert got[t] == want[t]
        else:
            assert got[t].tier == "none"
            assert model.evaluate_step(configs[t], got[t], buckets[0][t]).cost_usd <= cap + 1e-12
    rich = {t: replace(c, hourly_budget_usd=1e6) for t, c in configs.items()}
    rich_gate = baselines.BudgetGated(baselines.make_baseline("layered_small", rich, target_rho=0.1))
    assert rich_gate.plan(states, buckets[0]) == baselines.make_baseline(
        "layered_small", rich, target_rho=0.1).plan(states, buckets[0])


def test_the_gate_sheds_and_keeps_every_budget_in_a_run():
    spec = RunSpec(experiment="level_test", backend="sim", system="layered_small_level_gated",
                   workload="agentic", tenant_mix="premium_heavy", cluster_size="small", rep=0,
                   steps=120, run_id="lv-gate", seed=17, store_timeseries=False)
    m = sim_backend.execute(spec)["metrics"]
    assert m["budget_overrun_step_share"] <= 0.02
    ungated = sim_backend.execute(replace(spec, system="layered_small_level_capped", run_id="lv-cap"))["metrics"]
    assert ungated["budget_overrun_step_share"] > m["budget_overrun_step_share"]


# --- arms, tuning, scorer identity ---------------------------------------------
def test_the_treatment_uses_the_documented_half_replica_penalty():
    assert SYSTEMS["jcac_level"].params["switch_penalty"] == controller.SWITCH_PENALTY_HALF_REPLICA
    level = {k: v for k, v in SYSTEMS["jcac_level"].params.items()}
    dec = {k: v for k, v in SYSTEMS["jcac_level_decoupled"].params.items() if k != "knob_search"}
    assert level == dec and SYSTEMS["jcac_level_decoupled"].params["knob_search"] == "decoupled"


@pytest.mark.parametrize("arm", ["layered_small_level_capped", "layered_small_level_gated",
                                 "hpa_level_capped", "keda_level_capped"])
def test_level_comparators_are_tuned_per_size(arm):
    for size in ("small", "medium", "large"):
        params = sim_backend.base_params(SYSTEMS[arm], size)
        assert ("target_rho" in params) or ("rps_per_replica" in params)


def test_every_level_yaml_is_valid_and_shares_seeds():
    for name in LEVEL_YAMLS:
        spec = config.load(EVAL_DIR / "experiments" / f"{name}.yaml")
        assert spec.shared_seeds, name
        assert all(s in SYSTEMS for s in spec.systems), name


def test_shed_count_identity():
    import tune_arms

    offered, shed, cost = 1000.0, 40.0, 2.5
    served = offered - shed
    s = shed / offered
    assert tune_arms.shed_requests(cost, s, cost / served) == pytest.approx(shed)
    assert tune_arms.shed_requests(cost, 0.0, cost / offered) == 0.0
