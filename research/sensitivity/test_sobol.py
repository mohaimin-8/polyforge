"""The Sobol stage's wiring: SALib's indices on a known function, the factor
problem, and common random numbers across plants."""

import numpy as np
import pytest

import sobol


def test_indices_recover_the_ishigami_function():
    from SALib.sample import sobol as sample
    from SALib.test_functions import Ishigami

    prob = {"num_vars": 3, "names": ["x1", "x2", "x3"], "bounds": [[-np.pi, np.pi]] * 3}
    X = sample.sample(prob, 1024, calc_second_order=False, seed=1)
    t = sobol.indices(Ishigami.evaluate(X), prob).set_index("factor")
    # analytic: S1 = 0.314, 0.442, 0; ST = 0.558, 0.442, 0.244
    assert t.loc["x1", "S1"] == pytest.approx(0.314, abs=0.06) and t.loc["x2", "S1"] == pytest.approx(0.442, abs=0.06)
    assert t.loc["x3", "ST"] == pytest.approx(0.244, abs=0.06) and abs(t.loc["x3", "S1"]) < 0.06


def test_the_problem_uses_each_factors_wp5_range_and_refuses_unknown_names():
    fac = sobol.mp.factors()
    p = sobol.problem([fac[0].name, fac[1].name])
    assert p["bounds"] == [list(fac[0].sweep), list(fac[1].sweep)]
    with pytest.raises(ValueError):
        sobol.problem(["no_such_factor"])


def test_every_plant_draws_the_same_demand_per_workload():
    fac = sobol.mp.factors()
    prob = sobol.problem([fac[0].name])
    X = np.array([[fac[0].sweep[0]], [fac[0].sweep[1]]])
    jobs = sobol.jobs(X, prob)
    seeds = {}
    for pid, _, run in jobs:
        seeds.setdefault(run.workload, set()).add(run.seed)
    assert all(len(s) == 1 for s in seeds.values())       # one seed per workload across plants and arms
    assert {pid for pid, _, _ in jobs} == {0, 1}


def test_a_new_morris_screen_never_overwrites_the_committed_pilot():
    pilot, plan = sobol.mp.outputs(10, False), sobol.mp.outputs(20, True)
    assert [p.name for p in pilot.values()] == ["morris_runs.csv.gz", "MORRIS_PILOT.md", "morris_design.json"]
    assert [p.name for p in plan.values()] == ["morris_runs_r20_crn.csv.gz", "MORRIS_r20_crn.md",
                                               "morris_design_r20_crn.json"]
    others = [sobol.mp.outputs(10, True), sobol.mp.outputs(20, False)]
    names = [p for o in (pilot, plan, *others) for p in o.values()]
    assert len(names) == len(set(names))


def test_a_resumed_sobol_run_cannot_mix_two_factor_choices():
    a = sobol.design_tag(["cache_hit_max", "replica_capacity_wu"])
    assert a == sobol.design_tag(["cache_hit_max", "replica_capacity_wu"]) and a.startswith("k2_")
    assert a != sobol.design_tag(["replica_capacity_wu", "cache_hit_max"])
    assert a != sobol.design_tag(["cache_hit_max", "congestion_exponent"])
