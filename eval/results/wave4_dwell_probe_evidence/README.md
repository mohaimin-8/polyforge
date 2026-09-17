# NOT EVIDENCE — the dwell arm's plumbing on a laptop kind cluster with mock tiers (session 48, 2026-09-16)

`eval/experiments/wave4_dwell_probe.yaml`: `jcac-calibrated-dwell`,
`ai_cacheable`, one run, on a Windows laptop's kind cluster (Docker Desktop)
with `kaggle_tier_server.py --mock --no-tunnel` as the tier substrate. The
mock's delays are fixed sleeps and the laptop is CPU-starved under k6, so
**nothing here is a measurement of PolyForge on a real substrate** — the run
was voided by the harness's own guards (1.5% failed requests, 2 s p95, a
replica sampler at 37% coverage) exactly as a starved substrate should be.

What is real, and what this directory is for: the operator chart set
`--replica-dwell-steps=3` on the planner (`planner.replicaDwellSteps`), the
planner booted with it beside `--headroom-calibration`, `--headroom-cap=4.0`
and `--switch-penalty=0.006667`, and the WL-H2 knob-liveness gate PASSED
under that controller (`runs/*/knob_preflight.json`). That is the path
`PREREG_WAVE4_DWELL.md` relies on, exercised before any GPU time. The
sampler-coverage failure led to the wall-clock cadence fix (`48efa40`).
