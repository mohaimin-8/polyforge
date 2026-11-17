"""Markdown for the WP6 replay outputs (aggregates only; no prompt text)."""

from __future__ import annotations

import json
import math

import pandas as pd


def _cell(rate: float, lo: float, hi: float) -> str:
    if math.isnan(lo):
        return f"{rate:.3f}"
    return f"{rate:.3f} [{lo:.3f}, {hi:.3f}]"


def hit_table(summary: pd.DataFrame, cls: str, policy: str) -> list[str]:
    g = summary[(summary["class"] == cls) & (summary.policy == policy)]
    if g.empty:
        return []
    taus = sorted(g.tau.unique())
    caps = sorted(g.capacity.unique(), key=lambda c: (c == 0, c))
    lines = ["| C (entries) | " + " | ".join(f"tau {t:.2f}" for t in taus) + " | filled | resident |",
             "|---" * (len(taus) + 3) + "|"]
    for cap in caps:
        rows = g[g.capacity == cap].set_index("tau")
        cells = [_cell(rows.loc[t, "warm_hit_rate"], rows.loc[t, "warm_ci_lo"], rows.loc[t, "warm_ci_hi"])
                 for t in taus]
        first = rows.iloc[0]
        lines.append(f"| {'unbounded' if cap == 0 else cap} | " + " | ".join(cells)
                     + f" | {first.filled_share:.0%} | {first.mean_resident:.0f} |")
    return lines


def _f(x, fmt: str = ".3f") -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else format(x, fmt)


def precision_markdown(r: dict) -> str:
    q = r["qqp"]
    L = ["# WP6 hit correctness: QQP precision-recall and the LMSYS proxy", "",
         f"QQP ({'+'.join(q['splits'])}): {q['pairs']} labelled pairs, {q['duplicates']} duplicates, "
         f"{q['questions_embedded']} questions embedded with `{q['encoder']}` as `{q['key_format']}` key text "
         f"in {q['embed_seconds']:.1f} s. Average precision {_f(q['average_precision'])}. Curve: "
         "`qqp_pr_curve.csv`" + (", `qqp_pr_curve.png`." if q.get("plot") else "."), "",
         "| tau | QQP pairs >= tau | precision | recall | wrong-hit rate [Wilson 95%] | "
         "LMSYS proxy wrong-hit rate (rho 0.70) | LMSYS hit rate |", "|---|---|---|---|---|---|---|"]
    lm = r["lmsys_wrong_hit_rate"]["by_tau"]
    for tau, m in r["qqp_wrong_hit_rate"].items():
        lo, hi = m["wrong_hit_ci95"]
        p = lm.get(tau, {})
        L.append(f"| {tau} | {m['pairs_at_or_above']} | {_f(m['precision'])} | {_f(m['recall'])} | "
                 f"{_f(m['wrong_hit_rate'])} [{_f(lo)}, {_f(hi)}] | {_f(p.get('wrong_hit_rate'))} | "
                 f"{_f(p.get('hit_rate'))} |")
    L += ["", f"LMSYS proxy: {r['lmsys_wrong_hit_rate']['protocol']}; source "
          f"`{r['lmsys_wrong_hit_rate']['source']}` (first-turn stratum precision at 0.85: "
          f"{_f(r['lmsys_wrong_hit_rate']['first_turn_precision_at_0.85'])})."]
    L += [""] + [f"- {n}" for n in r["notes"]]
    return "\n".join(L) + "\n"


