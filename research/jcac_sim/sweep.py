"""α/β/γ hyperparameter sweep and Pareto figure (W30).

Sweeps the JCAC objective weights over the trace, runs every baseline once,
and renders the cost-vs-violation plane the paper's §5 argues from: each
JCAC weight setting is one point; a controller dominates another when it
sits below-and-left. Also probes γ (fairness weight) sensitivity at the
default α/β.

    python sweep.py --trace ../traces/out/azure_synth.csv.gz \
        --max-steps 120 --out ../results/jcac
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import simulate
from controller import Weights

ALPHAS = (0.3, 1.0, 3.0, 10.0)
BETAS = (0.5, 1.0, 2.0, 4.0)
GAMMA_DEFAULT = 0.5
GAMMA_PROBES = (0.0, 2.0)  # sensitivity at alpha=1, beta=2
BASELINES = ("static", "hpa", "keda", "layered")

# Validated categorical palette (dataviz reference, 5 slots, light mode).
COLORS = {
    "jcac": "#2a78d6",
    "static": "#1baf7a",
    "hpa": "#eda100",
    "keda": "#008300",
    "layered": "#4a3aa7",
}
MARKERS = {"static": "s", "hpa": "^", "keda": "v", "layered": "D"}
# hpa and keda converge to the same point on this trace; spread their labels.
LABEL_OFFSETS = {"static": (7, 4), "hpa": (7, 7), "keda": (7, -13), "layered": (7, 4)}
TEXT_PRIMARY, TEXT_SECONDARY = "#0b0b0b", "#52514e"


def pareto_front(points: list[dict]) -> list[dict]:
    """Non-dominated subset (minimize cost and violation), sorted by cost."""
    front = []
    for p in sorted(points, key=lambda p: (p["total_cost_usd"], p["mean_violation"])):
        if not front or p["mean_violation"] < front[-1]["mean_violation"]:
            front.append(p)
    return front


def run_sweep(trace: Path, max_steps: int | None) -> tuple[list[dict], list[dict]]:
    tenant_ids, buckets = simulate.load_trace_buckets(trace)

    jcac_rows = []
    grid = [(a, b, GAMMA_DEFAULT) for a in ALPHAS for b in BETAS]
    grid += [(1.0, 2.0, g) for g in GAMMA_PROBES]
    for alpha, beta, gamma in grid:
        res = simulate.run(
            "jcac", tenant_ids, buckets, collect_rows=False, max_steps=max_steps,
            weights=Weights(alpha=alpha, beta=beta, gamma=gamma),
        )
        row = {"alpha": alpha, "beta": beta, "gamma": gamma, **res.summary()}
        jcac_rows.append(row)
        print(f"jcac a={alpha:<5} b={beta:<4} g={gamma:<4} "
              f"cost=${row['total_cost_usd']:<8} viol={row['mean_violation']:<7} jain={row['mean_jain']}")

    base_rows = []
    for name in BASELINES:
        res = simulate.run(name, tenant_ids, buckets, collect_rows=False, max_steps=max_steps)
        base_rows.append(res.summary())
        print(f"{name}: {res.summary()}")
    return jcac_rows, base_rows


def plot(jcac_rows: list[dict], base_rows: list[dict], out_png: Path) -> None:
    fig, ax = plt.subplots(figsize=(7.2, 4.6), dpi=150)

    swept = [r for r in jcac_rows if r["gamma"] == GAMMA_DEFAULT]
    front = pareto_front(swept)
    ax.scatter(
        [r["total_cost_usd"] for r in swept],
        [r["mean_violation"] for r in swept],
        s=26, color=COLORS["jcac"], alpha=0.45, linewidths=0, zorder=3,
    )
    ax.plot(
        [r["total_cost_usd"] for r in front],
        [r["mean_violation"] for r in front],
        color=COLORS["jcac"], linewidth=2, marker="o", markersize=6, zorder=4,
    )
    knee = front[len(front) // 2] if front else None
    ax.annotate(
        "JCAC (α/β sweep, Pareto front)",
        xy=(front[0]["total_cost_usd"], front[0]["mean_violation"]),
        xytext=(6, -14), textcoords="offset points",
        fontsize=9, color=TEXT_PRIMARY,
    )
    if knee:
        ax.annotate(
            f"α={knee['alpha']}, β={knee['beta']}",
            xy=(knee["total_cost_usd"], knee["mean_violation"]),
            xytext=(8, 6), textcoords="offset points",
            fontsize=8, color=TEXT_SECONDARY,
        )

    for row in base_rows:
        name = row["controller"]
        ax.scatter(
            row["total_cost_usd"], row["mean_violation"],
            s=58, color=COLORS[name], marker=MARKERS[name], zorder=5, linewidths=0,
        )
        ax.annotate(
            name, xy=(row["total_cost_usd"], row["mean_violation"]),
            xytext=LABEL_OFFSETS[name], textcoords="offset points",
            fontsize=9, color=TEXT_PRIMARY,
        )

    ax.set_xscale("log")
    ax.set_xlabel("Total cost over replay window (USD, log scale)", color=TEXT_PRIMARY)
    ax.set_ylabel("Mean SLO violation (0–1)", color=TEXT_PRIMARY)
    ax.set_title("Cost vs SLO violation — JCAC weight sweep against single-layer baselines",
                 fontsize=10.5, color=TEXT_PRIMARY, loc="left")
    ax.grid(True, which="both", linewidth=0.4, color="#e4e3df", zorder=0)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#c9c8c3")
    ax.tick_params(colors=TEXT_SECONDARY, labelsize=8.5)

    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trace", required=True)
    ap.add_argument("--max-steps", type=int, default=120)
    ap.add_argument("--out", default="../results/jcac")
    args = ap.parse_args()

    jcac_rows, base_rows = run_sweep(Path(args.trace), args.max_steps)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "sweep.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(jcac_rows[0].keys()))
        writer.writeheader()
        writer.writerows(jcac_rows)
    with open(out / "baselines.json", "w") as f:
        json.dump(base_rows, f, indent=2)
    plot(jcac_rows, base_rows, out / "pareto.png")

    front = pareto_front([r for r in jcac_rows if r["gamma"] == GAMMA_DEFAULT])
    print(f"\nPareto front ({len(front)} points):")
    for r in front:
        print(f"  a={r['alpha']} b={r['beta']}: ${r['total_cost_usd']} viol={r['mean_violation']}")
    print(f"wrote {out / 'sweep.csv'}, {out / 'baselines.json'}, {out / 'pareto.png'}")


if __name__ == "__main__":
    main()
