"""fig23 -- coordination between knobs pays only when one interval may move
far and the layers share capacity -> FIGURE_WIDTH.md.

A ride-along for the frozen PREREG_WIDTH scorer: every number drawn here is
computed by `analysis_width.py` (its load, listwise rule, gains, bootstrap),
so the figure cannot disagree with RESULTS_WIDTH.md. It adds no reading.

    python fig_width.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import figures as F  # noqa: E402
from stats import record_path  # noqa: E402

_spec = importlib.util.spec_from_file_location("aw_fig", HERE / "analysis_width.py")
aw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aw)

ORDER = ("live", "published", "coupled")
PLANT_LABELS = {"live": "live-fitted\n(AI off the replicas)", "published": "published",
                "coupled": "coupled\n(AI 3× on replicas)"}
LATTICE = {"narrow": ("rate-limited (±2 replicas, 1 cache level)", "#2a78d6", "o"),
           "wide": ("wide (±8 replicas, any cache level)", "#e34948", "s")}


def summary(frames: dict) -> list[dict]:
    """Per plant and lattice: the coordination gain G as a share of the
    decoupled arm's J (the comparator's, as in fig22) with its CI, the
    disagreement share, and the plant's WD-H5 difference J(rate-limited joint)
    - J(wide joint)."""
    out = []
    for plant in ORDER:
        f = frames[plant]
        g = aw.gains(f)
        rate = float(g.rate_limit.mean())
        for lattice, joint, dec, col in (("narrow", aw.NARROW_JOINT, aw.NARROW_DEC, "g_narrow"),
                                         ("wide", aw.WIDE_JOINT, aw.WIDE_DEC, "g_wide")):
            base = float(aw._means(f, dec).loc[g.index].mean())
            lo, hi = aw.ci(g[col].to_numpy())
            out.append({"plant": plant, "lattice": lattice, "n": len(g), "g": float(g[col].mean()),
                        "pct": 100 * float(g[col].mean()) / base, "lo": 100 * lo / base, "hi": 100 * hi / base,
                        "disagree": float(f[f.system == joint].coordination_disagreement_share.mean()),
                        "rate": rate})
    return out


def draw(rows: list[dict]):
    fig, (ax_g, ax_d) = F.plt.subplots(1, 2, figsize=(12.5, 4.6), gridspec_kw={"width_ratios": [1.6, 1]})
    xs = {p: i for i, p in enumerate(ORDER)}
    for lattice, (label, color, marker) in LATTICE.items():
        sub = [r for r in rows if r["lattice"] == lattice]
        off = -0.12 if lattice == "narrow" else 0.12
        x = [xs[r["plant"]] + off for r in sub]
        ax_g.errorbar(x, [r["pct"] for r in sub],
                      yerr=[[r["pct"] - r["lo"] for r in sub], [r["hi"] - r["pct"] for r in sub]],
                      fmt=marker, color=color, capsize=3, lw=1.4, ms=6, label=label)
        ax_d.plot(x, [100 * r["disagree"] for r in sub], marker, color=color, ms=6, ls="none", label=label)
    ax_g.axhline(0, color=F.INK2, lw=0.9, ls="--")
    ax_g.set_xticks(range(len(ORDER)), [PLANT_LABELS[p] for p in ORDER], fontsize=8)
    ax_g.set_ylabel("coordination gain G = J(decoupled) − J(joint)\n% of the decoupled arm's J", fontsize=8)
    ax_g.set_title("coordination gain", fontsize=10, loc="left")
    ax_g.legend(fontsize=7.5, frameon=False, loc="upper left")
    F._style(ax_g)
    ax_d.set_xticks(range(len(ORDER)), [PLANT_LABELS[p] for p in ORDER], fontsize=8)
    ax_d.set_ylabel("joint best responses the decoupled\nsearch would have chosen differently (%)", fontsize=8)
    ax_d.set_title("disagreement", fontsize=10, loc="left")
    F._style(ax_d)
    fig.tight_layout(w_pad=2.0)
    return fig, caption_for(rows)


def caption_for(rows: list[dict]) -> str:
    by = {(r["plant"], r["lattice"]): r for r in rows}
    trio = " / ".join
    narrow = trio(f"{by[(p, 'narrow')]['pct']:+.1f}%" for p in ORDER)
    wide = trio(f"{by[(p, 'wide')]['pct']:+.1f}%" for p in ORDER)
    rates = trio(f"{by[(p, 'narrow')]['rate']:+.3f}" for p in ORDER)
    most_narrow = max(100 * r["disagree"] for r in rows if r["lattice"] == "narrow")
    most_live = max(100 * r["disagree"] for r in rows if r["plant"] == "live")
    return (
        "Coordination between one tenant's knobs grows with how far one interval may move, and only where "
        "the layers share capacity (PREREG_WIDTH, 48 AI-carrying design points × 5 reps per plant). Left: "
        "the gain G of the joint search over the decoupled one, as a share of the decoupled arm's J, with "
        "the 95% bootstrap CI, on the published rate-limited lattice (±2 replicas, one cache level per "
        "interval) and on a wide one (±8 replicas, any cache level). "
        f"Rate-limited: {narrow}; wide: {wide} (live-fitted / published / coupled plant). Right: the share "
        "of the joint search's best responses the decoupled search would have chosen differently — at most "
        f"{most_narrow:.2f}% on the rate-limited lattice, as local separability predicts, and at most "
        f"{most_live:.2f}% at either width on the live-fitted plant, where AI work never loads the replicas. "
        f"Wide steps raise the joint arm's own J on every plant (WD-H5, J rate-limited − J wide: {rates})."
    )


def sheet(rows: list[dict], caption: str) -> str:
    L = ["# fig23 — coordination vs actuation width (figure sheet)", "",
         "Generated by `fig_width.py` from the same inputs and functions as `RESULTS_WIDTH.md` "
         "(`analysis_width.py`); this sheet is the caption and every plotted number. Not a record: "
         "it adds no reading.", "", "![fig23](../../eval/results/figures/fig23_width_mechanism.png)", "",
         f"**Caption.** {caption}", "",
         "| plant | lattice | design points | G (J) | G % of decoupled J | 95% CI % | disagreement |",
         "|---|---|---:|---:|---:|---|---:|"]
    for r in rows:
        L.append(f"| {r['plant']} | {r['lattice']} | {r['n']} | {r['g']:+.4f} | {r['pct']:+.2f}% | "
                 f"[{r['lo']:+.2f}%, {r['hi']:+.2f}%] | {r['disagree']:.2%} |")
    L += ["", "WD-H5, the joint arm's own J on the rate-limited lattice minus on the wide one "
          "(negative = rate-limited better):", "",
          "| plant | J rate-limited − J wide |", "|---|---:|"]
    L += [f"| {r['plant']} | {r['rate']:+.4f} |" for r in rows if r["lattice"] == "narrow"]
    L.append("")
    return "\n".join(L) + "\n"


def main() -> int:
    frames = {p: aw.prepare(aw.load(p)) for p in aw.PLANTS}
    rows = summary(frames)
    fig, caption = draw(rows)
    F.save(fig, "fig23_width_mechanism", caption)
    out = record_path("FIGURE_WIDTH.md")
    out.write_text(sheet(rows, caption), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
