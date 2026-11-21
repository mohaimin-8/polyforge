"""index-probe against hand-made decisions, the T3 instance and a real
planner log."""

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "examples"))

import index_probe  # noqa: E402

OBJECTIVES = '''import math
def nan_inner(x):  # the one point off the axes of the 2 x 2 box
    return {(0, 0): 0.0, (1, 0): -1.0, (0, 1): -1.0}.get(tuple(x), math.nan)
def inf_axis(x):
    return math.inf if tuple(x) == (1, 0) else 0.0
def nan_anchor(x):
    return math.nan if tuple(x) == (0, 0) else 0.0
def product(x):
    return float(x[0] * x[1])
'''


def _decision(phi_map, anchor=(0, 0), values=((0, 1), (0, 1)), **extra):
    return {"id": "d", "values": [list(v) for v in values], "anchor": list(anchor),
            "phi": [[list(p), v] for p, v in phi_map.items()], **extra}


def _box_file(tmp_path, values=((0, 1), (0, 1)), anchor=(0, 0)):
    box = tmp_path / "box.json"
    box.write_text(json.dumps({"values": [list(v) for v in values], "anchor": list(anchor)}))
    return box


@pytest.fixture
def objectives(tmp_path, monkeypatch):
    (tmp_path / "probe_objectives.py").write_text(OBJECTIVES)
    monkeypatch.syspath_prepend(str(tmp_path))
    return "probe_objectives"


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


def test_every_decision_is_checked_or_counted_by_why_theorem1_says_nothing():
    """A joint optimum that uses a value infeasible on its own axis lies off the decomposition:
    both choices are feasible, the loss is defined, and it can exceed the index (here 4 against 0)."""
    off = {(0, 0): 0.0, (1, 0): None, (0, 1): -1.0, (1, 1): -5.0}
    r = index_probe.analyse_decision(_decision(off))
    assert r["per_knob"] == (0, 1) and r["joint"] == (1, 1) and r["loss"] == pytest.approx(4.0)
    assert r["index"] == pytest.approx(0.0) and not r["theorem1_applies"] and not r["needs_repair"]
    no_anchor = {(0, 0): None, (1, 0): -1.0, (0, 1): -1.0, (1, 1): -3.0}
    repair = {(0, 0): 0.0, (1, 0): -1.0, (0, 1): -1.0, (1, 1): None}
    nothing = dict.fromkeys(off)
    additive = {(0, 0): 0.0, (1, 0): -1.0, (0, 1): -2.0, (1, 1): -3.0}
    rows = [index_probe.analyse_decision(_decision(p)) for p in (off, no_anchor, repair, nothing, additive)]
    assert [r["outside_theorem1"] for r in rows] == [
        "joint choice off the decomposition", "anchor infeasible", "per-knob combination infeasible",
        "no feasible point", None]
    s = index_probe.summarise(rows)
    assert s["theorem1_checked"] == 1 and s["theorem1_violations"] == 0
    assert s["theorem1_checked"] + sum(s["outside_theorem1"].values()) == s["decisions"] == 5
    assert set(s["outside_theorem1"].values()) == {1}
    assert s["outside_loss_above_index"] == 1


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
    assert summary["theorem1_checked"] + sum(summary["outside_theorem1"].values()) == summary["decisions"]
    for row, d in zip(rows, logged):
        assert row["index"] == pytest.approx(d["planner_index"], abs=1e-12)


@pytest.mark.parametrize("sample", [None, 59])
@pytest.mark.parametrize("name", ["nan_inner", "inf_axis", "nan_anchor"])
def test_box_mode_refuses_a_non_finite_objective_value_exact_or_sampled(objectives, tmp_path, name, sample):
    """Sampling gave a range of NaN or Infinity here (the exact mode always refused)."""
    with pytest.raises(ValueError, match="not finite at"):
        index_probe.run_box(f"{objectives}:{name}", _box_file(tmp_path), sample, 1)


