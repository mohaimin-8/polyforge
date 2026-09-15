"""fig20 -- the B1' live plane: cost at iso-fairness for every arm in every
cell, and the joint_stress paired-bootstrap deltas -> FIGURE_WAVE4_CALIBRATED.md.

A ride-along for the frozen scorer: every number drawn here is computed by
`analysis_wave4_calibrated.py` (its load, means, window pairing, bootstrap,
seed), so the figure cannot disagree with RESULTS_WAVE4_CALIBRATED.md. This
script adds no reading; it draws the registered ones. The figure-content
fingerprint (figure_content.py) is what scripts/reproduce.py gates, and the
sheet it writes carries the caption and the plotted numbers as text.

    python fig_wave4_calibrated.py
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

_spec = importlib.util.spec_from_file_location("aw", HERE / "analysis_wave4_calibrated.py")
aw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aw)

# Colour follows the arm across both panels (figures.py's palette rule).
# jcac keeps its slot-1 blue everywhere; the four arms new to the figure set
# take the remaining validated slots.
ARM_COLORS = {
    "jcac-calibrated": "#1baf7a",   # slot 2 aqua
    "jcac": F.SYSTEM_COLORS["jcac"],
    "replica-only": "#eda100",      # slot 3 yellow
    "cache-only": "#008300",        # slot 4 green
    "tier-only": "#4a3aa7",         # slot 5 violet
}
ARM_LABELS = {
    "jcac-calibrated": "PolyForge (calibrated)",
    "jcac": "PolyForge (published)",
    "replica-only": "replica-only",
    "cache-only": "cache-only",
    "tier-only": "tier-only",
}
CELL_LABELS = {"ai_cacheable": "ai_cacheable", "tier_mixed": "tier_mixed",
               "crud_bursty": "crud_bursty", "joint_stress": "joint_stress (primary)"}


def per_cell_costs(df) -> dict[str, dict[str, tuple[float, list[float], list[int]]]]:
    """cell -> arm -> (mean cost over reps, the rep costs, the rep ids), reps in order."""
    out: dict[str, dict[str, tuple[float, list[float], list[int]]]] = {}
    for cell in aw.CELLS:
        out[cell] = {}
        for arm in aw.ARMS:
            sub = df[(df.workload == cell) & (df.system == arm)].sort_values("rep")
            if len(sub):
                out[cell][arm] = (float(sub.total_cost_usd.mean()),
                                  [float(r) for r in sub.total_cost_usd], [int(r) for r in sub.rep])
    return out


def primary_deltas(df) -> list[dict]:
    """The WL-H1' rows: calibrated minus arm per window bucket, as a
    percentage of the arm's mean bucket cost so four arms two orders of
    magnitude apart in spend share one axis. Point and CI are scaled by the
    same constant, so sign and zero-exclusion are exactly the record's."""
    rows = []
    reps = sorted(df.rep.unique())
    for arm in aw.ARMS:
        if arm == aw.TREATMENT or arm not in set(df.system.unique()):
            continue
        r = aw.compare(df, aw.PRIMARY_CELL, arm)
        arm_buckets = [b for rep in reps for b in (aw.window_costs(arm, aw.PRIMARY_CELL, int(rep)) or [])]
        if not arm_buckets or r["ci"] is None:
            rows.append({"arm": arm, "pct": None, "lo": None, "hi": None, "n": r["n_pairs"], "beats": r["beats"]})
            continue
        scale = 100.0 / float(np.mean(arm_buckets))
        deltas = aw.paired_deltas(aw.PRIMARY_CELL, aw.TREATMENT, arm, reps)
        rows.append({"arm": arm, "pct": float(np.mean(deltas)) * scale,
                     "lo": r["ci"][0] * scale, "hi": r["ci"][1] * scale,
                     "n": r["n_pairs"], "beats": r["beats"]})
    return rows


