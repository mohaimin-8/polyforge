"""The WP17 plant's arithmetic."""

import math
import sys
from pathlib import Path

import pytest

import service_plant as plant

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jcac_sim"))
import model  # noqa: E402


def test_congestion_is_the_simulators_form():
    saved = model.CONGESTION_EXPONENT, model.REPLICA_CAPACITY_WU
    try:
        for a in (0.7, 1.0):
            model.set_model_form(congestion_exponent=a, replica_capacity_wu=1.0)
            for rho in (0.1, 0.5, 0.94, 0.96, 1.5, 9.0):
                assert plant.congestion(rho, a) == pytest.approx(model.congestion(rho, 1))
    finally:
        model.set_model_form()
    assert (model.CONGESTION_EXPONENT, model.REPLICA_CAPACITY_WU) == saved


def test_lognormal_matches_the_plant2_sigma():
    for tail in (1.2, 1.8, 2.4):
        sigma = model._lognormal_sigma(tail)
        for x in (0.3, 1.0, 2.5):
            assert plant.lognormal_cdf(x, 1.0, tail) == pytest.approx(model._lognormal_cdf(x, 1.0, sigma))


def test_more_cpu_serves_faster_and_costs_more():
    p = plant.Plant()
    small = p.step(8.0, [0.5, 0.5], 10.0)
    big = p.step(8.0, [1.0, 1.0], 10.0)
    assert big["mean_s"] < small["mean_s"] and big["late"] < small["late"]
    assert big["cost"] == pytest.approx(2 * small["cost"])
    assert small["usage"] == [pytest.approx(0.2), pytest.approx(0.2)]


def test_one_request_never_uses_more_than_a_core():
    p = plant.Plant()
    assert p.unloaded_s(2.0) == p.unloaded_s(1.0) == p.cpu_per_request_s
    assert p.unloaded_s(0.25) == pytest.approx(4 * p.cpu_per_request_s)


def test_usage_saturates_at_the_limit_and_no_pods_is_all_late():
    p = plant.Plant()
    assert p.pod(100.0, 0.5)[2] == 0.5
    out = p.step(3.0, [], 10.0)
    assert out["late"] == out["requests"] == 30.0 and math.isinf(out["mean_s"])


def test_without_an_efficiency_table_the_plant_is_exactly_the_linear_one():
    p = plant.Plant()
    for c in p.cpu_levels:
        assert p.capacity_rps(c) == c / p.cpu_per_request_s == p.capacity_rps(c, total_cpu=6.0)
        assert p.pod(3.0, c) == p.pod(3.0, c, total_cpu=6.0)
    assert p.eta(6.0) == 1.0


def test_the_efficiency_is_one_to_a_core_then_through_the_points_then_held():
    p = plant.Plant(efficiency=((6.0, 0.6), (2.0, 0.9)))
    for total, eta in ((0.5, 1.0), (1.0, 1.0), (1.5, 0.95), (2.0, 0.9), (4.0, 0.75), (6.0, 0.6), (8.0, 0.6)):
        assert p.eta(total) == pytest.approx(eta)
    assert p.capacity_rps(1.0, total_cpu=4.0) == pytest.approx(1.0 / p.cpu_per_request_s * 0.75)
    assert p.unloaded_s(1.0, total_cpu=4.0) == pytest.approx(p.cpu_per_request_s / 0.75)


def test_a_deployment_on_a_crowded_node_is_slower_and_uses_more_cpu_per_request():
    linear, crowded = plant.Plant(), plant.Plant(efficiency=((6.0, 0.6),))
    rate = 0.7 * 6 * linear.capacity_rps(1.0)
    a, b = linear.step(rate, [1.0] * 6, 30.0), crowded.step(rate, [1.0] * 6, 30.0)
    assert b["late"] > a["late"] and b["mean_s"] > a["mean_s"]
    assert b["usage"][0] == pytest.approx(min(rate / 6 * linear.cpu_per_request_s / 0.6, 1.0))
    assert crowded.step(rate / 6, [1.0], 30.0) == linear.step(rate / 6, [1.0], 30.0)   # one core: eta 1


CURVE = ((1.0, 20.0), (0.25, 3.0), (2.0, 30.0), (0.5, 8.0))    # (cores, one pod's rate), unsorted on purpose


def test_on_the_measured_curve_a_pod_serves_at_the_curves_rate_for_its_cpu():
    p = plant.Plant(capacity_curve=CURVE)
    for c, mu in CURVE:
        assert p.capacity_rps(c) == pytest.approx(mu) and p.work_s(c) == pytest.approx(c / mu)
        assert p.unloaded_s(c) == pytest.approx(c / mu / min(c, 1.0))
        assert p.pod(1e6, c)[2] == c                                 # saturated: its whole CPU
        assert p.pod(0.5 * mu, c)[2] == pytest.approx(0.5 * c)       # half its rate: half its CPU
    assert p.capacity_rps(1.5) == pytest.approx(25.0)                # linear in c between the points
    assert p.capacity_rps(4.0) == pytest.approx(60.0)                # CPU per request held beyond them
    assert p.capacity_rps(0.125) == pytest.approx(1.5)


def test_on_the_measured_curve_one_pod_is_the_curve_and_the_table_applies_from_two_pods():
    p = plant.Plant(capacity_curve=CURVE, efficiency=((2.0, 0.8),))
    assert p.step(15.0, [2.0], 30.0)["mean_s"] == pytest.approx(p.pod(15.0, 2.0)[0])
    assert p.step(15.0, [1.0, 1.0], 30.0)["mean_s"] == pytest.approx(p.pod(7.5, 1.0, total_cpu=2.0)[0])
    assert p.capacity_rps(1.0, total_cpu=2.0) == pytest.approx(20.0 * 0.8)
    linear = plant.Plant(efficiency=((2.0, 0.8),))                   # the linear form is unchanged:
    assert linear.step(15.0, [2.0], 30.0)["mean_s"] == pytest.approx(linear.pod(15.0, 2.0, total_cpu=2.0)[0])