def fit_markdown(out: dict) -> str:
    b = out["bytes_per_entry"]
    L = ["# WP6 fits: h(c) = hmax * c / (c + half) per class, policy and tau", "",
         f"Bytes per entry (Go {b['go_version']}, real SemanticCache.Store path, {b['vector_bytes']}-byte "
         f"vector): overhead beyond key and completion bytes {b['overhead_bytes']:.0f} B (range over runs "
         f"{b['overhead_range'][0]:.0f}-{b['overhead_range'][1]:.0f}); a minimal entry (15-byte key, no "
         f"completion) holds {b['minimal_entry_bytes']:.0f} B.", "",
         "Fitted on the capacities whose cache was full when the warm half began, in three units: MiB of "
         "resident heap (plant 2's unit), MiB of completion bytes (the live knob's accounting) and "
         "entries. Intervals: 95% bootstrap over tenants.", "",
         "| class | policy | tau | hmax [95%] | half MiB [95%] | half knob MiB | half entries |",
         "|---|---|---|---|---|---|---|"]
    for cls, by_pol in out["curves"].items():
        for pol, by_tau in by_pol.items():
            for tau, c in by_tau.items():
                mb, knob, ent = c.get("mb"), c.get("knob_mb"), c.get("entries")
                if not mb:
                    L.append(f"| {cls} | {pol} | {tau} | not fitted | | | |")
                    continue
                ci = mb.get("cache_hit_max_ci95") or [float("nan")] * 2
                hci = mb.get("half_ci95") or [float("nan")] * 2
                L.append(f"| {cls} | {pol} | {tau} | {mb['cache_hit_max']:.3f} [{_f(ci[0])}, {_f(ci[1])}] | "
                         f"{mb['half']:.3f} [{_f(hci[0])}, {_f(hci[1])}] | "
                         f"{_f(knob['half'] if knob else None)} | {_f(ent['half'] if ent else None, '.0f')} |")
    L += ["", f"Plant 2 default ({out['plant2'].get('class')}, {out['plant2'].get('policy')}, tau "
          f"{out['plant2'].get('tau')}): " + json.dumps({k: v for k, v in out["plant2"].items()
                                                         if k.startswith("cache_")}), ""]
    return "\n".join(L) + "\n"


def replay_markdown(meta: dict, summary: pd.DataFrame) -> str:
    t = meta["timings"]
    L = ["# WP6 semantic-cache replay of LMSYS first user turns", "",
         f"Commit `{meta['commit'][:12]}`; encoder `{meta['encoder']}` ({meta['dims']}-d); key text "
         f"`{meta['key_format']}` (gateway = cache.go cacheKeyText, \"user: <prompt>\\n\"); virtual clock "
         f"{meta['dt_seconds']} s per request; {meta['first_turns']} first turns; tenant streams of "
         f"{meta['stream_length']} prompts; candidate neighbours k = {meta['neighbours_k']}.", "",
         "Class sizes (first turns): " + ", ".join(f"{k} {v}" for k, v in meta["class_sizes"].items()) + ".",
         "Tenants per class: " + ", ".join(f"{k} {v}" for k, v in meta["tenants"].items()) + ".",
         "Mean bytes per first turn: " + ", ".join(f"{k} {v:.0f}" for k, v in meta["mean_bytes"].items()) + ".",
         "", f"Timing: load and embed {t['load_and_embed_s']:.1f} s ({t['embedded']} prompts embedded in "
         f"{t['embed_s']:.1f} s), replay {t['replay_s']:.1f} s with {t['workers']} worker(s).", "",
         "Hit rate on the warm half of each tenant stream (requests after the first half), pooled over "
         "tenants, with a 95% bootstrap interval over tenants. `filled` is the share of tenants whose "
         "cache was full when the warm half began (otherwise the point is not a steady state); "
         "`resident` is the mean number of entries during the warm half. The gateway's defaults are "
         "tau 0.95 and C 10,000 per tenant; its eviction policy is cost-aware (W28)."]
    L += policy_comparison(summary)
    for cls in meta["tenants"]:
        for policy in meta["policies"]:
            table = hit_table(summary, cls, policy)
            if table:
                L += ["", f"## {cls}, {policy}", ""] + table
    return "\n".join(L) + "\n"


def policy_comparison(summary: pd.DataFrame, cls: str = "all") -> list[str]:
    """Cost-aware against LRU on hit rate and on the share of completion bytes
    served from cache (the gateway's Store prices a miss by completion bytes)."""
    g = summary[summary["class"] == cls]
    if not {"costaware", "lru"} <= set(g.policy) or "warm_saved_share" not in g:
        return []
    L = ["", f"## Cost-aware vs LRU, class {cls}: warm hit rate and completion-byte share served", "",
         "| tau | C | hit rate cost-aware | hit rate LRU | bytes served cost-aware | bytes served LRU |",
         "|---|---|---|---|---|---|"]
    for tau in sorted(g.tau.unique()):
        for cap in sorted(g.capacity.unique(), key=lambda c: (c == 0, c)):
            r = g[(g.tau == tau) & (g.capacity == cap)].set_index("policy")
            L.append(f"| {tau:.2f} | {'unbounded' if cap == 0 else cap} | {r.loc['costaware', 'warm_hit_rate']:.3f} | "
                     f"{r.loc['lru', 'warm_hit_rate']:.3f} | {r.loc['costaware', 'warm_saved_share']:.3f} | "
                     f"{r.loc['lru', 'warm_saved_share']:.3f} |")
    return L
