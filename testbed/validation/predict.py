"""The simulator's predictions for the WP11 validation cells (programme 2).

What is validated is the simulator's plant model, `model.evaluate_step` with
the plant-2 mechanics (mixture percentile, attainment), calibrated to the
testbed's own tiers. A cell has fixed pools (no controller): each tier pool
serves the tenants pinned to it (cell_schedules.py: t00 small, t01 mid), and
every 10 s step of the cell's exact offered demand (its request table,
inputs/cellNN.csv.gz) is evaluated under that tier's calibrated form.

Calibration, per tier, from its in-cluster load curve at one replica
(testbed/measure/load_curve.py --phases all --prompts, research/plant2's
format), the prompts being calibration prompts that are not the cells'
(cell_schedules.py calibration-prompts):
  shape   the congestion exponent a (power form, L0 fitted) and the tail's
          growth b (research/plant2/fits.py), from the protocol's levels;
  level   on the cells' kind of request: L0 = the fitted L0 x l0_trace / l0
          (unloaded means, real prompts over the protocol's), f0 = p95 / mean
          of the real-prompt baseline, capacity per replica = mu_trace (the
          closed loop on real prompts).
Capacity grows linearly with replicas, as in the simulator. Second declared
secondary (`measured_capacity`), fixed before any run once the curves showed
the small tier's capacity at 2 and 3 pods well below linear: the same, with
each pool's capacity per replica = the closed-loop rate measured at the
cell's own replica count / that count (calibrate.py measures small at 1-3,
mid at 1-2 replicas), so the cells separate the replica-scaling error from
the rest of the model.

Cache: whether each request hits, from cell_hits.py (the cell's own prompts
through the gateway's cache logic), its stores timed by a first pass without
hits; a hit answers in CACHE_HIT_LATENCY_MS and is on time and the misses
load the tier, the simulator's own arithmetic for a hit share
(test_predict.py checks that the two agree). Secondary, declared before any
run: the same with the steady-state hit rate of WP6's curve at threshold
0.95 for the cell's knob (`steady_cache`), what the curve alone would
predict for runs this short.

Rules fixed here, before any cell runs:
  SLO      on time = latency <= SLO_FACTOR x the small tier's calibrated L0
  scored   steps from cell_schedules.WARMUP_S on (warm-up requests are served
           and cached, not scored)
  cost     pods x cores per pod x price per core-hour x scored duration
Run-level p95 is the 0.95 quantile of the request-weighted mixture of every
scored step's hit and miss distributions, as the testbed's p95 is over all
answered requests.

    python predict.py --calibration ../out/curves --out predictions.json
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (REPO / "research" / "jcac_sim", REPO / "research" / "plant2", HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import model  # noqa: E402
from model import Demand, TenantConfig, TenantState  # noqa: E402

import cell_schedules as cs  # noqa: E402

SLO_FACTOR = 3.0
BIN_S = cs.mk.BIN_S
CORES = {"small": 1, "mid": 2}
WARMUP_STEPS = cs.WARMUP_S // BIN_S
WP6_CACHE = REPO / "research" / "cachereplay" / "out" / "full" / "plant2_cache.json"
TESTBED_TAU = "0.95"


def _p95(x: np.ndarray) -> float:
    s = np.sort(np.asarray(x, dtype=float))
    return float(s[max(0, math.ceil(0.95 * len(s)) - 1)])


def calibrate(curve_dir: Path) -> dict:
    """One tier's plant-2 form for the cells' requests (see the docstring)."""
    import fits

    requests = pd.read_csv(curve_dir / "load_curve_requests.csv", float_precision="round_trip")
    meta = json.loads((curve_dir / "load_curve_meta.json").read_text(encoding="utf-8"))
    missing = [k for k in ("l0_s", "l0_trace_s", "mu_trace_rps") if k not in meta]
    if missing:
        raise ValueError(f"{curve_dir}: {missing} missing; measure with --phases all --prompts")
    levels = fits.remeasured_levels(requests, meta)
    mu = 0.5 * (meta["mu_pre_rps"] + meta["mu_post_rps"])
    power = fits.fit_power_free(levels, mu)
    tail = fits.fit_tail(levels, mu)
    trace = requests[(requests.phase == "baseline_trace") & requests.latency_s.notna()].latency_s.to_numpy(float)
    f0_trace = _p95(trace) / float(trace.mean())
    replicas = int(meta.get("replicas", 1))
    return {"a": power["a"], "b": max(0.0, tail["b"]), "l0_s": power["l0"] * meta["l0_trace_s"] / meta["l0_s"],
            "f0": max(1.0, f0_trace), "capacity_per_replica_rps": meta["mu_trace_rps"] / replicas,
            "measured_at_replicas": replicas,
            "protocol": {"l0_fitted_s": power["l0"], "l0_measured_s": meta["l0_s"], "f0": tail["f0"], "mu_rps": mu},
            "trace": {"l0_s": meta["l0_trace_s"], "p95_over_mean": f0_trace, "mu_rps": meta["mu_trace_rps"],
                      "baseline_n": int(len(trace))}}


@contextlib.contextmanager
def tier_form(cal: dict, slo_ms: float):
    """The plant-2 form of one tier (no cache in the model: hits are data
    here), the SLO target, then the published form back."""
    saved_slo = dict(model.SLO_BASE_MS)
    model.set_model_form(
        congestion_exponent=min(2.0, max(1e-3, cal["a"])), p95_tail=(cal["f0"], cal["b"]),
        mixture_p95=True, attainment_slo=True, replica_capacity_wu=cal["capacity_per_replica_rps"],
        wu_ai_scale=1.0 / model.WORK_UNITS["chat"], wu_crud_scale=0.0, cacheable_uniform=True,
        tier_latency_ms={t: cal["l0_s"] * 1000 for t in ("small", "mid", "large")})
    model.set_economy(tier_cost_usd_per_req={"small": 0.0, "mid": 0.0, "large": 0.0})
    model.SLO_BASE_MS["ai"] = slo_ms  # a 'premium' tenant: class factor 1
    try:
        yield TenantConfig(tenant_id="pool", slo_class="premium", replica_max=8, hourly_budget_usd=1e9)
    finally:
        model.SLO_BASE_MS.clear()
        model.SLO_BASE_MS.update(saved_slo)
        model.set_model_form()
        model.set_economy()


def step_outcome(cfg: TenantConfig, pods: int, tier: str, n: float, hits: float) -> dict:
    """One step of one pool inside tier_form: n requests, `hits` of them hits."""
    hit_ms = model.CACHE_HIT_LATENCY_MS
    misses = n - hits
    out = {"within": hits * (1.0 if hit_ms <= model.SLO_BASE_MS["ai"] else 0.0), "mean_num": hits * hit_ms,
           "branches": [(hits, hit_ms, 0.0)] if hits > 0 else [], "miss_ms": math.nan}
    if misses > 0:
        d = Demand(rps={"chat": misses / BIN_S})
        m = model.evaluate_step(cfg, TenantState(replicas=pods, cache_mb=0, tier=tier), d)
        wu = d.work_units(0, tier)
        rho = wu / (pods * model.REPLICA_CAPACITY_WU)
        miss_ms = model.tier_base_latency_ms("chat", tier) * model.congestion(wu, pods)
        out["within"] += misses * (1.0 - m.violation)
        out["mean_num"] += misses * miss_ms
        out["branches"].append((misses, miss_ms, model._lognormal_sigma(model.p95_factor(rho))))
        out["miss_ms"] = miss_ms
    return out


def demand_from_table(table: list[dict], steps: int = cs.STEPS) -> dict[tuple[int, str], int]:
    out: dict[tuple[int, str], int] = {}
    for r in table:
        key = (min(int(r["t"] // BIN_S), steps - 1), r["tenant"])
        out[key] = out.get(key, 0) + 1
    return out


def _pool_counts(demand: Mapping, tenants: list[str], step: int, hits: Mapping | None) -> tuple[float, float]:
    n = float(sum(demand.get((step, t), 0) for t in tenants))
    h = float(sum(hits.get((step, t), 0) for t in tenants)) if hits else 0.0
    return n, h


def miss_latency_s(cell: dict, demand: Mapping, pins: Mapping[str, str], cal: dict) -> dict[tuple[str, int], float]:
    """Each tenant's predicted mean miss latency per step with no hits: when a
    miss's answer returns, and so when the gateway stores it."""
    slo_ms = SLO_FACTOR * cal["small"]["l0_s"] * 1000
    pods = {"small": cell["pools"][0], "mid": cell["pools"][1]}
    out = {}
    for tier in ("small", "mid"):
        tenants = [t for t, p in pins.items() if p == tier]
        with tier_form(cal[tier], slo_ms) as cfg:
            for step in range(cs.STEPS):
                n, _ = _pool_counts(demand, tenants, step, None)
                if n > 0:
                    ms = step_outcome(cfg, pods[tier], tier, n, 0.0)["miss_ms"]
                    out.update({(t, step): ms / 1000 for t in tenants})
    return out


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


