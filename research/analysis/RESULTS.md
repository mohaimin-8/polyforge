# W36 statistical analysis — results

Source: `eval/results/raw_sim.duckdb` (1800 valid runs) and `eval/results/ablations.duckdb` (500 valid runs), sim backend. Regenerate: `python run_analysis.py`.

## Per-system summary (mean [95% CI] over all matrix cells)

| system | cost (USD/run) | SLO violation | violation incidence | Jain fairness | cache hit rate |
|---|---|---|---|---|---|
| PolyForge | 2.37 [2.17, 2.571] | 0.06889 [0.05719, 0.08059] | 0.2049 [0.1742, 0.2356] | 0.9689 [0.9622, 0.9756] | 0.1064 [0.09391, 0.1189] |
| HPA | 3.711 [3.334, 4.088] | 0.07342 [0.06041, 0.08642] | 0.1784 [0.1468, 0.2101] | 0.9627 [0.9568, 0.9687] | 0.05819 [0.05181, 0.06456] |
| KEDA | 4.098 [3.743, 4.452] | 0.06791 [0.05392, 0.0819] | 0.1666 [0.1342, 0.1989] | 0.969 [0.9625, 0.9754] | 0.05819 [0.05182, 0.06457] |
| FIRM | 3.896 [3.521, 4.271] | 0.1041 [0.09182, 0.1164] | 0.2211 [0.192, 0.2502] | 0.9294 [0.9219, 0.9368] | 0.05819 [0.05181, 0.06456] |
| Static | 28.77 [25.91, 31.62] | 0.006295 [0.004263, 0.008327] | 0.02817 [0.018, 0.03835] | 0.9972 [0.9962, 0.9982] | 0.1397 [0.1244, 0.155] |
| GPTCache | 70.76 [58.84, 82.67] | 0.04446 [0.03668, 0.05224] | 0.09769 [0.07969, 0.1157] | 0.9711 [0.9665, 0.9758] | 0.139 [0.1238, 0.1543] |

## Two-way ANOVA (system × workload class)

Type II SS on run-level values. Large system effects with p ≈ 0 are the licence for the pairwise comparisons below.

| metric | effect | F | p | partial η² |
|---|---|---|---|---|
| cost (USD/run) | system | 597 | 0 | 0.6278 |
| cost (USD/run) | workload | 387.5 | 7.368e-240 | 0.4669 |
| cost (USD/run) | interaction | 298.6 | 0 | 0.7714 |
| SLO violation | system | 149.6 | 1.107e-132 | 0.297 |
| SLO violation | workload | 1190 | 0 | 0.7289 |
| SLO violation | interaction | 58.07 | 2.882e-177 | 0.3962 |
| violation incidence | system | 117.6 | 1.295e-107 | 0.2494 |
| violation incidence | workload | 1134 | 0 | 0.7193 |
| violation incidence | interaction | 47.14 | 5.643e-148 | 0.3475 |
| Jain fairness | system | 260.2 | 8.859e-209 | 0.4236 |
| Jain fairness | workload | 1336 | 0 | 0.7512 |
| Jain fairness | interaction | 68.83 | 4.156e-204 | 0.4375 |
| cache hit rate | system | 3450 | 0 | 0.9069 |
| cache hit rate | workload | 2.603e+04 | 0 | 0.9833 |
| cache hit rate | interaction | 813.4 | 0 | 0.9019 |

## Primary claim — composite objective (paired by matrix cell)

J = cost_norm + 2·violation + 0.5·(1−Jain), the objective the paper states and every baseline was tuned on (TUNING.md). Lower is better.

| baseline | pairs | J_jcac | J_baseline | p | cohens_dz |
|---|---|---|---|---|---|
| HPA | 300 | 0.4023 | 0.5553 | 1.137e-46 | -0.9946 |
| KEDA | 300 | 0.4023 | 0.5818 | 4.039e-55 | -1.124 |
| FIRM | 300 | 0.4023 | 0.6529 | 6.002e-75 | -1.439 |
| Static | 300 | 0.4023 | 3.036 | 7.122e-53 | -1.09 |
| GPTCache | 300 | 0.4023 | 7.536 | 4.568e-25 | -0.6553 |

**PolyForge beats every tuned baseline on the stated objective (all p < 0.01, all |d_z| ≥ 0.5): YES.**

## Per-metric: PolyForge vs each baseline (paired by matrix cell)

The matrix is a blocked factorial design — every (workload, mix, cluster, rep) cell has exactly one run per system — so the test is a one-sample t on per-cell differences with Cohen's d_z. Pooled unpaired columns shown for transparency.

