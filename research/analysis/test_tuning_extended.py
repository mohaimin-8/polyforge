"""TUNING_EXTENDED's optimum and edge logic, and the record it builds from the
committed sweeps."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent


def _mod():
    spec = importlib.util.spec_from_file_location("ate", HERE / "analysis_tuning_extended.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_an_optimum_at_the_last_value_swept_is_an_edge():
    mod = _mod()
    df = pd.DataFrame({"target_rho": [0.1, 0.2, 0.3], "mean_objective": [0.5, 0.6, 0.7]})
    assert mod.edges(df, mod.optimum(df)) == ["target_rho"]


def test_an_interior_optimum_is_not_an_edge():
    mod = _mod()
    df = pd.DataFrame({"target_rho": [0.1, 0.2, 0.3], "mean_objective": [0.6, 0.5, 0.7],
                       "cost_norm": 0.0, "violation": 0.0, "unfair": 0.0})
    assert mod.edges(df, mod.optimum(df)) == []
    assert mod.params_of(df) == ["target_rho"]


def test_a_parameter_swept_at_one_value_is_never_an_edge():
    mod = _mod()
    df = pd.DataFrame({"a": [1, 1], "b": [0.1, 0.2], "mean_objective": [0.6, 0.5]})
    assert mod.edges(df, mod.optimum(df)) == ["b"]


def test_an_optimum_at_a_parameters_floor_is_not_an_edge():
    mod = _mod()
    df = pd.DataFrame({"stable_intervals": [1, 3, 6], "mean_objective": [0.5, 0.6, 0.7]})
    assert mod.edges(df, mod.optimum(df)) == []


def test_the_record_builds_from_the_committed_sweeps(monkeypatch, tmp_path):
    mod = _mod()
    monkeypatch.setenv("POLYFORGE_ANALYSIS_OUT", str(tmp_path))
    text = mod.build()
    for size in mod.SIZES:
        assert f"| {size} |" in text
    for name in mod.MEDIUM:
        assert f"| `{name}` |" in text
    assert "Exploratory" in text
