"""Plant 2's mechanics in the simulator, and the two modes.

The first test is the R4 guard for this change: with the new mechanics off,
evaluate_step must equal, field for field and bit for bit, the model.py of
tag programme-1-final on random states, under the published form and under
every earlier override family.
"""

import dataclasses
import importlib.util
import random
import subprocess
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (HERE, REPO / "research" / "jcac_sim", REPO / "eval"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import model  # noqa: E402
from model import TenantConfig, TenantState, Demand  # noqa: E402

import plant  # noqa: E402


@pytest.fixture(autouse=True)
def published():
    model.set_model_form()
    model.set_economy()
    yield
    model.set_model_form()
    model.set_economy()


def _tagged_model() -> types.ModuleType:
    try:
        src = subprocess.run(["git", "show", "programme-1-final:research/jcac_sim/model.py"], cwd=REPO,
                             capture_output=True, check=True, timeout=30).stdout.decode("utf-8")
    except (OSError, subprocess.SubprocessError):
        pytest.skip("tag programme-1-final not available (shallow clone)")
    spec = importlib.util.spec_from_loader("model_tagged", loader=None)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["model_tagged"] = mod  # dataclasses resolve annotations through sys.modules
    exec(compile(src, "model_tagged", "exec"), mod.__dict__)
    return mod


def _cases(n: int, seed: int):
    rng = random.Random(seed)
    for _ in range(n):
        cls = rng.choice(list(model.SLO_CLASS_FACTOR))
        cfg = dict(tenant_id="t", slo_class=cls, hourly_budget_usd=rng.choice([0.5, 2.0, 5.0, 20.0]),
                   replica_max=16)
        state = dict(replicas=rng.randint(0, 16), cache_mb=rng.choice(model.CACHE_LEVELS_MB),
                     tier=rng.choice(model.TIERS))
        kinds = rng.sample(list(model.WORK_UNITS), rng.randint(1, 6))
        rps = {k: rng.choice([0.0, rng.uniform(0.1, 60.0)]) for k in kinds}
        yield cfg, state, dict(rps=rps, crud_base_ms=rng.uniform(5.0, 80.0))


FORMS = [
    {},
    {"congestion_exponent": 0.86, "p95_tail": (1.6909, 0.1303)},
    {"mixture_p95": True, "p95_tail": (1.6909, 0.1303)},
    {"wu_tier_factor": {"mid": 1.516, "large": 16.64}},
    {"replica_capacity_wu": 1123.0, "wu_ai_scale": 0.0, "crud_base_scale": 0.012, "cacheable_uniform": True,
     "tier_latency_ms": {"small": 262.0, "mid": 751.0, "large": 2156.0}},
    {"agent_latency_ms": {"small": 2000.0, "mid": 2500.0, "large": 3500.0}, "mixture_p95": True},
]


@pytest.mark.parametrize("form", FORMS, ids=[str(i) for i in range(len(FORMS))])
def test_new_mechanics_off_reproduce_the_tagged_model_bit_for_bit(form):
    old = _tagged_model()
    econ = {"tier_cost_usd_per_req": {"mid": 0.0015}, "cache_hit_max": 0.285, "cache_half_mb": 19.0}
    for mod in (model, old):
        mod.set_model_form(**form)
        mod.set_economy(**econ)
    for cfg, state, demand in _cases(400, seed=len(str(form))):
        new = model.evaluate_step(TenantConfig(**cfg), TenantState(**state), Demand(**demand))
        ref = old.evaluate_step(old.TenantConfig(**cfg), old.TenantState(**state), old.Demand(**demand))
        assert dataclasses.asdict(new) == dataclasses.asdict(ref)


def _gated(**form):
    model.set_model_form(admission_gate=True, **form)


def test_a_gate_with_room_changes_nothing():
    cfg, state = TenantConfig("t", hourly_budget_usd=1000.0), TenantState(replicas=3, cache_mb=128, tier="mid")
    demand = Demand(rps={"chat": 5.0, "crud_read": 20.0})
    free = model.evaluate_step(cfg, state, demand)
    _gated()
    assert dataclasses.asdict(model.evaluate_step(cfg, state, demand)) == dataclasses.asdict(free)


def test_a_binding_gate_conserves_requests_and_spends_the_budget_left():
    model.set_economy(tier_cost_usd_per_req={"large": 0.01})
    cfg, state = TenantConfig("t", hourly_budget_usd=2.0), TenantState(replicas=2, cache_mb=64, tier="large")
    demand = Demand(rps={"chat": 8.0, "agent": 2.0})
    free = model.evaluate_step(cfg, state, demand)
    _gated()
    m = model.evaluate_step(cfg, state, demand)
    share = model._admitted_share(cfg, state, demand)
    assert 0.0 < share < 1.0
    interval_hr = model.CONTROL_INTERVAL_S / 3600.0
    assert m.cost_usd == pytest.approx(cfg.hourly_budget_usd * interval_hr)
    offered_misses = free.cost_tier_usd / 0.01           # requests per interval, ungated
    billed = m.cost_tier_usd / 0.01
    assert billed + m.ai_shed_requests == pytest.approx(offered_misses)
    assert billed == pytest.approx(share * offered_misses)
    # Refused requests count against the SLO at the outage mark, even though
    # refusing them relieves the replicas the admitted ones queue on.
    assert m.ai_p95_ms >= model.NO_TIER_LATENCY_MS and m.excess > 0.0


def test_refused_requests_load_nothing():
    model.set_economy(tier_cost_usd_per_req={"large": 0.01})
    cfg, state = TenantConfig("t", hourly_budget_usd=1.0), TenantState(replicas=1, cache_mb=0, tier="large")
    demand = Demand(rps={"chat": 3.0})
    full = demand.work_units(0, "large")
    _gated()
    share = model._admitted_share(cfg, state, demand)
    assert share < 1.0
    assert demand.work_units(0, "large", share) == pytest.approx(share * full)


def test_attainment_is_the_share_within_target_and_falls_with_load():
    model.set_model_form(attainment_slo=True, mixture_p95=True)
    cfg = TenantConfig("t", slo_class="premium", hourly_budget_usd=50.0, replica_max=32)
    demand = Demand(rps={"chat": 4.0, "crud_read": 40.0, "crud_write": 10.0})
    viol = [model.evaluate_step(cfg, TenantState(replicas=r, cache_mb=256, tier="small"), demand).violation
            for r in (32, 8, 4, 2, 1)]
    assert all(0.0 <= v <= 1.0 for v in viol)
    assert all(a <= b + 1e-12 for a, b in zip(viol, viol[1:]))
    assert viol[0] < 0.05 and viol[-1] > 0.5


def test_unserved_requests_miss_the_target():
    model.set_model_form(attainment_slo=True)
    cfg = TenantConfig("t", slo_class="best-effort")
    m = model.evaluate_step(cfg, TenantState(replicas=8, cache_mb=0, tier="none"), Demand(rps={"chat": 2.0}))
    assert m.violation == pytest.approx(1.0)


def test_replica_price_and_crud_scale_and_resets():
    state, cfg, demand = TenantState(replicas=4, cache_mb=0, tier="small"), TenantConfig("t"), Demand(
        rps={"crud_read": 10.0, "chat": 1.0})
    base = model.evaluate_step(cfg, state, demand).cost_infra_usd
    model.set_economy(replica_cost_usd_hr=0.48)
    assert model.evaluate_step(cfg, state, demand).cost_infra_usd == pytest.approx(10 * base)
    model.set_economy()
    assert model.REPLICA_COST_USD_HR == 0.048
    model.set_model_form(wu_crud_scale=0.0)
    assert demand.work_units(0) == pytest.approx(1.0 * model.WORK_UNITS["chat"])
    snap = model.form_snapshot()
    assert snap[0][model._FORM_GLOBALS.index("WU_CRUD_SCALE")] == 0.0
    model.set_model_form()
    assert (model.WU_CRUD_SCALE, model.ADMISSION_GATE, model.ATTAINMENT_SLO) == (1.0, False, False)
    with pytest.raises(ValueError):
        model.set_economy(replica_cost_usd_hr=-1.0)
    assert model.REPLICA_COST_USD_HR == 0.048


def test_modes_refuse_missing_swept_factors_and_pass_the_harness_keys():
    from harness import config as hconfig

    prices = {"tier_cost_small": 1e-4, "tier_cost_mid": 3e-4, "tier_cost_large": 1e-3}
    for form, econ in (plant.mode_a(), plant.mode_a(**prices), plant.mode_b(), plant.mode_b(replica_cost_usd_hr=0.5)):
        assert set(form) <= hconfig.MODEL_FORM_KEYS
        assert set(econ) <= hconfig.ECONOMY_KEYS
    with pytest.raises(ValueError):
        plant.mode_a(wu_tier_large=3.0, **prices)


def test_tier_quality_override_moves_the_shortfall_and_resets():
    model.set_economy(tier_quality={"small": 0.306, "mid": 0.619, "large": 0.781})
    assert model.quality_shortfall("small") == pytest.approx(1 - 0.306 / 0.781)
    model.set_economy()
    assert model.TIER_QUALITY == model._DEFAULT_TIER_QUALITY
    with pytest.raises(ValueError):
        model.set_economy(tier_quality={"large": 0.0})
    assert model.TIER_QUALITY == model._DEFAULT_TIER_QUALITY


def _run(form, econ, workload="ai_uncacheable", steps=6):
    from harness import config as hconfig
    from harness import sim_backend

    spec = hconfig.ExperimentSpec(name="plant2_smoke", steps=steps, reps=1, systems=["jcac_converged"],
                                  workloads=[workload], tenant_mixes=["uniform"], cluster_sizes=["small"],
                                  model_form=form, economy=econ, shared_seeds=True, timeseries_reps=0)
    return sim_backend.execute(hconfig.expand(spec)[0])["metrics"]


def test_both_modes_run_through_the_harness():
    a = _run(*plant.mode_a(tier_cost_small=1e-4, tier_cost_mid=3e-4, tier_cost_large=1e-3))
    b = _run(*plant.mode_b(replica_cost_usd_hr=0.5))
    for m in (a, b):
        assert 0.0 <= m["mean_violation"] <= 1.0 and m["total_cost_usd"] > 0.0


def test_mode_a_reduces_to_the_live_fit_where_the_new_mechanics_do_not_bind():
    """At a live operating point (budget room, gateway far from congestion),
    mode A with the live cell's cache curve and the published tail prices
    and times every request exactly as the live-fit form does; only the
    violation definition differs."""
    live_form = {"replica_capacity_wu": 1122.5636, "wu_ai_scale": 0.0, "crud_base_scale": 0.0119708,
                 "cacheable_uniform": True, "tier_latency_ms": {"small": 261.73, "mid": 751.15, "large": 2155.73}}
    live_econ = {"cache_hit_max": 0.96243, "cache_half_mb": 4.0}
    cfg = TenantConfig("t", hourly_budget_usd=5.0)
    state, demand = TenantState(replicas=2, cache_mb=64, tier="small"), Demand(rps={"chat": 2.0, "crud_read": 30.0})
    model.set_model_form(**live_form)
    model.set_economy(**live_econ)
    ref = model.evaluate_step(cfg, state, demand)
    model.set_model_form(admission_gate=True, attainment_slo=True, **live_form)
    m = model.evaluate_step(cfg, state, demand)
    for f in ("cost_usd", "crud_p95_ms", "ai_p95_ms", "cache_hit_rate", "ai_shed_requests", "excess"):
        assert getattr(m, f) == getattr(ref, f), f
    form, _ = plant.mode_a(tier_cost_small=1e-4, tier_cost_mid=3e-4, tier_cost_large=1e-3)
    assert form["replica_capacity_wu"] == pytest.approx(1122.6, abs=0.05) and form["wu_ai_scale"] == 0.0


def test_replica_scaling_is_off_by_default_and_sublinear_when_set():
    assert model.REPLICA_SCALING_EXPONENT == 1.0 and model.pool_capacity_wu(3) == 3 * model.REPLICA_CAPACITY_WU
    try:
        model.set_model_form(replica_scaling_exponent=0.5)
        assert model.pool_capacity_wu(4) == pytest.approx(2 * model.REPLICA_CAPACITY_WU)
        wu = 1.5 * model.REPLICA_CAPACITY_WU
        sublinear = model.congestion(wu, 4)
        model.set_model_form()
        assert sublinear == pytest.approx(model.congestion(wu, 2))   # 4 pods at beta 0.5 = 2 linear pods
        for bad in (0.0, 1.5, float("nan")):
            with pytest.raises(ValueError):
                model.set_model_form(replica_scaling_exponent=bad)
            assert model.REPLICA_SCALING_EXPONENT == 1.0              # a refused form changes nothing
    finally:
        model.set_model_form()
    assert model.REPLICA_SCALING_EXPONENT == 1.0
