# NOT EVIDENCE — mock-driven diagnostic probe (session 44)

Produced by `eval/experiments/wave4_jointstress_probe.yaml` run against
`kaggle_tier_server.py --mock`, whose delays are fixed sleeps that the server
itself documents as "not measurements and never enter a record".

**Nothing in this directory is a measurement of PolyForge on a real
substrate**, and no scored record derives from it. It is committed only
because it is the raw proof behind one engineering finding:

`joint_stress` fails **concurrency**, not throughput. `k6-summary.json` here
shows 9,600 max VUs, 12.2-12.4% `http_req_failed`, and p95 under 190 ms — a
tier backend that is comfortable while the cluster refuses one request in
eight. k6 aborts on its `http_req_failed` threshold at ~70 s of a 300 s
window, which stops the replica sampler, which trips the coverage guard. The
`sampler covered 23%` error is three layers downstream of the cause.

See `docs/WAVE4_RENTED_HOST_RUNBOOK.md`, section "CORRECTION".

Real Wave 4 evidence, when it exists, lives in `wave4_live_plane_evidence/`.
