"""WP14: the confirmatory simulation campaign, every seed drawn from the registered round.

confirm.py runs this once per shard from the frozen commit C (campaign.json names it):

    python wp14_campaign.py --master HEX --shard I --shards N --out DIR
    python wp14_campaign.py --rehearsal --out DIR    # pipeline check: two of everything,
                                                     # a made-up master seed, the draft design allowed
    python wp14_campaign.py --check                  # commit C's design: not draft, every job buildable

design.json, frozen with the protocol, fixes what runs; a design still marked
"draft" runs only as a rehearsal. Two parts:

  dose  (H1, H2) new regimes of WP4's dose-response design. Every replica width
        of the pilot is crossed with `kappa_strata` equal strata of
        [kappa_min, kappa_max], and one kappa is drawn uniformly inside each
        stratum, so a regime repeats the pilot's grid with probability zero. A
        regime runs the joint and the decoupled planner on WP4's design points
        (`workloads` x `mixes`); a design point's demand seed is shared by every
        regime and arm, as in the pilot.
  comp  (H3, H4) `workloads` fresh workloads drawn from the registered
        population process (research/population/population.py's sampler on the
        same real windows, its generator seeded from the master), named
        pop1000, pop1001, ... so that no name is a registered one. Each plant
        runs every WP9 arm with the configuration WP9 selected (its frozen
        tuned.json) at each granularity; every arm of a workload sees the same
        demand.

A job's seed is seeds.run_seed(master, identity) modulo 2^31 - 1, the harness's
seed range. Shard i of n runs jobs i, i + n, ... of the full list, whose order
is fixed, and writes rows.csv (one row per job, in list order) and
population.json (the drawn workloads). A run that raises is recorded with its
error and J = NaN and is not retried. Nothing here tests anything:
wp14_score.py does.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import os
import re
import sys
import time
from multiprocessing import Pool
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for _p in (REPO / "eval", REPO / "research" / "jcac_sim", REPO / "research" / "plant2",
           REPO / "research" / "control2", REPO / "research" / "theory", REPO / "research" / "comparators",
           REPO / "research" / "population", HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import seeds  # noqa: E402

DESIGN = HERE / "design.json"
WINDOWS = REPO / "research" / "population" / "data" / "windows_600s.csv.gz"
SEED_RANGE = 2**31 - 1
FIRST_WORKLOAD = 1000
SEARCHES = ("joint", "decoupled")
REHEARSAL_MASTER = hashlib.sha256(b"WP14 rehearsal: not a registered draw").hexdigest()
REHEARSAL_SIZE = 2          # regimes, design points and workloads per plant in a rehearsal
HEX64 = re.compile(r"[0-9a-f]{64}")


def seed_of(master: str, identity: str) -> int:
    return seeds.run_seed(master, identity) % SEED_RANGE


def load_design(path: Path = DESIGN, rehearsal: bool = False) -> dict:
    design = json.loads(path.read_text(encoding="utf-8"))
    if design.get("draft") and not rehearsal:
        raise SystemExit(f"{path.name} is still marked draft: the protocol fixes it, and commit C freezes it, "
                         "before any confirmatory run")
    return design


# --------------------------------------------------------------------------
# The job list (pure: no run happens here)
# --------------------------------------------------------------------------

def regimes(master: str, dose: dict) -> list[dict]:
    """Every width crossed with the kappa strata, one kappa drawn uniformly in each stratum."""
    lo, hi, strata = float(dose["kappa_min"]), float(dose["kappa_max"]), int(dose["kappa_strata"])
    out = []
    for w in dose["widths"]:
        for q in range(strata):
            u = seeds.run_seed(master, f"dose|kappa|w{w}|q{q}") / 2**64
            out.append({"regime": f"w{w}q{q}", "width": int(w), "kappa": lo + (hi - lo) * (q + u) / strata})
    return out


def dose_jobs(master: str, dose: dict, rehearsal: bool = False) -> list[dict]:
    import dose_response_pilot as wp4

    unknown = sorted(set(dose["widths"]) - set(wp4.WIDTHS))
    if unknown:
        raise SystemExit(f"widths {unknown} have no planner arm (WP4 registers {list(wp4.WIDTHS)})")
    regs = regimes(master, dose)
    points = [(w, m) for w in dose["workloads"] for m in dose["mixes"]]
    if rehearsal:   # the narrowest and the widest regime, so the timing covers both ends
        regs, points = [regs[0], regs[-1]], points[:REHEARSAL_SIZE]
    return [{"part": "dose", "identity": f"dose|{r['regime']}|{wl}|{mix}|{search}", **r, "search": search,
             "workload": wl, "tenant_mix": mix, "cluster_size": dose["cluster_size"], "steps": int(dose["steps"]),
             "seed": seed_of(master, f"dose|demand|{wl}|{mix}")}
            for r in regs for wl, mix in points for search in SEARCHES]


def draw_population(master: str, n: int) -> list[dict]:
    """The first `n` workloads of the population process, its generator seeded from the master."""
    import numpy as np
    import pandas as pd
    import population

    if n < 1:
        raise SystemExit(f"the design asks for {n} workloads")
    rng = np.random.default_rng(seeds.run_seed(master, "comp|population"))
    drawn = population.sample(pd.read_csv(WINDOWS), rng, n)
    return [{"workload": f"pop{FIRST_WORKLOAD + i}", "tenants": w["tenants"]} for i, w in enumerate(drawn)]


def picks(tuned: dict) -> dict:
    """WP9's selection from its tuned.json, as protocol.evaluation_plan takes it."""
    import protocol

    def pick(p: dict | None):
        return None if p is None else protocol.Pick(p["config"], p["tuning_J"], p["n"])

    out = {}
    for name, arm in tuned["arms"].items():
        for p in (arm["overall"], *arm["per_class"].values()):
            if p is not None and arm["configs"][p["config"]] != p["params"]:
                raise SystemExit(f"{name}: tuned.json's pick {p['config']} is not the configuration it sampled")
        out[name] = {"overall": pick(arm["overall"]),
                     "per_class": {c: pick(p) for c, p in arm["per_class"].items() if p is not None}}
    return out


