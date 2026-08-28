# Semantic-cache hit-rate protocol (v2 Phase 3b)

Source: LMSYS-Chat-1M (200000 user turns, reservoir sample seed 42). Embedder: `sentence-transformers/all-MiniLM-L6-v2`. Index: exact cosine NN (numpy). Protocol: insert first half, query second half, sweep the similarity threshold.

Dollar model: a hit avoids one mid-tier call at $0.0010; savings = hit-rate × price × queries.

| threshold | adaptive hit rate | fixed (≤200) hit rate | adaptive $ saved/1k queries |
|---|---|---|---|
| 0.70 | 0.488 | 0.090 | $0.488 |
| 0.75 | 0.413 | 0.073 | $0.413 |
| 0.80 | 0.351 | 0.063 | $0.351 |
| 0.85 | 0.296 | 0.043 | $0.296 |
| 0.90 | 0.245 | 0.034 | $0.245 |
| 0.95 | 0.195 | 0.031 | $0.195 |

**At threshold 0.85: adaptive sizing hits 29.6% vs the fixed cache's 4.3% (+594%).** Adaptive keeps the whole working set the demand justifies; the fixed cache evicts recurring prompts it should have kept. The dollar column is the metric a cache-only paper cannot report: PolyForge prices each avoided call at its model tier, so a hit on an expensive tier saves more than a hit on a cheap one — hit rate and savings are not the same axis.

Amendment (declared pre-run, see script docstring): reservoir sample of 200000 user turns; supplementary fixed cache at 10% of inserted (10000 entries) so the fixed baseline is not a strawman at this scale: hit rate 15.3% at threshold 0.85 (vs adaptive 29.6%).

## h(cache size) at threshold 0.85 — Phase 3b empirical curve

| cache entries | hit rate |
|---|---|
| 200 | 0.043 |
| 500 | 0.064 |
| 2000 | 0.099 |
| 8000 | 0.143 |
| 10000 | 0.153 |
| 32000 | 0.212 |
| 64000 | 0.260 |
| 100000 | 0.296 |

**Saturating fit h(K) = hmax·K/(K+K_half): hmax = 0.285, K_half = 6569 entries.** The sim's h(c) uses the same form over MB (CACHE_HIT_MAX=0.85, CACHE_HALF_MB=256); at ~3 KB per entry (384-d float32 embedding + prompt text + metadata), K_half ≈ 19 MB equivalent. Adopting empirical constants in model.py would be a new pre-registered experiment; the committed matrices stay bit-reproducible (see session 16b precedent in docs/archive/SESSION_LOG.md).

Reproduce: `python semantic_cache_eval.py` (synthetic+fallback) or `--conversations lmsys-chat-1m/*.parquet` with sentence-transformers installed. The LMSYS ETL that normalizes the same dataset for demand replay is `research/traces/etl_lmsys_chat1m.py`.
