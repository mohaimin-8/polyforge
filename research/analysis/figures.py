"""W36 publication figures. Twelve figures, each answering one question,
saved as 600-DPI PNG (slides) and vector PDF (paper) into
eval/results/figures/, with captions collected in FIGURES.md.

Design method: the categorical palette is the validated color-blind-safe
reference set (worst adjacent CVD deltaE 24.2 in this fixed slot order);
color follows the *system* across every figure, never the rank. Bars are
reserved for zero-baseline quantities; anything on a log axis or a
truncated range uses point-range marks (position encodes, length never
lies). One axis per plot, no dual scales.
"""

from __future__ import annotations

import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import figure_content  # noqa: E402  (platform-independent content dump)
import stats  # noqa: E402  (research/analysis/stats.py)

# POLYFORGE_FIG_DIR redirects output so scripts/reproduce.py can regenerate
# into a scratch dir and diff against the committed figures without touching
# them.
FIG_DIR = (Path(os.environ["POLYFORGE_FIG_DIR"])
           if "POLYFORGE_FIG_DIR" in os.environ
           else stats.REPO_ROOT / "eval" / "results" / "figures")

# Fixed slot order of the validated categorical palette; color follows the
# system entity in every figure.
SYSTEM_COLORS = {
    "jcac": "#2a78d6",      # slot 1 blue  — PolyForge, always
    "hpa": "#1baf7a",       # slot 2 aqua
    "keda": "#eda100",      # slot 3 yellow
    "firm": "#008300",      # slot 4 green
    "static": "#4a3aa7",    # slot 5 violet
    "gptcache": "#e34948",  # slot 6 red
    # Ablation entities (never co-plotted with baselines):
    "jcac_noclassifier": "#eda100",
    "jcac_nojoint": "#008300",
    "jcac_noeviction": "#4a3aa7",
    "jcac_nofairness": "#e34948",
}
SYSTEM_LABELS = {
    "jcac": "PolyForge", "hpa": "HPA", "keda": "KEDA", "firm": "FIRM",
    "static": "Static", "gptcache": "GPTCache",
    "jcac_noclassifier": "− classifier", "jcac_nojoint": "− joint ctrl",
    "jcac_noeviction": "− cost-aware evict", "jcac_nofairness": "− fairness",
}
SYSTEM_ORDER = ["jcac", "hpa", "keda", "firm", "static", "gptcache"]
ABLATION_ORDER = ["jcac_noclassifier", "jcac_nojoint", "jcac_noeviction", "jcac_nofairness"]

INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, AXIS = "#e1e0d9", "#c3c2b7"

plt.rcParams.update({
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "axes.edgecolor": AXIS,
    "axes.linewidth": 0.8,
    "axes.labelcolor": INK,
    "axes.titlesize": 9.5,
    "axes.labelsize": 9,
    "axes.grid": True,
    "grid.color": GRID,
    "grid.linewidth": 0.6,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "xtick.color": INK2,
    "ytick.color": INK2,
    "legend.fontsize": 8,
    "legend.frameon": False,
    "font.family": "sans-serif",
    "pdf.fonttype": 42,  # embed TrueType: journals require editable text
    "ps.fonttype": 42,
})

CAPTIONS: list[tuple[str, str]] = []
INVENTORY = "FIGURES.md"
INVENTORY_HEADER = ("# W36 figure inventory\n\nEach caption answers: what does this "
                    "figure prove?\n\n")


def caption_line(name: str, caption: str) -> str:
    return f"- **{name}** — {caption}\n"


def read_inventory(path: Path) -> dict[str, str]:
    """Caption per figure stem, from an inventory file; {} if there is none.
    One parser for the writer here, the gate and the site build."""
    found: dict[str, str] = {}
    if not path.exists():
        return found
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("- **fig"):
            continue
        stem, _, rest = line[4:].partition("**")
        found[stem] = rest.lstrip(" —-").strip()
    return found


def record_caption(name: str, caption: str) -> None:
    """Upsert one caption line into FIG_DIR/FIGURES.md.

    The inventory was written once by main() for the base twelve and
    appended by advanced.py for 13-17; every figure a campaign script saved
    after that (18, 19, 20 ...) never reached it, because F.CAPTIONS lives in
    the process that drew the figure and nothing flushed it. The site build
    reads this file for its captions, so those figures were published with
    "No caption in FIGURES.md". Now the save that draws a figure is the save
    that inventories it: a line with this name is replaced in place, a new
    name is appended, everything else in the file is left as it was.
    """
    path = FIG_DIR / INVENTORY
    if not path.exists():
        path.write_text(INVENTORY_HEADER, encoding="utf-8")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    new = caption_line(name, caption)
    marker = f"- **{name}**"
    hit = [i for i, line in enumerate(lines) if line.startswith(marker)]
    if hit:
        lines[hit[0]] = new
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(new)
    path.write_text("".join(lines), encoding="utf-8")


