"""tb_planner: the arms' planners on the testbed's exact knobs, economy and form."""

import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent / "actuator"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import tb_planner as tbp  # noqa: E402

import actuate  # noqa: E402
import arms  # noqa: E402
import model  # noqa: E402
import protocol  # noqa: E402
from controller import ClusterLimits  # noqa: E402
from model import Demand, TenantState  # noqa: E402

STEPS = 60


def make_testbed(scaling=None) -> tbp.Testbed:
    tiers = {"small": tbp.TierForm(1, 1, 3, 0.8, 0.1, 1.8, 2.8, 0.47, scaling),
             "mid": tbp.TierForm(2, 1, 2, 0.8, 0.1, 1.8, 3.5, 0.31, scaling)}
    return tbp.Testbed(tiers, dict(tbp.PINS), 3 * 2.8 * 1000, 0.1315, 5, 0.2, 1.2)


def demand(scale: float = 1.0) -> dict:
    out = {}
    for k in range(STEPS):
        out[(k, "t00")] = int(round(scale * (2 + 6 * k / STEPS)))
        out[(k, "t01")] = int(round(scale * (1 + 2 * k / STEPS)))
    return out


def defaults(arm: str) -> dict:
    return arms.defaults(arms.space(arm))


def world() -> tuple:
    return (model.form_snapshot(), model.CACHE_LEVELS_MB, dict(model.SLO_BASE_MS), model.REPLICA_COST_USD_HR,
            model.CACHE_HIT_MAX, model.CACHE_HALF_MB)


@pytest.mark.parametrize("arm", tbp.PLANNER_ARMS)
def test_both_arms_plan_whole_pods_inside_the_core_budget_and_the_actuator_takes_the_plan(arm):
    tb = make_testbed()
    r = tbp.plan_run(arm, defaults(arm), demand(), tb, STEPS)
    assert len(r["path"]) == STEPS
    for states in r["path"]:
        tbp.check_states(tb, states)
    plan = tbp.decisions(r["path"], tb)
    bounds = actuate.Bounds.load(actuate.DEFAULT_BOUNDS)
    parsed = [actuate.parse_decision(d, bounds) for d in plan]
    assert plan[0]["t_s"] == 0.0 and set(plan[0]["replicas"]) == {"small", "mid"}
    assert all(p.t_s == d["step"] * tbp.BIN_S for p, d in zip(parsed, plan))
    o = r["outcome"]
    assert 0.0 <= o["attainment"] <= 1.0 and o["J"] > 0 and o["requests"] == sum(
        n for (k, _), n in demand().items() if k >= tbp.WARMUP_STEPS)


def test_more_load_buys_more_pods():
    tb = make_testbed()
    pods = {}
    for scale in (0.3, 3.0):
        path = tbp.plan_run("joint", defaults("joint"), demand(scale), tb, STEPS)["path"]
        pods[scale] = np.mean([s["t00"].replicas + s["t01"].replicas for s in path[tbp.WARMUP_STEPS:]])
    assert pods[3.0] > pods[0.3]


@pytest.mark.parametrize("beta", [None, 0.6])
def test_cores_as_replicas_give_each_tier_its_pod_capacity(beta):
    tb = make_testbed(beta)
    with tbp.testbed_world(tb):
        for tier, t in tb.tiers.items():
            tbp.use_tier(tb, tier)
            for pods in range(1, t.pod_max + 1):
                want = t.capacity_per_pod_rps * pods ** (1.0 if beta is None else beta)
                assert model.pool_capacity_wu(pods * t.cores_per_pod) == pytest.approx(want, rel=1e-12)
            # a chat request is one work unit, so capacity is in requests per second
            assert Demand(rps={"chat": 0.5}).work_units(0, tier) == pytest.approx(0.5)


def test_the_published_world_is_back_after_a_plan():
    before = world()
    tbp.plan_run("per_knob", defaults("per_knob"), demand(), make_testbed(0.6), STEPS, with_index=True)
    assert world() == before


