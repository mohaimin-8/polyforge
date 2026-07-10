"""W36 statistical analysis over the harness result databases.

Loads run-level metrics from DuckDB (valid runs only, by construction of
the metrics table), and computes:
- two-way ANOVA (system x workload) per metric, with partial eta^2
- Cohen's d for PolyForge vs every baseline, per metric
- 95% t-confidence intervals per system per metric
- ablation contribution table (full JCAC vs each jcac_no* variant)

Every number the paper's evaluation section quotes comes from here;
figures.py consumes the same frames so tables and plots cannot drift.
"""

from __future__ import annotations

import math
from pathlib import Path

import duckdb
import pandas as pd
from scipy import stats as sps
import statsmodels.api as sm
from statsmodels.formula.api import ols

REPO_ROOT = Path(__file__).resolve().parents[2]
FULL_DB = REPO_ROOT / "eval" / "results" / "raw_sim.duckdb"
ABLATIONS_DB = REPO_ROOT / "eval" / "results" / "ablations.duckdb"
FORECASTERS_DB = REPO_ROOT / "eval" / "results" / "forecasters.duckdb"
REALISM_DB = REPO_ROOT / "eval" / "results" / "realism.duckdb"

METRICS = [
    "total_cost_usd",
    "mean_violation",
    "violation_step_share",
    "mean_jain",
    "cache_hit_rate",
]
# Direction of "better": +1 higher is better, -1 lower is better.
BETTER = {
    "total_cost_usd": -1,
    "mean_violation": -1,
    "violation_step_share": -1,
    "mean_jain": 1,
    "cache_hit_rate": 1,
    "crud_p95_ms": -1,
    "ai_p95_ms": -1,
}
BASELINES = ["hpa", "keda", "firm", "static", "gptcache"]
ABLATIONS = ["jcac_noclassifier", "jcac_nojoint", "jcac_noeviction", "jcac_nofairness"]


def load_runs(db_path: Path = FULL_DB) -> pd.DataFrame:
    con = duckdb.connect(str(db_path), read_only=True)
    df = con.execute(
        """
        SELECT r.system, r.workload, r.tenant_mix, r.cluster_size, r.rep,
               m.total_cost_usd, m.mean_violation, m.violation_step_share,
               m.mean_jain, m.cache_hit_rate, m.crud_p95_ms, m.ai_p95_ms
        FROM metrics m JOIN runs r USING (run_id)
        WHERE r.status = 'valid'
        ORDER BY r.system, r.workload, r.tenant_mix, r.cluster_size, r.rep
        """
    ).df()
    con.close()
    return df


def load_timeseries(db_path: Path, **filters) -> pd.DataFrame:
    where = " AND ".join(f"r.{k} = ?" for k in filters)
    con = duckdb.connect(str(db_path), read_only=True)
    df = con.execute(
        f"""
        SELECT r.system, r.workload, r.tenant_mix, t.step, t.tenant,
               t.replicas, t.cache_mb, t.tier, t.cost_usd, t.violation
        FROM timeseries t JOIN runs r USING (run_id)
        WHERE r.status = 'valid' AND {where}
        ORDER BY t.step, t.tenant
        """,
        list(filters.values()),
    ).df()
    con.close()
    return df


def two_way_anova(df: pd.DataFrame, metric: str) -> dict:
    """system x workload ANOVA. Type II sums of squares (balanced-ish
    design; robust to the mild imbalance a failed run would introduce)."""
    frame = df[["system", "workload", metric]].rename(columns={metric: "y"})
    model = ols("y ~ C(system) * C(workload)", data=frame).fit()
    table = sm.stats.anova_lm(model, typ=2)
    ss_err = table.loc["Residual", "sum_sq"]
    out = {}
    for term in ("C(system)", "C(workload)", "C(system):C(workload)"):
        ss = table.loc[term, "sum_sq"]
        out[term] = {
            "F": float(table.loc[term, "F"]),
            "p": float(table.loc[term, "PR(>F)"]),
            "partial_eta_sq": float(ss / (ss + ss_err)),
        }
    return out


