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
import os
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

# Committed run-level exports (eval/scripts/export_metrics_csv.py). The raw
# DuckDB files carry the timeseries and are Zenodo-archived, not committed;
# these gzipped CSVs live in git so a clean clone can re-derive every
# run-level statistic and figure without the archives.
_RESULTS_DIR = REPO_ROOT / "eval" / "results"
CSV_EXPORTS = {
    FULL_DB: _RESULTS_DIR / "metrics_full.csv.gz",
    ABLATIONS_DB: _RESULTS_DIR / "metrics_ablations.csv.gz",
    FORECASTERS_DB: _RESULTS_DIR / "metrics_forecasters.csv.gz",
    REALISM_DB: _RESULTS_DIR / "metrics_realism.csv.gz",
    # Every other campaign whose analysis reads run-level metrics. Same rule
    # as above: the DuckDBs are Zenodo-archived, these committed exports are
    # what a clean clone re-derives the records from. (The two live Phase 7
    # databases have no export — their records are live measurements, not
    # re-derivable at the desk.)
    _RESULTS_DIR / "raw_sim_v2.duckdb": _RESULTS_DIR / "metrics_matrix_v2.csv.gz",
    _RESULTS_DIR / "raw_sim_v3.duckdb": _RESULTS_DIR / "metrics_matrix_v3_overload.csv.gz",
    _RESULTS_DIR / "vtc_fairness.duckdb": _RESULTS_DIR / "metrics_vtc_fairness.csv.gz",
    _RESULTS_DIR / "fairness_v2.duckdb": _RESULTS_DIR / "metrics_fairness_v2.csv.gz",
    _RESULTS_DIR / "isocost.duckdb": _RESULTS_DIR / "metrics_isocost.csv.gz",
    _RESULTS_DIR / "chaos_sim.duckdb": _RESULTS_DIR / "metrics_chaos_sim.csv.gz",
    _RESULTS_DIR / "raw_sim_clamp.duckdb": _RESULTS_DIR / "metrics_matrix_clamp.csv.gz",
    # Wave 2 economy + Wave 5 structural-form reruns (analysis_econ.py).
    _RESULTS_DIR / "raw_sim_gpu_econ.duckdb": _RESULTS_DIR / "metrics_matrix_gpu_econ.csv.gz",
    _RESULTS_DIR / "raw_sim_hk.duckdb": _RESULTS_DIR / "metrics_matrix_hk.csv.gz",
    _RESULTS_DIR / "raw_sim_lm.duckdb": _RESULTS_DIR / "metrics_matrix_lm.csv.gz",
    _RESULTS_DIR / "raw_sim_mixp95.duckdb": _RESULTS_DIR / "metrics_matrix_mixp95.csv.gz",
    _RESULTS_DIR / "raw_sim_tierwu.duckdb": _RESULTS_DIR / "metrics_matrix_tierwu.csv.gz",
}

# The order `load_runs`'s SQL imposes. The CSV branch re-applies it because
# exports are written in the DuckDB's own row order (see
# eval/scripts/export_metrics_csv.py), which is not this one; sorting here
# keeps the two branches identical row-for-row. Keys are unique per run, so
# the sort is total.
_RUN_ORDER = ["system", "workload", "tenant_mix", "cluster_size", "rep"]

RUN_COLUMNS = [
    "system", "workload", "tenant_mix", "cluster_size", "rep",
    "total_cost_usd", "mean_violation", "violation_step_share",
    "mean_jain", "cache_hit_rate", "crud_p95_ms", "ai_p95_ms",
]

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


def record_path(filename: str) -> Path:
    """Where a generated analysis record is written: research/analysis by
    default, or $POLYFORGE_ANALYSIS_OUT when scripts/reproduce.py redirects
    output so the frozen committed records are never overwritten."""
    base = (Path(os.environ["POLYFORGE_ANALYSIS_OUT"])
            if "POLYFORGE_ANALYSIS_OUT" in os.environ
            else Path(__file__).resolve().parent)
    return base / filename


def runs_available(db_path: Path) -> bool:
    """True when this campaign's run-level metrics are loadable — from the
    DuckDB if present, else from its committed csv.gz export."""
    export = CSV_EXPORTS.get(db_path)
    return db_path.exists() or (export is not None and export.exists())


