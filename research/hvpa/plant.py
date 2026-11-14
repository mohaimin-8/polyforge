"""The WP17 plant: a service that burns a fixed amount of CPU per request,
horizontally (replicas) and vertically (CPU per pod) scaled (programme 2).

Per pod with CPU c cores, receiving lambda / r of the requests (the Service
spreads them evenly):
  capacity    mu(c) = c / w requests per second (w CPU-seconds per request),
              linear in c as WP17 step 3 checks within +-10%; a calibration
              may replace it by its measured curve;
  latency     mean = s(c) x g(rho), rho = (lambda / r) / mu(c), s(c) = w /
              min(c, 1) (one request runs on at most one core), g the
              simulator's congestion form: (1 - rho)^-a below 0.95, then
              graded quadratic growth, capped at 50 (research/jcac_sim
              model.congestion);
  on time     a lognormal with that mean and p95 / mean = f0 x (1 - min(rho,
              0.95))^-b, the plant-2 tail; attainment = its CDF at the SLO;
  usage       min(lambda / r x w, c) cores, what metrics-server reports;
  cost        requested core-seconds x price (the plan's cost measure).
Every constant here is an a-priori placeholder until WP17 step 3 measures
the real service; `from_calibration` reads those measurements.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from pathlib import Path

SATURATION_RHO = 0.95
MAX_CONGESTION = 50.0
Z95 = 1.6448536269514722


def congestion(rho: float, a: float) -> float:
    if rho < SATURATION_RHO:
        return (1.0 - rho) ** (-a)
    at_sat = (1.0 - SATURATION_RHO) ** (-a)
    return min(MAX_CONGESTION, at_sat * (rho / SATURATION_RHO) ** 2)


def lognormal_cdf(x: float, mean: float, tail: float) -> float:
    """P(X <= x) for a lognormal with this mean and p95 / mean = tail (>= 1)."""
    if x <= 0:
        return 0.0
    if tail <= 1.0:
        return 1.0 if x >= mean else 0.0
    # p95/mean = exp(z sigma - sigma^2 / 2): the smaller root
    sigma = Z95 - math.sqrt(max(0.0, Z95 * Z95 - 2.0 * math.log(tail)))
    if sigma <= 0:
        return 1.0 if x >= mean else 0.0
    mu = math.log(mean) - 0.5 * sigma * sigma
    return 0.5 * (1.0 + math.erf((math.log(x) - mu) / (sigma * math.sqrt(2.0))))


@dataclass(frozen=True)
class Plant:
    cpu_per_request_s: float = 0.05
    congestion_exponent: float = 1.0
    tail_f0: float = 1.8
    tail_b: float = 0.3
    slo_s: float = 0.5
    price_core_hr: float = 0.526 / 4
    cpu_levels: tuple = (0.25, 0.5, 1.0, 2.0)
    status: str = "a-priori placeholder"

    def capacity_rps(self, cpu: float) -> float:
        return cpu / self.cpu_per_request_s

    def unloaded_s(self, cpu: float) -> float:
        return self.cpu_per_request_s / min(1.0, cpu)

    def pod(self, rate_rps: float, cpu: float) -> tuple[float, float, float]:
        """(mean latency s, share on time, CPU usage cores) of one pod."""
        rho = rate_rps / self.capacity_rps(cpu)
        mean = self.unloaded_s(cpu) * congestion(rho, self.congestion_exponent)
        tail = self.tail_f0 * (1.0 - min(rho, SATURATION_RHO)) ** (-self.tail_b)
        return mean, lognormal_cdf(self.slo_s, mean, tail), min(rate_rps * self.cpu_per_request_s, cpu)

    def step(self, rate_rps: float, cpus: list[float], dt_s: float) -> dict:
        """One interval of the whole Deployment: requests, late ones, cost, usage per pod."""
        if not cpus:
            n = rate_rps * dt_s
            return {"requests": n, "late": n, "mean_s": math.inf, "cost": 0.0, "usage": []}
        per = rate_rps / len(cpus)
        pods = [self.pod(per, c) for c in cpus]
        n = rate_rps * dt_s
        on_time = sum(p[1] for p in pods) / len(pods)
        return {"requests": n, "late": n * (1.0 - on_time),
                "mean_s": sum(p[0] for p in pods) / len(pods),
                "cost": sum(cpus) * dt_s / 3600.0 * self.price_core_hr, "usage": [p[2] for p in pods]}


def from_calibration(path: Path) -> Plant:
    """The plant as WP17 step 3 measured it (written by the calibration script)."""
    cal = json.loads(Path(path).read_text(encoding="utf-8"))
    return replace(Plant(), cpu_per_request_s=cal["cpu_per_request_s"],
                   congestion_exponent=cal["congestion_exponent"], tail_f0=cal["tail_f0"],
                   tail_b=cal["tail_b"], slo_s=cal["slo_s"], status=f"measured: {path.name}")
