"""EQUIVALENCE_FAIR (exploratory) reproduces the frozen scorers' registered
ΔJ exactly and its Pareto and equivalence helpers do what they say."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent


def _mod(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_registered_differences_reproduce_exactly():
    eq = _mod("equivalence_fair")
    fair_j, retuned = _mod("analysis_fair_j"), _mod("analysis_retuned")
    data = eq.campaigns()
    df, family, _ = data["FAIR_J"]
    registered = fair_j.score(df, fair_j.PRIMARY)
    for hid, comp in family.items():
        d = fair_j.design_diffs(df, eq.TREATMENT, comp, "J")
        assert eq.equivalence(d)["mean"] == registered[hid]["mean"]
    df, family, _ = data["RETUNED"]
    registered = retuned.score(retuned.prepare(retuned.load()), retuned.PRIMARY)
    for hid, comp in family.items():
        d = fair_j.design_diffs(df, eq.TREATMENT, comp, "J")
        assert eq.equivalence(d)["mean"] == registered[hid]["mean"]


def test_nondominated_keeps_exactly_the_front():
    eq = _mod("equivalence_fair")
    pts = np.array([[1.0, 1.0], [2.0, 0.5], [2.0, 2.0], [1.0, 1.0]])
    assert eq.nondominated(pts).tolist() == [True, True, False, True]


def test_equivalence_margin_covers_its_interval():
    eq = _mod("equivalence_fair")
    d = np.random.default_rng(1).normal(0.01, 0.05, 60)
    e = eq.equivalence(d)
    assert e["margin"] >= max(abs(e["ci90"][0]), abs(e["ci90"][1]))
    assert eq.mde(d, 0.05 / 3) > eq.mde(d, 0.05) > 0
