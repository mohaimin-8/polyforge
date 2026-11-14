"""WP12's design, tuning and plans, all before any live run (programme 2).

  regimes the two regimes (plan WP12: one low-index, one high-index): every
          candidate load level (CANDIDATE_UTILS, the small pool's offered
          utilisation at its largest size with every request a miss, WP11's
          measure) on PROBES schedules, both arms at their declared defaults.
          Eligible: an operating point, the joint planner's predicted
          attainment at least MIN_ATTAINMENT. Low = the eligible level with
          the smallest mean interaction index of the joint planner's
          decisions, high = the largest, when the largest is at least
          MIN_INDEX_RATIO x the smallest; otherwise the testbed has no
          high-index regime and the two are the lightest and the heaviest
          operating points -> regimes.json.
  design  the two regimes, each with PAIRS live schedules and TUNING tuning
          schedules of real arrivals: cell_schedules.build_cell's anchored
          native replay of an evaluation-half window, small share SMALL_SHARE,
          the three WP11 streams in turn. A live schedule carries real prompts
          (--out-schedules, never committed) and a request table without text
          (wp12/inputs); a tuning schedule only its demand (wp12/inputs/tuning,
          the same numbers build_cell would draw, test_plans.py checks).
  tune    each planner arm's 64 configurations (WP9's own draws,
          arms.sample_configs with seed 20261112) on every tuning schedule,
          scored by the planners' own prediction of WP9's objective
          (tb_planner.predicted_outcome); one configuration per arm overall:
          lowest mean J over the tuning schedules, ties to the lower index,
          a configuration that failed on any schedule excluded (protocol.select's
          rule). Journaled, so a stopped tuning resumes.
  plans   the two tuned arms on every live schedule: the plan, its predicted
          outcome and the interaction index of every decision -> plans.json.
          pairs.py runs nothing unless plans.json is committed and unchanged.

    python plans.py regimes --calibration ../validation/calibration --scaling fitted
    python plans.py design --curves ../validation/calibration --out-schedules ../out/wp12/schedules
    python plans.py tune --calibration ../out/curves --scaling fitted --workers 4
    python plans.py plans --calibration ../out/curves --scaling fitted
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import gzip
import hashlib
import io
import json
import math
import sys
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import tb_planner as tbp  # noqa: E402  (puts research/ and testbed/validation on the path)

import arms  # noqa: E402
import cell_schedules as cs  # noqa: E402
import predict  # noqa: E402

REGIMES = {"low": 0.30, "high": 0.75}   # used only when regimes.json has not chosen them
CANDIDATE_UTILS = (0.1, 0.15, 0.2, 0.3, 0.45, 0.6, 0.75, 0.9)
PROBES = 6                 # probe schedules per candidate level, the streams in turn
MIN_ATTAINMENT = 0.8       # a regime is an operating point: the joint planner meets the SLO this often
MIN_INDEX_RATIO = 2.0      # below this, the highest- and lowest-index levels are no index contrast
SMALL_SHARE = 0.7
STREAMS = ("burstgpt/ChatGPT/api", "burstgpt/ChatGPT/conv", "azure2024/conv")
PAIRS = 8
TUNING = 20
POOL_REF = (3, 1)          # the target is relative to the small tier's capacity at its largest pool
CELL_BASE = 12000
CONFIGS = 64
WP9_SEED = 20261112
PRICE_CORE_HR = 0.526 / 4  # predict.py's default: a T4 box's price over its 4 vCPU
DESIGN = HERE / "design.json"
INPUTS = HERE / "inputs"
TUNING_DIR = HERE / "tuning"
PLANS = HERE / "plans.json"
BOUNDS = tbp.TESTBED / "actuator" / "bounds.json"
JOURNAL_FIELDS = ("arm", "config", "schedule", "J", "cost_term", "violation_term", "unfairness_term",
                  "cost_per_1k", "attainment", "error")


REGIMES_FILE = HERE / "regimes.json"


def regimes_in_use(path: Path = REGIMES_FILE) -> dict[str, float]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["chosen"]
    return dict(REGIMES)


def schedule_id(regime: str, role: str, k: int) -> int:
    return CELL_BASE + 100 * ("low", "high").index(regime) + (50 if role == "tuning" else 0) + k


def probe_cell(i: int, util: float, k: int) -> dict:
    return {"cell": CELL_BASE + 800 + 10 * i + k, "target_util": util, "small_share": SMALL_SHARE,
            "stream": STREAMS[k % len(STREAMS)], "pools": list(POOL_REF), "cache": "off"}


def load_sources() -> tuple[dict, dict, int]:
    """population.json, every design stream's native 10 s counts, bins per day."""
    import make_schedule as mk

    native = mk.research_module("native")
    population = json.loads(mk.POPULATION.read_text(encoding="utf-8"))
    with np.load(native.COUNTS) as z:
        counts = {s: z[s.replace("/", "__")] for s in STREAMS}
    return population, counts, native.DAY_BINS


