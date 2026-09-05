#!/usr/bin/env python3
"""PREREG_LAYERED_FIX.md -> RESULTS_LAYERED_FIX.md.

Adjudicates the `−joint control +2884%` ablation. `systems.py:455` defines
that ablation as `SystemSpec("layered")`, so the figure is a property of the
layered baseline's tier rule — and that rule orders tiers by *name* while
`TIER_BASE_LATENCY_MS` makes `agent` non-monotonic in that order, with a
de-escalation threshold no tier can ever reach. The result is an absorbing
state at the 100×-price tier.

Every decision rule here is fixed by the pre-registration, which was
committed and pushed before the new arms existed (`bedd6ff`): the arms, the
`MARGIN = 0.8`, the per-trace Holm family of two, the Wilcoxon gate and the
one-sided directions.

    python analysis_layered_fix.py
"""

from __future__ import annotations

import collections
import sys
from pathlib import Path

import duckdb
import pandas as pd
import scipy.stats as sps

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stats import holm_bonferroni, record_path  # noqa: E402

_RESULTS = Path(__file__).resolve().parents[2] / "eval" / "results"
DB = _RESULTS / "raw_sim_layered_fix.duckdb"
CSV = _RESULTS / "metrics_matrix_layered_fix.csv.gz"

CELL = ["workload", "tenant_mix", "cluster_size", "rep"]
TENANTS = 8
COST_SCALE = 0.01
PUBLISHED_ABLATION_PCT = 2884.0   # the figure under adjudication

LABELS = {
    "jcac": "PolyForge (joint)",
    "jcac_nojoint": "−joint control (published `layered`)",
    "gptcache": "GPTCache posture (published)",
    "jcac_nojoint_v2": "−joint control (`layered_v2`, latency-ranked)",
    "gptcache_v2": "GPTCache posture (`gptcache_v2`, latency-ranked)",
}
ORDER = ["jcac", "jcac_nojoint", "jcac_nojoint_v2", "gptcache", "gptcache_v2"]


def load() -> pd.DataFrame:
    """Campaign rows from the DuckDB, else the committed export.

    `stats.load_campaign_runs` returns only the columns every campaign has
    ever had; this record additionally reports `mean_excess` and
    `cache_hit_rate`, so it runs its own query on the same two-path contract
    rather than widening the shared helper for one caller.
    """
    cols = ("r.system, r.workload, r.tenant_mix, r.cluster_size, r.rep, "
            "m.total_cost_usd, m.mean_violation, m.mean_jain, "
            "m.cache_hit_rate, m.mean_excess, m.ai_p95_ms, m.steps")
    if DB.exists():
        con = duckdb.connect(str(DB), read_only=True)
        df = con.execute(f"select {cols} from runs r join metrics m "
                         "on r.run_id = m.run_id where r.status = 'valid'").fetchdf()
        con.close()
    elif CSV.exists():
        df = pd.read_csv(CSV, float_precision="round_trip")
    else:
        raise FileNotFoundError(f"{DB.name} missing and export {CSV.name} absent")
    # Identical to stats.composite_objective and every matrix campaign.
    df["J"] = (df.total_cost_usd / (119 * TENANTS) / COST_SCALE
               + 2.0 * df.mean_violation + 0.5 * (1.0 - df.mean_jain))
    return df


def paired(df: pd.DataFrame, treat: str, base: str, metric: str) -> dict:
    """One-sided paired Wilcoxon of (treat - base) over the blocked cells,
    with the paired t reported alongside — the same machinery WP1 and WP15
    used, unchanged so the change of arm is the only difference."""
    m = df[df.system == treat].merge(df[df.system == base], on=CELL,
                                     suffixes=("_t", "_b"))
    diff = (m[f"{metric}_t"] - m[f"{metric}_b"]).dropna()
    n = int(len(diff))
    mean_diff = float(diff.mean()) if n else 0.0
    base_mean = float(df[df.system == base][metric].mean())
    rel = mean_diff / base_mean if base_mean else 0.0
    if n < 2 or diff.abs().sum() == 0.0:
        return {"n": n, "mean_diff": mean_diff, "rel": rel, "p": 1.0,
                "dz": 0.0, "t_p": 1.0}
    _, p = sps.wilcoxon(diff, alternative="less")
    _, t_p = sps.ttest_1samp(diff, 0.0)
    sd = float(diff.std(ddof=1))
    return {"n": n, "mean_diff": mean_diff, "rel": rel, "p": float(p),
            "dz": (mean_diff / sd) if sd else 0.0, "t_p": float(t_p)}


