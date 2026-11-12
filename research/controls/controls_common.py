"""Shared set-up for the WP22 controls (programme 2): import paths, the
scored objective J, in-process arm registration and paired runs of the joint
and per-knob (decoupled) planners.

Nothing here edits the harness registry on disk or any published controller:
arms are registered in-process for the duration of a call and removed after
it (`registered_arms`), and the planner classes are subclasses (rule R4).
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PATHS = (HERE, REPO / "research" / "theory", REPO / "research" / "control2",
         REPO / "research" / "jcac_sim", REPO / "eval", REPO / "research" / "plant2")
for _p in PATHS:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from controller_index import IndexedController, execute_indexed  # noqa: E402
from harness import config as hconfig  # noqa: E402
from width import CACHE_RADIUS_FOR_WIDTH, WidthController  # noqa: E402

DATA = HERE / "data"
EPS = 1e-9
# Every arm here plans like `jcac_converged`: anchored moves, sweeps to a
# fixed point. Joint and per-knob arms differ only in `knob_search`.
BASE_PARAMS = {"anchor_moves": True, "converge_sweeps": True}
JOINT, DECOUPLED = "x_wp22_joint", "x_wp22_decoupled"
# Outcome metrics of a run. The guard/diagnostic fields
# (coordination_disagreement_share, plan_*_share, capped_cycle_share) are
# left out on purpose: they describe the planner's bookkeeping, and a tracked
# joint arm reports 0.0 where an untracked arm reports None.
OUTCOME_METRICS = ("total_cost_usd", "mean_violation", "violation_step_share", "mean_jain",
                   "cache_hit_rate", "crud_p95_ms", "ai_p95_ms", "mean_excess",
                   "tier_none_step_share", "budget_overrun_step_share",
                   "clamp_violation_step_share", "mean_quality_loss",
                   "ai_shed_request_share", "shed_request_share", "cost_per_served_request", "steps")


class IndexedWidthController(IndexedController, WidthController):
    """The width-settable planner (research/control2/width.py) logging the
    index of its decisions (research/theory/controller_index.py)."""


def objective_j(metrics: dict, n_tenants: int) -> float:
    """The published composite J per tenant-step of THIS run: cost per
    tenant-step in units of 0.01 USD, plus 2 x mean violation, plus
    0.5 x (1 - mean Jain). The pilots divide cost by 119 x 8 for 120-step,
    8-tenant runs; here the divisor is the run's own steps x tenants, so
    runs of any length are comparable. A paired gain is unaffected except
    for that scale."""
    tenant_steps = max(1, int(metrics["steps"]) * int(n_tenants))
    return (metrics["total_cost_usd"] / tenant_steps / 0.01
            + 2.0 * metrics["mean_violation"] + 0.5 * (1.0 - metrics["mean_jain"]))


def width_params(width: int | None) -> dict:
    """Controller parameters for a replica move of +/- `width` per interval
    with the dose-response design's cache radius; None = the published
    lattice (+/-2 replicas, one cache level)."""
    if width is None:
        return {}
    if width not in CACHE_RADIUS_FOR_WIDTH:
        raise ValueError(f"width must be one of {sorted(CACHE_RADIUS_FOR_WIDTH)}, not {width!r}")
    return {"replica_radius": width, "cache_radius": CACHE_RADIUS_FOR_WIDTH[width]}


def arm_pair(extra_params: dict | None = None, gamma: float | None = None,
             tier_min: str | None = None, track: bool = True) -> dict:
    """SystemSpecs of the joint and the decoupled arm, identical except for
    the knob search (and the joint arm's reporting-only disagreement
    counter, `track_coordination`)."""
    from harness.systems import SystemSpec

    extra = dict(extra_params or {})
    joint = {**BASE_PARAMS, **extra, "track_coordination": track}
    dec = {**BASE_PARAMS, **extra, "knob_search": "decoupled", "log_index": False}
    return {JOINT: SystemSpec("jcac", params=joint, gamma=gamma, tier_min=tier_min),
            DECOUPLED: SystemSpec("jcac", params=dec, gamma=gamma, tier_min=tier_min)}


@contextlib.contextmanager
def registered_arms(arms: dict):
    """Register `arms` in the harness registry for the duration of the
    block, then restore whatever the names held before (or remove them).
    A pooled worker therefore never carries one job's arms into the next."""
    from harness.systems import SYSTEMS

    saved = {name: SYSTEMS.get(name) for name in arms}
    SYSTEMS.update(arms)
    try:
        yield
    finally:
        for name, old in saved.items():
            if old is None:
                SYSTEMS.pop(name, None)
            else:
                SYSTEMS[name] = old


def design_seed(salt: str, workload: str, mix: str, size: str, rep: int) -> int:
    """A demand seed per design point, shared by both arms of a pair."""
    digest = hashlib.sha256(f"{salt}|{workload}|{mix}|{size}|{rep}".encode()).hexdigest()
    return int(digest[:12], 16) % (2**31 - 1)


def run_specs(name: str, workload: str, mix: str, size: str, steps: int, seed: int,
              economy: dict | None = None, model_form: dict | None = None) -> dict:
    """RunSpecs of the joint and the decoupled arm for one design point,
    both on demand seed `seed` (shared seeds: identical demand)."""
    spec = hconfig.ExperimentSpec(
        name=name, steps=steps, reps=1, systems=[JOINT, DECOUPLED], workloads=[workload],
        tenant_mixes=[mix], cluster_sizes=[size], shared_seeds=True, timeseries_reps=0,
        economy=dict(economy or {}), model_form=dict(model_form or {}))
    return {r.system: dataclasses.replace(r, seed=seed) for r in hconfig.expand(spec)}


def summarise_rows(rows: list[dict], steps: int) -> dict:
    """Per-run summary of the index rows of scored decisions (the last plan
    of a run is never scored, so rows with cycle > steps are dropped)."""
    rows = [r for r in rows if r["cycle"] <= steps]
    applies = [r for r in rows if r["theorem1_applies"]]
    idx = [r["index"] for r in rows if r["index"] is not None]
    qidx = [r["quantile_index"] for r in rows if r["quantile_index"] is not None]
    violations = sum(1 for r in applies
                     if r["loss"] < -EPS or r["loss"] > r["bound"] + EPS or r["bound"] > r["index"] + EPS)

    def mean(xs):
        return float(sum(xs) / len(xs)) if xs else math.nan

    return {
        "decisions": len(rows),
        "index_defined": len(idx),
        "theorem_applies": len(applies),
        "theorem_violations": violations,
        "index_mean": mean(idx),
        "index_max": max(idx) if idx else math.nan,
        "quantile_index_mean": mean(qidx),
        "loss_mean": mean([r["loss"] for r in applies]),
        "loss_max": max((r["loss"] for r in applies), default=math.nan),
        "disagree_share": mean([float(r["disagree"]) for r in rows]),
        "costly_share": mean([float(r["disagree_with_loss"]) for r in rows]),
        "per_knob_infeasible_share": mean([float(not r["per_knob_feasible"]) for r in rows]),
        "anchor_infeasible_share": mean([float(not r["anchor_feasible"]) for r in rows]),
    }


def execute_pair(runs: dict, arms: dict, joint_class: type = IndexedWidthController) -> dict:
    """Run both arms of one design point with the arms registered for the
    call only. Returns the two results and the joint arm's index rows."""
    with registered_arms(arms):
        joint, rows = execute_indexed(runs[JOINT], joint_class)
        dec, _ = execute_indexed(runs[DECOUPLED], joint_class)
    return {"joint": joint, "decoupled": dec, "rows": rows}
