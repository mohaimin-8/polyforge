"""Why does the adversarial search's worst case have a large gain and a
small index? (WP22 follow-up; EXPLORATORY diagnosis of one regime.)

The search logs the index and the one-step loss along the JOINT planner's
trajectory, while the gain compares the two arms' whole runs. Three
explanations are possible, and this script separates them on the regime's
own confirmation seeds:

1. Trajectory: the per-knob arm drifts to states the joint run never visits,
   where per-knob search does lose. Test: log the index and the one-step
   loss along the per-knob arm's own trajectory.
2. Infeasible anchors: decisions that start from an infeasible state are
   outside Theorem 1 (the per-knob search then works from an admitted
   substitute anchor). Test: a hybrid arm that searches per knob except at
   those decisions, where it searches jointly. If the hybrid's gain vanishes,
   the gain lives in the decisions the index does not cover.
3. Neither: a dynamic effect no per-decision quantity sees.

    python diagnose_refutation.py [--cid 158] [--workers 2]
"""

from __future__ import annotations

import argparse
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import adversarial_search as adv  # noqa: E402
import controls_common as cc  # noqa: E402
import model  # noqa: E402  (research/jcac_sim, put on the path by controls_common)

ARMS = ("joint", "per_knob", "hybrid")


class HybridController(cc.IndexedWidthController):
    """Per-knob search, except joint search where the decision's anchor is
    infeasible (outside Theorem 1)."""

    switches = 0

    def _best_for_tenant_decoupled(self, tid, chosen, horizons, origin=None, cost_horizons=None):
        base = chosen[tid] if origin is None else origin[tid]
        budget = self.configs[tid].hourly_budget_usd * model.CONTROL_INTERVAL_S / 3600.0
        others = self._others(tid, chosen, horizons, cost_horizons)
        if self._candidate_score(tid, base, base, others, horizons, cost_horizons, budget) is None:
            HybridController.switches += 1
            return self._best_for_tenant(tid, chosen, horizons, origin, cost_horizons)
        return super()._best_for_tenant_decoupled(tid, chosen, horizons, origin, cost_horizons)


def _arms(width: int) -> dict:
    from harness.systems import SystemSpec

    extra = cc.width_params(width)
    base = {**cc.BASE_PARAMS, **extra}
    return {"joint": SystemSpec("jcac", params=dict(base)),
            "per_knob": SystemSpec("jcac", params={**base, "knob_search": "decoupled"}),
            "hybrid": SystemSpec("jcac", params={**base, "knob_search": "decoupled"})}


def _rows_summary(rows: list[dict], steps: int) -> dict:
    rows = [r for r in rows if r["cycle"] <= steps]
    applies = [r for r in rows if r["theorem1_applies"]]
    return {"index": float(np.mean([r["index"] for r in rows if r["index"] is not None])) if rows else np.nan,
            "loss": float(np.mean([r["loss"] for r in applies])) if applies else np.nan,
            "costly": float(np.mean([r["disagree_with_loss"] for r in rows])) if rows else np.nan,
            "anchor_infeasible": float(np.mean([not r["anchor_feasible"] for r in rows])) if rows else np.nan}


def run_point(job: tuple) -> list[dict]:
    from controller_index import execute_indexed

    theta, (workload, mix, size), steps, rep = job
    form, economy = adv.plant_of(theta)
    seed = cc.design_seed(adv.SALT, workload, mix, size, rep)
    specs = cc.run_specs("wp22_adversarial", workload, mix, size, steps, seed, economy, form)
    template = specs[cc.JOINT]
    out = []
    with cc.registered_arms(_arms(int(theta["width"]))):
        for arm in ARMS:
            import dataclasses

            run = dataclasses.replace(template, system=arm)
            HybridController.switches = 0
            cls = HybridController if arm == "hybrid" else cc.IndexedWidthController
            result, rows = execute_indexed(run, cls)
            out.append({"workload": workload, "rep": rep, "arm": arm,
                        "J": cc.objective_j(result["metrics"], result["tenants"]),
                        "hybrid_switches": HybridController.switches if arm == "hybrid" else 0,
                        **_rows_summary(rows, steps)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cid", type=int, default=158)
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()
    regimes = pd.read_csv(HERE / "data" / "adversarial_search_regimes.csv")
    row = regimes[regimes.cid == args.cid].iloc[0]
    theta = {p.name: (bool(row[p.name]) if p.kind == "bool" else row[p.name]) for p in adv.SPACE}
    search_args = pd.read_json(HERE / "data" / "adversarial_search_args.json", typ="series")
    reps = tuple(range(1, int(search_args["confirm_reps"]) + 1))
    steps = int(search_args["confirm_steps"] or search_args["steps"])
    panel = adv.panel_of(theta, int(search_args["panel"]))
    jobs = [(theta, point, steps, rep) for point in panel for rep in reps]
    with Pool(args.workers) as pool:
        rows = [r for part in pool.map(run_point, jobs, chunksize=1) for r in part]
    df = pd.DataFrame(rows)
    (HERE / "data").mkdir(exist_ok=True)
    df.to_csv(HERE / "data" / f"diagnose_{args.cid}.csv", index=False)
    wide = df.pivot_table(index=["workload", "rep"], columns="arm", values="J")
    g_dec, g_hyb = wide.per_knob - wide.joint, wide.hybrid - wide.joint
    by_arm = df.groupby("arm")[["index", "loss", "costly", "anchor_infeasible", "hybrid_switches"]].mean()
    L = [f"# Diagnosis of adversarial regime {args.cid} (WP22 follow-up; exploratory)", "",
         f"Generated by `research/controls/diagnose_refutation.py` on the regime's confirmation seeds "
         f"(reps {reps}, {steps} steps, panel of {len(panel)} design points). Gain = J(arm) - J(joint).", "",
         f"- per-knob gain: {g_dec.mean():+.4f} (se {g_dec.std(ddof=1) / np.sqrt(len(g_dec)):.4f}); "
         f"the search's confirmation reported {row.G:+.4f} on its own seeds (reps 0) and the fresh-seed value "
         "in ADVERSARIAL_SEARCH.md",
         f"- hybrid gain (joint search only where the anchor is infeasible): {g_hyb.mean():+.4f} "
         f"(se {g_hyb.std(ddof=1) / np.sqrt(len(g_hyb)):.4f})", "",
         "Along each arm's own trajectory (means over decisions):", "",
         "| arm | index | one-step loss | costly disagreements | anchor infeasible | hybrid switches per run |",
         "|---|---:|---:|---:|---:|---:|"]
    for arm in ARMS:
        r = by_arm.loc[arm]
        L.append(f"| {arm} | {r['index']:.4f} | {r['loss']:.5f} | {r['costly']:.2%} | {r['anchor_infeasible']:.2%} | "
                 f"{r['hybrid_switches']:.0f} |")
    L.append("")
    (HERE / f"DIAGNOSE_{args.cid}.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