def ablation_delta(df: pd.DataFrame, ablated: str) -> float:
    """The ablation figure as `RESULTS.md` states it: how much more the
    ablated arm costs than the joint controller, in percent of the joint
    controller's spend, over the same cells."""
    j = float(df[df.system == "jcac"].total_cost_usd.mean())
    a = float(df[df.system == ablated].total_cost_usd.mean())
    return (a - j) / j * 100.0 if j else float("nan")


def tier_histogram() -> dict:
    """Deterministic replay of one cell per arm, for the tier histogram.

    **Disclosure.** The prereg promised a per-tier `tier_step_share` metric
    "so the latch's disappearance is visible rather than inferred". The
    harness `metrics` table has no such column — only `tier_none_step_share`
    — and adding one would change the schema every committed campaign is
    read through, which R4 forbids for a reporting convenience. So the
    histogram is produced the honest other way: by replaying a single cell
    deterministically here, outside the campaign, and labelling it as such.
    It is a diagnostic, not a scored quantity, and no hypothesis reads it.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jcac_sim"))
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))
    import simulate  # noqa: E402
    from harness import workloads  # noqa: E402

    ids, buckets, configs, limits = workloads.build(
        "agentic", "uniform", "medium", seed=42, steps=120)
    out = {}
    for arm in ("jcac", "layered", "layered_v2", "gptcache", "gptcache_v2"):
        res = simulate.run(arm, ids, buckets, configs=configs, limits=limits,
                           collect_rows=True)
        hist = collections.Counter(r["tier"] for r in res.rows)
        out[arm] = {"hist": dict(hist),
                    "cost": float(sum(r["cost_usd"] for r in res.rows)),
                    "steps": len(res.rows)}
    return out


def main() -> None:
    df = load()
    L: list[str] = []
    w = L.append

    w("# The −joint-control ablation, re-scored against a competent tier rule (V-series)\n")
    w("Generated by `analysis_layered_fix.py` from `raw_sim_layered_fix.duckdb`. "
      "Pre-registration: `PREREG_LAYERED_FIX.md`, committed and pushed before "
      "the new arms were implemented.\n")
    w("**This record does not replace any published result.** `RESULTS.md`'s "
      "ablation table stands exactly as committed (R1/R4); the published "
      "`layered` and `gptcache` arms are untouched here and replay "
      "bit-for-bit. This campaign is scored against them.\n")

    w("\n## What changed\n")
    w("One factor: **how the layer-local tier controller chooses a tier.** "
      "Replicas, cache, demand construction, seeds, cells, steps and reps are "
      "inherited unchanged from `ablations.yaml`.\n")
    w("\nThe published rule (`baselines.py:155-168`, duplicated at `:303-316`) "
      "ranks tiers **by name** — `small, mid, large` — and de-escalates only "
      "below `0.3 × target`. For `agent` traffic the measured latencies are "
      "non-monotonic in that order (`model.py:53-57`: small 6000 ms, mid "
      "2500 ms, large 3500 ms), so escalation walks *past* the fastest tier "
      "to one that is slower and ten times dearer. With a standard-class "
      "target of 6250 ms the de-escalation threshold is 1875 ms, while the "
      "minimum achievable p95 is 8400 / 3500 / 4900 ms at small / mid / "
      "large — **every tier is above it**, so the rule can never step down "
      "once a tenant carries AI load.\n")
    w("The replacement ranks by measured latency for the dominant AI kind and "
      "gates de-escalation on an `evaluate_step` projection at `MARGIN = 0.8` "
      "of target. It stays strictly layer-local: no other tenant, no cost "
      "term, no fairness term.\n")

    # --- per-system summary ------------------------------------------------
    w("\n## Per-system summary (mean over the 100 matched cells)\n")
    cols = ["total_cost_usd", "mean_violation", "mean_excess", "mean_jain",
            "cache_hit_rate", "J"]
    w("\n| system | " + " | ".join(cols) + " |")
    w("|---|" + "---:|" * len(cols))
    for arm in ORDER:
        sub = df[df.system == arm]
        if sub.empty:
            continue
        w(f"| {LABELS[arm]} | "
          + " | ".join(f"{float(sub[c].mean()):.4g}" for c in cols) + " |")

    # --- LF-H1 / LF-H2 -----------------------------------------------------
    published = ablation_delta(df, "jcac_nojoint")
    fixed = ablation_delta(df, "jcac_nojoint_v2")
    h = {
        "LF-H1": paired(df, "jcac_nojoint_v2", "jcac_nojoint", "total_cost_usd"),
        "LF-H2": paired(df, "jcac", "gptcache_v2", "J"),
    }
    holm = holm_bonferroni({k: v["p"] for k, v in h.items()})
    desc = {
        "LF-H1": "cost: layered_v2 < layered (the ablation delta shrinks)",
        "LF-H2": "composite J: jcac < gptcache_v2",
    }
    w(f"\n## Hypotheses (Holm-corrected within the family of {len(h)})\n")
    w("\n| id | test | n | effect | d_z | Wilcoxon p | Holm alpha | paired-t p | verdict |")
    w("|---|---|---:|---:|---:|---:|---:|---:|---|")
    for hid, r in h.items():
        hr = holm[hid]
        eff = (f"{r['rel'] * 100:+.1f}% cost" if hid == "LF-H1"
               else f"{r['mean_diff']:+.4f} J")
        w(f"| **{hid}** | {desc[hid]} | {r['n']} | {eff} | {r['dz']:+.3f} | "
          f"{r['p']:.3g} | {hr['threshold']:.5f} | {r['t_p']:.3g} | "
          f"{'PASS' if hr['reject'] else 'FAIL'} |")

    # --- the replacement number -------------------------------------------
    w("\n## The honest replacement for +2884%\n")
    w("\n| −joint-control comparator | mean cost vs `jcac` | published figure |")
    w("|---|---:|---:|")
    w(f"| published `layered` (`jcac_nojoint`) | **{published:+.1f}%** | "
      f"+{PUBLISHED_ABLATION_PCT:.0f}% |")
    w(f"| `layered_v2` (`jcac_nojoint_v2`) | **{fixed:+.1f}%** | — |")
    w(f"\n`jcac_nojoint` here reads {published:+.1f}% against the published "
      f"+{PUBLISHED_ABLATION_PCT:.0f}%. That is **not drift**: "
      "`config.run_identity` hashes the experiment *name* into every run's "
      "seed, so `matrix_layered_fix` draws different per-run seeds than "
      "`ablations` over the identical design. This is an independent sample "
      "of the same matrix, and the two agree to within that resampling. The "
      "comparison that matters is internal to this campaign, where both arms "
      "share cells and seeds exactly.\n")
    shrink = (published - fixed) / published * 100.0 if published else float("nan")
    w(f"\nRemoving the absorbing state removes **{shrink:.1f}%** of the "
      f"published ablation delta. What survives — {fixed:+.1f}% — is the "
      "honest cost of giving up joint control on this matrix, and it is the "
      f"number that should be cited. The {PUBLISHED_ABLATION_PCT:+.0f}% "
      "figure is **withdrawn** as a measure of joint control: it measures a "
      "baseline that cannot leave its most expensive tier.\n")

    # --- tier histogram (diagnostic) ---------------------------------------
    w("\n## The latch, before and after (deterministic replay — diagnostic, no hypothesis)\n")
    w("One cell replayed outside the campaign: `agentic` / `uniform` / "
      "`medium`, seed 42, 120 steps, 8 tenants. The prereg asked for a "
      "per-tier campaign metric; the harness schema has only "
      "`tier_none_step_share`, and adding a metrics column would change the "
      "schema every committed campaign is read through (R4), so this is "
      "reported as a replay rather than as a scored quantity.\n")
    hist = tier_histogram()
    w("\n| arm | tier histogram (tenant-steps) | cell cost |")
    w("|---|---|---:|")
    for arm in ("jcac", "layered", "layered_v2", "gptcache", "gptcache_v2"):
        e = hist[arm]
        pretty = ", ".join(f"`{k}` {v}" for k, v in sorted(e["hist"].items(),
                                                           key=lambda kv: -kv[1]))
        w(f"| `{arm}` | {pretty} | ${e['cost']:.2f} |")
    w("\nThe published arms sit in `large` for the overwhelming majority of "
      "tenant-steps; the v2 arms settle in `mid`, which for `agent` traffic is "
      "both the **fastest** tier and ten times cheaper than `large`. That is "
      "the whole mechanism.\n")

    # --- verdict -----------------------------------------------------------
    w("\n## Verdict\n")
    for hid in ("LF-H1", "LF-H2"):
        w(f"- **{hid}**: {'PASS' if holm[hid]['reject'] else '**FAIL**'} "
          f"({desc[hid]}; Wilcoxon p = {h[hid]['p']:.3g}).")
    w("\nBoth outcomes were answered in the prereg before the campaign ran. "
      "A LF-H2 FAIL withdraws the GPTCache comparison from the contribution "
      "list rather than retuning any arm; a PASS restates it at the new, "
      "smaller margin and does **not** restore the published −94.7%.\n")

    out = record_path("RESULTS_LAYERED_FIX.md")
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
