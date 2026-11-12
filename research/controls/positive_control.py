"""WP22 item 2: the positive control (programme 2).

Claim under test: where an interaction is strong by construction, the index
and the gain of joint over per-knob search are both large. Three levels:

1. Unit (`unit_t3`): Proposition T3's instance with rho from 1 to 1e6. The
   index must equal rho, the loss rho - eta and the Theorem 1 bound rho, so
   loss / index = 1 - eta / rho reaches the bound as T3 promises. The same
   ridge embedded in a planner-sized box (`unit_embedded`) keeps index rho
   and loss rho - eta; when the ridge covers less than 5% of the box the
   QUANTILE index is 0 -- the trimmed index cannot see a concentrated
   interaction, the worst-case index can (a property of the definitions,
   recorded because H2 must choose between them).

2. Planner spike-in (`SpikedController`, `spike_run`): the real planner on a
   negative-control configuration (exactly additive, index 0) with a T3
   ridge of known size added to its candidate score: -eta for one replica
   step from the anchor, -eta for one cache level, +rho for both. The logged
   index must recover rho, the per-knob choice must be the ridge, the
   one-step loss must be rho - eta up to the native objective's spread, and
   the planner's own decoupled search must disagree with its joint search
   at the decisions where the ridge is present.

3. Strong regime (`regime_run`): the dose-response pilot's strongest regime
   (kappa = wu_ai_scale 2, width 16, cache radius any) on the pilot's own
   design seeds, a short run, against its weakest (kappa 0, width 1) on the
   same demand. Large means: mean index at least 10x the weak regime's and a
   positive run gain J(decoupled) - J(joint).

    python positive_control.py              # writes POSITIVE_CONTROL.md (about 2 CPU-minutes)
"""

from __future__ import annotations

import argparse
import math
import sys
import time

import controls_common as cc
from interaction_index import analyse_box, tight_instance

import pandas as pd

RIDGE_RHO, RIDGE_ETA = 1.0e4, 1.0e3
STRONG = {"kappa": 2.0, "width": 16}
WEAK = {"kappa": 0.0, "width": 1}
# Design points where the pilot (120 steps) measured its largest gains in the
# strong regime; the pilot's own seed salt, so these are the same demand.
DESIGN = (("agentic", "uniform", "large"), ("joint_stress", "uniform", "large"))
PILOT_SALT = "wp4_pilot"


# ---------------------------------------------------------------- unit level

def unit_t3(rhos=(1.0, 10.0, 1e3, 1e6), eta_share: float = 0.01) -> pd.DataFrame:
    rows = []
    for rho in rhos:
        eta = eta_share * rho
        a = analyse_box(*tight_instance(rho, eta))
        rows.append({"rho": rho, "eta": eta, "index": a.oscillation, "loss": a.loss,
                     "bound": a.bound, "loss_over_index": a.loss / a.oscillation,
                     "per_knob": a.per_knob, "joint": a.joint})
    return pd.DataFrame(rows)


def unit_embedded(shape=(5, 3, 4), rho: float = 1e3, eta: float = 10.0, anchor=(2, 1, 1)):
    """T3's ridge inside a box of the planner's shape: knob 0 one step up
    and knob 1 one step up from the anchor; every other point and knob is
    flat. Returns the analysis."""
    values = [list(range(n)) for n in shape]
    up = (anchor[0] + 1, anchor[1] + 1)

    def phi(x):
        a, b = x[0] == up[0], x[1] == up[1]
        return -eta * a - eta * b + rho * (a and b)

    return analyse_box(values, phi, tuple(anchor))


# ------------------------------------------------------------ planner level

