# v2 statistical analysis — results (pre-registered)

Source: `eval/results/raw_sim_v2.duckdb` (2100 valid runs), sim backend, experiment `matrix_v2` (2,100 planned runs). Protocol frozen in `PREREG_V2.md` **before** the first run; analysis executed once (stopping rule §6). v1 results (`RESULTS.md`) are immutable and reported alongside, never replaced. Regenerate: `python run_analysis.py`.

## Per-system summary (mean [95% CI] over all matrix cells)

| system | cost (USD/run) | SLO violation | violation incidence | Jain fairness | cache hit rate |
|---|---|---|---|---|---|
| PolyForge v2 | 2.12 [1.927, 2.313] | 0.07185 [0.05952, 0.08417] | 0.2074 [0.1757, 0.2392] | 0.9672 [0.9603, 0.9742] | 0.1013 [0.08911, 0.1136] |
| PolyForge | 2.373 [2.172, 2.574] | 0.06881 [0.05696, 0.08067] | 0.2044 [0.1737, 0.2352] | 0.9686 [0.9617, 0.9755] | 0.1061 [0.09355, 0.1186] |
| HPA | 3.709 [3.332, 4.086] | 0.07341 [0.06039, 0.08644] | 0.1778 [0.1462, 0.2094] | 0.9626 [0.9566, 0.9686] | 0.05822 [0.05184, 0.0646] |
| KEDA | 4.099 [3.744, 4.453] | 0.068 [0.05396, 0.08203] | 0.1664 [0.1341, 0.1988] | 0.969 [0.9625, 0.9755] | 0.05818 [0.0518, 0.06455] |
| FIRM | 3.897 [3.522, 4.272] | 0.1045 [0.09227, 0.1167] | 0.2207 [0.1916, 0.2498] | 0.929 [0.9218, 0.9363] | 0.05819 [0.05181, 0.06457] |
| Static | 28.76 [25.91, 31.62] | 0.006249 [0.004248, 0.00825] | 0.02818 [0.01802, 0.03833] | 0.9973 [0.9963, 0.9983] | 0.1397 [0.1244, 0.155] |
| GPTCache | 70.4 [58.47, 82.33] | 0.04439 [0.03661, 0.05216] | 0.09728 [0.0793, 0.1153] | 0.9711 [0.9665, 0.9757] | 0.1391 [0.1239, 0.1543] |

## H1 (confirmatory, PREREG_V2 §4) — the SLO segment

`jcac_v2` must show SLO violation significantly below **HPA** and **KEDA** (paired by cell, p < 0.01) with the paired cost point estimate ≤ 0 — a cost tie does not rescue a violation loss and a violation win does not excuse spending more.

| baseline | pairs | violation diff (v2−base) | p | d_z | violation cut mean / up to | cost diff (USD) | verdict |
|---|---|---|---|---|---|---|---|
| HPA | 300 | -0.001566 | 0.6485 | -0.02635 | 2.1% / 100.0% | -1.589 | FAIL (SLO) |
| KEDA | 300 | 0.00385 | 0.2638 | 0.06463 | -5.7% / 81.0% | -1.979 | FAIL (SLO) |

**H1: FAIL** — reported as measured; the stopping rule (PREREG_V2 §6) forbids re-running or re-tuning either way.

## H2 (confirmatory, PREREG_V2 §4) — no self-harm vs v1 `jcac`

Paired per cell within this matrix (both systems reran under matrix_v2 seeds; the immutable v1 database is not reused).

| metric | v2 mean | v1 mean | diff (v2−v1) | p | d_z | direction | large-effect regression |
|---|---|---|---|---|---|---|---|
| cost (USD/run) | 2.12 | 2.373 | -0.2532 | 4.478e-36 | -0.8314 | better | no |
| SLO violation | 0.07185 | 0.06881 | 0.003033 | 0.01006 | 0.1496 | worse | no |
| violation incidence | 0.2074 | 0.2044 | 0.002979 | 0.3147 | 0.05815 | worse | no |
| Jain fairness | 0.9672 | 0.9686 | -0.001378 | 0.03774 | -0.1205 | worse | no |
| cache hit rate | 0.1013 | 0.1061 | -0.004721 | 2.39e-09 | -0.3554 | worse | no |

