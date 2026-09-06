"""Forecast ablation on a SECOND real LLM trace — Azure LLM inference 2024.

External-validity restatement: DEFENSE_QA.md #12 names "a second LLM
demand trace" as the highest-value external-validity addition, and the
Azure LLM inference traces (AzurePublicDataset 2024 release, May 10-19
2024, one week, two production workloads: conversation + code completion;
the DynamoLLM/HPCA'25 dataset, CC-BY) are public and LLM-shaped — unlike
the descoped Azure *Functions* traces.

AMENDMENT (declared 2026-07-13 before any ablation ran): the protocol was
frozen naming the 2023 in-repo files, which hold only ~1 hour of traffic;
the 2024 release assets are the one-week data the protocol describes.
Same schema, mapping, constants, and analysis — see the ETL's amendment
note.

PROTOCOL — FROZEN BEFORE THE FIRST RUN on this trace (2026-07-13,
session 17; committed and pushed before any number was seen). It is the
*identical* decomposition applied to BurstGPT: the protocol functions are
imported from `forecast_trace.py` / `forecast_trace_real.py`, not
reimplemented, and the ETL (`research/traces/etl_azure_llm.py`) uses the
same committed token->latency constants as the BurstGPT ETL.

- Hourly buckets, total-rps series, rolling one-step-ahead forecasts with
  the real `controller.Forecast` for the same four methods
  (persistence / trend / holt / seasonal); MAE + RMSE; detected period.
- Reported per workload ("conv", "code") AND pooled: the two services are
  independent production demand streams — per-stream results are the
  external-validity signal, the pooled series is context.
- Gap handling: `contiguous_segments` (>= 24 empty hourly buckets),
  exactly as on BurstGPT; a one-week trace is expected to be one segment,
  the guard just makes the treatment identical.
- DECLARED EXPECTATION, direction not asserted: one week of production
  traffic can carry a daily cycle (lag 24, inside the seasonal detector's
  8-48 scan). On BurstGPT, seasonal did NOT transfer and damped Holt won
  (FORECAST_TRACE_REAL.md); whether that replicates, reverses, or splits
  by workload here is what this run measures. Whatever it says, it is
  reported as measured — the existing forecasting claim (Holt on real
  BurstGPT) does not depend on this file.
- One run, results to FORECAST_AZURE.md; a re-run requires a declared
  amendment.

    python ../traces/etl_azure_llm.py     # once, ~1 MB download
    python forecast_azure.py              # writes FORECAST_AZURE.md
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jcac_sim"))

import stats  # noqa: E402  (research/analysis/stats.py)

from forecast_trace import METHODS, detected_period, rolling_one_step  # noqa: E402
from forecast_trace_real import contiguous_segments  # noqa: E402
from simulate import load_trace_buckets  # noqa: E402

# Written through stats.record_path so POLYFORGE_ANALYSIS_OUT can redirect it;
# it resolved to this directory unconditionally, so running the script
# overwrote the committed record and the gate could not compare it.
OUT = stats.record_path("FORECAST_AZURE.md")
TRACE = Path(__file__).resolve().parents[1] / "traces" / "out" / "azure_llm_2024.csv.gz"
INTERVAL_S = 3600


def per_tenant_series(trace: Path) -> dict[str, list[float]]:
    tenant_ids, buckets = load_trace_buckets(trace, interval_s=INTERVAL_S)
    series = {tid: [] for tid in tenant_ids}
    series["pooled"] = []
    for per_tenant in buckets:
        total = 0.0
        for tid in tenant_ids:
            rps = per_tenant[tid].total_rps()
            series[tid].append(rps)
            total += rps
        series["pooled"].append(total)
    return series


def ablate(name: str, series: list[float], lines: list[str]) -> None:
    segments = contiguous_segments(series)
    if len(segments) > 1:
        lines.append(f"- **{name}**: {len(segments)} contiguous segments "
                     "(unexpected for a one-week trace; reported per segment).")
    for si, (a, b) in enumerate(segments):
        seg = series[a:b]
        lag, corr = detected_period(seg)
        results = {m: rolling_one_step(seg, m) for m in METHODS}
        best = min(results, key=lambda m: results[m][1])
        trend_rmse = results["trend"][1] or float("nan")
        tag = f"{name}" + (f" seg{si + 1}" if len(segments) > 1 else "")
        lines.append("")
        lines.append(f"### {tag} — {len(seg)} hourly buckets, detected "
                     f"period lag {lag} (autocorr {corr:.2f})")
        lines.append("")
        lines.append("| method | MAE | RMSE | RMSE vs trend |")
        lines.append("|---|---|---|---|")
        for m in METHODS:
            mae, rmse = results[m]
            delta = (rmse - trend_rmse) / trend_rmse if trend_rmse else 0.0
            marker = " **<- best**" if m == best else ""
            lines.append(f"| {m} | {mae:.3f} | {rmse:.3f} | {delta:+.1%}{marker} |")


def main() -> None:
    if not TRACE.exists():
        raise SystemExit(f"{TRACE} missing — run research/traces/etl_azure_llm.py first")
    series = per_tenant_series(TRACE)

    lines = ["# Forecast ablation — Azure LLM inference traces (2024 release)", ""]
    lines.append("Protocol frozen in `forecast_azure.py` (committed pre-run); "
                 "identical decomposition and constants as the BurstGPT "
                 "restatement (`FORECAST_TRACE_REAL.md`). Two production "
                 "workloads analyzed as independent demand streams, pooled "
                 "series for context. One week, hourly buckets.")
    for name in sorted(series):
        ablate(name, series[name], lines)
    lines.append("")
    lines.append("Reading rules (frozen pre-run): per-stream results are the "
                 "external-validity signal; the existing forecasting claim "
                 "(damped Holt on real BurstGPT) stands regardless of this "
                 "file's outcome, and any seasonal result here is bounded by "
                 "the v3 H2' mechanism scope (periodicity must actually be "
                 "present).")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
