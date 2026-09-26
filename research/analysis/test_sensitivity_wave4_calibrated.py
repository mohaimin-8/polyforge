"""The moving-block bootstrap beside B1''s registered i.i.d. reading: wider
under positive autocorrelation, equal-ish under none, deterministic, and the
record builds on synthetic evidence."""

from __future__ import annotations

import importlib.util
import json
import random
import zlib
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent


def _load(monkeypatch, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("POLYFORGE_ANALYSIS_OUT", str(out_dir))
    spec = importlib.util.spec_from_file_location("sw4c", HERE / "sensitivity_wave4_calibrated.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ar1(n: int, phi: float, seed: int, mean: float = -0.002, sd: float = 0.001) -> list[float]:
    rng = random.Random(seed)
    x, out = 0.0, []
    for _ in range(n):
        x = phi * x + rng.gauss(0.0, sd * (1 - phi * phi) ** 0.5)
        out.append(mean + x)
    return out


def test_block_bootstrap_widens_under_autocorrelation_and_not_without(tmp_path, monkeypatch):
    mod = _load(monkeypatch, tmp_path / "out")
    strong = {0: _ar1(31, 0.8, 1), 1: _ar1(31, 0.8, 2)}
    none = {0: _ar1(31, 0.0, 3), 1: _ar1(31, 0.0, 4)}
    for series in (strong, none):
        pooled = [d for s in series.values() for d in s]
        iid = mod.aw.bootstrap_ci(pooled)
        b5 = mod.block_bootstrap_ci(series, 5)
        width_iid, width_b5 = iid[1] - iid[0], b5[1] - b5[0]
        if series is strong:
            assert width_b5 > 1.3 * width_iid, (width_iid, width_b5)
            assert mod.lag1_acf(series[0]) > 0.5
        else:
            assert 0.6 * width_iid < width_b5 < 1.6 * width_iid, (width_iid, width_b5)
    # deterministic for the seed
    assert mod.block_bootstrap_ci(strong, 5) == mod.block_bootstrap_ci(strong, 5)


def test_record_builds_and_reports_holds_per_comparison(tmp_path, monkeypatch):
    mod = _load(monkeypatch, tmp_path / "out")
    aw = mod.aw
    evidence = tmp_path / "evidence"
    monkeypatch.setattr(aw, "EVIDENCE", evidence)
    rows = []
    per_bucket = {"jcac-calibrated": 0.008, "jcac": 0.010, "replica-only": 0.5, "cache-only": 0.04, "tier-only": 0.0081}
    for cell in aw.CELLS:
        for arm in aw.ARMS:
            for rep in (0, 1):
                # zlib.crc32, not hash(): str hashes are salted per process
                # (PYTHONHASHSEED), which made this fixture differ run to run.
                costs = _ar1(31, 0.6, zlib.crc32(f"{cell}|{arm}|{rep}".encode()) % 1000,
                             mean=per_bucket[arm], sd=0.0004)
                d = evidence / "runs" / f"{arm}__{cell}__uniform__small__rep{rep}"
                d.mkdir(parents=True)
                (d / "eval-export-fine.json").write_text(json.dumps({"buckets": [
                    {"bucket_start_utc": "x", "cost_usd": c} for c in costs]}))
                rows.append({"system": arm, "workload": cell, "rep": rep, "total_cost_usd": sum(costs),
                             "mean_jain": 1.0, "mean_violation": 0.0, "cache_hit_rate": 0.5,
                             "ai_p95_ms": 1.0, "crud_p95_ms": 1.0, "crud_p99_ms": 1.0, "ai_p99_ms": 1.0})
    monkeypatch.setattr(aw, "load", lambda: pd.DataFrame(rows))
    assert mod.main() == 0
    text = (tmp_path / "out" / "SENSITIVITY_WAVE4_CALIBRATED.md").read_text(encoding="utf-8")
    assert text.count("| joint_stress |") == 3 and "block 5" in text
    # the clear wins hold; tier-only at 0.0081 vs 0.008 is a near-tie the block CI need not separate
    for arm in ("jcac", "replica-only", "cache-only"):
        line = next(l for l in text.splitlines() if l.startswith(f"| {arm} |"))
        assert line.endswith("| **yes** |"), line
