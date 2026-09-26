"""The frozen PREREG_STRUCTURAL_MISMATCH scorer can pass, can fail, and uses
one Holm family across all plants -- tested before any campaign run."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent


def _mod():
    spec = importlib.util.spec_from_file_location("asm", HERE / "analysis_structural_mismatch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _plant(mod, effect: dict, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for wl in ("a", "b", "c", "d", "e"):
        for mix in ("m1", "m2", "m3", "m4"):
            for size in ("s", "m", "l"):
                base = rng.uniform(1.0, 3.0)
                for rep in range(5):
                    for arm in mod.ARMS:
                        rows.append({"system": arm, "workload": wl, "tenant_mix": mix,
                                     "cluster_size": size, "rep": rep,
                                     "total_cost_usd": base + effect.get(arm, 0.0) + rng.normal(0, 0.002),
                                     "mean_violation": 0.01, "mean_jain": 0.99,
                                     "mean_excess": 0.02, "tier_none_step_share": 0.0})
    return pd.DataFrame(rows)


def test_one_holm_family_of_ten_across_the_five_plants():
    mod = _mod()
    frames = {p: mod.prepare(_plant(mod, {"keda_fair": 0.5, "jcac_nojoint_v2_tuned": 0.5}))
              for p, _ in mod.PLANTS}
    res = mod.score(frames)
    assert len(res) == 10 and all(r["pass"] for r in res.values())
    assert min(r["threshold"] for r in res.values()) == 0.05 / 10


def test_a_plant_where_the_blind_planner_loses_fails_there_only():
    mod = _mod()
    frames = {p: mod.prepare(_plant(mod, {"keda_fair": 0.5, "jcac_nojoint_v2_tuned": 0.5}))
              for p, _ in mod.PLANTS}
    frames["live"] = mod.prepare(_plant(mod, {mod.BLIND: 0.5}))
    res = mod.score(frames)
    assert not res["SM-H1[live]"]["pass"] and not res["SM-H2[live]"]["pass"]
    assert res["SM-H1[latency]"]["pass"]


def test_the_record_builds(monkeypatch, tmp_path):
    mod = _mod()
    monkeypatch.setenv("POLYFORGE_ANALYSIS_OUT", str(tmp_path))
    text = mod.build({p: _plant(mod, {"keda_fair": 0.1}) for p, _ in mod.PLANTS})
    assert "SM-H1[live]" in text and "price of believing the wrong form" in text


def test_the_calibrated_blind_arm_is_scored_in_its_own_family():
    mod = _mod()
    frames = {p: mod.prepare(_plant(mod, {"keda_fair": 0.5, "jcac_nojoint_v2_tuned": 0.5,
                                          mod.CALIBRATED: 0.6}))
              for p, _ in mod.PLANTS}
    assert all(r["pass"] for r in mod.score(frames).values())
    sec = mod.score(frames, mod.CALIBRATED, "SM-S")
    assert set(sec) == {f"SM-S{i}[{p}]" for i in (1, 2) for p, _ in mod.PLANTS}
    assert not any(r["pass"] for r in sec.values())