def test_each_tenant_is_projected_under_its_own_tier():
    tb = make_testbed()
    configs = tbp.tenant_configs(tb)
    s = TenantState(replicas=2, cache_mb=0, tier="mid")
    horizon = [Demand(rps={"chat": 0.25})]
    with tbp.testbed_world(tb):
        ctl = tbp.TestbedPlanner(configs, tb, limits=ClusterLimits(cache_mb=10**6, replicas=5), anchor_moves=True,
                                 converge_sweeps=True, knob_search="joint")
        cost, violation, _ = ctl._project("t01", s, horizon)
        tbp.use_tier(tb, "mid")
        as_mid = model.evaluate_step(configs["t01"], s, horizon[0])
        tbp.use_tier(tb, "small")
        as_small = model.evaluate_step(configs["t01"], s, horizon[0])
    assert (cost, violation) == (as_mid.cost_usd, as_mid.violation)
    assert as_small.violation < as_mid.violation   # small's capacity per core is higher: the forms differ


def test_no_candidate_is_a_part_of_a_pod():
    tb = make_testbed()
    configs = tbp.tenant_configs(tb)
    with tbp.testbed_world(tb):
        ctl = tbp.TestbedPlanner(configs, tb, limits=ClusterLimits(cache_mb=10**6, replicas=5), anchor_moves=True,
                                 converge_sweeps=True, knob_search="decoupled")
        for base in (TenantState(2, 0, "mid"), TenantState(4, 1, "mid")):
            lattice = list(ctl._lattice("t01", base))
            assert lattice and all(c.replicas % 2 == 0 for c in lattice)
            for knob in ("replicas", "cache", "tier"):
                assert all(c.replicas % 2 == 0 for c in ctl._knob_candidates("t01", base, base, knob))
        assert {c.replicas for c in ctl._lattice("t00", TenantState(2, 0, "small"))} == {1, 2, 3}


def test_decisions_are_the_full_posture_then_only_changes():
    tb = make_testbed()
    a = {"t00": TenantState(1, 0, "small"), "t01": TenantState(2, 0, "mid")}
    b = {"t00": TenantState(2, 0, "small"), "t01": TenantState(2, 0, "mid")}
    c = {"t00": TenantState(2, 0, "small"), "t01": TenantState(4, 1, "mid")}
    plan = tbp.decisions([a, a, b, b, c], tb)
    assert plan == [
        {"step": 0, "t_s": 0.0, "replicas": {"small": 1, "mid": 1},
         "tenants": {"t00": {"model_tier": "small", "cache_size_mb": 0}, "t01": {"model_tier": "mid", "cache_size_mb": 0}}},
        {"step": 2, "t_s": 20.0, "replicas": {"small": 2}},
        {"step": 4, "t_s": 40.0, "replicas": {"mid": 2}, "tenants": {"t01": {"cache_size_mb": 1}}},
    ]


def test_the_hit_curve_fit_recovers_a_known_curve():
    hmax, half = tbp.fit_hit_curve(tbp.CACHE_LEVELS_MB, lambda c: 0.21 * c / (c + 1.19))
    assert hmax == pytest.approx(0.21, rel=0.01) and half == pytest.approx(1.19, rel=0.02)


def test_j_is_the_protocols_objective_for_two_tenants():
    v = np.array([[0.0, 0.5], [0.2, 0.2], [1.0, 1.0]])
    got = tbp.j_of(0.36, 3, v)
    jain = np.mean([model.jain_index([1.0, 0.5]), 1.0, 1.0])
    want = protocol.objective({"total_cost_usd": 0.36, "steps": 3, "mean_violation": v.mean(), "mean_jain": jain,
                               "mean_quality_loss": 0.0}, 2)
    assert got["J"] == pytest.approx(want["J"], rel=1e-15) and got["mean_jain"] == pytest.approx(jain)


def test_the_index_is_computed_on_the_testbeds_box():
    tb = make_testbed()
    r = tbp.plan_run("joint", defaults("joint"), demand(2.0), tb, STEPS, with_index=True)
    rows = r["index_rows"]
    assert len(rows) == 2 * (STEPS - 1)
    for row in rows:
        assert row["n_points"] <= 5 * 3   # whole-pod replica counts x neighbouring cache levels x one tier
        assert row["theorem1_applies"] in (True, False)
    s = tbp.index_summary(rows)
    assert s["decisions"] == len(rows) and s["mean_index"] >= 0.0