def draw(costs: dict, deltas: list[dict]) -> tuple[object, str]:
    fig, (ax_cost, ax_delta) = F.plt.subplots(
        1, 2, figsize=(7.4, 3.0), gridspec_kw={"width_ratios": [1.45, 1.0]})

    # Panel A: cost per run, every arm in every cell, log scale. Position
    # encodes (a log axis is legitimate for point marks, never for bars).
    cells = [c for c in aw.CELLS if costs.get(c)]
    ys_cell = np.arange(len(cells))[::-1] * 1.0
    n_arms = len(aw.ARMS)
    step = 0.15
    for yc, cell in zip(ys_cell, cells):
        for i, arm in enumerate(aw.ARMS):
            if arm not in costs[cell]:
                continue
            mean, reps, _ = costs[cell][arm]
            y = yc + (i - (n_arms - 1) / 2) * -step
            ax_cost.plot(reps, [y] * len(reps), "|", color=ARM_COLORS[arm], ms=7, mew=1.2)
            ax_cost.plot(mean, y, "o", color=ARM_COLORS[arm], ms=5.5,
                         markeredgecolor="white", markeredgewidth=0.8,
                         label=ARM_LABELS[arm] if yc == ys_cell[0] else None)
    ax_cost.set_yticks(ys_cell, [CELL_LABELS[c] for c in cells])
    ax_cost.set_xscale("log")
    ax_cost.set_xlabel("total cost per run (USD, log)")
    F._style(ax_cost, xgrid=True)
    # Below the panels, not inside one: where the arms fall on a log axis is
    # the finding, so no region of the plot is known empty in advance.
    fig.legend(loc="lower center", ncol=5, fontsize=7.5, bbox_to_anchor=(0.5, -0.04),
               handletextpad=0.4, columnspacing=1.2)

    # Panel B: WL-H1' -- calibrated minus each arm per window bucket, with the
    # paired bootstrap 95% CI, as % of the arm's mean bucket cost.
    arms = [d["arm"] for d in deltas]
    ys = np.arange(len(arms))[::-1] * 1.0
    for y, d in zip(ys, deltas):
        c = ARM_COLORS[d["arm"]]
        if d["pct"] is None:
            ax_delta.plot(0, y, "x", color=c, ms=6)
            continue
        ax_delta.hlines(y, d["lo"], d["hi"], color=c, lw=1.8)
        ax_delta.plot(d["pct"], y, "o", color=c, ms=6,
                      markeredgecolor="white", markeredgewidth=0.8)
    ax_delta.axvline(0, color=F.INK2, lw=0.8, ls="--")
    ax_delta.set_yticks(ys, [f"vs {ARM_LABELS[a]}" for a in arms])
    ax_delta.set_xlabel("joint_stress, calibrated − arm:\nΔ cost per bucket (% of arm's)")
    F._style(ax_delta, xgrid=True)
    fig.tight_layout(w_pad=2.0, rect=(0, 0.06, 1, 1))

    won = [d["arm"] for d in deltas if d["beats"]]
    lost = [d["arm"] for d in deltas if not d["beats"]]
    caption = (
        "B1′ on one L40S host, five arms × four cells × two reps. Left: total cost "
        "per run at iso-fairness (every arm's Jain index is within 0.01 of the "
        "calibrated arm's in every cell shown; the record lists the exceptions). "
        "Dots are the mean of two reps, ticks the reps. Right: the pre-registered primary "
        "reading — in joint_stress the calibrated joint controller's cost per 10-s bucket "
        "minus each arm's, paired by position within the load window, as a percentage of "
        "that arm's mean bucket cost, with the 95% paired-bootstrap CI (10,000 resamples, "
        "seed 20260915); a CI entirely left of zero is a win. "
        + (f"Won against {', '.join(ARM_LABELS[a] for a in won)}" if won else "Won against none")
        + (f"; not won against {', '.join(ARM_LABELS[a] for a in lost)}." if lost else ".")
    )
    return fig, caption


def sheet(costs: dict, deltas: list[dict], caption: str) -> str:
    L = ["# fig20 — the B1′ live plane (figure sheet)", "",
         "Generated by `fig_wave4_calibrated.py` from the same inputs and functions as "
         "`RESULTS_WAVE4_CALIBRATED.md` (`analysis_wave4_calibrated.py`); this sheet is the "
         "figure's caption and the numbers it plots, so the figure can be checked without "
         "rendering it. Not a record: it adds no reading.", "",
         "![fig20](../../eval/results/figures/fig20_calibrated_plane.png)", "",
         f"**Caption.** {caption}", "",
         "## Panel A — total cost per run (USD)", "",
         "`window $` is the sum of the run's window buckets (what the pairing compares); "
         "`total` is the registered run-level metric, which also carries the WL-H2 preflight's "
         "own spend and any traffic outside the window.", "",
         "| cell | arm | mean total | reps total | reps window $ |", "|---|---|---|---|---|"]
    for cell in aw.CELLS:
        for arm in aw.ARMS:
            if arm in costs.get(cell, {}):
                mean, reps, rep_ids = costs[cell][arm]
                win = [aw.window_costs(arm, cell, rep) for rep in rep_ids]
                win_s = ", ".join(f"{sum(w):.4f}" if w else "n/a" for w in win)
                L.append(f"| {cell} | {arm} | {mean:.4f} | {', '.join(f'{r:.4f}' for r in reps)} | {win_s} |")
    L += ["", "## Panel B — joint_stress, calibrated − arm per window bucket", "",
          "| vs arm | Δ % of arm's mean bucket cost | 95% CI | pairs | beats |", "|---|---|---|---|---|"]
    for d in deltas:
        if d["pct"] is None:
            L.append(f"| {d['arm']} | n/a | n/a | {d['n']} | {'yes' if d['beats'] else 'no'} |")
        else:
            L.append(f"| {d['arm']} | {d['pct']:+.2f}% | [{d['lo']:+.2f}%, {d['hi']:+.2f}%] | {d['n']} | "
                     f"{'yes' if d['beats'] else 'no'} |")
    L.append("")
    return "\n".join(L) + "\n"


def main() -> int:
    df = aw.load()
    costs = per_cell_costs(df)
    deltas = primary_deltas(df)
    fig, caption = draw(costs, deltas)
    F.save(fig, "fig20_calibrated_plane", caption)
    out = record_path("FIGURE_WAVE4_CALIBRATED.md")
    out.write_text(sheet(costs, deltas, caption), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
