"""The frozen PREREG_HELDOUT scorer can pass, can fail, reads equivalence only
inside its margin, and builds its record -- tested before any campaign run."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent


def _mod():
    spec = importlib.util.spec_from_file_location("aho", HERE / "analysis_heldout.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _matrix(mod, effect: dict, seed: int = 5, noise: float = 0.002) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for wl in ("mmpp_crud", "mmpp_mixed"):
        for mix in ("m1", "m2", "m3", "m4"):
            for size in mod.SIZES:
                base = rng.uniform(1.0, 3.0)
                for rep in range(5):
                    for arm in mod.ARMS:
                        rows.append({"system": arm, "workload": wl, "tenant_mix": mix,
                                     "cluster_size": size, "rep": rep,
                                     "total_cost_usd": base + effect.get(arm, 0.0) + rng.normal(0, noise),
                                     "mean_violation": 0.01, "mean_jain": 0.99, "mean_excess": 0.02,
                                     **{g: 0.0 for g in mod.GUARDS}})
    return pd.DataFrame(rows)


def test_a_clear_win_passes_all_five():
    mod = _mod()
    res = mod.score(mod.prepare(_matrix(mod, {c: 0.5 for c in mod.CONFIRMATORY.values()})),
                    mod.CONFIRMATORY)
    assert set(res) == set(mod.CONFIRMATORY) and all(r["pass"] for r in res.values())


def test_a_comparator_that_wins_fails_and_reads_as_a_loss():
    mod = _mod()
    res = mod.score(mod.prepare(_matrix(mod, {mod.TREATMENT: 0.5})), mod.CONFIRMATORY)
    assert not any(r["pass"] for r in res.values())
    assert all(mod.direction(r) == "comparator better" for r in res.values())


def test_identical_search_arm_is_equivalent_not_a_win():
    mod = _mod()
    res = mod.score(mod.prepare(_matrix(mod, {}, noise=0.0005)), mod.CONFIRMATORY)
    assert not res["HO-C1"]["pass"] and mod.equivalent(res["HO-C1"])


def test_the_arms_match_the_frozen_experiment():
    import yaml

    mod = _mod()
    spec = yaml.safe_load((HERE.parents[1] / "eval" / "experiments" / "matrix_heldout.yaml")
                          .read_text(encoding="utf-8"))
    assert set(spec["systems"]) == set(mod.ARMS) and len(mod.ARMS) == 7
    assert spec["workloads"] == ["mmpp_crud", "mmpp_mixed"]
    assert spec["shared_seeds"] is True and spec["reps"] == 5 and "transition_costs" not in spec


def test_the_record_builds(monkeypatch, tmp_path):
    mod = _mod()
    monkeypatch.setenv("POLYFORGE_ANALYSIS_OUT", str(tmp_path))
    text = mod.build(_matrix(mod, {"static_share": 0.1}))
    for hid in mod.CONFIRMATORY:
        assert f"**{hid}**" in text
    assert "JOINTNESS verdict" in text and "Per workload and cluster size" in text