def check_settings(plant: str, settings: dict, comp: dict, rel: str) -> None:
    want = {"plant": plant, "mix": comp["mix"], "budget": comp["budget"], "steps": int(comp["steps"]),
            "cluster_size": comp["cluster_size"]}
    got = {k: settings.get(k) for k in want}
    if got != want:
        raise SystemExit(f"{rel} was tuned for {got}, the design asks for {want}")


def comp_jobs(master: str, comp: dict, workloads: list[dict], rehearsal: bool = False) -> list[dict]:
    import protocol

    names = [w["workload"] for w in workloads][: REHEARSAL_SIZE if rehearsal else None]
    classes = {w["workload"]: protocol.class_of_spec(w) for w in workloads}
    jobs = []
    for plant, rel in sorted(comp["plants"].items()):
        tuned = json.loads((REPO / rel).read_text(encoding="utf-8"))
        check_settings(plant, tuned["settings"], comp, rel)
        for p in protocol.evaluation_plan(picks(tuned), names, classes, tuple(comp["granularities"])):
            jobs.append({"part": "comp", "identity": f"comp|{plant}|{p['arm']}|c{p['config']}|{p['workload']}",
                         "plant": plant, **p, "class": classes[p["workload"]],
                         "params": tuned["arms"][p["arm"]]["configs"][p["config"]], "budget": comp["budget"],
                         "quality_weight": float(tuned["settings"]["quality_weight"]), "mix": comp["mix"],
                         "cluster_size": comp["cluster_size"], "steps": int(comp["steps"]),
                         "seed": seed_of(master, f"comp|demand|{p['workload']}")})
    return jobs


def plan(master: str, design: dict, rehearsal: bool = False) -> tuple[list[dict], list[dict]]:
    """(jobs, workloads): every job of the campaign in its fixed order, and the drawn workloads
    (a rehearsal draws only the first few, the same as the first few of the full draw)."""
    workloads = draw_population(master, REHEARSAL_SIZE if rehearsal else int(design["comp"]["workloads"]))
    jobs = dose_jobs(master, design["dose"], rehearsal) + comp_jobs(master, design["comp"], workloads, rehearsal)
    ids = [j["identity"] for j in jobs]
    if len(set(ids)) != len(ids):
        raise SystemExit("two jobs share an identity: their seeds would coincide")
    return jobs, workloads


# --------------------------------------------------------------------------
# Running one job (in a pool worker)
# --------------------------------------------------------------------------

def use_population(path: str) -> None:
    """Point the harness's population workloads at the drawn workloads (pool initializer)."""
    from harness import population_workloads

    population_workloads.POPULATION = Path(path)
    population_workloads._CACHE.pop("data", None)


def _run_dose(job: dict) -> dict:
    import dose_response_pilot as wp4
    from harness import config as hconfig

    spec = hconfig.ExperimentSpec(
        name="wp14_dose", steps=job["steps"], reps=1, systems=[wp4.arm(job["width"], job["search"])],
        workloads=[job["workload"]], tenant_mixes=[job["tenant_mix"]], cluster_sizes=[job["cluster_size"]],
        shared_seeds=True, timeseries_reps=0, model_form={"wu_ai_scale": job["kappa"]})
    (run,) = hconfig.expand(spec)
    try:
        row = wp4.run_one((job["kappa"], dataclasses.replace(run, seed=job["seed"])))
    except Exception as exc:  # recorded in the row, never retried
        return {"J": math.nan, "error": f"{type(exc).__name__}: {exc}"}
    return {**row, "error": ""}


