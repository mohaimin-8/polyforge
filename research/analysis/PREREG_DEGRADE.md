# Pre-registration: graceful-degradation policy probe (gap 4.4)

Registered 2026-07-19 (session 25). Committed and pushed **before** the
run; the push event is the timestamp anchor, mirrored to
`OSF_REGISTRATION.md` prospectively per DEFENSE_QA #17.

## Question

When the action lattice is entirely infeasible (budget + cluster caps
admit no candidate), the published controller sheds to
`TenantState(replicas=min, cache_mb=0, tier="none")` — a designed AI
outage (`NO_TIER_LATENCY_MS = 30000 ms`). An operator would usually
prefer degraded *service* to an outage. `degrade_gracefully=True` instead
serves on the cheapest **affordable** tier at the replica floor, shedding
to `none` only if even that exceeds budget or the cluster replica cap —
so it never introduces a budget violation.

Two facts fix the design of this experiment:

1. The fallback is **result-bearing**, so the option ships default-off
   (bit-identical committed campaigns; property-tested both ways) and any
   measurement is pre-registered.
2. The fallback **provably never binds** in any committed campaign:
   measured 0 shed-signature rows (`tier='none' AND cache_mb=0`) across
   `raw_sim` (v1), `raw_sim_v2`, `raw_sim_v3`, `raw_sim_hk`, `raw_sim_lm`
   — 0 / 57600, 0 / 57600, 0 / 46080, 0 / 57600, 0 / 57600. A headline
   rerun would therefore be identically null. So the headline is **not**
   rerun (the null is established by the query above); instead a targeted
   probe measures the fix in the budget band where the outage occurs.

## Design (frozen)

- Script: `research/analysis/degrade_probe.py` (this commit). 20 seeded
  instances (seeds 0–19, generator frozen in the script) per budget in a
  frozen sweep {0.02, 0.03, 0.05, 0.08, 0.12, 0.20, 0.40} $/hr, 4
  premium tenants, AI-heavy demand (chat 6–14, agent 2–6 rps), a tight
  shared replica cap (2× tenant count), tenants started high enough that
  the move-clamped neighbourhood is expensive — the regime where a
  one-step floor jump is affordable but the neighbourhood is not. Both
  arms are the same controller on identical Poisson-jittered instances;
  only the fallback differs.
- Metrics: outage step-share (fraction of tenant-steps at `tier='none'`),
  mean violation, aggregate cost, per arm per budget.

## Hypotheses (frozen)

- **DG-H1:** wherever the fallback binds (shed outage share > 0), the
  graceful arm's outage share is ≤ the shed arm's, and strictly less on
  at least one budget. (Serving instead of blacking out.)
- **DG-H2:** the graceful arm introduces no runaway cost — its aggregate
  cost stays within a 4× band of the shed arm's (it only ever serves
  within each tenant's per-step budget, so it cannot blow up spend).

## Outcome handling

Results to `DEGRADE_PROBE.md` as measured (a FAIL is published verbatim
and the option's default stays off with the honest reading). One
execution of the frozen sweep; no widening, no reseeding. The headline
null (fallback never binds at matrix budgets) stands on the committed-DB
query above and needs no rerun.
