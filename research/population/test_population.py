"""Population workloads through the harness: own tenants, plant-relative
budgets, deterministic demand, and the validator's rules."""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (REPO / "research" / "jcac_sim", REPO / "eval", REPO / "research" / "plant2"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import model  # noqa: E402
from harness import config as hconfig  # noqa: E402
from harness import population_workloads as pw  # noqa: E402
from harness import workloads  # noqa: E402

pytestmark = pytest.mark.skipif(not pw.POPULATION.exists(), reason="population.json not generated")


@pytest.fixture(autouse=True)
def published():
    model.set_model_form()
    model.set_economy()
    yield
    model.set_model_form()
    model.set_economy()


def test_a_population_workload_builds_its_own_tenants_deterministically():
    name = pw.names("evaluation")[0]
    a = workloads.build(name, "own", "large", 11, 20)
    b = workloads.build(name, "own", "large", 11, 20)
    assert a[0] == b[0] and len(a[0]) == 8 and len(a[1]) == 21
    assert [d["t00"].rps for d in a[1]] == [d["t00"].rps for d in b[1]]
    assert all(set(d[t].rps) == {"chat"} for d in a[1] for t in a[0])
    assert {cfg.slo_class for cfg in a[2].values()} <= set(model.SLO_CLASS_FACTOR)


def test_budgets_follow_the_plant():
    import plant

    name = pw.names("tuning")[0]
    form, econ = plant.mode_b()
    from harness.sim_backend import _apply_economy, _apply_model_form

    _apply_economy(tuple(sorted(econ.items())))
    _apply_model_form(tuple(sorted(form.items())))
    b_budget = workloads.build(name, "own", "large", 1, 5)[2]["t00"].hourly_budget_usd
    model.set_model_form()
    model.set_economy()
    published_budget = workloads.build(name, "own", "large", 1, 5)[2]["t00"].hourly_budget_usd
    assert b_budget > 0 and published_budget > 0 and b_budget != published_budget


def test_the_validator_keeps_population_workloads_apart(tmp_path):
    name = pw.names()[0]
    good = tmp_path / "good.yaml"
    good.write_text(f"name: x\nworkloads: [{name}]\ntenant_mixes: [own]\nsystems: [jcac]\n", encoding="utf-8")
    hconfig.load(good)
    for body in (f"name: x\nworkloads: [{name}, agentic]\ntenant_mixes: [own]\n",
                 f"name: x\nworkloads: [{name}]\ntenant_mixes: [uniform]\n",
                 "name: x\nworkloads: [pop999]\ntenant_mixes: [own]\n"):
        bad = tmp_path / "bad.yaml"
        bad.write_text(body, encoding="utf-8")
        with pytest.raises(ValueError):
            hconfig.load(bad)