@pytest.mark.parametrize("values, anchor, why", [
    (((0, 1, 1, 1), (0, 1)), (0, 0), "distinct"),
    (((0, 1), (0, 1)), (5, 0), "not a value of knob 0"),
])
def test_sampling_checks_the_box_as_the_exact_mode_does(objectives, tmp_path, values, anchor, why):
    """A repeated value would be drawn more often than the others: T5 needs uniform draws."""
    box = _box_file(tmp_path, values, anchor)
    for sample in (None, 59):
        with pytest.raises(ValueError, match=why):
            index_probe.run_box(f"{objectives}:product", box, sample, 1)


@pytest.mark.parametrize("sample", [0, -3])
def test_a_sample_below_one_is_refused(objectives, tmp_path, sample):
    with pytest.raises(ValueError, match="at least 1"):
        index_probe.run_box(f"{objectives}:product", _box_file(tmp_path), sample, 1)


def test_a_candidate_logged_outside_the_box_is_counted_and_leaves_the_decision_incomplete():
    """A mistyped label ("1" for 1) puts the joint optimum outside the box, where it is not used:
    the index and loss are then exact only over what was logged, and the summary must say so."""
    phi = {(0, 0): 0.0, (1, 0): -1.0, (0, 1): -1.0, (1, 1): -5.0}
    good = index_probe.analyse_decision(_decision(phi))
    typo = _decision({p: v for p, v in phi.items() if p != (1, 1)})
    typo["phi"].append([[1, "1"], -5.0])
    r = index_probe.analyse_decision(typo)
    assert good["evaluated"] == good["box_size"] == 4 and good["logged_outside_box"] == 0
    assert r["evaluated"] == 3 and r["box_size"] == 4 and r["logged_outside_box"] == 1
    s = index_probe.summarise([good, r])
    assert s["incompletely_logged"] == 1 and s["logged_outside_box"] == 1


def test_a_candidate_logged_twice_must_carry_one_value():
    phi = {(0, 0): 0.0, (1, 0): -1.0, (0, 1): -1.0, (1, 1): -5.0}
    same = _decision(phi)
    same["phi"].append([[1, 1], -5.0])
    assert index_probe.analyse_decision(same)["joint"] == (1, 1)
    clash = _decision(phi)
    clash["phi"].append([[1, 1], 0.0])
    with pytest.raises(ValueError, match="logged twice"):
        index_probe.analyse_decision(clash)


def test_log_mode_refuses_bad_input_naming_the_line_and_decision(tmp_path, capsys):
    """Refused input exits 2 with one line saying where (exit 1 means Theorem 1 violations)."""
    ok = {(0, 0): 0.0, (1, 0): -1.0, (0, 1): -2.0, (1, 1): -3.0}
    cases = {
        "not finite at (1, 1)": json.dumps({**_decision({**ok, (1, 1): math.nan}), "id": "t7@3"}),
        "not JSON": "{not json",
        '"values", "anchor" and "phi"': json.dumps({"id": "t7@3", "values": [[0, 1]]}),
    }
    for why, line in cases.items():
        log = tmp_path / "log.jsonl"
        log.write_text(json.dumps(_decision(ok)) + "\n\n" + line + "\n", encoding="utf-8")
        assert index_probe.main(["log", str(log)]) == 2
        err = capsys.readouterr().err
        assert "line 3" in err and why in err and "Traceback" not in err
        assert ("'t7@3'" in err) == (why != "not JSON")


def test_box_mode_refusals_exit_2_with_one_line(objectives, tmp_path, capsys):
    box = str(_box_file(tmp_path))
    assert index_probe.main(["box", "--objective", f"{objectives}:inf_axis", "--box", box, "--sample", "59"]) == 2
    assert "not finite at (1, 0)" in capsys.readouterr().err
    assert index_probe.main(["box", "--objective", f"{objectives}:product", "--box", box, "--sample", "0"]) == 2
    assert "at least 1" in capsys.readouterr().err
