"""The WP17 twin's mechanics: clocks, the node, the planners' bookkeeping, the arms."""

import math

import numpy as np
import pytest

import twin
import workloads
from service_plant import Plant

P = Plant()


def test_periodic_events_fall_in_their_10s_steps():
    hpa = [k for k in range(12) if twin.ticks(k * 10.0, 15.0)]
    assert hpa == [0, 1, 3, 4, 6, 7, 9, 10]     # 0, 15, 30, 45, 60, ... s
    assert [k for k in range(12) if twin.ticks(k * 10.0, 60.0)] == [0, 6]
    assert [k for k in range(7) if twin.ticks(k * 10.0, 30.0)] == [0, 3, 6]


def test_static_cost_is_cores_times_time_times_price():
    rates = np.full(36, 5.0)
    res = twin.run(twin.Static(2, 0.5), P, rates)
    assert res["cost"] == pytest.approx(2 * 0.5 * 360 / 3600 * P.price_core_hr)
    assert 0.0 < res["attainment"] <= 1.0 and res["resizes"] == 0


def test_the_node_leaves_pods_pending_and_recreates_a_deferred_resize():
    cl = twin.Cluster()
    cl.start(4, 1.0)
    cl.scale_to(8, 1.0, 0.0)
    assert sum(p.scheduled_s is None for p in cl.pods) == 2      # 6 cores fit
    cl.scale_to(4, 1.0, 10.0)
    assert all(p.scheduled_s is not None for p in cl.pods)
    cl.resize(cl.pods[0], 3.0, 20.0)                            # 6 cores: fits
    cl.resize(cl.pods[1], 2.0, 20.0)                            # 7 cores: deferred
    assert cl.pods[1].resize_to == 2.0 and cl.resizes == 1
    cl.tick(20.0 + twin.RESIZE_DEFER_LIMIT_S)
    assert cl.recreations == 1 and cl.pods[1].cpu == 2.0 and cl.pods[1].scheduled_s is None


def test_joint_is_never_worse_on_its_own_lattice_and_index_is_nonnegative():
    for width in (1, 2, 4):
        for rate in (2.0, 15.0, 40.0, 70.0):
            for r, c in ((2, 0.5), (3, 1.0), (1, 2.0)):
                choice, info = twin.Planner(True, width).decide(P, rate, r, c)
                assert info["index"] >= -1e-15
                if math.isfinite(info["loss"]):
                    assert info["loss"] >= -1e-15


def test_planners_respond_to_load_and_scale_back():
    rates = workloads.shape("step_up", workloads.base_rps(P))
    joint = twin.run(twin.Planner(True, 2), P, rates)
    static = twin.run(twin.Static(*twin.INITIAL), P, rates)
    assert joint["J"] < static["J"] and joint["decisions"] == len(rates) // 3
    assert twin.clairvoyant(P, rates) <= joint["J"] + 1e-12


def test_hpa_vpa_arms_run_and_the_vpa_raises_starved_pods():
    rates = workloads.shape("step_up", workloads.base_rps(P))
    a1 = twin.run(twin.HpaVpa("cpu", P), P, rates)
    a2 = twin.run(twin.HpaVpa("rate", P), P, rates)
    assert 0 < a1["attainment"] <= 1 and 0 < a2["attainment"] <= 1
    # HPA holding 80 % keeps the median below the default VPA's lower bound: no resize
    assert a1["resizes"] == 0
    # at the replica ceiling the pods saturate, and the VPA raises them
    flood = twin.run(twin.HpaVpa("cpu", P), P, np.full(120, 150.0))
    assert flood["resizes"] > 0


def test_the_fast_setting_lets_the_vpa_lower_an_oversized_request():
    rates = np.full(360, 2.0)              # far below what 2 x 0.5 core serve
    fast = twin.run(twin.HpaVpa("rate", P, vpa_setting="fast", vpa_min_replicas=1), P, rates)
    slow = twin.run(twin.HpaVpa("rate", P, vpa_setting="default", vpa_min_replicas=1), P, rates)
    assert fast["resizes"] > 0 and slow["resizes"] == 0 and fast["cost"] < slow["cost"]


def test_noise_keeps_the_mean_and_shapes_are_as_documented():
    rng = np.random.default_rng(0)
    base = workloads.shape("ramp", 10.0)
    assert base[0] == 10.0 and base[-1] == pytest.approx(40.0)
    long = workloads.noisy(np.full(200000, 10.0), 0.4, rng)
    assert long.mean() == pytest.approx(10.0, rel=0.02)
    burst = workloads.shape("burst_train", 1.0)
    assert burst[:12].tolist() == [3.0] * 12 and burst[12] == 1.0
