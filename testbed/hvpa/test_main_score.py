"""WP17's main-run scorer on the pilot's own records and on known answers (no live run here)."""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import main_runs  # noqa: E402
import main_score  # noqa: E402
import pilot  # noqa: E402

PILOT_RUNS = HERE / "pilot_runs"
PILOT_REPORT = main_score.REPO / "research" / "hvpa" / "PILOT_LIVE.md"


def predictions() -> dict:
    return json.loads(main_score.PREDICTIONS.read_text(encoding="utf-8"))


def plan_of(preds: dict, n: int, tests: dict | None = None) -> dict:
    """A plan shaped as main_runs.py writes it, over the live regimes, schedules 0 .. n-1."""
    regs = [{"width": w, "sigma": s, "targets": targets,
             "contrasts": {c["arm"]: {"test": (tests or {}).get(c["arm"], "superiority")} for c in group},
             "schedules": [{"k": k} for k in range(n)]}
            for w, s, targets, group in pilot.regimes(pilot.contrasts(preds))]
    return {"predictions_sha256": hashlib.sha256(json.dumps(preds, sort_keys=True).encode()).hexdigest(),
            "regimes": regs}


def design() -> dict:
    return main_score.load_design(allow_draft=True)


def write(runs: Path, width: int, sigma: float, k: int, arm: str, cost: float) -> None:
    path = pilot.record_path(runs, width, sigma, k, arm)
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {"k6_job": "succeeded", "k6": {"requests": 10000, "attainment": 1.0},
           "watch": {"cost": cost, "max_gap_s": 1.0}, "power": {"start": True, "end": True}}
    path.write_text(json.dumps(rec), encoding="utf-8")


def test_on_the_pilots_records_it_gives_the_pilots_differences():
    preds = predictions()
    s = main_score.score(plan_of(preds, pilot.PAIRS), PILOT_RUNS, design(), preds)
    want = main_runs.pilot_differences(PILOT_RUNS, preds)
    assert len(s["contrasts"]) == 6 and s["H8_rank"]["regimes"] == 3
    for c in s["contrasts"]:
        d = want[(c["width"], c["sigma"], c["arm"])]["diffs"]
        assert c["n"] == len(d) and c["left_out"] == pilot.PAIRS - len(d)
        assert c["mean_d"] == pytest.approx(statistics.fmean(d), rel=1e-12)
        assert c["sigma_d"] == pytest.approx(statistics.stdev(d), rel=1e-12)


def test_its_numbers_match_the_pilots_report():
    preds = predictions()
    got = {main_score.key(c): c for c in main_score.score(plan_of(preds, pilot.PAIRS), PILOT_RUNS, design(),
                                                          preds)["contrasts"]}
    rows = [line.split("|") for line in PILOT_REPORT.read_text(encoding="utf-8").splitlines()
            if line.startswith("| w")]
    assert len(rows) == 6
    for r in rows:
        regime, name, pairs, mean, sd = (x.strip() for x in r[1:6])
        c = got[f"{regime} {name}"]
        assert c["n"] == int(pairs) and f"{c['mean_d']:+.5f}" == mean and f"{c['sigma_d']:.5f}" == sd


def known_answers(runs: Path) -> tuple[dict, dict]:
    """Six schedules per regime: A3 0.1 dearer than joint (A4); A1 the same as joint, tested for
    equivalence, with schedule 0's A1 run lost."""
    preds = predictions()
    plan = plan_of(preds, 6, tests={"A1": "equivalence"})
    noise = [0.001, -0.001, 0.0005, -0.0005, 0.0002, -0.0002]
    for r in plan["regimes"]:
        for k in range(6):
            write(runs, r["width"], r["sigma"], k, "A4", 1.0)
            write(runs, r["width"], r["sigma"], k, "A3", 1.1 + noise[k])      # joint 0.1 cheaper
            if k:                                                             # schedule 0 lost its A1 run
                write(runs, r["width"], r["sigma"], k, "A1", 1.0 + noise[k])  # the same as joint
    return plan, preds


def test_known_answers_with_a_lost_run_and_an_equivalence_contrast(tmp_path):
    plan, preds = known_answers(tmp_path)
    s = main_score.score(plan, tmp_path, design(), preds)
    for c in s["contrasts"]:
        if c["arm"] == "A3":
            assert c["supported"] and c["mean_d"] == pytest.approx(0.1, abs=1e-3) and c["left_out"] == 0
        else:
            assert c["test"] == "equivalence" and c["supported"] and c["n"] == 5 and c["left_out"] == 1
    assert len(s["H8_beat"]) == 3 and not any(b["supported"] for b in s["H8_beat"])   # equivalence never beats


