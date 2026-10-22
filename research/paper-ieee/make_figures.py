"""IEEE-size renderings of fig22 (tracks A and B) and fig23 for the
conference paper -> figures/fig_chain.pdf, figures/fig_width.pdf.

Every plotted number comes from the gated figure scripts' own functions
(`fig_adjudication_chain.frames/rows`, `fig_width.summary`), which ride along
the frozen PREREG scorers. This file only lays those numbers out at IEEE
column widths with readable type, and refuses to save if any number differs
from the committed figure sheets (FIGURE_ADJUDICATION_CHAIN.md,
FIGURE_WIDTH.md). It adds no reading.

    python make_figures.py        (from research/paper-ieee/)
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ANALYSIS = HERE.parent / "analysis"
OUT = HERE / "figures"
sys.path.insert(0, str(ANALYSIS))
import figures as F  # noqa: E402  (shared palette and axis style)


def _mod(name: str):
    spec = importlib.util.spec_from_file_location(f"paper_{name}", ANALYSIS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


chain = _mod("fig_adjudication_chain")
width = _mod("fig_width")

F.plt.rcParams.update({"axes.titlesize": 8, "axes.labelsize": 7.5, "xtick.labelsize": 7,
                       "ytick.labelsize": 7, "legend.fontsize": 7})

# Short IEEE labels for the gated chain rows, keyed by (frame, treatment, comparator).
LABELS = {
    ("headline", "jcac", "hpa"): ("As first published", "pre-audit matrix"),
    ("eviction", "jcac", "hpa_fair"): ("No LRU charge, 512 MB cache", "EVICTION_PARITY"),
    ("fair_j", "jcac_converged", "hpa_fair"): ("Fresh shared seeds, converged solver", "FJ-H1"),
    ("retuned", "jcac_converged", "hpa_fair_retuned"): ("HPA re-tuned per cluster size", "RT-H1"),
    ("jointness", "jcac_converged", "hpa_fair_capped"): ("HPA held to the cluster caps", "JN-B4"),
    ("heldout", "jcac_converged", "hpa_fair_capped"): ("Unseen, non-periodic demand", "HO-B4"),
    ("actuation", "jcac_converged", "hpa_fair_capped"): ("Regime: replicas one interval late", "AC-H3"),
    ("fair_j", "jcac_converged", "jcac_nojoint_v2_tuned"): ("Registered stack, latency-ranked tiers", "FJ-H3"),
    ("retuned", "jcac_converged", "jcac_nojoint_v2_retuned"): ("Same stack, re-tuned per size", "RT-H3"),
    ("jointness", "jcac_converged", "layered_small_tuned"): ("Stack with tier pinned to cheapest", "JN-B1"),
    ("quality", "jcac_quality", "layered_quality_capped"): ("Quality priced, quality-aware router", "QL-B1"),
    ("heldout", "jcac_converged", "layered_small_tuned"): ("Unseen, non-periodic demand", "HO-B1"),
}
PANELS = (("A", "(a) Against reactive replica control (HPA)"),
          ("B", "(b) Against the layered per-knob stack"))


def _sheet_rows(name: str) -> list[list[str]]:
    text = (ANALYSIS / name).read_text(encoding="utf-8")
    return [[c.strip() for c in line.strip().strip("|").split("|")]
            for line in text.splitlines() if line.startswith("| ") and not line.startswith("| ---")]


def check_chain(rs: list[dict]) -> None:
    # (step label, source, treatment, comparator) is unique; the six WIDTH rows
    # share their source and arms and differ only in the label.
    rows = _sheet_rows("FIGURE_ADJUDICATION_CHAIN.md")[1:]
    sheet = {(r[1], r[2], r[3].strip("`"), r[4].strip("`")): r for r in rows}
    assert len(sheet) == len(rows) == len(rs), (len(sheet), len(rows), len(rs))
    for r in rs:
        s = sheet[(r["label"], r["source"], r["a"], r["b"])]
        got = (f"{r['pct']:+.1f}%", f"[{r['lo']:+.1f}%, {r['hi']:+.1f}%]")
        assert got == (s[7], s[8]), (r["source"], got, s[7:9])


def check_width(rows: list[dict]) -> None:
    sheet = {(r[0], r[1]): r for r in _sheet_rows("FIGURE_WIDTH.md")[1:] if len(r) == 7}
    for r in rows:
        s = sheet[(r["plant"], r["lattice"])]
        got = (f"{r['g']:+.4f}", f"{r['pct']:+.2f}%", f"[{r['lo']:+.2f}%, {r['hi']:+.2f}%]")
        assert got == (s[3], s[4], s[5]), (r["plant"], r["lattice"], got, s[3:6])


def draw_chain(rs: list[dict]):
    rows_by = {t: [r for r in rs if r["track"] == t and (r["frame"], r["a"], r["b"]) in LABELS]
               for t, _ in PANELS}
    # Stacked, full text width: the step labels are long, and side-by-side
    # panels left either the labels or the intervals unreadably small.
    fig, axes = F.plt.subplots(2, 1, figsize=(7.16, 3.05),
                               gridspec_kw={"height_ratios": [len(rows_by["A"]), len(rows_by["B"])],
                                            "hspace": 0.95})
    fig.subplots_adjust(left=0.33, right=0.985)
    for ax, (t, title) in zip(axes, PANELS):
        sub = rows_by[t]
        color = chain.TRACKS[t][1]
        ys = list(range(len(sub)))[::-1]
        for y, r in zip(ys, sub):
            hollow = r["regime"]
            ax.errorbar(r["pct"], y, xerr=[[r["pct"] - r["lo"]], [r["hi"] - r["pct"]]], fmt="o",
                        color=F.MUTED if hollow else color, mfc="white" if hollow else color,
                        capsize=2, lw=1.0, ms=3.6, mew=1.0)
        ax.axvline(0, color=F.INK2, lw=0.7, ls="--")
        ax.set_yticks(ys, [f"{LABELS[(r['frame'], r['a'], r['b'])][0]}  "
                           f"[{LABELS[(r['frame'], r['a'], r['b'])][1]}]" for r in sub])
        ax.set_ylim(-0.6, len(sub) - 0.4)
        ax.set_title(title, loc="left", fontsize=8)
        ax.set_xlabel(r"$\Delta J$, % of comparator's $J$  (< 0: joint better)")
        F._style(ax, xgrid=True)
    return fig


def draw_width(rows: list[dict]):
    fig, ax = F.plt.subplots(figsize=(3.45, 2.05))
    order = width.ORDER
    xs = {p: i for i, p in enumerate(order)}
    names = {"live": "live-fitted\n(AI off replicas)", "published": "published",
             "coupled": "coupled\n(AI 3× on replicas)"}
    for lattice, (_, color, marker) in width.LATTICE.items():
        sub = [r for r in rows if r["lattice"] == lattice]
        off = -0.13 if lattice == "narrow" else 0.13
        label = "rate-limited: ±2 replicas, 1 cache level" if lattice == "narrow" \
            else "wide: ±8 replicas, any cache level"
        ax.errorbar([xs[r["plant"]] + off for r in sub], [r["pct"] for r in sub],
                    yerr=[[r["pct"] - r["lo"] for r in sub], [r["hi"] - r["pct"] for r in sub]],
                    fmt=marker, color=color, capsize=2, lw=1.0, ms=3.8, label=label)
    ax.axhline(0, color=F.INK2, lw=0.7, ls="--")
    ax.set_xticks(range(len(order)), [names[p] for p in order])
    ax.set_xlim(-0.5, len(order) - 0.5)
    ax.set_ylim(-0.4, 6.6)                     # headroom so the legend clears every interval
    ax.set_ylabel("coordination gain $G$\n% of decoupled arm's $J$")
    ax.legend(loc="upper left", handletextpad=0.3, borderaxespad=0.2)
    F._style(ax)
    return fig


def main() -> int:
    OUT.mkdir(exist_ok=True)
    rs = chain.rows(chain.frames())
    check_chain(rs)
    wrows = width.summary({p: width.aw.prepare(width.aw.load(p)) for p in width.aw.PLANTS})
    check_width(wrows)
    plotted = {"chain": [{k: r[k] for k in ("track", "source", "a", "b", "n", "dJ", "pct", "lo", "hi", "regime")}
                         for r in rs if (r["frame"], r["a"], r["b"]) in LABELS],
               "width": wrows}
    for name, fig in (("fig_chain", draw_chain(rs)), ("fig_width", draw_width(wrows))):
        fig.savefig(OUT / f"{name}.pdf", bbox_inches="tight", pad_inches=0.02)
        F.plt.close(fig)
        print(f"wrote figures/{name}.pdf")
    (OUT / "plotted_numbers.json").write_text(json.dumps(plotted, indent=1, sort_keys=True) + "\n",
                                              encoding="utf-8")
    print("every plotted number matches the committed figure sheets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