**H2: PASS** — no large-effect regression on any metric.

## Composite objective (paired by matrix cell)

J = cost_norm + 2·violation + 0.5·(1−Jain) — the objective every baseline was tuned on (TUNING.md). Lower is better.

| baseline | pairs | J_v2 | J_baseline | p | cohens_dz |
|---|---|---|---|---|---|
| HPA | 300 | 0.3828 | 0.5551 | 3.998e-55 | -1.124 |
| KEDA | 300 | 0.3828 | 0.582 | 1.081e-65 | -1.29 |
| FIRM | 300 | 0.3828 | 0.6538 | 2.251e-79 | -1.512 |
| Static | 300 | 0.3828 | 3.035 | 2.081e-53 | -1.098 |
| GPTCache | 300 | 0.3828 | 7.498 | 6.72e-25 | -0.6525 |

## Per-metric: PolyForge v2 vs each v1 baseline (paired by matrix cell)

The v1 gate definition applied to `jcac_v2`, against the original (non-iso-cost) baseline set. The v1 structural impossibility documented in RESULTS.md applies here identically: `static` and `gptcache` buy their metric wins with 12–30× spend. Gate v2 below removes exactly that confound; this section exists so v1 and v2 are always shown side by side.

| baseline | metric | v2_mean | baseline_mean | p | cohens_dz | pooled_p | pooled_d | verdict |
|---|---|---|---|---|---|---|---|---|
| HPA | cost (USD/run) | 2.12 | 3.709 | 5.229e-34 | -0.7991 | 7.672e-13 | -0.6028 | **better, p<0.01, |dz|≥0.5** |
| HPA | SLO violation | 0.07185 | 0.07341 | 0.6485 | -0.02635 | 0.8637 | -0.01403 | better |
| HPA | violation incidence | 0.2074 | 0.1778 | 0.000419 | 0.206 | 0.1935 | 0.1063 | worse |
| HPA | Jain fairness | 0.9672 | 0.9626 | 0.02669 | 0.1286 | 0.32 | 0.08127 | better |
| HPA | cache hit rate | 0.1013 | 0.05822 | 7.235e-32 | 0.7654 | 1.709e-09 | 0.5022 | **better, p<0.01, |dz|≥0.5** |
| KEDA | cost (USD/run) | 2.12 | 4.099 | 2.424e-50 | -1.051 | 3.562e-20 | -0.7874 | **better, p<0.01, |dz|≥0.5** |
| KEDA | SLO violation | 0.07185 | 0.068 | 0.2638 | 0.06463 | 0.6852 | 0.03312 | worse |
| KEDA | violation incidence | 0.2074 | 0.1664 | 8.061e-07 | 0.291 | 0.07567 | 0.1453 | worse |
| KEDA | Jain fairness | 0.9672 | 0.969 | 0.365 | -0.05238 | 0.7195 | -0.02933 | worse |
| KEDA | cache hit rate | 0.1013 | 0.05818 | 6.777e-32 | 0.7658 | 1.649e-09 | 0.5027 | **better, p<0.01, |dz|≥0.5** |
| FIRM | cost (USD/run) | 2.12 | 3.897 | 4.225e-41 | -0.9091 | 1.389e-15 | -0.6764 | **better, p<0.01, |dz|≥0.5** |
| FIRM | SLO violation | 0.07185 | 0.1045 | 2.164e-19 | -0.5577 | 0.0002338 | -0.3023 | **better, p<0.01, |dz|≥0.5** |
| FIRM | violation incidence | 0.2074 | 0.2207 | 0.08004 | -0.1014 | 0.5446 | -0.0495 | better |
| FIRM | Jain fairness | 0.9672 | 0.929 | 7.479e-44 | 0.9513 | 3.246e-13 | 0.6084 | **better, p<0.01, |dz|≥0.5** |
| FIRM | cache hit rate | 0.1013 | 0.05819 | 7.265e-32 | 0.7654 | 1.666e-09 | 0.5025 | **better, p<0.01, |dz|≥0.5** |
| Static | cost (USD/run) | 2.12 | 28.76 | 1.803e-55 | -1.13 | 5.079e-51 | -1.498 | **better, p<0.01, |dz|≥0.5** |
| Static | SLO violation | 0.07185 | 0.006249 | 3.397e-26 | 0.674 | 9.407e-22 | 0.8439 | worse |
| Static | violation incidence | 0.2074 | 0.02818 | 1.916e-30 | 0.7428 | 5.64e-23 | 0.8643 | worse |
| Static | Jain fairness | 0.9672 | 0.9973 | 1.398e-17 | -0.5249 | 1.771e-15 | -0.6848 | worse |
| Static | cache hit rate | 0.1013 | 0.1397 | 4.42e-38 | -0.8625 | 0.0001323 | -0.3142 | worse |
| GPTCache | cost (USD/run) | 2.12 | 70.4 | 6.138e-25 | -0.6532 | 9.099e-25 | -0.9196 | **better, p<0.01, |dz|≥0.5** |
| GPTCache | SLO violation | 0.07185 | 0.04439 | 4.404e-10 | 0.3726 | 0.0002318 | 0.3028 | worse |
| GPTCache | violation incidence | 0.2074 | 0.09728 | 5.673e-19 | 0.5502 | 5.462e-09 | 0.4852 | worse |
| GPTCache | Jain fairness | 0.9672 | 0.9711 | 0.1075 | -0.0932 | 0.3659 | -0.07388 | worse |
| GPTCache | cache hit rate | 0.1013 | 0.1391 | 8.389e-38 | -0.8582 | 0.0001589 | -0.3104 | worse |

