"""index-probe against hand-made decisions, the T3 instance and a real
planner log."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "examples"))

import index_probe  # noqa: E402


def _decision(phi_map, anchor=(0, 0), values=((0, 1), (0, 1)), **extra):
    return {"id": "d", "values": [list(v) for v in values], "anchor": list(anchor),
            "phi": [[list(p), v] for p, v in phi_map.items()], **extra}


def test_additive_decision_has_zero_index_and_no_loss():
    phi = {(0, 0): 0.0, (1, 0): -1.0, (0, 1): -2.0, (1, 1): -3.0}
    r = index_probe.analyse_decision(_decision(phi))
    assert r["index"] == pytest.approx(0.0) and r["loss"] == pytest.approx(0.0)
    assert r["per_knob"] == r["joint"] == (1, 1) and r["theorem1_holds"]


def test_interacting_decision_respects_theorem1_and_reports_the_systems_gap():
    phi = {(0, 0): 0.0, (1, 0): -1.0, (0, 1): -1.0, (1, 1): 0.5}
    r = index_probe.analyse_decision(_decision(phi, chosen=[1, 1]))
    assert r["per_knob"] == (1, 1) and r["joint"] in ((1, 0), (0, 1))
    assert r["loss"] == pytest.approx(1.5) and r["bound"] == pytest.approx(2.5)
    assert r["index"] == pytest.approx(2.5) and r["theorem1_holds"]
    assert r["chosen_gap"] == pytest.approx(1.5)


def test_infeasible_per_knob_combination_is_counted_as_needing_repair():
    phi = {(0, 0): 0.0, (1, 0): -1.0, (0, 1): -1.0, (1, 1): None}
    r = index_probe.analyse_decision(_decision(phi))
    assert r["needs_repair"] and not r["theorem1_applies"]


def test_box_mode_on_the_tight_instance(tmp_path):
    import tight

    box = tmp_path / "box.json"
    box.write_text(json.dumps({"values": [list(v) for v in tight.VALUES], "anchor": list(tight.ANCHOR)}))
    exact = index_probe.run_box("tight:phi", box, None, 1)
    # T3: per-knob search loses rho - eta of an index rho (tight as eta -> 0).
    assert exact["index"] == pytest.approx(tight.RHO) and exact["bound"] == pytest.approx(tight.RHO)
    assert exact["loss"] == pytest.approx(tight.RHO - tight.ETA)
    sampled = index_probe.run_box("tight:phi", box, 59, 3)
    assert sampled["sampled_range"] <= exact["index"] + 1e-12
    assert sampled["covers_quantile_index_with_probability_at_least"] == pytest.approx(1 - 2 * 0.95 ** 59)


def test_a_real_planner_log_has_no_theorem1_violation(tmp_path):
    out = tmp_path / "log.jsonl"
    subprocess.run([sys.executable, str(HERE / "examples" / "gateway_log.py"), "--steps", "3", "--out", str(out)],
                   check=True, capture_output=True, timeout=600)
    rows, summary = index_probe.run_log(out)
    assert summary["decisions"] == len(rows) > 0
    assert summary["theorem1_violations"] == 0 and summary["incompletely_logged"] == 0


def test_a_kubernetes_planner_log_gives_the_planners_own_index(tmp_path):
    """Worked example 3: the WP17 twin's joint planner on the calibrated plant."""
    out = tmp_path / "k8s.jsonl"
    subprocess.run([sys.executable, str(HERE / "examples" / "kubernetes_log.py"), "--minutes", "10", "--out", str(out)],
                   check=True, capture_output=True, timeout=600)
    logged = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows, summary = index_probe.run_log(out)
    assert summary["decisions"] == len(rows) == len(logged) == 20        # one per 30 s in 10 minutes
    assert summary["theorem1_violations"] == 0 and summary["incompletely_logged"] == 0
    for row, d in zip(rows, logged):
        assert row["index"] == pytest.approx(d["planner_index"], abs=1e-12)
