"""v2 Phase 3a: forecast ablation on a real-shaped trace's periodicity.

The synthetic-workload forecast ablation (session 11) found Holt beats the
W30 linear-trend default and seasonal was *neutral* — because the synthetic
demand has no period for it to lock onto. This harness re-runs the same four
forecasters (the real `controller.Forecast`) on a demand series bucketed
from a normalized trace at a resolution where the trace's daily cycle
becomes a ~24-bucket period — exactly the regime V2_README predicts seasonal
should finally activate.

Protocol: bucket the trace hourly, aggregate to a total chat-rps series,
then roll one-step-ahead: feed each observation to the forecaster, compare
its next-step prediction to the actual. Report MAE/RMSE per method and the
detected seasonal period. Runs on the committed synthetic trace by default;
point --trace at the real BurstGPT normalized parquet for the headline
number.

    python forecast_trace.py --trace ../traces/out/burstgpt_synth.csv.gz
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jcac_sim"))
from controller import Forecast  # noqa: E402
from model import Demand  # noqa: E402
from simulate import load_trace_buckets  # noqa: E402

METHODS = ("persistence", "trend", "holt", "seasonal")
OUT = Path(__file__).resolve().parent / "FORECAST_TRACE.md"


def total_chat_series(trace: Path, interval_s: int) -> list[float]:
    """Aggregate the trace into one total-rps value per bucket."""
    _, buckets = load_trace_buckets(trace, interval_s=interval_s)
    series = []
    for per_tenant in buckets:
        series.append(sum(d.total_rps() for d in per_tenant.values()))
    return series


def rolling_one_step(series: list[float], method: str) -> tuple[float, float]:
    """Rolling one-step-ahead forecast error (MAE, RMSE) for one method,
    driving the real Forecast class exactly as the controller does."""
    fc = Forecast(method=method)
    abs_err, sq_err, n = 0.0, 0.0, 0
    for i, value in enumerate(series):
        fc.observe(Demand(rps={"chat": value}))
        if i + 1 < len(series):
            pred = fc.horizon()[0].rps.get("chat", 0.0)
            actual = series[i + 1]
            abs_err += abs(pred - actual)
            sq_err += (pred - actual) ** 2
            n += 1
    if n == 0:
        return 0.0, 0.0
    return abs_err / n, math.sqrt(sq_err / n)


def detected_period(series: list[float]) -> tuple[int, float]:
    """The seasonal detector's own logic, reported once over the full
    series: best-autocorrelation lag on the detrended signal."""
    n = len(series)
    xbar = (n - 1) / 2.0
    ybar = sum(series) / n
    sxx = sum((i - xbar) ** 2 for i in range(n)) or 1.0
    slope = sum((i - xbar) * (series[i] - ybar) for i in range(n)) / sxx
    resid = [series[i] - (ybar + slope * (i - xbar)) for i in range(n)]
    var = sum(v * v for v in resid) or 1.0
    best_lag, best_corr = 0, 0.0
    for lag in range(8, min(48, n // 2) + 1):
        cov = sum(resid[i] * resid[i - lag] for i in range(lag, n))
        corr = cov / var
        if corr > best_corr:
            best_lag, best_corr = lag, corr
    return best_lag, best_corr


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trace", default=str(
        Path(__file__).resolve().parents[1] / "traces" / "out" / "burstgpt_synth.csv.gz"))
    ap.add_argument("--interval-s", type=int, default=3600, help="bucket size (default hourly)")
    args = ap.parse_args()

    trace = Path(args.trace)
    series = total_chat_series(trace, args.interval_s)
    lag, corr = detected_period(series)

    results = {m: rolling_one_step(series, m) for m in METHODS}
    best = min(results, key=lambda m: results[m][1])

    lines = ["# Forecast ablation on real-trace periodicity (v2 Phase 3a)", ""]
    w = lines.append
    w(f"Trace: `{trace.name}` bucketed at {args.interval_s}s "
      f"({len(series)} buckets); one total-rps series, rolling one-step-ahead "
      "forecast with the real `controller.Forecast`. The trace is the "
      "committed synthetic BurstGPT stand-in unless `--trace` points at the "
      "real normalized parquet — same code path either way.")
    w("")
    w(f"**Detected period: {lag} buckets at autocorrelation {corr:.2f}** "
      f"({'above' if corr >= 0.4 else 'below'} the 0.4 activation threshold — "
      f"seasonal {'engages its seasonal-naive forecast' if corr >= 0.4 else 'falls back to trend'}). "
      f"At {args.interval_s}s buckets a 24-bucket period is the trace's daily cycle.")
    w("")
    w("| forecaster | MAE (rps) | RMSE (rps) | vs trend RMSE |")
    w("|---|---|---|---|")
    trend_rmse = results["trend"][1]
    for m in METHODS:
        mae, rmse = results[m]
        rel = (rmse - trend_rmse) / trend_rmse if trend_rmse else 0.0
        w(f"| {m} | {mae:.3f} | {rmse:.3f} | {rel:+.1%} |")
    w("")
    w(f"**Lowest-error forecaster: `{best}`.** "
      + ("On real periodicity the seasonal method that was *neutral* on "
         "synthetic noise now has a period to exploit and leads, confirming "
         "the V2_README Phase 3a prediction."
         if best == "seasonal" else
         f"On this trace `{best}` leads; seasonal "
         f"{'activates but does not win' if corr >= 0.4 else 'stays in trend fallback (period too weak at this resolution)'}. "
         "Reported as measured."))
    w("")
    w("Note: the control-loop forecast ablation (forecasters.yaml, 200 runs) "
      "operates at the 10s control interval where sub-minute periods dominate; "
      "this harness is the complementary coarse-resolution view where the "
      "daily cycle lives. Both use the identical forecaster code.")
    w("")
    w("The measurement on the real BurstGPT v2.0 release (10.63M requests) "
      "lives in `FORECAST_TRACE_REAL.md` (`forecast_trace_real.py`, same "
      "protocol functions); where the stand-in and the real trace disagree, "
      "the real trace is the reading that counts.")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}: period={lag}@{corr:.2f}, best={best}")


if __name__ == "__main__":
    main()
