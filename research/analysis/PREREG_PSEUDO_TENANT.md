# Pre-registration: pseudo-per-tenant forecast decomposition (Wave 2, closes DEFENSE_QA #18)

Registered 2026-07-16 (session 20). Committed and pushed **before** any run;
push event is the timestamp anchor; to be mirrored to OSF prospectively per
DEFENSE_QA #17.

## Question

The per-tenant forecasting claim currently extrapolates across one
aggregation level: BurstGPT and Azure results are aggregate demand streams.
DEFENSE_QA #18 names the closure — a pseudo-per-tenant decomposition of the
*same* traces under the *same* frozen protocol. If "the winning forecaster
tracks each stream's measured periodicity" is real, it must keep holding
when the aggregates are split into their natural sub-streams; and if
per-tenant pluggability matters, different sub-streams should pick
different winners.

## Data (already on disk, no new downloads)

- BurstGPT v2.0 raw parts (committed fetch script, kept raws): sub-streams
  = Model × Log Type, i.e. up to {ChatGPT, GPT-4} × {Conversation log,
  API log}. Values other than these, if present, are reported and included
  by the same rule.
- Azure LLM 2024 week: the two released streams (code, conv) — already
  per-stream by construction; they enter the same table for completeness.

## Protocol (frozen = the committed one, functions imported not reimplemented)

- Hourly buckets (3600 s), one rps series per sub-stream, built by the same
  bucketing rule as `simulate.load_trace_buckets` (count/interval).
- Forecast error: `forecast_trace.rolling_one_step` (the real
  `controller.Forecast`, one-step-ahead MAE/RMSE) over
  `METHODS = (persistence, trend, holt, seasonal)`.
- Periodicity: `forecast_trace.detected_period` (best detrended
  autocorrelation over lags 8–48).
- BurstGPT's ~104-day collection silence: series split into contiguous
  segments by `forecast_trace_real.contiguous_segments` (≥ 24 consecutive
  empty hourly buckets = a break); (sub-stream × segment) is the analysis
  unit, exactly as FORECAST_TRACE_REAL.md treated the aggregate.
- Inclusion gate (frozen): a (sub-stream × segment) series enters the
  hypothesis count only with ≥ 168 non-empty hourly buckets (one week) and
  ≥ 5,000 requests; excluded series are still listed in the output table,
  marked excluded.
- Script: `research/analysis/pseudo_tenant.py`, committed with this file;
  one execution, output to `PSEUDO_TENANT.md`.

## Hypotheses (frozen; boundary fixed from prior measurements before this run:
aggregate autocorr 0.49 → seasonal never won, 0.57 → seasonal won, so the
declared decision boundary is 0.5)

- **PT-H1 (confirmatory):** every included series whose max detrended
  autocorrelation (lags 8–48) is **< 0.5** shows no material seasonal win:
  RMSE(seasonal) > 0.95 × RMSE(trend). H1 holds only if true for **all**
  such series.
- **PT-H2 (exploratory, may have few qualifying series):** included series
  with autocorrelation **≥ 0.5** show RMSE(seasonal) < RMSE(trend).
- **PT-H3 (descriptive, the pluggability claim):** at least two distinct
  best methods appear across included series — different sub-streams want
  different forecasters, which is what a per-tenant pluggable forecasting
  layer is *for*.

## Outcome handling and stopping rule

Results as measured to `PSEUDO_TENANT.md`, including any PT-H1
counterexample verbatim (a strongly periodic sub-stream hiding inside a
weakly periodic aggregate would *refute the boundary, not embarrass the
system* — it would be reported as exactly that). One execution; the
inclusion gate and boundary may not move after this push; no further
decompositions (finer than Model × Log Type) in this campaign.
