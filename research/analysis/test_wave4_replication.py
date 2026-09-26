"""The frozen PREREG_WAVE4_REPLICATION scorer can pass, can fail on each
guard, refuses an incomplete matrix, and builds -- tested before any run."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

HERE = Path(__file__).resolve().parent


def _mod():
    spec = importlib.util.spec_from_file_location("awr", HERE / "analysis_wave4_replication.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _frame(mod, cost=None, jain=None, viol=None, drop=None, seed=7):
    """cost/jain/viol: arm -> offset; drop: (arm, cell, rep) runs to omit."""
    rng = np.random.default_rng(seed)
    rows = []
    for cell in mod.CELLS:
        for rep in range(mod.REPS):
            base = rng.uniform(0.2, 0.4)
            for arm in mod.ARMS:
                if drop and (arm, cell, rep) in drop:
                    continue
                rows.append({"system": arm, "workload": cell, "rep": rep,
                             "total_cost_usd": base + (cost or {}).get(arm, 0.0) + rng.normal(0, 1e-4),
                             "mean_jain": 0.95 + (jain or {}).get(arm, 0.0),
                             "mean_violation": 0.02 + (viol or {}).get(arm, 0.0),
                             "cache_hit_rate": 0.9, "ai_p95_ms": 900.0, "crud_p95_ms": 20.0})
    return pd.DataFrame(rows)


def test_cheaper_on_every_seed_at_iso_fairness_and_slo_passes():
    mod = _mod()
    v, rows = mod.verdict(_frame(mod, cost={mod.TREATMENT: -0.05}), mod.PRIMARY_CELL)
    assert v == "PASS" and all(r["p"] == 1 / 32 for r in rows)


def test_one_seed_lost_fails_the_comparator():
    mod = _mod()
    df = _frame(mod, cost={mod.TREATMENT: -0.05})
    hit = (df.system == mod.TREATMENT) & (df.workload == mod.PRIMARY_CELL) & (df.rep == 3)
    df.loc[hit, "total_cost_usd"] += 0.2
    v, rows = mod.verdict(df, mod.PRIMARY_CELL)
    assert v == "FAIL" and all(not r["beats"] for r in rows)


def test_the_fairness_and_slo_guards_each_fail_a_cheaper_treatment():
    mod = _mod()
    assert mod.verdict(_frame(mod, cost={mod.TREATMENT: -0.05}, jain={mod.TREATMENT: -0.02}),
                       mod.PRIMARY_CELL)[0] == "FAIL"
    assert mod.verdict(_frame(mod, cost={mod.TREATMENT: -0.05}, viol={mod.TREATMENT: 0.02}),
                       mod.PRIMARY_CELL)[0] == "FAIL"


def test_a_missing_run_makes_the_cell_not_evaluable():
    mod = _mod()
    df = _frame(mod, cost={mod.TREATMENT: -0.05}, drop={("tier-only", mod.PRIMARY_CELL, 2)})
    assert mod.verdict(df, mod.PRIMARY_CELL)[0] == "NOT EVALUABLE"
    assert mod.verdict(df, mod.HELD_OUT_CELL)[0] == "PASS"


def test_control_overhead_is_charged_to_operator_arms_only():
    mod = _mod()
    oh = mod.control_overhead_usd()
    assert 0.0 < oh < 0.05
    # A treatment ahead by less than the overhead against the non-operator
    # HPA arms loses those comparisons once the overhead is charged.
    df = _frame(mod, cost={mod.TREATMENT: -oh / 2, "jcac": oh, "cache-only": oh, "tier-only": oh})
    assert mod.verdict(df, mod.PRIMARY_CELL)[0] == "PASS"
    v, rows = mod.verdict(df, mod.PRIMARY_CELL, oh)
    lost = {r["vs"] for r in rows if not r["beats"]}
    assert v == "FAIL" and lost == {"replica-only", "replica-only-tuned"}


def test_the_arms_and_cells_match_the_frozen_experiment():
    mod = _mod()
    spec = yaml.safe_load((HERE.parents[1] / "eval" / "experiments" / "wave4_replication.yaml")
                          .read_text(encoding="utf-8"))
    assert spec["systems"] == mod.ARMS and spec["workloads"] == mod.CELLS
    assert spec["reps"] == mod.REPS and spec["shared_seeds"] is True and spec["steps"] == mod.STEPS


def test_the_record_builds(monkeypatch, tmp_path):
    mod = _mod()
    monkeypatch.setenv("POLYFORGE_ANALYSIS_OUT", str(tmp_path))
    text = mod.build(_frame(mod, cost={mod.TREATMENT: -0.05}))
    assert "WL-R1 (primary)" in text and "WL-R2 (secondary" in text and "control overhead" in text