def level_summary(rows: Sequence[dict]) -> dict[float, dict]:
    """Per candidate level: mean index, mean predicted attainment, eligible."""
    by: dict[float, list[dict]] = {}
    for r in rows:
        by.setdefault(r["util"], []).append(r)
    out = {}
    for u, rs in sorted(by.items()):
        att = float(np.mean([r["attainment"] for r in rs]))
        out[u] = {"mean_index": float(np.mean([r["mean_index"] for r in rs])), "mean_attainment": att,
                  "eligible": att >= MIN_ATTAINMENT}
    return out


def index_contrast(rows: Sequence[dict]) -> dict:
    """The eligible levels (the joint planner's predicted attainment at least
    MIN_ATTAINMENT; every level when fewer than two are), the levels with the
    smallest and largest mean index (ties: lower, then higher level), their
    ratio, and whether it is a contrast (at least MIN_INDEX_RATIO)."""
    levels = level_summary(rows)
    eligible = {u: s for u, s in levels.items() if s["eligible"]}
    pool = eligible if len(eligible) >= 2 else levels
    lo = min(pool, key=lambda u: (pool[u]["mean_index"], u))
    hi = max((u for u in pool if u != lo), key=lambda u: (pool[u]["mean_index"], u))
    ratio = pool[hi]["mean_index"] / pool[lo]["mean_index"] if pool[lo]["mean_index"] > 0 else math.inf
    return {"pool": sorted(pool), "lowest_index": lo, "highest_index": hi, "ratio": ratio,
            "contrast": ratio >= MIN_INDEX_RATIO}


def choose_regimes(rows: Sequence[dict]) -> dict[str, float]:
    """Low and high index (plan WP12) when the eligible levels offer an index
    contrast; without one there is no high-index regime on the testbed, and
    the two regimes are the lightest and the heaviest operating points."""
    c = index_contrast(rows)
    if c["contrast"]:
        return {"low": c["lowest_index"], "high": c["highest_index"]}
    return {"low": min(c["pool"]), "high": max(c["pool"])}


def probe_regimes(tb: tbp.Testbed, curves: Path, out_file: Path | None = None) -> dict:
    """Plan WP12's regimes: every candidate load level, PROBES schedules
    each, both arms at their declared defaults; the joint planner's index."""
    population, counts, day_bins = load_sources()
    rows = []
    for i, util in enumerate(CANDIDATE_UTILS):
        for k in range(PROBES):
            cell = probe_cell(i, util, k)
            demand = demand_counts(cell, curves, population, counts, day_bins)
            res = {arm: tbp.plan_run(arm, arms.defaults(arms.space(arm)), demand, tb, cs.STEPS, with_index=True)
                   for arm in tbp.PLANNER_ARMS}
            s = tbp.index_summary(res["joint"]["index_rows"])
            rows.append({"util": util, "k": k, "cell": cell["cell"], "stream": cell["stream"],
                         "requests": int(sum(demand.values())), **{f"joint_{m}": v for m, v in s.items()},
                         "mean_index": s["mean_index"], "attainment": res["joint"]["outcome"]["attainment"],
                         "predicted_diff_J": res["per_knob"]["outcome"]["J"] - res["joint"]["outcome"]["J"]})
    out = {"rule": f"among the levels where the joint planner's predicted attainment is at least {MIN_ATTAINMENT} "
                   "(an operating point; every level if fewer than two are), low = the smallest mean index of its "
                   "decisions, high = the largest (plan WP12: one low-index and one high-index regime), when the "
                   f"largest is at least {MIN_INDEX_RATIO:g} x the smallest; otherwise there is no high-index regime "
                   "and the regimes are the lightest and the heaviest operating points; both arms at their "
                   "declared defaults",
           "levels": {str(u): s for u, s in level_summary(rows).items()},
           "index_contrast": index_contrast(rows), "candidates": rows, "chosen": choose_regimes(rows)}
    (out_file or REGIMES_FILE).write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def cells(regimes: dict | None = None) -> list[dict]:
    """Every schedule of the design as a cell_schedules cell."""
    out = []
    for regime, util in (regimes or regimes_in_use()).items():
        for role, n in (("live", PAIRS), ("tuning", TUNING)):
            for k in range(n):
                out.append({"cell": schedule_id(regime, role, k), "regime": regime, "role": role, "k": k,
                            "target_util": util, "small_share": SMALL_SHARE, "stream": STREAMS[k % len(STREAMS)],
                            "pools": list(POOL_REF), "cache": "off"})
    return out


