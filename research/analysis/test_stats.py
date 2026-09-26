"""Tests for the shared statistics helpers. Run: python -m pytest test_stats.py -q

These decide PASS/FAIL verdicts in published records, so they are tested
against hand-computed expectations rather than against themselves.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stats import holm_bonferroni, load_campaign_runs  # noqa: E402


def test_holm_thresholds_step_down():
    # n=4, alpha=0.05 -> thresholds 0.0125, 0.01667, 0.025, 0.05 by rank.
    out = holm_bonferroni({"a": 0.001, "b": 0.02, "c": 0.04, "d": 0.9})
    assert out["a"]["rank"] == 1
    assert out["a"]["threshold"] == 0.05 / 4
    assert out["b"]["threshold"] == 0.05 / 3
    assert out["c"]["threshold"] == 0.05 / 2
    assert out["d"]["threshold"] == 0.05


def test_holm_is_step_down_not_per_test():
    # b fails at 0.02 > 0.0167; c must then fail too even though its own
    # threshold (0.025) would admit 0.04... it would not, but the step-down
    # property is what this pins: nothing after the first failure rejects.
    out = holm_bonferroni({"a": 0.001, "b": 0.02, "c": 0.021, "d": 0.9})
    assert out["a"]["reject"] is True
    assert out["b"]["reject"] is False
    assert out["c"]["reject"] is False, "step-down: no rejection after a failure"
    assert out["d"]["reject"] is False


def test_holm_rejects_all_when_all_tiny():
    out = holm_bonferroni({"a": 1e-30, "b": 1e-25, "c": 1e-20})
    assert all(v["reject"] for v in out.values()), \
        "the headline comparisons are far below any correction threshold"


def test_holm_marginal_pass_depends_on_family_size():
    # The audit's concrete example, with the arithmetic done properly.
    # A lone p=0.0073 is significant at alpha=0.05.
    alone = holm_bonferroni({"RB-H1": 0.0073})
    assert alone["RB-H1"]["reject"] is True

    # In a small family it still survives: Holm's first threshold is
    # alpha/n = 0.05/6 = 0.00833, and 0.0073 < 0.00833. This is exactly why
    # Holm is used rather than plain Bonferroni — it is less brutal on the
    # smallest p in the family.
    small = holm_bonferroni({f"H{i}": p for i, p in
                             enumerate([0.0073, 0.01, 0.02, 0.03, 0.04, 0.045])})
    assert small["H0"]["reject"] is True

    # Across the repository's full set of 48 pre-registered hypotheses the
    # first threshold is 0.05/48 = 0.00104, and 0.0073 does NOT survive. So
    # whether a marginal result stands is a statement about which family it
    # belongs to — which is why the V-series preregs each declare their own.
    wide = {"RB-H1": 0.0073}
    wide.update({f"other{i}": 0.5 for i in range(47)})
    assert holm_bonferroni(wide)["RB-H1"]["reject"] is False


def test_holm_single_hypothesis_is_uncorrected():
    out = holm_bonferroni({"only": 0.04})
    assert out["only"]["threshold"] == 0.05
    assert out["only"]["reject"] is True


def test_holm_preserves_every_input_key():
    pvals = {"x": 0.5, "y": 0.001, "z": 0.2}
    out = holm_bonferroni(pvals)
    assert set(out) == set(pvals)
    for k in pvals:
        assert out[k]["p"] == pvals[k]


def _campaign_db(path, rows):
    import duckdb

    con = duckdb.connect(str(path))
    con.execute("create table runs (run_id text, system text, workload text, tenant_mix text, "
                "cluster_size text, rep integer, status text)")
    con.execute("create table metrics (run_id text, total_cost_usd double, mean_violation double, "
                "mean_jain double, steps integer)")
    for i, (system, workload, rep, cost) in enumerate(rows):
        con.execute("insert into runs values (?,?,?,?,?,?,?)",
                    [f"r{i}", system, workload, "uniform", "small", rep, "valid"])
        con.execute("insert into metrics values (?,?,?,?,?)", [f"r{i}", cost, 0.0, 1.0, 30])
    con.close()


def test_duckdb_rows_come_back_in_the_committed_export_order(tmp_path):
    """Audit 2026-09-26: bootstraps resample rows by POSITION, and a DuckDB join
    promises no row order, so the archive tier could print different CI bounds
    than the clean clone. The committed export is the canonical order."""
    import pandas as pd

    rows = [("jcac", "a", 0, 1.0), ("hpa", "a", 0, 2.0), ("jcac", "b", 0, 3.0), ("hpa", "b", 0, 4.0)]
    _campaign_db(tmp_path / "c.duckdb", list(reversed(rows)))       # scrambled insertion
    export = pd.DataFrame([{"system": s, "workload": w, "tenant_mix": "uniform",
                            "cluster_size": "small", "rep": r, "total_cost_usd": c,
                            "mean_violation": 0.0, "mean_jain": 1.0, "steps": 30}
                           for s, w, r, c in [rows[2], rows[0], rows[3], rows[1]]])
    export.to_csv(tmp_path / "c.csv.gz", index=False)
    got = load_campaign_runs(tmp_path / "c.duckdb", tmp_path / "c.csv.gz")
    assert list(got.total_cost_usd) == [3.0, 1.0, 4.0, 2.0]


def test_duckdb_without_an_export_is_sorted_by_run_key(tmp_path):
    rows = [("jcac", "b", 0, 3.0), ("hpa", "a", 0, 2.0), ("jcac", "a", 0, 1.0)]
    _campaign_db(tmp_path / "c.duckdb", rows)
    got = load_campaign_runs(tmp_path / "c.duckdb", tmp_path / "absent.csv.gz")
    assert list(got.total_cost_usd) == [2.0, 1.0, 3.0]
