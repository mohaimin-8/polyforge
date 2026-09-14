# NOT EVIDENCE — the same mock probe on a fresh EC2 host (session 48 dry run, 2026-09-14)

Produced by `eval/experiments/wave4_jointstress_probe.yaml` against
`kaggle_tier_server.py --mock --no-tunnel` on the SAME host, exactly as
runbook section 1c prescribes, on a `c6i.2xlarge` (8 vCPU, 15.3 GiB, no GPU,
Deep Learning Base OSS AMI, Ubuntu 22.04) rented for the purpose of
rehearsing the runbook's mechanics before the GPU quota existed. See
`host_facts.json`, which the harness now writes for every run.

**Nothing here is a measurement of PolyForge on a real substrate**; the
tier delays are the mock's fixed sleeps. It is kept for two findings:

1. **The 8-vCPU concurrency refusal is not the laptop's.** `k6-summary.json`
   shows `vus_max` 9,600, `http_req_failed` 12.34%, p95 1,961 ms, 0 dropped
   iterations, k6 aborting on its `http_req_failed` threshold at ~60 s of the
   300 s window — the session-45 shape (12.2–12.4% on an 8-core Windows
   laptop) reproduced on an 8-vCPU Linux cloud host. The runbook's host
   floor of >= 16 vCPU stands on two machines, not one.
2. **WL-H2 passed through the advertised host IP.** `knob_preflight.json`:
   cache knob hit 1.00 @64 MB vs 0.00 @0 MB, tier knob small 1,336 ms vs
   large 1,997 ms, routing moved — with the gateway pod dialling
   `http://172.31.2.13:9109/v1`, the host's primary address. Before session
   48 the single-host route printed `127.0.0.1`, which from a pod is the
   pod; this is the measurement behind the fix in `b1_tier_host.sh` and
   `kaggle_tier_server.py --no-tunnel`.

Instance `i-093e06c4759c15582`, us-east-1a, ~50 minutes, terminated and
confirmed. Real Wave 4 evidence, when it exists, lives in
`wave4_live_plane_evidence/`.