def _style(ax, xgrid=False):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="x" if xgrid else "y")
    ax.grid(axis="y" if xgrid else "x", visible=False)
    ax.set_axisbelow(True)


def save(fig, name: str, caption: str):
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG_DIR / f"{name}.png", dpi=600, bbox_inches="tight")
    fig.savefig(FIG_DIR / f"{name}.pdf", bbox_inches="tight")
    # After the saves, so tick labels are populated by the draw, and before
    # close(), which discards the artists. The rendered files cannot be
    # compared across platforms -- bbox_inches="tight" sizes them from local
    # font metrics -- so what the figure PLOTS is dumped separately and that
    # is what scripts/reproduce.py gates on.
    figure_content.write(fig, name, FIG_DIR)
    plt.close(fig)
    CAPTIONS.append((name, caption))
    record_caption(name, caption)
    print(f"  wrote {name} (.png 600dpi, .pdf vector)")


def _pointrange(ax, summary: pd.DataFrame, metric: str, order: list[str], log=False):
    """Horizontal mean +/- 95% CI per system: position encodes, so log
    scales and non-zero ranges are legitimate (bars would lie)."""
    ys = np.arange(len(order))[::-1]
    for y, system in zip(ys, order):
        row = summary.loc[system]
        color = SYSTEM_COLORS[system]
        ax.hlines(y, row[f"{metric}_lo"], row[f"{metric}_hi"], color=color, lw=1.6)
        ax.plot(row[metric], y, "o", color=color, ms=6,
                markeredgecolor="white", markeredgewidth=1)
    ax.set_yticks(ys, [SYSTEM_LABELS[s] for s in order])
    if log:
        ax.set_xscale("log")
    _style(ax, xgrid=True)


def fig_cost(summary):
    fig, ax = plt.subplots(figsize=(3.6, 2.2))
    _pointrange(ax, summary, "total_cost_usd", SYSTEM_ORDER, log=True)
    ax.set_xlabel("total cost per run (USD, log)")
    save(fig, "fig01_cost_by_system",
         "PolyForge spends the least of any controller (mean ± 95% CI over all "
         "300 matrix cells per system, log scale) — joint control converts "
         "visibility into savings rather than over-provisioning.")


def fig_violation(summary):
    fig, ax = plt.subplots(figsize=(3.6, 2.2))
    _pointrange(ax, summary, "mean_violation", SYSTEM_ORDER)
    ax.set_xlabel("mean SLO violation (0–1)")
    save(fig, "fig02_violation_by_system",
         "SLO violations by system (mean ± 95% CI): PolyForge holds violations "
         "near the over-provisioned floor at a fraction of its cost.")


def fig_pareto(summary):
    fig, ax = plt.subplots(figsize=(3.8, 2.8))
    # Per-system label placement: the three feedback controllers cluster
    # bottom-left, so default offsets collide.
    offsets = {
        "jcac": (-8, -3, "right"), "hpa": (0, 14, "center"), "keda": (8, -13, "left"),
        "firm": (8, 3, "left"), "static": (8, 5, "left"), "gptcache": (8, 5, "left"),
    }
    for system in SYSTEM_ORDER:
        row = summary.loc[system]
        c = SYSTEM_COLORS[system]
        ax.plot([row.total_cost_usd_lo, row.total_cost_usd_hi],
                [row.mean_violation, row.mean_violation], color=c, lw=1.2)
        ax.plot([row.total_cost_usd, row.total_cost_usd],
                [row.mean_violation_lo, row.mean_violation_hi], color=c, lw=1.2)
        ax.plot(row.total_cost_usd, row.mean_violation, "o", color=c, ms=7,
                markeredgecolor="white", markeredgewidth=1)
        dx, dy, ha = offsets[system]
        ax.annotate(SYSTEM_LABELS[system], (row.total_cost_usd, row.mean_violation),
                    textcoords="offset points", xytext=(dx, dy), fontsize=8,
                    color=INK2, ha=ha)
    ax.set_xscale("log")
    ax.set_xlabel("total cost per run (USD, log)")
    ax.set_ylabel("mean SLO violation")
    _style(ax)
    save(fig, "fig03_pareto_cost_violation",
         "The cost–violation plane (means ± 95% CI): PolyForge sits on the "
         "bottom-left frontier — every baseline is dominated on at least one "
         "axis without winning the other.")


