"""The B1'' scorer, frozen with its pre-registration: churn from the fine
export, the damping rule, the non-inferiority rule, on synthetic data."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd

spec = importlib.util.spec_from_file_location(
    "aw4d", Path(__file__).resolve().parent / "analysis_wave4_dwell.py")
aw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aw)


def _write_run(root: Path, arm: str, cell: str, rep: int, replicas: list[int], tier: float) -> None:
    d = root / "runs" / f"{arm}__{cell}__uniform__small__rep{rep}"
    d.mkdir(parents=True)
    rows = [{"bucket_start_utc": f"t{i}", "n_events": 1000, "cost_infra_usd": r * aw.PRICE_PER_REPLICA_SAMPLE,
             "cost_tier_usd": tier, "cost_usd": r * aw.PRICE_PER_REPLICA_SAMPLE + tier}
            for i, r in enumerate(replicas)]
    (d / "eval-export-fine.json").write_text(json.dumps({"buckets": rows}))


def _df(rows_spec: dict[tuple[str, str], tuple[float, float]]) -> pd.DataFrame:
    rows = []
    for (arm, cell), (cost, jain) in rows_spec.items():
        for rep in (0, 1):
            rows.append({"system": arm, "workload": cell, "rep": rep, "total_cost_usd": cost, "mean_jain": jain,
                         "mean_violation": 0.0, "cache_hit_rate": 0.9, "ai_p95_ms": 1.0, "crud_p95_ms": 1.0,
                         "crud_p99_ms": 1.0, "ai_p99_ms": 1.0})
    return pd.DataFrame(rows)


def _seed(root: Path, dwell_traj: list[int], cal_traj: list[int], dwell_tier: float, cal_tier: float, abl_tier: float):
    for cell in aw.CELLS:
        for rep in (0, 1):
            _write_run(root, aw.TREATMENT, cell, rep, dwell_traj, dwell_tier)
            _write_run(root, aw.REFERENCE, cell, rep, cal_traj, cal_tier)
            _write_run(root, aw.ABLATION, cell, rep, [16] * len(cal_traj), abl_tier)


def test_churn_is_recovered_and_the_damping_rule_reads_it(tmp_path, monkeypatch):
    monkeypatch.setattr(aw, "EVIDENCE", tmp_path)
    oscillating = [12, 9, 15, 9, 15, 9, 15, 9, 15, 9, 15, 9]   # 11 changes, largest 6
    damped = [12, 12, 12, 10, 10, 10, 10, 8, 8, 8, 8, 8]       # 2 changes, largest 2
    _seed(tmp_path, damped, oscillating, 0.003, 0.003, 0.004)
    c = aw.churn(aw.REFERENCE, "joint_stress", [0, 1])
    assert (c["changes"], c["largest"], c["reps"]) == (11, 6, 2)
    d = aw.churn(aw.TREATMENT, "joint_stress", [0, 1])
    assert (d["changes"], d["largest"]) == (2, 2)


def test_verdicts_pass_when_damped_and_not_worse(tmp_path, monkeypatch):
    monkeypatch.setattr(aw, "EVIDENCE", tmp_path)
    monkeypatch.setattr(aw, "matrix_audit", lambda: ({"expected_runs": 24, "valid_runs": 24, "failed_runs": 0,
                                                       "runner_passes": 1}, 24))
    oscillating = [12, 9, 15, 9, 15, 9, 15, 9, 15, 9, 15, 9]
    damped = [12, 12, 12, 10, 10, 10, 10, 8, 8, 8, 8, 8]
    _seed(tmp_path, damped, oscillating, 0.0030, 0.0030, 0.0040)
    spec_rows = {}
    for cell in aw.CELLS:
        spec_rows[(aw.TREATMENT, cell)] = (0.26, 1.0)
        spec_rows[(aw.REFERENCE, cell)] = (0.265, 1.0)
        spec_rows[(aw.ABLATION, cell)] = (0.32, 1.0)
    monkeypatch.setattr(aw, "load", lambda: _df(spec_rows))
    text = aw.build()
    assert text.startswith("# B1″ — the damped joint controller on the live plane: WL-H6 PASS, WL-H7 PASS")

    # the dwell arm oscillating just as much -> WL-H6 FAIL; costing 20% more per bucket -> WL-H7 FAIL
    import shutil
    shutil.rmtree(tmp_path / "runs")
    _seed(tmp_path, oscillating, oscillating, 0.0036, 0.0030, 0.0040)
    text = aw.build()
    assert "WL-H6 FAIL, WL-H7 FAIL" in text.splitlines()[0]
