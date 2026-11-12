# WP6 hit correctness: QQP precision-recall and the LMSYS proxy

QQP (validation): 500 labelled pairs, 175 duplicates, 999 questions embedded with `sentence-transformers/all-MiniLM-L6-v2` as `gateway` key text in 54.7 s. Average precision 0.728. Curve: `qqp_pr_curve.csv`, `qqp_pr_curve.png`.

| tau | QQP pairs >= tau | precision | recall | wrong-hit rate [Wilson 95%] | LMSYS proxy wrong-hit rate (rho 0.70) | LMSYS hit rate |
|---|---|---|---|---|---|---|
| 0.75 | 241 | 0.618 | 0.851 | 0.382 [0.323, 0.444] | 0.727 | 0.347 |
| 0.80 | 206 | 0.660 | 0.777 | 0.340 [0.279, 0.407] | 0.707 | 0.291 |
| 0.85 | 151 | 0.728 | 0.629 | 0.272 [0.207, 0.347] | 0.687 | 0.241 |
| 0.90 | 96 | 0.719 | 0.394 | 0.281 [0.201, 0.378] | 0.672 | 0.197 |
| 0.95 | 44 | 0.841 | 0.211 | 0.159 [0.079, 0.294] | 0.662 | 0.156 |

LMSYS proxy: 100k reservoir (prompt, response) pairs, all user turns, raw text, insert first half, query second half, unbounded cache; correct if response cosine >= 0.70; source `research/analysis/CACHE_PRECISION.md` (first-turn stratum precision at 0.85: 0.353).

- QQP pairs were sampled to be topically related (about 37% are duplicates), so its precision is not the precision of a cache's hits; it bounds how often a pair at a given cosine is a true paraphrase.
- The LMSYS proxy counts a hit wrong when responses disagree, including stylistic disagreement between models; it is a pessimistic bound (CACHE_PRECISION.md).
