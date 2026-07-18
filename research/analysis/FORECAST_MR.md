# Multi-resolution forecasting — closing the deployed-artifact gap (engineering validation, not a thesis claim)

Written 2026-07-19 (session 24). This is an **engineering closure with
unit-level validation**, deliberately not a pre-registered campaign: no
headline number changes, and no thesis claim rests on it.

## The gap it closes

The thesis's confirmed forecasting mechanism is day-scale: seasonal
forecasting wins exactly where a genuine daily cycle exists
(`FORECAST_TRACE_REAL.md`, `FORECAST_AZURE.md`, `PSEUDO_TENANT.md` — the
boundary reproduced across two traces and 19 sub-streams). But the
*deployed* planner observes at the 10 s control interval and the
`seasonal` forecaster keeps 96 observations — a **16-minute window**
whose lag search (8–48 steps) can only represent 80–480 s periods. The
shipped artifact was physically incapable of exploiting the periodicity
the offline studies proved matters. Two further service-level gaps
compounded it: the planner service had no way to select a forecaster at
all (hard-wired to the trend default), and any tenant churn or weight
retune rebuilt the controller and cold-started every tenant's forecast
history.

## What changed (all default-off or live-path-only; committed campaigns replay bit-identically)

- `Forecast(method="seasonal_mr")` (`controller.py`): keeps the fine
  96-step window *and* aggregates every 60 observations into a 10-minute
  coarse bucket, retaining 432 buckets (3 days). Period detection runs at
  both resolutions (the shared detrended-autocorrelation detector,
  factored out unchanged as `_best_period`); a credible fine season wins
  (it can phase-align within the horizon), otherwise a credible coarse
  season forecasts the level one period back — the value the daily cycle
  says is coming — and only then does it fall back to trend.
- Planner service: `--forecast {persistence,trend,holt,seasonal,
  seasonal_mr}` (`planner.py`), so the deployment can actually select the
  evidence-based recommendation; churn-safe forecast reconciliation and a
  request lock (see the session-24 planner hardening commit) make the
  history survivable long enough for day-scale detection to matter.

## Validation (unit tests, `SeasonalMRTests` / `ForecastMethodPlumbingTests`)

- A cycle spanning 40 coarse buckets — invisible from the 16-minute fine
  window — is forecast correctly by `seasonal_mr` at the pre-burst phase
  (predicts the returning high level exactly) while `seasonal` and
  `trend` predict the current low level.
- With a credible fine-window season, `seasonal_mr` output is identical
  to `seasonal` (precedence contract).
- Coarse memory is bounded (432 buckets; fine window unchanged at 96).
- The service plumbs the method to the controller and to churn arrivals.

## What would make this a *claim*

A pre-registered replay of the BurstGPT daily shape through the live
planner at 10 s cadence with `seasonal_mr` vs `holt`/`trend` — the
natural companion to the Wave 4 live plane when that gate opens. Until
then the honest statement is: the capability gap between the offline
evidence and the deployed artifact is closed and unit-validated, and the
day-scale benefit live remains unmeasured.
