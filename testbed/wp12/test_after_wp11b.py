"""after_wp11b.py: the form rule, the G3 section and what is kept."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import after_wp11b as a  # noqa: E402


def test_the_form_follows_the_validation_verdicts():
    assert a.form_rule(True, None) == ("linear", True, "VALIDATION.md (WP11 primary)")
    assert a.form_rule(False, True)[:2] == ("fitted", True)
    assert a.form_rule(False, False)[:2] == ("fitted", False)
    assert a.form_rule(None, None) == ("fitted", None, "no WP11b verdict")


def pilot(n_eq_low, n_sup_high, effect_high=0.15):
    return {"summary": {"low": {"J": {"n_equivalence": n_eq_low, "n_superiority": 900,
                                      "predicted_relative_effect": 0.001}},
                        "high": {"J": {"n_equivalence": 5, "n_superiority": n_sup_high,
                                       "predicted_relative_effect": effect_high}}}}


def test_g3_needs_a_validated_simulator_and_feasible_pairs_in_both_regimes():
    assert "G3 met." in a.g3_section(pilot(12, 30), True, "x")
    assert "G3 NOT met." in a.g3_section(pilot(41, 30), True, "x")
    assert "G3 NOT met." in a.g3_section(pilot(12, 41), True, "x")
    assert "G3 NOT met." in a.g3_section(pilot(12, None), True, "x")
    assert "G3 NOT met." in a.g3_section(pilot(12, 30), False, "x")
    text = a.g3_section(pilot(12, 30), True, "x", {"ratio": 1.4, "contrast": False})
    assert "no index contrast" in text and "1.40x" in text


def test_g3_needs_a_high_regime_whose_predicted_effect_is_at_least_ten_percent():
    assert "G3 met." in a.g3_section(pilot(12, 30, effect_high=0.10), True, "x")
    text = a.g3_section(pilot(12, 30, effect_high=0.0008), True, "x")
    assert "G3 NOT met." in text and "predicted effect 0.08%, below the 10%" in text
    assert "G3 NOT met." in a.g3_section(pilot(12, 30, effect_high=float("nan")), True, "x")
    assert "G3 NOT met." in a.g3_section(pilot(12, 30, effect_high=None), True, "x")


def test_the_gate_constants_are_those_of_pairs():
    import pairs
    assert (a.G3_MAX_PAIRS, a.MIN_LIVE_EFFECT) == (pairs.G3_MAX_PAIRS, pairs.MIN_LIVE_EFFECT)


def test_keep_runs_copies_the_records_without_anything_else(tmp_path):
    run = tmp_path / "src" / "low" / "pair0" / "joint" / "attempt1"
    run.mkdir(parents=True)
    for name in (*a.RUN_FILES, "k6.log", "schedule.json"):
        (run / name).write_text("x")
    assert a.keep_runs(tmp_path / "src", tmp_path / "dst") == 1
    kept = {p.name for p in (tmp_path / "dst" / "low" / "pair0" / "joint" / "attempt1").iterdir()}
    assert kept == set(a.RUN_FILES)
