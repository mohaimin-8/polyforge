"""The WP17 pilot's bookkeeping: order, J, pairs needed, contrasts."""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pilot  # noqa: E402


def test_abba_alternates_within_each_pair():
    assert [pilot.order(k, "A4", "A3") for k in range(4)] == [("A4", "A3"), ("A3", "A4"), ("A4", "A3"), ("A3", "A4")]


def test_live_j_charges_late_requests_at_the_twins_price():
    rec = {"k6": {"requests": 1000, "attainment": 0.9}, "watch": {"cost": 0.05}}
    assert pilot.j_live(rec) == pytest.approx(0.05 + 1e-4 * 100)


def test_pairs_needed_follow_the_plans_formulas():
    s = pilot.sample_sizes([0.01, 0.02, 0.03], comparator_mean_j=1.0, predicted_gain=0.1)
    sd = 0.01
    assert s["sigma_d"] == pytest.approx(sd)
    assert s["n_superiority"] == math.ceil(((1.645 + 1.282) * sd / 0.1) ** 2)
    assert s["n_equivalence"] == math.ceil(((1.645 + 1.645) * sd / 0.02) ** 2)
    assert pilot.sample_sizes([0.0, 0.1], 1.0, 0.0)["n_superiority"] is None


def test_each_live_regime_gives_two_contrasts_with_their_targets():
    preds = {"regimes": [{"width": 2, "sigma": 0.2, "gain_vs_A3": 0.3, "gain_vs_A1": 0.1}],
             "live_regimes": [{"width": 2, "sigma": 0.2}],
             "tuned_targets": {"0.2": {"cpu_default": 0.4, "rate_default": 0.5}}}
    c = pilot.contrasts(preds)
    assert [(x["arm"], x["target"], x["gain"]) for x in c] == [("A3", 2, 0.3), ("A1", 0.4, 0.1)]
    assert pilot.record_path(Path("o"), c[1], 3, "A4").as_posix() == "o/w2_s0.2/A4_vs_A1/pair3/A4.json"


GOOD = {"k6_job": "succeeded", "k6": {"requests": 100, "attainment": 0.9}, "power": {"start": True, "end": True},
        "watch": {"cost": 0.05, "max_gap_s": 10.2}}


def test_a_run_is_a_measurement_only_when_load_power_and_polls_are_sound():
    assert pilot.problems(GOOD) == []
    assert pilot.problems({**GOOD, "k6_job": "timeout", "k6": None}) == ["k6 job timeout", "no k6 summary ()"]
    assert pilot.problems({**GOOD, "watch": {"cost": 0.05, "max_gap_s": 48.0}})[0].endswith("the host stalled")
    assert pilot.problems({**GOOD, "power": {"start": True, "end": False}}) == ["on battery"]
    assert pilot.problems({**GOOD, "power": {"start": None, "end": None}}) == []     # unknown is not a failure
    assert pilot.problems({"error": "RuntimeError: kubectl apply"}) == ["RuntimeError: kubectl apply"]


def test_an_invalid_run_is_repeated_and_kept_and_a_lost_cause_gives_up(tmp_path, monkeypatch):
    import live

    results = iter([RuntimeError("kubectl get"), {**GOOD, "k6_job": "failed", "k6": None}, dict(GOOD)])

    def fake_run(arm, target, regime, calibration, out):
        r = next(results)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(live, "run", fake_run)
    path = tmp_path / "pair0" / "A4.json"
    rec = pilot.attempt("A4", 2.0, {}, tmp_path / "cal.json", path)
    assert rec["attempt"] == 3 and path.exists()
    assert sorted(p.name for p in path.parent.glob("*.invalid.json")) == ["A4.attempt1.invalid.json",
                                                                         "A4.attempt2.invalid.json"]
    monkeypatch.setattr(live, "run", lambda *a: {**GOOD, "power": {"start": False, "end": False}})
    assert pilot.attempt("A1", 0.6, {}, tmp_path / "cal.json", tmp_path / "pair1" / "A1.json") is None
    assert len(list((tmp_path / "pair1").glob("A1.attempt*.invalid.json"))) == pilot.ATTEMPTS
