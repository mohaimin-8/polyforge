"""The simulator's predictions for the WP11 validation cells (programme 2).

What is validated is the simulator's plant model, `model.evaluate_step`,
with the plant-2 mechanics (mixture percentile, attainment), calibrated to
the testbed's own tiers. Each cell has fixed pools (no controller), so each
tier pool is one tenant with a fixed state, and every 10 s step of the
cell's exact offered demand (`demand_10s.csv` of make_schedule.py) is
evaluated under that tier's calibrated form.

Calibration, per tier, from its in-cluster load curve (testbed/measure/
load_curve.py writes research/plant2's format): the power form with L0
fitted and the p95/mean tail (research/plant2/fits.py), capacity per replica
= the measured service rate / the replica count it was measured at.

Rules fixed here, before any cell runs:
  SLO        a request is on time when its latency <= SLO_FACTOR x the small
             tier's unloaded mean latency (L0 of its calibration)
  cache      off = 0; small = 1 MiB; large = 16 MiB of gateway heap per tenant, on
             plant 2's cache curve (research/plant2/plant.py, measured by WP6:
             half-saturation 1.19 MiB), so small sits near the knee and large near
             saturation; the testbed sets the gateway's per-tenant knob to the
             equivalent completion bytes (WP6: knob MiB = heap MiB x 0.346)
  hit        a cache hit answers in CACHE_HIT_LATENCY_MS (the plant's constant)
  cost       pods x cores per pod x price per core-hour x duration
Run-level p95 is the 0.95 quantile of the request-weighted mixture of every
step's hit and miss distributions, as the testbed's p95 is over all requests.

    python predict.py --calibration ../out/curves --schedules ../out/schedules --out predictions.json
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (REPO / "research" / "jcac_sim", REPO / "research" / "plant2"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import model  # noqa: E402
from model import Demand, TenantConfig, TenantState  # noqa: E402

SLO_FACTOR = 3.0


def _cache_curve() -> dict:
    import plant

    table = {c.name: c.value for c in plant.constants() if "B" in c.modes.split(",")}
    return {"cache_hit_max": table["cache_hit_max"], "cache_half_mb": table["cache_half_mb"]}


CACHE_CURVE = _cache_curve()
CACHE_MB = {"off": 0, "small": 1, "large": 16}
TIER = {"small": "small", "mid": "mid"}
BIN_S = 10
Z95 = model._Z95


def calibrate(curve_dir: Path) -> dict:
    """Power form (L0 fitted), tail and capacity per replica of one tier."""
    import fits

    requests = pd.read_csv(curve_dir / "load_curve_requests.csv", float_precision="round_trip")
    meta = json.loads((curve_dir / "load_curve_meta.json").read_text(encoding="utf-8"))
    levels = fits.remeasured_levels(requests, meta)
    mu = 0.5 * (meta["mu_pre_rps"] + meta["mu_post_rps"])
    power = fits.fit_power_free(levels, mu)
    tail = fits.fit_tail(levels, mu)
    replicas = int(meta.get("replicas", 1))
    return {"a": power["a"], "l0_s": power["l0"], "f0": max(1.0, tail["f0"]), "b": max(0.0, tail["b"]),
            "capacity_per_replica_rps": mu / replicas, "measured_at_replicas": replicas}


@contextlib.contextmanager
def tier_form(cal: dict, slo_ms: float, cache_mb: int):
    """The plant-2 form for one tier, the SLO target, then the published form back."""
    saved_slo = dict(model.SLO_BASE_MS)
    model.set_model_form(
        congestion_exponent=min(2.0, max(1e-3, cal["a"])), p95_tail=(cal["f0"], cal["b"]),
        mixture_p95=True, attainment_slo=True, replica_capacity_wu=cal["capacity_per_replica_rps"],
        wu_ai_scale=1.0 / model.WORK_UNITS["chat"], wu_crud_scale=0.0, cacheable_uniform=True,
        tier_latency_ms={"small": cal["l0_s"] * 1000, "mid": cal["l0_s"] * 1000, "large": cal["l0_s"] * 1000})
    model.set_economy(tier_cost_usd_per_req={"small": 0.0, "mid": 0.0, "large": 0.0},
                      cache_hit_max=CACHE_CURVE["cache_hit_max"], cache_half_mb=CACHE_CURVE["cache_half_mb"])
    model.SLO_BASE_MS["ai"] = slo_ms  # a 'premium' tenant: class factor 1
    try:
        yield
    finally:
        model.SLO_BASE_MS.clear()
        model.SLO_BASE_MS.update(saved_slo)
        model.set_model_form()
        model.set_economy()


def _mixture_quantile(branches: list[tuple[float, float, float]], q: float = 0.95) -> float:
    """q-quantile of a weighted mixture of lognormals (weight, mean, sigma; sigma 0 = point mass)."""
    total = sum(w for w, _, _ in branches)
    lo = min(math.log(m) - 6 * s - 0.5 * s * s for _, m, s in branches)
    hi = max(math.log(m) + 6 * s for _, m, s in branches)
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        cdf = sum(w * model._lognormal_cdf(math.exp(mid), m, s) for w, m, s in branches) / total
        lo, hi = (mid, hi) if cdf < q else (lo, mid)
    return math.exp(0.5 * (lo + hi))


def predict_cell(cell: dict, demand: pd.DataFrame, cal: dict[str, dict], price_core_hr: float) -> dict:
    slo_ms = SLO_FACTOR * cal["small"]["l0_s"] * 1000
    cache_mb = CACHE_MB[cell["cache"]]
    per_step = demand.groupby("step")["count"].sum().sort_index()
    shares = {"small": cell["small_share"], "mid": 1.0 - cell["small_share"]}
    pods = {"small": cell["pools"][0], "mid": cell["pools"][1]}
    cores = {"small": 1, "mid": 2}
    branches, served, within, mean_num = [], 0.0, 0.0, 0.0
    for tier in ("small", "mid"):
        if shares[tier] <= 0:
            continue
        with tier_form(cal[tier], slo_ms, cache_mb):
            cfg = TenantConfig(tenant_id=tier, slo_class="premium", replica_max=8, hourly_budget_usd=1e9)
            state = TenantState(replicas=pods[tier], cache_mb=cache_mb, tier=TIER[tier])
            hit = model.hit_rate(cache_mb)
            for count in per_step:
                n = count * shares[tier]
                if n <= 0:
                    continue
                d = Demand(rps={"chat": n / BIN_S})
                m = model.evaluate_step(cfg, state, d)
                wu = d.work_units(cache_mb, state.tier)
                inflate = model.congestion(wu, state.replicas)
                rho = wu / (state.replicas * model.REPLICA_CAPACITY_WU)
                miss_ms = model.tier_base_latency_ms("chat", state.tier) * inflate
                sigma = model._lognormal_sigma(model.p95_factor(rho))
                branches += [(n * hit, model.CACHE_HIT_LATENCY_MS, 0.0), (n * (1 - hit), miss_ms, sigma)]
                mean_num += n * (hit * model.CACHE_HIT_LATENCY_MS + (1 - hit) * miss_ms)
                within += n * (1.0 - m.violation)
                served += n
    duration_s = len(per_step) * BIN_S
    branches = [b for b in branches if b[0] > 0]
    cost = sum(pods[t] * cores[t] for t in pods) * price_core_hr * duration_s / 3600.0
    return {"cell": cell["cell"], "slo_ms": slo_ms, "requests": served,
            "mean_latency_s": mean_num / served / 1000 if served else math.nan,
            "p95_latency_s": _mixture_quantile(branches) / 1000 if branches else math.nan,
            "attainment": within / served if served else math.nan,
            "throughput_rps": served / duration_s, "cost": cost}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--calibration", type=Path, required=True, help="dir with small/ and mid/ load curves")
    ap.add_argument("--schedules", type=Path, required=True, help="dir with cellNN/demand_10s.csv")
    ap.add_argument("--price-core-hr", type=float, default=0.526 / 4, help="USD per core-hour (T4 box / 4 vCPU)")
    ap.add_argument("--out", type=Path, default=HERE / "predictions.json")
    args = ap.parse_args()
    cells = json.loads((HERE / "cells.json").read_text(encoding="utf-8"))["cells"]
    cal = {t: calibrate(args.calibration / t) for t in ("small", "mid")}
    preds = [predict_cell(c, pd.read_csv(args.schedules / f"cell{c['cell']:02d}" / "demand_10s.csv"), cal,
                          args.price_core_hr) for c in cells]
    args.out.write_text(json.dumps({"calibration": cal, "slo_factor": SLO_FACTOR, "cache_mb": CACHE_MB,
                                    "predictions": preds}, indent=1), encoding="utf-8")
    print(f"wrote {args.out}: {len(preds)} cells; commit it before any cell runs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
