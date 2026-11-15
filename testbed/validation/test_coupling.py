"""coupling.py on made-up intervals, regressions and a synthetic calibration."""

import math
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import coupling  # noqa: E402
import predict  # noqa: E402

CAL = {"small": {"a": 0.8, "b": 0.0, "l0_s": 1.2, "f0": 1.8, "capacity_per_replica_rps": 0.4},
       "mid": {"a": 0.8, "b": 0.0, "l0_s": 2.4, "f0": 1.8, "capacity_per_replica_rps": 0.15}}
SLO_MS = predict.SLO_FACTOR * CAL["small"]["l0_s"] * 1000


def _demand(rate_rps: float, share: float = 0.5) -> dict:
    out = {}
    for step in range(predict.cs.STEPS):
        out[(step, "t00")] = rate_rps * 10 * share
        out[(step, "t01")] = rate_rps * 10 * (1 - share)
    return out


def test_mean_inflight_averages_the_overlap_over_the_window():
    starts, ends = np.array([0.0, 5.0, 20.0]), np.array([10.0, 15.0, 30.0])
    assert coupling.mean_inflight(starts, ends, 0.0, 10.0) == pytest.approx((10 + 5) / 10)
    assert coupling.mean_inflight(starts, ends, 0.0, 10.0, exclude=0) == pytest.approx(0.5)
    assert coupling.mean_inflight(starts, ends, 16.0, 19.0) == 0.0


def test_busy_cores_cap_in_flight_at_the_pods_and_weigh_by_cores():
    starts, ends = np.array([0.0, 0.0, 0.0]), np.array([0.9, 0.9, 0.9])  # three in flight over two grid cells
    cum = coupling.busy_cores_cumsum(starts, ends, pods=2, cores=2, t0=0.0, n=4)
    assert cum.tolist() == [0.0, 4.0, 8.0, 8.0, 8.0]


def test_the_fixed_effects_regression_recovers_known_slopes():
    rng = np.random.default_rng(1)
    rows = []
    for cell in range(4):
        for run in range(3):
            for _ in range(60):
                own, other = rng.uniform(0, 2), rng.uniform(0, 4)
                y = 0.5 * cell + 0.15 * own + 0.2 * other + rng.normal(0, 0.01)
                rows.append((y, own, other, cell, f"{cell}/{run}"))
    fit = coupling.ols_fixed_effects(rows)
    assert fit["own"] == pytest.approx(0.15, abs=0.005) and fit["other"] == pytest.approx(0.2, abs=0.005)
    assert fit["runs"] == 12 and fit["n"] == 720 and 0 < fit["other_se"] < 0.01


def test_without_coupling_it_is_predict_cells_own_attainment():
    cell = {"cell": 0, "pools": [1, 1], "cache": "off"}
    demand = _demand(0.3)
    expected = predict.predict_cell(cell, demand, predict.cs.PINS, None, CAL, 0.1)["attainment"]
    assert coupling.coupled_attainment(cell, demand, {}, CAL, 0.0, SLO_MS) == pytest.approx(expected, abs=1e-12)


def test_a_stronger_coupling_lowers_attainment_and_an_idle_other_tier_changes_nothing():
    cell = {"cell": 0, "pools": [1, 1], "cache": "off"}
    demand = _demand(0.3)
    att = [coupling.coupled_attainment(cell, demand, {}, CAL, g, SLO_MS) for g in (0.0, 0.1, 0.3)]
    assert att[0] > att[1] > att[2]
    alone = _demand(0.2, share=1.0)  # nothing for the mid tier: no busy cores to couple through
    assert coupling.coupled_attainment(cell, alone, {}, CAL, 0.3, SLO_MS) == pytest.approx(
        coupling.coupled_attainment(cell, alone, {}, CAL, 0.0, SLO_MS), abs=1e-12)


def test_spearman_is_the_rank_correlation():
    assert coupling.spearman(np.array([1.0, 2.0, 3.0]), np.array([10.0, 30.0, 20.0])) == pytest.approx(0.5)
    assert math.isclose(coupling.spearman(np.array([1.0, 2.0, 3.0]), np.array([3.0, 2.0, 1.0])), -1.0)
