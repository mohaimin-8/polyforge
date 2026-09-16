"""fig21 -- the B1'' damped controller: the replica trajectory the dwell was
meant to calm, the churn it did not halve, and the cost it did not hold
-> FIGURE_WAVE4_DWELL.md.

A ride-along for the frozen scorer, exactly as fig20 is for B1': every number
drawn here is computed by `analysis_wave4_dwell.py` (its load, trajectory
recovery, churn count, window pairing, bootstrap, seed), so the figure cannot
disagree with RESULTS_WAVE4_DWELL.md. This script adds no reading; it draws
the registered ones. The figure-content fingerprint (figure_content.py) is
what scripts/reproduce.py gates, and the sheet it writes carries the caption
and the plotted numbers as text.

    python fig_wave4_dwell.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import figures as F  # noqa: E402
from stats import record_path  # noqa: E402

_spec = importlib.util.spec_from_file_location("ad", HERE / "analysis_wave4_dwell.py")
ad = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ad)

# Colour follows the arm across figures (figures.py's palette rule): the
# calibrated and tier-only arms keep the slots fig20 gave them; the dwell arm
# is new to the figure set and takes the one validated slot fig20 left free.
ARM_COLORS = {
    "jcac-calibrated-dwell": "#e34948",   # slot 6 red
    "jcac-calibrated": "#1baf7a",         # slot 2 aqua (as fig20)
    "tier-only": "#4a3aa7",               # slot 5 violet (as fig20)
}
ARM_LABELS = {
    "jcac-calibrated-dwell": "PolyForge (calibrated + dwell)",
    "jcac-calibrated": "PolyForge (calibrated)",
    "tier-only": "tier-only",
}
SHORT_LABELS = {"jcac-calibrated": "calibrated", "tier-only": "tier-only"}
CELL_LABELS = {"ai_cacheable": "ai_cacheable", "tier_mixed": "tier_mixed",
               "crud_bursty": "crud_bursty", "joint_stress": "joint_stress\n(primary)"}
REP_STYLE = {0: "-", 1: "--"}  # rep 0 solid, rep 1 dashed; further reps dotted


def trajectories(reps: list[int]) -> dict[str, dict[str, dict[int, list[int]]]]:
    """cell -> arm -> rep -> replica count per 10-s sample inside the load window."""
    out: dict[str, dict[str, dict[int, list[int]]]] = {}
    for cell in ad.CELLS:
        out[cell] = {}
        for arm in (ad.TREATMENT, ad.REFERENCE):
            out[cell][arm] = {}
            for rep in reps:
                t = ad.trajectory(arm, cell, rep)
                if t:
                    out[cell][arm][rep] = t
    return out


def churn_rows(reps: list[int]) -> list[dict]:
    """The WL-H6 rows: changes per window for both controllers in every AI
    cell, and the registered bound (CHURN_RATIO x the calibrated arm's)."""
    rows = []
    for cell in ad.AI_CELLS:
        t, r = ad.churn(ad.TREATMENT, cell, reps), ad.churn(ad.REFERENCE, cell, reps)
        bound = (ad.CHURN_RATIO * r["changes"]) if r["changes"] is not None else None
        ok = (t["changes"] is not None and r["changes"] is not None and r["changes"] > 0
              and t["changes"] <= ad.CHURN_RATIO * r["changes"] and t["largest"] <= r["largest"])
        rows.append({"cell": cell, "dwell": t, "calibrated": r, "bound": bound, "pass": ok})
    return rows


def cost_rows(df, reps: list[int]) -> list[dict]:
    """The WL-H7 rows: dwell minus arm per window bucket in the primary cell,
    as a percentage of the arm's mean bucket cost, with the paired-bootstrap
    CI scaled by the same constant so sign and bound-crossing are exactly the
    record's. The non-inferiority bound is NONINFERIORITY of the reference
    arm's mean bucket cost, i.e. +100*NONINFERIORITY % on this axis."""
    rows = []
    for arm in (ad.REFERENCE, ad.ABLATION):
        deltas = ad.paired(ad.PRIMARY_CELL, ad.TREATMENT, arm, reps)
        ci = ad.aw.bootstrap_ci(deltas)
        arm_buckets = [b for rep in reps for b in (ad.window_costs(arm, ad.PRIMARY_CELL, rep) or [])]
        if not deltas or ci is None or not arm_buckets:
            rows.append({"arm": arm, "pct": None, "lo": None, "hi": None, "n": len(deltas), "bound": None})
            continue
        scale = 100.0 / float(np.mean(arm_buckets))
        rows.append({"arm": arm, "pct": float(np.mean(deltas)) * scale,
                     "lo": ci[0] * scale, "hi": ci[1] * scale, "n": len(deltas),
                     "bound": 100.0 * ad.NONINFERIORITY if arm == ad.REFERENCE else None})
    return rows


def draw(traj: dict, churn: list[dict], costs: list[dict]) -> tuple[object, str]:
    fig, (ax_t, ax_c, ax_d) = F.plt.subplots(
        1, 3, figsize=(7.8, 3.0), gridspec_kw={"width_ratios": [1.5, 0.95, 1.15]})

    # Panel A: the replica knob every 10 s through the primary cell's load
    # window, the damped arm against the arm it damps. Time is the x-axis
    # because the finding is a slow swing, not a jitter.
    prim = traj.get(ad.PRIMARY_CELL, {})
    for arm in (ad.REFERENCE, ad.TREATMENT):
        for rep, t in sorted(prim.get(arm, {}).items()):
            x = np.arange(len(t)) * ad.REPLICA_SAMPLE_S
            ax_t.plot(x, t, REP_STYLE.get(rep, ":"), color=ARM_COLORS[arm], lw=1.4,
                      label=ARM_LABELS[arm] if rep == min(prim[arm]) else None)
    ax_t.set_xlabel("seconds into the load window")
    ax_t.set_ylabel("replicas (joint_stress)")
    F._style(ax_t)
    fig.legend(loc="lower center", ncol=3, fontsize=7.5, bbox_to_anchor=(0.5, -0.04),
               handletextpad=0.4, columnspacing=1.2)

    # Panel B: WL-H6 -- changes per window in each AI cell. The tick is the
    # registered bound: the dwell arm passes only left of it.
    ys_c = np.arange(len(churn))[::-1] * 1.0
    for y, row in zip(ys_c, churn):
        r, t = row["calibrated"]["changes"], row["dwell"]["changes"]
        if r is not None:
            ax_c.plot(r, y, "o", color=ARM_COLORS[ad.REFERENCE], ms=6,
                      markeredgecolor="white", markeredgewidth=0.8)
        if t is not None:
            ax_c.plot(t, y, "o", color=ARM_COLORS[ad.TREATMENT], ms=6,
                      markeredgecolor="white", markeredgewidth=0.8)
        if row["bound"] is not None:
            ax_c.plot(row["bound"], y, "|", color=F.INK2, ms=11, mew=1.4)
    ax_c.set_yticks(ys_c, [CELL_LABELS[r["cell"]] for r in churn])
    ax_c.set_xlim(left=0)
    ax_c.set_xlabel("replica changes per window\n(tick: WL-H6 bound)")
    F._style(ax_c, xgrid=True)

    # Panel C: WL-H7 -- dwell minus each arm per window bucket with the paired
    # bootstrap 95% CI, as % of the arm's mean bucket cost. The tick on the
    # calibrated row is the non-inferiority bound: the CI must sit left of it.
    ys_d = np.arange(len(costs))[::-1] * 1.0
    for y, d in zip(ys_d, costs):
        c = ARM_COLORS[d["arm"]]
        if d["pct"] is None:
            ax_d.plot(0, y, "x", color=c, ms=6)
            continue
        ax_d.hlines(y, d["lo"], d["hi"], color=c, lw=1.8)
        ax_d.plot(d["pct"], y, "o", color=c, ms=6, markeredgecolor="white", markeredgewidth=0.8)
        if d["bound"] is not None:
            ax_d.plot(d["bound"], y, "|", color=F.INK2, ms=11, mew=1.4)
    ax_d.axvline(0, color=F.INK2, lw=0.8, ls="--")
    ax_d.set_yticks(ys_d, [f"vs {SHORT_LABELS[d['arm']]}" for d in costs])
    ax_d.set_xlabel("dwell − arm, joint_stress\nΔ cost/bucket (% of arm's)")
    F._style(ax_d, xgrid=True)
    fig.tight_layout(w_pad=1.2, rect=(0, 0.06, 1, 1))

    h6 = "PASS" if churn and all(r["pass"] for r in churn) else "FAIL"
    ref = next((d for d in costs if d["arm"] == ad.REFERENCE), None)
    abl = next((d for d in costs if d["arm"] == ad.ABLATION), None)
    noninf = ref is not None and ref["hi"] is not None and ref["hi"] <= ref["bound"]
    beats = abl is not None and abl["hi"] is not None and abl["hi"] < 0
    caption = (
        "B1″ on one L40S host, three arms × four cells × two reps, scored from the load "
        "window only. Left: the replica count every 10 s through the joint_stress window "
        "for the dwell-damped controller and the calibrated controller it damps (solid rep 0, "
        "dashed rep 1) — the oscillation is a slow swing, which a dwell on reversals cannot "
        "remove. Middle: WL-H6 — replica-count changes per window in the three AI cells, "
        "both controllers; the tick is the pre-registered bound, half the calibrated arm's "
        f"changes, and the dwell arm must sit left of it: {h6}. Right: WL-H7 — the dwell "
        "arm's cost per 10-s bucket minus each arm's, paired by position within the window, "
        "as a percentage of that arm's mean bucket cost, with the 95% paired-bootstrap CI "
        f"({ad.aw.N_BOOT:,} resamples, seed {ad.aw.SEED}); the tick on the calibrated row is the "
        f"+{int(100 * ad.NONINFERIORITY)}% non-inferiority bound. Non-inferior to the calibrated arm: "
        f"{'yes' if noninf else 'NOT shown'}; beats tier-only: {'yes' if beats else 'no'}."
    )
    return fig, caption


def sheet(traj: dict, churn: list[dict], costs: list[dict], caption: str) -> str:
    L = ["# fig21 — the B1″ damped controller (figure sheet)", "",
         "Generated by `fig_wave4_dwell.py` from the same inputs and functions as "
         "`RESULTS_WAVE4_DWELL.md` (`analysis_wave4_dwell.py`); this sheet is the figure's "
         "caption and the numbers it plots, so the figure can be checked without rendering it. "
         "Not a record: it adds no reading.", "",
         "![fig21](../../eval/results/figures/fig21_dwell_plane.png)", "",
         f"**Caption.** {caption}", "",
         "## Panel A — replica trajectories (every cell, both controllers)", "",
         "Replica count per 10-s sample inside the load window, recovered from `cost_infra_usd` "
         "exactly as the record's churn is; only the primary cell is drawn, every cell is listed.", "",
         "| cell | arm | rep | samples | min | max | changes | largest | trajectory |",
         "|---|---|---|---|---|---|---|---|---|"]
    for cell in ad.CELLS:
        for arm in (ad.TREATMENT, ad.REFERENCE):
            for rep, t in sorted(traj.get(cell, {}).get(arm, {}).items()):
                deltas = [b - a for a, b in zip(t, t[1:]) if b != a]
                L.append(f"| {cell} | {arm} | {rep} | {len(t)} | {min(t)} | {max(t)} | {len(deltas)} | "
                         f"{max((abs(d) for d in deltas), default=0)} | {' '.join(str(v) for v in t)} |")
    L += ["", "## Panel B — WL-H6 replica churn (AI cells)", "",
          "| cell | dwell changes | dwell largest | calibrated changes | calibrated largest | bound (½ calibrated) | pass |",
          "|---|---|---|---|---|---|---|"]
    for r in churn:
        t, c = r["dwell"], r["calibrated"]
        fmt = lambda v: "n/a" if v is None else (f"{v:.1f}" if isinstance(v, float) else str(v))  # noqa: E731
        L.append(f"| {r['cell']} | {fmt(t['changes'])} | {fmt(t['largest'])} | {fmt(c['changes'])} | "
                 f"{fmt(c['largest'])} | {fmt(r['bound'])} | {'yes' if r['pass'] else 'no'} |")
    L += ["", "## Panel C — joint_stress, dwell − arm per window bucket", "",
          "| vs arm | Δ % of arm's mean bucket cost | 95% CI | pairs | bound |", "|---|---|---|---|---|"]
    for d in costs:
        bound = "n/a" if d["bound"] is None else f"+{d['bound']:.0f}%"
        if d["pct"] is None:
            L.append(f"| {d['arm']} | n/a | n/a | {d['n']} | {bound} |")
        else:
            L.append(f"| {d['arm']} | {d['pct']:+.2f}% | [{d['lo']:+.2f}%, {d['hi']:+.2f}%] | {d['n']} | {bound} |")
    L.append("")
    return "\n".join(L) + "\n"


def main() -> int:
    df = ad.load()
    reps = sorted(int(r) for r in df.rep.unique())
    traj = trajectories(reps)
    churn = churn_rows(reps)
    costs = cost_rows(df, reps)
    fig, caption = draw(traj, churn, costs)
    F.save(fig, "fig21_dwell_plane", caption)
    out = record_path("FIGURE_WAVE4_DWELL.md")
    out.write_text(sheet(traj, churn, costs, caption), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
