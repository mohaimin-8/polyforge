"""run_pilot.py: the predictions' sanity rule and what is kept."""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import run_pilot  # noqa: E402


def predictions(**change) -> dict:
    p = {"regimes": [{"width": 1, "sigma": 0.05, "gain_vs_A3": 0.0, "gain_vs_A1": 0.12},
                     {"width": 4, "sigma": 0.4, "gain_vs_A3": 0.08, "gain_vs_A1": -0.3}],
         "live_regimes": [{"width": 1, "sigma": 0.05}, {"width": 4, "sigma": 0.4}],
         "tuned_targets": {"0.05": {"cpu_default": 0.6}, "0.4": {"cpu_default": 0.35}}}
    return {**p, **change}


def test_sane_predictions_pass():
    assert run_pilot.problems(predictions()) == []


def test_each_defect_is_named():
    nan = float("nan")
    cases = {
        "no regimes": predictions(regimes=[], live_regimes=[]),
        "not a gain below 1": predictions(regimes=[{"width": 1, "sigma": 0.05, "gain_vs_A3": nan, "gain_vs_A1": 0.1},
                                                   {"width": 4, "sigma": 0.4, "gain_vs_A3": 0.1, "gain_vs_A1": 0.1}]),
        "not a predicted regime": predictions(live_regimes=[{"width": 2, "sigma": 0.05}]),
        "not in (0, 1]": predictions(tuned_targets={"0.05": {"cpu_default": 0.6}, "0.4": {"cpu_default": 0.0}}),
    }
    for words, pred in cases.items():
        found = run_pilot.problems(pred)
        assert found and any(words in f for f in found), (words, found)


def test_keep_copies_every_record_and_the_report(tmp_path, monkeypatch):
    src, dst, research = tmp_path / "pilot", tmp_path / "kept", tmp_path / "research"
    (src / "w1_s0.05" / "A4_vs_A3" / "pair0").mkdir(parents=True)
    (src / "w1_s0.05" / "A4_vs_A3" / "pair0" / "A4.json").write_text(json.dumps({"arm": "A4"}))
    (src / "w1_s0.05" / "A4_vs_A3" / "pair0" / "A3.attempt1.invalid.json").write_text("{}")
    (src / "PILOT.md").write_text("# WP17 live pilot\n")
    research.mkdir()
    monkeypatch.setattr(run_pilot, "RESEARCH", research)
    assert run_pilot.keep(src, dst) == 2
    assert (dst / "w1_s0.05" / "A4_vs_A3" / "pair0" / "A4.json").exists()
    assert (research / "PILOT_LIVE.md").read_text() == "# WP17 live pilot\n"
