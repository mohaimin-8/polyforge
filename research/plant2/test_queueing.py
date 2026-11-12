"""The Ramani-Tantawi chain against cases with a known closed form."""

import math
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from queueing import (RTParams, batch_time, capacity, chunk_count, power_latency,  # noqa: E402
                      service_time, steady_state, work)

P = RTParams.from_ms(6.68, 2.01e-2, 5.52e-5)   # the paper's Table I, Llama-3.1-8B


def test_one_slot_is_mm1():
    n, m = 200, 100
    tau = service_time(1, n, m, P)
    for rho in (0.1, 0.5, 0.9):
        out = steady_state(rho / tau, n, m, 1, P)
        assert out.rho == pytest.approx(rho)
        assert out.response == pytest.approx(tau / (1.0 - rho), rel=1e-12)
        assert out.n_mean == pytest.approx(rho / (1.0 - rho), rel=1e-12)


def test_no_overhead_and_a_wide_batch_is_processor_sharing():
    """alpha = 0: the chain's down rate i/tau(i) = 1/W_tot is constant, so it
    is M/M/1 with service time W_tot whatever the batch limit."""
    p = RTParams(0.0, 1e-4, 1e-6)
    n, m = 64, 32
    w_tot = work(n, m, 1, p)[2]
    for rho in (0.2, 0.7):
        out = steady_state(rho / w_tot, n, m, 400, p)
        assert out.response == pytest.approx(w_tot / (1.0 - rho), rel=1e-9)


def test_unlimited_budget_is_one_chunk_and_matches_appendix_b():
    n, m = 300, 50
    assert chunk_count(7, n, m, None) == 1
    w_tot = work(n, m, 1, P)[2]
    assert w_tot == pytest.approx(P.beta * (n + m) + P.gamma * (m + 1) * (n + m / 2.0))


def test_chunk_count_limits_from_appendix_a():
    assert chunk_count(1, 5000, 100, 2048) == math.ceil(5000 / 2048)
    # long inputs: c(i) ~ ceil(i*n/M)
    assert chunk_count(8, 4096, 1, 8192) == math.ceil(8 * 4096 / 8192)


def test_service_time_telescopes():
    """tau(i) = (c+m)*T(i-1) + W_tot = (c+m)*T(i) (eq. tau)."""
    n, m, i = 128, 64, 5
    w_tot = work(n, m, 1, P)[2]
    d = w_tot / (1 + m)
    assert service_time(i, n, m, P) == pytest.approx((1 + m) * (P.alpha + (i - 1) * d) + w_tot)


def test_batch_time_and_capacity():
    n, m, b = 50, 48, 64
    assert batch_time(b, n, m, P) == pytest.approx((1 + m) * P.alpha + b * work(n, m, 1, P)[2])
    assert capacity(n, m, b, P) == pytest.approx(b / batch_time(b, n, m, P))


def test_latency_rises_with_load_and_light_load_is_unloaded_service():
    n, m, b = 256, 128, 32
    mu = capacity(n, m, b, P)
    resp = [steady_state(f * mu, n, m, b, P).response for f in (1e-6, 0.3, 0.6, 0.9)]
    assert all(x < y for x, y in zip(resp, resp[1:]))
    assert resp[0] == pytest.approx(service_time(1, n, m, P), rel=1e-4)


def test_ttft_and_itl_are_consistent_with_service():
    n, m, b = 512, 256, 64
    out = steady_state(0.5 * capacity(n, m, b, P), n, m, b, P)
    assert out.prefill + m * out.itl == pytest.approx(out.service)
    assert out.ttft == pytest.approx(out.wait + out.prefill + out.itl)


def test_refusals():
    with pytest.raises(ValueError):
        steady_state(2.0 * capacity(50, 48, 4, P), 50, 48, 4, P)
    with pytest.raises(ValueError):
        RTParams(-1.0, 0.0, 0.0)
    with pytest.raises(ValueError):
        power_latency(2.0, 1.0, 1.0, 0.86)