def load_runs(db_path: Path = FULL_DB) -> pd.DataFrame:
    if not db_path.exists():
        export = CSV_EXPORTS.get(db_path)
        if export is not None and export.exists():
            df = pd.read_csv(export)[RUN_COLUMNS]
            for col in RUN_COLUMNS[5:]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            return (df.sort_values(_RUN_ORDER, kind="mergesort")
                      .reset_index(drop=True))
        raise FileNotFoundError(
            f"{db_path} missing and no committed csv.gz export covers it; "
            "run the experiment or fetch the Zenodo archive")
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


# The run-level projection every post-v3 matrix campaign scores on
# (concurrency, learned, risk, risk_budget, tenant_scale). `steps` is read
# from the metrics table in the DuckDB path and from the runs table in the
# export; they are the same value (verified equal on every valid row).
CAMPAIGN_RUN_COLUMNS = [
    "system", "workload", "tenant_mix", "cluster_size", "rep",
    "total_cost_usd", "mean_violation", "mean_jain", "steps",
]


def load_campaign_runs(db_path: Path, csv_path: Path) -> pd.DataFrame:
    """Run-level rows for one campaign matrix: from its DuckDB when that is
    present, else from its committed csv.gz export.

    The DuckDB files carry the timeseries and are Zenodo-archived rather than
    committed, so without this fallback a clean clone could not re-derive
    these campaigns' records at all. Both paths return the same columns for
    the same `status = 'valid'` rows; callers compute their own J on top.

    Row *order* is part of the contract, not an incidental detail: several
    of these campaigns report bootstrap CIs, and resampling under a fixed
    seed reads the rows positionally, so a different order shifts the
    published CI bounds. `eval/scripts/export_metrics_csv.py` therefore
    writes the export in the DuckDB's own row order and neither path sorts,
    which is what makes the two byte-identical.
    """
    if db_path.exists():
        con = duckdb.connect(str(db_path), read_only=True)
        df = con.execute(
            "select r.system, r.workload, r.tenant_mix, r.cluster_size, r.rep, "
            "m.total_cost_usd, m.mean_violation, m.mean_jain, m.steps "
            "from runs r join metrics m on r.run_id = m.run_id "
            "where r.status = 'valid'"
        ).fetchdf()
        con.close()
        return df
    if csv_path.exists():
        # Exports carry only valid rows, so there is no status filter here.
        df = pd.read_csv(csv_path)[CAMPAIGN_RUN_COLUMNS]
        for col in ("total_cost_usd", "mean_violation", "mean_jain", "steps"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    raise FileNotFoundError(
        f"{db_path.name} missing and its committed export {csv_path.name} is "
        "absent too; run the campaign or fetch the Zenodo archive")


# Columns load_timeseries returns, in order.
_TS_COLUMNS = ["system", "workload", "tenant_mix", "step", "tenant",
               "replicas", "cache_mb", "tier", "cost_usd", "violation"]
# The one timeseries slice committed to git: the two runs fig09 plots.
# Full timeseries stay in the Zenodo archives — this is a named exception,
# not a general fallback, so anything outside it still fails loudly.
_TS_SLICE_CSV = _RESULTS_DIR / "agg_fig09_timeseries_slices.csv.gz"


def load_timeseries(db_path: Path, **filters) -> pd.DataFrame:
    if not db_path.exists():
        if db_path == FULL_DB and _TS_SLICE_CSV.exists():
            df = pd.read_csv(_TS_SLICE_CSV)
            for key, value in filters.items():
                if key not in df.columns:
                    df = df.iloc[0:0]
                    break
                df = df[df[key].astype(str) == str(value)]
            if not df.empty:
                for col in ("step", "tenant", "replicas", "cache_mb",
                            "cost_usd", "violation"):
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                return (df.sort_values(["step", "tenant"], kind="mergesort")
                          [_TS_COLUMNS].reset_index(drop=True))
            raise FileNotFoundError(
                f"{db_path.name}: timeseries for {filters} are not in the "
                f"committed slice ({_TS_SLICE_CSV.name} covers only the runs "
                "fig09 plots); fetch the Zenodo archive")
        raise FileNotFoundError(
            f"{db_path.name}: timeseries exist only in the DuckDB archives "
            "(Zenodo deposit), not in the committed csv.gz exports")
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
