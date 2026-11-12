"""Latency against load for one model server, in two forms (programme 2, WP2).

1. Empirical: mean latency L(rho) = L0 * (1 - rho)^(-a), rho = lambda / mu,
   the form `research/calibration/measure_congestion.py` fitted (a = 0.86).

2. Published: Ramani and Tantawi, "An Approximate Queueing Model of LLM
   Inference Serving for SLO-Driven Autoscaling", arXiv:2609.20957v1 (2026).
   Implemented from the paper's TeX source, section IV and appendices A-B:

   - work of an (n, m) request with c effective prefill stages:
       W_pref = beta*n + gamma*n*(c+1)/2                       (eq. wpref)
       W_dec  = beta*m + gamma*m*(n + (m+1)/2)                 (eq. wdec)
       W_tot  = W_pref + W_dec,   delta = W_tot / (c+m)        (eqs. wtotal, delta)
   - iteration time with x active requests: T(x) = alpha + x*delta  (eq. titer)
   - service time in state i <= B: tau(i) = (c(i)+m) * T(i)    (eq. tau)
   - birth-death chain: up rate lambda, down rate i/tau(i) for i <= B and
     B/tau(B) above; rho = lambda*tau(B)/B < 1                 (eq. rho)
   - N, X (mean batch), Q = N - X; S = X/lambda, W = Q/lambda (Little)
   - at the mean batch X and its chunk count c(X):
       Tp = c*T(X-1) + W_pref,  ITL = (S - Tp)/m,  TTFT = W + Tp + ITL
   - chunk count under a per-iteration token budget M (appendix A):
       Phi_i = m*(M-i+1) - i*n,  c_min = (-Phi + sqrt(Phi^2 + 4*M*n*m)) / (2M),
       c(i) = ceil(c_min);  M = None is the unlimited case c = 1 (appendix B).

   The paper's end-to-end mean latency is W + S = N / lambda.

Times are in seconds throughout this module; the paper reports alpha in ms and
beta, gamma in ms per token, so `RTParams.from_ms` converts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class RTParams:
    """alpha: baseline time per iteration; beta: compute time per token;
    gamma: KV-cache access time per token. Seconds."""

    alpha: float
    beta: float
    gamma: float

    @classmethod
    def from_ms(cls, alpha_ms: float, beta_ms: float, gamma_ms: float) -> "RTParams":
        return cls(alpha_ms / 1000.0, beta_ms / 1000.0, gamma_ms / 1000.0)

    def __post_init__(self) -> None:
        if min(self.alpha, self.beta, self.gamma) < 0.0:
            raise ValueError("alpha, beta and gamma must be non-negative")


@dataclass(frozen=True)
class RTMetrics:
    rho: float          # lambda * tau(B) / B
    p_wait: float       # probability that more than B requests are present
    n_mean: float       # mean number in system
    x_mean: float       # mean batch size
    q_mean: float       # mean number waiting
    service: float      # mean service time S = X / lambda
    wait: float         # mean waiting time W = Q / lambda
    response: float     # mean end-to-end latency W + S = N / lambda
    prefill: float      # Tp at the mean batch
    itl: float          # inter-token latency at the mean batch
    ttft: float         # time to first token


def chunk_count(i: float, n: float, m: float, budget: int | None) -> int:
    """Effective prefill stages at occupancy i (appendix A); 1 if unlimited."""
    if budget is None:
        return 1
    if n <= 0 or m <= 0:
        raise ValueError("input and output lengths must be positive")
    phi = m * (budget - i + 1.0) - i * n
    c_min = (-phi + math.sqrt(phi * phi + 4.0 * budget * n * m)) / (2.0 * budget)
    return max(1, math.ceil(c_min - 1e-12))


def work(n: float, m: float, c: int, p: RTParams) -> tuple[float, float, float]:
    """(W_pref, W_dec, W_tot) of an (n, m) request with c prefill stages."""
    w_pref = p.beta * n + p.gamma * n * (c + 1) / 2.0
    w_dec = p.beta * m + p.gamma * m * (n + (m + 1) / 2.0)
    return w_pref, w_dec, w_pref + w_dec


def delta(n: float, m: float, c: int, p: RTParams) -> float:
    return work(n, m, c, p)[2] / (c + m)


def iteration_time(x: float, n: float, m: float, c: int, p: RTParams) -> float:
    return p.alpha + x * delta(n, m, c, p)


def service_time(i: int, n: float, m: float, p: RTParams, budget: int | None = None) -> float:
    """tau(i) = (c(i) + m) * T(i) for 1 <= i <= B (eq. tau)."""
    c = chunk_count(i, n, m, budget)
    return (c + m) * iteration_time(i, n, m, c, p)


def batch_time(x: int, n: float, m: float, p: RTParams, budget: int | None = None) -> float:
    """Wall time of a static batch of x identical requests that stay together
    for all their c + m iterations: tau(x) = (c+m)*alpha + x*W_tot."""
    return service_time(x, n, m, p, budget)


def capacity(n: float, m: float, batch: int, p: RTParams, budget: int | None = None) -> float:
    """Saturation throughput B / tau(B), requests per second."""
    return batch / service_time(batch, n, m, p, budget)


def steady_state(lam: float, n: float, m: float, batch: int, p: RTParams,
                 budget: int | None = None) -> RTMetrics:
    """The chain's mean metrics at arrival rate `lam` (requests per second)."""
    if lam <= 0.0:
        raise ValueError("arrival rate must be positive")
    if batch < 1:
        raise ValueError("batch limit must be at least 1")
    i = np.arange(1, batch + 1)
    tau = np.array([service_time(int(k), n, m, p, budget) for k in i])
    rho = lam * tau[-1] / batch
    if rho >= 1.0:
        raise ValueError(f"no steady state: rho = {rho:.4f} >= 1")
    # log w_i = sum_{l<=i} log g(l), g(l) = lam * tau(l) / l; w_0 = 1.
    log_w = np.concatenate(([0.0], np.cumsum(np.log(lam * tau / i))))
    top = log_w.max()
    w = np.exp(log_w - top)
    tail = rho / (1.0 - rho)                       # sum_{k>=1} rho^k
    tail_moment = batch * tail + rho / (1.0 - rho) ** 2   # sum_{k>=1} (B+k) rho^k
    z = w.sum() + w[-1] * tail
    pi = w / z
    p_wait = pi[-1] * tail
    k = np.arange(batch + 1)
    head_moment = float((k * pi).sum())
    n_mean = head_moment + pi[-1] * tail_moment
    x_mean = head_moment + batch * p_wait
    q_mean = n_mean - x_mean
    service = x_mean / lam
    wait = q_mean / lam
    c = chunk_count(x_mean, n, m, budget)
    w_pref = work(n, m, c, p)[0]
    prefill = c * iteration_time(x_mean - 1.0, n, m, c, p) + w_pref
    itl = (service - prefill) / m
    return RTMetrics(rho=float(rho), p_wait=float(p_wait), n_mean=float(n_mean),
                     x_mean=float(x_mean), q_mean=float(q_mean), service=float(service),
                     wait=float(wait), response=float(wait + service), prefill=float(prefill),
                     itl=float(itl), ttft=float(wait + prefill + itl))


def power_latency(lam: float | np.ndarray, l0: float, mu: float, a: float):
    """Empirical form: L0 * (1 - lam/mu)^(-a), defined below saturation."""
    rho = np.asarray(lam, dtype=float) / mu
    if np.any(rho >= 1.0):
        raise ValueError("power form is defined for rho < 1 only")
    return l0 * (1.0 - rho) ** (-a)