def fig_jain_by_mix(df):
    mixes = ["uniform", "premium_heavy", "besteffort_heavy", "whale"]
    fig, ax = plt.subplots(figsize=(7.0, 2.4))
    width = 0.12
    for i, system in enumerate(SYSTEM_ORDER):
        xs, ys, los, his = [], [], [], []
        for j, mix in enumerate(mixes):
            vals = df[(df.system == system) & (df.tenant_mix == mix)].mean_jain
            mean, lo, hi = stats.ci95(vals)
            xs.append(j + (i - 2.5) * width)
            ys.append(mean); los.append(mean - lo); his.append(hi - mean)
        ax.errorbar(xs, ys, yerr=[los, his], fmt="o", ms=4.5, lw=0, elinewidth=1.2,
                    capsize=2, color=SYSTEM_COLORS[system],
                    markeredgecolor="white", markeredgewidth=0.8,
                    label=SYSTEM_LABELS[system])
    ax.set_xticks(range(len(mixes)),
                  ["uniform", "premium-heavy", "best-effort-heavy", "whale"])
    ax.set_ylabel("Jain fairness index")
    ax.legend(ncol=6, loc="lower left", bbox_to_anchor=(0, 1.01), columnspacing=1.0)
    _style(ax)
    save(fig, "fig04_jain_by_tenant_mix",
         "Fairness by tenant composition (mean ± 95% CI): the whale mix is where "
         "fairness is hard, and where the fairness-aware objective earns its term.")


def fig_hit_rate(df):
    classes = ["ai_cacheable", "ai_uncacheable", "agentic"]
    fig, ax = plt.subplots(figsize=(5.4, 2.4))
    width = 0.13
    for i, system in enumerate(SYSTEM_ORDER):
        for j, wl in enumerate(classes):
            vals = df[(df.system == system) & (df.workload == wl)].cache_hit_rate
            mean, lo, hi = stats.ci95(vals)
            ax.bar(j + (i - 2.5) * width, mean, width * 0.86,
                   color=SYSTEM_COLORS[system],
                   label=SYSTEM_LABELS[system] if j == 0 else None)
            ax.errorbar(j + (i - 2.5) * width, mean, yerr=[[mean - lo], [hi - mean]],
                        fmt="none", ecolor=INK, elinewidth=0.9, capsize=1.5)
    ax.set_xticks(range(len(classes)), ["AI-cacheable", "AI-uncacheable", "agentic"])
    ax.set_ylabel("realized cache hit rate")
    ax.legend(ncol=6, loc="lower left", bbox_to_anchor=(0, 1.01), columnspacing=1.0)
    _style(ax)
    save(fig, "fig05_cache_hit_by_workload",
         "Realized semantic-cache hit share on AI traffic: adaptive cache sizing "
         "doubles the fixed-cache baselines on cacheable workloads and correctly "
         "declines to spend memory on uncacheable ones.")


def fig_cost_by_cluster(df):
    sizes = ["small", "medium", "large"]
    fig, ax = plt.subplots(figsize=(3.8, 2.6))
    for system in SYSTEM_ORDER:
        means = [df[(df.system == system) & (df.cluster_size == s)].total_cost_usd.mean()
                 for s in sizes]
        ax.plot(range(3), means, "-o", color=SYSTEM_COLORS[system], lw=2, ms=5,
                markeredgecolor="white", markeredgewidth=0.8,
                label=SYSTEM_LABELS[system])
    ax.set_xticks(range(3), sizes)
    ax.set_yscale("log")
    ax.set_xlabel("cluster size")
    ax.set_ylabel("mean cost per run (USD, log)")
    ax.legend(ncol=2)
    _style(ax)
    save(fig, "fig06_cost_by_cluster_size",
         "Cost scaling with cluster headroom: static and cache-max policies pay "
         "for whatever exists; feedback controllers' spend is demand-shaped, and "
         "PolyForge stays lowest at every size.")


