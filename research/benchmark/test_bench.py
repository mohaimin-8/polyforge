"""The benchmark on a tiny run: any controller plugs in, references bracket it."""

import dataclasses
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench  # noqa: E402

TINY = {"plant": "B", "mix": "own", "workloads": 1, "steps": 4, "reference_iterations": 3}


def test_a_static_controller_is_scored_against_the_references():
    import baselines

    df = bench.score(baselines.StaticController, {}, **TINY)
    row = df.iloc[0]
    assert len(df) == 1 and math.isfinite(row["J"])
    assert row["hindsight"] == min(row["ub"], row["static"])
    assert row["regret"] == pytest.approx(row["J"] - row["hindsight"]) and row["regret"] > 0
    assert row["lb0"] <= row["lb2"] + 1e-12 and row["lb2"] <= row["ub"] + 1e-12   # equal sums, other orders
    assert row["cap_overflow_step_share"] == 0.0 and row["clamp_violation_step_share"] == 0.0
    s = bench.summary(df)
    assert s["workloads"] == 1 and s["J"]["ci95"][0] <= s["J"]["mean"] <= s["J"]["ci95"][1]
    assert s["vs_lb2"]["mean"] == pytest.approx((row["J"] - row["lb2"]) / (row["static"] - row["lb2"]))


class AtTheCeiling:
    """Every tenant at its own replica ceiling: 8 x 16 replicas against the large cluster's 96."""

    def __init__(self, configs, **kw):
        self.configs = configs
        self.name = "at_the_ceiling"

    def plan(self, states, demands):
        return {t: dataclasses.replace(s, replicas=self.configs[t].replica_max) for t, s in states.items()}


def test_a_controller_that_leaves_the_references_constraints_is_flagged():
    """Plant 2's simulator applies any plan: the benchmark counts what the references never do."""
    row = bench.score(AtTheCeiling, {}, **TINY).iloc[0]
    assert row["cap_overflow_step_share"] == 1.0         # every scored step over the replica cap
    assert row["clamp_violation_step_share"] > 0.0       # 2 -> 16 replicas in one step


def test_a_wp9_arm_scores_as_tune_scores_it(tmp_path):
    import arms
    import protocol
    import tune

    params = arms.defaults(arms.space("rules"))
    controller, full = arms.controller_params("rules", params, "plant")
    df = bench.score(lambda configs, **kw: arms.build(controller, configs, **kw), full, **TINY)
    name = df.workload[0]
    args = tune.parse_args(["--plant", "B", "--mix", "own", "--steps", "4", "--out", str(tmp_path)])
    result = arms.execute("rules", params, tune.build_runs(args, [name])[name], "plant")
    assert df.J[0] == protocol.objective(result["metrics"], result["tenants"], 0.0)["J"]


def test_the_summary_is_wp9s_ratio_of_means_never_a_ratio_per_workload():
    """The second workload's static plan is its hindsight plan (a zero gap, as on many published
    workloads): a per-workload share is undefined there, the ratio of means is not."""
    df = pd.DataFrame({"workload": ["w1", "w2"], "J": [0.30, 0.20], "static": [0.25, 0.21], "ub": [0.26, 0.21],
                       "lb2": [0.20, 0.19], "lb0": [0.19, 0.18]})
    df["regret"] = df.J - df[["ub", "static"]].min(axis=1)
    s = bench.summary(df)
    for key, low in (("vs_lb2", "lb2"), ("vs_lb0", "lb0")):
        expect = (df.J.mean() - df[low].mean()) / (df.static.mean() - df[low].mean())
        assert s[key]["mean"] == pytest.approx(expect)
        assert s[key]["ci95"][0] <= s[key]["mean"] <= s[key]["ci95"][1]
    assert s == bench.summary(df)                                        # seeded
    flat = df.assign(static=df.lb2)                                      # static no worse than the bound
    assert bench.summary(flat)["vs_lb2"] is None


def test_cap_overflow_counts_steps_over_a_cap_never_at_it():
    limits = type("Limits", (), {"replicas": 4, "cache_mb": 256})()
    rows = [{"step": 1, "replicas": 2, "cache_mb": 128}, {"step": 1, "replicas": 2, "cache_mb": 128},
            {"step": 2, "replicas": 3, "cache_mb": 128}, {"step": 2, "replicas": 2, "cache_mb": 128},
            {"step": 3, "replicas": 1, "cache_mb": 256}, {"step": 3, "replicas": 1, "cache_mb": 128}]
    assert bench.cap_overflow_share(rows, limits) == pytest.approx(2 / 3)
    with pytest.raises(ValueError):
        bench.cap_overflow_share([], limits)


def test_the_command_line_takes_every_workload_as_zero(monkeypatch, capsys):
    seen = {}

    def fake(factory, params, plant, mix, workloads, steps):
        seen["workloads"] = workloads
        return pd.DataFrame({"workload": ["w"], "J": [0.2]})

    monkeypatch.setattr(bench, "score", fake)
    assert bench.main(["--controller", "baselines:StaticController", "--workloads", "0"]) == 0
    assert seen["workloads"] is None and '"workloads": 1' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        bench.main(["--controller", "baselines:StaticController", "--workloads", "-1"])


def test_the_registry_is_restored():
    import baselines
    from harness.systems import SYSTEMS

    before = baselines.make_baseline
    with bench.registered(baselines.StaticController, {}) as key:
        assert key in SYSTEMS and baselines.make_baseline is not before
    assert bench.NAME not in SYSTEMS and baselines.make_baseline is before


def test_bootstrap_is_seeded_and_brackets_the_mean():
    x = np.arange(20.0)
    lo, hi = bench.bootstrap(x)
    assert (lo, hi) == bench.bootstrap(x) and lo < x.mean() < hi


def test_controllers_load_by_module_and_class():
    assert bench.load_factory("baselines:HPAController").__name__ == "HPAController"
