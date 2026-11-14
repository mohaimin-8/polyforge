"""The benchmark on a tiny run: any controller plugs in, references bracket it."""

import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench  # noqa: E402


def test_a_static_controller_is_scored_against_the_references():
    import baselines

    df = bench.score(baselines.StaticController, {}, plant="B", mix="own", workloads=1, steps=4,
                     reference_iterations=3)
    row = df.iloc[0]
    assert len(df) == 1 and math.isfinite(row["J"])
    assert row["hindsight"] == min(row["ub"], row["static"])
    assert row["regret"] == pytest.approx(row["J"] - row["hindsight"]) and row["regret"] > 0
    s = bench.summary(df)
    assert s["workloads"] == 1 and s["J"]["ci95"][0] <= s["J"]["mean"] <= s["J"]["ci95"][1]


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