class SpikedController(cc.IndexedWidthController):
    """The planner with a T3 ridge of known size added to its candidate
    score, relative to the decision's anchor (the admitted interval-start
    state): -eta at the replica count one step below the anchor's in the
    index's box (above it at the floor), -eta at the cache level one step
    below likewise, +rho when both. Every search (joint, decoupled) and the
    index logger read the same score. Exploratory instrument check only."""

    ridge_rho = RIDGE_RHO
    ridge_eta = RIDGE_ETA

    def _ridge_values(self, tid, base):
        from controller_index import tenant_box

        anchor = self._admitted_anchor(tid, base)
        reps, caches, _ = tenant_box(self, tid, base)  # the box the index is computed on

        def step(values, v):
            # One step DOWN when there is one: a smaller value never breaks a
            # cluster cap or the budget, so the ridge stays feasible. (A
            # ridge one step UP lets the planner chase -eta into the cache
            # cap within one cycle, after which the ridge is infeasible and,
            # correctly, outside the index.)
            if v not in values or len(values) < 2:
                return None
            i = values.index(v)
            return values[i - 1] if i > 0 else values[i + 1]

        return step(reps, anchor.replicas), step(caches, anchor.cache_mb)

    def _candidate_score(self, tid, candidate, base, others, horizons, cost_horizons, budget_per_step):
        score = super()._candidate_score(tid, candidate, base, others, horizons, cost_horizons,
                                         budget_per_step)
        if score is None:
            return None
        r_up, c_up = self._ridge_values(tid, base)
        a = r_up is not None and candidate.replicas == r_up
        b = c_up is not None and candidate.cache_mb == c_up
        return score - self.ridge_eta * a - self.ridge_eta * b + self.ridge_rho * (a and b)


def spike_run(steps: int = 15, workload: str = "crud_bursty", mix: str = "uniform",
              size: str = "small") -> dict:
    """The spiked joint planner on the crud_only negative control. Rows with
    the ridge in the box (both ridge values present) must show index rho."""
    seed = cc.design_seed("wp22_positive_spike", workload, mix, size, 0)
    runs = cc.run_specs("wp22_positive_spike", workload, mix, size, steps, seed)
    arms = cc.arm_pair()
    with cc.registered_arms(arms):
        from controller_index import execute_indexed

        result, rows = execute_indexed(runs[cc.JOINT], SpikedController)
    rows = [r for r in rows if r["cycle"] <= steps]
    present = [r for r in rows if r["index"] is not None and r["index"] > 0.5 * RIDGE_RHO]
    applies = [r for r in present if r["theorem1_applies"]]
    return {
        "decisions": len(rows), "ridge_present": len(present),
        "index_min": min((r["index"] for r in present), default=math.nan),
        "index_max": max((r["index"] for r in present), default=math.nan),
        "loss_min": min((r["loss"] for r in applies), default=math.nan),
        "loss_max": max((r["loss"] for r in applies), default=math.nan),
        "bound_min": min((r["bound"] for r in applies), default=math.nan),
        "per_knob_on_ridge": sum(r["per_knob"] != r["joint"] for r in present),
        "theorem_violations": cc.summarise_rows(rows, steps)["theorem_violations"],
        "planner_disagreement_share": result["metrics"]["coordination_disagreement_share"],
    }


# ------------------------------------------------------------- regime level

def regime_run(workload: str, mix: str, size: str, steps: int, kappa: float, width: int) -> dict:
    seed = cc.design_seed(PILOT_SALT, workload, mix, size, 0)
    runs = cc.run_specs("wp22_positive_regime", workload, mix, size, steps, seed,
                        model_form={"wu_ai_scale": kappa})
    arms = cc.arm_pair(cc.width_params(width), track=False)
    started = time.time()
    out = cc.execute_pair(runs, arms)
    jj = cc.objective_j(out["joint"]["metrics"], out["joint"]["tenants"])
    jd = cc.objective_j(out["decoupled"]["metrics"], out["decoupled"]["tenants"])
    return {"workload": workload, "tenant_mix": mix, "cluster_size": size, "steps": steps,
            "seed": seed, "kappa": kappa, "width": width, "wall_s": time.time() - started,
            "J_joint": jj, "J_decoupled": jd, "gain": jd - jj, **cc.summarise_rows(out["rows"], steps)}


def regime_passes(strong: dict, weak: dict) -> bool:
    return (strong["theorem_violations"] == 0 and strong["gain"] > 0.0
            and strong["index_mean"] >= 10.0 * weak["index_mean"])


# ------------------------------------------------------------------- report

