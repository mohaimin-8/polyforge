"""WP9 comparators: parameter spaces, every arm on both plant modes, the
tuning protocol (selection on the tuning half, reporting on the evaluation
half, the class rule) and the Kubernetes HPA's documented defaults."""

import contextlib
import dataclasses
import importlib.util
import io
import json
import math
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (HERE, REPO / "research" / "plant2", REPO / "research" / "jcac_sim", REPO / "eval"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import arms  # noqa: E402
import baselines  # noqa: E402
import k8s_hpa  # noqa: E402
import model  # noqa: E402
import plant  # noqa: E402
import protocol  # noqa: E402
from harness import config as hconfig  # noqa: E402
from harness import sim_backend  # noqa: E402
from harness.systems import SYSTEMS, base_params  # noqa: E402
from layer_control import gate_state  # noqa: E402
from model import Demand, TenantConfig, TenantState  # noqa: E402
from tunable_planner import TruncatedForecast, TunablePlanner  # noqa: E402

pytestmark = pytest.mark.skipif(not protocol.POPULATION.exists(), reason="population.json not generated")


@pytest.fixture(autouse=True)
def published():
    model.set_economy()
    model.set_model_form()
    yield
    model.set_economy()
    model.set_model_form()


def _run(mode: str, steps: int = 3, workload: str | None = None):
    form, econ = plant.mode_a() if mode == "A" else plant.mode_b()
    spec = hconfig.ExperimentSpec(
        name="wp9_test", steps=steps, reps=1, systems=["wp9"],
        workloads=[workload or protocol.split_names("tuning")[0]], tenant_mixes=["own"],
        cluster_sizes=["large"], shared_seeds=True, timeseries_reps=0, model_form=form, economy=econ)
    return hconfig.expand(spec)[0]


def _load_tune():
    """tune.py under its own module name: eval/baselines/tune.py is `tune`."""
    spec = importlib.util.spec_from_file_location("wp9_tune", HERE / "tune.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# Parameter spaces
# --------------------------------------------------------------------------

@pytest.mark.parametrize("quality_weight", [0.0, 2.0])
@pytest.mark.parametrize("name", arms.TUNED)
def test_spaces_are_valid_and_every_draw_stays_inside(name, quality_weight):
    params = arms.space(name, quality_weight)
    names = [p.name for p in params]
    assert len(names) == len(set(names)) and params
    assert ("quality_weight" in names) == (quality_weight > 0 and arms.arm(name).quality)
    for config in arms.sample_configs(name, 200, 7, quality_weight):
        assert set(config) == set(names)
        for p in params:
            assert p.contains(config[p.name]), (p.name, config[p.name])
            assert type(config[p.name]) in (int, float, str)


def test_sampling_is_seeded_per_arm_and_prefix_stable():
    a = arms.sample_configs("layers", 10, 3)
    assert a == arms.sample_configs("layers", 10, 3)
    assert a[:5] == arms.sample_configs("layers", 5, 3)
    assert a[0] == arms.defaults(arms.space("layers"))
    assert arms.sample_configs("layers", 10, 4)[1:] != a[1:]
    assert arms.sample_configs("layers", 3, 3, include_default=False)[0] != a[0]
    assert len({json.dumps(c, sort_keys=True) for c in a}) == 10


def test_bad_parameters_spaces_and_budgets_are_refused():
    with pytest.raises(ValueError):
        arms.controller_params("rules", {"bogus": 1})
    with pytest.raises(ValueError):
        arms.controller_params("rules", {}, budget="loose")
    with pytest.raises(ValueError):
        arms.controller_params("static_best", {})
    with pytest.raises(ValueError):
        arms.Param("x", "float", 5.0, 0.0, 1.0)
    with pytest.raises(ValueError):
        arms.Param("x", "float", 0.5, 0.0, 1.0, log=True)


def test_budget_mode_reaches_every_arm():
    assert arms.controller_params("joint", {}, "enforced")[1]["enforce_budget"] is True
    assert arms.controller_params("joint", {}, "plant")[1]["enforce_budget"] is False
    assert arms.controller_params("layers", {}, "enforced")[0] == "wp9_layers_capped_gate"
    assert arms.controller_params("rules", {}, "plant")[0] == "wp9_rules_capped"
    assert arms.controller_params("one_knob", {})[1]["knob_search"] == "knobwise"


def test_the_budget_gate_wraps_the_caps():
    configs = {"t0": TenantConfig("t0")}
    ctl = arms.build("wp9_rules_capped_gate", configs, **arms.defaults(arms.space("rules")))
    assert type(ctl).__name__ == "BudgetGate" and isinstance(ctl.inner, baselines.CapEnforced)
    assert type(ctl.inner.inner).__name__ == "RulesStackController" and ctl.name == "wp9_rules_capped_gate"


# --------------------------------------------------------------------------
# Registration hygiene
# --------------------------------------------------------------------------

def test_arm_system_leaves_nothing_behind_and_reads_no_tuned_yaml():
    original = baselines.make_baseline
    params = arms.defaults(arms.space("rules"))
    with arms.arm_system("rules", params) as key:
        spec = SYSTEMS[key]
        assert base_params(spec, "large") == spec.params
        assert baselines.make_baseline is not original
        configs = {"t0": TenantConfig("t0")}
        assert isinstance(baselines.make_baseline("hpa_capped", configs), baselines.CapEnforced)
        assert type(baselines.make_baseline("hpa", configs)) is baselines.HPAController
    assert baselines.make_baseline is original
    assert not [k for k in SYSTEMS if k.startswith(arms.PREFIX)]


# --------------------------------------------------------------------------
# Every arm on both plant modes
# --------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["A", "B"])
@pytest.mark.parametrize("name", arms.TUNED)
def test_every_arm_runs_three_steps_on_both_modes(name, mode):
    run = _run(mode)
    for config in arms.sample_configs(name, 2, 11):
        result = arms.execute(name, config, run)
        m = result["metrics"]
        assert result["tenants"] == 8 and m["steps"] == run.steps  # population: steps + 1 buckets
        terms = protocol.objective(m, result["tenants"], 2.0)
        assert all(math.isfinite(v) for v in terms.values()), (name, mode, config)
    assert not getattr(baselines.make_baseline, "wp9_dispatch", False)


@pytest.mark.parametrize("name", ["joint", "layers", "rules"])
def test_the_plant_only_budget_mode_runs(name):
    result = arms.execute(name, arms.defaults(arms.space(name)), _run("B"), budget="plant")
    assert math.isfinite(protocol.objective(result["metrics"], result["tenants"])["J"])


def test_the_planner_at_its_defaults_is_the_published_converged_planner():
    run = _run("B", steps=6)
    tuned = arms.execute("joint", arms.defaults(arms.space("joint")), run)
    published = sim_backend.execute(dataclasses.replace(run, system="jcac_converged"))
    assert tuned["metrics"] == published["metrics"]


def test_a_truncated_forecast_is_the_head_of_the_full_one():
    full, short = model.HORIZON_STEPS, 2
    a, b = TruncatedForecast(method="trend", steps=short), TruncatedForecast(method="trend", steps=full)
    for rate in (1.0, 3.0, 2.0, 5.0):
        a.observe(Demand(rps={"chat": rate}))
        b.observe(Demand(rps={"chat": rate}))
    assert len(a.horizon()) == short and a.horizon() == b.horizon()[:short]
    configs = {"t0": TenantConfig("t0")}
    for bad in (0, full + 1):
        with pytest.raises(ValueError):
            TunablePlanner(configs, horizon_steps=bad)
    assert TunablePlanner(configs, weight_beta=8.0).weights.beta == 8.0


# --------------------------------------------------------------------------
# Kubernetes HPA defaults (k8s_hpa.py cites the documentation)
# --------------------------------------------------------------------------

def test_the_tolerance_band_holds_the_count():
    assert k8s_hpa.metric_recommendation(10, 1.05) == 10
    assert k8s_hpa.metric_recommendation(10, 0.95) == 10
    assert k8s_hpa.metric_recommendation(10, 1.11) == 12
    assert k8s_hpa.metric_recommendation(10, 0.89) == 9
    # the published k8s_default arm has no band: it scales on a 5% excess
    hpa = baselines.HPAController({"t0": TenantConfig("t0", replica_max=20)}, target_rho=0.5, policy="k8s_default")
    state = TenantState(replicas=10, cache_mb=0, tier="small")
    demand = Demand(rps={"crud_read": 0.525 * 10 * model.REPLICA_CAPACITY_WU})
    assert hpa.plan({"t0": state}, {"t0": demand})["t0"].replicas == 11


def test_scale_up_adds_the_larger_of_100_percent_and_4_pods_per_15_s():
    h = k8s_hpa.PoolHistory()
    one, h1 = k8s_hpa.reconcile(h, 0.0, 1, 100.0, 1, 100)
    assert one == 5  # max(2 x 1, 1 + 4)
    two, h2 = k8s_hpa.reconcile(h1, 15.0, one, 100.0, 1, 100)
    assert two == 10  # the t=0 event is outside the 15 s period: max(2 x 5, 5 + 4)
    near, _ = k8s_hpa.reconcile(h1, 10.0, one, 100.0, 1, 100)
    assert near == 5  # inside the period the policies count from its start (1): max(2, 5)
    legacy, _ = k8s_hpa.reconcile(k8s_hpa.PoolHistory(), 0.0, 1, 100.0, 1, 100, behavior="unset")
    assert legacy == 4  # no behavior field: max(2 x current, 4) replicas


def test_scale_down_waits_out_the_300_s_window_then_drops_at_once():
    h, current, t = k8s_hpa.PoolHistory(), 8, 0.0
    current, h = k8s_hpa.reconcile(h, t, current, 1.0, 1, 16)  # at target: recommendation 8
    counts = []
    for _ in range(25):
        t += k8s_hpa.SYNC_PERIOD_S
        current, h = k8s_hpa.reconcile(h, t, current, 0.25, 1, 16)  # wants 2
        counts.append((t, current))
    held = [t for t, c in counts if c == 8]
    assert max(held) == 285.0 and dict(counts)[300.0] == 2  # the t=0 sample leaves the window at 300 s


def test_the_hpa_syncs_on_the_15_s_grid():
    configs = {"t0": TenantConfig("t0")}
    layer = k8s_hpa.K8sReplicaLayer(configs)
    states, demands = {"t0": TenantState()}, {"t0": Demand(rps={"chat": 1.0})}
    for _ in range(6):
        layer.replicas(states, demands)
    assert layer.syncs == 4  # ticks 0, 15, 30, 45 s in the intervals starting 0, 10, 30, 40 s


# --------------------------------------------------------------------------
# Budget gate
# --------------------------------------------------------------------------

def test_the_gate_trims_replicas_in_a_self_hosted_plant_and_never_sheds():
    form, econ = plant.mode_b()
    sim_backend._apply_economy(tuple(sorted(econ.items())))
    sim_backend._apply_model_form(tuple(sorted(form.items())))
    budget = 2.5 * model.REPLICA_COST_USD_HR
    config = TenantConfig("t0", hourly_budget_usd=budget, replica_max=16)
    want = TenantState(replicas=6, cache_mb=128, tier="small")
    out = gate_state(config, want, Demand(rps={"chat": 5.0}))
    assert out.tier == "small" and out.replicas == 2
    cap = budget * model.CONTROL_INTERVAL_S / 3600.0
    assert model.evaluate_step(config, out, Demand(rps={"chat": 5.0})).cost_usd <= cap


def test_the_gate_refuses_billed_model_calls_first_on_a_priced_plant():
    config = TenantConfig("t0", hourly_budget_usd=0.5)
    want = TenantState(replicas=2, cache_mb=128, tier="large")
    out = gate_state(config, want, Demand(rps={"chat": 2.0}))
    assert out == dataclasses.replace(want, tier="none")
    floor = TenantConfig("t0", hourly_budget_usd=0.5, tier_min="small")  # may not refuse: trims instead
    assert gate_state(floor, want, Demand(rps={"chat": 2.0})) == dataclasses.replace(want, replicas=1)


# --------------------------------------------------------------------------
# Protocol
# --------------------------------------------------------------------------

def test_the_objective_is_the_pilots_composite():
    m = {"steps": 119, "total_cost_usd": 3.3, "mean_violation": 0.1, "mean_jain": 0.9, "mean_quality_loss": 0.4}
    morris = m["total_cost_usd"] / (119 * 8) / 0.01 + 2.0 * m["mean_violation"] + 0.5 * (1.0 - m["mean_jain"])
    assert protocol.objective(m, 8)["J"] == pytest.approx(morris, rel=1e-12)
    assert protocol.objective(m, 8, 2.0)["J"] == pytest.approx(morris + 0.8, rel=1e-12)


def _rows(arm, table, phase="tuning"):
    return [{"phase": phase, "arm": arm, "config": c, "workload": w, "J": j}
            for c, by_wl in table.items() for w, j in by_wl.items()]


def test_selection_uses_the_tuning_half_and_reports_on_the_evaluation_half():
    classes = {"w1": "api_or_code", "w2": "api_or_code", "w3": "conversation"}
    table = {0: {"w1": 1.0, "w2": 1.0, "w3": 1.0},      # mean 1.00: best overall
             1: {"w1": 0.5, "w2": 0.5, "w3": 3.0},      # best on api_or_code
             2: {"w1": 2.0, "w2": 2.0, "w3": 0.1},      # best on conversation
             3: {"w1": 0.0, "w2": math.nan, "w3": 0.0}}  # failed once: never selected
    picks = protocol.select(_rows("a", table), classes)["a"]
    assert picks["overall"].config == 0 and picks["overall"].n == 3
    assert picks["per_class"]["api_or_code"].config == 1
    assert picks["per_class"]["conversation"].config == 2
    eval_classes = {**classes, "e1": "api_or_code", "e2": "conversation"}
    plan = protocol.evaluation_plan({"a": picks}, ["e1", "e2"], eval_classes)
    got = {(j["config"], j["workload"]): sorted(j["granularities"]) for j in plan}
    assert got == {(0, "e1"): ["overall"], (1, "e1"): ["per_class"],
                   (0, "e2"): ["overall"], (2, "e2"): ["per_class"]}
    assert not {j["workload"] for j in plan} & set(classes)
    with pytest.raises(ValueError):
        protocol.select(_rows("a", table, phase="evaluation"), classes)


def test_ties_go_to_the_lower_index_and_shared_picks_run_once():
    classes = {"w1": "api_or_code", "w2": "conversation"}
    picks = protocol.select(_rows("a", {0: {"w1": 1.0, "w2": 1.0}, 1: {"w1": 1.0, "w2": 1.0}}), classes)["a"]
    assert picks["overall"].config == 0 and picks["per_class"]["conversation"].config == 0
    plan = protocol.evaluation_plan({"a": picks}, ["e1"], {**classes, "e1": "conversation"})
    assert len(plan) == 1 and sorted(plan[0]["granularities"]) == ["overall", "per_class"]


def test_a_class_without_tuning_workloads_falls_back_to_the_overall_pick():
    picks = protocol.select(_rows("a", {0: {"w1": 1.0}, 1: {"w1": 0.5}}), {"w1": "api_or_code"})["a"]
    plan = protocol.evaluation_plan({"a": picks}, ["e2"], {"w1": "api_or_code", "e2": "conversation"})
    assert len(plan) == 1 and plan[0]["config"] == 1 and plan[0]["per_class_fallback"]


def test_the_class_rule_is_deterministic_and_fixed():
    names = protocol.split_names("tuning") + protocol.split_names("evaluation")
    first = [protocol.workload_class(w) for w in names]
    assert first == [protocol.workload_class(w) for w in names]
    assert set(first) == set(protocol.CLASSES)
    counts = {half: {c: sum(protocol.workload_class(w) == c for w in protocol.split_names(half))
                     for c in protocol.CLASSES} for half in ("tuning", "evaluation")}
    assert counts == {"tuning": {"api_or_code": 64, "conversation": 56},
                      "evaluation": {"api_or_code": 56, "conversation": 64}}
    assert protocol.class_of_shares({"conversation": 2.0, "api_or_code": 2.0}) == "api_or_code"
    with pytest.raises(ValueError):
        protocol.workload_class("pop999")


def test_short_runs_keep_every_class():
    picked = protocol.pick_workloads("tuning", 2)
    assert sorted(protocol.workload_class(w) for w in picked) == sorted(protocol.CLASSES)
    assert protocol.pick_workloads("tuning", 3) == protocol.pick_workloads("tuning", 3)
    assert len(protocol.pick_workloads("evaluation")) == 120


def test_a_class_short_of_its_quota_passes_the_rest_on_never_returning_fewer_than_asked():
    """The evaluation half holds 56 api_or_code workloads: 119 asks 60 of them (old rule: 115 returned)."""
    for n in (24, 112, 113, 119):
        picked = protocol.pick_workloads("evaluation", n)
        counts = {c: sum(protocol.workload_class(w) == c for w in picked) for c in protocol.CLASSES}
        assert len(picked) == len(set(picked)) == n and picked == sorted(picked)
        assert counts["api_or_code"] == min(56, n // 2 + n % 2)
    by_class = {c: [w for w in protocol.split_names("evaluation") if protocol.workload_class(w) == c]
                for c in protocol.CLASSES}
    for n in (1, 2, 3, 24, 112):                                   # both quotas fit: the rule as before
        quota = (n // 2 + n % 2, n // 2)
        assert protocol.pick_workloads("evaluation", n) == sorted(
            w for c, q in zip(protocol.CLASSES, quota) for w in by_class[c][:q])


# --------------------------------------------------------------------------
# tune.py
# --------------------------------------------------------------------------

def test_the_dry_run_counts_the_full_protocol():
    tune = _load_tune()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert tune.main(["--plant", "B", "--dry-run"]) == 0
    text = out.getvalue()
    assert "total: 38400 tuning runs + at most 1200 evaluation runs + 0 reference worlds" in text
    assert "api_or_code 64, conversation 56" in text


def test_a_tiny_tuning_run_writes_its_outputs(tmp_path):
    tune = _load_tune()
    with contextlib.redirect_stdout(io.StringIO()):
        status = tune.main(["--plant", "A", "--arms", "rules", "--configs", "2", "--tuning-workloads", "2",
                            "--evaluation-workloads", "2", "--steps", "3", "--workers", "1",
                            "--out", str(tmp_path)])
    assert status == 0
    [out] = list(tmp_path.glob("plantA_own_large_s3_enforced_*"))
    tuned = json.loads((out / "tuned.json").read_text(encoding="utf-8"))
    entry = tuned["arms"]["rules"]
    assert entry["overall"]["params"] == entry["configs"][entry["overall"]["config"]]
    assert set(entry["per_class"]) == set(protocol.CLASSES)
    text = (out / "report.md").read_text(encoding="utf-8")
    assert "REDUCED RUN" in text and "## Evaluation half, granularity `per_class`" in text
    assert (out / "runs.csv.gz").exists()


def test_runs_that_differ_in_any_setting_get_different_directories():
    tune = _load_tune()
    base = {"plant": "B", "mix": "own", "cluster_size": "large", "steps": 120, "budget": "enforced",
            "quality_weight": 0.0, "configs": 64, "seed": 1, "generated": "2026-11-13"}
    assert tune.run_tag(base) == tune.run_tag({**base, "generated": "2027-01-01"})
    assert tune.run_tag(base) != tune.run_tag({**base, "configs": 2})
    assert tune.run_tag(base).startswith("plantB_own_large_s120_enforced_")


# --------------------------------------------------------------------------
# rerender.py: a committed campaign's report from its records
# --------------------------------------------------------------------------

def _campaign_copy(tmp_path: Path, name: str = "pilot/plantA_own_large_s120_enforced_9e5754fd") -> Path:
    import shutil

    out = tmp_path / Path(name).name
    out.mkdir()
    for f in ("tuned.json", "runs.csv.gz", "references.csv.gz"):
        if (HERE / name / f).exists():
            shutil.copy(HERE / name / f, out / f)
    return out


def test_a_committed_campaign_report_is_rebuilt_from_its_records(tmp_path):
    import rerender

    campaign = _campaign_copy(tmp_path)
    out = rerender.rerender(campaign)
    committed = (HERE / "pilot" / campaign.name / "report.md").read_text(encoding="utf-8")
    assert out.read_text(encoding="utf-8") == committed


def test_rerender_refuses_records_that_select_other_configurations(tmp_path):
    import rerender

    campaign = _campaign_copy(tmp_path)
    tuned = json.loads((campaign / "tuned.json").read_text(encoding="utf-8"))
    arm = next(a for a, e in tuned["arms"].items() if e["overall"] is not None)
    tuned["arms"][arm]["overall"]["config"] += 1
    (campaign / "tuned.json").write_text(json.dumps(tuned), encoding="utf-8")
    with pytest.raises(SystemExit, match="select other configurations"):
        rerender.rerender(campaign)
    assert not (campaign / "report.md").exists()
