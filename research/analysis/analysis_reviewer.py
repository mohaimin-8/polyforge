"""PREREG_REVIEWER_FOLLOWUP.md -> RESULTS_REVIEWER_FOLLOWUP.md.

Campaign W (published plant): wide-step joint control against the autoscaler
with Kubernetes' default behavior and the other baselines. Campaign L
(live-fitted plant): the main comparisons. Plus an exploratory shed-price
sensitivity on the JOINTNESS data. FROZEN with the pre-registration; the test
rule is analysis_fair_j's, imported.

    python analysis_reviewer.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from analysis_fair_j import (  # noqa: E402
    ALPHA, BOOT_N, BOOT_SEED, DESIGN, complete_design_points, design_diffs, superiority,
)
from stats import composite_objective, holm_bonferroni, record_path  # noqa: E402

RESULTS = HERE.parents[1] / "eval" / "results"
RECORD = "RESULTS_REVIEWER_FOLLOWUP.md"
EQ_MARGIN = 0.025
COLUMNS = ["total_cost_usd", "mean_violation", "mean_jain", "mean_excess", "tier_none_step_share"]

CAMPAIGNS = {
    "W": {"db": "raw_sim_reviewer_wide.duckdb", "plant": "published",
          "arms": ["jcac_converged", "jcac_joint_wide_tracked", "hpa_fair_capped", "hpa_k8s_capped",
                   "layered_small_tuned", "static_share"],
          "tests": {"RV-W1": ("jcac_joint_wide_tracked", "hpa_k8s_capped"),
                    "RV-W2": ("jcac_joint_wide_tracked", "hpa_fair_capped"),
                    "RV-W3": ("jcac_joint_wide_tracked", "layered_small_tuned"),
                    "RV-W4": ("jcac_converged", "jcac_joint_wide_tracked")},
          "descriptive": [("default HPA − clamped HPA", "hpa_k8s_capped", "hpa_fair_capped"),
                          ("rate-limited JC − default HPA", "jcac_converged", "hpa_k8s_capped"),
                          ("rate-limited JC − clamped HPA", "jcac_converged", "hpa_fair_capped")]},
    "L": {"db": "raw_sim_reviewer_live.duckdb", "plant": "live-fitted",
          "arms": ["jcac_converged", "jcac_decoupled", "jcac_tier_floor", "hpa_fair_capped",
                   "keda_fair_capped", "layered_small_tuned", "static_share"],
          "tests": {"RV-L1": ("jcac_converged", "hpa_fair_capped"),
                    "RV-L2": ("jcac_converged", "keda_fair_capped"),
                    "RV-L3": ("jcac_converged", "layered_small_tuned"),
                    "RV-L4": ("jcac_converged", "static_share")},
          "equivalence": ("RV-L-EQ", "jcac_converged", "jcac_decoupled"),
          "descriptive": [("never shed − JC", "jcac_tier_floor", "jcac_converged"),
                          ("never shed − clamped HPA", "jcac_tier_floor", "hpa_fair_capped")]},
}
SHED_COMPARATORS = ["static_share", "keda_fair_capped", "hpa_fair_capped", "layered_small_tuned"]


def export_path(db: Path) -> Path:
    """The committed export the gate reads when the gitignored duckdb is absent."""
    return RESULTS / db.name.replace("raw_sim_", "metrics_").replace(".duckdb", ".csv.gz")


def load(db: Path) -> pd.DataFrame:
    if not db.exists():
        keep = ["system", "workload", "tenant_mix", "cluster_size", "rep", *COLUMNS]
        return pd.read_csv(export_path(db), float_precision="round_trip")[keep]
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(
            "select r.system, r.workload, r.tenant_mix, r.cluster_size, r.rep, "
            + ", ".join(f"m.{c}" for c in COLUMNS)
            + " from runs r join metrics m on r.run_id = m.run_id where r.status = 'valid'").fetchdf()
    finally:
        con.close()


def prepare(df: pd.DataFrame, arms: list[str]) -> pd.DataFrame:
    df = complete_design_points(df, arms)
    return df.assign(J=composite_objective(df))


def ci(r: dict) -> str:
    return f"[{r['ci'][0]:+.4f}, {r['ci'][1]:+.4f}]"


def reading(r: dict) -> str:
    if r["ci"][1] < 0:
        return "a better"
    if r["ci"][0] > 0:
        return "b better"
    return "no difference shown"


def campaign_section(key: str, spec: dict, df: pd.DataFrame) -> list[str]:
    n_all = df[DESIGN].drop_duplicates().shape[0]
    df = prepare(df, spec["arms"])
    n = df[DESIGN].drop_duplicates().shape[0]
    L = [f"## Campaign {key} ({spec['plant']} plant): {n} of {n_all} design points complete", "",
         "| arm | J | cost $ | violation | excess | Jain | AI shed |", "|---|---:|---:|---:|---:|---:|---:|"]
    for arm in spec["arms"]:
        s = df[df.system == arm]
        L.append(f"| `{arm}` | {s.J.mean():.4f} | {s.total_cost_usd.mean():.4f} | {s.mean_violation.mean():.4f} | "
                 f"{s.mean_excess.mean():.4f} | {s.mean_jain.mean():.4f} | {s.tier_none_step_share.mean():.1%} |")
    res = {hid: superiority(design_diffs(df, a, b, "J")) for hid, (a, b) in spec["tests"].items()}
    holm = holm_bonferroni({hid: r["p"] for hid, r in res.items()}, ALPHA)
    L += ["", f"### Confirmatory: {', '.join(spec['tests'])} (one Holm family)", "",
          "| id | a | b | design points | ΔJ = a − b | 95% CI | a better in | Wilcoxon p | Holm threshold | "
          "verdict | reading |", "|---|---|---|---:|---:|---|---:|---:|---:|---|---|"]
    for hid, (a, b) in spec["tests"].items():
        r = res[hid]
        ok = bool(holm[hid]["reject"] and r["ci"][1] < 0)
        L.append(f"| **{hid}** | `{a}` | `{b}` | {r['n']} | {r['mean']:+.4f} | {ci(r)} | {r['better']:.0%} | "
                 f"{r['p']:.3g} | {holm[hid]['threshold']:.4f} | {'PASS' if ok else 'FAIL'} | {reading(r)} |")
    if "equivalence" in spec:
        hid, a, b = spec["equivalence"]
        r = superiority(design_diffs(df, a, b, "J"))
        eq = -EQ_MARGIN < r["ci"][0] and r["ci"][1] < EQ_MARGIN
        L += ["", f"### Equivalence within ±{EQ_MARGIN} J", "", "| id | a | b | ΔJ | 95% CI | equivalent? |",
              "|---|---|---|---:|---|---|",
              f"| {hid} | `{a}` | `{b}` | {r['mean']:+.4f} | {ci(r)} | {'EQUIVALENT' if eq else 'not shown'} |"]
    L += ["", "### Descriptive contrasts (no test)", "", "| contrast | ΔJ | 95% CI | reading |",
          "|---|---:|---|---|"]
    for label, a, b in spec["descriptive"]:
        r = superiority(design_diffs(df, a, b, "J"))
        L.append(f"| {label} | {r['mean']:+.4f} | {ci(r)} | {reading(r)} |")
    return L + [""]


def shed_price_section() -> list[str]:
    """Exploratory: J' = J + kappa * s on JOINTNESS; the kappa at which each
    instant-actuation mean difference changes sign, with a bootstrap CI."""
    path = RESULTS / "metrics_matrix_jointness.csv.gz"
    df = pd.read_csv(path, float_precision="round_trip")
    arms = ["jcac_converged"] + SHED_COMPARATORS
    df = prepare(df, arms)
    L = ["## Exploratory: the shed price that erases each difference (JOINTNESS data)", "",
         "J' = J + κ·s, with s the AI shed step share (only the joint controller sheds). κ* is the price at "
         "which the mean ΔJ' crosses zero; its 95% CI resamples design points. Exploratory, not a test.", "",
         "| vs | ΔJ (κ = 0) | Δs | κ* | 95% CI of κ* | resamples with Δs ≤ 0 (dropped) |",
         "|---|---:|---:|---:|---|---:|"]
    rng = np.random.default_rng(BOOT_SEED)
    for comp in SHED_COMPARATORS:
        dj = design_diffs(df, "jcac_converged", comp, "J")
        ds = design_diffs(df, "jcac_converged", comp, "tier_none_step_share")
        kappa = -dj.mean() / ds.mean() if ds.mean() > 0 else float("nan")
        idx = rng.integers(0, len(dj), size=(BOOT_N, len(dj)))
        den = ds[idx].mean(axis=1)
        ok = den > 0  # a resample with no extra shedding has no crossing price
        lo, hi = np.percentile(-dj[idx].mean(axis=1)[ok] / den[ok], [2.5, 97.5])
        L.append(f"| `{comp}` | {dj.mean():+.4f} | {ds.mean():+.3f} | {kappa:+.3f} | [{lo:+.3f}, {hi:+.3f}] | "
                 f"{(~ok).sum()} |")
    return L + [""]


def build() -> str:
    L = ["# Reviewer follow-up: wide steps against the baselines, and the live-fitted plant", "",
         "Generated by `analysis_reviewer.py`, frozen with `PREREG_REVIEWER_FOLLOWUP.md`. Unit: the design "
         "point (reps averaged), listwise complete. PASS = one-sided Wilcoxon at its Holm threshold AND the "
         f"95% bootstrap CI of the mean ΔJ entirely below 0 ({BOOT_N:,} resamples, seed {BOOT_SEED}). "
         "Negative ΔJ = arm a is better.", ""]
    for key, spec in CAMPAIGNS.items():
        db = RESULTS / spec["db"]
        ran = db.exists() or export_path(db).exists()
        L += campaign_section(key, spec, load(db)) if ran else [f"## Campaign {key}: not run", ""]
    L += shed_price_section()
    return "\n".join(L) + "\n"


def main() -> int:
    out = record_path(RECORD)
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
