# Forecast ablation on the real BurstGPT v2.0 trace (Phase 3a, completed)

Trace: `burstgpt_real.csv.gz` — the normalized full BurstGPT v2.0 release (10,632,194 real Azure OpenAI/ChatGPT requests; `research/traces/fetch_burstgpt.py`), bucketed at 3600s (8040 buckets). Protocol functions are imported unchanged from `forecast_trace.py`: one total-rps series, rolling one-step-ahead forecast with the real `controller.Forecast`.

The release was collected in two periods with a ~104-day silence between parts 2 and 3; the ablation is reported per contiguous segment (break = ≥24 consecutive empty hourly buckets) because the inter-collection silence is missing data, not zero demand. The gap-inclusive full series is reported below it for transparency.

**Headline reading (as measured): the synthetic stand-in's seasonal prediction does not transfer to the real trace.** The real series' periodicity is far weaker than the stand-in's textbook diurnal (best autocorrelation ≤0.49 at any lag ≤48 h), and the seasonal-naive forecast never beats trend meaningfully here. What the real trace *does* confirm is the W36+ control-loop ablation's evidence-based recommendation: damped **Holt** — exactly the forecaster `jcac_v2` runs — leads segment 1 and the persistence/Holt family leads everywhere, while the reactive `trend` default is the worst choice on real data. Seasonal's value is regime-specific: it needs strong periodicity (RESULTS_V3.md H2′ confirms the mechanism where that regime exists).

## Segment 1 — buckets 0..2904 (121 days)

**Best-autocorrelation lag: 8 buckets at 0.49** (above the 0.4 activation threshold; a genuine daily cycle would put the best lag at 24 buckets — the real trace's strongest structure is weaker and shorter than the stand-in assumed).

| forecaster | MAE (rps) | RMSE (rps) | vs trend RMSE |
|---|---|---|---|
| persistence | 0.319 | 0.721 | -24.2% |
| trend | 0.412 | 0.951 | +0.0% |
| holt | 0.321 | 0.695 | -26.9% |
| seasonal | 0.411 | 0.950 | -0.1% |

**Lowest-error forecaster: `holt`.**

## Segment 2 — buckets 5400..8040 (110 days)

**Best-autocorrelation lag: 8 buckets at 0.36** (below the 0.4 activation threshold; a genuine daily cycle would put the best lag at 24 buckets — the real trace's strongest structure is weaker and shorter than the stand-in assumed).

| forecaster | MAE (rps) | RMSE (rps) | vs trend RMSE |
|---|---|---|---|
| persistence | 0.230 | 0.656 | -16.6% |
| trend | 0.275 | 0.787 | +0.0% |
| holt | 0.288 | 0.733 | -6.9% |
| seasonal | 0.284 | 0.804 | +2.1% |

**Lowest-error forecaster: `persistence`.**

## Full release including the collection gap (transparency)

Detected period 8 @ autocorrelation 0.45; the ~104-day zero-filled gap suppresses the daily autocorrelation and rewards predicting the silence, which is why these numbers are not the headline reading.

| forecaster | MAE (rps) | RMSE (rps) | vs trend RMSE |
|---|---|---|---|
| persistence | 0.191 | 0.574 | -21.2% |
| trend | 0.239 | 0.728 | +0.0% |
| holt | 0.210 | 0.592 | -18.6% |
| seasonal | 0.242 | 0.733 | +0.8% |

Lowest-error forecaster on the gap-inclusive series: `persistence`.

Reproduce: `python ../traces/fetch_burstgpt.py` then `python forecast_trace_real.py`. The synthetic stand-in's ablation (`FORECAST_TRACE.md`) is kept alongside, regenerable via `python forecast_trace.py`.
