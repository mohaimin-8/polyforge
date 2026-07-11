# v3 statistical analysis — overload matrix (pre-registered)

Source: `eval/results/raw_sim_v3.duckdb` (1200 valid runs), sim backend, experiment `matrix_v3_overload` (1,200 planned runs). Protocol, cell definitions, and hypotheses frozen in `PREREG_V3.md` **before** the first run; analysis executed once (stopping rule §6). RESULTS.md (v1) and RESULTS_V2.md are immutable and reported alongside, never replaced. Regenerate: `python run_analysis.py`.

## Per-system summary (mean [95% CI] over all overload-matrix cells)

| system | cost (USD/run) | SLO violation | violation incidence | Jain fairness | cache hit rate |
|---|---|---|---|---|---|
| PolyForge (seasonal) | 2.361 [2.136, 2.587] | 0.1894 [0.1723, 0.2066] | 0.3417 [0.3122, 0.3712] | 0.8574 [0.8417, 0.873] | 0.111 [0.09559, 0.1265] |
| PolyForge (v1 trend) | 2.264 [2.06, 2.468] | 0.1962 [0.1794, 0.2131] | 0.3494 [0.3196, 0.3792] | 0.8543 [0.8389, 0.8697] | 0.1085 [0.09324, 0.1238] |
| HPA | 7.655 [6.497, 8.814] | 0.1389 [0.1284, 0.1493] | 0.2331 [0.2082, 0.2579] | 0.8838 [0.8757, 0.8918] | 0.06039 [0.05239, 0.0684] |
| KEDA | 8.067 [6.923, 9.21] | 0.09551 [0.0843, 0.1067] | 0.1784 [0.1537, 0.2032] | 0.9242 [0.9157, 0.9327] | 0.06037 [0.05237, 0.06837] |
| FIRM | 7.689 [6.529, 8.848] | 0.2155 [0.2087, 0.2223] | 0.3055 [0.2899, 0.321] | 0.8088 [0.8015, 0.816] | 0.06038 [0.05237, 0.06838] |

## H1′ (confirmatory, PREREG_V3 §4) — the SLO segment in the overload regime

`jcac_seasonal` must show SLO violation significantly below **HPA** and **KEDA** (paired by cell, p < 0.01) with the paired cost point estimate ≤ 0 — the identical bar the v2 H1 failed on the non-overload matrix.

| baseline | pairs | violation diff (seas−base) | p | d_z | violation cut mean / up to | cost diff (USD) | verdict |
|---|---|---|---|---|---|---|---|
| HPA | 240 | 0.05058 | 2.831e-08 | 0.3706 | -36.4% / 100.0% | -5.294 | FAIL (SLO) |
| KEDA | 240 | 0.09392 | 3.25e-24 | 0.7336 | -98.3% / 100.0% | -5.705 | FAIL (SLO) |

**H1′: FAIL** — reported as measured; the stopping rule (PREREG_V3 §6) forbids re-running or widening either way.

## H2′ (confirmatory, PREREG_V3 §4) — mechanism attribution vs v1 `jcac`

Both arms share every knob, weight, and guardrail; they differ only in the forecaster. A PASS attributes the H1′ result to forecasting rather than to the joint knobs the baselines lack.

| metric | seasonal mean | trend mean | diff (seas−trend) | p | d_z |
|---|---|---|---|---|---|
| cost (USD/run) | 2.361 | 2.264 | 0.09766 | 0.01183 | 0.1637 |
| SLO violation | 0.1894 | 0.1962 | -0.006776 | 0.0005812 | -0.2251 |
| violation incidence | 0.3417 | 0.3494 | -0.007678 | 0.05189 | -0.1261 |
| Jain fairness | 0.8574 | 0.8543 | 0.00308 | 0.03355 | 0.138 |
| cache hit rate | 0.111 | 0.1085 | 0.002515 | 0.0003823 | 0.2326 |

- violation below trend at p<0.01: **yes** (p=0.000581, d_z=-0.225)
- large-effect cost regression: **no** (diff=+0.098 USD, p=0.0118, d_z=0.164)

**H2′: PASS** — reported as measured.

## Per-metric: seasonal vs each v3 baseline (paired by matrix cell)