def test_the_report_gives_an_equivalence_contrasts_margin_and_90_percent_interval(tmp_path):
    """An equivalence contrast holds at 0.05 exactly when its 90% interval lies inside +-margin, so
    SCORE.md gives the margin and that interval beside the 95% one; a superiority contrast its 95% one."""
    plan, preds = known_answers(tmp_path)
    s = main_score.score(plan, tmp_path, design(), preds)
    text = main_score.report(s, {"draft": True, "plan_sha256": "0" * 64, "design_sha256": "0" * 64})
    for c in s["contrasts"]:
        row = next(r for r in text.splitlines() if r.startswith(f"| {main_score.key(c)} |"))
        lo95, hi95 = (main_score.f(v) for v in c["ci95"])
        assert f"95% CI [{lo95}, {hi95}]" in row
        if c["test"] == "equivalence":
            lo90, hi90 = (main_score.f(v) for v in c["ci90"])
            assert f"90% CI [{lo90}, {hi90}]" in row and f"margin +-{c['margin']:.4f}" in row
            assert c["supported"] and -c["margin"] < c["ci90"][0] and c["ci90"][1] < c["margin"]
        else:
            assert "90% CI" not in row and "margin" not in row


def test_the_rank_criterion_follows_the_twins_index(tmp_path):
    preds = predictions()
    plan = plan_of(preds, 6)
    index = {(r["width"], r["sigma"]): r["mean_index"] for r in preds["regimes"]}
    for r in plan["regimes"]:
        gain = index[(r["width"], r["sigma"])]        # a gain that grows with the twin's index
        for k in range(6):
            write(tmp_path, r["width"], r["sigma"], k, "A4", 1.0)
            write(tmp_path, r["width"], r["sigma"], k, "A3", 1.0 + gain + 0.0001 * k)
            write(tmp_path, r["width"], r["sigma"], k, "A1", 1.0 + 0.0001 * k)
    rank = main_score.score(plan, tmp_path, design(), preds)["H8_rank"]
    assert rank["regimes"] == 3 and rank["rho"] == pytest.approx(1.0) and rank["met"]


def test_a_draft_design_is_refused_unless_allowed():
    with pytest.raises(SystemExit):
        main_score.load_design()


def test_a_plan_from_other_predictions_is_refused():
    preds = predictions()
    with pytest.raises(SystemExit):
        main_score.score({**plan_of(preds, 6), "predictions_sha256": "0" * 64}, PILOT_RUNS, design(), preds)


def test_a_kept_record_that_is_not_a_measurement_is_refused(tmp_path):
    preds = predictions()
    plan = plan_of(preds, 1)
    r = plan["regimes"][0]
    write(tmp_path, r["width"], r["sigma"], 0, "A4", 1.0)
    path = pilot.record_path(tmp_path, r["width"], r["sigma"], 0, "A3")
    path.write_text(json.dumps({"error": "kubectl timed out"}), encoding="utf-8")
    with pytest.raises(SystemExit):
        main_score.score(plan, tmp_path, design(), preds)


def test_the_command_writes_both_files(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(plan_of(predictions(), pilot.PAIRS)), encoding="utf-8")
    out = tmp_path / "score"
    assert main_score.main(["--plan", str(plan), "--runs", str(PILOT_RUNS), "--out", str(out), "--allow-draft"]) == 0
    assert "DRAFT DESIGN" in (out / "SCORE.md").read_text(encoding="utf-8")
    assert len(json.loads((out / "score.json").read_text(encoding="utf-8"))["contrasts"]) == 6


def test_the_plan_main_runs_writes_scores_as_the_hand_built_one():
    """make_plan's own plan (the pilot's records, predictions.json, a valid drand draw), cut to the
    pilot's schedules, scores as plan_of's does: the scorer reads the format main_runs.py writes."""
    import seeds
    from test_seeds import ROUND_1000

    preds, digest = predictions(), "ab" * 32
    draw = {"round": 1000, "randomness": ROUND_1000["randomness"], "signature": ROUND_1000["signature"],
            "protocol_sha256": digest, "master_seed": seeds.master_seed(digest, ROUND_1000["randomness"])}
    plan = main_runs.make_plan(PILOT_RUNS, preds, draw, main_runs.parse_tests(["A1=equivalence"]))
    assert all(r["schedules_n"] >= pilot.PAIRS for r in plan["regimes"])
    cut = {**plan, "regimes": [{**r, "schedules": r["schedules"][:pilot.PAIRS]} for r in plan["regimes"]]}
    got = main_score.score(cut, PILOT_RUNS, design(), preds)["contrasts"]
    want = main_score.score(plan_of(preds, pilot.PAIRS, tests={"A1": "equivalence"}), PILOT_RUNS, design(),
                            preds)["contrasts"]
    assert [main_score.key(c) for c in got] == [main_score.key(c) for c in want] and len(got) == 6
    for g, w in zip(got, want):
        assert (g["test"], g["n"], g["left_out"]) == (w["test"], w["n"], w["left_out"])
        assert g["mean_d"] == pytest.approx(w["mean_d"]) and g["p"] == pytest.approx(w["p"], nan_ok=True)
    assert {c["test"] for c in got if c["arm"] == "A1"} == {"equivalence"}
