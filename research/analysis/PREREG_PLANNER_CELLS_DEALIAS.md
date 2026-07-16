# Pre-registration: de-aliased planning-cell assignment (Wave 4 follow-up)

Registered 2026-07-16 (session 21), committed and pushed with its script
`planner_cells_dealias.py` **before** the run. This is the disciplined
follow-up to a confound discovered in `PREREG_PLANNER_CELLS.md`'s frozen run,
not a re-run of it (that campaign's stopping rule stands; its result is
published as measured).

## The confound (diagnosed, not tuned away)

The frozen run partitioned by **round-robin on tenant index** (cell = i mod C)
and placed a whale at **every 8th tenant** (i mod 8 == 0). When the cell count
C is a multiple of 8 — which it is at N ∈ {256, 512, 1024} (C = 8, 16, 32) —
the two periods alias and **all whales collapse into a small set of cells**
(measured: N=256 → all 32 whales in 1 of 8 cells; N=512 → 2 of 16; N=1024 →
4 of 32). The frozen run therefore measured a near-worst-case whale
concentration, so its global-Jain drop (0.99 → 0.90) conflates the
partitioning question with an index-period artifact. The latency result
(PS-H1) is unaffected and stands.

## Question

Does the *fairness* cost of planning-cell partitioning survive once the
assignment no longer aliases with periodic tenant structure? Hash-based
assignment is the standard load-balancing fix for exactly this failure mode.

## Design (frozen)

Identical to `PREREG_PLANNER_CELLS.md` in every respect — same deployed
`PlannerCore.plan`, same heterogeneous demand (seed 42, whale every 8th), same
cell size K = 32, same capacity-parity per-cell limits, same N grid
{8, 32, 64, 128, 256, 512, 1024} — with **one changed factor**: the partition
rule.

- **De-aliased rule (frozen):** order tenants by `md5(tenant_id)` and chunk
  into contiguous groups of K. Hash order is pseudo-random with respect to
  tenant index, so the whale period cannot align with the cell count; cell
  sizes stay even (chunking). No other knob moves.
- Monolithic Jain is recomputed here with one plan call per N (no timed reps —
  latency is not re-measured; it is identical to the frozen run by
  construction, cells are the same size) so the comparison is self-contained.

## Hypotheses (frozen)

- **PF-H1 (primary, the fairness question PS-H2 could not answer):** under
  hash-based cell assignment, global Jain(partitioned) is within
  **ΔJain ≤ 0.05** of the monolithic plan at every N through 1024.
- **PF-H2 (descriptive, the engineering lesson):** report hash-assignment
  ΔJain beside the frozen round-robin ΔJain at each N; the gap between the two
  rules is the measured cost of the aliasing and the value of hashing.

## Outcome handling and stopping rule

Results as measured to `PLANNER_CELLS_DEALIAS.md`, including a PF-H1 failure
verbatim (a residual fairness cost under de-aliased assignment would be the
true, un-confounded price of partitioning and would be reported as such). One
execution; assignment rule, seed, K, and N grid frozen by this push. Both the
frozen round-robin result and this de-aliased result are reported together —
neither replaces the other.
