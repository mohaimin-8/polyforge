"""fig22 -- how the joint controller's advantage vanished, one registered
adjudication at a time -> FIGURE_ADJUDICATION_CHAIN.md.

Every row is computed here from a committed run-level export through the
frozen scorer that judged it (its load, its listwise rule, its J), at the
design-point unit with the scorers' bootstrap (10,000 resamples, seed
20260926). The figure adds no reading: each row is a difference a record
already reports. The two pre-audit rows come from the exports
REANALYSIS_MATRIX re-reads.

Three tracks, each a chain of registered comparisons:
  A  against reactive replica control (HPA)
  B  against the layered stack of per-knob controllers
  C  joint search against the same controller searching the knobs one by one
ΔJ is treatment − comparator as a share of the comparator's J (J_q, priced
with answer quality, in the QUALITY rows); negative = the joint controller
is better.

    python fig_adjudication_chain.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import figures as F  # noqa: E402
from reanalysis_matrix import DESIGN, bootstrap_ci  # noqa: E402
from stats import composite_objective, record_path  # noqa: E402

RESULTS = HERE.parents[1] / "eval" / "results"
TRACKS = {
    "A": ("vs reactive replica control (HPA)", "#1baf7a"),
    "B": ("vs the layered stack", "#4a3aa7"),
    "C": ("joint vs one-knob-at-a-time search", "#2a78d6"),
}


def _mod(name: str):
    spec = importlib.util.spec_from_file_location(f"chain_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _export(name: str) -> pd.DataFrame:
    df = pd.read_csv(RESULTS / name, float_precision="round_trip")
    return df.assign(J=composite_objective(df))


def frames() -> dict[str, pd.DataFrame]:
    """Each campaign exactly as its frozen scorer prepares it."""
    fj = _mod("analysis_fair_j")
    fair_arms = [fj.TREATMENT, fj.ALSO, *fj.PRIMARY.values(), *fj.SECONDARY.values()]
    fair = fj.complete_design_points(fj.load(), fair_arms)
    out = {"headline": _export("metrics_full.csv.gz"),
           "eviction": _export("metrics_matrix_eviction_parity.csv.gz"),
           "fair_j": fair.assign(J=composite_objective(fair))}
    for key, name in (("retuned", "analysis_retuned"), ("jointness", "analysis_jointness"),
                      ("actuation", "analysis_actuation"), ("quality", "analysis_quality"),
                      ("heldout", "analysis_heldout")):
        m = _mod(name)
        out[key] = m.prepare(m.load())
    w = _mod("analysis_width")
    for plant in w.PLANTS:
        out[f"width_{plant}"] = w.prepare(w.load(plant))
    return out


# (track, campaign frame, treatment, comparator, label, record + hypothesis)
CHAIN = [
    ("A", "headline", "jcac", "hpa", "as published: LRU charge, 128 MB pin, grid-edge tuning",
     "REANALYSIS_MATRIX (RESULTS.md)"),
    ("A", "eviction", "jcac", "hpa_fair", "no LRU charge, 512 MB cache", "REANALYSIS_MATRIX (EVICTION_PARITY)"),
    ("A", "fair_j", "jcac_converged", "hpa_fair", "fresh shared seeds, converged solver", "FAIR_J FJ-H1"),
    ("A", "retuned", "jcac_converged", "hpa_fair_retuned", "comparator re-tuned per cluster size", "RETUNED RT-H1"),
    ("A", "jointness", "jcac_converged", "hpa_fair_capped", "comparator inside the cluster caps", "JOINTNESS JN-B4"),
    ("A", "heldout", "jcac_converged", "hpa_fair_capped", "unseen, non-periodic demand", "HELDOUT HO-B4"),
    ("A", "actuation", "jcac_converged", "hpa_fair_capped", "regime: replicas one interval late",
     "ACTUATION AC-H3"),
    ("B", "fair_j", "jcac_converged", "jcac_nojoint_v2_tuned", "registered stack (latency-ranked tier rule)",
     "FAIR_J FJ-H3"),
    ("B", "retuned", "jcac_converged", "jcac_nojoint_v2_retuned", "same stack, re-tuned per size", "RETUNED RT-H3"),
    ("B", "jointness", "jcac_converged", "layered_small_tuned", "stack with no tier layer", "JOINTNESS JN-B1"),
    ("B", "quality", "jcac_quality", "layered_quality_capped", "quality priced; quality-aware router",
     "QUALITY QL-B1"),
    ("B", "heldout", "jcac_converged", "layered_small_tuned", "unseen, non-periodic demand", "HELDOUT HO-B1"),
    ("C", "jointness", "jcac_converged", "jcac_decoupled", "published plant and lattice", "JOINTNESS JN-C1"),
    ("C", "quality", "jcac_quality", "jcac_quality_decoupled", "quality priced", "QUALITY QL-C1"),
    ("C", "heldout", "jcac_converged", "jcac_decoupled", "unseen, non-periodic demand", "HELDOUT HO-C1"),
    ("C", "width_live", "jcac_joint_tracked", "jcac_decoupled", "live-fitted plant, rate-limited", "WIDTH"),
    ("C", "width_live", "jcac_joint_wide_tracked", "jcac_decoupled_wide", "live-fitted plant, wide steps", "WIDTH"),
    ("C", "width_published", "jcac_joint_tracked", "jcac_decoupled", "published plant, rate-limited", "WIDTH"),
    ("C", "width_published", "jcac_joint_wide_tracked", "jcac_decoupled_wide", "published plant, wide steps",
     "WIDTH"),
    ("C", "width_coupled", "jcac_joint_tracked", "jcac_decoupled", "coupled plant, rate-limited", "WIDTH"),
    ("C", "width_coupled", "jcac_joint_wide_tracked", "jcac_decoupled_wide", "coupled plant, wide steps", "WIDTH"),
]

# Rows that are a regime the controller could run in, not a step in the
# adjudication: replicas one interval late, and the wide lattice no published
# arm uses. Drawn hollow.
REGIMES = {("actuation", "jcac_converged", "hpa_fair_capped"),
           *((f"width_{p}", "jcac_joint_wide_tracked", "jcac_decoupled_wide")
             for p in ("live", "published", "coupled"))}


def row(df: pd.DataFrame, a: str, b: str) -> dict:
    ma = df[df.system == a].groupby(DESIGN).J.mean()
    mb = df[df.system == b].groupby(DESIGN).J.mean()
    d = (ma - mb).dropna().sort_index()
    base = float(mb.loc[d.index].mean())
    lo, hi = bootstrap_ci(d.to_numpy())
    return {"n": len(d), "dJ": float(d.mean()), "pct": 100 * float(d.mean()) / base,
            "lo": 100 * lo / base, "hi": 100 * hi / base}


def rows(data: dict[str, pd.DataFrame]) -> list[dict]:
    return [{"track": t, "label": label, "source": src, "frame": f, "a": a, "b": b,
             "regime": (f, a, b) in REGIMES, **row(data[f], a, b)}
            for t, f, a, b, label, src in CHAIN]


def verdict(r: dict) -> str:
    """What the row's 95% CI says, in the caption's words."""
    if r["hi"] < 0:
        return "joint better"
    if r["lo"] > 0:
        return "comparator better"
    return "CI spans 0"


def caption_for(rs: list[dict]) -> str:
    by = {(r["frame"], r["a"], r["b"]): r for r in rs}
    steps_a = [r for r in rs if r["track"] == "A" and not r["regime"]]
    a0, a_last = steps_a[0], steps_a[-1]
    later = ("every later step's CI spans 0" if all(verdict(r) == "CI spans 0" for r in steps_a[1:])
             else "later steps: " + ", ".join(f"{r['pct']:+.1f}% ({verdict(r)})" for r in steps_a[1:]))
    ac = by[("actuation", "jcac_converged", "hpa_fair_capped")]
    b0 = next(r for r in rs if r["track"] == "B")
    jn_b1 = by[("jointness", "jcac_converged", "layered_small_tuned")]
    ho_b1 = by[("heldout", "jcac_converged", "layered_small_tuned")]
    c0 = by[("jointness", "jcac_converged", "jcac_decoupled")]
    ho_c1 = by[("heldout", "jcac_converged", "jcac_decoupled")]
    return (
        "How the joint controller's advantage vanished under registered adjudication. Each row is "
        "treatment − comparator on J at the design-point unit, as a share of the comparator's J, with the "
        "95% bootstrap CI (10,000 resamples, seed 20260926), computed from the committed export through "
        "the scorer that judged it (J_q, priced with answer quality, in the QUALITY rows); hollow rows are "
        "regimes, not steps. "
        f"A (against HPA): {a0['pct']:+.1f}% as first published; once the audited comparator defects are "
        f"removed {later}, ending at {a_last['pct']:+.1f}% on unseen demand. With replicas one interval "
        f"late: {ac['pct']:+.1f}% ({verdict(ac)}). "
        f"B (against the layered stack): {b0['pct']:+.1f}% against the registered stack and its "
        f"latency-ranked tier rule; with the tier pinned to the cheapest model instead, {jn_b1['pct']:+.1f}% "
        f"({verdict(jn_b1)}) on the published matrix and {ho_b1['pct']:+.1f}% ({verdict(ho_b1)}) on unseen "
        "demand. "
        f"C (coordination itself): {c0['pct']:+.1f}% ({verdict(c0)}) on the published plant and "
        f"{ho_c1['pct']:+.1f}% ({verdict(ho_c1)}) on unseen demand, inside the registered ±0.025 J "
        "equivalence margin both times. It grows only with wide steps where the layers share capacity "
        "(WIDTH), and wide steps raise the joint controller's own J on every plant (WD-H5)."
    )


def draw(rs: list[dict]):
    fig, axes = F.plt.subplots(1, 3, figsize=(15.5, 5.6), sharex=False)
    for ax, (t, (title, color)) in zip(axes, TRACKS.items()):
        sub = [r for r in rs if r["track"] == t]
        ys = list(range(len(sub)))[::-1]
        for y, r in zip(ys, sub):
            ax.errorbar(r["pct"], y, xerr=[[r["pct"] - r["lo"]], [r["hi"] - r["pct"]]], fmt="o",
                        color=F.MUTED if r["regime"] else color, mfc="white" if r["regime"] else color,
                        capsize=3, lw=1.4, ms=6)
        ax.axvline(0, color=F.INK2, lw=0.9, ls="--")
        ax.set_yticks(ys, [f"{r['label']}\n{r['source']}" for r in sub], fontsize=7.5)
        ax.set_title(title, fontsize=10, loc="left")
        ax.set_xlabel("ΔJ, % of comparator's J (< 0: joint better)", fontsize=8)
        F._style(ax, xgrid=True)
    fig.tight_layout(w_pad=1.5)
    return fig, caption_for(rs)


def sheet(rs: list[dict], caption: str) -> str:
    L = ["# fig22 — how the advantage vanished (figure sheet)", "",
         "Generated by `fig_adjudication_chain.py` from the committed exports, through each campaign's "
         "frozen scorer; this sheet is the caption and every plotted number. Not a record: it adds no "
         "reading.", "", "![fig22](../../eval/results/figures/fig22_adjudication_chain.png)", "",
         f"**Caption.** {caption}", "",
         "| track | step | source | treatment | comparator | design points | ΔJ | ΔJ % | 95% CI % |",
         "|---|---|---|---|---|---:|---:|---:|---|"]
    for r in rs:
        L.append(f"| {r['track']} | {r['label']} | {r['source']} | `{r['a']}` | `{r['b']}` | {r['n']} | "
                 f"{r['dJ']:+.4f} | {r['pct']:+.1f}% | [{r['lo']:+.1f}%, {r['hi']:+.1f}%] |")
    L.append("")
    return "\n".join(L) + "\n"


def main() -> int:
    rs = rows(frames())
    fig, caption = draw(rs)
    F.save(fig, "fig22_adjudication_chain", caption)
    out = record_path("FIGURE_ADJUDICATION_CHAIN.md")
    out.write_text(sheet(rs, caption), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
