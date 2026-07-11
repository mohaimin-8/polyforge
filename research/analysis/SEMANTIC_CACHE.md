# Semantic-cache hit-rate protocol (v2 Phase 3b)

Source: synthetic (4000 prompts, controllable dup-rate). Embedder: `sentence-transformers/all-MiniLM-L6-v2`. Index: exact cosine NN (numpy). Protocol: insert first half, query second half, sweep the similarity threshold.

> **Real encoder on synthetic prompts**: the synthetic set's duplicate structure was designed to exercise *lexical* discrimination; a semantic encoder correctly collapses its template families, so hit rates saturate near 1.0. This run verifies the full semantic pipeline end-to-end; the absolute numbers comparable to the published anchors (InstCache 51.34%, SCALM +63% vs GPTCache) require `--conversations` on the real LMSYS-Chat-1M parquet (gated: needs an HF account + accept).

Dollar model: a hit avoids one mid-tier call at $0.0010; savings = hit-rate × price × queries.

| threshold | adaptive hit rate | fixed (≤200) hit rate | adaptive $ saved/1k queries |
|---|---|---|---|
| 0.70 | 1.000 | 1.000 | $1.000 |
| 0.75 | 1.000 | 1.000 | $1.000 |
| 0.80 | 1.000 | 0.994 | $1.000 |
| 0.85 | 1.000 | 0.966 | $1.000 |
| 0.90 | 0.990 | 0.805 | $0.990 |
| 0.95 | 0.785 | 0.499 | $0.785 |

**At threshold 0.85: adaptive sizing hits 100.0% vs the fixed cache's 96.5% (+4%).** Adaptive keeps the whole working set the demand justifies; the fixed cache evicts recurring prompts it should have kept. The dollar column is the metric a cache-only paper cannot report: PolyForge prices each avoided call at its model tier, so a hit on an expensive tier saves more than a hit on a cheap one — hit rate and savings are not the same axis.

Reproduce: `python semantic_cache_eval.py` (synthetic+fallback) or `--conversations lmsys-chat-1m/*.parquet` with sentence-transformers installed. The LMSYS ETL that normalizes the same dataset for demand replay is `research/traces/etl_lmsys_chat1m.py`.
