# WP6 check: the old protocol reproduced

Sample: 200000 user turns (all six LMSYS parquet files), reservoir seed 42, via the old script's loader; encoder: the old script's MiniLM wrapper (3202.9 s to embed, 0 = cached).

## (b) WP6 engine vs the old functions, query by query

| cache size K | max abs score difference | hit-count differences at the six thresholds |
|---|---|---|
| 200 | 1.78e-15 | [0] |
| 500 | 1.55e-15 | [0] |
| 2000 | 1.78e-15 | [0] |
| 8000 | 1.89e-15 | [0] |
| 10000 | 1.89e-15 | [0] |
| 32000 | 1.89e-15 | [0] |
| 64000 | 1.89e-15 | [0] |
| 100000 | 1.89e-15 | [0] |

Engine identical to the old protocol: **True**.

## (a) old functions vs the published table (SEMANTIC_CACHE.md)

| row | tau | published | reproduced |
|---|---|---|---|
| adaptive | 0.70 | 0.488 | 0.488 |
| fixed200 | 0.70 | 0.090 | 0.090 |
| adaptive | 0.75 | 0.413 | 0.413 |
| fixed200 | 0.75 | 0.073 | 0.073 |
| adaptive | 0.80 | 0.351 | 0.351 |
| fixed200 | 0.80 | 0.063 | 0.063 |
| adaptive | 0.85 | 0.296 | 0.296 |
| fixed200 | 0.85 | 0.043 | 0.043 |
| adaptive | 0.90 | 0.245 | 0.245 |
| fixed200 | 0.90 | 0.034 | 0.034 |
| adaptive | 0.95 | 0.195 | 0.195 |
| fixed200 | 0.95 | 0.031 | 0.031 |
| ladder K=200 | 0.85 | 0.043 | 0.043 |
| ladder K=500 | 0.85 | 0.064 | 0.064 |
| ladder K=2000 | 0.85 | 0.099 | 0.099 |
| ladder K=8000 | 0.85 | 0.143 | 0.143 |
| ladder K=10000 | 0.85 | 0.153 | 0.153 |
| ladder K=32000 | 0.85 | 0.212 | 0.212 |
| ladder K=64000 | 0.85 | 0.260 | 0.260 |
| ladder K=100000 | 0.85 | 0.296 | 0.296 |

Largest difference at three decimals: 0.000; reproduced: **True**.
