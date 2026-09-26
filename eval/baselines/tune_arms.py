"""Per-size tuning of the FAIR_J comparator ARMS (audit 2026-09-26, Phase 3).

tune.py tunes the bare controllers on the medium cluster inside bounded
grids; TUNING_EXTENDED.md showed those optima were grid edges and that the
best setting depends on the cluster size. The confirmatory comparators are
arms, not bare controllers: hpa_fair and keda_fair add a 512 MB cache without
the LRU cost penalty, and jcac_nojoint_v2_tuned is the layered stack whose
replica layer carries HPA's target. This sweep tunes each arm AS THE HARNESS
RUNS IT (sim_backend.execute's controller, starting cache, miss-cost factor
and params) on tune.py's slice, seeds, jitter and objective, at each cluster
size, and writes:

- grids_arms/<arm>_<size>.csv   every value's J and its three terms
- tuned_arms.yaml               per arm, per size: the best-of-grid values

Arms declaring `tuned_arm` read tuned_arms.yaml; nothing else does, so every
published arm is untouched.

Run from eval/:  python baselines/tune_arms.py
"""

from __future__ import annotations

import csv
import hashlib
import sys
from pathlib import Path

import yaml

EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import tune  # noqa: E402  (the published slice, seeds and objective)
from harness.sim_backend import _JITTER_SALT  # noqa: E402
from harness.systems import SYSTEMS, base_params, lru_miss_cost_factor  # noqa: E402

OUT = Path(__file__).resolve().parent
RHO = [0.01, 0.02, 0.03, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.4, 0.6, 0.8]
RPS = [0.02, 0.05, 0.075, 0.1, 0.15, 0.25, 0.5, 1.0, 2.0, 4.0]
ARMS: dict[str, tuple[str, list]] = {
    "hpa_fair": ("target_rho", RHO),
    "keda_fair": ("rps_per_replica", RPS),
    "jcac_nojoint_v2_tuned": ("target_rho", RHO),
}
SIZES = ("small", "medium", "large")


def score_controller(controller: str, params: dict, cluster: str, *,
                     miss_cost_factor: float = 1.0, initial_cache_mb: int | None = None) -> dict:
    """One setting on the tuning slice: J and its three terms."""
    if _JITTER_SALT != 0x5F3759DF:
        raise SystemExit("sim_backend's jitter salt no longer matches tune.py's")
    terms = {"cost_norm": 0.0, "violation": 0.0, "unfair": 0.0}
    n = len(tune.TUNING_CELLS) * tune.TUNING_REPS
    for wl, mix in tune.TUNING_CELLS:
        for rep in range(tune.TUNING_REPS):
            digest = hashlib.sha256(f"{wl}|{mix}|{rep}".encode()).hexdigest()
            seed = tune.TUNING_SEED + int(digest[:8], 16) % 10_000
            tenant_ids, buckets, configs, limits = tune.workloads.build(
                wl, mix, cluster, seed, tune.TUNING_STEPS)
            result = tune.simulate.run(
                controller, tenant_ids, buckets, configs=configs, limits=limits,
                collect_rows=False, controller_params=dict(params),
                jitter_seed=seed ^ _JITTER_SALT, miss_cost_factor=miss_cost_factor,
                initial_cache_mb=initial_cache_mb)
            tenants = len(tenant_ids)
            terms["cost_norm"] += (result.total_cost_usd / max(1, result.steps * tenants)
                                   / tune.COST_SCALE_USD) / n
            terms["violation"] += result.mean_violation / n
            terms["unfair"] += (1.0 - result.mean_jain) / n
    j = tune.ALPHA * terms["cost_norm"] + tune.BETA * terms["violation"] + tune.GAMMA * terms["unfair"]
    return {"mean_objective": round(j, 5), **{k: round(v, 5) for k, v in terms.items()}}


def score_arm(arm: str, overrides: dict, cluster: str) -> dict:
    """The arm as sim_backend.execute runs it, with `overrides` on its params.
    Refuses arms whose execution adds anything this replay omits."""
    spec = SYSTEMS[arm]
    if spec.knob_freeze or spec.blind_classifier or spec.gamma is not None or spec.seeded:
        raise SystemExit(f"{arm}: execute() does more for this arm than tune_arms replays")
    params = {**base_params(spec), **overrides}
    for engine_only in ("chaos_planner_outage", "chaos_replica_kill", "evict_overhead_us", "isocost"):
        if engine_only in params:
            raise SystemExit(f"{arm}: {engine_only} is not replayed here")
    return score_controller(
        spec.controller, params, cluster,
        miss_cost_factor=lru_miss_cost_factor() if spec.lru_eviction else 1.0,
        initial_cache_mb=spec.static_cache_mb)


def write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    check = score_controller("hpa", {"target_rho": 0.3}, "medium")["mean_objective"]
    if check != round(tune.score_combo("hpa", {"target_rho": 0.3}), 5):
        raise SystemExit(f"score_controller drifted from tune.score_combo ({check})")
    tuned: dict[str, dict[str, dict]] = {}
    for arm, (knob, values) in ARMS.items():
        tuned[arm] = {}
        for size in SIZES:
            rows = []
            for value in values:
                rows.append({knob: value, **score_arm(arm, {knob: value}, size)})
                print(f"  {arm}@{size} {rows[-1]}", flush=True)
            write(OUT / "grids_arms" / f"{arm}_{size}.csv", rows)
            best = min(rows, key=lambda r: r["mean_objective"])
            if best[knob] in (values[0], values[-1]):
                raise SystemExit(f"{arm}@{size}: optimum {knob}={best[knob]} is a grid edge; widen the grid")
            tuned[arm][size] = {knob: best[knob]}
    header = ("# Per-size best-of-grid parameters for the FAIR_J comparator arms.\n"
              "# Generated by tune_arms.py (see its docstring); read only by arms that\n"
              "# declare `tuned_arm`. Regenerate with: python baselines/tune_arms.py\n")
    (OUT / "tuned_arms.yaml").write_text(header + yaml.safe_dump(tuned, sort_keys=True), encoding="utf-8")
    print(f"wrote {OUT / 'tuned_arms.yaml'}")


if __name__ == "__main__":
    main()
