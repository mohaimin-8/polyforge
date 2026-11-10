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
    try:
        sim_backend._apply_model_form(run.model_form)
        assert model.AGENT_LATENCY == MONO
    finally:
        model.set_model_form()


@pytest.mark.parametrize("key", ["agent_latency_small", "agent_latency_large"])
def test_a_zero_latency_is_refused_when_the_campaign_loads(tmp_path, key):
    """config.load accepted 0 (it checks only for negatives), so the error
    surfaced at the first run instead of before the campaign started."""
    text = (EVAL_DIR / "experiments" / "matrix_level_mono.yaml").read_text(encoding="utf-8")
    line = next(x for x in text.splitlines() if x.strip().startswith(key + ":"))
    bad = tmp_path / "bad.yaml"
    bad.write_text(text.replace(line, line.split(":")[0] + ": 0"), encoding="utf-8")
    with pytest.raises(ValueError, match="positive"):
        config.load(bad)


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


def test_the_outage_price_is_charged_on_the_horizon_the_cost_term_uses():
    """With a separate cost horizon (risk_cost_at_point) the cost term is the
    point forecast; the outage price must be charged on that horizon too,
    not on the risk-inflated one the SLO is judged on."""
    tids, buckets, configs, limits = workloads.build("agentic", "uniform", "small", 3, 8)
    tid = tids[0]
    point = {t: [buckets[0][t]] * 3 for t in tids}
    risky = {t: [buckets[len(buckets) // 2][t]] * 3 for t in tids}
    chosen = {t: model.TenantState(replicas=1, cache_mb=0, tier="small") for t in tids}
    shed = replace(chosen[tid], tier="none")

    def score(**kw):
        ctl = controller.JCACController(configs, limits=limits, anchor_moves=True, converge_sweeps=True, **kw)
        others = ctl._others(tid, chosen, risky, point)
        return ctl, ctl._candidate_score(tid, shed, chosen[tid], others, risky, point, float("inf"))

    _, unpriced = score()
    ctl, priced = score(shed_price_tier="small")
    billed = model.evaluate_step(configs[tid], chosen[tid], point[tid][0]).cost_tier_usd
    provisioned = model.evaluate_step(configs[tid], chosen[tid], risky[tid][0]).cost_tier_usd
    assert billed != pytest.approx(provisioned, rel=1e-3)  # the two horizons really differ
    assert priced - unpriced == pytest.approx(ctl.weights.alpha * billed / controller.COST_SCALE_USD, rel=1e-9)


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


class _FixedPlan:
    """An inner baseline that always wants the same plan, so each tenant's
    budget alone decides which of the gate's paths runs."""

    name = "fixed"

    def __init__(self, configs, plan):
        self.configs, self._plan = configs, plan

    def plan(self, states, demands):
        return dict(self._plan)


def test_the_gate_takes_each_path_its_budget_calls_for():
    """Every path of BudgetGated, each on purpose: pass the wanted step
    (affordable), refuse model calls with replicas and cache kept, trim
    replicas to the closest affordable count, or take the cheapest reachable
    step when nothing is affordable. Caps sit at midpoints between bills,
    so no assertion rides on a rounding edge."""
    tids, buckets, configs, _ = workloads.build("agentic", "uniform", "small", 5, 8)
    demand = buckets[0]
    want = model.TenantState(replicas=3, cache_mb=128, tier="small")
    states = {t: model.TenantState(replicas=3, cache_mb=128, tier="small") for t in tids}
    gated = replace(want, tier="none")

    def bill(t, state):
        return model.evaluate_step(configs[t], state, demand[t]).cost_usd

    paths = ("pass", "refuse", "trim", "broke")
    budgets, expected = {}, {}
    for i, t in enumerate(tids):
        path = paths[i % len(paths)]
        assert bill(t, want) > bill(t, gated) > bill(t, replace(gated, replicas=2)) > bill(t, replace(gated, replicas=1))
        cap = {"pass": 2.0 * bill(t, want),
               "refuse": 0.5 * (bill(t, want) + bill(t, gated)),
               "trim": 0.5 * (bill(t, gated) + bill(t, replace(gated, replicas=2))),
               "broke": 0.5 * bill(t, replace(gated, replicas=1))}[path]
        budgets[t] = cap * 3600.0 / model.CONTROL_INTERVAL_S
        expected[t] = {"pass": want, "refuse": gated, "trim": replace(gated, replicas=2),
                       "broke": replace(gated, replicas=max(configs[t].replica_min, 1))}[path]
    priced = {t: replace(c, hourly_budget_usd=budgets[t]) for t, c in configs.items()}
    got = baselines.BudgetGated(_FixedPlan(priced, {t: want for t in tids})).plan(states, demand)
    assert got == expected


def test_the_gate_is_transparent_when_every_budget_is_ample():
    tids, buckets, configs, _ = workloads.build("agentic", "uniform", "small", 5, 8)
    states = {t: model.TenantState(replicas=2, cache_mb=128, tier="small") for t in tids}
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


@pytest.mark.parametrize("argv", [
    ["--level", "--level-gpu"],
    ["--quality", "--level"],
    ["--level-arms", "layered_small_level_gated"],
    ["--level-gpu", "--level-arms", "layered_small_level_gated"],
    ["--quality-arms", "hpa_quality_capped"],
    ["--arms", "hpa_fair", "--level"],
])
def test_the_tuner_refuses_flags_it_would_silently_ignore(argv, monkeypatch):
    """`--level-gpu` used to win over `--level` and drop `--level-arms`;
    every such combination now stops before any tuning or file write. The
    work functions are stubbed so a regression fails here instead of
    re-tuning and rewriting tuned_arms.yaml."""
    import tune_arms

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("the tuner started work on an ambiguous command line")

    for name in ("tune_level", "tune_quality", "score_controller", "_write_tuned"):
        monkeypatch.setattr(tune_arms, name, must_not_run)
    with pytest.raises(SystemExit) as stop:
        tune_arms.main(argv)
    assert stop.value.code == 2


def test_shed_count_identity():
    import tune_arms

    offered, shed, cost = 1000.0, 40.0, 2.5
    served = offered - shed
    s = shed / offered
    assert tune_arms.shed_requests(cost, s, cost / served) == pytest.approx(shed)
    assert tune_arms.shed_requests(cost, 0.0, cost / offered) == 0.0


def test_the_lag_follow_up_arm_changes_only_the_switching_penalty():
    ratchet = dict(SYSTEMS["jcac_level_ratchet"].params)
    level = dict(SYSTEMS["jcac_level"].params)
    assert level.pop("switch_penalty") == controller.SWITCH_PENALTY_HALF_REPLICA
    assert "switch_penalty" not in ratchet and ratchet == level
    for name in ("matrix_level_lag2", "matrix_level_inst2"):
        spec = config.load(EVAL_DIR / "experiments" / f"{name}.yaml")
        assert spec.shared_seeds and all(s in SYSTEMS for s in spec.systems)


def test_level2_arms_and_experiments():
    dec = dict(SYSTEMS["jcac_level_ratchet_decoupled"].params)
    assert dec.pop("knob_search") == "decoupled" and dec == dict(SYSTEMS["jcac_level_ratchet"].params)
    for arm in ("layered_costaware_gpu_gated", "layered_costaware_gpu_capped", "layered_small_gpu_gated",
                "hpa_gpu_capped"):
        for size in ("small", "medium", "large"):
            assert "target_rho" in sim_backend.base_params(SYSTEMS[arm], size)
    for name in ("matrix_level2", "matrix_level2_mono", "matrix_level2_lag", "matrix_level2_heldout",
                 "matrix_level2_trace", "matrix_level2_gpu"):
        spec = config.load(EVAL_DIR / "experiments" / f"{name}.yaml")
        assert spec.shared_seeds and all(s in SYSTEMS for s in spec.systems), name