def report(t3: pd.DataFrame, emb, spike: dict, regimes: list[tuple[dict, dict]], steps: int) -> str:
    L = ["# Positive control (WP22 item 2)", "",
         "Generated by `research/controls/positive_control.py`. Exploratory instrument check, not a "
         "registered result.", "",
         "## Unit level: T3 scaled up", "",
         "| rho | eta | index | loss | bound | loss / index |", "|---:|---:|---:|---:|---:|---:|"]
    for r in t3.itertuples():
        L.append(f"| {r.rho:g} | {r.eta:g} | {r.index:g} | {r.loss:g} | {r.bound:g} | {r.loss_over_index:.4f} |")
    L += ["", f"Embedded in a 5 x 3 x 4 box (rho {1e3:g}, eta 10): index {emb.oscillation:g}, loss "
          f"{emb.loss:g}, bound {emb.bound:g}, quantile index {emb.quantile_index:g} (the ridge covers "
          f"4 of 60 points, above the 5% tail). In a 33 x 6 x 4 box (width 16) it covers 4 of 792 "
          f"points and the quantile index is 0: see `test_quantile_index_misses_a_concentrated_ridge`.",
          "", "## Planner spike-in", "",
          f"T3 ridge (rho {RIDGE_RHO:g}, eta {RIDGE_ETA:g}) added to the real planner's score on the "
          f"crud_only negative control ({spike['decisions']} decisions):", "",
          f"- ridge in the box: {spike['ridge_present']} decisions; logged index "
          f"{spike['index_min']:.6g} to {spike['index_max']:.6g} (expected {RIDGE_RHO:g})",
          f"- one-step loss {spike['loss_min']:.6g} to {spike['loss_max']:.6g} (expected rho - eta = "
          f"{RIDGE_RHO - RIDGE_ETA:g} plus the native objective's spread); smallest bound "
          f"{spike['bound_min']:.6g}",
          f"- per-knob choice on the ridge (x^D != x^J): {spike['per_knob_on_ridge']} of "
          f"{spike['ridge_present']}; Theorem 1 violations: {spike['theorem_violations']}",
          f"- the planner's own decoupled search disagreed with its joint search at "
          f"{spike['planner_disagreement_share']:.1%} of best-response calls", "",
          f"## Strong regime against weak regime ({steps} steps, the pilot's own demand seeds)", "",
          "| design point | regime | mean index | mean one-step loss | costly disagreements "
          "| gain J(dec) - J(joint) | pass |",
          "|---|---|---:|---:|---:|---:|---|"]
    for strong, weak in regimes:
        dp = f"{strong['workload']}/{strong['tenant_mix']}/{strong['cluster_size']}"
        ok = regime_passes(strong, weak)
        for r, label in ((strong, "kappa 2, width 16"), (weak, "kappa 0, width 1")):
            verdict = ("yes" if ok else "NO") if r is strong else ""
            L.append(f"| {dp} | {label} | {r['index_mean']:.3f} | {r['loss_mean']:.5f} | "
                     f"{r['costly_share']:.2%} | {r['gain']:+.4f} | {verdict} |")
    L += ["", "Pass: Theorem 1 holds on every decision, the strong regime's mean index is at least 10x the "
          "weak regime's, and its run gain is positive. Short runs of single design points: the gain is "
          "one paired difference, not an estimate (the pilot's 120-step, 20-point means are in "
          "research/theory/DOSE_RESPONSE_PILOT.md).", ""]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--steps", type=int, default=30, help="steps of each regime run")
    ap.add_argument("--design", type=int, default=1, help="how many of the design points to run")
    args = ap.parse_args()
    started = time.time()
    t3, emb = unit_t3(), unit_embedded()
    spike = spike_run()
    regimes = []
    for w, m, s in DESIGN[:args.design]:
        regimes.append((regime_run(w, m, s, args.steps, **STRONG), regime_run(w, m, s, args.steps, **WEAK)))
    (cc.HERE / "POSITIVE_CONTROL.md").write_text(report(t3, emb, spike, regimes, args.steps), encoding="utf-8")
    cc.DATA.mkdir(exist_ok=True)
    flat = [r for pair in regimes for r in pair]
    pd.DataFrame(flat).to_csv(cc.DATA / "positive_control_regimes.csv", index=False)
    ok = all(regime_passes(s, w) for s, w in regimes) and spike["theorem_violations"] == 0
    print(f"done in {time.time() - started:.0f}s; positive control {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