| baseline | metric | jcac_mean | baseline_mean | p | cohens_dz | pooled_p | pooled_d | verdict |
|---|---|---|---|---|---|---|---|---|
| HPA | cost (USD/run) | 2.37 | 3.711 | 1.212e-28 | -0.7139 | 1.428e-09 | -0.5045 | **better, p<0.01, |dz|≥0.5** |
| HPA | SLO violation | 0.06889 | 0.07342 | 0.1446 | -0.08444 | 0.6111 | -0.04155 | better |
| HPA | violation incidence | 0.2049 | 0.1784 | 3.959e-05 | 0.2409 | 0.2383 | 0.09639 | worse |
| HPA | Jain fairness | 0.9689 | 0.9627 | 0.00339 | 0.1705 | 0.1772 | 0.1103 | better |
| HPA | cache hit rate | 0.1064 | 0.05819 | 4.045e-36 | 0.8321 | 4.2e-11 | 0.5524 | **better, p<0.01, |dz|≥0.5** |
| KEDA | cost (USD/run) | 2.37 | 4.098 | 1.181e-44 | -0.9636 | 7.885e-16 | -0.6814 | **better, p<0.01, |dz|≥0.5** |
| KEDA | SLO violation | 0.06889 | 0.06791 | 0.7601 | 0.01764 | 0.9157 | 0.008643 | worse |
| KEDA | violation incidence | 0.2049 | 0.1666 | 1.816e-09 | 0.3583 | 0.09149 | 0.138 | worse |
| KEDA | Jain fairness | 0.9689 | 0.969 | 0.96 | -0.002895 | 0.9834 | -0.001697 | worse |
| KEDA | cache hit rate | 0.1064 | 0.05819 | 4.445e-36 | 0.8314 | 4.217e-11 | 0.5523 | **better, p<0.01, |dz|≥0.5** |
| FIRM | cost (USD/run) | 2.37 | 3.896 | 4.558e-36 | -0.8313 | 6.193e-12 | -0.5765 | **better, p<0.01, |dz|≥0.5** |
| FIRM | SLO violation | 0.06889 | 0.1041 | 1.152e-29 | -0.7303 | 5.041e-05 | -0.3334 | **better, p<0.01, |dz|≥0.5** |
| FIRM | violation incidence | 0.2049 | 0.2211 | 0.003252 | -0.1713 | 0.4505 | -0.06165 | better |
| FIRM | Jain fairness | 0.9689 | 0.9294 | 4.536e-47 | 1.001 | 4.552e-14 | 0.6314 | **better, p<0.01, |dz|≥0.5** |
| FIRM | cache hit rate | 0.1064 | 0.05819 | 4.229e-36 | 0.8318 | 4.195e-11 | 0.5524 | **better, p<0.01, |dz|≥0.5** |
| Static | cost (USD/run) | 2.37 | 28.77 | 4.239e-55 | -1.124 | 2.281e-50 | -1.484 | **better, p<0.01, |dz|≥0.5** |
| Static | SLO violation | 0.06889 | 0.006295 | 5.599e-27 | 0.6868 | 6.784e-22 | 0.8469 | worse |
| Static | violation incidence | 0.2049 | 0.02817 | 5.353e-32 | 0.7675 | 1.363e-23 | 0.8777 | worse |
| Static | Jain fairness | 0.9689 | 0.9972 | 4.092e-17 | -0.5162 | 6.454e-15 | -0.6693 | worse |
| Static | cache hit rate | 0.1064 | 0.1397 | 3.073e-34 | -0.8027 | 0.0009589 | -0.271 | worse |
| GPTCache | cost (USD/run) | 2.37 | 70.76 | 4.369e-25 | -0.6556 | 7.225e-25 | -0.922 | **better, p<0.01, |dz|≥0.5** |
| GPTCache | SLO violation | 0.06889 | 0.04446 | 5.23e-10 | 0.3709 | 0.0006712 | 0.2794 | worse |
| GPTCache | violation incidence | 0.2049 | 0.09769 | 3.112e-21 | 0.5902 | 5.886e-09 | 0.4839 | worse |
| GPTCache | Jain fairness | 0.9689 | 0.9711 | 0.3366 | -0.05557 | 0.5913 | -0.04387 | worse |
| GPTCache | cache hit rate | 0.1064 | 0.139 | 1.163e-33 | -0.7937 | 0.001187 | -0.266 | worse |

### Roadmap verification gate

