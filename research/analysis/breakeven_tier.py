"""Tier-price break-even analysis — DECLARED EXPLORATORY (Wave 1).

Declared 2026-07-16 (session 20), grid frozen in this file before execution.

Motivation (DEFENSE_QA.md #15): the matrix prices model tiers at API-market
ratios (small:mid:large = 1:10:100, TIER_COST_USD_PER_REQ) while the Phase 6
GPU bench measured GPU-seconds per request at 1:1.52:16.6 on one fixed card
(TIER_BENCH.md). SENSITIVITY_J.md swept the objective *weights*, never the
price *constants*, so how much of the cost win survives at self-hosting
ratios is unmeasured. This script answers the *accounting* half of that
question by exact algebra over the recorded per-step rows: every run's
tier spend is re-priced under a frozen grid of price vectors while the
recorded decisions stay fixed. The *decision* half — controllers re-planning
under the new economy — is the pre-registered rerun (PREREG_TIER_RATIO.md);
this analysis is what motivates and bounds it.

Method, stated before the first run:
- Source: per-step timeseries rows (rep 0 only — `timeseries_reps: 1`) of
  the closed campaigns below. No simulation is run, no campaign file is
  touched.
- Exact decomposition per row: billing follows the *nominal* recorded state
  (simulate.run bills nominal even when serving is degraded), so
    infra_step = replicas * REPLICA_COST_USD_HR * (10/3600)
               + (cache_mb/1024) * MEM_COST_USD_GB_HR * (10/3600)
    tier_step  = max(0, cost_usd - infra_step)
  tier_step is the recorded row's tier spend *including* the LRU
  miss-cost factor for LRU systems; that factor is price-independent and
  multiplicative, so it survives re-pricing unchanged.
- Re-pricing: tier_step' = tier_step * p_new[tier]/p_old[tier], with
  p_old = (1e-4, 1e-3, 1e-2) frozen and tier read from the row. Rows with
  tier "none" carry zero tier spend by construction.
- Identity check (must pass before any output is written): re-pricing at
  the frozen corner (r_mid=10, r_large=100, level=1) must reproduce every
  run's recorded total within 2e-3 USD (row rounding is 1e-6; <=952 scored
  rows per run).

Frozen grid — do not extend after execution:
  ratio r_mid  in {1.52, 3, 5, 10}          (mid price / small price)
  ratio r_large in {5, 10, 16.6, 33, 100}   (large price / small price)
  level s in {0.2, 1, 5}                    (multiplies all three prices;
                                             the tier-vs-infra economy axis)
  small price anchored at 1e-4 * s. 60 price vectors; (10, 100, 1) is the
  published economy, (1.52, 16.6, 1) is the measured GPU-bench corner.

Rules of this analysis:
- EXPLORATORY. Decisions are frozen at what the controllers chose under the
  published economy; a controller re-planning under a new economy could do
  better (or reallocate spend), so these are *accounting* readings, not
  performance claims. No confirmatory claim derives from this file.
- Descriptive statistics only. Rep-0 rows give one run per (system, cell);
  run seeds differ per system, so cross-system deltas within a cell compare
  different jitter draws of the same workload — fine for direction and
  magnitude, not for paired p-values. No p-values are reported.
- One execution, output to BREAKEVEN_TIER.md. If this file and the output
  disagree, the output is stale and must be regenerated, never edited.

Campaigns covered (treatment vs baselines):
  v1 headline   raw_sim.duckdb     jcac          vs hpa/keda/firm/static/gptcache
  v2 matrix     raw_sim_v2.duckdb  jcac_v2       vs hpa/keda/firm/static/gptcache
  v3 overload   raw_sim_v3.duckdb  jcac_seasonal vs hpa/keda/firm

Amendment (declared after the first execution, reporting only — grid and
method unchanged): the first output summarized each price point by the mean
of per-cell *relative* deltas, which over-weights cells whose baseline cost
is near zero (v1 jcac-vs-hpa read +1.4% by that statistic while aggregate
rep-0 spend is 142 vs 223 USD). The aggregate delta (sum of treatment spend
vs sum of baseline spend over the same 60 cells) is added beside it; both
statistics are shown, nothing is removed, and flips are listed under both
definitions.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "eval" / "results"
OUT = Path(__file__).resolve().parent / "BREAKEVEN_TIER.md"

# Frozen economy of every committed campaign (model.py).
P_OLD = {"small": 1e-4, "mid": 1e-3, "large": 1e-2}
REPLICA_COST_USD_HR = 0.048
MEM_COST_USD_GB_HR = 0.005
INTERVAL_HR = 10.0 / 3600.0

# Frozen grid — do not extend after execution.
R_MID = [1.52, 3.0, 5.0, 10.0]
R_LARGE = [5.0, 10.0, 16.6, 33.0, 100.0]
LEVEL = [0.2, 1.0, 5.0]
PUBLISHED = (10.0, 100.0, 1.0)
GPU_CORNER = (1.52, 16.6, 1.0)

IDENTITY_TOL_USD = 2e-3

CAMPAIGNS = [
    {
        "name": "v1 headline matrix",
        "source": "raw_sim.duckdb",
        "treatment": "jcac",
        "baselines": ["hpa", "keda", "firm", "static", "gptcache"],
    },
    {
        "name": "v2 matrix",
        "source": "raw_sim_v2.duckdb",
        "treatment": "jcac_v2",
        "baselines": ["hpa", "keda", "firm", "static", "gptcache"],
    },
    {
        "name": "v3 overload matrix",
        "source": "raw_sim_v3.duckdb",
        "treatment": "jcac_seasonal",
        "baselines": ["hpa", "keda", "firm"],
    },
]

CELL = ["workload", "tenant_mix", "cluster_size"]


def load_rows(source: str) -> pd.DataFrame:
    """Rep-0 per-step rows joined with run identity, decomposed into
    infra/tier spend per row (exact, see module docstring)."""
    con = duckdb.connect(str(RESULTS_DIR / source), read_only=True)
    df = con.execute(
        """
        SELECT r.run_id, r.system, r.workload, r.tenant_mix, r.cluster_size,
               t.replicas, t.cache_mb, t.tier, t.cost_usd
        FROM timeseries t JOIN runs r USING (run_id)
        WHERE r.status = 'valid' AND r.rep = 0
        """
    ).df()
    con.close()
    infra = (
        df.replicas * REPLICA_COST_USD_HR * INTERVAL_HR
        + (df.cache_mb / 1024.0) * MEM_COST_USD_GB_HR * INTERVAL_HR
    )
    df["infra_usd"] = infra
    df["tier_usd"] = (df.cost_usd - infra).clip(lower=0.0)
    return df


def reprice(per_run: pd.DataFrame, r_mid: float, r_large: float, s: float) -> pd.Series:
    """Total re-priced cost per run_id. per_run holds per-(run, tier) sums."""
    ratio = {
        "none": 0.0,
        "small": s * 1e-4 / P_OLD["small"],
        "mid": s * r_mid * 1e-4 / P_OLD["mid"],
        "large": s * r_large * 1e-4 / P_OLD["large"],
    }
    scaled = per_run.tier_usd * per_run.tier.map(ratio)
    return (per_run.infra_usd + scaled).groupby(per_run.run_id).sum()


def main() -> None:
    lines: list[str] = []
    w = lines.append
    w("# Tier-price break-even — EXPLORATORY (declared before run, Wave 1)")
    w("")
    w("Generated by `breakeven_tier.py`; grid, method and rules are frozen in that")
    w("file's docstring. Decisions stay fixed at what each controller chose under")
    w("the published 1:10:100 economy — this is the *accounting* reading of")
    w("DEFENSE_QA #15; the controllers-re-decide reading is the pre-registered")
    w("rerun (`PREREG_TIER_RATIO.md`). **No confirmatory claim derives from this")
    w("file.** Rep-0 rows only; descriptive statistics only (no p-values, seeds")
    w("differ per system within a cell).")
    w("")

    for spec in CAMPAIGNS:
        rows = load_rows(spec["source"])
        # Collapse to per-(run, tier) sums once; re-pricing is then a map+sum.
        per_run = (
            rows.groupby(["run_id", "system", "workload", "tenant_mix",
                          "cluster_size", "tier"], as_index=False)
            [["infra_usd", "tier_usd"]].sum()
        )
        recorded = rows.groupby("run_id").cost_usd.sum()

        # Identity check at the published corner.
        repriced_pub = reprice(per_run, *PUBLISHED)
        gap = (repriced_pub - recorded).abs()
        assert gap.max() < IDENTITY_TOL_USD, (
            f"{spec['source']}: identity check failed, max gap {gap.max():.6f} USD"
        )

        ident = per_run.groupby("run_id").first()[
            ["system", "workload", "tenant_mix", "cluster_size"]
        ]

        w(f"## {spec['name']} — `{spec['source']}`, rep-0 rows, "
          f"identity check max gap {gap.max():.2e} USD")
        w("")

        # Spend composition at the published economy.
        comp = (
            per_run.groupby("system")[["infra_usd", "tier_usd"]].sum()
        )
        comp["tier_share"] = comp.tier_usd / (comp.infra_usd + comp.tier_usd)
        w("| system | infra USD | tier USD | tier share of spend |")
        w("|---|---|---|---|")
        for system, r in comp.sort_values("tier_share", ascending=False).iterrows():
            w(f"| {system} | {r.infra_usd:.3f} | {r.tier_usd:.3f} | {r.tier_share:.1%} |")
        w("")

        # Tier posture: share of tenant-steps at each tier.
        posture = (
            rows.groupby(["system", "tier"]).size().unstack(fill_value=0)
        )
        posture = posture.div(posture.sum(axis=1), axis=0)
        cols = [t for t in ("none", "small", "mid", "large") if t in posture.columns]
        w("| system | " + " | ".join(f"steps @ {t}" for t in cols) + " |")
        w("|---" * (len(cols) + 1) + "|")
        for system, r in posture.iterrows():
            w(f"| {system} | " + " | ".join(f"{r[t]:.1%}" for t in cols) + " |")
        w("")

        # Break-even sweep.
        treatment = spec["treatment"]
        w(f"### `{treatment}` vs each baseline across the frozen grid")
        w("")
        w("Cells where treatment is cheaper / total cells; the mean of per-cell")
        w("relative deltas (equal-weights every cell, over-weights near-zero-cost")
        w("cells — see the declared amendment); and the aggregate spend delta")
        w("(matches how the headline totals aggregate). Shown at the published")
        w("corner (10, 100, x1), the GPU-bench corner (1.52, 16.6, x1), and the")
        w("grid's worst point for the treatment by aggregate delta.")
        w("")
        w("| baseline | published corner | GPU corner | worst grid point | flips |")
        w("|---|---|---|---|---|")

        # Precompute per-run totals for every grid point.
        grid_totals = {}
        for r_mid in R_MID:
            for r_large in R_LARGE:
                for s in LEVEL:
                    grid_totals[(r_mid, r_large, s)] = reprice(per_run, r_mid, r_large, s)

        for baseline in spec["baselines"]:
            def cell_stats(point):
                totals = grid_totals[point]
                frame = ident.assign(total=totals).reset_index()
                t = frame[frame.system == treatment]
                b = frame[frame.system == baseline]
                m = t.merge(b, on=CELL, suffixes=("_t", "_b"))
                delta = (m.total_t - m.total_b) / m.total_b
                cheaper = int((m.total_t < m.total_b).sum())
                agg = float((m.total_t.sum() - m.total_b.sum()) / m.total_b.sum())
                return cheaper, len(m), float(delta.mean()), agg

            def fmt(st):
                return (f"{st[0]}/{st[1]} cheaper, cell-mean {st[2]:+.1%}, "
                        f"aggregate {st[3]:+.1%}")

            pub = cell_stats(PUBLISHED)
            gpu = cell_stats(GPU_CORNER)
            worst_point, worst = None, None
            flips_cell, flips_agg = [], []
            for point in grid_totals:
                st = cell_stats(point)
                if worst is None or st[3] > worst[3]:
                    worst_point, worst = point, st
                if st[2] > 0:
                    flips_cell.append((point, st[2]))
                if st[3] > 0:
                    flips_agg.append((point, st[3]))

            def flip_desc(flips):
                if not flips:
                    return "none in grid"
                return "; ".join(
                    f"(r_mid={p[0]:g}, r_large={p[1]:g}, s={p[2]:g}: {d:+.1%})"
                    for p, d in sorted(flips, key=lambda x: -x[1])[:6]
                )

            w(f"| {baseline} | {fmt(pub)} | {fmt(gpu)} "
              f"| (r_mid={worst_point[0]:g}, r_large={worst_point[1]:g}, "
              f"s={worst_point[2]:g}): {fmt(worst)} "
              f"| cell-mean: {flip_desc(flips_cell)}; aggregate: {flip_desc(flips_agg)} |")
        w("")

    w("## Reading")
    w("")
    w("The tier-share table is the structural answer: where the treatment's cost")
    w("win is dominated by infrastructure spend (replicas + cache memory), no")
    w("re-pricing of the tier axis can undo it; where tier spend is material, the")
    w("sweep shows how far the ratios must move before the sign changes. Flip")
    w("points, if any, are listed verbatim — including them is the point. All of")
    w("this holds decisions fixed; the pre-registered rerun measures what the")
    w("controllers do when they can *react* to the new economy.")
    w("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
