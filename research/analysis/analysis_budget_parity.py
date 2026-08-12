#!/usr/bin/env python3
"""PREREG_BUDGET_PARITY.md -> RESULTS_BUDGET_PARITY.md.

Removes the third confound in the baseline comparison: only `jcac` was
subject to the per-tenant budget filter (`controller.py:647` rejects every
over-budget candidate before the objective is evaluated; no baseline
`plan()` has a cost term at all). WP1 therefore compared a constrained
optimiser against unconstrained reactive controllers and read the
difference as control quality.

The prereg brackets the truth in both directions rather than one: it caps
the comparators (`hpa_budget`, `keda_budget`) *and* lifts jcac's cap
(`jcac_nobudget`), so neither "you crippled the baseline" nor "you let the
proposal cheat" is available as an objection.

Every decision rule here is fixed by the pre-registration, which was
committed and pushed before the campaign ran (efd353e), including
Amendment 1, disclosed before any hypothesis was scored: the per-trace Holm
family of four, the 0.05 non-inferiority margin inherited from
`PREREG_EVICTION_PARITY`, the one-sided directions, and the feasibility
reading of BP-H1.

    python analysis_budget_parity.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import scipy.stats as sps

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stats import holm_bonferroni, record_path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))
from harness.systems import lru_miss_cost_factor  # noqa: E402

_RESULTS = Path(__file__).resolve().parents[2] / "eval" / "results"

# --- frozen by PREREG_BUDGET_PARITY §Design ------------------------------
NI_MARGIN = 0.05                     # BP-H2 margin, inherited from EP
WP1_ARMS = ["jcac", "jcac_v2", "hpa", "keda", "firm", "hpa_fair", "keda_fair"]
NEW_ARMS = ["hpa_budget", "keda_budget", "jcac_nobudget"]
# The replication gate: the WP1 arms carry both mechanisms OFF and so must
# replay bit-identically against the campaign they were published from (R4).
REPLICATION_COLS = ["total_cost_usd", "mean_violation", "violation_step_share",
                    "mean_jain", "cache_hit_rate", "mean_excess",
                    "tier_none_step_share", "total_tier_cost_usd", "J"]
# Arms deployed in the published posture pay the LRU inference penalty on
# tier spend; the fair, budget and proposal arms do not (systems.py:284-295).
LRU_ARMS = {"hpa", "keda", "firm"}

TRACES = {
    "burstgpt": {"label": "BurstGPT v2.0", "windows": "96 x 6 h",
                 "runs": _RESULTS / "budget_parity_burstgpt_runs.csv",
                 "wp1": _RESULTS / "trace_parity_burstgpt_runs.csv"},
    "azure": {"label": "Azure LLM 2024", "windows": "72 x 3 h",
              "runs": _RESULTS / "budget_parity_azure_runs.csv",
              "wp1": _RESULTS / "trace_parity_azure_runs.csv"},
}

LABELS = {"jcac": "PolyForge (v1 trend)", "jcac_v2": "PolyForge v2 (Holt)",
          "hpa": "HPA (published)", "keda": "KEDA (published)",
          "firm": "FIRM (published)", "hpa_fair": "HPA (fair)",
          "keda_fair": "KEDA (fair)", "hpa_budget": "HPA (fair + budget)",
          "keda_budget": "KEDA (fair + budget)",
          "jcac_nobudget": "PolyForge (budget lifted)"}


def paired(df: pd.DataFrame, treat: str, base: str, metric: str,
           shift: float = 0.0) -> dict:
    """One-sided paired Wilcoxon of (treat - base - shift), paired by window.

    Identical machinery to `analysis_trace_parity.paired` on purpose: this
    campaign's whole claim is that one factor changed, so the test must not
    change with it. `shift` carries the non-inferiority margin.
    """
    m = (df[df.system == treat]
         .merge(df[df.system == base], on="window", suffixes=("_t", "_b")))
    diff = (m[f"{metric}_t"] - m[f"{metric}_b"]).dropna()
    n, mean_diff = int(len(diff)), (float(diff.mean()) if len(diff) else 0.0)
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


def replication_check(runs: pd.DataFrame, wp1: pd.DataFrame) -> dict:
    """Worst relative per-window deviation of the seven WP1 arms against the
    campaign that published them. Both new mechanisms default OFF, so this
    must be exactly zero; anything else means the wrapper leaked into arms
    it was frozen not to touch, and the campaign halts (R4)."""
    worst: dict[str, float] = {}
    m = runs.merge(wp1, on=["system", "window"], suffixes=("_n", "_o"))
    m = m[m.system.isin(WP1_ARMS)]
    for col in REPLICATION_COLS:
        a, b = m[f"{col}_n"], m[f"{col}_o"]
        denom = b.abs().where(b.abs() > 0, 1.0)
        worst[col] = float(((a - b).abs() / denom).max()) if len(m) else float("nan")
    worst["_rows"] = float(len(m))
    return worst


def spend_split(df: pd.DataFrame, arm: str) -> tuple[float, float]:
    """Mean infra and billed tier spend per window for `arm`.

    `miss_cost_factor` scales only `cost_tier_usd` (simulate.py:380), so the
    billed tier spend is `factor * total_tier_cost_usd` and infra is the
    remainder. This is the measurement behind Amendment 1's price table: if
    tier dominates, the replica knob cannot move affordability.
    """
    sub = df[df.system == arm]
    factor = lru_miss_cost_factor() if arm in LRU_ARMS else 1.0
    tier = float((factor * sub.total_tier_cost_usd).mean())
    return float(sub.total_cost_usd.mean()) - tier, tier


def decomposition(df: pd.DataFrame, fair: str, capped: str) -> dict:
    """BP-H3: how much of WP1's gap each direction of the bracket accounts for.

    WP1's cost gap on this trace is `fair - jcac`. Capping the comparator
    closes `fair - capped` of it; lifting jcac's cap closes
    `jcac_nobudget - jcac`. The same split is reported for `mean_excess`,
    where the gap runs the other way (jcac overshoots more).
    """
    def mean(arm: str, col: str) -> float:
        return float(df[df.system == arm][col].mean())

    cost_gap = mean(fair, "total_cost_usd") - mean("jcac", "total_cost_usd")
    exc_gap = mean("jcac", "mean_excess") - mean(fair, "mean_excess")
    cap_cost = mean(fair, "total_cost_usd") - mean(capped, "total_cost_usd")
    lift_cost = mean("jcac_nobudget", "total_cost_usd") - mean("jcac", "total_cost_usd")
    lift_exc = mean("jcac", "mean_excess") - mean("jcac_nobudget", "mean_excess")
    cap_exc = mean(capped, "mean_excess") - mean(fair, "mean_excess")
    return {
        "cost_gap": cost_gap, "exc_gap": exc_gap,
        "cap_cost": cap_cost,
        "cap_cost_share": cap_cost / cost_gap if cost_gap else float("nan"),
        "lift_cost": lift_cost,
        "lift_cost_share": lift_cost / cost_gap if cost_gap else float("nan"),
        "lift_exc": lift_exc,
        "lift_exc_share": lift_exc / exc_gap if exc_gap else float("nan"),
        "cap_exc": cap_exc,
    }


def score_trace(key: str, meta: dict, w) -> dict:
    df = pd.read_csv(meta["runs"])
    wp1 = pd.read_csv(meta["wp1"])
    w(f"\n## {meta['label']} ({meta['windows']})\n")

    # --- replication gate (prereg: halt and diagnose before scoring) -----
    rep = replication_check(df, wp1)
    ok = (rep["_rows"] > 0
          and all(v == 0.0 for k, v in rep.items() if k != "_rows"))
    w("### Replication of WP1's seven arms (gate)\n")
    w("Both mechanisms default OFF, so the seven arms inherited from "
      "`PREREG_TRACE_PARITY.md` must replay unchanged. "
      f"{int(rep['_rows'])} shared per-window rows; worst relative "
      "deviation, per column:\n")
    w("\n| " + " | ".join(REPLICATION_COLS) + " |")
    w("|" + "---:|" * len(REPLICATION_COLS))
    w("| " + " | ".join(f"{rep[c]:.2e}" for c in REPLICATION_COLS) + " |")
    w(f"\n**Replication {'PASS' if ok else 'FAIL'}** — the WP1 arms "
      + ("reproduce exactly; the budget wrapper touched only the arms it was "
         "frozen to touch.\n" if ok else
         "**DIVERGE**. Per the prereg the campaign halts here: the wrapper "
         "leaked into arms frozen to be unchanged, which invalidates the "
         "comparison rather than merely weakening it.\n"))

    # --- per-system summary ---------------------------------------------
    w("### Per-system summary (mean over windows)\n")
    cols = ["total_cost_usd", "mean_violation", "mean_excess",
            "tier_none_step_share", "mean_jain", "cache_hit_rate", "J"]
    w("\n| system | " + " | ".join(cols) + " | infra $ | tier $ |")
    w("|---|" + "---:|" * (len(cols) + 2))
    for arm in WP1_ARMS + NEW_ARMS:
        sub = df[df.system == arm]
        if sub.empty:
            continue
        infra, tier = spend_split(df, arm)
        w(f"| {LABELS[arm]} | "
          + " | ".join(f"{float(sub[c].mean()):.4g}" for c in cols)
          + f" | {infra:.3f} | {tier:.2f} |")

    # --- hypotheses (Holm family of four, per trace) ---------------------
    h = {
        "BP-H1a": paired(df, "jcac", "hpa_budget", "total_cost_usd"),
        "BP-H1b": paired(df, "jcac", "keda_budget", "total_cost_usd"),
        "BP-H2a": paired(df, "jcac_nobudget", "hpa_fair", "mean_excess", NI_MARGIN),
        "BP-H2b": paired(df, "jcac_nobudget", "keda_fair", "mean_excess", NI_MARGIN),
    }
    holm = holm_bonferroni({k: v["p"] for k, v in h.items()})
    desc = {
        "BP-H1a": "cost: jcac < hpa_budget",
        "BP-H1b": "cost: jcac < keda_budget",
        "BP-H2a": f"mean_excess of jcac_nobudget NI vs hpa_fair (margin {NI_MARGIN})",
        "BP-H2b": f"mean_excess of jcac_nobudget NI vs keda_fair (margin {NI_MARGIN})",
    }
    w(f"\n### Hypotheses (Holm-corrected within this trace's family of {len(h)})\n")
    w("\n| id | test | n | effect | d_z | Wilcoxon p | Holm alpha | paired-t p | verdict |")
    w("|---|---|---:|---:|---:|---:|---:|---:|---|")
    for hid, r in h.items():
        hr = holm[hid]
        eff = (f"{r['rel'] * 100:+.1f}% cost" if hid.startswith("BP-H1")
               else f"{r['mean_diff']:+.4f} excess")
        w(f"| **{hid}** | {desc[hid]} | {r['n']} | {eff} | {r['dz']:+.3f} | "
          f"{r['p']:.3g} | {hr['threshold']:.5f} | {r['t_p']:.3g} | "
          f"{'PASS' if hr['reject'] else 'FAIL'} |")

    # --- Amendment 1's feasibility reading, measured on this trace -------
    w("\n### What the budget filter could actually move (Amendment 1, measured)\n")
    w("The wrapper applies the controller's own per-step cap to a "
      "replica-only arm. Amendment 1 predicted, from the price table and "
      "before any scoring, that this cannot bind: tier spend dominates "
      "infra spend by three orders of magnitude, and the replica knob does "
      "not reach tier. Measured here:\n")
    w("\n| comparator | infra $ | tier $ | total $ | vs its uncapped twin |")
    w("|---|---:|---:|---:|---:|")
    feas = {}
    for fair, capped in (("hpa_fair", "hpa_budget"), ("keda_fair", "keda_budget")):
        fi, ft = spend_split(df, fair)
        ci, ct = spend_split(df, capped)
        rel = (ci + ct - fi - ft) / (fi + ft) if (fi + ft) else 0.0
        feas[capped] = {"infra_rel": (ci - fi) / fi if fi else 0.0,
                        "tier_rel": (ct - ft) / ft if ft else 0.0, "rel": rel}
        w(f"| `{fair}` | {fi:.3f} | {ft:.2f} | {fi + ft:.2f} | — |")
        w(f"| `{capped}` | {ci:.3f} | {ct:.2f} | {ci + ct:.2f} | "
          f"**{rel * 100:+.1f}%** |")
    infra_moves = ", ".join(f"{v['infra_rel'] * 100:+.1f}%" for v in feas.values())
    worst_tier = max(abs(v["tier_rel"]) for v in feas.values())
    w(f"\nThe cap moved infra spend by {infra_moves} and tier spend by at "
      f"most {worst_tier * 100:.2f}%. "
      + ("Tier spend is untouched, which is exactly the feasibility result "
         "stated before scoring: no replica-only arm can meet the per-tenant "
         "budget under AI load, because the only knob it has does not reach "
         "the cost that dominates. `hpa_budget` and `keda_budget` are "
         "therefore reported as *over-budget* arms, not as budget-respecting "
         "comparators — no such comparator exists in the replica-only class.\n"
         if worst_tier < 1e-9 else
         "Tier spend moved, so on this trace the cap did reach the dominant "
         "cost and the feasibility argument must be re-read against it.\n"))

    # --- BP-H3 (descriptive, no test) ------------------------------------
    w("### BP-H3 — decomposition of WP1's gap (descriptive, no test)\n")
    w("\n| direction | cost effect | share of WP1 cost gap | excess effect | share of WP1 excess gap |")
    w("|---|---:|---:|---:|---:|")
    dec = {}
    for fair, capped in (("hpa_fair", "hpa_budget"), ("keda_fair", "keda_budget")):
        d = decomposition(df, fair, capped)
        dec[fair] = d
        w(f"| cap the comparator (`{capped}` vs `{fair}`) | {d['cap_cost']:+.2f} | "
          f"{d['cap_cost_share'] * 100:+.0f}% | {d['cap_exc']:+.4f} | — |")
        w(f"| lift jcac's cap (`jcac_nobudget` vs `jcac`, gap vs `{fair}`) | "
          f"{d['lift_cost']:+.2f} | {d['lift_cost_share'] * 100:+.0f}% | "
          f"{d['lift_exc']:+.4f} | {d['lift_exc_share'] * 100:+.0f}% |")
    w("\nShed rate (`tier_none_step_share`) for every arm — the mechanism the "
      "budget constraint acts through:\n")
    all_arms = WP1_ARMS + NEW_ARMS
    w("\n| " + " | ".join(f"`{a}`" for a in all_arms) + " |")
    w("|" + "---:|" * len(all_arms))
    w("| " + " | ".join(
        f"{float(df[df.system == a].tier_none_step_share.mean()):.4f}"
        for a in all_arms) + " |")

    return {"key": key, "meta": meta, "h": h, "holm": holm,
            "replicated": ok, "dec": dec, "feas": feas}


def main() -> None:
    present = {k: m for k, m in TRACES.items() if m["runs"].exists()}
    pending = [m["label"] for k, m in TRACES.items() if k not in present]
    if not present:
        raise FileNotFoundError(
            "no campaign output — run `python trace_parity.py --campaign "
            "budget --trace {burstgpt,azure}` first")

    L: list[str] = []
    w = L.append
    w("# Budget parity — the constraint asymmetry in the baseline comparison (V-series)\n")
    w("Generated by `analysis_budget_parity.py` from "
      "`budget_parity_*_runs.csv`. Pre-registration: "
      "`PREREG_BUDGET_PARITY.md` (+ Amendment 1), committed and pushed "
      "before the campaign ran.\n")
    w("**This record does not replace any published result.** "
      "`RESULTS_TRACE_PARITY.md` and its FAIL verdicts stand exactly as "
      "committed (R1/R4); this campaign is scored against them. The two "
      "traces are replications and are **never pooled** — each carries its "
      "own Holm family of four, so scoring one cannot touch the other.\n")
    if pending:
        w(f"\n> **INTERIM — {', '.join(pending)} is still running.** Its "
          "arms, windows, metrics, margin and tests were frozen before the "
          "campaign launched and nothing scored here can alter them; this "
          "record is regenerated when that trace lands. No cross-trace claim "
          "is made until it does.\n")
    w("\n## What changed\n")
    w("One factor: **which arms the per-tenant budget filter applies to.** "
      "Traces, windows, demand construction, seeds, tenants, cluster limits, "
      "forecaster, cache/tier machinery, eviction accounting and the fair "
      "cache posture are inherited unchanged from `PREREG_TRACE_PARITY.md`.\n")
    w("\n| arm | budget filter | cache posture |")
    w("|---|---|---|")
    w("| WP1's seven | as published (`jcac`/`jcac_v2` only) | as published |")
    w("| `hpa_budget` / `keda_budget` | **applied** — the controller's own "
      "per-step rule, verbatim | fair (512 MB, no LRU penalty) |")
    w("| `jcac_nobudget` | **lifted** | as published |")

    verdicts = [score_trace(k, m, w) for k, m in present.items()]

    w("\n## Verdict\n")
    for v in verdicts:
        h1 = [hid for hid in ("BP-H1a", "BP-H1b") if v["holm"][hid]["reject"]]
        h2 = [hid for hid in ("BP-H2a", "BP-H2b") if v["holm"][hid]["reject"]]
        w(f"- **{v['meta']['label']}**: BP-H1 (cost vs a capped comparator) "
          f"passing: {', '.join(h1) if h1 else '**none**'}. "
          f"BP-H2 (severity non-inferiority with the cap lifted) passing: "
          f"{', '.join(h2) if h2 else '**none**'}. "
          f"Replication of WP1's arms: "
          f"{'PASS' if v['replicated'] else '**FAIL**'}.")
    w("\nEvery hypothesis is reported in the direction it landed, and the "
      "response to each outcome was fixed in the prereg before the campaign "
      "ran. BP-H1 is read as a **feasibility** result per Amendment 1: the "
      "capped comparators remain over budget, so a PASS is not evidence of a "
      "cost advantage over a budget-respecting reactive controller — it is "
      "evidence that no such controller exists in the replica-only class. A "
      "BP-H2 PASS makes WP1's severity FAIL constraint-induced, and the cost "
      "and severity results must then be quoted together or not at all.\n")
    if pending:
        w(f"\n**Not yet scored:** {', '.join(pending)}. This record is not "
          "registered in `scripts/reproduce.py` until the campaign completes; "
          "an interim record must not enter the byte-identity gate.\n")

    out = record_path("RESULTS_BUDGET_PARITY.md")
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
