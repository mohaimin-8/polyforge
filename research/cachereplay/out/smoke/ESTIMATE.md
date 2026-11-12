# Runtime estimate for `python run_all.py --preset full --workers 6`

Measured 2026-11-13 05:43 in process CPU time on small samples (single thread), projected with an effective parallelism of 4.5 core-equivalents (this machine: Intel i5-1135G7, 4 cores / 8 threads, 15.7 GB). First turns: 1,000,000; tenants of 32768: all 30, conversation 27, code 3.

| stage | CPU hours | parallelism | wall hours |
|---|---|---|---|
| embed LMSYS first turns | 27.99 | 4.5 | 6.22 |
| replay grid | 3.83 | 4.5 | 0.85 |
| embed QQP questions | 2.72 | 4.5 | 0.60 |
| old check: loader | 0.01 | 1.0 | 0.01 |
| old check: embed 200,000 turns | 5.86 | 4.5 | 1.30 |
| old check: old NN + engine | 0.40 | 2.2 | 0.18 |
| fit and Go heap measurement | 0.08 | 1.0 | 0.08 |

**Total: about 9.2 h of wall time on an otherwise idle machine** (the replay grid bounds evictions as if every lookup missed, so it is an upper bound). Embedding is cached under data/, so re-running the replay or the fit skips it.

Measurements:

```
{
 "encoder": {
  "lmsys_first_turn": {
   "cpu_s_per_text": 0.10074869791666667,
   "mean_tokens": 67.27604166666667
  },
  "qqp_question": {
   "cpu_s_per_text": 0.018229166666666668,
   "mean_tokens": 16.458333333333332
  },
  "old_raw_turn": {
   "cpu_s_per_text": 0.10546875,
   "mean_tokens": 75.17708333333333
  }
 },
 "replay": {
  "costaware_us_per_lookup": 76.171875,
  "lru_us_per_lookup": 35.71428571428571,
  "victim_us_n1024": 30.852770578797976,
  "victim_us_n4096": 46.33920296570899,
  "victim_us_n16384": 99.16699722332407
 },
 "kernels": {
  "gemm_gflop_per_cpu_s": 17.926820018086957,
  "argpartition_ns_per_element": 8.195638656616211
 },
 "old_loader": {
  "cpu_s_per_conversation": 3.541666666666666e-05
 }
}
```