def fig_violation_by_workload(df):
    classes = ["crud_bursty", "crud_steady", "ai_cacheable", "ai_uncacheable", "agentic"]
    fig, ax = plt.subplots(figsize=(7.0, 2.4))
    width = 0.12
    for i, system in enumerate(SYSTEM_ORDER):
        xs, ys, los, his = [], [], [], []
        for j, wl in enumerate(classes):
            vals = df[(df.system == system) & (df.workload == wl)].mean_violation
            mean, lo, hi = stats.ci95(vals)
            xs.append(j + (i - 2.5) * width)
            ys.append(mean); los.append(mean - lo); his.append(hi - mean)
        ax.errorbar(xs, ys, yerr=[los, his], fmt="o", ms=4.5, lw=0, elinewidth=1.2,
                    capsize=2, color=SYSTEM_COLORS[system],
                    markeredgecolor="white", markeredgewidth=0.8,
                    label=SYSTEM_LABELS[system])
    ax.set_xticks(range(len(classes)),
                  ["CRUD-bursty", "CRUD-steady", "AI-cache.", "AI-uncache.", "agentic"])
    ax.set_ylabel("mean SLO violation")
    ax.legend(ncol=6, loc="lower left", bbox_to_anchor=(0, 1.01), columnspacing=1.0)
    _style(ax)
    save(fig, "fig07_violation_by_workload",
         "Where baselines break, by taxonomy class (mean ± 95% CI): replica-only "
         "controllers cannot fix agentic latency (it needs the tier knob), which "
         "is the taxonomy's control-implication column made measurable.")


def fig_ablation(df_abl):
    table = stats.ablation_table(df_abl)
    metrics = ["total_cost_usd", "mean_violation", "mean_jain"]
    titles = ["cost", "SLO violation", "Jain fairness"]
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.3), sharey=True)
    ys = np.arange(len(ABLATION_ORDER))[::-1]
    for ax, metric, title in zip(axes, metrics, titles):
        sub = table[table.metric == metric].set_index("ablation")
        vals = [100 * sub.loc[a, "relative_change"] for a in ABLATION_ORDER]
        # Dots on a symlog axis, not bars: the deltas span 0.1%..~3000%,
        # and bar lengths on a compressed scale would lie. Diverging color
        # around zero: worse = warm, better = cool.
        colors = ["#e34948" if v * stats.BETTER[metric] < 0 else "#2a78d6" for v in vals]
        span = max(abs(v) for v in vals)
        if span > 100.0:
            ax.set_xscale("symlog", linthresh=10)
        ax.axvline(0, color=AXIS, lw=0.8)
        for y, v, a, c in zip(ys, vals, ABLATION_ORDER, colors):
            star = "*" if sub.loc[a, "significant"] else ""
            ax.plot(v, y, "o", color=c, ms=7, markeredgecolor="white",
                    markeredgewidth=1, zorder=3)
            ax.annotate(f"{v:+.1f}%{star}", (v, y), textcoords="offset points",
                        xytext=(0, 8), fontsize=7.5, ha="center", color=INK2)
        ax.set_title(title)
        ax.set_ylim(-0.6, len(ABLATION_ORDER) - 0.2)
        if span > 100.0:
            # Symlog: a small fixed sliver left of zero, decades to the right.
            ax.set_xlim(min(min(vals) * 1.5, -2), span * 4)
        else:
            margin = span * 0.45 + 1
            ax.set_xlim(min(vals) - margin, max(vals) + margin)
        _style(ax, xgrid=True)
    axes[0].set_yticks(ys, [SYSTEM_LABELS[a] for a in ABLATION_ORDER])
    fig.suptitle("change vs full PolyForge when one component is removed (%)",
                 fontsize=9, y=1.06)
    save(fig, "fig08_ablation_deltas",
         "Removing any one contribution measurably hurts (red = worse than full "
         "PolyForge; * = p < 0.01): each of the four components carries "
         "independent, statistically significant weight.")


def fig_adaptation(ts_jcac, ts_hpa):
    fig, axes = plt.subplots(2, 1, figsize=(7.0, 3.2), sharex=True)
    for ts, system in ((ts_jcac, "jcac"), (ts_hpa, "hpa")):
        agg = ts.groupby("step").agg(replicas=("replicas", "sum"),
                                     cache=("cache_mb", "sum"))
        axes[0].plot(agg.index, agg.replicas, lw=2, color=SYSTEM_COLORS[system],
                     label=SYSTEM_LABELS[system])
        axes[1].plot(agg.index, agg.cache / 1024.0, lw=2, color=SYSTEM_COLORS[system])
    axes[0].set_ylabel("cluster replicas")
    axes[1].set_ylabel("cluster cache (GB)")
    axes[1].set_xlabel("control interval (10 s steps)")
    axes[0].legend(ncol=2, loc="lower left", bbox_to_anchor=(0, 1.02))
    for ax in axes:
        _style(ax)
    save(fig, "fig09_adaptation_trace",
         "One agentic run, blow by blow: within the first minute PolyForge "
         "fills its replica budget *and* quadruples the semantic cache — the "
         "forecast says bursts will keep coming — while HPA chases every burst "
         "with the only knob it has and never touches the cache. Joint control "
         "in a single trace.")


