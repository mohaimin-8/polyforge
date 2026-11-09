"""Every data figure and table of the IEEE paper, generated from committed
records and exports -> figures/*.pdf, tables/*.tex.

Figures re-lay out numbers the gated figure scripts compute (fig22, fig23)
or that the frozen scorers wrote into their records (the per-size tables),
at IEEE column widths. Tables are written from the records' own rows and
from the run-level exports. No number is typed by hand; the chain and width
figures refuse to save if a plotted value differs from the committed figure
sheets (FIGURE_ADJUDICATION_CHAIN.md, FIGURE_WIDTH.md). Every value used is
also written to figures/plotted_numbers.json.

    python make_assets.py        (from research/paper-ieee/)
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

import pandas as pd
from matplotlib.figure import Figure

HERE = Path(__file__).resolve().parent
ANALYSIS = HERE.parent / "analysis"
RESULTS = HERE.parents[1] / "eval" / "results"
FIGS = HERE / "figures"
TABS = HERE / "tables"
sys.path.insert(0, str(ANALYSIS))
import figures as F  # noqa: E402  (shared palette and axis style)


def _mod(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"paper_{name}", ANALYSIS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


chain = _mod("fig_adjudication_chain")
width = _mod("fig_width")

F.plt.rcParams.update({"axes.titlesize": 7.5, "axes.labelsize": 7, "xtick.labelsize": 6.5,
                       "ytick.labelsize": 6.5, "legend.fontsize": 6.5})

# One colour per comparator family in every figure (fig22's tracks: HPA aqua,
# layered stack violet).
COLOR = {"hpa": F.SYSTEM_COLORS["hpa"], "keda": F.SYSTEM_COLORS["keda"], "static": F.MUTED,
         "layered": chain.TRACKS["B"][1]}
COMPARATORS = [("layered_small_tuned", "Layered, cheapest tier", "layered", "D"),
               ("static_share", "Static equal share", "static", "s"),
               ("hpa_fair_capped", "HPA, re-tuned, capped", "hpa", "o"),
               ("keda_fair_capped", "KEDA, re-tuned, capped", "keda", "^")]
SIZES = ("small", "medium", "large")


# ---------------------------------------------------------------- records
def md_table(record: str, heading: str) -> list[dict[str, str]]:
    """Rows of the first markdown table under the section whose heading
    starts with `heading`, cells stripped of bold and code marks."""
    lines = (ANALYSIS / record).read_text(encoding="utf-8").splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith(heading))
    header, rows = None, []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        if not line.startswith("|"):
            if header is not None:
                break
            continue
        cells = [c.strip().replace("**", "").replace("`", "") for c in line.strip().strip("|").split("|")]
        if header is None:
            header = cells
        elif not set(line.replace("|", "").strip()) <= set("-: "):
            rows.append(dict(zip(header, cells)))
    if not rows:
        raise ValueError(f"no table under {heading!r} in {record}")
    return rows


NUM = re.compile(r"[-+]?\d*\.?\d+(?:e[-+]?\d+)?")


def nums(cell: str) -> list[float]:
    return [float(x) for x in NUM.findall(cell)]


def signed(x: float, digits: int = 4) -> str:
    return f"{x:+.{digits}f}"


def ci_tex(cell: str) -> str:
    lo, hi = nums(cell)
    return f"$[{signed(lo)},{signed(hi)}]$"


DIRECTION = {"treatment better": "B", "no difference shown": "T", "comparator better": "W"}


# ---------------------------------------------------------------- figures
LABELS = {
    ("headline", "jcac", "hpa"): ("Initial evaluation", "pre-audit"),
    ("eviction", "jcac", "hpa_fair"): ("No LRU charge, 512 MB cache", "EVICTION_PARITY"),
    ("fair_j", "jcac_converged", "hpa_fair"): ("Fresh seeds, converged solver", "FJ-H1"),
    ("retuned", "jcac_converged", "hpa_fair_retuned"): ("HPA re-tuned per cluster size", "RT-H1"),
    ("jointness", "jcac_converged", "hpa_fair_capped"): ("HPA held to the cluster caps", "JN-B4"),
    ("heldout", "jcac_converged", "hpa_fair_capped"): ("Unseen, non-periodic demand", "HO-B4"),
    ("actuation", "jcac_converged", "hpa_fair_capped"): ("Regime: one-interval lag", "AC-H3"),
    ("fair_j", "jcac_converged", "jcac_nojoint_v2_tuned"): ("Stack, latency-ranked tiers", "FJ-H3"),
    ("retuned", "jcac_converged", "jcac_nojoint_v2_retuned"): ("Same stack, re-tuned per size", "RT-H3"),
    ("jointness", "jcac_converged", "layered_small_tuned"): ("Tier pinned to cheapest model", "JN-B1"),
    ("quality", "jcac_quality", "layered_quality_capped"): ("Quality priced, quality router", "QL-B1"),
    ("heldout", "jcac_converged", "layered_small_tuned"): ("Unseen, non-periodic demand", "HO-B1"),
}
PANELS = (("A", "(a) against reactive replica control (HPA)"),
          ("B", "(b) against a layered stack of per-layer controllers"))


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


def draw_chain(rs: list[dict]) -> Figure:
    rows_by = {t: [r for r in rs if r["track"] == t and (r["frame"], r["a"], r["b"]) in LABELS]
               for t, _ in PANELS}
    # One column wide, panels stacked, each on its own x scale (the HPA steps
    # span about 45 points of J, the stack steps about 125). Step labels on
    # the left, the registered test of each step on the right.
    fig, axes = F.plt.subplots(2, 1, figsize=(3.45, 2.3),
                               gridspec_kw={"height_ratios": [len(rows_by["A"]), len(rows_by["B"])],
                                            "hspace": 0.5})
    fig.subplots_adjust(left=0.385, right=0.805, top=0.935, bottom=0.125)
    for ax, (t, title) in zip(axes, PANELS):
        sub = rows_by[t]
        color = chain.TRACKS[t][1]
        ys = list(range(len(sub)))[::-1]
        for y, r in zip(ys, sub):
            hollow = r["regime"]
            ax.errorbar(r["pct"], y, xerr=[[r["pct"] - r["lo"]], [r["hi"] - r["pct"]]], fmt="o",
                        color=F.MUTED if hollow else color, mfc="white" if hollow else color,
                        capsize=1.6, lw=0.9, ms=3.0, mew=0.9)
            ax.annotate(LABELS[(r["frame"], r["a"], r["b"])][1], (1.02, y),
                        xycoords=("axes fraction", "data"), va="center", ha="left",
                        fontsize=5.6, family="monospace", color=F.INK2, annotation_clip=False)
        ax.axvline(0, color=F.INK2, lw=0.7, ls="--")
        ax.set_yticks(ys, [LABELS[(r["frame"], r["a"], r["b"])][0] for r in sub], fontsize=6.2)
        ax.tick_params(axis="x", labelsize=6.3, pad=1.5)
        ax.tick_params(axis="y", pad=1.5)
        ax.set_ylim(-0.6, len(sub) - 0.4)
        ax.set_title(title, loc="left", fontsize=6.8, pad=3, x=-0.66)
        if t == "B":   # the stack track spans -86% to +40% (FIGURE_ADJUDICATION_CHAIN.md)
            ax.set_xticks([-80, -40, 0, 40])
        F._style(ax, xgrid=True)
    axes[-1].set_xlabel(r"$\Delta J$, % of comparator's $J$  (< 0: joint better)", fontsize=6.5,
                        labelpad=2)
    return fig


def size_rows() -> dict[str, list[dict]]:
    """Per-cluster-size differences as the frozen scorers wrote them."""
    out = {}
    for regime, record in (("instant", "RESULTS_JOINTNESS.md"), ("lag", "RESULTS_ACTUATION.md")):
        rows = md_table(record, "## Per cluster size")
        out[regime] = [{"cluster": r["cluster"], "vs": r["vs"], "dJ": nums(r["ΔJ"])[0],
                        "lo": nums(r["95% CI"])[0], "hi": nums(r["95% CI"])[1],
                        "direction": r["direction"]}
                       for r in rows if r["vs"] in {c[0] for c in COMPARATORS}]
    return out


def draw_sizes(data: dict[str, list[dict]]) -> Figure:
    fig, axes = F.plt.subplots(1, 2, figsize=(3.45, 1.9), sharey=True,
                               gridspec_kw={"wspace": 0.08})
    titles = {"instant": "(a) instant actuation", "lag": "(b) replicas one interval late"}
    for ax, regime in zip(axes, ("instant", "lag")):
        for k, (arm, label, key, marker) in enumerate(COMPARATORS):
            pts = [next(r for r in data[regime] if r["vs"] == arm and r["cluster"] == s) for s in SIZES]
            xs = [i + (k - 1.5) * 0.17 for i in range(len(SIZES))]
            ax.errorbar(xs, [p["dJ"] for p in pts],
                        yerr=[[p["dJ"] - p["lo"] for p in pts], [p["hi"] - p["dJ"] for p in pts]],
                        fmt=marker, color=COLOR[key], ms=3.2, capsize=1.5, lw=0.9, label=label)
        ax.axhline(0, color=F.INK2, lw=0.7, ls="--")
        ax.set_xticks(range(len(SIZES)), SIZES)
        ax.set_xlim(-0.55, len(SIZES) - 0.45)
        ax.set_title(titles[regime], loc="left", fontsize=7)
        F._style(ax)
    axes[0].set_ylabel(r"$\Delta J$ = joint $-$ comparator  (< 0: joint better)")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=2, frameon=False, bbox_to_anchor=(0.53, 1.13),
               handletextpad=0.3, columnspacing=1.0)
    return fig


def draw_width(rows: list[dict]) -> Figure:
    fig, (ax_g, ax_d) = F.plt.subplots(1, 2, figsize=(3.45, 1.75),
                                       gridspec_kw={"width_ratios": [1.15, 1], "wspace": 0.42})
    order = width.ORDER
    names = {"live": "live-\nfitted", "published": "pub-\nlished", "coupled": "coupled"}
    for lattice, (_, color, marker) in width.LATTICE.items():
        sub = [r for r in rows if r["lattice"] == lattice]
        off = -0.13 if lattice == "narrow" else 0.13
        label = "rate-limited: ±2 replicas, 1 cache level" if lattice == "narrow" \
            else "wide: ±8 replicas, any cache level"
        xs = [order.index(r["plant"]) + off for r in sub]
        ax_g.errorbar(xs, [r["pct"] for r in sub],
                      yerr=[[r["pct"] - r["lo"] for r in sub], [r["hi"] - r["pct"] for r in sub]],
                      fmt=marker, color=color, capsize=1.8, lw=1.0, ms=3.4, label=label)
        ax_d.plot(xs, [100 * r["disagree"] for r in sub], marker, color=color, ms=3.6, ls="none")
    for ax in (ax_g, ax_d):
        ax.set_xticks(range(len(order)), [names[p] for p in order])
        ax.set_xlim(-0.5, len(order) - 0.5)
        F._style(ax)
    ax_g.axhline(0, color=F.INK2, lw=0.7, ls="--")
    ax_g.set_ylabel("gain $G$, % of decoupled $J$ (> 0: joint better)", fontsize=6.5)
    ax_d.set_ylabel("decisions where the searches\ndisagree (%)", fontsize=6.5)
    ax_g.set_title("(a) coordination gain", loc="left", fontsize=7)
    ax_d.set_title("(b) disagreement", loc="left", fontsize=7)
    ax_d.set_ylim(bottom=0)
    handles, labels = ax_g.get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=1, frameon=False, bbox_to_anchor=(0.5, 1.15),
               handletextpad=0.3)
    return fig


TRADEOFF_COLOR = {"jc": F.SYSTEM_COLORS["jcac"], "layered": COLOR["layered"], "hpa": COLOR["hpa"],
                  "keda": COLOR["keda"], "static": COLOR["static"]}
TRADEOFF_GROUP = {"jcac_converged": "jc", "jcac_decoupled": "jc", "jcac_knobwise": "jc",
                  "jcac_violation": "jc", "jcac_tier_floor": "jc", "layered_small_tuned": "layered",
                  "layered_costaware_tuned": "layered", "jcac_nojoint_v2_retuned": "layered",
                  "static_share": "static", "hpa_fair_retuned": "hpa", "hpa_fair_capped": "hpa",
                  "keda_fair_capped": "keda"}
# (label, arm the arrow points at, text position in data coordinates, alignment)
TRADEOFF_LABELS = (
    ("JC: joint, decoupled and\nknob-wise search", "jcac_converged", (3.25, 0.098), "left"),
    ("JC, plans on violation", "jcac_violation", (2.0, 0.112), "left"),
    ("JC, never sheds", "jcac_tier_floor", (3.25, 0.074), "left"),
    ("HPA, KEDA, static share,\ncheapest-tier stack", "hpa_fair_capped", (3.25, 0.024), "left"),
    ("Layered, cost-aware tier", "layered_costaware_tuned", (4.05, 0.124), "right"),
    ("Layered, registered rule", "jcac_nojoint_v2_retuned", (8.6, 0.040), "right"),
)


def draw_tradeoff(arms: list[dict]) -> Figure:
    """Mean cost against mean SLO violation for every arm of Table II."""
    by_arm = {a["arm"]: a for a in arms}
    fig, ax = F.plt.subplots(figsize=(3.45, 1.9))
    for a in arms:
        group = TRADEOFF_GROUP[a["arm"]]
        ax.plot(a["cost"], a["violation"], "D" if group == "layered" else "o", ms=3.6,
                color=TRADEOFF_COLOR[group], mec="white", mew=0.4, zorder=3)
    for text, arm, (tx, ty), ha in TRADEOFF_LABELS:
        a = by_arm[arm]
        ax.annotate(text, (a["cost"], a["violation"]), xytext=(tx, ty), textcoords="data", ha=ha, va="center",
                    fontsize=6, color=F.INK2, linespacing=1.0,
                    arrowprops={"arrowstyle": "-", "color": F.MUTED, "lw": 0.5, "shrinkA": 1, "shrinkB": 3})
    ax.set_xscale("log")
    ax.set_xticks([2, 3, 4, 6, 8], ["2", "3", "4", "6", "8"])
    ax.xaxis.set_minor_formatter(F.plt.NullFormatter())
    ax.set_xlim(1.85, 9.5)
    ax.set_ylim(0, 0.135)
    ax.set_xlabel("mean cost per run (simulated \\$, log scale)")
    ax.set_ylabel("mean SLO violation")
    F._style(ax)
    return fig


# ---------------------------------------------------------------- tables
CAMPAIGNS = [
    ("FAIR\\_J", "FJ", ["fair_j"], "Fair comparators", "RESULTS_FAIR_J.md"),
    ("RETUNED", "RT", ["retuned"], "Per-size re-tuning", "RESULTS_RETUNED.md"),
    ("JOINTNESS", "JN", ["jointness"], "Coordination, stacks, caps", "RESULTS_JOINTNESS.md"),
    ("ACTUATION", "AC", ["actuation"], "One-interval actuation lag", "RESULTS_ACTUATION.md"),
    ("QUALITY", "QL", ["quality"], "Answer quality priced", "RESULTS_QUALITY.md"),
    ("HELDOUT", "HO", ["heldout"], "Unseen bursty demand", "RESULTS_HELDOUT.md"),
    ("WIDTH", "WD", ["width_published", "width_coupled", "width_live"],
     "Step width $\\times$ coupling", "RESULTS_WIDTH.md"),
]


def ledger_counts() -> dict[str, tuple[int, int]]:
    """(PASS, total) registered p-valued tests per record, from the ledger."""
    out: dict[str, list[set]] = {}
    for line in (ANALYSIS / "HYPOTHESIS_LEDGER.md").read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `RESULTS_"):
            continue
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(c) < 4 or c[3] not in ("PASS", "FAIL"):
            continue
        rec = c[0].strip("`")
        tests, passed = out.setdefault(rec, [set(), set()])
        tests.add(c[2])
        if c[3] == "PASS":
            passed.add(c[2])
    return {rec: (len(p), len(t)) for rec, (t, p) in out.items()}


# Registered follow-ups to reviews: (name, tag, exports metrics_<e>.csv.gz,
# question, record). BM's exports hold only its three new arms; its tests pair
# them with the registered JOINTNESS and TRACE_AUDITED rows.
FOLLOWUPS = [
    ("REVIEWER", "RV", ["reviewer_wide", "reviewer_live"], "Wide steps, live plant",
     "RESULTS_REVIEWER_FOLLOWUP.md"),
    ("TRACE\\_AUD.", "TA", ["trace_audited"], "Real BurstGPT demand", "RESULTS_TRACE_AUDITED.md"),
    ("CACHE\\_SENS.", "CS", ["cache_measured", "cache_published", "cache_high"], "Three cache-hit curves",
     "RESULTS_CACHE_SENSITIVITY.md"),
    ("BUDGET\\_MAT.", "BM", ["budget_jointness", "budget_trace"], "Comparators on the budget",
     "RESULTS_BUDGET_MATCHED.md"),
]


def _joined(counts: list[int], sep: str) -> str:
    if len(set(counts)) == 1:
        return f"{len(counts)}$\\times${counts[0]}" if len(counts) > 1 and sep == "x" else str(counts[0])
    return "+".join(str(c) for c in counts)


def followup_rows(ledger: dict) -> tuple[list[str], list[dict]]:
    lines, data = [], []
    for name, tag, exps, question, record in FOLLOWUPS:
        frames = [pd.read_csv(RESULTS / f"metrics_{e}.csv.gz",
                              usecols=["system", "workload", "tenant_mix", "cluster_size", "rep"]) for e in exps]
        runs = sum(len(f) for f in frames)
        arms = [f.system.nunique() for f in frames]
        points = [f.groupby(["workload", "tenant_mix", "cluster_size"]).ngroups for f in frames]
        passed, total = ledger[record]
        data.append({"campaign": name, "tag": tag, "runs": runs, "arms": arms, "points": points,
                     "pass": passed, "tests": total})
        lines.append(f"{name} & {tag} & {question} & {_joined(arms, '+')} & {_joined(points, 'x')} & "
                     f"{runs:,} & {passed}/{total} \\\\")
    return lines, data


def table_campaigns() -> tuple[str, dict]:
    ledger = ledger_counts()
    lines, data = [], []
    tot_runs = tot_pass = tot_tests = 0
    for name, tag, exps, question, record in CAMPAIGNS:
        frames = [pd.read_csv(RESULTS / f"metrics_matrix_{e}.csv.gz",
                              usecols=["system", "workload", "tenant_mix", "cluster_size", "rep"])
                  for e in exps]
        runs = sum(len(f) for f in frames)
        # WIDTH's three plants must share the same arms and the same design
        # points, not merely as many of each.
        arm_sets = {frozenset(f.system.unique()) for f in frames}
        point_sets = {frozenset(f[["workload", "tenant_mix", "cluster_size"]].itertuples(index=False, name=None))
                      for f in frames}
        assert len(arm_sets) == 1 and len(point_sets) == 1, (name, arm_sets, point_sets)
        points = [len(next(iter(point_sets)))] * len(frames)
        npoints = f"{len(points)}$\\times${points[0]}" if len(points) > 1 else str(points[0])
        passed, total = ledger[record]
        tot_runs, tot_pass, tot_tests = tot_runs + runs, tot_pass + passed, tot_tests + total
        data.append({"campaign": name, "tag": tag, "runs": runs, "arms": len(next(iter(arm_sets))),
                     "points": points, "pass": passed, "tests": total})
        lines.append(f"{name} & {tag} & {question} & {data[-1]['arms']} & {npoints} & {runs:,} & "
                     f"{passed}/{total} \\\\")
    body = "\n".join(lines)
    f_lines, f_data = followup_rows(ledger)
    f_runs = sum(r["runs"] for r in f_data)
    f_pass = sum(r["pass"] for r in f_data)
    f_tests = sum(r["tests"] for r in f_data)
    all_runs, all_pass, all_tests = tot_runs + f_runs, tot_pass + f_pass, tot_tests + f_tests
    tex = ("\\begin{tabular}{@{}l l l r r r r@{}}\n\\toprule\n"
           "Campaign & Tag & Question & Arms & Points & Runs & PASS \\\\\n\\midrule\n"
           f"{body}\n\\midrule\nSubtotal & & & & & {tot_runs:,} & {tot_pass}/{tot_tests} \\\\\n"
           "\\midrule\n" + "\n".join(f_lines) + "\n\\midrule\n"
           f"Total & & & & & {all_runs:,} & {all_pass}/{all_tests} \\\\\n"
           "\\bottomrule\n\\end{tabular}\n")
    return tex, {"rows": data, "runs": tot_runs, "pass": tot_pass, "tests": tot_tests,
                 "followups": f_data, "all_runs": all_runs, "all_pass": all_pass, "all_tests": all_tests}


TIER_MODELS = {"small": "0.5B", "mid": "3B", "large": "7B"}


def table_tiers() -> tuple[str, list[dict]]:
    """The plant's model tiers, read from the simulator's own constants."""
    import math

    sys.path.insert(0, str(HERE.parent / "jcac_sim"))
    import model as M  # noqa: E402

    kinds = ("chat", "embed", "agent")
    lines, data = [], []
    for tier, price in M.TIER_COST_USD_PER_REQ.items():
        if price:
            exp = round(math.log10(price))
            assert math.isclose(price, 10.0 ** exp), (tier, price)
            price_tex = f"$10^{{{exp}}}$"
        else:
            price_tex = "0"
        if tier == "none":
            label = "none"
            lat = f"\\multicolumn{{3}}{{c}}{{{M.NO_TIER_LATENCY_MS:,.0f}, no model call}}"
            lat_ms = {k: M.NO_TIER_LATENCY_MS for k in kinds}
        else:
            label = f"{tier} ({TIER_MODELS[tier]})"
            lat_ms = {k: M.TIER_BASE_LATENCY_MS[k][tier] for k in kinds}
            lat = " & ".join(f"{lat_ms[k]:,.0f}" for k in kinds)
        quality = M.TIER_QUALITY[tier]
        data.append({"tier": tier, "price": price, "latency_ms": lat_ms, "quality": quality})
        lines.append(f"{label} & {price_tex} & {lat} & {quality:.3f} \\\\")
    tex = ("\\begin{tabular}{@{}l c r r r c@{}}\n\\toprule\n"
           " & Price & \\multicolumn{3}{c}{Base latency (ms)} & \\\\\n\\cmidrule(lr){3-5}\n"
           "Tier (model) & (\\$/miss) & Chat & Embed & Agent & Quality \\\\\n\\midrule\n" + "\n".join(lines) +
           "\n\\bottomrule\n\\end{tabular}\n")
    return tex, data


ARMS = [("jcac_converged", "\\jcac{}, joint search"),
        ("jcac_decoupled", "\\jcac{}, decoupled search"),
        ("jcac_knobwise", "\\jcac{}, knob-wise search"),
        ("jcac_violation", "\\jcac{}, plans on violation"),
        ("jcac_tier_floor", "\\jcac{}, never sheds"),
        ("layered_small_tuned", "Layered, cheapest tier"),
        ("layered_costaware_tuned", "Layered, cost-aware tier"),
        ("jcac_nojoint_v2_retuned", "Layered, registered rule"),
        ("static_share", "Static equal share"),
        ("hpa_fair_retuned", "HPA, re-tuned"),
        ("hpa_fair_capped", "HPA, re-tuned, capped"),
        ("keda_fair_capped", "KEDA, re-tuned, capped")]


def table_arms() -> tuple[str, list[dict]]:
    rows = {r["arm"]: r for r in md_table("RESULTS_JOINTNESS.md", "## Per-arm means")}
    lines, data = [], []
    for i, (arm, label) in enumerate(ARMS):
        r = rows[arm]
        vals = {"J": float(r["J"]), "cost": float(r["cost $"]), "violation": float(r["violation"]),
                "excess": float(r["excess"]), "shed": r["AI shed"], "overrun": r["budget overrun"]}
        data.append({"arm": arm, **vals})
        sep = "\\midrule\n" if i in (5, 8) else ""
        lines.append(f"{sep}{label} & {vals['J']:.4f} & {vals['cost']:.2f} & {vals['violation']:.3f} & "
                     f"{vals['excess']:.3f} & {vals['shed'].replace('%', '')} & "
                     f"{vals['overrun'].replace('%', '')} \\\\")
    tex = ("\\begin{tabular}{@{}l r r r r r r@{}}\n\\toprule\n"
           "Arm & $J$ & Cost & Viol. & Excess & Shed & Over. \\\\\n"
           " & & (\\$) & & & (\\%) & (\\%) \\\\\n\\midrule\n" + "\n".join(lines) +
           "\n\\bottomrule\n\\end{tabular}\n")
    return tex, data


POST = [("Decoupled search", "jcac_decoupled", ("JN-C1", None, "HO-C1")),
        ("Layered, cheapest tier", "layered_small_tuned", ("JN-B1", "AC-H1", "HO-B1")),
        ("Static equal share", "static_share", ("JN-B3", "AC-H2", "HO-B3")),
        ("HPA, re-tuned, capped", "hpa_fair_capped", ("JN-B4", "AC-H3", "HO-B4")),
        ("KEDA, re-tuned, capped", "keda_fair_capped", ("JN-B5", "AC-H4", "HO-B5"))]
POST_BLOCKS = (("Instant actuation", "jointness", "RESULTS_JOINTNESS.md", "## Confirmatory: JN-C1"),
               ("Replicas one interval late", "actuation", "RESULTS_ACTUATION.md", "## Confirmatory: AC-H1"),
               ("Unseen, non-periodic demand", "heldout", "RESULTS_HELDOUT.md", "## Confirmatory: HO-C1"))


EQ_HEADING = "## Coordination: equivalence"


def has_section(record: str, heading: str) -> bool:
    return any(l.startswith(heading) for l in (ANALYSIS / record).read_text(encoding="utf-8").splitlines())


def design_points(exp: str) -> int:
    f = pd.read_csv(RESULTS / f"metrics_matrix_{exp}.csv.gz", usecols=["workload", "tenant_mix", "cluster_size"])
    return f.groupby(["workload", "tenant_mix", "cluster_size"]).ngroups


def table_post() -> tuple[str, list[dict]]:
    # One column wide: one block of rows per regime rather than one column each.
    lines, data = [], []
    for col, (regime, exp, record, heading) in enumerate(POST_BLOCKS):
        conf = {r["id"]: r for r in md_table(record, heading)}
        # ACTUATION registered no equivalence reading; anywhere the section
        # exists, a parse failure must raise rather than drop the EQ marks.
        eq = ({r["id"].removesuffix("-EQ"): r["equivalent?"]
               for r in md_table(record, EQ_HEADING)} if has_section(record, EQ_HEADING) else {})
        sep = "\\midrule\n" if col else ""
        lines.append(f"{sep}\\multicolumn{{5}}{{@{{}}l}}{{\\textit{{{regime}}} "
                     f"({design_points(exp)} design points)}} \\\\")
        for label, arm, ids in POST:
            test = ids[col]
            if test is None:
                continue
            r = conf[test]
            assert r["vs"] == arm, (test, r["vs"], arm)
            mark = DIRECTION[r["direction"]] + (", EQ" if eq.get(test) == "EQUIVALENT" else "")
            lines.append(f"\\quad {label} & \\texttt{{{test}}} & ${signed(nums(r['ΔJ'])[0])}$ & "
                         f"{ci_tex(r['95% CI of ΔJ'])} & {mark} \\\\")
            data.append({"test": test, "vs": arm, "dJ": nums(r["ΔJ"])[0], "ci": nums(r["95% CI of ΔJ"]),
                         "mark": mark})
    tex = ("\\begin{tabular}{@{}l l r c l@{}}\n\\toprule\n"
           "Comparator & Test & $\\Delta J$ & 95\\% CI & \\\\\n\\midrule\n" + "\n".join(lines) +
           "\n\\bottomrule\n\\end{tabular}\n")
    return tex, data


WIDTH_ORDER = (("live", "Live-fitted"), ("published", "Published"), ("coupled", "Coupled"))


def table_width() -> tuple[str, list[dict]]:
    per = {r["plant"]: r for r in md_table("RESULTS_WIDTH.md", "## Per plant")}
    h5 = {r["id"]: r for r in md_table("RESULTS_WIDTH.md", "## Confirmatory: WD-H5")}
    lines, data = [], []
    for plant, label in WIDTH_ORDER:
        r = per[plant]
        gn, gw, did = nums(r["G narrow [95% CI]"])[0], nums(r["G wide [95% CI]"])[0], r["DiD [95% CI]"]
        d, lo, hi = nums(did)
        rate = nums(h5[f"WD-H5[{plant}]"]["J narrow - J wide"])[0]
        data.append({"plant": plant, "g_n": gn, "g_w": gw, "did": d, "did_ci": [lo, hi], "rate": rate,
                     "dis_n": r["disagreement narrow"], "dis_w": r["disagreement wide"]})
        lines.append(f"{label} & ${signed(gn)}$ & ${signed(gw)}$ & ${signed(d)}$ "
                     f"$[{signed(lo)},{signed(hi)}]$ & ${signed(rate)}$ \\\\")
    tex = ("\\begin{tabular}{@{}lrrcr@{}}\n\\toprule\n"
           "Plant & $G_n$ & $G_w$ & DiD [95\\% CI] & $J_n-J_w$ \\\\\n\\midrule\n" + "\n".join(lines) +
           "\n\\midrule\nTests & \\multicolumn{2}{l}{\\texttt{WD-H4}: EQ, all} & "
           "\\texttt{WD-H1}, \\texttt{H2} PASS; \\texttt{H3} EQ & \\texttt{WD-H5} PASS \\\\\n"
           "\\bottomrule\n\\end{tabular}\n")
    return tex, data


# ---------------------------------------------------------------- main
def main() -> int:
    FIGS.mkdir(exist_ok=True)
    TABS.mkdir(exist_ok=True)
    rs = chain.rows(chain.frames())
    check_chain(rs)
    wrows = width.summary({p: width.aw.prepare(width.aw.load(p)) for p in width.aw.PLANTS})
    check_width(wrows)
    sizes = size_rows()
    _, arms = table_arms()
    for name, fig in (("fig_chain", draw_chain(rs)), ("fig_sizes", draw_sizes(sizes)),
                      ("fig_width", draw_width(wrows)), ("fig_tradeoff", draw_tradeoff(arms))):
        fig.savefig(FIGS / f"{name}.pdf", bbox_inches="tight", pad_inches=0.02)
        F.plt.close(fig)
        print(f"wrote figures/{name}.pdf")
    used = {"chain": [{k: r[k] for k in ("track", "source", "a", "b", "n", "dJ", "pct", "lo", "hi", "regime")}
                      for r in rs if (r["frame"], r["a"], r["b"]) in LABELS],
            "width": wrows, "sizes": sizes}
    for name, builder in (("tab_campaigns", table_campaigns), ("tab_arms", table_arms),
                          ("tab_post", table_post), ("tab_width", table_width), ("tab_tiers", table_tiers)):
        tex, data = builder()
        (TABS / f"{name}.tex").write_text(f"% generated by make_assets.py -- do not edit\n{tex}",
                                          encoding="utf-8", newline="\n")
        used[name] = data
        print(f"wrote tables/{name}.tex")
    (FIGS / "plotted_numbers.json").write_text(json.dumps(used, indent=1, sort_keys=True) + "\n",
                                               encoding="utf-8", newline="\n")
    print("every chain and width number matches the committed figure sheets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
