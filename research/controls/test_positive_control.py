"""WP22 positive control: a strong interaction by construction gives a large
index and a large loss of per-knob search, at the unit level (T3), inside
the real planner (spike-in) and in the pilot's strongest regime."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import controls_common  # noqa: E402,F401  (sets the import paths)
import positive_control as pc  # noqa: E402
import pytest  # noqa: E402


def test_t3_scaled_up_reaches_the_bound():
    for r in pc.unit_t3(rhos=(1.0, 1e3, 1e6), eta_share=1e-3).itertuples():
        assert r.index == pytest.approx(r.rho, rel=1e-12)
        assert r.bound == pytest.approx(r.rho, rel=1e-12)
        assert r.loss == pytest.approx(r.rho - r.eta, rel=1e-12)
        assert r.loss_over_index == pytest.approx(1.0 - 1e-3, rel=1e-9)


def test_embedded_ridge_keeps_index_and_loss():
    a = pc.unit_embedded(shape=(5, 3, 4), rho=1e3, eta=10.0, anchor=(2, 1, 1))
    assert a.oscillation == pytest.approx(1e3)
    assert a.loss == pytest.approx(990.0) and a.bound == pytest.approx(1e3)
    assert a.quantile_index == pytest.approx(1e3)  # 4 of 60 points: inside the 5% tail


def test_quantile_index_misses_a_concentrated_ridge():
    """4 ridge points of a width-16 box (33 x 6 x 4 = 792): the worst-case
    index and the loss see the ridge, the 5%-trimmed index does not."""
    a = pc.unit_embedded(shape=(33, 6, 4), rho=1e3, eta=10.0, anchor=(16, 2, 1))
    assert a.oscillation == pytest.approx(1e3) and a.loss == pytest.approx(990.0)
    assert a.quantile_index == 0.0


def test_spike_in_is_recovered_by_the_planner_and_the_logger():
    s = pc.spike_run(steps=8)
    assert s["ridge_present"] == s["decisions"] == 64
    assert s["index_min"] == pytest.approx(pc.RIDGE_RHO, rel=1e-9)
    assert s["index_max"] == pytest.approx(pc.RIDGE_RHO, rel=1e-9)
    native = 10.0  # the native objective's spread on this control is a few units
    expected = pc.RIDGE_RHO - pc.RIDGE_ETA
    assert expected - native <= s["loss_min"] <= s["loss_max"] <= expected + native
    assert s["bound_min"] == pytest.approx(pc.RIDGE_RHO, rel=1e-9)
    assert s["per_knob_on_ridge"] == s["ridge_present"]
    assert s["theorem_violations"] == 0
    assert s["planner_disagreement_share"] > 0.0  # the planner's own decoupled search falls in


def test_strong_regime_has_large_index_and_positive_gain():
    workload, mix, size = pc.DESIGN[0]
    strong = pc.regime_run(workload, mix, size, 15, **pc.STRONG)
    weak = pc.regime_run(workload, mix, size, 15, **pc.WEAK)
    assert strong["theorem_violations"] == 0 and weak["theorem_violations"] == 0
    assert pc.regime_passes(strong, weak)
    assert strong["gain"] > 0.05 and strong["loss_mean"] > 10 * weak["loss_mean"]
