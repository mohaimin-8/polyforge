#!/usr/bin/env python3
"""PREREG_TRACE_PARITY.md -> RESULTS_TRACE_PARITY.md.

Adjudicates the two real-demand cost headlines. `RESULTS_TRACE2.md`
(BurstGPT, n=96) and `RESULTS_TRACE_AZURE.md` (Azure LLM 2024, n=72) were
both scored through `trace_matrix.run_one`, which charges every reactive
baseline a 1.4581x LRU inference penalty jcac never pays and pins those
baselines at the 128 MB cache they cannot move. This scores the same
windows against comparators that pay neither penalty.

Every decision rule here is fixed by the pre-registration, which was
committed and pushed before the campaign ran (97f5879): the arms, the
512 MB pre-size, the 0.05 non-inferiority margin, the per-trace Holm
family of four, the Wilcoxon gate and the one-sided directions.

    python analysis_trace_parity.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import scipy.stats as sps

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stats import holm_bonferroni, record_path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))
from harness.systems import (  # noqa: E402
    eviction_sensitivity_band, lru_miss_cost_factor,
)

_RESULTS = Path(__file__).resolve().parents[2] / "eval" / "results"

# --- frozen by PREREG_TRACE_PARITY §Design -------------------------------
NI_MARGIN = 0.05          # TP-H3 non-inferiority margin, inherited from EP
SWING_PP = 10.0           # TP-H2 descriptive threshold
PUBLISHED_ARMS = ["jcac", "jcac_v2", "hpa", "keda", "firm"]
FAIR_ARMS = ["hpa_fair", "keda_fair"]
REPLICATION_COLS = ["total_cost_usd", "mean_violation", "violation_step_share",
                    "mean_jain", "cache_hit_rate", "J"]

TRACES = {
    "burstgpt": {
        "label": "BurstGPT v2.0",
        "prereg": "PREREG_TRACE2.md",
        "record": "RESULTS_TRACE2.md",
        "parity": _RESULTS / "trace_parity_burstgpt_runs.csv",
        "published": _RESULTS / "trace_replay2_runs.csv",
        "windows": "96 x 6 h",
    },
    "azure": {
        "label": "Azure LLM 2024",
        "prereg": "PREREG_TRACE_AZURE.md",
        "record": "RESULTS_TRACE_AZURE.md",
        "parity": _RESULTS / "trace_parity_azure_runs.csv",
        "published": _RESULTS / "trace_replay_azure_runs.csv",
        "windows": "72 x 3 h",
    },
}

LABELS = {"jcac": "PolyForge (v1 trend)", "jcac_v2": "PolyForge v2 (Holt)",
          "hpa": "HPA (published)", "keda": "KEDA (published)",
          "firm": "FIRM (published)", "hpa_fair": "HPA (fair)",
          "keda_fair": "KEDA (fair)"}


def paired(df: pd.DataFrame, treat: str, base: str, metric: str,
           shift: float = 0.0) -> dict:
    """One-sided paired Wilcoxon of (treat - base - shift) on `metric`,
    paired by window. `shift` carries the non-inferiority margin.

    The paired t of the same differences is reported alongside because the
    published records (`PREREG_TRACE2` §3) froze a paired t at alpha=0.01;
    the prereg states the Wilcoxon is the gate and the t is reported so the
    change of test is checkable rather than taken on trust.
    """
    m = (df[df.system == treat]
         .merge(df[df.system == base], on="window", suffixes=("_t", "_b")))
    diff = (m[f"{metric}_t"] - m[f"{metric}_b"]).dropna()
    n, mean_diff = int(len(diff)), float(diff.mean()) if len(diff) else 0.0
    base_mean = float(df[df.system == base][metric].mean())
    rel = mean_diff / base_mean if base_mean else 0.0
    shifted = diff - shift
    if n < 2 or shifted.abs().sum() == 0.0:
        return {"n": n, "mean_diff": mean_diff, "rel": rel, "p": 1.0,
                "dz": 0.0, "t_p": 1.0}
    _, p = sps.wilcoxon(shifted, alternative="less")
    _, t_p = sps.ttest_1samp(diff, 0.0)
    sd = float(diff.std(ddof=1))
    return {"n": n, "mean_diff": mean_diff, "rel": rel, "p": float(p),
            "dz": (mean_diff / sd) if sd else 0.0, "t_p": float(t_p)}


def rel_cost(df: pd.DataFrame, treat: str, base: str) -> float:
    t = float(df[df.system == treat].total_cost_usd.mean())
    b = float(df[df.system == base].total_cost_usd.mean())
    return (t - b) / b if b else 0.0


def replication_check(parity: pd.DataFrame, published: pd.DataFrame) -> dict:
    """The prereg's halt condition: the five published arms must reproduce
    their committed per-window rows. Returns the worst relative deviation
    per column, over the arms and windows both frames share."""
    worst: dict[str, float] = {}
    m = parity.merge(published, on=["system", "window"], suffixes=("_n", "_o"))
    m = m[m.system.isin(PUBLISHED_ARMS)]
    for col in REPLICATION_COLS:
        a, b = m[f"{col}_n"], m[f"{col}_o"]
        denom = b.abs().where(b.abs() > 0, 1.0)
        worst[col] = float(((a - b).abs() / denom).max()) if len(m) else float("nan")
    worst["_rows"] = float(len(m))
    return worst


def band_table(df: pd.DataFrame, base: str) -> list[tuple[str, float, float]]:
    """The headline delta recomputed at every eviction comparator.

    `miss_cost_factor` scales only `cost_tier_usd` (simulate.py:380), so the
    baseline's cost at factor f is exactly `infra + f * tier` where
    `infra = total - published_factor * tier`. Nothing is re-simulated.
    """
    lru = lru_miss_cost_factor()
    b = df[df.system == base]
    tier = b.total_tier_cost_usd.to_numpy()
    infra = b.total_cost_usd.to_numpy() - lru * tier
    jcac_mean = float(df[df.system == "jcac"].total_cost_usd.mean())
    out = []
    for name, f in eviction_sensitivity_band().items():
        base_mean = float((infra + f * tier).mean())
        out.append((name, f, (jcac_mean - base_mean) / base_mean if base_mean else 0.0))
    return out


def score_trace(key: str, meta: dict, w) -> dict:
    parity = pd.read_csv(meta["parity"])
    published = pd.read_csv(meta["published"])

    w(f"\n## {meta['label']} ({meta['windows']}, `{meta['prereg']}` protocol)\n")

    # --- replication gate (prereg: halt and diagnose before scoring) -----
    rep = replication_check(parity, published)
    ok = all(v <= 1e-9 for k, v in rep.items() if k != "_rows") and rep["_rows"] > 0
    w("### Replication of the published arms (gate)\n")
    w(f"The five published arms were re-run here; {int(rep['_rows'])} per-window "
      f"rows are shared with `{meta['published'].name}`. Worst relative "
      "deviation, per column:\n")
    w("\n| " + " | ".join(REPLICATION_COLS) + " |")
    w("|" + "---:|" * len(REPLICATION_COLS))
    w("| " + " | ".join(f"{rep[c]:.2e}" for c in REPLICATION_COLS) + " |")
    w(f"\n**Replication {'PASS' if ok else 'FAIL'}** — the published arms "
      f"{'reproduce bit-for-bit; the substrate is sound' if ok else 'DIVERGE. Per the prereg the campaign halts here: a replication failure invalidates the substrate, not merely this comparison'}.\n")

    # --- per-system summary ---------------------------------------------
    w("### Per-system summary (mean over windows)\n")
    cols = ["total_cost_usd", "mean_violation", "mean_excess",
            "tier_none_step_share", "mean_jain", "cache_hit_rate", "J"]
    w("\n| system | " + " | ".join(cols) + " |")
    w("|---|" + "---:|" * len(cols))
    for arm in PUBLISHED_ARMS + FAIR_ARMS:
        sub = parity[parity.system == arm]
        if sub.empty:
            continue
        w(f"| {LABELS[arm]} | " +
          " | ".join(f"{float(sub[c].mean()):.4g}" for c in cols) + " |")

    # --- hypotheses ------------------------------------------------------
    h = {
        "TP-H1a": paired(parity, "jcac", "hpa_fair", "total_cost_usd"),
        "TP-H1b": paired(parity, "jcac", "keda_fair", "total_cost_usd"),
        "TP-H3a": paired(parity, "jcac", "hpa_fair", "mean_excess", NI_MARGIN),
        "TP-H3b": paired(parity, "jcac", "keda_fair", "mean_excess", NI_MARGIN),
    }
    holm = holm_bonferroni({k: v["p"] for k, v in h.items()})
    desc = {
        "TP-H1a": "cost: jcac < hpa_fair", "TP-H1b": "cost: jcac < keda_fair",
        "TP-H3a": f"mean_excess NI vs hpa_fair (margin {NI_MARGIN})",
        "TP-H3b": f"mean_excess NI vs keda_fair (margin {NI_MARGIN})",
    }
    w(f"\n### Hypotheses (Holm-corrected within this trace's family of {len(h)})\n")
    w("\n| id | test | n | effect | d_z | Wilcoxon p | Holm alpha | paired-t p | verdict |")
    w("|---|---|---:|---:|---:|---:|---:|---:|---|")
    for hid, r in h.items():
        hr = holm[hid]
        eff = (f"{r['rel'] * 100:+.1f}% cost" if hid.startswith("TP-H1")
               else f"{r['mean_diff']:+.4f} excess")
        w(f"| **{hid}** | {desc[hid]} | {r['n']} | {eff} | {r['dz']:+.3f} | "
          f"{r['p']:.3g} | {hr['threshold']:.5f} | {r['t_p']:.3g} | "
          f"{'PASS' if hr['reject'] else 'FAIL'} |")

    # --- TP-H2 -----------------------------------------------------------
    swings = {}
    w("\n### TP-H2 — how much of the headline is accounting (descriptive)\n")
    w("\n| published comparator | published delta | fair delta | swing |")
    w("|---|---:|---:|---:|")
    for pub, fair in (("hpa", "hpa_fair"), ("keda", "keda_fair")):
        p_rel, f_rel = rel_cost(parity, "jcac", pub), rel_cost(parity, "jcac", fair)
        swings[pub] = (f_rel - p_rel) * 100.0
        w(f"| `{pub}` | {p_rel * 100:+.1f}% | {f_rel * 100:+.1f}% | "
          f"**{swings[pub]:+.1f} pp** |")
    h2 = max(abs(v) for v in swings.values()) > SWING_PP
    w(f"\n**TP-H2 {'PASS' if h2 else 'FAIL'}** (threshold: |swing| > {SWING_PP:.0f} pp).\n")

    # --- sensitivity band -------------------------------------------------
    w("### Eviction comparator sensitivity band\n")
    w("The delta vs `hpa` recomputed at every policy in "
      "`eviction_comparison.csv`, from measured unscaled tier spend — not "
      "re-simulated. GDSF, a standard policy in the same table, **beats** the "
      "proposed cost-aware policy at both capacities.\n")
    w("\n| comparator | factor | jcac vs hpa |")
    w("|---|---:|---:|")
    for name, f, delta in band_table(parity, "hpa"):
        w(f"| `{name}` | {f:.4f} | {delta * 100:+.1f}% |")

    return {"key": key, "meta": meta, "h": h, "holm": holm,
            "h2": h2, "swings": swings, "replicated": ok,
            "published_rel": rel_cost(parity, "jcac", "hpa"),
            "fair_rel": rel_cost(parity, "jcac", "hpa_fair")}


def main() -> None:
    missing = [m["parity"].name for m in TRACES.values() if not m["parity"].exists()]
    if missing:
        raise FileNotFoundError(
            f"campaign output absent: {', '.join(missing)} — run "
            "`python trace_parity.py --trace {burstgpt,azure}` first")

    L: list[str] = []
    w = L.append
    w("# Trace-replay eviction parity — the real-demand headlines re-scored (V-series)\n")
    w("Generated by `analysis_trace_parity.py` from "
      "`trace_parity_{burstgpt,azure}_runs.csv`. Pre-registration: "
      "`PREREG_TRACE_PARITY.md`, committed and pushed before the campaign ran.\n")
    w("**This record does not replace any published result.** "
      "`RESULTS_TRACE2.md`, `RESULTS_TRACE_AZURE.md` and their committed CSVs "
      "stand exactly as published; this campaign is scored against them "
      "(R1/R4). The two traces are replications and are **never pooled** — "
      "each carries its own Holm family.\n")
    w("\n## What changed\n")
    w("One factor family: how eviction is priced, and what cache posture the "
      "reactive comparator is deployed with. Traces, windows, demand scale, "
      "jitter seeds, tenants, cluster limits, controllers and tuned "
      "parameters are untouched and are called out of the committed "
      "`trace_matrix{,2,_azure}.py`.\n")
    w("\n| arm | eviction pricing | cache posture |")
    w("|---|---|---|")
    w("| `jcac` / `jcac_v2` | none (as published) | free, controller-chosen |")
    w("| `hpa` / `keda` / `firm` | LRU x1.4581 (as published) | pinned 128 MB |")
    w("| `hpa_fair` / `keda_fair` | **none** | **pinned 512 MB** |")

    verdicts = [score_trace(k, m, w) for k, m in TRACES.items()]

    w("\n## Verdict\n")
    for v in verdicts:
        primary = [hid for hid in ("TP-H1a", "TP-H1b") if v["holm"][hid]["reject"]]
        sev = [hid for hid in ("TP-H3a", "TP-H3b") if v["holm"][hid]["reject"]]
        w(f"- **{v['meta']['label']}**: published delta vs `hpa` "
          f"{v['published_rel'] * 100:+.1f}%, fair delta vs `hpa_fair` "
          f"{v['fair_rel'] * 100:+.1f}%. Cost primaries passing: "
          f"{', '.join(primary) if primary else '**none**'}. "
          f"Severity non-inferiority passing: "
          f"{', '.join(sev) if sev else '**none**'}. "
          f"Replication of published arms: "
          f"{'PASS' if v['replicated'] else '**FAIL**'}.")
    w("\nEvery hypothesis above is reported in the direction it landed. Per "
      "the pre-registration and `docs/PUBLICATION_ROADMAP.md` §7, a FAIL of "
      "TP-H1 is the headline finding of this record and is answered by "
      "restating the scoreboard's Cost row in `RESULTS_MASTER.md` — never by "
      "softening this record or editing a published one.\n")

    out = record_path("RESULTS_TRACE_PARITY.md")
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
