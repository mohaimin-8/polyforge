"""The replica-only erratum is derived from the evidence, not asserted: it must
name the contamination when the exports show it and say so when they don't."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent


def _load(monkeypatch, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("POLYFORGE_ANALYSIS_OUT", str(out_dir))
    spec = importlib.util.spec_from_file_location("erratum", HERE / "erratum_wave4_replica_only.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _export(evidence: Path, arm: str, cell: str, rep: int, large: int, hit: float) -> None:
    d = evidence / "runs" / f"{arm}__{cell}__uniform__small__rep{rep}"
    d.mkdir(parents=True)
    (d / "eval-export.json").write_text(json.dumps(
        {"tier_requests": {"large": large, "small": 800}, "cache_hit_rate": hit,
         "total_cost_usd": 20.0 if large > 100 else 0.3}), encoding="utf-8")


def _runs(rows):
    return pd.DataFrame([{"system": a, "workload": c, "rep": r, "status": "valid",
                          "total_cost_usd": 20.0 if a == "replica-only" else 0.3}
                         for a, c, r in rows])


def test_names_the_contaminated_arm_from_the_exports(tmp_path, monkeypatch):
    mod = _load(monkeypatch, tmp_path / "out")
    ev = tmp_path / "ev"
    _export(ev, "replica-only", "joint_stress", 0, 2988, 0.81)
    _export(ev, "tier-only", "joint_stress", 0, 12, 0.95)
    monkeypatch.setattr(mod, "B1P_EVIDENCE", ev)
    monkeypatch.setattr(mod, "load_b1p", lambda: _runs([("replica-only", "joint_stress", 0),
                                                        ("tier-only", "joint_stress", 0)]))
    monkeypatch.setattr(mod, "load_b1", lambda: _runs([("replica-only", "joint_stress", 0),
                                                       ("tier-only", "joint_stress", 0)]))
    assert mod.main() == 0
    text = (tmp_path / "out" / "ERRATUM_WAVE4_REPLICA_ONLY.md").read_text(encoding="utf-8")
    assert "| joint_stress | replica-only | 0 | 2988 | 0.810 |" in text
    assert "every other arm sent 12" in text
    assert "CONTAMINATED" in text


def test_says_so_when_the_exports_show_no_contamination(tmp_path, monkeypatch):
    mod = _load(monkeypatch, tmp_path / "out")
    ev = tmp_path / "ev"
    _export(ev, "replica-only", "joint_stress", 0, 12, 0.95)
    _export(ev, "tier-only", "joint_stress", 0, 12, 0.95)
    monkeypatch.setattr(mod, "B1P_EVIDENCE", ev)
    monkeypatch.setattr(mod, "load_b1p", lambda: _runs([("replica-only", "joint_stress", 0),
                                                        ("tier-only", "joint_stress", 0)]))
    monkeypatch.setattr(mod, "load_b1", lambda: _runs([]))
    assert mod.main() == 0
    text = (tmp_path / "out" / "ERRATUM_WAVE4_REPLICA_ONLY.md").read_text(encoding="utf-8")
    assert "NOT OBSERVED" in text and "CONTAMINATED" not in text
