"""RESULTS_DOMINANCE must state what non-dominance means, and how stable it is.

Audit 2026-09-26: the record said a non-dominated arm "cannot be beaten by
any weighting" of the objectives. That is false -- non-dominance only means no
other arm is at least as good on every objective at once; a baseline better
on violation still wins under a violation-heavy weighting. And the count was
taken on 5-rep cell means with a 1e-12 tolerance, so it carried no
uncertainty at all.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent


def _mod(monkeypatch, tmp_path):
    monkeypatch.setenv("POLYFORGE_ANALYSIS_OUT", str(tmp_path))
    spec = importlib.util.spec_from_file_location("dom", HERE / "analysis_dominance.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_record_does_not_claim_immunity_to_weighting(monkeypatch, tmp_path):
    mod = _mod(monkeypatch, tmp_path)
    assert mod.main() == 0
    text = (tmp_path / mod.RECORD).read_text(encoding="utf-8").lower()
    assert "cannot be beaten by **any** weighting" not in text
    assert "can put a baseline ahead of it" not in text
    assert "not the winner under every weighting" in text


def test_a_trade_off_is_non_dominated_yet_loses_under_some_weighting(monkeypatch, tmp_path):
    mod = _mod(monkeypatch, tmp_path)
    cheap = {"total_cost_usd": 1.0, "mean_violation": 0.10, "unfair": 0.0}
    safe = {"total_cost_usd": 2.0, "mean_violation": 0.01, "unfair": 0.0}
    assert not mod.dominates(safe, cheap) and not mod.dominates(cheap, safe)
    heavy_violation = lambda r: r["total_cost_usd"] + 100 * r["mean_violation"]  # noqa: E731
    assert heavy_violation(safe) < heavy_violation(cheap)


def test_rep_bootstrap_separates_stable_from_unstable_cells(monkeypatch, tmp_path):
    mod = _mod(monkeypatch, tmp_path)
    rows = []
    for rep in range(5):
        # cell A: jcac clearly cheaper and no worse -> stably non-dominated
        rows += [{"system": "jcac", "workload": "A", "tenant_mix": "u", "cluster_size": "s", "rep": rep,
                  "total_cost_usd": 1.0, "mean_violation": 0.01, "unfair": 0.0},
                 {"system": "hpa", "workload": "A", "tenant_mix": "u", "cluster_size": "s", "rep": rep,
                  "total_cost_usd": 2.0, "mean_violation": 0.01, "unfair": 0.0}]
        # cell B: identical means but noisy reps -> dominance flips resample to resample
        rows += [{"system": "jcac", "workload": "B", "tenant_mix": "u", "cluster_size": "s", "rep": rep,
                  "total_cost_usd": 1.0 + (0.5 if rep % 2 else -0.5), "mean_violation": 0.01, "unfair": 0.0},
                 {"system": "hpa", "workload": "B", "tenant_mix": "u", "cluster_size": "s", "rep": rep,
                  "total_cost_usd": 1.0, "mean_violation": 0.01, "unfair": 0.0}]
    share = mod.rep_bootstrap(pd.DataFrame(rows), "jcac", n=400, seed=1)
    assert share[("A", "u", "s")] == 1.0
    assert 0.05 < share[("B", "u", "s")] < 0.95
