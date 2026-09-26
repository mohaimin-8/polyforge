# Advanced-work results (Tier 2 + security)

## Forecast ablation (fig. 13)

Same JCAC controller, four forecasters, paired by cell vs the base linear trend (the forecaster the headline 1,800-run evaluation used). Violation means, lower is better:

| forecaster | mean violation | vs trend | p | d_z |
|---|---|---|---|---|
| persistence | 0.0335 | -18.6% | 0.0067 | -0.40 |
| trend (base) | 0.0411 | +0.0% | 1 | +0.00 |
| Holt | 0.0344 | -16.3% | 0.0027 | -0.45 |
| seasonal | 0.0407 | -1.2% | 0.74 | -0.05 |

**The honest, useful finding — an ablation that improves our own system:** the W30 linear-trend forecaster *overreacts* to single-bucket noise. Damped Holt smoothing beats it by 16% on violation (p = 0.0027, d_z = -0.45) at statistically equal cost, and persistence — which simply refuses to extrapolate noise — does about as well. Online-seasonal detection helps only where a genuine period exists (neutral here). **Holt is the recommended default going forward.** We report this rather than silently swapping it in, because the committed 1,800-run headline used trend — so this gain *stacks on top of* the reported results rather than being folded into them. Lookahead still matters (all methods beat a reactive scaler); the lesson is that the lookahead must be noise-robust.

## Reconfiguration realism (fig. 14-15)

Replica scale-ups take one interval to serve and grown caches start half-warm, billed at the nominal configuration immediately. No controller is told. This is where hysteresis and self-calibration earn their keep.

- Self-calibration vs the base controller under realism: d_z = +0.46, p = 0.029 on SLO violation (paired) — a real, measurable gain from learning effective capacity online.
- The reactive baselines' violations rise more than PolyForge's under realism, because their new capacity keeps arriving one step after the burst it was bought for (fig. 14).

## Cache timing side channel — security contribution (fig. 16)

A shared semantic cache leaks tenant prompt membership at AUC 0.88 from response time alone; PolyForge's per-tenant cache returns the attacker to chance (AUC 0.50). Naive equal-split isolation costs 29% of the aggregate hit rate; PolyForge's demand-proportional sizing recovers 18% of that penalty.

- **Attack**: a shared semantic cache leaks tenant prompt membership at AUC **0.88** from response time alone.
- **Defense**: PolyForge's per-tenant cache returns the attacker to chance (AUC **0.50**), eliminating essentially all (100%) of the exploitable signal above chance. The Go invariant behind this is `TestCacheGivesNoCrossTenantHit` (internal/ai/gateway).
- **Cost of isolation**: naive equal splitting loses 29% of the aggregate hit rate; the joint planner's demand-proportional sizing cuts that to 24% (recovering 18% of the penalty), so isolation still costs 24% of the hit rate. The proportional split gives the largest tenant 1,725 MB, above the model's 1,024 MB top cache level; capped there and re-split, it recovers 19%, so the figure is not flattered by the infeasible allocation (audit 2026-09-26).

This is a novel framing: prior semantic-cache work optimizes hit rate; treating the shared cache as a **cross-tenant covert channel** and quantifying the isolation/efficiency trade-off is, to our knowledge, new — and PolyForge's W28 per-tenant design already implements the defense.

## Defense frontier — v2 Phase 5 (fig. 17)

The two standard timing-channel mitigations, swept over fixed grids (committed, not tuned) and scored on the identical attack, against per-tenant partitioning on the same leakage/cost axes:

| defense | best worst-case AUC | latency benefit given up | hits retained |
|---|---|---|---|
| none (best of grid) | 0.88 | 0% | 100% |
| pad (best of grid) | 0.73 | 28% | 100% |
| ttl (best of grid) | 0.53 | 90% | 10% |
| per-tenant partition | 0.50 | 24% | 76% |

**The claim upgrades from 'we have a defense' to 'partitioning dominates the known frontier.'** Response-time padding never drives leakage below AUC 0.73 (it equalizes hit/miss latency only by padding *everything* to the miss time, discarding the benefit entirely); TTL jitter only reaches chance by discarding ~90% of hits (both latency and inference dollars). Per-tenant partitioning reaches chance-level AUC (0.50) at 24% latency cost while retaining 76% of hits — strictly lower-left of either mitigation curve. No point on either curve dominates it.
