"""sensitivity_fair.py re-scores RESULTS_FAIR_J and RESULTS_RETUNED at other
lateness weights and without the cap -- EXPLORATORY, but its registered cell
(capped, w=2) must reproduce the frozen scorers exactly. Tested here before
the generated record is trusted."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent


def _mod():
    spec = importlib.util.spec_from_file_location("sensfair", HERE / "sensitivity_fair.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _matrix(mod, arms: list[str], effect: dict | None = None, vio: dict | None = None,
            seed: int = 11) -> pd.DataFrame:
    """Full factorial: 5 workloads x 4 tenant mixes x 3 cluster sizes x 5 reps,
    every arm. `effect[arm]` shifts an arm's cost, `vio[arm]` its violation
    (and, doubled, its uncapped excess) relative to the baseline."""
    effect, vio = effect or {}, vio or {}
    rng = np.random.default_rng(seed)
    rows = []
    for wl in ("a", "b", "c", "d", "e"):
        for mix in ("m1", "m2", "m3", "m4"):
            for size in ("s", "m", "l"):
                base = rng.uniform(1.0, 3.0)
                for rep in range(5):
                    for arm in arms:
                        rows.append({
                            "system": arm, "workload": wl, "tenant_mix": mix,
                            "cluster_size": size, "rep": rep,
                            "total_cost_usd": base + effect.get(arm, 0.0) + rng.normal(0, 0.002),
                            "mean_violation": 0.1 + vio.get(arm, 0.0) + rng.normal(0, 0.0005),
                            "mean_jain": 0.99,
                            "mean_excess": 0.2 + 2 * vio.get(arm, 0.0) + rng.normal(0, 0.0005),
                            "tier_none_step_share": 0.0,
                        })
    return pd.DataFrame(rows)


def _both_campaigns_arms(mod) -> list[str]:
    return sorted(set([
        mod.fair_j.TREATMENT, mod.fair_j.ALSO, *mod.fair_j.PRIMARY.values(),
        *mod.fair_j.SECONDARY.values(), *mod.retuned.PRIMARY.values(), *mod.retuned.PUBLISHED.values(),
    ]))


# 1. weighted_j at the registered weight/measure is the frozen composite_objective.

def test_weighted_j_at_w2_capped_equals_composite_objective_bit_for_bit():
    mod = _mod()
    arms = [mod.fair_j.TREATMENT, *mod.fair_j.PRIMARY.values()]
    df = _matrix(mod, arms)
    lhs = mod.weighted_j(df, 2.0, "mean_violation")
    rhs = mod.fair_j.composite_objective(df)
    assert (lhs.to_numpy() == rhs.to_numpy()).all()


# 2. The registered cell(), run end to end, matches the frozen superiority().

def test_capped_w2_cell_matches_frozen_superiority_mean_and_ci():
    mod = _mod()
    arms = _both_campaigns_arms(mod)
    df = _matrix(mod, arms, effect={"hpa_fair": 0.3, "jcac_nojoint_v2_tuned": 1.0},
                 vio={"hpa_fair": 0.02, "jcac_nojoint_v2_tuned": 0.15})
    comp = "hpa_fair"

    got = mod.cell(df, comp, 2.0, "mean_violation")

    j = df.assign(J=mod.fair_j.composite_objective(df))
    want = mod.fair_j.superiority(mod.fair_j.design_diffs(j, mod.TREATMENT, comp, "J"))
    assert got["n"] == want["n"]
    assert got["mean"] == want["mean"]
    assert got["ci"] == want["ci"]


# 3. Real-data anchor: the capped w=2 cells reproduce the registered rows.

def test_capped_w2_reproduces_the_registered_rows_on_real_data():
    mod = _mod()
    if not mod.fair_j.CSV.exists():
        pytest.skip("eval/results/metrics_matrix_fair_j.csv.gz is missing")
    if not mod.retuned.CSV.exists():
        pytest.skip("eval/results/metrics_matrix_retuned.csv.gz is missing")

    frames = mod.campaigns()

    fair_df, _ = frames["FAIR_J"]
    r = mod.cell(fair_df, "hpa_fair", 2.0, "mean_violation")
    assert f"{r['mean']:+.4f}" == "-0.0037"
    assert f"[{r['ci'][0]:+.4f}, {r['ci'][1]:+.4f}]" == "[-0.0234, +0.0166]"

    retuned_df, _ = frames["RETUNED"]
    r2 = mod.cell(retuned_df, "hpa_fair_retuned", 2.0, "mean_violation")
    assert f"{r2['mean']:+.4f}" == "-0.0001"
    assert f"[{r2['ci'][0]:+.4f}, {r2['ci'][1]:+.4f}]" == "[-0.0239, +0.0257]"


# 4. crossover(): analytic weight, same-arm agreement, and zero lateness diff.

def _pair_row(system: str, total_cost_usd: float, mean_violation: float) -> dict:
    return {"system": system, "workload": "wl", "tenant_mix": "mix", "cluster_size": "sz",
            "rep": 0, "total_cost_usd": total_cost_usd, "mean_violation": mean_violation,
            "mean_jain": 0.99}


def test_crossover_returns_the_analytic_weight():
    mod = _mod()
    cost_norm_unit = 119 * 8 * 0.01  # the cost normalizer: cost_norm 1.0 at this many dollars
    df = pd.DataFrame([
        _pair_row(mod.TREATMENT, 0.0, 2.0),           # cheaper by cost_norm 1, later by 2
        _pair_row("comp", cost_norm_unit, 0.0),
    ])
    w = mod.crossover(df, "comp", "mean_violation")
    # rest = -1.0 (treatment cheaper), late = +2.0 (treatment later) -> w* = -rest/late = 0.5
    assert w == pytest.approx(0.5, abs=1e-9)


def test_crossover_returns_none_when_both_sides_favour_the_same_arm():
    mod = _mod()
    cost_norm_unit = 119 * 8 * 0.01
    df = pd.DataFrame([
        _pair_row(mod.TREATMENT, 0.0, 0.0),            # treatment cheaper AND less late
        _pair_row("comp", cost_norm_unit, 2.0),
    ])
    assert mod.crossover(df, "comp", "mean_violation") is None


def test_crossover_returns_none_when_the_lateness_difference_is_zero():
    mod = _mod()
    cost_norm_unit = 119 * 8 * 0.01
    df = pd.DataFrame([
        _pair_row(mod.TREATMENT, 0.0, 1.0),            # cost differs, lateness tied
        _pair_row("comp", cost_norm_unit, 1.0),
    ])
    assert mod.crossover(df, "comp", "mean_violation") is None


# 5. Direction label flips from "treatment better" to "comparator better" as
#    the lateness weight that a clear lateness disadvantage is multiplied by grows.

def test_direction_reads_comparator_better_at_high_weight():
    mod = _mod()
    rng = np.random.default_rng(31)
    rows = []
    for i in range(20):
        wl = f"wl{i}"
        for rep in range(5):
            rows.append({"system": mod.TREATMENT, "workload": wl, "tenant_mix": "mix",
                         "cluster_size": "sz", "rep": rep,
                         "total_cost_usd": 10.0 + rng.normal(0, 0.01),
                         "mean_violation": 0.1 + rng.normal(0, 0.0005), "mean_jain": 0.99})
            rows.append({"system": "comp", "workload": wl, "tenant_mix": "mix",
                         "cluster_size": "sz", "rep": rep,
                         "total_cost_usd": 12.0 + rng.normal(0, 0.01),   # treatment cheaper
                         "mean_violation": 0.0 + rng.normal(0, 0.0001), "mean_jain": 0.99})  # treatment later
    df = pd.DataFrame(rows)

    low = mod.cell(df, "comp", 1.0, "mean_violation")
    high = mod.cell(df, "comp", 8.0, "mean_violation")

    assert low["direction"] == "treatment better"
    assert high["direction"] == "comparator better"


# 6. identical_runs: one differing metric breaks identity for that pair only.

def test_identical_runs_counts_paired_runs_with_all_metrics_equal():
    mod = _mod()
    common = {"total_cost_usd": 1.0, "mean_violation": 0.1, "mean_jain": 0.99,
              "mean_excess": 0.2, "tier_none_step_share": 0.0}
    rows = []
    for rep in (0, 1, 2):
        for arm in ("A", "B"):
            row = {"system": arm, "workload": "a", "tenant_mix": "m", "cluster_size": "s", "rep": rep}
            row.update(common)
            if rep == 2 and arm == "B":
                row["mean_excess"] = 999.0   # breaks identity for this one pair only
            rows.append(row)
    df = pd.DataFrame(rows)

    same, total = mod.identical_runs(df, "A", "B")
    assert (same, total) == (2, 3)


# 7. build() renders the required sections from synthetic frames.

def test_build_writes_required_sections_with_synthetic_frames(monkeypatch, tmp_path):
    mod = _mod()
    monkeypatch.setenv("POLYFORGE_ANALYSIS_OUT", str(tmp_path))

    fair_arms = [mod.fair_j.TREATMENT, mod.fair_j.ALSO, *mod.fair_j.PRIMARY.values(),
                 *mod.fair_j.SECONDARY.values()]
    retuned_arms = [mod.retuned.TREATMENT, *mod.retuned.PRIMARY.values(), *mod.retuned.PUBLISHED.values()]
    frames = {
        "FAIR_J": (_matrix(mod, fair_arms, seed=21), mod.fair_j.PRIMARY),
        "RETUNED": (_matrix(mod, retuned_arms, seed=22), mod.retuned.PRIMARY),
    }

    text = mod.build(frames)

    for heading in ("## Summary", "## Crossover", "## What each registered J term contributes",
                    "## Where the treatment sheds AI"):
        assert heading in text
