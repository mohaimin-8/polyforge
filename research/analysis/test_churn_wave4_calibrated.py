"""The churn record recovers each run's replica trajectory exactly from the
fine export's per-bucket infra cost and counts its changes."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent


def _load(monkeypatch, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("POLYFORGE_ANALYSIS_OUT", str(out_dir))
    spec = importlib.util.spec_from_file_location("cw4c", HERE / "churn_wave4_calibrated.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_run(evidence: Path, arm: str, cell: str, rep: int, replicas: list[int], price: float) -> None:
    d = evidence / "runs" / f"{arm}__{cell}__uniform__small__rep{rep}"
    d.mkdir(parents=True)
    pre = [{"n_events": 20, "cost_infra_usd": 0.0, "cost_usd": 0.0}] * 3
    win = [{"n_events": 1000, "cost_infra_usd": r * price, "cost_usd": r * price} for r in replicas]
    for i, b in enumerate(pre + win):
        b["bucket_start_utc"] = f"t{i}"
    (d / "eval-export-fine.json").write_text(json.dumps({"buckets": pre + win}))
    presence = {f"p{i}": 12 for i in range(max(replicas))}
    presence.update({f"blink{i}": 1 for i in range(2)})
    (d / "load_distribution.json").write_text(json.dumps({"presence": presence}))


def test_trajectory_and_changes_are_recovered_exactly(tmp_path, monkeypatch):
    mod = _load(monkeypatch, tmp_path / "out")
    aw = mod.aw
    monkeypatch.setattr(aw, "EVIDENCE", tmp_path / "evidence")
    traj = [16, 16, 12, 12, 24, 24, 24, 9, 9, 9]
    _write_run(tmp_path / "evidence", "jcac-calibrated", "joint_stress", 0, traj, mod.PRICE_PER_REPLICA_SAMPLE)
    assert mod.trajectory("jcac-calibrated", "joint_stress", 0) == traj
    d = mod.describe("jcac-calibrated", "joint_stress", 0)
    assert (d["changes"], d["ups"], d["downs"], d["largest"]) == (3, 1, 2, 15)
    assert d["min"] == 9 and d["max"] == 24 and d["replica_seconds"] == sum(traj) * 10
    assert (d["pods"], d["short"]) == (26, 2)


def test_record_lists_every_run_and_names_the_primary_cell(tmp_path, monkeypatch):
    mod = _load(monkeypatch, tmp_path / "out")
    aw = mod.aw
    monkeypatch.setattr(aw, "EVIDENCE", tmp_path / "evidence")
    rows = []
    for cell in aw.CELLS:
        for arm in aw.ARMS:
            for rep in (0, 1):
                traj = [16] * 10 if arm != "jcac-calibrated" else [12, 9, 15, 9, 15, 9, 15, 9, 15, 9]
                _write_run(tmp_path / "evidence", arm, cell, rep, traj, mod.PRICE_PER_REPLICA_SAMPLE)
                rows.append({"system": arm, "workload": cell, "rep": rep, "total_cost_usd": 0.3, "mean_jain": 1.0,
                             "mean_violation": 0.0, "cache_hit_rate": 0.5, "ai_p95_ms": 1.0, "crud_p95_ms": 1.0,
                             "crud_p99_ms": 1.0, "ai_p99_ms": 1.0})
    monkeypatch.setattr(aw, "load", lambda: pd.DataFrame(rows))
    assert mod.main() == 0
    text = (tmp_path / "out" / "CHURN_WAVE4_CALIBRATED.md").read_text(encoding="utf-8")
    assert text.count("| joint_stress |") == 10 and text.count("| ai_cacheable |") == 10
    assert "changed its replica count at 9 and 9 of its 10 and 10 steps" in text
    assert "| joint_stress | tier-only | 0 | 10 | 16..16 (16.0) | 0 (0 / 0) | 0 |" in text


def test_a_run_that_is_not_valid_is_not_printed(tmp_path, monkeypatch):
    """Audit 2026-09-26, HIGH-4: the failed replica-only crud_bursty rep1 held
    a copy of jcac tier_mixed rep0's export (a harness defect, since fixed),
    and the record printed jcac's trajectory under replica-only's name. The
    record must describe only runs the scored table calls valid."""
    mod = _load(monkeypatch, tmp_path / "out")
    aw = mod.aw
    monkeypatch.setattr(aw, "EVIDENCE", tmp_path / "evidence")
    for rep in (0, 1):
        _write_run(tmp_path / "evidence", "replica-only", "crud_bursty", rep, [5] * 10,
                   mod.PRICE_PER_REPLICA_SAMPLE)
    _write_run(tmp_path / "evidence", "jcac", "tier_mixed", 1, [16] * 10,
               mod.PRICE_PER_REPLICA_SAMPLE)

    def row(arm, cell, rep):
        return {"system": arm, "workload": cell, "rep": rep, "total_cost_usd": 0.3,
                "mean_jain": 1.0, "mean_violation": 0.0, "cache_hit_rate": 0.5,
                "ai_p95_ms": 1.0, "crud_p95_ms": 1.0, "crud_p99_ms": 1.0, "ai_p99_ms": 1.0}

    # replica-only crud_bursty rep1 FAILED, but rep 1 exists for another arm,
    # so the record iterates rep 1 -- exactly the B1' situation.
    valid_only_rep0 = [row("replica-only", "crud_bursty", 0), row("jcac", "tier_mixed", 1)]
    monkeypatch.setattr(aw, "load", lambda: pd.DataFrame(valid_only_rep0))
    assert mod.main() == 0
    text = (tmp_path / "out" / "CHURN_WAVE4_CALIBRATED.md").read_text(encoding="utf-8")
    assert "| crud_bursty | replica-only | 0 |" in text
    assert "| crud_bursty | replica-only | 1 |" not in text
