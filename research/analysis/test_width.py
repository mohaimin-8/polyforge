"""The frozen PREREG_WIDTH scorer reads every registered direction the right
way round -- tested before any campaign run."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
J_PER_DOLLAR = 1 / (119 * 8) / 0.01   # J's cost term per dollar of total cost


def _mod():
    spec = importlib.util.spec_from_file_location("awd", HERE / "analysis_width.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _frame(mod, j_offset: dict, seed: int = 3, noise: float = 0.0005) -> pd.DataFrame:
    """J offsets per arm, applied through cost."""
    rng = np.random.default_rng(seed)
    rows = []
    for wl in ("ai_cacheable", "ai_uncacheable", "agentic", "joint_stress"):
        for mix in ("m1", "m2", "m3", "m4"):
            for size in mod.SIZES:
                base = rng.uniform(1.0, 3.0)
                for rep in range(5):
                    for arm in mod.ARMS:
                        dj = j_offset.get(arm, 0.0) + rng.normal(0, noise)
                        rows.append({"system": arm, "workload": wl, "tenant_mix": mix,
                                     "cluster_size": size, "rep": rep,
                                     "total_cost_usd": base + dj / J_PER_DOLLAR,
                                     "mean_violation": 0.01, "mean_jain": 0.99, "mean_excess": 0.02,
                                     "tier_none_step_share": 0.0, "clamp_violation_step_share": 0.0,
                                     "coordination_disagreement_share": 0.01})
    return pd.DataFrame(rows)


def _frames(mod, published: dict, coupled: dict, live: dict) -> dict:
    return {"published": mod.prepare(_frame(mod, published, 3)),
            "coupled": mod.prepare(_frame(mod, coupled, 4)),
            "live": mod.prepare(_frame(mod, live, 5))}


# Joint better than decoupled by 0.05 on the wide lattice only; wide joint
# worse than narrow joint by 0.03.
MECH = {"jcac_decoupled_wide": 0.08, "jcac_joint_wide_tracked": 0.03}


def test_coordination_that_pays_only_when_wide_passes_the_mechanism_tests():
    mod = _mod()
    res = mod.score(_frames(mod, MECH, MECH, {}))
    assert res["mech"]["WD-H1"]["pass"] and res["mech"]["WD-H2"]["pass"]
    assert res["WD-H3"]                        # live: no DiD
    assert all(res["WD-H4"].values())          # no gain on the narrow lattice anywhere
    assert res["rate"]["WD-H5[published]"]["pass"] and res["rate"]["WD-H5[coupled]"]["pass"]
    assert not res["rate"]["WD-H5[live]"]["pass"]


def test_coordination_that_pays_only_when_narrow_fails_them():
    mod = _mod()
    flipped = {"jcac_decoupled": 0.05}         # G_narrow = 0.05, G_wide = 0: DiD < 0
    res = mod.score(_frames(mod, flipped, flipped, {}))
    assert not res["mech"]["WD-H1"]["pass"] and not res["mech"]["WD-H2"]["pass"]
    assert not res["WD-H4"]["published"]


def test_the_arms_match_the_three_frozen_experiments():
    import yaml

    mod = _mod()
    for plant in mod.PLANTS:
        spec = yaml.safe_load((HERE.parents[1] / "eval" / "experiments" / f"matrix_width_{plant}.yaml")
                              .read_text(encoding="utf-8"))
        assert spec["systems"] == mod.ARMS and spec["shared_seeds"] is True and spec["reps"] == 5
        assert spec["output"].endswith(f"raw_sim_matrix_width_{plant}.duckdb")


def test_the_record_builds(monkeypatch, tmp_path):
    mod = _mod()
    monkeypatch.setenv("POLYFORGE_ANALYSIS_OUT", str(tmp_path))
    text = mod.build({p: _frame(mod, MECH, i) for i, p in enumerate(mod.PLANTS)})
    assert "**WD-H1**" in text and "**WD-H2**" in text and "WD-H5[live]" in text
