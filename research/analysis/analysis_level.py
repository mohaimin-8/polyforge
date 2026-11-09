"""PREREG_LEVEL.md -> RESULTS_LEVEL.md.

The level-playing-field campaign: every leak the final audit (2026-11-09)
found, removed on both sides, then the joint controller tested once more.
FROZEN with the pre-registration; the superiority rule is analysis_fair_j's,
imported (one-sided Wilcoxon at its Holm threshold AND the 95% bootstrap CI
of the mean design-point difference below 0).

The registered objective is J_L: the published J plus an outage price, every
refused AI request (tier "none" cache miss) charged the small tier's price,

    J_L = (cost + shed x p_small) / (steps x 8) / c0 + 2 v + 0.5 (1 - Jain),

with shed recovered exactly from the stored metrics (shed = served x s /
(1 - s), served = cost / cost-per-served; SHED_POOLED's identity) and each
run's own scored steps. The published J and J_L with the mid-tier price are
reported beside it, descriptively.

    python analysis_level.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from analysis_fair_j import ALPHA, BOOT_N, BOOT_SEED, DESIGN, NI_MARGIN, boot_means, design_diffs, superiority  # noqa: E402
from stats import holm_bonferroni, record_path  # noqa: E402

RESULTS = HERE.parents[1] / "eval" / "results"
RECORD = "RESULTS_LEVEL.md"
TREATMENT = "jcac_level"
DECOUPLED = "jcac_level_decoupled"
GATED = "layered_small_level_gated"
TENANTS, COST_SCALE_USD = 8, 0.01
P_SMALL, P_MID = 1.0e-4, 1.0e-3
EQ_MARGIN = 0.01        # registered coordination margin (tighter than JOINTNESS's 0.025)
EQ_MARGIN_OLD = 0.025   # JOINTNESS's margin, reported for continuity
ARMS_MAIN = [TREATMENT, DECOUPLED, "jcac_converged", GATED, "layered_small_level_capped",
             "hpa_level_capped", "keda_level_capped", "static_share"]
ARMS_TRACE = [a for a in ARMS_MAIN if a != "jcac_converged"]
# campaign -> (experiment name, arms, reps, expected valid runs)
CAMPAIGNS = {
    "LV": ("matrix_level", ARMS_MAIN, 5, 2400),
    "MO": ("matrix_level_mono", ARMS_MAIN, 5, 2400),
    "LG": ("matrix_level_lag", ARMS_MAIN, 5, 2400),
    "HO": ("matrix_level_heldout", ARMS_MAIN, 5, 960),
    "TR": ("matrix_level_trace", ARMS_TRACE, 1, 672),
}
LABEL = {"LV": "main matrix, published plant", "MO": "main matrix, monotone agent row",
         "LG": "main matrix, replicas one interval late", "HO": "held-out MMPP workloads",
         "TR": "real BurstGPT windows"}
# One Holm family of 11 (PREREG_LEVEL §Tests).
TESTS = {
    "LV-B1": ("LV", GATED),
    "LV-B2": ("LV", "hpa_level_capped"),
    "LV-B3": ("LV", "keda_level_capped"),
    "LV-B4": ("LV", "layered_small_level_capped"),
    "LV-B5": ("LV", "static_share"),
    "LV-P1": ("LV", "jcac_converged"),
    "LV-R1": ("MO", GATED),
    "LV-L1": ("LG", GATED),
    "LV-L2": ("LG", "hpa_level_capped"),
    "LV-O1": ("HO", GATED),
    "LV-T1": ("TR", GATED),
}
COLUMNS = ["total_cost_usd", "mean_violation", "mean_jain", "mean_excess", "tier_none_step_share",
           "budget_overrun_step_share", "shed_request_share", "ai_shed_request_share",
           "cost_per_served_request", "mean_quality_loss", "coordination_disagreement_share", "steps"]
KEYS = ["system"] + DESIGN + ["rep"]


def load(experiment: str) -> pd.DataFrame | None:
    db = RESULTS / f"raw_sim_{experiment.removeprefix('matrix_')}.duckdb"
    csv = RESULTS / f"metrics_{experiment}.csv.gz"
    if db.exists():
        import duckdb

        con = duckdb.connect(str(db), read_only=True)
        try:
            return con.execute(
                "select " + ", ".join(f"r.{c}" for c in KEYS) + ", " + ", ".join(f"m.{c}" for c in COLUMNS)
                + " from runs r join metrics m on r.run_id = m.run_id where r.status = 'valid'").fetchdf()
        finally:
            con.close()
    if csv.exists():
        return pd.read_csv(csv, float_precision="round_trip")[KEYS + COLUMNS]
    return None


def shed_requests(df: pd.DataFrame) -> pd.Series:
    s = df.shed_request_share.fillna(0.0)
    cps = df.cost_per_served_request.fillna(0.0)
    served = np.where(cps > 0.0, df.total_cost_usd / cps.where(cps > 0.0, 1.0), 0.0)
    out = np.where((s > 0.0) & (cps > 0.0), served * s / (1.0 - s), 0.0)
    return pd.Series(out, index=df.index)


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    norm = df.steps * TENANTS * COST_SCALE_USD
    shed = shed_requests(df)
    base = 2.0 * df.mean_violation + 0.5 * (1.0 - df.mean_jain)
    return df.assign(
        shed=shed,
        J=df.total_cost_usd / norm + base,
        J_L=(df.total_cost_usd + shed * P_SMALL) / norm + base,
        J_Lmid=(df.total_cost_usd + shed * P_MID) / norm + base,
    )


def complete(df: pd.DataFrame, arms: list[str], reps: int) -> pd.DataFrame:
    counts = df[df.system.isin(arms)].groupby(DESIGN + ["system"]).size().unstack("system")
    ok = counts.reindex(columns=arms).fillna(0).ge(reps).all(axis=1)
    keep = set(counts.index[ok])
    return df[[k in keep for k in zip(*(df[c] for c in DESIGN))]]


def ci(lo: float, hi: float) -> str:
    return f"[{lo:+.4f}, {hi:+.4f}]"


def effect(df: pd.DataFrame, a: str, b: str, metric: str) -> tuple[float, float, float]:
    d = design_diffs(df, a, b, metric)
    means = boot_means(d)
    return float(d.mean()), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def arm_table(df: pd.DataFrame, arms: list[str]) -> list[str]:
    L = ["| arm | J_L | J | J_L (mid price) | cost $ | violation | excess | Jain | AI shed (steps) | "
         "shed (requests) | budget overrun | $ per 1,000 served |",
         "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for arm in arms:
        s = df[df.system == arm]
        L.append(f"| `{arm}` | {s.J_L.mean():.4f} | {s.J.mean():.4f} | {s.J_Lmid.mean():.4f} | "
                 f"{s.total_cost_usd.mean():.3f} | {s.mean_violation.mean():.4f} | {s.mean_excess.mean():.4f} | "
                 f"{s.mean_jain.mean():.4f} | {s.tier_none_step_share.mean():.1%} | "
                 f"{s.shed_request_share.mean():.1%} | {s.budget_overrun_step_share.mean():.1%} | "
                 f"{1000 * s.cost_per_served_request.mean():.4f} |")
    return L


def build(frames: dict[str, tuple[pd.DataFrame, bool, str]]) -> str:
    L = ["# The level-playing-field campaign: the joint controller against comparators with every audited leak removed", "",
         "Generated by `analysis_level.py`, frozen with `PREREG_LEVEL.md`. Treatment `jcac_level`. Unit: the "
         "design point (reps averaged) or the window (TR), listwise complete over every arm. Primary objective "
         "J_L = J plus every refused AI request charged the small tier's price ($1e-4), each run's own scored steps. "
         f"PASS = one-sided Wilcoxon at its Holm threshold (one family of {len(TESTS)}) AND the 95% bootstrap CI "
         f"of the mean ΔJ_L below 0 ({BOOT_N:,} resamples, seed {BOOT_SEED}). Negative ΔJ_L = treatment better.", ""]
    prepared: dict[str, pd.DataFrame] = {}
    for key, (df, ok, line) in frames.items():
        experiment, arms, reps, _ = CAMPAIGNS[key]
        L += [f"## {key}: {LABEL[key]} (`{experiment}`)", "", f"**Validity: {'HOLDS' if ok else 'FAILS'}** — {line}", ""]
        n_all = df[DESIGN].drop_duplicates().shape[0]
        df = prepare(complete(df, arms, reps))
        prepared[key] = df
        n = df[DESIGN].drop_duplicates().shape[0]
        L += [f"{n} of {n_all} units complete.", ""] + arm_table(df, arms) + [""]
    # Confirmatory family.
    results = {}
    for hid, (key, comp) in TESTS.items():
        if key in prepared:
            results[hid] = (key, comp, superiority(design_diffs(prepared[key], TREATMENT, comp, "J_L")))
    holm = holm_bonferroni({hid: r["p"] for hid, (_, _, r) in results.items()}, ALPHA) if results else {}
    L += [f"## Confirmatory: LV-B1..B5, LV-P1, LV-R1, LV-L1, LV-L2, LV-O1, LV-T1 (one Holm family of {len(TESTS)})", "",
          "| id | campaign | vs | units | ΔJ_L | d_z | 95% CI | treatment better in | Wilcoxon p | Holm threshold | verdict |",
          "|---|---|---|---:|---:|---:|---|---:|---:|---:|---|"]
    for hid, (key, comp, r) in results.items():
        ok = bool(holm[hid]["reject"] and r["ci"][1] < 0)
        L.append(f"| **{hid}** | {key} | `{comp}` | {r['n']} | {r['mean']:+.4f} | {r['dz']:+.2f} | {ci(*r['ci'])} | "
                 f"{r['better']:.0%} | {r['p']:.3g} | {holm[hid]['threshold']:.4f} | {'PASS' if ok else 'FAIL'} |")
    for hid, (key, comp) in TESTS.items():
        if hid not in results:
            L.append(f"| {hid} | {key} | `{comp}` | — | — | — | — | — | — | — | NOT RUN |")
    # Coordination.
    L += ["", f"## Coordination: treatment − decoupled search on J_L (registered margin ±{EQ_MARGIN}; JOINTNESS's "
          f"±{EQ_MARGIN_OLD} for continuity)", "",
          "| id | campaign | ΔJ_L | 95% CI | gain, % of decoupled J_L | equivalent ±0.01 | equivalent ±0.025 | disagreement |",
          "|---|---|---:|---|---:|---|---|---:|"]
    for key, df in prepared.items():
        m, lo, hi = effect(df, TREATMENT, DECOUPLED, "J_L")
        base = df[df.system == DECOUPLED].J_L.mean()
        dis = df[df.system == TREATMENT].coordination_disagreement_share.mean()
        L.append(f"| LV-C[{key}] | {key} | {m:+.4f} | {ci(lo, hi)} | {-100 * m / base:+.1f}% | "
                 f"{'EQUIVALENT' if -EQ_MARGIN < lo and hi < EQ_MARGIN else 'not shown'} | "
                 f"{'EQUIVALENT' if -EQ_MARGIN_OLD < lo and hi < EQ_MARGIN_OLD else 'not shown'} | "
                 f"{'—' if pd.isna(dis) else f'{dis:.2%}'} |")
    # SLO guards and per-metric deltas for the primary campaign's comparisons.
    for key in prepared:
        df = prepared[key]
        comps = [c for (k, c) in TESTS.values() if k == key]
        if not comps:
            continue
        L += ["", f"## Per metric, treatment − comparator ({key}; descriptive)", "",
              "| vs | ΔJ (published) | Δcost $ | Δviolation | Δexcess | Δshed requests | Δbudget overrun | "
              f"excess non-inferior (+{NI_MARGIN})? |", "|---|---|---|---|---|---|---|---|"]
        for comp in comps:
            cells = [effect(df, TREATMENT, comp, m) for m in
                     ("J", "total_cost_usd", "mean_violation", "mean_excess", "shed_request_share",
                      "budget_overrun_step_share")]
            ucb = float(np.percentile(boot_means(design_diffs(df, TREATMENT, comp, "mean_excess")), 95.0))
            L.append(f"| `{comp}` | " + " | ".join(f"{m:+.4f} {ci(lo, hi)}" for m, lo, hi in cells)
                     + f" | {'PASS' if ucb < NI_MARGIN else 'FAIL'} (UB {ucb:+.4f}) |")
    # Where the result lives (descriptive).
    for key in ("LV", "LG"):
        if key not in prepared:
            continue
        df = prepared[key]
        L += ["", f"## Per cluster size and per workload class, treatment − comparator on J_L ({key}; descriptive)", "",
              "| cut | vs | units | ΔJ_L | 95% CI |", "|---|---|---:|---:|---|"]
        cuts = [("size=" + s, df[df.cluster_size == s]) for s in ("small", "medium", "large")]
        cuts += [("workload=" + w, df[df.workload == w]) for w in sorted(df.workload.unique())]
        ai = df[~df.workload.str.startswith("crud")]
        cuts += [("AI workloads only", ai)]
        for name, sub in cuts:
            for comp in (GATED, "hpa_level_capped"):
                m, lo, hi = effect(sub, TREATMENT, comp, "J_L")
                n = sub[DESIGN].drop_duplicates().shape[0]
                L.append(f"| {name} | `{comp}` | {n} | {m:+.4f} | {ci(lo, hi)} |")
    return "\n".join(L) + "\n"


def main() -> int:
    frames = {}
    for key, (experiment, arms, reps, expected) in CAMPAIGNS.items():
        df = load(experiment)
        if df is None:
            continue
        valid = len(df[df.system.isin(arms)])
        ok = valid == expected
        frames[key] = (df, ok, f"{valid:,} valid runs of {expected:,} expected")
    out = record_path(RECORD)
    if not frames:
        out.write_text("# Level-playing-field campaign\n\nNo campaign has run.\n", encoding="utf-8")
    else:
        out.write_text(build(frames), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