def predict_cell(cell: dict, demand: Mapping, pins: Mapping[str, str], hits: Mapping | None, cal: dict,
                 price_core_hr: float) -> dict:
    """Mean and p95 latency, attainment, throughput and cost over the scored steps."""
    slo_ms = SLO_FACTOR * cal["small"]["l0_s"] * 1000
    pods = {"small": cell["pools"][0], "mid": cell["pools"][1]}
    scored = range(WARMUP_STEPS, cs.STEPS)
    branches, served, within, mean_num, hit_n = [], 0.0, 0.0, 0.0, 0.0
    for tier in ("small", "mid"):
        tenants = [t for t, p in pins.items() if p == tier]
        with tier_form(cal[tier], slo_ms) as cfg:
            for step in scored:
                n, h = _pool_counts(demand, tenants, step, hits)
                if n <= 0:
                    continue
                o = step_outcome(cfg, pods[tier], tier, n, h)
                branches += o["branches"]
                within += o["within"]
                mean_num += o["mean_num"]
                served += n
                hit_n += h
    duration_s = len(scored) * BIN_S
    branches = [b for b in branches if b[0] > 0]
    cost = sum(pods[t] * CORES[t] for t in pods) * price_core_hr * duration_s / 3600.0
    return {"cell": cell["cell"], "slo_ms": slo_ms, "requests": served, "scored_s": duration_s,
            "hit_rate": hit_n / served if served else math.nan,
            "mean_latency_s": mean_num / served / 1000 if served else math.nan,
            "p95_latency_s": _mixture_quantile(branches) / 1000 if branches else math.nan,
            "attainment": within / served if served else math.nan,
            "throughput_rps": served / duration_s, "cost": cost}


