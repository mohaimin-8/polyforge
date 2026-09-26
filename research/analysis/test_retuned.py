"""The frozen PREREG_RETUNED scorer can pass, can fail, labels a loss as a
loss, and builds its record -- tested before any campaign run."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent


def _mod():
    spec = importlib.util.spec_from_file_location("art", HERE / "analysis_retuned.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _matrix(mod, effect: dict, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for wl in ("a", "b", "c", "d", "e"):
        for mix in ("m1", "m2", "m3", "m4"):
            for size in mod.SIZES:
                base = rng.uniform(1.0, 3.0)
                for rep in range(5):
                    for arm in mod.ARMS:
                        rows.append({"system": arm, "workload": wl, "tenant_mix": mix,
                                     "cluster_size": size, "rep": rep,
                                     "total_cost_usd": base + effect.get(arm, 0.0) + rng.normal(0, 0.002),
                                     "mean_violation": 0.01, "mean_jain": 0.99,
                                     "mean_excess": 0.02, "tier_none_step_share": 0.0})
    return pd.DataFrame(rows)


def test_a_clear_win_over_every_comparator_passes_all_three():
    mod = _mod()
    df = mod.prepare(_matrix(mod, {c: 0.5 for c in mod.PRIMARY.values()}))
    res = mod.score(df, mod.PRIMARY)
    assert set(res) == {"RT-H1", "RT-H2", "RT-H3"} and all(r["pass"] for r in res.values())


def test_a_comparator_that_wins_fails_and_reads_as_a_loss():
    mod = _mod()
    df = mod.prepare(_matrix(mod, {mod.TREATMENT: 0.5}))
    res = mod.score(df, mod.PRIMARY)
    assert not any(r["pass"] for r in res.values())
    assert all(mod.direction(r) == "comparator better" for r in res.values())


def test_the_registered_treatment_is_fair_js():
    mod = _mod()
    assert mod.TREATMENT == "jcac_converged"
    assert mod.ARMS[0] == "jcac_converged" and len(set(mod.ARMS)) == 6


def test_the_arms_match_the_frozen_experiment():
    import yaml

    mod = _mod()
    spec = yaml.safe_load((HERE.parents[1] / "eval" / "experiments" / "matrix_retuned.yaml")
                          .read_text(encoding="utf-8"))
    assert set(spec["systems"]) == set(mod.ARMS)
    assert spec["shared_seeds"] is True and spec["reps"] == 5


def test_the_record_builds(monkeypatch, tmp_path):
    mod = _mod()
    monkeypatch.setenv("POLYFORGE_ANALYSIS_OUT", str(tmp_path))
    text = mod.build(_matrix(mod, {"hpa_fair_retuned": 0.1}))
    for hid in ("RT-H1", "RT-H2", "RT-H3"):
        assert f"**{hid}**" in text
    assert "Per cluster size" in text and "What re-tuning bought" in text