def demand_counts(cell: dict, curves: Path, population: dict, counts_by_stream: dict,
                  day_bins: int) -> dict[tuple[int, str], int]:
    """build_cell's per-step, per-tenant counts without placing or prompting
    a request: the same random draws in the same order."""
    rng = np.random.default_rng([cs.SEED, cell["cell"]])
    c_small = cs.capacity(curves, "small", cell["pools"][0])
    target = int(round(cell["target_util"] * c_small * cs.DURATION_S / cell["small_share"]))
    days = cs.stream_days(counts_by_stream[cell["stream"]], day_bins)
    _w, day, start = cs.choose_anchor(cs.evaluation_windows(population, cell["stream"]), len(days), day_bins, rng)
    counts, _passes = cs.anchored_counts(days, day, start, cs.STEPS, target, rng)
    split = cs.split_counts(counts, cell["small_share"], rng)
    return {(k, t): int(split[t][k]) for t in cs.TENANTS for k in range(cs.STEPS) if split[t][k]}


def write_demand(path: Path, demand: Mapping[tuple[int, str], int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(("step", "tenant", "requests"))
    for (k, t), n in sorted(demand.items()):
        w.writerow((k, t, n))
    with gzip.GzipFile(path, "wb", mtime=0) as fh:
        fh.write(buf.getvalue().encode("utf-8"))


def read_demand(path: Path) -> dict[tuple[int, str], int]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return {(int(r["step"]), r["tenant"]): int(r["requests"]) for r in csv.DictReader(fh)}


def schedule_demand(entry: dict, base: Path = HERE) -> dict[tuple[int, str], int]:
    path = base / entry["input"]
    if entry["role"] == "tuning":
        return read_demand(path)
    return predict.demand_from_table(cs.read_table(path))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def design(curves: Path, out_schedules: Path, base: Path | None = None, regimes: dict | None = None) -> dict:
    import make_schedule as mk
    import pandas as pd

    base, regimes = base or HERE, regimes or regimes_in_use()
    population, counts, day_bins = load_sources()
    cache_bytes = pd.read_parquet(cs.wp6_meta_path(), columns=["prompt_bytes"])["prompt_bytes"].to_numpy()
    entries = []
    for cell in cells(regimes):
        name = f"cell{cell['cell']}"
        demand = demand_counts(cell, curves, population, counts, day_bins)
        if cell["role"] == "live":
            built = cs.build_cell(cell, curves, population, counts, day_bins, mk.lmsys_first_turns, cache_bytes)
            cs.write_cell(out_schedules / name, built, base / "inputs")
            rel = f"inputs/{name}.csv.gz"
            if predict.demand_from_table(cs.read_table(base / rel)) != demand:
                raise SystemExit(f"{name}: the schedule's demand differs from demand_counts")
        else:
            rel = f"inputs/tuning/{name}.csv.gz"
            write_demand(base / rel, demand)
        entries.append({**{k: cell[k] for k in ("cell", "regime", "role", "k", "stream", "target_util")},
                        "requests": int(sum(demand.values())), "input": rel, "sha256": sha256(base / rel)})
    out = {"protocol": "plan WP12; testbed/wp12/plans.py", "regimes": regimes, "small_share": SMALL_SHARE,
           "pairs": PAIRS, "tuning": TUNING, "streams": list(STREAMS), "pool_ref": list(POOL_REF),
           "seed": cs.SEED, "schedules": entries}
    (base / "design.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


_DEMAND_CACHE: dict[int, dict] = {}


def tune_job(job: tuple) -> dict:
    """One (arm, configuration, tuning schedule): the planners' predicted J."""
    arm, ci, params, entry, tb, base = job
    row = {"arm": arm, "config": ci, "schedule": entry["cell"]}
    try:
        if entry["cell"] not in _DEMAND_CACHE:
            _DEMAND_CACHE[entry["cell"]] = schedule_demand(entry, base)
        o = tbp.plan_run(arm, params, _DEMAND_CACHE[entry["cell"]], tb, cs.STEPS)["outcome"]
        row.update({k: o[k] for k in ("J", "cost_term", "violation_term", "unfairness_term", "cost_per_1k",
                                      "attainment")}, error="")
    except tbp.PlanError as err:
        row.update({k: math.nan for k in JOURNAL_FIELDS[3:-1]}, error=str(err)[:200])
    return row


def read_journal(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return [{**r, "config": int(r["config"]), "schedule": int(r["schedule"]), "J": float(r["J"])}
                for r in csv.DictReader(fh)]


def select(rows: Sequence[dict], n_schedules: int) -> dict[str, dict]:
    """Per arm: the configuration with the lowest mean J over every tuning
    schedule; one that failed anywhere, or has a schedule missing, is out."""
    out = {}
    for arm in tbp.PLANNER_ARMS:
        by: dict[int, list[float]] = {}
        for r in rows:
            if r["arm"] == arm:
                by.setdefault(r["config"], []).append(r["J"] if not r.get("error") else math.nan)
        means = {c: float(np.mean(v)) for c, v in by.items() if len(v) == n_schedules and np.all(np.isfinite(v))}
        if not means:
            raise SystemExit(f"{arm}: no configuration ran cleanly on all {n_schedules} tuning schedules")
        best = min(means, key=lambda c: (means[c], c))
        out[arm] = {"config": best, "mean_J": means[best], "configs_scored": len(means)}
    return out


def tune(tb: tbp.Testbed, workers: int, out_dir: Path = TUNING_DIR, scaling: str = "", design_path: Path = DESIGN,
         configs_per_arm: int = CONFIGS) -> dict:
    d = json.loads(design_path.read_text(encoding="utf-8"))
    tuning = [e for e in d["schedules"] if e["role"] == "tuning"]
    configs = {arm: arms.sample_configs(arm, configs_per_arm, WP9_SEED) for arm in tbp.PLANNER_ARMS}
    out_dir.mkdir(parents=True, exist_ok=True)
    journal = out_dir / "journal.csv"
    done = {(r["arm"], r["config"], r["schedule"]) for r in read_journal(journal)}
    jobs = [(arm, ci, p, e, tb, design_path.parent) for arm in tbp.PLANNER_ARMS for ci, p in enumerate(configs[arm])
            for e in tuning if (arm, ci, e["cell"]) not in done]
    new = not journal.exists()
    pool = cf.ProcessPoolExecutor(max_workers=workers) if workers > 1 else None
    try:
        results = pool.map(tune_job, jobs, chunksize=8) if pool else map(tune_job, jobs)
        with journal.open("a", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=JOURNAL_FIELDS)
            if new:
                w.writeheader()
            for i, row in enumerate(results):
                w.writerow(row)
                if i % 64 == 0:
                    fh.flush()
    finally:
        if pool:
            pool.shutdown()
    rows = read_journal(journal)
    picks = select(rows, len(tuning))
    tuned = {"settings": {"scaling": scaling, "configs": configs_per_arm, "seed": WP9_SEED,
                          "tuning_schedules": len(tuning),
                          "testbed": tb.as_dict() if tb is not None else None},
             "arms": {arm: {**s, "params": configs[arm][s["config"]]} for arm, s in picks.items()}}
    (out_dir / "tuned.json").write_text(json.dumps(tuned, indent=1), encoding="utf-8")
    return tuned


def plans(tb: tbp.Testbed, tuned: dict, scaling: str, design_path: Path | None = None,
          out_file: Path | None = None) -> dict:
    design_path = design_path or DESIGN
    d = json.loads(design_path.read_text(encoding="utf-8"))
    out = []
    for e in (x for x in d["schedules"] if x["role"] == "live"):
        demand = schedule_demand(e, design_path.parent)
        for arm in tbp.PLANNER_ARMS:
            params = tuned["arms"][arm]["params"]
            r = tbp.plan_run(arm, params, demand, tb, cs.STEPS, with_index=True)
            out.append({"schedule": e["cell"], "regime": e["regime"], "k": e["k"], "arm": arm,
                        "decisions": tbp.decisions(r["path"], tb), "predicted": r["outcome"],
                        "index": tbp.index_summary(r["index_rows"])})
    summary = {}
    for regime in d["regimes"]:
        diffs = {m: [] for m in ("J", "cost_per_1k", "attainment")}
        for k in range(d["pairs"]):
            p = {x["arm"]: x["predicted"] for x in out if x["regime"] == regime and x["k"] == k}
            for m in diffs:
                diffs[m].append(p["per_knob"][m] - p["joint"][m])
        idx = [x["index"].get("mean_index", math.nan) for x in out if x["regime"] == regime and x["arm"] == "joint"]
        summary[regime] = {"predicted_mean_diff": {m: float(np.mean(v)) for m, v in diffs.items()},
                           "joint_mean_index": float(np.nanmean(idx))}
    result = {"rules": {"design": "testbed/wp12/design.json", "scaling": scaling, "bin_s": tbp.BIN_S,
                        "warmup_steps": tbp.WARMUP_STEPS, "difference": "per_knob minus joint",
                        "tuned": "testbed/wp12/tuning/tuned.json"},
              "testbed": tb.as_dict(), "tuned": tuned["arms"], "summary": summary, "plans": out}
    (out_file or PLANS).write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("design")
    d.add_argument("--curves", type=Path, required=True)
    d.add_argument("--out-schedules", type=Path, required=True)
    for name in ("regimes", "tune", "plans"):
        p = sub.add_parser(name)
        p.add_argument("--calibration", type=Path, required=True)
        p.add_argument("--scaling", choices=("linear", "fitted"), required=True)
        p.add_argument("--price-core-hr", type=float, default=PRICE_CORE_HR)
        if name == "tune":
            p.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)
    if args.cmd == "design":
        out = design(args.curves, args.out_schedules)
        for e in out["schedules"]:
            print(f"{e['cell']} {e['regime']:4s} {e['role']:6s} {e['stream']:22s} {e['requests']:5d} requests")
        return 0
    tb = tbp.testbed_from(args.calibration, BOUNDS, args.scaling, args.price_core_hr)
    if args.cmd == "regimes":
        r = probe_regimes(tb, args.calibration)
        for row in r["candidates"]:
            print(f"util {row['util']:.2f} {row['stream']:22s} index {row['mean_index']:.4f} "
                  f"predicted per_knob - joint J {row['predicted_diff_J']:+.5f}")
        print(f"chosen: {r['chosen']}")
        return 0
    if args.cmd == "tune":
        t = tune(tb, args.workers, scaling=args.scaling)
        for arm, s in t["arms"].items():
            print(f"{arm}: config {s['config']} mean J {s['mean_J']:.5f} ({s['configs_scored']} scored)")
        return 0
    tuned = json.loads((TUNING_DIR / "tuned.json").read_text(encoding="utf-8"))
    if tuned["settings"]["scaling"] != args.scaling:
        raise SystemExit(f"tuned.json was tuned with {tuned['settings']['scaling']!r} scaling, not {args.scaling!r}")
    r = plans(tb, tuned, args.scaling)
    for regime, s in r["summary"].items():
        print(f"{regime}: predicted per_knob - joint {s['predicted_mean_diff']}, joint mean index "
              f"{s['joint_mean_index']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
