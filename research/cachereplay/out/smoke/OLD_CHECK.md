# WP6 check: the old protocol reproduced

Sample: 600 user turns (first 1,000 conversations of file 0), reservoir seed 42, via the old script's loader; encoder: the old script's MiniLM wrapper (0.0 s to embed, 0 = cached).

## (b) WP6 engine vs the old functions, query by query

| cache size K | max abs score difference | hit-count differences at the six thresholds |
|---|---|---|
| 30 | 7.77e-16 | [0] |
| 200 | 1.67e-15 | [0] |
| 300 | 1.67e-15 | [0] |

Engine identical to the old protocol: **True**.

## (a) old functions vs the published table (SEMANTIC_CACHE.md)

Not applicable to this sample (the published numbers are for the 200,000-turn reservoir; run `--preset full`).
