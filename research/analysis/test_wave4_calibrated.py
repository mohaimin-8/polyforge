"""The B1' scorer, frozen with its pre-registration: the bootstrap and the
'beats' rule on synthetic data, cluster-free."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd

spec = importlib.util.spec_from_file_location(
    "aw4c", Path(__file__).resolve().parent / "analysis_wave4_calibrated.py")
aw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aw)


def _write_run(root: Path, arm: str, cell: str, rep: int, costs: list[float]) -> None:
    d = root / "runs" / f"{arm}__{cell}__uniform__small__rep{rep}"
    d.mkdir(parents=True)
    (d / "eval-export-fine.json").write_text(json.dumps({"buckets": [
        {"bucket_start_utc": f"2026-09-16T00:00:{i:02d}Z", "cost_usd": c} for i, c in enumerate(costs)]}))


def test_bootstrap_ci_is_deterministic_and_bracketed():
    d = [-1.0, -0.5, -0.7, -0.9, -0.6, -0.8]
    a = aw.bootstrap_ci(d); b = aw.bootstrap_ci(d)
    assert a == b and a[0] <= -0.75 <= a[1] and a[1] < 0
    assert aw.bootstrap_ci([0.1]) is None


def test_compare_requires_cost_win_iso_fairness_and_a_ci_below_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(aw, "EVIDENCE", tmp_path)
    cell = "joint_stress"
    _write_run(tmp_path, "jcac-calibrated", cell, 0, [0.010] * 30)
    _write_run(tmp_path, "tier-only", cell, 0, [0.012] * 30)
    _write_run(tmp_path, "jcac-calibrated", cell, 1, [0.010] * 30)
    _write_run(tmp_path, "tier-only", cell, 1, [0.012] * 30)
    df = pd.DataFrame([
        {"system": "jcac-calibrated", "workload": cell, "rep": 0, "total_cost_usd": 0.30, "mean_jain": 1.0,
         "mean_violation": 0.0, "cache_hit_rate": 0.9, "ai_p95_ms": 1, "crud_p95_ms": 1, "crud_p99_ms": 1, "ai_p99_ms": 1},
        {"system": "jcac-calibrated", "workload": cell, "rep": 1, "total_cost_usd": 0.30, "mean_jain": 1.0,
         "mean_violation": 0.0, "cache_hit_rate": 0.9, "ai_p95_ms": 1, "crud_p95_ms": 1, "crud_p99_ms": 1, "ai_p99_ms": 1},
        {"system": "tier-only", "workload": cell, "rep": 0, "total_cost_usd": 0.36, "mean_jain": 1.0,
         "mean_violation": 0.0, "cache_hit_rate": 0.9, "ai_p95_ms": 1, "crud_p95_ms": 1, "crud_p99_ms": 1, "ai_p99_ms": 1},
        {"system": "tier-only", "workload": cell, "rep": 1, "total_cost_usd": 0.36, "mean_jain": 1.0,
         "mean_violation": 0.0, "cache_hit_rate": 0.9, "ai_p95_ms": 1, "crud_p95_ms": 1, "crud_p99_ms": 1, "ai_p99_ms": 1},
    ])
    r = aw.compare(df, cell, "tier-only")
    assert r["beats"] and r["n_pairs"] == 60 and r["ci"][1] < 0
    # fairness worse by more than the margin: not a win, whatever the cost
    df2 = df.copy(); df2.loc[df2.system == "jcac-calibrated", "mean_jain"] = 0.98
    assert not aw.compare(df2, cell, "tier-only")["beats"]
    # equal bucket costs: CI straddles 0 -> not a win
    for rep in (0, 1):
        p = tmp_path / "runs" / f"tier-only__{cell}__uniform__small__rep{rep}" / "eval-export-fine.json"
        p.write_text(json.dumps({"buckets": [{"bucket_start_utc": "x", "cost_usd": 0.010} for _ in range(30)]}))
    r3 = aw.compare(df, cell, "tier-only")
    assert not r3["beats"] and not r3["ci_excludes_0"]


def test_missing_bucket_costs_drop_the_rep_not_the_record(tmp_path, monkeypatch):
    monkeypatch.setattr(aw, "EVIDENCE", tmp_path)
    _write_run(tmp_path, "jcac-calibrated", "tier_mixed", 0, [0.01] * 5)
    assert aw.paired_deltas("tier_mixed", "jcac-calibrated", "jcac", [0, 1]) == []
    assert aw.bucket_costs("jcac", "tier_mixed", 0) is None


def _write_shaped_run(root: Path, arm: str, cell: str, rep: int, window: list[float], lead_edge: int = 497) -> None:
    """A fine export shaped like the first live run: five preflight buckets
    with a handful of events (two of them expensive), a partial edge bucket,
    the window at ~1000 events a bucket, a partial tail bucket."""
    d = root / "runs" / f"{arm}__{cell}__uniform__small__rep{rep}"
    d.mkdir(parents=True)
    pre = [(16, 0.0016), (64, 0.004), (29, 0.0128), (5, 0.05), (6, 0.06)]
    rows = [{"n_events": n, "cost_usd": c} for n, c in pre]
    rows.append({"n_events": lead_edge, "cost_usd": 0.02})
    rows += [{"n_events": 1000 + i, "cost_usd": c} for i, c in enumerate(window)]
    rows.append({"n_events": 443, "cost_usd": 0.002})
    for i, r in enumerate(rows):
        r["bucket_start_utc"] = f"2026-09-15T11:25:{i:02d}Z"
    (d / "eval-export-fine.json").write_text(json.dumps({"buckets": rows}))


def test_window_is_the_full_buckets_not_the_preflight_or_the_edges(tmp_path, monkeypatch):
    """Amendment 2: 37 buckets in the export, 30 in the window. The preflight's
    eleven `large` requests (0.05 + 0.06) must not enter the pairing."""
    monkeypatch.setattr(aw, "EVIDENCE", tmp_path)
    _write_shaped_run(tmp_path, "jcac-calibrated", "joint_stress", 0, [0.010] * 30)
    w = aw.window_costs("jcac-calibrated", "joint_stress", 0)
    assert w == [0.010] * 30
    assert len(aw.bucket_costs("jcac-calibrated", "joint_stress", 0)) == 37


def test_windows_a_bucket_apart_pair_to_the_shorter_and_synthetic_exports_pair_whole(tmp_path, monkeypatch):
    monkeypatch.setattr(aw, "EVIDENCE", tmp_path)
    _write_shaped_run(tmp_path, "jcac-calibrated", "joint_stress", 0, [0.010] * 30)
    # the other arm's window edge landed inside the grid: its lead bucket is full
    _write_shaped_run(tmp_path, "tier-only", "joint_stress", 0, [0.012] * 30, lead_edge=980)
    assert len(aw.window_costs("tier-only", "joint_stress", 0)) == 31
    deltas = aw.paired_deltas("joint_stress", "jcac-calibrated", "tier-only", [0])
    assert len(deltas) == 30 and all(abs(d - (0.010 - 0.02)) < 1e-12 for d in deltas[:1])
    # exports without n_events (the synthetic ones above) are taken whole
    _write_run(tmp_path, "jcac", "tier_mixed", 0, [0.01] * 12)
    assert aw.window_costs("jcac", "tier_mixed", 0) == [0.01] * 12


def test_a_trough_inside_the_window_stays_inside_it(tmp_path, monkeypatch):
    """Amendment 4: joint_stress dips to 342 events a bucket mid-window (floor
    481). The span keeps the trough; Amendment 2's contiguous rule returned
    the longer fragment, whose first pair then sat mid-window."""
    monkeypatch.setattr(aw, "EVIDENCE", tmp_path)
    d = tmp_path / "runs" / "jcac__joint_stress__uniform__small__rep0"
    d.mkdir(parents=True)
    counts = [60, 39, 12, 5, 4] + [900] * 12 + [300, 350] + [900] * 16 + [200]
    rows = [{"bucket_start_utc": f"t{i}", "n_events": n, "cost_usd": 0.001 * (i + 1)} for i, n in enumerate(counts)]
    (d / "eval-export-fine.json").write_text(json.dumps({"buckets": rows}))
    span = aw.window_costs("jcac", "joint_stress", 0)
    frag = aw.window_costs_contiguous("jcac", "joint_stress", 0)
    assert len(span) == 30 and span[0] == 0.001 * 6 and span[-1] == 0.001 * 35
    assert len(frag) == 16 and frag[0] == 0.001 * 20  # the fragment after the trough
