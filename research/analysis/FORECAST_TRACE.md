# Forecast ablation on real-trace periodicity (v2 Phase 3a)

Trace: `burstgpt_synth.csv.gz` bucketed at 3600s (168 buckets); one total-rps series, rolling one-step-ahead forecast with the real `controller.Forecast`. The trace is the committed synthetic BurstGPT stand-in unless `--trace` points at the real normalized parquet — same code path either way.

**Detected period: 24 buckets at autocorrelation 0.85** (above the 0.4 activation threshold — seasonal engages its seasonal-naive forecast). At 3600s buckets a 24-bucket period is the trace's daily cycle.

| forecaster | MAE (rps) | RMSE (rps) | vs trend RMSE |
|---|---|---|---|
| persistence | 0.073 | 0.113 | -2.0% |
| trend | 0.071 | 0.116 | +0.0% |
| holt | 0.102 | 0.157 | +35.4% |
| seasonal | 0.029 | 0.064 | -44.8% |

**Lowest-error forecaster: `seasonal`.** On real periodicity the seasonal method that was *neutral* on synthetic noise now has a period to exploit and leads, confirming the V2_README Phase 3a prediction.

Note: the control-loop forecast ablation (forecasters.yaml, 200 runs) operates at the 10s control interval where sub-minute periods dominate; this harness is the complementary coarse-resolution view where the daily cycle lives. Both use the identical forecaster code.

The measurement on the real BurstGPT v2.0 release (10.63M requests) lives in `FORECAST_TRACE_REAL.md` (`forecast_trace_real.py`, same protocol functions); where the stand-in and the real trace disagree, the real trace is the reading that counts.
