# NOT EVIDENCE — the corrected joint controller on the live plant with mock tiers (session 48, 2026-09-15)

`eval/experiments/wave4_calibrated_probe.yaml`: `joint_stress`, three arms,
one run each, on a real kind cluster (`c6i.2xlarge`, 8 vCPU, no GPU) with
`kaggle_tier_server.py --mock --no-tunnel` as the tier substrate. The mock's
delays are fixed sleeps that never enter a record, so **nothing here is a
measurement of PolyForge on a real substrate**. What is real is every
controller decision — replicas, cache size, tier — taken against the
cluster's actual CRUD latency and the realized p95s the operator now feeds
back. This directory is the raw proof behind commit `f1102d6` and the
reason a follow-up sitting is pre-registered.

| arm | total $ | tier $ | infra $ | requests on `mid` | cache hit | Jain | violation |
|---|---|---|---|---|---|---|---|
| jcac-calibrated (rev. 2) | **0.2886** | 0.2246 | 0.0640 | 1 | 0.950 | 1.000 | 0 |
| jcac (published) | 0.3561 | 0.2550 | 0.1011 | 37 | 0.944 | 1.000 | 0 |
| tier-only | 0.3789 | 0.3106 | 0.0683 | 88 | 0.948 | 1.000 | 0 |

The corrected controller is the cheapest arm at identical fairness: its
infra spend is below the pinned ablation's (it walked replicas to the floor
once the calibrated model agreed with the plant) and it routed one request
to `mid`. The published controller's extra spend is the replicas its
open-loop model demanded; the ablation's is tier routing.

Also in the record, because it was measured: the FIRST revision of the
corrected arm (capacity calibrated from the AI family too; reversal-only
hysteresis) cost **$0.973** on this plant — 584 requests on `mid`, replica
churn (37 distinct pods), cache hit 0.894. The mock's ~1.3 s chat against
the tier table's 0.3 s read as "weak replicas" and halved the believed
capacity. Revision 2 calibrates capacity from CRUD alone, learns an additive
tier-latency offset from the AI family, and charges half a replica-step per
changed knob. Its run replaced the first in `runs/`; the first is described
here rather than kept, since its mechanism is fully explained by the
numbers above.

Per-run directories carry `eval-export.json`, `knob_preflight.json`,
`load_distribution.json`, `host_facts.json` and `metrics_histogram.json`
(the clause-4 histogram, scraped in-run for the first time: replay route
p95 4.76 ms against the replay clock's 1.0 ms, the gap clause 4 named).
