# Semantic-cache hit precision on real LMSYS-Chat-1M

Protocol frozen in `cache_hit_precision.py` (committed pre-run). n = 100000 (prompt, response) pairs, seed 42; insert first half, query second half; adaptive (unbounded) cache; embedder MiniLM-L6-v2 both sides; responses truncated to 1000 chars. A hit at prompt-cosine tau serves the argmax neighbor's stored response; it counts CORRECT when the response-agreement cosine >= rho (headline rho = 0.7, declared pre-run).

| tau | hit rate | precision @0.60 | precision @0.70 (headline) | precision @0.80 | incorrect hits /1k queries (rho=0.70) |
|---|---|---|---|---|---|
| 0.70 | 0.411 | 0.366 | 0.249 | 0.144 | 308.6 |
| 0.75 | 0.347 | 0.385 | 0.273 | 0.164 | 252.1 |
| 0.80 | 0.291 | 0.401 | 0.293 | 0.184 | 205.6 |
| 0.85 | 0.241 | 0.416 | 0.313 | 0.205 | 165.5 |
| 0.90 | 0.197 | 0.419 | 0.328 | 0.224 | 132.3 |
| 0.95 | 0.156 | 0.417 | 0.338 | 0.247 | 103.4 |

## Strata at the operating threshold tau = 0.85 (rho = 0.7)

Overall: hit rate 24.1%, precision 0.313, 165.5 incorrect hits per 1,000 queries.

- Same-model pairs (style confounder removed): precision 0.443 over 4943 hits (41% of hits).
- Cross-model pairs (style confounder present): precision 0.223 over 7104 hits (59% of hits).
- First-turn-only pairs (context-free, cacheable-traffic view): precision 0.353 over 7362 hits (61% of hits).

Declared readings (directions frozen pre-run, magnitudes measured):
- Cross-model style divergence deflates agreement, so overall precision is a conservative LOWER bound; the same-model stratum bounds the effect.
- Low agreement on mid-conversation turns is a REAL caching error (context-blind serving), not a proxy artifact; the first-turn stratum shows the cache's precision on traffic it should serve.
- No quality-adjusted dollar figure is reported: pricing a wrong answer is application-specific. Combine the hit-rate protocol's savings-per-1k with the incorrect-hits-per-1k column above.

Reproduce: `python cache_hit_precision.py --conversations '<lmsys parquet glob>'` (gated dataset: HF account + license accept + token).
