"""Where WP11's attainment error comes from beyond replica scaling: the two
tiers share one host (exploratory, descriptive; programme 2).

The simulator treats each tier pool as an independent server, and calibration
measured each tier alone. On the testbed both tiers share the host's cores.
Two checks on WP11's kept runs, neither a prediction:

  within-run   every answered, scored cache miss: log(latency / L0) on its own
               tier's in-flight requests per pod and the other tier's busy
               cores (cores per pod x min(pods, in-flight)), both averaged over
               the request's own life; cell fixed effects, standard errors
               clustered by run. A request starts at the run's schedule origin
               (from k6's end timestamps) plus its scheduled time.
  in-sample    predict.py's per-step model with each pool's capacity measured
               at its size, plus a coupling: in each step a tier's service is
               slowed by s = exp(gamma x the other tier's busy cores), L0 x s
               and capacity / s, the busy cores by Little's law from the other
               tier's predicted latency, iterated; hits per step are the runs'
               own (WP11 predicts them exactly). Attainment scored as score.py
               does, over a grid of gamma chosen on the same runs: how much a
               coupling could explain, not how well it predicts.

    python coupling.py --runs runs          # writes COUPLING.md
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import urllib.parse
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import predict as pr  # noqa: E402
import score  # noqa: E402
from check_run import METRIC, open_text, read_k6_requests, summarise  # noqa: E402

cs = pr.cs
GRID_S = 0.5
GAMMAS = (0.0, 0.05, 0.10, 0.15, 0.20, 0.30)
FIXED_POINT_ITERATIONS = 4
OTHER = {"small": "mid", "mid": "small"}


def run_requests(run: Path, table: dict[int, dict]) -> list[dict]:
    """The run's requests in its cell's table, with start and end times."""
    reqs = []
    with open_text(run / "k6-samples.csv.gz") as fh:
        for row in csv.DictReader(fh):
            if row["metric_name"] != METRIC:
                continue
            tags = {k: v[0] for k, v in urllib.parse.parse_qs(row.get("extra_tags") or "").items()}
            seq = int(tags.get("seq", -1))
            if seq in table:
                reqs.append({"seq": seq, "tenant": tags.get("tenant", ""), "hit": tags.get("cache") == "hit",
                             "status": row.get("status") or "", "end_s": float(row["timestamp"]),
                             "latency_s": float(row["metric_value"]) / 1000})
    origin = float(np.median([r["end_s"] - r["latency_s"] - float(table[r["seq"]]["t"]) for r in reqs]))
    for r in reqs:
        r["start"] = origin + float(table[r["seq"]]["t"])
        r["end"] = r["start"] + r["latency_s"]
        r["tier"] = cs.PINS[r["tenant"]]
        r["warmup"] = str(table[r["seq"]]["warmup"]) in ("1", "True", "true")
    return reqs


def scored_run(run: Path) -> bool:
    """The runs score.py scores: valid, and on AC power at start and end."""
    info = json.loads((run / "run.json").read_text(encoding="utf-8"))
    return bool(info.get("valid")) and score.on_mains(info)


def mean_inflight(starts: np.ndarray, ends: np.ndarray, a: float, b: float, exclude: int | None = None) -> float:
    """Time-averaged number of the intervals overlapping [a, b]."""
    overlap = np.clip(np.minimum(ends, b) - np.maximum(starts, a), 0.0, None)
    if exclude is not None:
        overlap[exclude] = 0.0
    return float(overlap.sum() / max(b - a, 1e-9))


def busy_cores_cumsum(starts: np.ndarray, ends: np.ndarray, pods: int, cores: int, t0: float, n: int) -> np.ndarray:
    """Cumulative sum over a GRID_S grid from t0 of cores x min(pods, in-flight)."""
    count = np.zeros(n)
    for a, b in zip(starts, ends):
        count[int((a - t0) // GRID_S):int((b - t0) // GRID_S) + 1] += 1
    return np.concatenate([[0.0], np.cumsum(np.minimum(count, pods) * cores)])


def within_run_rows(cell: dict, runs: Path, inputs: Path, cal: dict) -> dict[str, list[tuple]]:
    """(log latency / L0, own in-flight per pod, other busy cores, cell, run) per scored answered miss."""
    k, pods = cell["cell"], {"small": cell["pools"][0], "mid": cell["pools"][1]}
    table = {int(r["seq"]): r for r in cs.read_table(inputs / f"cell{k:02d}.csv.gz")}
    rows: dict[str, list[tuple]] = {"small": [], "mid": []}
    for run in sorted((runs / f"cell{k:02d}").glob("run*")):
        if not scored_run(run):
            continue
        misses = [r for r in run_requests(run, table) if not r["hit"]]
        if not misses:
            continue
        by = {t: [r for r in misses if r["tier"] == t] for t in pods}
        spans = {t: (np.array([r["start"] for r in by[t]]), np.array([r["end"] for r in by[t]])) for t in pods}
        t0, t1 = min(r["start"] for r in misses), max(r["end"] for r in misses)
        n = int((t1 - t0) // GRID_S) + 2
        busy = {t: busy_cores_cumsum(*spans[t], pods[t], pr.CORES[t], t0, n) for t in pods}
        for tier in pods:
            for i, r in enumerate(by[tier]):
                if r["warmup"] or r["status"] != "200":
                    continue
                lo, hi = int((r["start"] - t0) // GRID_S), int((r["end"] - t0) // GRID_S) + 1
                other = (busy[OTHER[tier]][hi] - busy[OTHER[tier]][lo]) / max(hi - lo, 1)
                rows[tier].append((math.log(r["latency_s"] / cal[tier]["l0_s"]),
                                   mean_inflight(*spans[tier], r["start"], r["end"], i) / pods[tier],
                                   other, k, f"{k}/{run.name}"))
    return rows


def ols_fixed_effects(rows: list[tuple]) -> dict:
    """OLS of y on the two regressors with a fixed effect per cell; standard
    errors clustered by run (no small-sample correction)."""
    y = np.array([r[0] for r in rows])
    fe = sorted({r[3] for r in rows})
    X = np.column_stack([[r[1] for r in rows], [r[2] for r in rows]] +
                        [[1.0 if r[3] == c else 0.0 for r in rows] for c in fe])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    bread = np.linalg.pinv(X.T @ X)
    meat = np.zeros((X.shape[1], X.shape[1]))
    clusters = np.array([r[4] for r in rows])
    for g in sorted(set(clusters)):
        score = X[clusters == g].T @ resid[clusters == g]
        meat += np.outer(score, score)
    se = np.sqrt(np.diag(bread @ meat @ bread))
    return {"n": len(rows), "runs": len(set(clusters)), "own": float(beta[0]), "own_se": float(se[0]),
            "other": float(beta[1]), "other_se": float(se[1])}


def measured(cell: dict, runs: Path, inputs: Path, slo_ms: float) -> tuple[float, dict]:
    """Mean attainment over the valid runs (score.py's rule) and their mean hits per (step, tenant)."""
    k = cell["cell"]
    table = {int(r["seq"]): r for r in cs.read_table(inputs / f"cell{k:02d}.csv.gz")}
    valid = [r for r in sorted((runs / f"cell{k:02d}").glob("run*")) if scored_run(r)]
    atts, hits = [], {}
    for run in valid:
        atts.append(summarise(read_k6_requests(run / "k6-samples.csv.gz"), slo_ms)["attainment"])
        for r in run_requests(run, table):
            if r["hit"]:
                key = (min(int(float(table[r["seq"]]["t"]) // pr.BIN_S), cs.STEPS - 1), r["tenant"])
                hits[key] = hits.get(key, 0.0) + 1.0 / len(valid)
    return float(np.mean(atts)), hits


def tier_steps(cell: dict, demand: dict, hits: dict, calm: dict, slow: dict, slo_ms: float) -> dict:
    """(n, hits, step_outcome) per (tier, step), each tier's service slowed by slow[(tier, step)]."""
    pods = {"small": cell["pools"][0], "mid": cell["pools"][1]}
    out = {}
    for tier in pods:
        tenants = [t for t, p in cs.PINS.items() if p == tier]
        for step in range(cs.STEPS):
            n, h = pr._pool_counts(demand, tenants, step, hits)
            if n <= 0:
                continue
            s = slow.get((tier, step), 1.0)
            c = {**calm[tier], "l0_s": calm[tier]["l0_s"] * s,
                 "capacity_per_replica_rps": calm[tier]["capacity_per_replica_rps"] / s}
            with pr.tier_form(c, slo_ms) as cfg:
                out[(tier, step)] = (n, h, pr.step_outcome(cfg, pods[tier], tier, n, h))
    return out


def coupled_attainment(cell: dict, demand: dict, hits: dict, calm: dict, gamma: float, slo_ms: float) -> float:
    """Predicted attainment with the host coupling (gamma 0: predict.py's measured_capacity form)."""
    pods = {"small": cell["pools"][0], "mid": cell["pools"][1]}
    slow: dict = {}
    for _ in range(FIXED_POINT_ITERATIONS if gamma > 0 else 0):
        busy = {}
        for (tier, step), (n, h, o) in tier_steps(cell, demand, hits, calm, slow, slo_ms).items():
            miss_s = 0.0 if math.isnan(o["miss_ms"]) else o["miss_ms"] / 1000
            busy[(tier, step)] = pr.CORES[tier] * min(pods[tier], (n - h) / pr.BIN_S * miss_s)
        slow = {(t, s): math.exp(gamma * busy.get((OTHER[t], s), 0.0)) for t in pods for s in range(cs.STEPS)}
    within = served = 0.0
    for (tier, step), (n, h, o) in tier_steps(cell, demand, hits, calm, slow, slo_ms).items():
        if step >= pr.WARMUP_STEPS:
            within, served = within + o["within"], served + n
    return within / served


spearman = score.spearman  # average ranks for ties


def sweep(cells: list[dict], runs: Path, inputs: Path, cal: dict, calibration: Path, gammas) -> tuple[list, list]:
    slo_ms = pr.SLO_FACTOR * cal["small"]["l0_s"] * 1000
    obs, data = [], []
    for cell in cells:
        att, hits = measured(cell, runs, inputs, slo_ms)
        obs.append(att)
        data.append((cell, pr.demand_from_table(cs.read_table(inputs / f"cell{cell['cell']:02d}.csv.gz")), hits,
                     pr.with_measured_capacity(cal, calibration, cell["pools"])))
    obs = np.array(obs)
    out = []
    for g in gammas:
        pred = np.array([coupled_attainment(c, d, h, m, g, slo_ms) for c, d, h, m in data])
        ape = np.abs(pred - obs) / np.maximum(np.abs(obs), 1e-12)
        out.append({"gamma": g, "mape_median": float(np.median(ape)), "spearman": spearman(pred, obs),
                    "mean_diff": float(np.mean(pred - obs)), "pred": pred.tolist()})
    return out, obs.tolist()


def report(within: dict, sw: list, obs: list, targets: dict, cells: list[dict]) -> str:
    lines = ["# WP11: the two tiers share one host (exploratory)", "",
             "Generated by `testbed/validation/coupling.py` from WP11's kept runs (`runs/`). Descriptive and "
             "in-sample: no number here was fixed before the runs, and none is a validation.", "",
             "## Within runs", "",
             "Every answered, scored cache miss: log(latency / L0) on its own tier's in-flight requests per pod and "
             "the other tier's busy cores (cores per pod x min(pods, in-flight)), each averaged over the request's "
             "own life; cell fixed effects, standard errors clustered by run.", "",
             "| tier | requests | runs | own in-flight per pod | other tier's busy cores |", "|---|---:|---:|---|---|"]
    for tier, w in within.items():
        lines.append(f"| {tier} | {w['n']} | {w['runs']} | {w['own']:+.3f} (se {w['own_se']:.3f}) | "
                     f"{w['other']:+.3f} (se {w['other_se']:.3f}) |")
    lines += ["", "## In-sample: the measured-capacity form plus a coupling", "",
              "Each tier's service slowed by exp(gamma x the other tier's busy cores) per step; hits per step from "
              "the runs; gamma chosen on the same runs. Targets as in VALIDATION.md (median absolute error <= "
              f"{targets['median_abs_pct_error_max']:.0%}, Spearman >= {targets['rank_correlation_min']}).", "",
              "| gamma | median abs. % error | Spearman | mean diff (pred - meas) |", "|---:|---:|---:|---:|"]
    for s in sw:
        lines.append(f"| {s['gamma']:.2f} | {s['mape_median']:.1%} | {s['spearman']:+.2f} | {s['mean_diff']:+.3f} |")
    lines += ["", "## Per cell", "", "| cell | pools | attainment measured | " +
              " | ".join(f"gamma {s['gamma']:.2f}" for s in sw) + " |", "|---:|---|---:|" + "---:|" * len(sw)]
    for i, cell in enumerate(cells):
        lines.append(f"| {cell['cell']} | {cell['pools'][0]}+{cell['pools'][1]} | {obs[i]:.3f} | " +
                     " | ".join(f"{s['pred'][i]:.3f}" for s in sw) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=Path, required=True)
    ap.add_argument("--cells", type=Path, default=HERE / "cells.json")
    ap.add_argument("--inputs", type=Path, default=HERE / "inputs")
    ap.add_argument("--predictions", type=Path, default=HERE / "predictions.json")
    ap.add_argument("--calibration", type=Path, default=HERE / "calibration")
    ap.add_argument("--out", type=Path, default=HERE / "COUPLING.md")
    args = ap.parse_args()
    design = json.loads(args.cells.read_text(encoding="utf-8"))
    cal = json.loads(args.predictions.read_text(encoding="utf-8"))["calibration"]
    within: dict[str, list] = {"small": [], "mid": []}
    for cell in design["cells"]:
        for tier, rows in within_run_rows(cell, args.runs, args.inputs, cal).items():
            within[tier] += rows
    fits = {tier: ols_fixed_effects(rows) for tier, rows in within.items()}
    sw, obs = sweep(design["cells"], args.runs, args.inputs, cal, args.calibration, GAMMAS)
    args.out.write_text(report(fits, sw, obs, design["targets"], design["cells"]), encoding="utf-8")
    print(args.out.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