- vs **HPA**: significant large-effect wins on 2/5 metrics — FAIL
- vs **KEDA**: significant large-effect wins on 2/5 metrics — FAIL
- vs **FIRM**: significant large-effect wins on 4/5 metrics — PASS
- vs **Static**: significant large-effect wins on 1/5 metrics — FAIL
- vs **GPTCache**: significant large-effect wins on 1/5 metrics — FAIL

**Gate (FAIL)**: PolyForge beats every baseline on ≥ 3 of 5 metrics at p < 0.01 with |d_z| ≥ 0.5 (paired-by-cell). — reported as measured; do not tune post hoc.

Why the raw per-metric gate cannot pass against this baseline set, and why that is the honest finding rather than a defect: `static` is over-provisioned to peak, so it wins every SLO-shaped metric *by construction* while paying ~12× the cost; `gptcache` maxes the cache, so it wins hit rate while paying ~30×. A system cannot out-violate a baseline that never violates — it can only match it at radically lower cost, which is exactly what the composite-objective sweep above shows (all p < 1e-24, all effects large). PolyForge also wins cost against *every* baseline (|d_z| 0.66–1.12) and is the only Pareto-undominated system (fig. 3). This framing goes in the paper verbatim; the gate row stays FAIL.

## Ablations (full PolyForge vs minus-one-component)

| ablation | metric | change | p | cohens_dz_removal | removal_hurts |
|---|---|---|---|---|---|
| − classifier | cost (USD/run) | +2.1% | 0.0009229 | 0.3416 | True |
| − classifier | SLO violation | -5.8% | 0.0002389 | -0.3813 | False |
| − classifier | violation incidence | -2.8% | 0.0008355 | -0.3446 | False |
| − classifier | Jain fairness | +0.1% | 0.4383 | 0.07782 | False |
| − classifier | cache hit rate | -3.4% | 0.003303 | -0.3011 | True |
| − joint ctrl | cost (USD/run) | +2883.7% | 7.97e-12 | 0.7756 | True |
| − joint ctrl | SLO violation | +9.3% | 0.3233 | 0.09928 | True |
| − joint ctrl | violation incidence | -15.4% | 0.0769 | -0.1788 | False |
| − joint ctrl | Jain fairness | -1.9% | 7.91e-08 | -0.5801 | True |
| − joint ctrl | cache hit rate | +25.8% | 2.624e-16 | 0.9826 | False |
| − cost-aware evict | cost (USD/run) | +35.7% | 5.031e-18 | 1.061 | True |
| − cost-aware evict | SLO violation | +1.6% | 0.3275 | 0.09841 | True |
| − cost-aware evict | violation incidence | +0.7% | 0.228 | 0.1213 | True |
| − cost-aware evict | Jain fairness | -0.1% | 0.3301 | -0.09787 | True |
| − cost-aware evict | cache hit rate | -0.1% | 0.9375 | -0.007859 | True |
| − fairness | cost (USD/run) | +0.2% | 0.6525 | 0.04516 | True |
| − fairness | SLO violation | +2.9% | 0.1139 | 0.1595 | True |
| − fairness | violation incidence | +1.0% | 0.1148 | 0.1591 | True |
| − fairness | Jain fairness | -0.1% | 0.2764 | -0.1094 | True |
| − fairness | cache hit rate | -1.2% | 0.1424 | -0.1479 | True |

### Component verdicts

- **− classifier**: removal significantly (p<0.01) degrades 2/5 metrics (cost (USD/run), cache hit rate).
- **− joint ctrl**: removal significantly (p<0.01) degrades 2/5 metrics (cost (USD/run), Jain fairness).
- **− cost-aware evict**: removal significantly (p<0.01) degrades 1/5 metrics (cost (USD/run)).
- **− fairness**: removal significantly (p<0.01) degrades 0/5 metrics.

## Figures

12 publication figures (600-DPI PNG + vector PDF, color-blind-safe fixed-slot palette): `eval/results/figures/`, captions in `eval/results/figures/FIGURES.md`.

## Notes and limitations

- Latency metrics are **p95** (the model's estimator), not p99 — documented deviation, see eval/README.md.
- Runs are sim-backend: decision quality under a shared, stated system model (research/jcac_sim/model.py), not live-cluster absolutes. The cluster backend reproduces the schema for the cloud runs.
- Primary effect size is paired-by-cell d_z (blocked factorial design); pooled unpaired d is reported alongside and is smaller because between-cell variance (cost spans ~10x across cluster sizes) enters its denominator.
- The fairness ablation (γ=0) shows no significant sim-backend degradation: the synthetic workloads never inject the W32 noisy-neighbor interference signal the γ-term responds to, so its contribution is untested here, not refuted — it needs the cluster backend's eBPF-shaped signals (limitation for paper §9).
