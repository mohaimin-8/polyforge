"""Analysis for the Wave 2 economy reruns (PREREG_TIER_RATIO.md /
PREREG_HK_ADOPTION.md). Tests, pairing and alpha are frozen in the preregs;
this script only mechanizes them (analysis_v2.py precedent).

  python analysis_econ.py tier   -> RESULTS_TIER_RATIO.md
  python analysis_econ.py hk     -> RESULTS_HK_ADOPTION.md

Primary/secondary tests per prereg: per-cell pairing on CELL_KEYS
(workload, tenant_mix, cluster_size, rep) = 300 pairs per baseline,
one-sample t on paired differences, alpha 0.01. Everything else in the
output is descriptive context, labeled as such.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd
from scipy import stats as sps

import stats

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "eval" / "results"
ALPHA = 0.01
BASELINES = ["hpa", "keda", "firm", "static", "gptcache"]

CAMPAIGNS = {
    "tier": {
        "db": "raw_sim_gpu_econ.duckdb",
        "out": "RESULTS_TIER_RATIO.md",
        "title": "Tier-price ratio rerun (GPU corner 1:1.516:16.64) — as measured",
        "prereg": "PREREG_TIER_RATIO.md",
        "primary_metric": "total_cost_usd",
        "primary_name": "TR-H1 cost win vs every baseline",
        "secondary_metric": "J",
        "secondary_name": "TR-H2 composite-J win vs every baseline",
        # Frozen accounting predictions (BREAKEVEN_TIER.md, GPU corner,
        # aggregate rep-0 deltas) quoted in the prereg.
        "accounting": {"hpa": -0.402, "keda": -0.459, "firm": -0.431,
                        "static": -0.600, "gptcache": -0.830},
    },
    "hk": {
        "db": "raw_sim_hk.duckdb",
        "out": "RESULTS_HK_ADOPTION.md",
        "title": "Empirical cache-curve rerun (hmax 0.285, half 19 MB) — as measured",
        "prereg": "PREREG_HK_ADOPTION.md",
        "primary_metric": "J",
        "primary_name": "HK-H1 composite-J win vs every baseline",
        "secondary_metric": "total_cost_usd",
        "secondary_name": "HK-H2 cost win vs every baseline",
        "accounting": None,
    },
}


def paired(df: pd.DataFrame, baseline: str, metric: str) -> dict:
    t = df[df.system == "jcac"]
    b = df[df.system == baseline]
    m = t.merge(b, on=stats.CELL_KEYS, suffixes=("_t", "_b"))
    diff = m[f"{metric}_t"] - m[f"{metric}_b"]
    sd = diff.std(ddof=1)
    tstat, p = sps.ttest_1samp(diff, 0.0)
    agg = float((m[f"{metric}_t"].sum() - m[f"{metric}_b"].sum())
                / m[f"{metric}_b"].sum()) if m[f"{metric}_b"].sum() else float("nan")
    return {
        "pairs": len(m), "mean": float(diff.mean()),
        "dz": float(diff.mean() / sd) if sd else 0.0, "p": float(p),
        "agg": agg, "win": bool(diff.mean() < 0 and p < ALPHA),
    }


def tier_posture(db_path: Path) -> pd.DataFrame:
    con = duckdb.connect(str(db_path), read_only=True)
    df = con.execute(
        """
        SELECT r.system, t.tier, COUNT(*) AS n,
               AVG(t.cache_mb) AS mean_cache_mb
        FROM timeseries t JOIN runs r USING (run_id)
        WHERE r.status = 'valid'
        GROUP BY r.system, t.tier
        """
    ).df()
    con.close()
    return df


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "tier"
    spec = CAMPAIGNS[which]
    db = RESULTS_DIR / spec["db"]
    df = stats.load_runs(db)
    df = df.assign(J=stats.composite_objective(df))
    ref = stats.load_runs(RESULTS_DIR / "raw_sim.duckdb")
    ref = ref.assign(J=stats.composite_objective(ref))

    lines: list[str] = []
    w = lines.append
    w(f"# {spec['title']}")
    w("")
    w(f"Campaign of `{spec['prereg']}` (pushed before the run); "
      f"database `{spec['db']}`, {len(df)} valid runs. Tests and pairing are the")
    w("frozen ones: 300 per-cell pairs per baseline, one-sample t on paired")
    w(f"differences, alpha {ALPHA}. The v1 headline matrix (published economy) is")
    w("quoted beside each number for context, never in place of it.")
    w("")

    for role in ("primary", "secondary"):
        metric = spec[f"{role}_metric"]
        w(f"## {spec[f'{role}_name']} ({role}, metric `{metric}`)")
        w("")
        w("| baseline | pairs | mean paired diff | dz | p | aggregate delta "
          "| v1 aggregate delta | verdict |")
        w("|---|---|---|---|---|---|---|---|")
        outcomes = []
        for baseline in BASELINES:
            r = paired(df, baseline, metric)
            r_ref = paired(ref, baseline, metric)
            outcomes.append(r["win"])
            w(f"| {baseline} | {r['pairs']} | {r['mean']:+.4f} | {r['dz']:+.2f} "
              f"| {r['p']:.2g} | {r['agg']:+.1%} | {r_ref['agg']:+.1%} "
              f"| {'WIN' if r['win'] else 'NO WIN'} |")
        verdict = "PASS" if all(outcomes) else "FAIL"
        w("")
        w(f"**{role.capitalize()} hypothesis: {verdict}** "
          f"({sum(outcomes)}/{len(outcomes)} baselines beaten at p < {ALPHA}).")
        w("")

    if spec["accounting"]:
        w("## Decision response vs the accounting prediction (descriptive)")
        w("")
        w("The prereg froze BREAKEVEN_TIER.md's fixed-decision aggregate deltas at")
        w("this corner; the gap to the rerun's aggregate is the measured effect of")
        w("controllers re-planning under the new prices.")
        w("")
        w("| baseline | accounting (decisions frozen) | rerun (decisions live) | gap |")
        w("|---|---|---|---|")
        for baseline in BASELINES:
            r = paired(df, baseline, "total_cost_usd")
            acc = spec["accounting"][baseline]
            w(f"| {baseline} | {acc:+.1%} | {r['agg']:+.1%} | {r['agg']-acc:+.1%} |")
        w("")

    w("## System-level context (descriptive)")
    w("")
    w("| system | mean cost/run | mean violation | mean Jain | cache hit rate "
      "| v1 cache hit rate |")
    w("|---|---|---|---|---|---|")
    agg = df.groupby("system")[
        ["total_cost_usd", "mean_violation", "mean_jain", "cache_hit_rate"]
    ].mean()
    ref_agg = ref.groupby("system").cache_hit_rate.mean()
    for system in ["jcac"] + BASELINES:
        a = agg.loc[system]
        w(f"| {system} | {a.total_cost_usd:.3f} | {a.mean_violation:.4f} "
          f"| {a.mean_jain:.4f} | {a.cache_hit_rate:.3f} | {ref_agg[system]:.3f} |")
    w("")

    w("## Tier / cache posture, rep-0 rows (descriptive)")
    w("")
    post = tier_posture(db)
    total = post.groupby("system").n.sum()
    w("| system | " + " | ".join(f"steps @ {t}" for t in ("none", "small", "mid", "large"))
      + " | mean cache MB |")
    w("|---|---|---|---|---|---|")
    for system in ["jcac"] + BASELINES:
        sub = post[post.system == system].set_index("tier")
        shares = [f"{(sub.n.get(t, 0) / total[system]):.1%}"
                  for t in ("none", "small", "mid", "large")]
        cache = (sub.n * sub.mean_cache_mb).sum() / total[system]
        w(f"| {system} | " + " | ".join(shares) + f" | {cache:.0f} |")
    w("")

    out = Path(__file__).resolve().parent / spec["out"]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
