"""Analysis for the Wave 2 economy reruns (PREREG_TIER_RATIO.md /
PREREG_HK_ADOPTION.md) and the Wave 5 structural-form reruns
(PREREG_LM_ADOPTION.md / PREREG_MIXTURE_P95.md / PREREG_TIER_WU.md).
Tests, pairing and alpha are frozen in the preregs; this script only
mechanizes them (analysis_v2.py precedent).

  python analysis_econ.py tier    -> RESULTS_TIER_RATIO.md
  python analysis_econ.py hk      -> RESULTS_HK_ADOPTION.md
  python analysis_econ.py lm      -> RESULTS_LM_ADOPTION.md
  python analysis_econ.py mixp95  -> RESULTS_MIXTURE_P95.md
  python analysis_econ.py tierwu  -> RESULTS_TIER_WU.md

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
    "lm": {
        "db": "raw_sim_lm.duckdb",
        "out": "RESULTS_LM_ADOPTION.md",
        "title": "Measured latency-model rerun (a 0.86, tail 1.6909·(1−ρ)^−0.1303) — as measured",
        "prereg": "PREREG_LM_ADOPTION.md",
        "primary_metric": "J",
        "primary_name": "LM-H1 composite-J win vs every baseline",
        "secondary_metric": "total_cost_usd",
        "secondary_name": "LM-H2 cost win vs every baseline",
        "accounting": None,
        # LM-H3: violation non-inferiority vs the tuned reactive scalers.
        # PASS iff the one-sided 99% upper bound of the paired mean-violation
        # diff stays within (v1 paired diff + margin), per baseline.
        "noninferiority": {"tag": "LM-H3", "metric": "mean_violation",
                           "baselines": ["hpa", "keda"], "margin": 0.02},
    },
    "mixp95": {
        "db": "raw_sim_mixp95.duckdb",
        "out": "RESULTS_MIXTURE_P95.md",
        "title": "Mixture-percentile rerun (ai_p95 = true hit/miss mixture p95) — as measured",
        "prereg": "PREREG_MIXTURE_P95.md",
        "primary_metric": "J",
        "primary_name": "MX-H1 composite-J win vs every baseline",
        "secondary_metric": "total_cost_usd",
        "secondary_name": "MX-H2 cost win vs every baseline",
        "accounting": None,
        "noninferiority": {"tag": "MX-H3", "metric": "mean_violation",
                           "baselines": ["hpa", "keda"], "margin": 0.02},
    },
    "clamp": {
        "db": "raw_sim_clamp.duckdb",
        "out": "RESULTS_MOVE_CLAMP.md",
        "title": "Clamp-fixed controller rerun (moves anchored at interval start) — as measured",
        "prereg": "PREREG_MOVE_CLAMP.md",
        "treatment": "jcac_anchored",
        "primary_metric": "J",
        "primary_name": "MC-H1 composite-J win vs every baseline (clamp-fixed jcac)",
        "secondary_metric": "total_cost_usd",
        "secondary_name": "MC-H2 cost win vs every baseline",
        "accounting": None,
        "noninferiority": {"tag": "MC-H3", "metric": "mean_violation",
                           "baselines": ["hpa", "keda"], "margin": 0.02},
    },
    "tierwu": {
        "db": "raw_sim_tierwu.duckdb",
        "out": "RESULTS_TIER_WU.md",
        "title": "Tier-scaled work-unit rerun (mid 1.516×, large 16.64×) — as measured",
        "prereg": "PREREG_TIER_WU.md",
        "primary_metric": "J",
        "primary_name": "TW-H1 composite-J win vs every baseline",
        "secondary_metric": "total_cost_usd",
        "secondary_name": "TW-H2 cost win vs every baseline",
        "accounting": None,
    },
}


def paired(df: pd.DataFrame, baseline: str, metric: str,
           treatment: str = "jcac") -> dict:
    t = df[df.system == treatment]
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
            r = paired(df, baseline, metric, spec.get("treatment", "jcac"))
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

    if spec.get("noninferiority"):
        ni = spec["noninferiority"]
        w(f"## {ni['tag']} violation non-inferiority vs tuned reactive scalers")
        w("")
        w(f"PASS per baseline iff the one-sided 99% upper confidence bound of the")
        w(f"paired `{ni['metric']}` diff (jcac − baseline) stays within the v1")
        w(f"paired diff + {ni['margin']:.2f} (margin frozen in the prereg). The v1")
        w("reference is recomputed live from the committed `raw_sim.duckdb`.")
        w("")
        w("| baseline | pairs | paired diff | 99% UB (one-sided) | v1 paired diff "
          "| bound (v1 + margin) | verdict |")
        w("|---|---|---|---|---|---|---|")
        outcomes = []
        for baseline in ni["baselines"]:
            t = df[df.system == spec.get("treatment", "jcac")]
            b = df[df.system == baseline]
            m = t.merge(b, on=stats.CELL_KEYS, suffixes=("_t", "_b"))
            diff = m[f"{ni['metric']}_t"] - m[f"{ni['metric']}_b"]
            n = len(diff)
            ub = float(diff.mean()) + sps.t.ppf(0.99, n - 1) * float(diff.std(ddof=1)) / n ** 0.5
            rt = ref[ref.system == "jcac"].merge(
                ref[ref.system == baseline], on=stats.CELL_KEYS, suffixes=("_t", "_b"))
            ref_diff = float((rt[f"{ni['metric']}_t"] - rt[f"{ni['metric']}_b"]).mean())
            bound = ref_diff + ni["margin"]
            ok = ub <= bound
            outcomes.append(ok)
            w(f"| {baseline} | {n} | {diff.mean():+.4f} | {ub:+.4f} | {ref_diff:+.4f} "
              f"| {bound:+.4f} | {'PASS' if ok else 'FAIL'} |")
        verdict = "PASS" if all(outcomes) else "FAIL"
        w("")
        w(f"**{ni['tag']}: {verdict}** ({sum(outcomes)}/{len(outcomes)} within the margin).")
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
    treatment = spec.get("treatment", "jcac")
    for system in [treatment] + BASELINES:
        a = agg.loc[system]
        ref_hit = ref_agg.get(system if system in ref_agg else "jcac")
        w(f"| {system} | {a.total_cost_usd:.3f} | {a.mean_violation:.4f} "
          f"| {a.mean_jain:.4f} | {a.cache_hit_rate:.3f} | {ref_hit:.3f} |")
    w("")

    w("## Tier / cache posture, rep-0 rows (descriptive)")
    w("")
    post = tier_posture(db)
    total = post.groupby("system").n.sum()
    w("| system | " + " | ".join(f"steps @ {t}" for t in ("none", "small", "mid", "large"))
      + " | mean cache MB |")
    w("|---|---|---|---|---|---|")
    for system in [treatment] + BASELINES:
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
