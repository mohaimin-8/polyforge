# Forecast ablation — Azure LLM inference traces (2024 release)

Protocol frozen in `forecast_azure.py` (committed pre-run); identical decomposition and constants as the BurstGPT restatement (`FORECAST_TRACE_REAL.md`). Two production workloads analyzed as independent demand streams, pooled series for context. One week, hourly buckets.

### code — 168 hourly buckets, detected period lag 24 (autocorr 0.57)

| method | MAE | RMSE | RMSE vs trend |
|---|---|---|---|
| persistence | 4.769 | 7.281 | +12.7% |
| trend | 4.266 | 6.461 | +0.0% |
| holt | 6.400 | 10.242 | +58.5% |
| seasonal | 3.435 | 5.451 | -15.6% **<- best** |

### conv — 169 hourly buckets, detected period lag 8 (autocorr 0.35)

| method | MAE | RMSE | RMSE vs trend |
|---|---|---|---|
| persistence | 3.806 | 4.950 | -19.5% **<- best** |
| trend | 4.720 | 6.153 | +0.0% |
| holt | 5.090 | 6.287 | +2.2% |
| seasonal | 6.689 | 9.136 | +48.5% |

### pooled — 216 hourly buckets, detected period lag 24 (autocorr 0.58)

| method | MAE | RMSE | RMSE vs trend |
|---|---|---|---|
| persistence | 5.788 | 8.946 | -3.4% **<- best** |
| trend | 6.459 | 9.262 | +0.0% |
| holt | 8.139 | 12.153 | +31.2% |
| seasonal | 10.880 | 18.932 | +104.4% |

Reading rules (frozen pre-run): per-stream results are the external-validity signal; the existing forecasting claim (damped Holt on real BurstGPT) stands regardless of this file's outcome, and any seasonal result here is bounded by the v3 H2' mechanism scope (periodicity must actually be present).
