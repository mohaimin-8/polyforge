# Pre-registration — Wave 4 follow-up: the damped joint controller on the live plane (B1″)

Committed and pushed before any scored run. This file freezes arms, cells,
metrics, hypotheses, falsifiers and the stopping rule for one sitting. The
scorer, `analysis_wave4_dwell.py`, is committed with it and implements
exactly the readings below. Whether the sitting is paid for is the
author's decision; nothing here changes if it is not.

## Why this experiment exists

`RESULTS_WAVE4_CALIBRATED.md` (B1′) scored the corrected joint controller
`jcac-calibrated` as the cheapest arm at iso-fairness in every cell.
`CHURN_WAVE4_CALIBRATED.md`, recovered from the same evidence, shows how it
did it: in the AI-heavy cells it changes its replica count at 21–24 of 30
steps, in `tier_mixed` between 8 and 24 replicas with single-step jumps of
9–11 — a limit cycle. It is inside the cost the record scores, its fairness
and violation are unchanged, and the win stands; but a controller that
creates and destroys a dozen pods a minute is not one an operator would
run, and the objective does not see pod creations.

The damping is a **replica dwell**: a tenant that changed its replica count
may not reverse that change for three control cycles (30 s); same-direction
moves and the other knobs stay free (`6ca8626`; the arm
`jcac-calibrated-dwell`). It is the smallest change that can hold a plan
still without touching the objective, the lattice, the learning or the
penalty. Its plumbing was exercised on a laptop kind cluster with mock
tiers (`wave4_dwell_probe.yaml`, NOT EVIDENCE): the chart set the flag, the
planner booted with it and passed WL-H2.

The question registered: does the dwell damp the cycle **without giving
back the cost win** — or does it cost something, which would be the price
of the cycle, measured.

## Substrate (frozen)

As B1′'s host class and tiers (`g6e.2xlarge` or better on the same floor,
three vLLM tiers, residency and a real decode verified before the matrix),
with the harness changes made after B1′ and disclosed here:

- **Every metric is scored from the load window only.** The harness passes
  the window's opening instant to the export (`--since`, `e94c3e5`); the
  WL-H2 preflight's own requests, which B1 and B1′ carried in
  `total_cost_usd`, latency, violation and cache-hit alike, are excluded.
  **Run-level numbers of this sitting are therefore not comparable to
  B1/B1′'s** without that offset (about $0.13 of `large`-tier spend per
  run); every comparison this protocol makes is within the sitting.
- The load-distribution guard judges pinning per sample from recorded
  snapshots (`ad9e788`); the presence filter of B1′'s Amendment 1 stands.
- The replica sampler keeps a wall-clock cadence (`71d6ea7`).
- The control plane's latency histogram resolves below 5 ms (`c7c0865`);
  the metrics-server manifest is vendored (`25c00db`).

## Arms (frozen)

- **jcac-calibrated-dwell** — the corrected controller with a three-cycle
  replica dwell (planner flags of B1′'s corrected arm plus
  `--replica-dwell-steps=3`).
- **jcac-calibrated** — B1′'s winner, unchanged: the reference.
- **tier-only** — the cheapest single-knob arm live in B1′: the arm the
  damped controller must still beat.

Tuning identical to B1′; the dwell adds one integer, fixed here at 3.

## Cells (frozen)

B1′'s four: `ai_cacheable`, `tier_mixed`, `crud_bursty`, `joint_stress`.
`uniform` mix, `small` cluster size, 30 steps. The AI-heavy cells
(`ai_cacheable`, `tier_mixed`, `joint_stress`) are where the cycle was
measured and where WL-H6 is read; `crud_bursty` is run for the record.

## Design

3 arms × 4 cells × 2 reps = 24 runs, one sitting (about 5 hours on the
box). The runner resumes.

## Hypotheses (frozen)

- **WL-H6 (primary, damping).** In every AI-heavy cell the dwell arm's
  replica-count changes per window (changes between consecutive 10-s
  samples, recovered from `cost_infra_usd` per bucket exactly as
  `CHURN_WAVE4_CALIBRATED.md` does; mean over reps) are **at most half** of
  `jcac-calibrated`'s in the same cell, and its largest single change is no
  larger. **Falsifier:** any AI-heavy cell where either fails.
- **WL-H7 (primary, no regression).** In `joint_stress` the dwell arm's
  cost at iso-fairness (Jain within 0.01) is **not worse** than
  `jcac-calibrated`'s: the paired-bootstrap 95% CI on the per-bucket delta
  (dwell − calibrated; B1′'s pairing, resamples and seed) has its upper
  bound at or below **+5% of the reference arm's mean bucket cost**; AND
  the dwell arm still beats `tier-only` (CI entirely below 0 at
  iso-fairness). **Falsifier:** either fails. A dwell that damps the cycle
  by paying for it is reported as exactly that.
- **WL-H2 (gating).** The knob-liveness gate before every run must pass; a
  failure voids that run.

## Metrics and readings

Cost `total_cost_usd` per run and `cost_usd` per 10-s bucket, both from the
window-scoped export; fairness `mean_jain`; latency replay-clock and
histogram, printed, never compared to each other; churn as above. Margin
0.01; non-inferiority 5%; churn ratio one half — all fixed by this push.

## What voids a run, and the stopping rule

As B1′ (its guards as amended, the per-sample pinning test included). Two
reps per cell executed once; a run that fails before its window opens (an
infrastructure error with no measurement) is re-executed by the runner's
resume, as B1′'s Amendment 3 records; a guard-voided run is not. No arm,
cell, margin, bound, dwell length or reading changes after this push.

## Provenance

Raw DuckDB `eval/results/wave4_dwell_plane.duckdb` (gitignored, Zenodo);
committed export `wave4_dwell_plane_runs.csv`; per-run evidence under
`wave4_dwell_plane_evidence/runs/`. Record: `RESULTS_WAVE4_DWELL.md`,
generated by the scorer committed with this file and registered in
`scripts/reproduce.py` once the run exists.
