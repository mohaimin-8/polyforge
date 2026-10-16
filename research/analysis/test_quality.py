"""The frozen PREREG_QUALITY scorer prices quality, can pass, can fail,
reads equivalence only inside its margin, and builds its record -- tested
before any campaign run."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent


def _mod():
    spec = importlib.util.spec_from_file_location("aql", HERE / "analysis_quality.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _matrix(mod, cost: dict, quality: dict | None = None, seed: int = 5,
            noise: float = 0.002) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    quality = quality or {}
    rows = []
    for wl in ("crud_bursty", "crud_steady", "ai_cacheable", "ai_uncacheable", "agentic"):
        for mix in ("m1", "m2", "m3", "m4"):
            for size in mod.SIZES:
                base = rng.uniform(1.0, 3.0)
                for rep in range(5):
                    for arm in mod.ARMS:
                        rows.append({"system": arm, "workload": wl, "tenant_mix": mix,
                                     "cluster_size": size, "rep": rep,
                                     "total_cost_usd": base + cost.get(arm, 0.0) + rng.normal(0, noise),
                                     "mean_violation": 0.01, "mean_jain": 0.99, "mean_excess": 0.02,
                                     "mean_quality_loss": 0.1 + quality.get(arm, 0.0),
                                     **{g: 0.0 for g in mod.GUARDS}})
    return pd.DataFrame(rows)


def test_quality_is_priced_into_the_objective():
    mod = _mod()
    df = _matrix(mod, {})
    assert np.allclose(mod.quality_j(df) - mod.composite_objective(df), mod.QUALITY_WEIGHT * 0.1)


def test_a_comparator_losing_only_on_quality_fails_the_treatment_test_in_its_favour():
    mod = _mod()
    df = mod.prepare(_matrix(mod, {}, {c: 0.2 for c in mod.CONFIRMATORY.values()}))
    res = mod.score(df)
    assert all(r["pass"] for r in res.values())


def test_a_comparator_that_wins_fails_and_reads_as_a_loss():
    mod = _mod()
    res = mod.score(mod.prepare(_matrix(mod, {mod.TREATMENT: 0.5})))
    assert not any(r["pass"] for r in res.values())
    assert all(mod.direction(r) == "comparator better" for r in res.values())


def test_identical_search_arms_are_equivalent_not_a_win():
    mod = _mod()
    res = mod.score(mod.prepare(_matrix(mod, {}, noise=0.0005)))
    for hid in mod.COORDINATION:
        assert not res[hid]["pass"] and mod.equivalent(res[hid])


def test_the_arms_match_the_frozen_experiment():
    import yaml

    mod = _mod()
    spec = yaml.safe_load((HERE.parents[1] / "eval" / "experiments" / "matrix_quality.yaml")
                          .read_text(encoding="utf-8"))
    assert set(spec["systems"]) == set(mod.ARMS) and len(mod.ARMS) == 8
    assert spec["shared_seeds"] is True and spec["reps"] == 5
    assert spec["economy"] == {"tier_cost_small": 1.0e-4, "tier_cost_mid": 1.475e-4,
                               "tier_cost_large": 1.518e-4}


def test_the_record_builds(monkeypatch, tmp_path):
    mod = _mod()
    monkeypatch.setenv("POLYFORGE_ANALYSIS_OUT", str(tmp_path))
    text = mod.build(_matrix(mod, {"static_share_quality": 0.1}))
    for hid in mod.CONFIRMATORY:
        assert f"**{hid}**" in text
    assert "What the budget filter and ignoring quality cost" in text
