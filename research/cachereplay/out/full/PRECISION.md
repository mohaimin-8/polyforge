# WP6 hit correctness: QQP precision-recall and the LMSYS proxy

QQP (train+validation): 404276 labelled pairs, 149263 duplicates, 537346 questions embedded with `sentence-transformers/all-MiniLM-L6-v2` as `gateway` key text in 2374.7 s. Average precision 0.761. Curve: `qqp_pr_curve.csv`, `qqp_pr_curve.png`.

| tau | QQP pairs >= tau | precision | recall | wrong-hit rate [Wilson 95%] | LMSYS proxy wrong-hit rate (rho 0.70) | LMSYS hit rate |
|---|---|---|---|---|---|---|
| 0.75 | 209637 | 0.629 | 0.883 | 0.371 [0.369, 0.374] | 0.727 | 0.347 |
| 0.80 | 173512 | 0.672 | 0.781 | 0.328 [0.326, 0.331] | 0.707 | 0.291 |
| 0.85 | 130953 | 0.723 | 0.634 | 0.277 [0.275, 0.280] | 0.687 | 0.241 |
| 0.90 | 84335 | 0.783 | 0.442 | 0.217 [0.214, 0.220] | 0.672 | 0.197 |
| 0.95 | 38512 | 0.858 | 0.221 | 0.142 [0.138, 0.145] | 0.662 | 0.156 |

LMSYS proxy: 100k reservoir (prompt, response) pairs, all user turns, raw text, insert first half, query second half, unbounded cache; correct if response cosine >= 0.70; source `research/analysis/CACHE_PRECISION.md` (first-turn stratum precision at 0.85: 0.353).

- QQP pairs were sampled to be topically related (about 37% are duplicates), so its precision is not the precision of a cache's hits; it bounds how often a pair at a given cosine is a true paraphrase.
- The LMSYS proxy counts a hit wrong when responses disagree, including stylistic disagreement between models; it is a pessimistic bound (CACHE_PRECISION.md).