| baseline | metric | seasonal_mean | baseline_mean | p | cohens_dz | pooled_p | pooled_d | verdict |
|---|---|---|---|---|---|---|---|---|
| HPA | cost (USD/run) | 2.361 | 7.655 | 1.061e-21 | -0.6828 | 1.577e-16 | -0.8068 | **better, p<0.01, |dz|≥0.5** |
| HPA | SLO violation | 0.1894 | 0.1389 | 2.831e-08 | 0.3706 | 1.038e-06 | 0.453 | worse |
| HPA | violation incidence | 0.3417 | 0.2331 | 2.845e-17 | 0.5898 | 4.751e-08 | 0.5069 | worse |
| HPA | Jain fairness | 0.8574 | 0.8838 | 0.003028 | -0.1934 | 0.003367 | -0.2695 | worse |
| HPA | cache hit rate | 0.111 | 0.06039 | 1.836e-27 | 0.7979 | 2.065e-08 | 0.5236 | **better, p<0.01, |dz|≥0.5** |
| KEDA | cost (USD/run) | 2.361 | 8.067 | 8.049e-25 | -0.7457 | 5.624e-19 | -0.88 | **better, p<0.01, |dz|≥0.5** |
| KEDA | SLO violation | 0.1894 | 0.09551 | 3.25e-24 | 0.7336 | 6.298e-18 | 0.825 | worse |
| KEDA | violation incidence | 0.3417 | 0.1784 | 2.172e-36 | 0.9702 | 7.641e-16 | 0.7627 | worse |
| KEDA | Jain fairness | 0.8574 | 0.9242 | 1.298e-13 | -0.5074 | 9.811e-13 | -0.6748 | worse |
| KEDA | cache hit rate | 0.111 | 0.06037 | 1.783e-27 | 0.7981 | 2.032e-08 | 0.5239 | **better, p<0.01, |dz|≥0.5** |
| FIRM | cost (USD/run) | 2.361 | 7.689 | 5.582e-22 | -0.6885 | 1.14e-16 | -0.8111 | **better, p<0.01, |dz|≥0.5** |
| FIRM | SLO violation | 0.1894 | 0.2155 | 0.000691 | -0.2219 | 0.005673 | -0.2543 | better |
| FIRM | violation incidence | 0.3417 | 0.3055 | 0.002431 | 0.1978 | 0.03288 | 0.1955 | worse |
| FIRM | Jain fairness | 0.8574 | 0.8088 | 9.108e-12 | 0.4631 | 5.728e-08 | 0.5068 | better |
| FIRM | cache hit rate | 0.111 | 0.06038 | 1.757e-27 | 0.7982 | 2.043e-08 | 0.5238 | **better, p<0.01, |dz|≥0.5** |

## E1 (exploratory, declared) — per-class breakdown

Prereg prediction: wins concentrated in `flash_*`/`spike_*`, tie on the `ramp_gentle` control cell — the specificity check that the effect lives exactly where actuation binds.

| class | vs | pairs | violation diff | p | d_z | cost diff (USD) |
|---|---|---|---|---|---|---|
| flash_crud | HPA | 60 | -0.0146 | 0.228 | -0.1573 | 0.294 |
| flash_crud | KEDA | 60 | 0.09158 | 1.223e-11 | 1.083 | -0.4912 |
| flash_crud | PolyForge (v1 trend) | 60 | 0.006028 | 0.04819 | 0.2605 | -0.007989 |
| flash_ai | HPA | 60 | 0.1002 | 2.89e-18 | 1.617 | -18.34 |
| flash_ai | KEDA | 60 | 0.148 | 2.897e-31 | 2.985 | -18.63 |
| flash_ai | PolyForge (v1 trend) | 60 | 0.0004423 | 0.8772 | 0.02004 | 0.1079 |
| spike_agentic | HPA | 60 | -0.03691 | 4.765e-05 | -0.5668 | -2.932 |
| spike_agentic | KEDA | 60 | -0.0175 | 0.04015 | -0.2709 | -3.045 |
| spike_agentic | PolyForge (v1 trend) | 60 | -0.03716 | 4.332e-14 | -1.273 | 0.2916 |
| ramp_gentle | HPA | 60 | 0.1537 | 1.747e-08 | 0.8417 | -0.1965 |
| ramp_gentle | KEDA | 60 | 0.1536 | 1.794e-08 | 0.8408 | -0.6536 |
| ramp_gentle | PolyForge (v1 trend) | 60 | 0.003583 | 0.2325 | 0.1557 | -0.0009311 |

## E2 (exploratory, declared) — KEDA's posture on flash cells

| class | KEDA cost (USD/run) | seasonal cost (USD/run) | ratio |
|---|---|---|---|
| flash_crud | 1.329 | 0.8382 | 1.586 |
| flash_ai | 23.09 | 4.461 | 5.176 |
| spike_agentic | 6.5 | 3.455 | 1.881 |
| ramp_gentle | 1.345 | 0.6912 | 1.946 |

Prereg prediction: tuned KEDA (2 rps/replica) pins near replica_max on high-rps classes — SLO bought by permanent overspend, the v2 iso-cost finding recurring in the overload regime.

## E4 (exploratory, declared) — composite objective J (paired by cell)

| baseline | pairs | J_seasonal | J_baseline | p | cohens_dz |
|---|---|---|---|---|---|
| HPA | 240 | 0.6982 | 1.14 | 1.251e-14 | -0.531 |
| KEDA | 240 | 0.6982 | 1.076 | 1.088e-11 | -0.4612 |
| FIRM | 240 | 0.6982 | 1.334 | 1.4e-30 | -0.8585 |
| PolyForge (v1 trend) | 240 | 0.6982 | 0.7031 | 0.283 | -0.06946 |

## Verdict summary

- H1′ (SLO segment vs HPA/KEDA at ≤ cost, overload regime): **FAIL**
- H2′ (mechanism: forecaster, not knobs): **PASS**
- The v2 iso-attainment framing (parity at −43…−48% cost) remains the SLO segment's claim; this attempt is closed (PREREG_V3 §4).

## Notes

- Systems, tuning, engine semantics identical to v1/v2 headline (no transition costs, no interference); only the demand regime is new.
- `jcac_seasonal` runs its trend fallback until the period detector locks (≈2 cycles), so run-mean numbers *underestimate* steady-state benefit (PREREG_V3 §3, dilution (c)).
- On `spike_agentic`, part of HPA/KEDA's violation is their fixed small tier on agent traffic (disclosed in PREREG_V3 §3); the forecast-only attribution is H2′, where both arms have all knobs.
- Latency metrics are p95, not p99 (documented deviation, eval/README.md).
- Sim-backend decision quality, not live-cluster absolutes (ground rule 4).
