"""Index pilot (programme 2, WP3 step 11): the interaction index on every
decision of existing campaigns. EXPLORATORY: these regimes and seeds were
seen before; nothing here is confirmatory.

For each regime, replication 0 of every design point is re-run with
`IndexedController`; the run's metrics must equal the committed export (the
logger changes no decision). Per decision: Theorem 1 is checked; per run:
the index, the one-step loss and the disagreement are summarised and set
beside the run-level coordination gain the campaign measured (joint minus
decoupled arm on the campaign's objective, same seeds).

    python index_pilot.py            # writes data/index_pilot_runs.csv.gz and INDEX_PILOT.md
"""

from __future__ import annotations

import math
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (HERE, REPO / "research" / "jcac_sim", REPO / "eval"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from controller_index import execute_indexed  # noqa: E402
from harness import config as hconfig  # noqa: E402
from harness import workloads  # noqa: E402

EPS = 1e-9
DESIGN = ["workload", "tenant_mix", "cluster_size"]
METRICS = ["total_cost_usd", "mean_violation", "mean_jain", "mean_excess", "tier_none_step_share"]
# regime -> (experiment file, joint arm, decoupled arm, objective of the campaign)
REGIMES = {
    "width/published/narrow": ("matrix_width_published", "jcac_joint_tracked", "jcac_decoupled", "J"),
    "width/published/wide": ("matrix_width_published", "jcac_joint_wide_tracked", "jcac_decoupled_wide", "J"),
    "width/coupled/narrow": ("matrix_width_coupled", "jcac_joint_tracked", "jcac_decoupled", "J"),
    "width/coupled/wide": ("matrix_width_coupled", "jcac_joint_wide_tracked", "jcac_decoupled_wide", "J"),
    "width/live/narrow": ("matrix_width_live", "jcac_joint_tracked", "jcac_decoupled", "J"),
    "width/live/wide": ("matrix_width_live", "jcac_joint_wide_tracked", "jcac_decoupled_wide", "J"),
    "jointness/main": ("matrix_jointness", "jcac_converged", "jcac_decoupled", "J"),
    "level2/main": ("matrix_level2", "jcac_level_ratchet", "jcac_level_ratchet_decoupled", "J_L"),
}


def _export(experiment: str) -> pd.DataFrame:
    return pd.read_csv(REPO / "eval" / "results" / f"metrics_{experiment}.csv.gz", float_precision="round_trip")


def objective(df: pd.DataFrame, kind: str) -> pd.Series:
    """The campaign's registered objective. J: the published composite
    (cost per 119 x 8 tenant-steps); J_L: LEVEL's, with every refused
    request charged the small tier's price and each run's own steps."""
    base = 2.0 * df.mean_violation + 0.5 * (1.0 - df.mean_jain)
    if kind == "J":
        return df.total_cost_usd / (119 * 8) / 0.01 + base
    s = df.shed_request_share.fillna(0.0)
    cps = df.cost_per_served_request.fillna(0.0)
    served = np.where(cps > 0, df.total_cost_usd / cps.where(cps > 0, 1.0), 0.0)
    shed = np.where((s > 0) & (cps > 0), served * s / (1.0 - s), 0.0)
    return (df.total_cost_usd + shed * 1e-4) / (df.steps * 8) / 0.01 + base


def jobs() -> list[tuple]:
    out = []
    for regime, (experiment, joint, _, _) in REGIMES.items():
        spec = hconfig.load(REPO / "eval" / "experiments" / f"{experiment}.yaml")
        # `objective` normalises by 8 tenants: refuse a mix of another size.
        assert all(len(workloads.TENANT_MIXES[m]) == 8 for m in spec.tenant_mixes), experiment
        for run in hconfig.expand(spec):
            if run.system == joint and run.rep == 0:
                out.append((regime, run))
    return out


def run_one(job: tuple) -> dict:
    regime, run = job
    started = time.time()
    result, rows = execute_indexed(run)
    rows = [r for r in rows if r["cycle"] <= run.steps]  # the last plan is never scored
    applies = [r for r in rows if r["theorem1_applies"]]
    violations = sum(1 for r in applies
                     if r["loss"] < -EPS or r["loss"] > r["bound"] + EPS or r["bound"] > r["index"] + EPS)
    idx = np.array([r["index"] for r in rows if r["index"] is not None])
    qidx = np.array([r["quantile_index"] for r in rows if r["quantile_index"] is not None])
    loss = np.array([r["loss"] for r in applies])
    loss_rep = np.array([r["loss_repaired"] for r in rows if r["loss_repaired"] is not None])
    return {
        "regime": regime, "workload": run.workload, "tenant_mix": run.tenant_mix,
        "cluster_size": run.cluster_size, "rep": run.rep, "seed": run.seed, "wall_s": time.time() - started,
        **{f"m_{k}": result["metrics"][k] for k in METRICS},
        "decisions": len(rows), "theorem_applies": len(applies), "theorem_violations": violations,
        "at_fixed_point_share": float(np.mean([r["at_fixed_point"] for r in rows])),
        "per_knob_infeasible_share": float(np.mean([not r["per_knob_feasible"] for r in rows])),
        "disagree_share": float(np.mean([r["disagree"] for r in rows])),
        "disagree_with_loss_share": float(np.mean([r["disagree_with_loss"] for r in rows])),
        "index_mean": float(idx.mean()) if idx.size else math.nan,
        "index_median": float(np.median(idx)) if idx.size else math.nan,
        "index_p90": float(np.percentile(idx, 90)) if idx.size else math.nan,
        "index_max": float(idx.max()) if idx.size else math.nan,
        "quantile_index_mean": float(qidx.mean()) if qidx.size else math.nan,
        "loss_mean": float(loss.mean()) if loss.size else math.nan,
        "loss_positive_share": float((loss > EPS).mean()) if loss.size else math.nan,
        "loss_repaired_mean": float(loss_rep.mean()) if loss_rep.size else math.nan,
        "loss_over_index_max": float(max((r["loss"] / r["index"] for r in applies if r["index"] > EPS),
                                         default=0.0)),
    }


def integrity(runs: pd.DataFrame) -> tuple[int, float]:
    """Compare each re-run's metrics with the committed export."""
    worst, n = 0.0, 0
    for regime, (experiment, joint, _, _) in REGIMES.items():
        ex = _export(experiment)
        ex = ex[(ex.system == joint) & (ex.rep == 0)].set_index(DESIGN)
        sub = runs[runs.regime == regime].set_index(DESIGN)
        for key, row in sub.iterrows():
            for m in METRICS:
                worst = max(worst, abs(float(ex.loc[key, m]) - float(row[f"m_{m}"])))
            n += 1
    return n, worst


def run_level_gain(regime: str) -> pd.Series:
    """Decoupled minus joint arm on the campaign's objective, replication 0
    (positive = joint better), per design point."""
    experiment, joint, decoupled, kind = REGIMES[regime]
    ex = _export(experiment)
    ex = ex[ex.rep == 0].assign(OBJ=lambda d: objective(d, kind))
    j = ex[ex.system == joint].set_index(DESIGN).OBJ
    d = ex[ex.system == decoupled].set_index(DESIGN).OBJ
    return (d - j.reindex(d.index)).rename("run_gain")


def boot_ci(x: np.ndarray, rng: np.random.Generator, n: int = 10_000) -> tuple[float, float]:
    idx = rng.integers(0, len(x), size=(n, len(x)))
    means = x[idx].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    return float(pd.Series(a).rank().corr(pd.Series(b).rank()))


def report(runs: pd.DataFrame, n_int: int, worst: float) -> str:
    rng = np.random.default_rng(20261112)
    gains = pd.concat({r: run_level_gain(r) for r in REGIMES}, names=["regime"]).reset_index()
    df = runs.merge(gains, on=["regime"] + DESIGN, how="left")
    total_dec, total_app, total_viol = runs.decisions.sum(), runs.theorem_applies.sum(), runs.theorem_violations.sum()
    fixed = float((runs.at_fixed_point_share * runs.decisions).sum() / total_dec)
    L = [
        "# Interaction index on existing campaigns (pilot, exploratory)",
        "",
        "Generated by `research/theory/index_pilot.py`. Programme 2, WP3. Every regime below was "
        "designed and run before this analysis existed; the numbers are exploratory and choose "
        "nothing confirmatory except, per the plan, the regimes of the dose-response pilot (WP4).",
        "",
        "## Integrity",
        "",
        f"{n_int} re-runs (replication 0 of every design point of each regime) with the index logger "
        f"attached; the largest difference between a re-run's metrics and the committed export is "
        f"{worst:.3g} (the runner refuses to write this report above 1e-12). "
        f"Decisions at a fixed point of the planner's sweeps, where the analysed decision is the "
        f"planner's own: {fixed:.2%}.",
        "",
        "## H1: the one-step loss never exceeds the index",
        "",
        f"Decisions logged: {total_dec:,}. Theorem 1 applies (anchor, per-knob and joint choices "
        f"feasible, both remainders defined): {total_app:,}. Violations of "
        f"`0 <= loss <= R(x^D) - R(x^J) <= index`: **{total_viol}**.",
        "",
        "## Per regime",
        "",
        "Index and loss are in the planner's own objective units for one decision (horizon-mean "
        "score with every other tenant at its plan). Disagree: x^D differs from x^J; costly: it "
        "differs and loses more than 1e-9. Per-knob infeasible: the per-knob combination breaks a "
        "budget or cap (constraint coupling, outside the bound). Run gain: decoupled minus joint arm "
        "on the campaign's registered objective (J, or J_L for LEVEL2), replication 0, mean over "
        "design points with a 95% bootstrap interval; positive = joint better.",
        "",
        "| regime | runs | mean index | median | p90 | disagree | costly | one-step loss | per-knob infeasible | run gain | 95% CI |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    reg_rows = []
    for regime in REGIMES:
        s = df[df.regime == regime]
        lo, hi = boot_ci(s.run_gain.to_numpy(), rng)
        reg_rows.append((regime, s.index_mean.mean(), s.run_gain.mean(), s.disagree_with_loss_share.mean()))
        L.append(f"| {regime} | {len(s)} | {s.index_mean.mean():.4f} | {s.index_median.mean():.4f} | "
                 f"{s.index_p90.mean():.4f} | {s.disagree_share.mean():.2%} | "
                 f"{s.disagree_with_loss_share.mean():.2%} | {s.loss_mean.mean():.5f} | "
                 f"{s.per_knob_infeasible_share.mean():.2%} | {s.run_gain.mean():+.4f} | [{lo:+.4f}, {hi:+.4f}] |")
    del reg_rows
    # Candidate predictors of the run gain (which to register is a WP4 decision).
    preds = {"index_mean": "worst-case index (max R - min R)",
             "quantile_index_mean": "quantile index (5% tails trimmed)",
             "loss_mean": "realised one-step loss of per-knob search",
             "disagree_with_loss_share": "share of costly disagreements"}
    reg = df.groupby("regime", sort=False)[list(preds) + ["run_gain"]].mean()
    width = reg[reg.index.str.startswith("width")]
    L += [
        "",
        "## Which quantity tracks the measured coordination gain? (exploratory)",
        "",
        "Rank correlation (Spearman) of each candidate with the run gain: across regime means, and "
        "within a regime across its design points. The theorem bounds the one-step loss by the index; "
        "whether either predicts a whole run's gain is the empirical question H2 will register.",
        "",
        "| candidate | 8 regimes | 6 WIDTH regimes | within pub/wide | within coupled/wide | within pub/narrow | within jointness | span across regimes |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for col, label in preds.items():
        within = []
        for regime in ("width/published/wide", "width/coupled/wide", "width/published/narrow", "jointness/main"):
            s = df[df.regime == regime]
            within.append(f"{spearman(s[col].to_numpy(), s.run_gain.to_numpy()):+.2f}")
        lo = reg[col].min()
        span = f"{reg[col].max() / lo:.0f}x" if lo > 0 else "n/a"
        L.append(f"| {label} | {spearman(reg[col].to_numpy(), reg.run_gain.to_numpy()):+.2f} | "
                 f"{spearman(width[col].to_numpy(), width.run_gain.to_numpy()):+.2f} | "
                 + " | ".join(within) + f" | {span} |")
    L += [
        "",
        "Gate G1(b) asks the index to vary at least tenfold across the regimes the confirmatory "
        "campaign will use; the last column is the span over these eight.",
        "",
        "Caveats: index and loss are one decision of the planner's score, the run gain a whole run's "
        "registered objective; the WIDTH regimes are the ones whose result motivated the index; "
        "replication 0 only; eight regimes give a coarse rank correlation.",
        "",
    ]
    return "\n".join(L)


def main() -> int:
    import argparse
    import os

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workers", type=int, default=max(1, min(6, (os.cpu_count() or 2) - 2)))
    args = ap.parse_args()
    todo = jobs()
    print(f"{len(todo)} runs on {args.workers} workers", flush=True)
    started = time.time()
    with Pool(args.workers) as pool:
        rows = pool.map(run_one, todo, chunksize=1)
    runs = pd.DataFrame(rows)
    (HERE / "data").mkdir(exist_ok=True)
    runs.to_csv(HERE / "data" / "index_pilot_runs.csv.gz", index=False)
    n_int, worst = integrity(runs)
    if worst > 1e-12:
        print(f"FAIL: re-run metrics differ from the committed export by {worst:.3g}; no report written")
        return 1
    (HERE / "INDEX_PILOT.md").write_text(report(runs, n_int, worst), encoding="utf-8")
    print(f"done in {time.time() - started:.0f}s; theorem violations: {runs.theorem_violations.sum()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
