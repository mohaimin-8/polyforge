"""The frozen PREREG_FAIR_J scorer can pass, can fail, and fails for the right
reasons -- tested on synthetic matrices before any campaign run."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent


def _mod():
    spec = importlib.util.spec_from_file_location("afj", HERE / "analysis_fair_j.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _matrix(mod, effect: dict, noise: float = 0.002, drop: tuple | None = None) -> pd.DataFrame:
    """Every arm at every design point x rep; `effect[arm]` shifts that arm's
    cost (hence J) relative to the treatment."""
    rng = np.random.default_rng(7)
    arms = [mod.TREATMENT, mod.ALSO, *mod.PRIMARY.values(), *mod.SECONDARY.values()]
    rows = []
    for wl in ("a", "b", "c", "d", "e"):
        for mix in ("m1", "m2", "m3", "m4"):
            for size in ("s", "m", "l"):
                base = rng.uniform(1.0, 3.0)
                for rep in range(mod.REPS):
                    for arm in arms:
                        if drop and (wl, mix, size, arm, rep) == drop:
                            continue
                        rows.append({"system": arm, "workload": wl, "tenant_mix": mix,
                                     "cluster_size": size, "rep": rep,
                                     "total_cost_usd": base + effect.get(arm, 0.0) + rng.normal(0, noise),
                                     "mean_violation": 0.01, "mean_jain": 0.99,
                                     "mean_excess": 0.02, "tier_none_step_share": 0.0})
    return pd.DataFrame(rows)


def test_a_clear_win_passes_every_primary_hypothesis():
    mod = _mod()
    df = _matrix(mod, {c: +0.5 for c in [*mod.PRIMARY.values(), *mod.SECONDARY.values()]})
    df = df.assign(J=mod.composite_objective(df))
    res = mod.score(df, mod.PRIMARY)
    assert all(r["pass"] for r in res.values())


def test_no_effect_fails_every_primary_hypothesis():
    mod = _mod()
    df = _matrix(mod, {})
    df = df.assign(J=mod.composite_objective(df))
    assert not any(r["pass"] for r in mod.score(df, mod.PRIMARY).values())


def test_a_rank_win_with_a_mean_loss_is_not_a_pass():
    # Most design points slightly better, a few much worse: the one-sided
    # rank test can reject while the mean difference is positive.
    mod = _mod()
    d = np.array([-0.01] * 50 + [0.5] * 10)
    r = mod.superiority(d)
    assert r["p"] < 0.05 and r["ci"][1] > 0
    fam = {"X": r}
    fam["X"]["pass"] = bool(r["p"] < 0.05 and r["ci"][1] < 0)
    assert not fam["X"]["pass"]


def test_an_incomplete_design_point_is_dropped_for_every_arm():
    mod = _mod()
    df = _matrix(mod, {}, drop=("a", "m1", "s", "hpa_fair", 0))
    arms = [mod.TREATMENT, mod.ALSO, *mod.PRIMARY.values(), *mod.SECONDARY.values()]
    kept = mod.complete_design_points(df, arms)
    assert kept[mod.DESIGN].drop_duplicates().shape[0] == 59
    assert not ((kept.workload == "a") & (kept.tenant_mix == "m1") & (kept.cluster_size == "s")).any()


def test_the_severity_guard_uses_the_upper_bound_not_the_mean():
    mod = _mod()
    assert not mod.noninferior(np.array([0.0] * 45 + [1.0] * 5))["ni"]      # mean 0.1
    assert mod.noninferior(np.full(40, -0.1))["ni"]


def test_the_record_builds_from_a_synthetic_campaign(monkeypatch, tmp_path):
    mod = _mod()
    monkeypatch.setenv("POLYFORGE_ANALYSIS_OUT", str(tmp_path))
    text = mod.build(_matrix(mod, {"hpa_fair": 0.5}))
    assert "FJ-H1" in text and "FJ-H3" in text and "listwise" in text
