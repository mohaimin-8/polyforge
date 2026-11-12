"""Headroom pilot (programme 2, WP1): reference points for LEVEL2's main
matrix, and every registered arm placed between them. EXPLORATORY.

For each of the 300 worlds of `matrix_level2` (60 design points x 5
replications, shared seeds, so every arm of a world saw the same demand):
LB0, LB1, LB2, UB and the hindsight-best static plan (clairvoyant.bounds)
on LEVEL's objective J_L, beside each arm's J_L from the committed export.

    python headroom_pilot.py      # writes data/headroom_worlds.csv.gz and HEADROOM_PILOT.md
"""

from __future__ import annotations

import sys
import time
from multiprocessing import Pool
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (HERE, REPO / "research" / "jcac_sim", REPO / "eval"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from clairvoyant import bounds, plan_objective, world_for_run  # noqa: E402
from harness import config as hconfig  # noqa: E402
from index_pilot import objective  # noqa: E402

EXPERIMENT = "matrix_level2"
PRICE_OUT = 1e-4  # LEVEL's outage price (the small tier's)
KEYS = ["workload", "tenant_mix", "cluster_size", "rep"]
# Obey the planner's move limits, except when the infeasible-lattice fallback
# jumps straight to the floor (counted separately below).
JOINT = ["jcac_level_ratchet", "jcac_level_ratchet_decoupled"]


def jobs() -> list:
    """One run spec per world (shared seeds: every arm of a world saw the
    same demand, so any arm's spec builds it)."""
    spec = hconfig.load(REPO / "eval" / "experiments" / f"{EXPERIMENT}.yaml")
    return [r for r in hconfig.expand(spec) if r.system == spec.systems[0]]


def solve(run) -> dict:
    """The world's bounds, plus the plans a planner with a perfect forecast
    of the demand CURVE (but not of the Poisson noise around it) would make:
    planned on the noise-free world, scored on the realised one."""
    t0 = time.time()
    world = world_for_run(run)
    b = bounds(world, PRICE_OUT)
    curve = bounds(world_for_run(run, jitter=False), PRICE_OUT, iterations=60)
    return {"workload": run.workload, "tenant_mix": run.tenant_mix, "cluster_size": run.cluster_size,
            "rep": run.rep, "seed": run.seed, "lb0": b.lb0, "lb1": b.lb1, "lb2": b.lb2, "ub": b.ub,
            "ub_nofair": b.ub_nofair, "static": b.static, "iterations": b.iterations,
            "ub_curve": plan_objective(world, curve.ub_plan, PRICE_OUT),
            "static_curve": plan_objective(world, curve.static_plan, PRICE_OUT),
            "wall_s": time.time() - t0}


def arms() -> pd.DataFrame:
    ex = pd.read_csv(REPO / "eval" / "results" / f"metrics_{EXPERIMENT}.csv.gz", float_precision="round_trip")
    return ex.assign(J_L=objective(ex, "J_L"))[["system"] + KEYS + ["J_L", "plan_fallback_share"]]


def regret(arm: pd.Series, lb: pd.Series, ref: pd.Series) -> float:
    """Regret over a cut as a ratio of means: (mean arm - mean LB2) /
    (mean static - mean LB2). Per-world ratios are unstable where the static
    plan is almost optimal (steady CRUD), so they are never averaged."""
    den = ref.mean() - lb.mean()
    return (arm.mean() - lb.mean()) / den if den > 0 else float("nan")


def _checks(df: pd.DataFrame, w: pd.DataFrame) -> list[str]:
    order = (w.lb0 <= w.lb1 + 1e-12) & (w.lb1 <= w.lb2 + 1e-12) & (w.lb2 <= w.ub + 1e-12)
    joint = df[df.system.isin(JOINT)]
    return [
        "## Checks",
        "",
        f"- Worlds: {len(w)}; ordering LB0 <= LB1 <= LB2 <= UB holds in {int(order.sum())}.",
        f"- Arm runs below LB0 (impossible unless the arrays are wrong): {int(df.below_lb0.sum())} of {len(df)}.",
        f"- Joint-controller runs below LB1: {int(joint.below_lb1.sum())} of {len(joint)}; of those, runs "
        f"that never used the move-breaking fallback (where it would be impossible): "
        f"{int(joint[joint.plan_fallback_share.fillna(0) == 0].below_lb1.sum())}.",
        f"- Arm runs below UB (a registered arm did better than the best plan found): "
        f"{int(df.beats_ub.sum())} of {len(df)}.",
        f"- Bound gap (UB - LB2) / (static - LB2): median {w.gap.median():.1%}, worlds under 10%: "
        f"{(w.gap < 0.10).mean():.0%} (the plan asks for at least 80%). Without the fairness term in UB "
        f"(the term LB2 cannot see): median {w.gap_nofair.median():.1%}, under 10%: "
        f"{(w.gap_nofair < 0.10).mean():.0%}.",
        "",
    ]


def _headroom(w: pd.DataFrame) -> list[str]:
    L = ["## Headroom", "",
         "| cut | worlds | static J_L | LB2 | UB | headroom (static - LB2) / static | (static - UB) / static |",
         "|---|---:|---:|---:|---:|---:|---:|"]
    cuts = [("all", w)] + [(f"size={s}", w[w.cluster_size == s]) for s in ("small", "medium", "large")]
    cuts += [(f"workload={x}", w[w.workload == x]) for x in sorted(w.workload.unique())]
    for name, s in cuts:
        st, lb, ub = s.static.mean(), s.lb2.mean(), s.ub.mean()
        L.append(f"| {name} | {len(s)} | {st:.4f} | {lb:.4f} | {ub:.4f} | {(st - lb) / st:.1%} | {(st - ub) / st:.1%} |")
    return L + [""]


def _arms(df: pd.DataFrame) -> list[str]:
    L = ["## Every registered arm between the bounds", "",
         "| arm | mean J_L | regret | regret: small / medium / large | beats hindsight static | beats UB |",
         "|---|---:|---:|---|---:|---:|"]
    for arm, s in df.groupby("system"):
        by = [regret(*(s[s.cluster_size == z][c] for c in ("J_L", "lb2", "static")))
              for z in ("small", "medium", "large")]
        L.append(f"| `{arm}` | {s.J_L.mean():.4f} | {regret(s.J_L, s.lb2, s.static):.2f} | "
                 + " / ".join(f"{v:.2f}" for v in by)
                 + f" | {s.beats_static.mean():.0%} | {s.beats_ub.mean():.0%} |")
    classes = sorted(df.workload.unique())
    L += ["", "## Regret by workload class", "", "| arm | " + " | ".join(classes) + " |",
          "|---|" + "---:|" * len(classes)]
    for arm, s in df.groupby("system"):
        cells = [regret(*(s[s.workload == x][c] for c in ("J_L", "lb2", "static"))) for x in classes]
        L.append(f"| `{arm}` | " + " | ".join(f"{v:.2f}" for v in cells) + " |")
    return L + [""]


def _decomposition(df: pd.DataFrame, w: pd.DataFrame) -> list[str]:
    joint = df[df.system == "jcac_level_ratchet"].J_L.mean()
    decoupled = df[df.system == "jcac_level_ratchet_decoupled"].J_L.mean()
    best_layer = df[~df.system.isin(JOINT)].groupby("system").J_L.mean().min()
    ub_best = w[["ub", "ub_curve"]].min(axis=1)  # both plans are feasible in the realised world
    rows = [
        ("decoupled search (same planner, per-knob)", decoupled),
        ("best per-layer arm (by its mean)", best_layer),
        ("static chosen on the noise-free demand curve, run on realised demand", w.static_curve.mean()),
        ("hindsight static (chosen on realised demand)", w.static.mean()),
        ("plan made on the noise-free demand curve, run on realised demand", w.ub_curve.mean()),
        ("clairvoyant plan found (knows the noise too; best of both plans)", ub_best.mean()),
        ("lower bound (LB2)", w.lb2.mean()),
    ]
    L = ["## The joint controller's distance to the references (means over the worlds)", "",
         "| reference | J_L | minus joint search |", "|---|---:|---:|",
         f"| joint search | {joint:.4f} | |"]
    L += [f"| {name} | {value:.4f} | {value - joint:+.4f} |" for name, value in rows]
    L += ["",
          f"The plan made on the noise-free curve beats the heuristic's plan for the realised world in "
          f"{(w.ub_curve < w.ub - 1e-12).mean():.0%} of worlds; the best of the two is a valid upper "
          "bound on the capped clairvoyant optimum. The distance from joint search to the curve plan is "
          "what a perfect forecast of the demand curve could buy; the further distance to the "
          "clairvoyant plan is the price of the Poisson noise.", ""]
    return L


def report(worlds: pd.DataFrame) -> str:
    df = arms().merge(worlds, on=KEYS)
    w = worlds.assign(
        gap=(worlds.ub - worlds.lb2) / (worlds.static - worlds.lb2),
        gap_nofair=(worlds.ub_nofair - worlds.lb2) / (worlds.static - worlds.lb2),
    )
    df = df.assign(beats_ub=df.J_L < df.ub - 1e-12, below_lb0=df.J_L < df.lb0 - 1e-12,
                   below_lb1=df.J_L < df.lb1 - 1e-12, beats_static=df.J_L < df.static - 1e-12)
    L = [
        "# Reference points for LEVEL2's main matrix (pilot, exploratory)",
        "",
        "Generated by `research/theory/headroom_pilot.py`. Programme 2, WP1. Objective: LEVEL's J_L "
        "(every refused request charged the small tier's price). Each world is one design point and "
        "replication of `matrix_level2`; the arms' values are their registered runs, which saw the "
        "same demand (shared seeds).",
        "",
        "- LB0: the cheapest state per tenant and step, no move limits, no caps (below every controller).",
        "- LB1: the best path per tenant under the planner's move limits (+-2 replicas, +-1 cache level), "
        "no caps.",
        "- LB2: LB1 with the cluster caps dualised (Lagrangian, subgradient ascent). The lower bounds omit "
        "the fairness term.",
        "- UB: the best capped-feasible plan found (Lagrangian heuristic plus block-coordinate improvement) "
        "that starts within one move of the simulator's initial state (2 replicas, 128 MB), scored with the "
        "full objective. The capped clairvoyant optimum from that start lies in [LB2, UB].",
        "- Static: the best fixed state per tenant inside an equal share of the caps, chosen with hindsight "
        "and held from the first step (as the simulator's static arm is), full objective.",
        "- Regret of an arm over a cut: (mean arm - mean LB2) / (mean static - mean LB2); 0 is the lower "
        "bound, 1 the hindsight-static plan, above 1 worse than it.",
        "",
        "Both references are clairvoyant: they know each world's realised demand, Poisson noise included. "
        "They bound what any controller could do; no deployable controller attains them.",
        "",
    ]
    L += _checks(df, w) + _headroom(w) + _arms(df) + _decomposition(df, w)
    L += ["Caveats: one plant (the published one) and one objective; the static reference is an equal "
          "share, not the best unequal fixed allocation; the clairvoyant references know the realised "
          "Poisson noise, so part of every gap to them cannot be forecast.", ""]
    return "\n".join(L)


def main() -> int:
    import argparse
    import os

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workers", type=int, default=max(1, min(6, (os.cpu_count() or 2) - 2)))
    args = ap.parse_args()
    todo = jobs()
    print(f"{len(todo)} worlds on {args.workers} workers", flush=True)
    t0 = time.time()
    with Pool(args.workers) as pool:
        rows = pool.map(solve, todo, chunksize=1)
    worlds = pd.DataFrame(rows)
    (HERE / "data").mkdir(exist_ok=True)
    worlds.to_csv(HERE / "data" / "headroom_worlds.csv.gz", index=False)
    (HERE / "HEADROOM_PILOT.md").write_text(report(worlds), encoding="utf-8")
    print(f"done in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