def steady_hit_rate(knob_mb: float, path: Path = WP6_CACHE) -> float:
    """WP6's steady-state hit rate at the testbed's threshold for a knob, log-
    interpolated in knob MiB (completion bytes of LMSYS answers)."""
    if knob_mb <= 0:
        return 0.0
    pts = [p for p in json.loads(path.read_text(encoding="utf-8"))["curves"]["all"]["costaware"][TESTBED_TAU]
           ["points"] if p["capacity"] > 0]
    return float(np.interp(math.log(knob_mb), np.log([p["knob_mb"] for p in pts]), [p["hit_rate"] for p in pts]))


def measured_capacity(calibration: Path, tier: str, replicas: int) -> float:
    """Closed-loop rate on real prompts measured at `replicas` (its curve's mu_trace_rps)."""
    meta = json.loads((calibration / f"{tier}_r{replicas}" / "load_curve_meta.json").read_text(encoding="utf-8"))
    return float(meta["mu_trace_rps"])


def with_measured_capacity(cal: dict, calibration: Path, pools: list[int]) -> dict:
    """The calibration with each tier's capacity per replica at the cell's own pool size."""
    out = {}
    for tier, n in (("small", pools[0]), ("mid", pools[1])):
        out[tier] = {**cal[tier], "capacity_per_replica_rps": measured_capacity(calibration, tier, n) / n}
    return out


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def predict_all(calibration: Path, inputs: Path, price_core_hr: float) -> dict:
    import cell_hits

    design = json.loads((HERE / "cells.json").read_text(encoding="utf-8"))
    cal = {t: calibrate(calibration / f"{t}_r1") for t in ("small", "mid")}
    meta_path = cs.wp6_meta_path()
    wp6 = pd.read_parquet(meta_path, columns=["doc", "key_bytes"])
    vectors = np.load(str(meta_path).replace(".meta.parquet", ".f32.npy"), mmap_mode="r")
    docs, key_bytes = wp6["doc"].tolist(), wp6["key_bytes"].to_numpy()
    preds = []
    for cell in design["cells"]:
        path = inputs / f"cell{cell['cell']:02d}.csv.gz"
        table = cs.read_table(path)
        demand = demand_from_table(table)
        knob = {t: cs.CACHE_KNOB_MB[cell["cache"]] for t in cs.TENANTS}
        delays = miss_latency_s(cell, demand, cs.PINS, cal)
        replay = cell_hits.replay_cell(table, vectors, docs, key_bytes, knob, lambda t, s: delays[(t, s)])
        hits = cell_hits.hits_per_step(table, replay.hits, cs.STEPS)
        steady = {k: n * steady_hit_rate(knob[k[1]]) for k, n in demand.items()}
        p = predict_cell(cell, demand, cs.PINS, hits, cal, price_core_hr)
        p["cache_replay"] = {"evictions": replay.evictions, "max_used_bytes": replay.max_used_bytes,
                             "max_entries": replay.max_entries, "knob_mb": knob}
        p["steady_cache"] = predict_cell(cell, demand, cs.PINS, steady, cal, price_core_hr)
        p["measured_capacity"] = predict_cell(cell, demand, cs.PINS, hits,
                                              with_measured_capacity(cal, calibration, cell["pools"]), price_core_hr)
        p["input_sha256"] = _sha256(path)
        preds.append(p)
    return {"rules": {"slo_factor": SLO_FACTOR, "warmup_steps": WARMUP_STEPS, "bin_s": BIN_S,
                      "cache_knob_mb": cs.CACHE_KNOB_MB, "pins": cs.PINS, "threshold": float(TESTBED_TAU),
                      "price_core_hr": price_core_hr, "primary": "cache hits from the replay of each cell's prompts",
                      "secondary": "steady_cache: WP6's steady-state hit rate at the cell's knob",
                      "secondary_2": "measured_capacity: each pool's capacity measured at its own replica count"},
            "calibration": cal, "predictions": preds}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--calibration", type=Path, required=True, help="dir with small_r1/ and mid_r1/ load curves")
    ap.add_argument("--inputs", type=Path, default=cs.INPUTS, help="dir with cellNN.csv.gz")
    ap.add_argument("--price-core-hr", type=float, default=0.526 / 4, help="USD per core-hour (T4 box / 4 vCPU)")
    ap.add_argument("--out", type=Path, default=HERE / "predictions.json")
    args = ap.parse_args()
    out = predict_all(args.calibration, args.inputs, args.price_core_hr)
    args.out.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"wrote {args.out}: {len(out['predictions'])} cells; commit it before any cell runs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
