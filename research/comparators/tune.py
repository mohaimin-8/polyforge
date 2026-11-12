"""WP9 tuning protocol (programme 2). EXPLORATORY until registered.

For every tuned arm of arms.py: draw `--configs` configurations from its
declared space (the declared defaults first, then seeded random draws; 64 by
default, the same budget for every arm), run each on the population's tuning
half, select by mean J (protocol.py: one configuration overall, and one per
workload class), then run the selected configurations on the evaluation half
and report there. Plant (mode A or B, research/plant2/plant.py) and tenant
mix (own = MMPP2, native = real-arrival replay) are arguments. Every arm and
configuration of a workload sees the same seeded demand (shared seeds).

    python tune.py --plant B --dry-run             # runs and configurations only
    python tune.py --plant B --workers 6           # the full protocol (long: see --dry-run)
    python tune.py --plant B --arms joint rules --configs 2 --tuning-workloads 2 \\
        --evaluation-workloads 2 --steps 8 --references --workers 1 --out smoke   # pipeline check

Writes <out>/<tag>/tuned.json (selected parameters and every sampled
configuration), report.md, runs.csv.gz (every tuning and evaluation run) and,
with --references, references.csv.gz (WP1's static best and clairvoyant
bounds per evaluation workload). Exit status 1 if any run failed (its
configuration is excluded from selection and the report lists it).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import multiprocessing
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for _p in (HERE, REPO / "research" / "plant2", REPO / "research" / "theory", REPO / "research" / "jcac_sim",
           REPO / "eval"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pandas as pd  # noqa: E402

import arms  # noqa: E402
import protocol  # noqa: E402
import report  # noqa: E402

DEFAULT_SEED = 20261112
METRIC_KEYS = ("total_cost_usd", "mean_violation", "mean_jain", "mean_quality_loss", "mean_excess",
               "budget_overrun_step_share", "clamp_violation_step_share", "ai_shed_request_share",
               "tier_none_step_share", "steps")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    from harness.workloads import CLUSTER_SIZES

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plant", required=True, choices=("A", "B"), help="plant 2 mode")
    ap.add_argument("--mix", default="own", choices=("own", "native"), help="population tenant mix")
    ap.add_argument("--arms", nargs="+", default=list(arms.TUNED), choices=arms.TUNED)
    ap.add_argument("--configs", type=int, default=64, help="configurations per arm")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED, help="configuration sampling seed")
    ap.add_argument("--no-default", action="store_true",
                    help="all configurations random (default: the declared defaults are configuration 0)")
    ap.add_argument("--steps", type=int, default=120)
    ap.add_argument("--cluster-size", default="large", choices=sorted(CLUSTER_SIZES))
    ap.add_argument("--tuning-workloads", type=int, default=None,
                    help="first N tuning workloads, stratified by class (default: all)")
    ap.add_argument("--evaluation-workloads", type=int, default=None,
                    help="first N evaluation workloads, stratified by class (default: all)")
    ap.add_argument("--granularity", default="both", choices=("overall", "per_class", "both"))
    ap.add_argument("--quality-weight", type=float, default=0.0,
                    help="price of mean answer-quality loss in J (default 0: J as scored so far)")
    ap.add_argument("--budget", default="enforced", choices=arms.BUDGETS,
                    help="enforced: every arm keeps tenants inside their budget; plant: only the plant's gate")
    ap.add_argument("--references", action="store_true",
                    help="also compute WP1's static best and clairvoyant bounds per evaluation workload")
    ap.add_argument("--reference-iterations", type=int, default=120)
    ap.add_argument("--workers", type=int, default=max(1, min(6, (os.cpu_count() or 2) - 2)))
    ap.add_argument("--stall-timeout", type=float, default=3600.0,
                    help="seconds without any finished run before the pool is declared stalled")
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--dry-run", action="store_true", help="print runs and configurations, run nothing")
    args = ap.parse_args(argv)
    if args.steps < 3 or args.configs < 1 or args.workers < 1 or args.quality_weight < 0.0:
        ap.error("need --steps >= 3, --configs >= 1, --workers >= 1 and --quality-weight >= 0")
    return args


def plant_bundle(mode: str) -> tuple[dict, dict]:
    import plant

    return plant.mode_a() if mode == "A" else plant.mode_b()


def build_runs(args: argparse.Namespace, workloads: list[str]) -> dict:
    """One harness RunSpec per workload. The experiment name holds the plant
    and mix only, so every arm, configuration and budget mode of a workload
    draws the same demand."""
    from harness import config as hconfig

    form, econ = plant_bundle(args.plant)
    spec = hconfig.ExperimentSpec(
        name=f"wp9_{args.plant.lower()}_{args.mix}", steps=args.steps, reps=1, systems=["wp9"],
        workloads=workloads, tenant_mixes=[args.mix], cluster_sizes=[args.cluster_size],
        shared_seeds=True, timeseries_reps=0, model_form=form, economy=econ)
    return {r.workload: r for r in hconfig.expand(spec)}


def run_job(job: dict) -> dict:
    """One tuning or evaluation run, or one reference world. Failures are
    recorded in the row (and printed by the parent), never dropped."""
    t0 = time.time()
    row = {k: job[k] for k in ("phase", "arm", "config", "workload", "class", "granularities", "per_class_fallback")
           if k in job}
    try:
        if job["phase"] == "reference":
            row.update(_reference(job))
        else:
            result = arms.execute(job["arm"], job["params"], job["run"], job["budget"])
            m = result["metrics"]
            row.update(protocol.objective(m, result["tenants"], job["quality_weight"]))
            row.update({k: m[k] for k in METRIC_KEYS})
        row["error"] = ""
    except Exception as exc:  # recorded per run; the report and exit status carry it
        row.update(J=math.nan, error=f"{type(exc).__name__}: {exc}")
    row["wall_s"] = time.time() - t0
    return row


def _reference(job: dict) -> dict:
    from clairvoyant import bounds, world_for_run

    b = bounds(world_for_run(job["run"]), 0.0, iterations=job["iterations"])
    return {"static": b.static, "lb0": b.lb0, "lb1": b.lb1, "lb2": b.lb2, "ub": b.ub}


def execute(jobs: list[dict], workers: int, label: str, stall_s: float = 3600.0) -> list[dict]:
    """Run `jobs` in order (one worker) or on a pool. A pool that finishes no
    run for `stall_s` seconds raises: a worker killed outside Python (out of
    memory) loses its task and would otherwise leave the campaign waiting."""
    if not jobs:
        return []
    t0, rows, step = time.time(), [], max(1, len(jobs) // 20)

    def note(row: dict) -> None:
        rows.append(row)
        if row.get("error"):
            print(f"  FAILED {row['phase']} {row.get('arm', '')} {row.get('config', '')} "
                  f"{row['workload']}: {row['error']}", file=sys.stderr, flush=True)
        if len(rows) % step == 0 or len(rows) == len(jobs):
            print(f"  {label}: {len(rows)}/{len(jobs)} in {time.time() - t0:.0f}s", flush=True)

    if workers <= 1 or len(jobs) == 1:
        for job in jobs:
            note(run_job(job))
    else:
        with Pool(min(workers, len(jobs))) as pool:
            results = pool.imap_unordered(run_job, jobs, chunksize=1)
            for _ in jobs:
                try:
                    note(results.next(timeout=stall_s))
                except multiprocessing.TimeoutError as exc:
                    raise RuntimeError(f"{label}: no run finished in {stall_s:.0f}s after {len(rows)} of "
                                       f"{len(jobs)}; a worker may have died") from exc
    return rows


def granularities(args: argparse.Namespace) -> tuple:
    return protocol.GRANULARITIES if args.granularity == "both" else (args.granularity,)


def dry_run(args: argparse.Namespace, configs: dict, tuning: list[str], evaluation: list[str],
            classes: dict) -> None:
    def counts(names: list[str]) -> str:
        return ", ".join(f"{c} {sum(classes[w] == c for w in names)}" for c in protocol.CLASSES)

    per_eval = len(granularities(args))
    print(f"WP9 tuning protocol: plant {args.plant}, mix {args.mix}, {args.cluster_size} cluster, "
          f"{args.steps} steps, budget {args.budget}, quality weight {args.quality_weight:g}")
    print(f"tuning workloads {len(tuning)} ({counts(tuning)}); evaluation workloads {len(evaluation)} "
          f"({counts(evaluation)}); granularity {args.granularity}")
    print(f"{'arm':<10} {'configs':>7} {'params':>6} {'tuning runs':>12} {'evaluation runs (max)':>22}")
    total_t = total_e = 0
    for name in args.arms:
        n_t, n_e = len(configs[name]) * len(tuning), per_eval * len(evaluation)
        total_t, total_e = total_t + n_t, total_e + n_e
        print(f"{name:<10} {len(configs[name]):>7} {len(arms.space(name, args.quality_weight)):>6} "
              f"{n_t:>12} {n_e:>22}")
    refs = len(evaluation) if args.references and args.quality_weight == 0.0 else 0
    print(f"total: {total_t} tuning runs + at most {total_e} evaluation runs + {refs} reference worlds")


def meta_of(args: argparse.Namespace, tuning: list[str], evaluation: list[str], classes: dict) -> dict:
    return {
        "plant": args.plant, "mix": args.mix, "cluster_size": args.cluster_size, "steps": args.steps,
        "arms": list(args.arms), "configs": args.configs, "include_default": not args.no_default,
        "seed": args.seed, "budget": args.budget, "quality_weight": args.quality_weight,
        "granularity": args.granularity, "granularities": list(granularities(args)),
        "references_requested": args.references, "reference_iterations": args.reference_iterations,
        "tuning_n": len(tuning), "evaluation_n": len(evaluation),
        "tuning_workloads": list(tuning), "evaluation_workloads": list(evaluation),
        "tuning_total": len(protocol.split_names("tuning")),
        "evaluation_total": len(protocol.split_names("evaluation")),
        "class_counts": {half: {c: sum(classes[w] == c for w in names) for c in protocol.CLASSES}
                         for half, names in (("tuning", tuning), ("evaluation", evaluation))},
        "generated": datetime.date.today().isoformat(),
    }


def run_tag(meta: dict) -> str:
    """Directory name: the main settings, then a digest of every setting, so
    runs that differ in any setting never overwrite each other."""
    tag = f"plant{meta['plant']}_{meta['mix']}_{meta['cluster_size']}_s{meta['steps']}_{meta['budget']}"
    if meta["quality_weight"]:
        tag += f"_q{meta['quality_weight']:g}"
    settings = {k: v for k, v in meta.items() if k != "generated"}
    digest = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()[:8]
    return f"{tag}_{digest}"


def arms_meta(args: argparse.Namespace) -> dict:
    out = {}
    for name in args.arms:
        a = arms.arm(name)
        out[name] = {"maps_to": a.maps_to, "description": a.description, "fixed": dict(a.fixed),
                     "space": [{"name": p.name, "range": p.describe(), "default": p.default, "doc": p.doc}
                               for p in arms.space(name, args.quality_weight)]}
    if args.references:
        for name in arms.REFERENCES:
            a = arms.arm(name)
            out[name] = {"maps_to": a.maps_to, "description": a.description, "fixed": {}, "space": []}
    return out


def write_outputs(out_dir: Path, meta: dict, arm_info: dict, configs: dict, selection: dict,
                  classes: dict, tuning: list[str], evaluation: list[str], rows: list[dict],
                  references: list[dict]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    def pick(p) -> dict | None:
        return None if p is None else {"config": p.config, "tuning_J": p.mean_J, "n": p.n}

    tuned = {"protocol": "WP9 random search (research/comparators/tune.py)", "settings": meta,
             "class_rule": protocol.CLASS_RULE,
             "classes": {half: {c: [w for w in names if classes[w] == c] for c in protocol.CLASSES}
                         for half, names in (("tuning", tuning), ("evaluation", evaluation))},
             "arms": {}}
    for name, picks in selection.items():
        entry = {**arm_info[name], "configs": configs[name], "overall": pick(picks["overall"]),
                 "per_class": {c: pick(p) for c, p in picks["per_class"].items()}}
        for scope in [entry["overall"], *entry["per_class"].values()]:
            if scope is not None:
                scope["params"] = configs[name][scope["config"]]
        tuned["arms"][name] = entry
    (out_dir / "tuned.json").write_text(json.dumps(tuned, indent=1, default=str), encoding="utf-8")
    frame = pd.DataFrame([{**r, "granularities": "+".join(r.get("granularities", []))} for r in rows])
    frame.to_csv(out_dir / "runs.csv.gz", index=False)
    if references:
        pd.DataFrame(references).to_csv(out_dir / "references.csv.gz", index=False)
    (out_dir / "report.md").write_text(report.render(meta, arm_info, configs, selection, rows, references),
                                       encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    tuning = protocol.pick_workloads("tuning", args.tuning_workloads)
    evaluation = protocol.pick_workloads("evaluation", args.evaluation_workloads)
    classes = {w: protocol.workload_class(w) for w in tuning + evaluation}
    configs = {name: arms.sample_configs(name, args.configs, args.seed, args.quality_weight, not args.no_default)
               for name in args.arms}
    if args.dry_run:
        dry_run(args, configs, tuning, evaluation, classes)
        return 0

    t0 = time.time()
    runs_t = build_runs(args, tuning)
    base = {"budget": args.budget, "quality_weight": args.quality_weight}
    jobs = [{**base, "phase": "tuning", "arm": name, "config": i, "params": cfg, "workload": w,
             "class": classes[w], "run": runs_t[w]}
            for name in args.arms for i, cfg in enumerate(configs[name]) for w in tuning]
    print(f"tuning: {len(jobs)} runs on {args.workers} workers", flush=True)
    tuning_rows = execute(jobs, args.workers, "tuning", args.stall_timeout)
    selection = protocol.select(tuning_rows, {w: classes[w] for w in tuning})

    runs_e = build_runs(args, evaluation)
    plan = protocol.evaluation_plan(selection, evaluation, classes, granularities(args))
    jobs = [{**base, **p, "phase": "evaluation", "params": configs[p["arm"]][p["config"]],
             "class": classes[p["workload"]], "run": runs_e[p["workload"]]} for p in plan]
    if args.references and args.quality_weight == 0.0:
        jobs += [{"phase": "reference", "workload": w, "class": classes[w], "run": runs_e[w],
                  "iterations": args.reference_iterations} for w in evaluation]
    print(f"evaluation: {len(jobs)} runs on {args.workers} workers", flush=True)
    done = execute(jobs, args.workers, "evaluation", args.stall_timeout)
    rows = tuning_rows + [r for r in done if r["phase"] == "evaluation"]
    references = [r for r in done if r["phase"] == "reference"]

    meta = meta_of(args, tuning, evaluation, classes)
    out_dir = Path(args.out) / run_tag(meta)
    write_outputs(out_dir, meta, arms_meta(args), configs, selection, classes, tuning, evaluation, rows,
                  references)
    failed = sum(1 for r in rows + references if r.get("error"))
    print(f"done in {time.time() - t0:.0f}s; {failed} failed runs; wrote {out_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
