"""Pseudo-per-tenant forecast decomposition (Wave 2, PREREG_PSEUDO_TENANT.md).

Protocol frozen in the pre-registration; this script is committed with it
and executed once after the push. It splits the *same* traces the aggregate
results used into their natural sub-streams and drives the *identical*
frozen protocol functions (imported, never reimplemented):

- BurstGPT v2.0 raw parts (research/traces/data/BurstGPT_{1,2,3}.csv):
  sub-streams = Model x Log Type, hourly request-rate series, contiguous
  segments split at >= 24 consecutive empty hourly buckets (the ~104-day
  collection silence is missing data, not zero demand).
- Azure LLM 2024 week (research/traces/data/azure-llm/
  AzureLLMInferenceTrace_{code,conv}_1week.csv): already per-stream.

Inclusion gate (frozen): >= 168 non-empty hourly buckets AND >= 5000
requests per (sub-stream x segment); excluded series are listed, marked.

Hypotheses (frozen, boundary 0.5 declared from prior aggregate results):
  PT-H1 every included series with max detrended autocorr (lags 8-48) < 0.5
        has RMSE(seasonal) > 0.95 * RMSE(trend)  [no material seasonal win]
  PT-H2 (exploratory) series with autocorr >= 0.5: RMSE(seasonal) < RMSE(trend)
  PT-H3 (descriptive) >= 2 distinct best methods across included series

One execution, output to PSEUDO_TENANT.md; if this file and the output
disagree, the output is stale and must be regenerated, never edited.
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SIM_DIR = REPO_ROOT / "research" / "jcac_sim"
if str(SIM_DIR) not in sys.path:
    sys.path.insert(0, str(SIM_DIR))
ANALYSIS_DIR = Path(__file__).resolve().parent
if str(ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_DIR))

from forecast_trace import METHODS, detected_period, rolling_one_step  # noqa: E402
from forecast_trace_real import contiguous_segments  # noqa: E402

DATA = REPO_ROOT / "research" / "traces" / "data"
OUT = ANALYSIS_DIR / "PSEUDO_TENANT.md"

INTERVAL_S = 3600
MIN_BUCKETS = 168
MIN_REQUESTS = 5000
BOUNDARY = 0.5
MATERIAL = 0.95  # RMSE(seasonal) < MATERIAL * RMSE(trend) = material win


def burstgpt_streams() -> dict[str, list[float]]:
    """Hourly rps series per (Model x Log Type) over the three raw parts.

    Part clocks continue across files (fetch_burstgpt.py finding), so
    bucket indices are global: bucket = int(timestamp_s) // 3600.
    """
    counts: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for part in ("BurstGPT_1.csv", "BurstGPT_2.csv", "BurstGPT_3.csv"):
        with open(DATA / part, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    ts = float(row["Timestamp"])
                except (ValueError, KeyError):
                    continue
                key = f"{row['Model'].strip()} / {row['Log Type'].strip()}"
                counts[key][int(ts) // INTERVAL_S] += 1
    streams = {}
    for key, per_bucket in counts.items():
        lo, hi = min(per_bucket), max(per_bucket)
        streams[key] = [per_bucket.get(b, 0) / INTERVAL_S for b in range(lo, hi + 1)]
    return streams


def azure_streams() -> dict[str, list[float]]:
    """Hourly rps series for the two 2024-week streams (TIMESTAMP column,
    ISO-8601 with fractional seconds; tz-naive within one file)."""
    from datetime import datetime

    streams = {}
    for name, fname in (("azure code (1 week)", "AzureLLMInferenceTrace_code_1week.csv"),
                        ("azure conv (1 week)", "AzureLLMInferenceTrace_conv_1week.csv")):
        per_bucket: dict[int, int] = defaultdict(int)
        with open(DATA / "azure-llm" / fname, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            ts_col = reader.fieldnames[0]
            for row in reader:
                raw = row[ts_col].split("+")[0]
                head, _, frac = raw.partition(".")
                dt = datetime.fromisoformat(head + ("." + frac[:6] if frac else ""))
                per_bucket[int(dt.timestamp()) // INTERVAL_S] += 1
        lo, hi = min(per_bucket), max(per_bucket)
        streams[name] = [per_bucket.get(b, 0) / INTERVAL_S for b in range(lo, hi + 1)]
    return streams


def main() -> None:
    rows = []
    all_streams = {**burstgpt_streams(), **azure_streams()}
    for stream, series in sorted(all_streams.items()):
        for si, (a, b) in enumerate(contiguous_segments(series), start=1):
            seg = series[a:b]
            nonzero = sum(1 for v in seg if v > 0.0)
            requests = sum(seg) * INTERVAL_S
            included = nonzero >= MIN_BUCKETS and requests >= MIN_REQUESTS
            entry = {
                "stream": stream, "segment": si, "buckets": len(seg),
                "nonzero": nonzero, "requests": int(round(requests)),
                "included": included,
            }
            if included:
                lag, corr = detected_period(seg)
                results = {m: rolling_one_step(seg, m) for m in METHODS}
                best = min(results, key=lambda m: results[m][1])
                entry.update({
                    "lag": lag, "corr": corr, "best": best,
                    "rmse": {m: results[m][1] for m in METHODS},
                })
            rows.append(entry)

    lines: list[str] = []
    w = lines.append
    w("# Pseudo-per-tenant forecast decomposition — as measured (PREREG_PSEUDO_TENANT.md)")
    w("")
    w("Generated by `pseudo_tenant.py`; protocol, gate and boundary frozen in the")
    w("pre-registration pushed before this run. Sub-streams of the same traces the")
    w("aggregate results used; protocol functions imported from `forecast_trace.py`")
    w("/ `forecast_trace_real.py` unchanged.")
    w("")
    w("| stream | seg | buckets | nonzero | requests | autocorr (lag) | "
      "persistence | trend | holt | seasonal | best | seasonal/trend |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|")

    included = [r for r in rows if r["included"]]
    h1_scope, h1_viol, h2_scope, h2_viol = [], [], [], []
    for r in rows:
        if not r["included"]:
            w(f"| {r['stream']} | {r['segment']} | {r['buckets']} | {r['nonzero']} "
              f"| {r['requests']:,} | excluded (gate) | | | | | | |")
            continue
        rm = r["rmse"]
        ratio = rm["seasonal"] / rm["trend"] if rm["trend"] else float("inf")
        w(f"| {r['stream']} | {r['segment']} | {r['buckets']} | {r['nonzero']} "
          f"| {r['requests']:,} | {r['corr']:.2f} (@{r['lag']}h) "
          f"| {rm['persistence']:.3f} | {rm['trend']:.3f} | {rm['holt']:.3f} "
          f"| {rm['seasonal']:.3f} | **{r['best']}** | {ratio:.3f} |")
        if r["corr"] < BOUNDARY:
            h1_scope.append(r)
            if ratio < MATERIAL:
                h1_viol.append(r)
        else:
            h2_scope.append(r)
            if ratio >= 1.0:
                h2_viol.append(r)

    w("")
    w("## Hypothesis outcomes")
    w("")
    verdict = "PASS" if h1_scope and not h1_viol else ("NO QUALIFYING SERIES" if not h1_scope else "FAIL")
    w(f"- **PT-H1 ({verdict}):** {len(h1_scope)} included series with autocorr < "
      f"{BOUNDARY}; {len(h1_viol)} showed a material seasonal win "
      f"(RMSE ratio < {MATERIAL}). "
      + ("Counterexamples: " + "; ".join(
          f"{r['stream']} seg{r['segment']} (ratio "
          f"{r['rmse']['seasonal']/r['rmse']['trend']:.3f}, corr {r['corr']:.2f})"
          for r in h1_viol) if h1_viol else "No counterexamples."))
    if h2_scope:
        w(f"- **PT-H2 (exploratory):** {len(h2_scope)} series at autocorr >= "
          f"{BOUNDARY}; seasonal beat trend in {len(h2_scope) - len(h2_viol)} of them."
          + (" Misses: " + "; ".join(
              f"{r['stream']} seg{r['segment']}" for r in h2_viol) if h2_viol else ""))
    else:
        w(f"- **PT-H2 (exploratory):** no included series reached autocorr {BOUNDARY}.")
    winners = sorted({r["best"] for r in included})
    w(f"- **PT-H3 (descriptive):** distinct best methods across included series: "
      f"{', '.join(winners)} ({len(winners)} of 4) — "
      + ("supports per-tenant pluggable forecasting."
         if len(winners) >= 2 else "does NOT show per-stream divergence."))
    w("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT}: {len(included)} included series, "
          f"H1 {verdict}, winners {winners}")


if __name__ == "__main__":
    main()
