# Simulated chaos campaign — as measured (PREREG_CHAOS_SIM.md)

Database `chaos_sim.duckdb`, 540 valid runs; pairing (workload, rep) = 60 pairs; paired t, alpha 0.01. Windows: planner
outage planning steps [40,46)/[40,58) (scored [41,47)/[41,59)); replica
kill scored steps [60,63).

## Pre-registered hypotheses

- **CH-H1 (primary)** J(jcac_outage_1m) < J(hpa): mean -0.2325, dz -1.69, p 4.2e-19 -> **PASS** — PolyForge with a dead planner for 1 min still beats a healthy HPA.
- **CH-H2 (primary)** J(jcac_kill50) < J(hpa_kill50): mean -0.2406, dz -1.79, p 3.3e-20 -> **PASS**.
- **CH-H3 (secondary, dose)** J(jcac_outage_3m) - J(jcac): mean +0.0043 (95% CI [-0.0034, +0.0121]), dz +0.15, p 0.26. Directional expectation was degradation > 0: consistent.
  - jcac_outage_3m vs healthy hpa, no hypothesis, as measured: mean -0.2345, dz -1.79, p 3.5e-20 (jcac_outage_3m ahead).
- **CH-H4 (secondary, centralization tax, descriptive)** 1-min freeze costs jcac dJ = +0.0063 (95% CI [-0.0040, +0.0166]) vs hpa dJ = +0.0109 (95% CI [+0.0071, +0.0147]).

## Per-arm dose table (J vs own control, paired; descriptive)

| arm | control | mean dJ | dz | p | 95% CI |
|---|---|---|---|---|---|
| jcac_outage_1m | jcac | +0.0063 | +0.16 | 0.22 | [-0.0040, +0.0166] |
| jcac_outage_3m | jcac | +0.0043 | +0.15 | 0.26 | [-0.0034, +0.0121] |
| hpa_outage_1m | hpa | +0.0109 | +0.74 | 3.8e-07 | [+0.0071, +0.0147] |
| jcac_kill50 | jcac | +0.0055 | +0.17 | 0.19 | [-0.0028, +0.0138] |
| hpa_kill50 | hpa | +0.0072 | +1.12 | 4.6e-12 | [+0.0055, +0.0089] |
| keda_kill50 | keda | +0.0065 | +0.73 | 4.9e-07 | [+0.0042, +0.0089] |

## Recovery time (frozen metric: steps after window end until 3-step
rolling mean violation is within 0.02 of own control; descriptive)

| arm | ai_cacheable | crud_bursty | flash_crud | spike_agentic |
|---|---|---|---|---|
| jcac_outage_1m | 2 | 2 | 2 | 5 |
| jcac_outage_3m | 2 | 2 | 2 | 3 |
| hpa_outage_1m | 2 | 2 | 3 | 3 |
| jcac_kill50 | 2 | 2 | 2 | 2 |
| hpa_kill50 | 2 | 2 | 2 | 5 |
| keda_kill50 | 2 | 2 | 2 | 5 |

## Context: mean metrics per system (descriptive)

| system | J | cost | violation | Jain |
|---|---|---|---|---|
| jcac | 0.4033 | 2.385 | 0.0654 | 0.9561 |
| jcac_outage_3m | 0.4077 | 2.416 | 0.0660 | 0.9562 |
| jcac_kill50 | 0.4089 | 2.378 | 0.0679 | 0.9535 |
| jcac_outage_1m | 0.4097 | 2.398 | 0.0674 | 0.9540 |
| keda | 0.5737 | 4.380 | 0.0462 | 0.9577 |
| keda_kill50 | 0.5802 | 4.385 | 0.0487 | 0.9556 |
| hpa | 0.6422 | 3.940 | 0.0929 | 0.9150 |
| hpa_kill50 | 0.6494 | 3.949 | 0.0956 | 0.9132 |
| hpa_outage_1m | 0.6531 | 3.939 | 0.0974 | 0.9107 |