def cohens_d(a: pd.Series, b: pd.Series) -> float:
    """Unpaired Cohen's d with pooled SD; sign is (a - b)."""
    na, nb = len(a), len(b)
    va, vb = a.var(ddof=1), b.var(ddof=1)
    pooled = math.sqrt(((na - 1) * va + (nb - 1) * vb) / (na + nb - 2))
    if pooled == 0.0:
        return 0.0
    return float((a.mean() - b.mean()) / pooled)


def ci95(values: pd.Series) -> tuple[float, float, float]:
    """(mean, lo, hi) t-based 95% CI."""
    n = len(values)
    mean = float(values.mean())
    if n < 2:
        return mean, mean, mean
    half = float(sps.t.ppf(0.975, n - 1) * values.std(ddof=1) / math.sqrt(n))
    return mean, mean - half, mean + half


def system_summary(df: pd.DataFrame, metrics: list[str] | None = None) -> pd.DataFrame:
    """Per-system mean and 95% CI for each metric, over all matrix cells."""
    rows = []
    for system, group in df.groupby("system"):
        row = {"system": system, "n": len(group)}
        for m in metrics or METRICS + ["crud_p95_ms", "ai_p95_ms"]:
            mean, lo, hi = ci95(group[m])
            row[m], row[f"{m}_lo"], row[f"{m}_hi"] = mean, lo, hi
        rows.append(row)
    return pd.DataFrame(rows).set_index("system")


CELL_KEYS = ["workload", "tenant_mix", "cluster_size", "rep"]


def headline_comparison(df: pd.DataFrame, treatment: str = "jcac",
                        baselines: list[str] | None = None) -> pd.DataFrame:
    """PolyForge vs each baseline on each metric.

    The matrix is a blocked factorial design: every (workload, mix,
    cluster, rep) cell contains exactly one run per system, so the
    appropriate test is *paired by cell* — a one-sample t on the per-cell
    differences with Cohen's d_z = mean(diff)/sd(diff). Pooled unpaired
    statistics are reported alongside for transparency, but they answer a
    weaker question (they are dominated by between-cell variance: cost
    alone spans an order of magnitude across cluster sizes).
    """
    rows = []
    t_vals = df[df.system == treatment]
    for baseline in baselines or BASELINES:
        b_vals = df[df.system == baseline]
        merged = t_vals.merge(b_vals, on=CELL_KEYS, suffixes=("_t", "_b"))
        for m in METRICS:
            diff = merged[f"{m}_t"] - merged[f"{m}_b"]
            sd = diff.std(ddof=1)
            if sd == 0.0:
                t_stat, p, dz = 0.0, 1.0, 0.0
            else:
                t_stat, p = sps.ttest_1samp(diff, 0.0)
                dz = float(diff.mean() / sd)
            t_un, p_un = sps.ttest_ind(t_vals[m], b_vals[m], equal_var=False)
            d_un = cohens_d(t_vals[m], b_vals[m])
            better = diff.mean() * BETTER[m] > 0
            rows.append({
                "baseline": baseline, "metric": m, "pairs": len(merged),
                "jcac_mean": t_vals[m].mean(), "baseline_mean": b_vals[m].mean(),
                "t": float(t_stat), "p": float(p), "cohens_dz": dz,
                "pooled_p": float(p_un), "pooled_d": d_un,
                "jcac_better": bool(better),
                "significant_large": bool(better and p < 0.01 and abs(dz) >= 0.5),
            })
    return pd.DataFrame(rows)