def _run_comp(job: dict) -> dict:
    import tune

    args = argparse.Namespace(plant=job["plant"], mix=job["mix"], steps=job["steps"],
                              cluster_size=job["cluster_size"])
    run = tune.build_runs(args, [job["workload"]])[job["workload"]]
    keys = ("arm", "config", "workload", "class", "granularities", "per_class_fallback", "params", "budget",
            "quality_weight")
    return tune.run_job({"phase": "evaluation", **{k: job[k] for k in keys},
                         "run": dataclasses.replace(run, seed=job["seed"])})


def run_job(job: dict) -> dict:
    t0 = time.time()
    try:
        out = _run_dose(job) if job["part"] == "dose" else _run_comp(job)
    except Exception as exc:  # a run that cannot even be built is recorded the same way
        out = {"J": math.nan, "error": f"{type(exc).__name__}: {exc}"}
    row = {**job, **out, "identity": job["identity"], "seed": job["seed"]}
    if "granularities" in row:
        row["granularities"] = "+".join(row["granularities"])
    if "params" in row:
        row["params"] = json.dumps(row["params"], sort_keys=True)
    row.setdefault("wall_s", time.time() - t0)
    return row


def execute(jobs: list[dict], population_file: Path, workers: int) -> list[dict]:
    if workers <= 1:
        use_population(str(population_file))
        return [run_job(j) for j in jobs]
    with Pool(workers, initializer=use_population, initargs=(str(population_file),)) as pool:
        return pool.map(run_job, jobs, chunksize=1)


# --------------------------------------------------------------------------
# Outputs and command line
# --------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_population(path: Path, workloads: list[dict]) -> None:
    meta = {"drawn_by": "research/registration/wp14_campaign.py draw_population",
            "sampler": "research/population/population.py sample()", "windows": WINDOWS.relative_to(REPO).as_posix(),
            "windows_sha256": sha256_file(WINDOWS), "seed_identity": "comp|population", "workloads": workloads}
    path.write_text(json.dumps(meta, indent=1), encoding="utf-8")


def write_rows(path: Path, rows: list[dict]) -> None:
    import pandas as pd

    pd.DataFrame(rows).to_csv(path, index=False)


def check(design_path: Path) -> int:
    """Commit C's readiness, before the round is due: the design is final and every job builds."""
    design = load_design(design_path)
    jobs, workloads = plan(REHEARSAL_MASTER, design)
    parts: dict[str, int] = {}
    for j in jobs:
        key = "dose" if j["part"] == "dose" else f"comp {j['plant']}"
        parts[key] = parts.get(key, 0) + 1
    print(f"{design_path.name} is final: {len(jobs)} jobs build {parts}, {len(workloads)} workloads drawn "
          "(with a made-up seed: this checks identities and sizes, not the registered draw)")
    return 0


def master_of(args: argparse.Namespace) -> str:
    if args.rehearsal:
        if args.master:
            raise SystemExit("a rehearsal uses its own made-up master seed, never a draw's")
        return REHEARSAL_MASTER
    if not args.master or not HEX64.fullmatch(args.master):
        raise SystemExit("--master takes the draw's master seed, 64 lower-case hex digits")
    return args.master


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--master", help="the registered draw's master seed (confirm.py passes it)")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--design", type=Path, default=DESIGN)
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--rehearsal", action="store_true",
                    help="two of everything, a made-up seed (a pipeline check: one failed run fails it)")
    ap.add_argument("--check", action="store_true",
                    help="before the round is due: the design is final and every job builds (nothing runs)")
    args = ap.parse_args(argv)
    if args.check:
        return check(args.design)
    if args.out is None:
        raise SystemExit("--out is required to run")
    master = master_of(args)
    if not 0 <= args.shard < args.shards:
        raise SystemExit(f"--shard must be one of 0..{args.shards - 1}")
    design = load_design(args.design, args.rehearsal)
    jobs, workloads = plan(master, design, args.rehearsal)
    mine = jobs[args.shard::args.shards]
    args.out.mkdir(parents=True, exist_ok=True)
    population_file = args.out / "population.json"
    write_population(population_file, workloads)
    t0 = time.time()
    print(f"shard {args.shard} of {args.shards}: {len(mine)} of {len(jobs)} runs on {args.workers} workers",
          flush=True)
    rows = execute(mine, population_file, args.workers)
    write_rows(args.out / "rows.csv", rows)
    failed = sum(1 for r in rows if r.get("error"))
    print(f"done in {time.time() - t0:.0f}s: {len(rows)} runs, {failed} failed (recorded, not retried)")
    if args.rehearsal and failed:   # the campaign records a failed run as missing data; a rehearsal stops on it
        first = next(r for r in rows if r.get("error"))
        print(f"rehearsal failed: {first['identity']}: {first['error']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
