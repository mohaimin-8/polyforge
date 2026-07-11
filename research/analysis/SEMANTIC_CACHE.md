# Semantic-cache hit-rate protocol (v2 Phase 3b)

Source: synthetic (4000 prompts, controllable dup-rate). Embedder: `hash-3gram (fallback, lexical)`. Index: exact cosine NN (numpy). Protocol: insert first half, query second half, sweep the similarity threshold.

> **Fallback embedder active** (sentence-transformers not installed): absolute hit rates below are *lexical*, not semantic. The protocol, curves, and fixed-vs-adaptive/dollar comparisons are the exact code the real run uses — install sentence-transformers and pass `--conversations` for the headline semantic numbers (anchors: InstCache 51.34%, SCALM +63% vs GPTCache).

Dollar model: a hit avoids one mid-tier call at $0.0010; savings = hit-rate × price × queries.

| threshold | adaptive hit rate | fixed (≤200) hit rate | adaptive $ saved/1k queries |
|---|---|---|---|
| 0.70 | 1.000 | 0.984 | $1.000 |
| 0.75 | 0.910 | 0.620 | $0.910 |
| 0.80 | 0.496 | 0.479 | $0.496 |
| 0.85 | 0.478 | 0.464 | $0.478 |
| 0.90 | 0.478 | 0.404 | $0.478 |
| 0.95 | 0.270 | 0.079 | $0.270 |

**At threshold 0.85: adaptive sizing hits 47.8% vs the fixed cache's 46.4% (+3%).** Adaptive keeps the whole working set the demand justifies; the fixed cache evicts recurring prompts it should have kept. The dollar column is the metric a cache-only paper cannot report: PolyForge prices each avoided call at its model tier, so a hit on an expensive tier saves more than a hit on a cheap one — hit rate and savings are not the same axis.

Reproduce: `python semantic_cache_eval.py` (synthetic+fallback) or `--conversations lmsys-chat-1m/*.parquet` with sentence-transformers installed. The LMSYS ETL that normalizes the same dataset for demand replay is `research/traces/etl_lmsys_chat1m.py`.
