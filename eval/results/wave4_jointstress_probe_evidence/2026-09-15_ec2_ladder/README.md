# NOT EVIDENCE — the ladder that took `joint_stress` apart (session 48, 2026-09-15)

Five executions of `eval/experiments/wave4_jointstress_probe.yaml` against
`kaggle_tier_server.py --mock --no-tunnel`, on one fresh `c6i.2xlarge`
(8 vCPU, 15.3 GiB, no GPU, AWS DLAMI Ubuntu 22.04; instance
`i-0941ca375f842129d`, terminated and confirmed). Each run added
`K6_OUT=csv=...` so every request carried its HTTP status, and from rung 3 a
follower on the gateway pod's log. **Nothing here measures PolyForge on a
real substrate** — the tier delays are the mock's fixed sleeps — and no
scored record derives from it. It is kept because it is the raw proof
behind commit `87bc889`, which changed what B1 needs.

| rung | change since previous rung | requests | failed | status codes |
|---|---|---|---|---|
| 0 | none — the frozen cell as sessions 45–47 ran it | 5,545 | 740 (13.3%) | 734 × **429**, 6 × 502 |
| 1 | gateway per-tenant limiter lifted (was hard-coded 600 RPM / burst 60; the cell bursts a tenant to 1,200 AI RPM) | 20,282 | 225 (1.11%) | 98 × 502 (2 ms), 127 × EOF |
| 2 | mock listen backlog 5 → 2048 | 19,768 | 198 (1.00%) | 112 × 502, 86 × EOF |
| 3 | gateway log followed: 502 = `dial 127.0.0.1:11434: connection refused`, `backend=""` — unpinned tenants fell to a phantom external provider; EOFs had no gateway-side error at all | 37,270 | 309 (0.83%) | 89 × 502, 220 × EOF; then the harness crashed after the window (`'Event' object is not callable`: `LoadDistributionSampler._stop` shadowed `Thread._stop()` on Python 3.10) |
| 4 | unpinned → default tier; AI load path on a NodePort instead of `kubectl port-forward`; `_stop` → `_halt` | 37,269 | **1 (0.003%)** | **VALID** — full window, WL-H2 PASS, `eval-export.json` written |

Three things the table says that the earlier reading did not:

1. **`vus_max: 9600` is the pre-allocated pool.** Active VUs peaked at
   96–108 in every rung (`k6-summary.json`, `vus.max`). The cell never
   drove 9,600 concurrent requests at anything.
2. **The 12.3% was a policy, not a capacity.** 734 of rung 0's 740
   failures are the AI gateway's own token bucket answering 429 on
   `/ai/chat`; the CRUD path had zero failures in every rung.
3. **The residue after the limiter was three substrate defects, none of
   them the host**: a fallback to a provider that does not exist in the
   eval install, a port-forward dropping streams, and a Python 3.10-only
   crash in the harness after the load window.

Files: rung 4's evidence (`k6-summary.json`, `knob_preflight.json`,
`host_facts.json`, `load_distribution.json`, `eval-export.json`) and the
per-request CSVs of rung 0 and rung 4 (gzipped). Rungs 1–3 CSVs are held
locally and summarised above. Real Wave 4 evidence, when it exists, lives
in `wave4_live_plane_evidence/`.
