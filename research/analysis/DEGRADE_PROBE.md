# Graceful degradation probe — cheapest affordable tier vs designed outage

Campaign of `PREREG_DEGRADE.md` (pushed before the run); mechanics in `degrade_probe.py`, rows in `degrade_probe.csv`. Both arms are the same controller on identical seeded instances; only the infeasible-lattice fallback differs (published shed-to-none vs `degrade_gracefully`).

Context: the fallback binds in **zero** rows of every committed campaign (v1/v2/v3/hk/lm) at their $5/hr budgets, so this is measured out of the headline envelope, in the budget band where the outage actually occurs.

| hourly budget | outage share (shed) | outage share (graceful) | mean violation (shed) | mean violation (graceful) |
|---|---|---|---|---|
| $0.02 | 100.0% | 100.0% | 1.000 | 1.000 |
| $0.03 | 100.0% | 100.0% | 1.000 | 1.000 |
| $0.05 | 100.0% | 100.0% | 1.000 | 1.000 |
| $0.08 | 100.0% | 100.0% | 1.000 | 1.000 |
| $0.12 | 100.0% | 100.0% | 1.000 | 1.000 |
| $0.20 | 99.7% | 99.7% | 1.000 | 1.000 |
| $0.40 | 98.7% | 98.7% | 1.000 | 1.000 |

**DG-H1 (graceful STRICTLY reduces outage share where the fallback binds): NOT MET — graceful equals shed on every budget.**
**DG-H2 (graceful introduces no runaway cost — stays within a 4x band of the shed cost): PASS.**

## Honest reading — the fix is a measured no-op, and that is the finding

DG-H1 is **not met**: graceful degradation produces the identical outage share to the published shed on every budget. The diagnosis is the useful result. The infeasible-lattice fallback binds only when even the **cheapest serving tier's per-request cost exceeds the tenant's per-step budget** — and that cost is dominated by the tier's inference price on the *miss* stream. Graceful's floor candidate uses `cache_mb=0`, which *maximises* the miss rate, so it can never fit a budget the optimizer's already-cached small-tier candidates could not. When the budget cannot afford to serve the demand at all, a designed `tier=none` outage is the **correct budget-respecting response**, not a design flaw. DG-H2 holds: graceful never serves outside budget.

The value here is the measurement: a proposed robustness fix, pre-registered, measured, and found unnecessary — the published shed-to-none was already right. The option ships default-off (committed campaigns replay bit-identically) and is retained only as a documented, budget-safe alternative; no headline number changes. A cache-preserving graceful variant (keep enough cache to cut misses under a tight budget) is the only design that could differ, and it is named as future work, not run here.