def paired_summary(df: pd.DataFrame, systems: list[str], baseline: str,
                   metric: str) -> pd.DataFrame:
    """Per-system paired-by-cell comparison vs `baseline` on one metric,
    over whatever cells the given experiment defines. Reused by the
    forecast ablation and the realism experiment."""
    b = df[df.system == baseline]
    rows = []
    for system in systems:
        s = df[df.system == system]
        merged = s.merge(b, on=CELL_KEYS, suffixes=("_s", "_b"))
        diff = merged[f"{metric}_s"] - merged[f"{metric}_b"]
        sd = diff.std(ddof=1)
        if len(merged) < 2 or sd == 0.0:
            t_stat, p, dz = 0.0, 1.0, 0.0
        else:
            t_stat, p = sps.ttest_1samp(diff, 0.0)
            dz = float(diff.mean() / sd)
        rows.append({
            "system": system, "mean": float(s[metric].mean()),
            "vs_baseline_rel": float((s[metric].mean() - b[metric].mean())
                                     / abs(b[metric].mean())) if b[metric].mean() else 0.0,
            "p": float(p), "cohens_dz": dz,
        })
    return pd.DataFrame(rows).set_index("system")


def composite_objective(df: pd.DataFrame) -> pd.Series:
    """The paper's stated objective J = cost_norm + 2·violation +
    0.5·(1−Jain), cost normalized per scored tenant-step (119 × 8) by the
    controller's COST_SCALE_USD — identical to the W34 tuning objective."""
    return (df.total_cost_usd / (119 * 8) / 0.01
            + 2.0 * df.mean_violation + 0.5 * (1.0 - df.mean_jain))


def objective_comparison(df: pd.DataFrame, treatment: str = "jcac") -> pd.DataFrame:
    """Paired-by-cell comparison on the composite objective — the primary
    headline claim: does joint control win on the stated joint objective?"""
    frame = df.assign(J=composite_objective(df))
    t_vals = frame[frame.system == treatment]
    rows = []
    for baseline in BASELINES:
        merged = t_vals.merge(frame[frame.system == baseline],
                              on=CELL_KEYS, suffixes=("_t", "_b"))
        diff = merged.J_t - merged.J_b
        t_stat, p = sps.ttest_1samp(diff, 0.0)
        rows.append({
            "baseline": baseline, "pairs": len(merged),
            "J_jcac": float(merged.J_t.mean()), "J_baseline": float(merged.J_b.mean()),
            "p": float(p), "cohens_dz": float(diff.mean() / diff.std(ddof=1)),
        })
    return pd.DataFrame(rows)


def ablation_table(df_abl: pd.DataFrame) -> pd.DataFrame:
    """Marginal contribution of each component: full JCAC vs each
    ablation, paired by cell (same blocked design as the headline)."""
    rows = []
    full = df_abl[df_abl.system == "jcac"]
    for ablation in ABLATIONS:
        v = df_abl[df_abl.system == ablation]
        if v.empty:
            continue
        merged = v.merge(full, on=CELL_KEYS, suffixes=("_a", "_f"))
        for m in METRICS:
            diff = merged[f"{m}_a"] - merged[f"{m}_f"]  # ablation minus full
            sd = diff.std(ddof=1)
            if sd == 0.0:
                t_stat, p, dz = 0.0, 1.0, 0.0
            else:
                t_stat, p = sps.ttest_1samp(diff, 0.0)
                dz = float(diff.mean() / sd)
            # Removing a component "hurts" if the ablation is worse.
            hurts = diff.mean() * BETTER[m] < 0
            rel = (v[m].mean() - full[m].mean()) / abs(full[m].mean()) if full[m].mean() else 0.0
            rows.append({
                "ablation": ablation, "metric": m,
                "full_mean": full[m].mean(), "ablated_mean": v[m].mean(),
                "relative_change": float(rel), "t": float(t_stat), "p": float(p),
                "cohens_dz_removal": dz, "removal_hurts": bool(hurts),
                "significant": bool(p < 0.01),
            })
    return pd.DataFrame(rows)
