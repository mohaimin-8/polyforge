"""Fit the simulator's plant constants to the B1' live evidence
-> research/calibration/live_plant_b1prime.json (+ LIVE_PLANT_FIT.md).

PREREG_WAVE4_SIM_TRANSFER.md freezes this rule before the calibrated
simulator runs. Every number comes from eval/results/wave4_calibrated_plane_evidence/
(40 runs on one L40S host, 2026-09-15); nothing is tuned to a sim outcome.

The rule, per quantity:

- replica_capacity_wu (one value): the smallest capacity consistent with the
  observed ABSENCE of congestion. Across every valid run, take the highest
  window-mean CRUD work per replica (CRUD requests/s from the in-run
  histogram's replay-route count over the window, times the cell's mean
  work units per CRUD request, over the mean replica count); the live p95
  is flat across the whole observed range (1.01 ms; 2.01 ms in crud_bursty)
  so utilisation there is at most RHO_CAL = 0.05, which bounds capacity from
  below. The bound is what the fit uses -- the plant is at least this big.
- wu_ai_scale = 0: structural, not fitted. On the live plane AI requests go
  k6 -> gateway -> vLLM and never touch a control-plane replica.
- crud_base_scale (per cell): the median window-bucket CRUD p95 of the
  cell's valid runs over the published prediction at zero load
  (crud_base_ms * P95_FACTOR).
- cache (per cell with AI traffic): cacheable_uniform = 1 and cache_hit_max
  = the mean cache-hit rate of the `tier-only` arm's valid runs (its cache
  is pinned at the chart default, so the number is the plant's ceiling for
  that cell's prompt pool, not a planner's choice), cache_half_mb = 4 so the
  lattice's 64 MB level sits at 94% of the ceiling as the live cache does.
- tier_latency_ms (flat, all kinds): small and large from the knob
  preflight's sequential probes (mean over the valid runs' reports); mid is
  NOT measured live and is set to the geometric mean of the two, declared.

    python live_plant_fit.py
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "research" / "jcac_sim"))
sys.path.insert(0, str(ROOT / "eval"))
import model  # noqa: E402
from harness.workloads import WORKLOAD_CLASSES  # noqa: E402

EVIDENCE = ROOT / "eval" / "results" / "wave4_calibrated_plane_evidence" / "runs"
CSV = ROOT / "eval" / "results" / "wave4_calibrated_plane_runs.csv"
OUT_JSON = HERE / "live_plant_b1prime.json"
OUT_MD = HERE / "LIVE_PLANT_FIT.md"

CELLS = ("ai_cacheable", "tier_mixed", "crud_bursty", "joint_stress")
RHO_CAL = 0.05
CACHE_HALF_MB = 4.0
REPLICA_COST_USD_HR = 0.048
REPLICA_SAMPLE_S = 10.0
PRICE_PER_REPLICA_SAMPLE = REPLICA_COST_USD_HR / 3600.0 * REPLICA_SAMPLE_S
WINDOW_EVENT_FRACTION = 0.5


def valid_runs() -> set[str]:
    import csv
    with CSV.open(encoding="utf-8") as f:
        return {f"{r['system']}__{r['workload']}__uniform__small__rep{r['rep']}"
                for r in csv.DictReader(f) if r["status"] == "valid"}


def window(buckets: list[dict]) -> list[dict]:
    floor = WINDOW_EVENT_FRACTION * sorted(int(b["n_events"]) for b in buckets)[len(buckets) // 2]
    idx = [i for i, b in enumerate(buckets) if int(b["n_events"]) >= floor]
    return buckets[idx[0]:idx[-1] + 1] if idx else []


def crud_wu_per_request(cell: str) -> float:
    rps = WORKLOAD_CLASSES[cell].base_rps
    crud = {k: v for k, v in rps.items() if k in model.CRUD_KINDS}
    return sum(v * model.WORK_UNITS[k] for k, v in crud.items()) / sum(crud.values())


def per_run(name: str) -> dict:
    d = EVIDENCE / name
    arm, cell = name.split("__")[0], name.split("__")[1]
    fine = json.loads((d / "eval-export-fine.json").read_text(encoding="utf-8"))["buckets"]
    win = window(fine)
    hist = json.loads((d / "metrics_histogram.json").read_text(encoding="utf-8"))
    replay = next((v for k, v in (hist.get("routes") or {}).items() if "replay" in k), None)
    export = json.loads((d / "eval-export.json").read_text(encoding="utf-8"))
    pre = json.loads((d / "knob_preflight.json").read_text(encoding="utf-8"))
    reps = [b["cost_infra_usd"] / PRICE_PER_REPLICA_SAMPLE for b in win if b["cost_infra_usd"] > 0]
    crud_rps = (replay["count"] / (10.0 * len(win))) if (replay and win) else None
    return {
        "arm": arm, "cell": cell,
        "crud_rps": crud_rps, "mean_replicas": statistics.fmean(reps) if reps else None,
        "crud_p95_median_ms": statistics.median([b["crud_p95_ms"] for b in win if b["crud_p95_ms"] > 0]),
        "cache_hit_rate": float(export["cache_hit_rate"]),
        "tier_small_ms": float(pre["tier"]["mean_ms_a"]) if pre.get("tier", {}).get("tier_a") == "small" else None,
        "tier_large_ms": float(pre["tier"]["mean_ms_b"]) if pre.get("tier", {}).get("tier_b") == "large" else None,
    }


def fit() -> tuple[dict, list[dict]]:
    runs = [per_run(n) for n in sorted(valid_runs()) if (EVIDENCE / n).exists()]
    # capacity: the highest observed CRUD work per replica, bounded by RHO_CAL
    loads = [(r["crud_rps"] * crud_wu_per_request(r["cell"]) / r["mean_replicas"], r)
             for r in runs if r["crud_rps"] and r["mean_replicas"]]
    peak_wu, peak_run = max(loads, key=lambda t: t[0])
    capacity = peak_wu / RHO_CAL
    cells = {}
    for cell in CELLS:
        cr = [r for r in runs if r["cell"] == cell]
        base = WORKLOAD_CLASSES[cell].crud_base_ms * model.P95_FACTOR
        p95 = statistics.median(r["crud_p95_median_ms"] for r in cr)
        entry = {"crud_base_scale": p95 / base, "live_crud_p95_ms": p95, "published_crud_p95_ms_at_zero_load": base}
        tier_only = [r for r in cr if r["arm"] == "tier-only"]
        has_ai = any(k in model.AI_KINDS for k in WORKLOAD_CLASSES[cell].base_rps)
        if has_ai and tier_only:
            entry["cache_hit_max"] = statistics.fmean(r["cache_hit_rate"] for r in tier_only)
            entry["cache_half_mb"] = CACHE_HALF_MB
            entry["cacheable_uniform"] = 1
        cells[cell] = entry
    small = statistics.fmean(r["tier_small_ms"] for r in runs if r["tier_small_ms"])
    large = statistics.fmean(r["tier_large_ms"] for r in runs if r["tier_large_ms"])
    mid = (small * large) ** 0.5
    plant = {
        "source": "eval/results/wave4_calibrated_plane_evidence (B1', 2026-09-15, one L40S host)",
        "rule": "PREREG_WAVE4_SIM_TRANSFER.md; research/calibration/live_plant_fit.py",
        "valid_runs": len(runs),
        "replica_capacity_wu": capacity,
        "replica_capacity_basis": {
            "peak_crud_wu_per_replica_per_s": peak_wu, "rho_cal": RHO_CAL,
            "peak_run": f"{peak_run['arm']}/{peak_run['cell']}",
            "peak_crud_rps": peak_run["crud_rps"], "peak_mean_replicas": peak_run["mean_replicas"],
            "published_replica_capacity_wu": model.REPLICA_CAPACITY_WU},
        "wu_ai_scale": 0.0,
        "tier_latency_ms": {"small": small, "mid": mid, "large": large},
        "tier_latency_basis": "small/large: knob-preflight sequential probes, mean over valid runs; mid: geometric mean, not measured",
        "cells": cells,
    }
    return plant, runs


def sheet(plant: dict) -> str:
    b = plant["replica_capacity_basis"]
    L = ["# The live plant, as fitted from B1′ (for the calibrated simulator)", "",
         f"Generated by `live_plant_fit.py` from {plant['valid_runs']} valid runs under the rule frozen in "
         "`PREREG_WAVE4_SIM_TRANSFER.md`. Written to `live_plant_b1prime.json`, which the four "
         "`wave4_sim_transfer_*.yaml` experiments carry as `model_form` / `economy` overrides.", "",
         "| quantity | published | fitted | basis |", "|---|---|---|---|",
         f"| replica_capacity_wu | {b['published_replica_capacity_wu']:.0f} | {plant['replica_capacity_wu']:.0f} | "
         f"peak {b['peak_crud_wu_per_replica_per_s']:.1f} wu/s per replica ({b['peak_run']}, {b['peak_crud_rps']:.1f} CRUD rps over "
         f"{b['peak_mean_replicas']:.1f} replicas) with flat p95, bounded at ρ ≤ {b['rho_cal']} |",
         "| wu_ai_scale | 1 | 0 | structural: AI requests never reach a control-plane replica on the live plane |",
         f"| tier_latency_ms small / mid / large | per kind (chat 300/800/2000; agent 6000/2500/3500) | "
         f"{plant['tier_latency_ms']['small']:.0f} / {plant['tier_latency_ms']['mid']:.0f} / {plant['tier_latency_ms']['large']:.0f} "
         f"(flat) | {plant['tier_latency_basis']} |"]
    for cell, e in plant["cells"].items():
        cache = (f"cache_hit_max {e['cache_hit_max']:.3f}, half {e['cache_half_mb']:.0f} MB, uniform"
                 if "cache_hit_max" in e else "no AI traffic in the cell: published cache")
        L.append(f"| {cell}: crud_base_scale | 1 | {e['crud_base_scale']:.4f} | live p95 {e['live_crud_p95_ms']:.2f} ms vs "
                 f"{e['published_crud_p95_ms_at_zero_load']:.0f} ms predicted at zero load; {cache} |")
    L += ["", "What the fit cannot say: the capacity is a lower bound (no run reached congestion), `mid`'s latency "
          "is interpolated, and the cache ceiling is the plant's under this harness's prompt pools, not a property "
          "of the cache design.", ""]
    return "\n".join(L) + "\n"


NL = "\n"
EXPERIMENTS = ROOT / "eval" / "experiments"
ARMS = ["jcac-calibrated", "jcac", "replica-only", "cache-only", "tier-only"]


def write_experiments(plant: dict) -> list[Path]:
    """One sim experiment per cell under the fitted plant (the cache ceiling
    is per cell, so the cells cannot share one file), plus one five-arm
    control under the published plant. Same steps and reps as wave4_sim_ref."""
    written = []
    head = ("# Generated by research/calibration/live_plant_fit.py from live_plant_b1prime.json"
            + NL + "# under the rule frozen in PREREG_WAVE4_SIM_TRANSFER.md. Do not edit by hand.")
    for cell, e in plant["cells"].items():
        lines = [head, f"name: wave4_sim_transfer_{cell}", "backend: sim", "steps: 30", "reps: 3",
                 f"systems: [{', '.join(ARMS)}]", f"workloads: [{cell}]", "tenant_mixes: [uniform]",
                 "cluster_sizes: [small]", f"output: eval/results/wave4_sim_transfer_{cell}.duckdb",
                 "retries: 0", "timeseries_reps: 0", "model_form:",
                 f"  replica_capacity_wu: {plant['replica_capacity_wu']:.4g}",
                 "  wu_ai_scale: 0",
                 f"  crud_base_scale: {e['crud_base_scale']:.4g}",
                 f"  tier_latency_small: {plant['tier_latency_ms']['small']:.4g}",
                 f"  tier_latency_mid: {plant['tier_latency_ms']['mid']:.4g}",
                 f"  tier_latency_large: {plant['tier_latency_ms']['large']:.4g}"]
        if "cache_hit_max" in e:
            lines += ["  cacheable_uniform: 1", "economy:",
                      f"  cache_hit_max: {e['cache_hit_max']:.4g}",
                      f"  cache_half_mb: {e['cache_half_mb']:.4g}"]
        out = EXPERIMENTS / f"wave4_sim_transfer_{cell}.yaml"
        out.write_bytes((NL.join(lines) + NL).encode("utf-8"))
        written.append(out)
    control = EXPERIMENTS / "wave4_sim_transfer_control.yaml"
    control.write_bytes((NL.join([
        head, "name: wave4_sim_transfer_control", "backend: sim", "steps: 30", "reps: 3",
        f"systems: [{', '.join(ARMS)}]", f"workloads: [{', '.join(plant['cells'])}]",
        "tenant_mixes: [uniform]", "cluster_sizes: [small]",
        "output: eval/results/wave4_sim_transfer_control.duckdb", "retries: 0", "timeseries_reps: 0",
        "# the published plant: no overrides -- the five-arm control the fitted cells are read against"]) + NL).encode("utf-8"))
    written.append(control)
    return written


def main() -> int:
    plant, _ = fit()
    OUT_JSON.write_bytes((json.dumps(plant, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    OUT_MD.write_bytes(sheet(plant).encode("utf-8"))
    for path in write_experiments(plant):
        print(f"wrote {path.relative_to(ROOT)}")
    print(f"wrote {OUT_JSON.name} and {OUT_MD.name}")
    print(f"replica_capacity_wu {plant['replica_capacity_wu']:.0f}; tiers {plant['tier_latency_ms']}")
    for cell, e in plant["cells"].items():
        print(f"  {cell}: crud_base_scale {e['crud_base_scale']:.4f}"
              + (f", cache_hit_max {e['cache_hit_max']:.3f}" if "cache_hit_max" in e else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