- vs **HPA**: significant large-effect wins on 2/5 metrics — FAIL
- vs **KEDA**: significant large-effect wins on 2/5 metrics — FAIL
- vs **FIRM**: significant large-effect wins on 4/5 metrics — PASS
- vs **Static**: significant large-effect wins on 1/5 metrics — FAIL
- vs **GPTCache**: significant large-effect wins on 1/5 metrics — FAIL

**Gate (FAIL)**: ≥ 3 of 5 metrics at p < 0.01 with |d_z| ≥ 0.5 (paired-by-cell) against every baseline in this set — reported as measured either way.

## Gate v2 — all baselines iso-cost (PREREG_V2 §5)

`static_isocost` and `cache_isocost` are budget-matched to `jcac_v2`'s realized per-cell spend (ceiling rounding — the baseline gets at least PolyForge's budget; strict against PolyForge; in 36/60 cells the pinned posture outspends the whole budget even at 1 replica, see isocost_budgets.yaml). HPA/KEDA/FIRM are unchanged and tuned (TUNING.md). Reported alongside the v1 gate, never instead.

| baseline | metric | v2_mean | baseline_mean | p | cohens_dz | pooled_p | pooled_d | verdict |
|---|---|---|---|---|---|---|---|---|
| HPA | cost (USD/run) | 2.12 | 3.709 | 5.229e-34 | -0.7991 | 7.672e-13 | -0.6028 | **better, p<0.01, |dz|≥0.5** |
| HPA | SLO violation | 0.07185 | 0.07341 | 0.6485 | -0.02635 | 0.8637 | -0.01403 | better |
| HPA | violation incidence | 0.2074 | 0.1778 | 0.000419 | 0.206 | 0.1935 | 0.1063 | worse |
| HPA | Jain fairness | 0.9672 | 0.9626 | 0.02669 | 0.1286 | 0.32 | 0.08127 | better |
| HPA | cache hit rate | 0.1013 | 0.05822 | 7.235e-32 | 0.7654 | 1.709e-09 | 0.5022 | **better, p<0.01, |dz|≥0.5** |
| KEDA | cost (USD/run) | 2.12 | 4.099 | 2.424e-50 | -1.051 | 3.562e-20 | -0.7874 | **better, p<0.01, |dz|≥0.5** |
| KEDA | SLO violation | 0.07185 | 0.068 | 0.2638 | 0.06463 | 0.6852 | 0.03312 | worse |
| KEDA | violation incidence | 0.2074 | 0.1664 | 8.061e-07 | 0.291 | 0.07567 | 0.1453 | worse |
| KEDA | Jain fairness | 0.9672 | 0.969 | 0.365 | -0.05238 | 0.7195 | -0.02933 | worse |
| KEDA | cache hit rate | 0.1013 | 0.05818 | 6.777e-32 | 0.7658 | 1.649e-09 | 0.5027 | **better, p<0.01, |dz|≥0.5** |
| FIRM | cost (USD/run) | 2.12 | 3.897 | 4.225e-41 | -0.9091 | 1.389e-15 | -0.6764 | **better, p<0.01, |dz|≥0.5** |
| FIRM | SLO violation | 0.07185 | 0.1045 | 2.164e-19 | -0.5577 | 0.0002338 | -0.3023 | **better, p<0.01, |dz|≥0.5** |
| FIRM | violation incidence | 0.2074 | 0.2207 | 0.08004 | -0.1014 | 0.5446 | -0.0495 | better |
| FIRM | Jain fairness | 0.9672 | 0.929 | 7.479e-44 | 0.9513 | 3.246e-13 | 0.6084 | **better, p<0.01, |dz|≥0.5** |
| FIRM | cache hit rate | 0.1013 | 0.05819 | 7.265e-32 | 0.7654 | 1.666e-09 | 0.5025 | **better, p<0.01, |dz|≥0.5** |
| Static (iso-cost) | cost (USD/run) | 2.12 | 27.61 | 1.216e-52 | -1.086 | 2.492e-48 | -1.44 | **better, p<0.01, |dz|≥0.5** |
| Static (iso-cost) | SLO violation | 0.07185 | 0.188 | 2.998e-33 | -0.7872 | 4.406e-20 | -0.783 | **better, p<0.01, |dz|≥0.5** |
| Static (iso-cost) | violation incidence | 0.2074 | 0.2705 | 1.116e-05 | -0.258 | 0.003507 | -0.2393 | better |
| Static (iso-cost) | Jain fairness | 0.9672 | 0.8341 | 8.972e-42 | 0.9194 | 2.541e-32 | 1.064 | **better, p<0.01, |dz|≥0.5** |
| Static (iso-cost) | cache hit rate | 0.1013 | 0.1397 | 3.509e-38 | -0.8641 | 0.0001303 | -0.3145 | worse |
| Cache-max (iso-cost) | cost (USD/run) | 2.12 | 77.68 | 1.879e-26 | -0.6782 | 2.939e-26 | -0.9546 | **better, p<0.01, |dz|≥0.5** |
| Cache-max (iso-cost) | SLO violation | 0.07185 | 0.04578 | 1.954e-09 | 0.3575 | 0.0005169 | 0.2853 | worse |
| Cache-max (iso-cost) | violation incidence | 0.2074 | 0.1003 | 6.084e-19 | 0.5497 | 1.56e-08 | 0.4698 | worse |
| Cache-max (iso-cost) | Jain fairness | 0.9672 | 0.9704 | 0.1813 | -0.07735 | 0.4611 | -0.06022 | worse |
| Cache-max (iso-cost) | cache hit rate | 0.1013 | 0.1102 | 2.936e-20 | -0.5731 | 0.3282 | -0.0799 | worse |

- vs **HPA**: significant large-effect wins on 2/5 metrics — FAIL
- vs **KEDA**: significant large-effect wins on 2/5 metrics — FAIL
- vs **FIRM**: significant large-effect wins on 4/5 metrics — PASS
- vs **Static (iso-cost)**: significant large-effect wins on 3/5 metrics — PASS
- vs **Cache-max (iso-cost)**: significant large-effect wins on 1/5 metrics — FAIL

**Gate (FAIL)**: ≥ 3 of 5 metrics at p < 0.01 with |d_z| ≥ 0.5 (paired-by-cell) against every baseline in this set — reported as measured either way.

## Verdict summary

- H1 (SLO segment vs HPA/KEDA at ≤ cost): **FAIL**
- H2 (no self-harm vs v1): **PASS**
- Headline system for v2 claims: **jcac (v1 stands)**

## Notes

- Same blocked factorial design, metrics, pairing, and thresholds as v1 (RESULTS.md); the only new system is the pre-registered `jcac_v2`.
- Latency metrics are p95, not p99 (documented deviation, eval/README.md).
- Sim-backend decision quality, not live-cluster absolutes; real-trace replays land in Phase 3 and are reported on their own substrate, never mixed into these tables (ground rule 4).