def fig_p95(summary):
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.3), sharey=True)
    _pointrange(axes[0], summary, "crud_p95_ms", SYSTEM_ORDER, log=True)
    axes[0].set_xlabel("CRUD p95 (ms, log)")
    _pointrange(axes[1], summary, "ai_p95_ms", SYSTEM_ORDER, log=True)
    axes[1].set_xlabel("AI p95 (ms, log)")
    axes[1].set_yticks([])
    save(fig, "fig10_latency_p95",
         "Tail latency by traffic family (mean ± 95% CI, log): PolyForge holds "
         "both families' tails near the SLO-clean systems while spending an "
         "order of magnitude less than they do.")


def fig_violation_share(summary):
    fig, ax = plt.subplots(figsize=(3.6, 2.2))
    _pointrange(ax, summary, "violation_step_share", SYSTEM_ORDER)
    ax.set_xlabel("share of tenant-steps with any violation")
    save(fig, "fig11_violation_step_share",
         "How often anyone is violating at all: the incidence view of SLO "
         "compliance, complementing fig. 2's severity view.")


def fig_objective(df):
    fig, ax = plt.subplots(figsize=(3.6, 2.2))
    # cost normalized per scored tenant-step (119 steps x 8 tenants) by the
    # controller's COST_SCALE_USD, matching the tuning objective exactly.
    j = (df.total_cost_usd / (119 * 8) / 0.01
         + 2.0 * df.mean_violation + 0.5 * (1.0 - df.mean_jain))
    frame = df.assign(J=j)
    rows = []
    for system in SYSTEM_ORDER:
        mean, lo, hi = stats.ci95(frame[frame.system == system].J)
        rows.append({"system": system, "J": mean, "J_lo": lo, "J_hi": hi})
    summary = pd.DataFrame(rows).set_index("system")
    _pointrange(ax, summary, "J", SYSTEM_ORDER, log=True)
    ax.set_xlabel("composite objective J (log; lower is better)")
    save(fig, "fig12_composite_objective",
         "The paper's composite objective (J = cost + 2·violation + 0.5·(1−Jain), "
         "normalized per tenant-step): the single-number ranking every other "
         "figure decomposes.")


def main() -> None:
    df = stats.load_runs(stats.FULL_DB)
    df_abl = stats.load_runs(stats.ABLATIONS_DB)
    summary = stats.system_summary(df)
    print(f"full matrix: {len(df)} runs; ablations: {len(df_abl)} runs")

    fig_cost(summary)
    fig_violation(summary)
    fig_pareto(summary)
    fig_jain_by_mix(df)
    fig_hit_rate(df)
    fig_cost_by_cluster(df)
    fig_violation_by_workload(df)
    fig_ablation(df_abl)
    # agentic/uniform/medium is the rep-0 cell with the richest cache
    # dynamics (all three knobs move) — crud cells leave the cache flat
    # because there is nothing cacheable in them.
    try:
        ts_jcac = stats.load_timeseries(stats.FULL_DB, system="jcac", workload="agentic",
                                        tenant_mix="uniform", cluster_size="medium", rep=0)
        ts_hpa = stats.load_timeseries(stats.FULL_DB, system="hpa", workload="agentic",
                                       tenant_mix="uniform", cluster_size="medium", rep=0)
        fig_adaptation(ts_jcac, ts_hpa)
    except FileNotFoundError as exc:
        print(f"fig09_adaptation_trace skipped: {exc}")
    fig_p95(summary)
    fig_violation_share(summary)
    fig_objective(df)

    # The base twelve open the inventory; save() has already upserted each
    # of their lines, so this rewrite fixes the header and their order and
    # keeps, in name order after them, every later figure's line (advanced,
    # risk, wave-4) that a previous run left in the file -- the same flat
    # list the gate produces when those scripts append to a fresh file.
    later = {k: v for k, v in read_inventory(FIG_DIR / INVENTORY).items()
             if k not in {n for n, _ in CAPTIONS}}
    with open(FIG_DIR / INVENTORY, "w", encoding="utf-8") as f:
        f.write(INVENTORY_HEADER)
        for name, caption in CAPTIONS:
            f.write(caption_line(name, caption))
        for name in sorted(later):
            f.write(caption_line(name, later[name]))
    print(f"12 figures + FIGURES.md in {FIG_DIR}")


if __name__ == "__main__":
    main()
