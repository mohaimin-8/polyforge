# Pre-registration: coordination-gap measurement (Wave 5, bounds the "solve exactly" claim)

Registered 2026-07-19 (session 24). Committed and pushed **before** the
run; the push event is the timestamp anchor, mirrored to
`OSF_REGISTRATION.md` prospectively per DEFENSE_QA #17.

## Question

`controller.py` solves the per-tenant move-blocked subproblem exactly by
enumeration, but coordinates tenants through **two fixed sweeps of
coordinate descent in sorted-tenant order**, with no convergence check
and no bound on distance from the joint optimum — and the docstring's
"exactly" invites an examiner to read more than is proven. This
experiment measures the gap where the exact joint optimum is computable:
the full product of per-tenant candidate lattices at N = 2 and N = 3.

## Design (frozen)

- Script: `research/analysis/coordination_gap.py` (this commit). 60
  seeded instances per N (seeds 0–59, generator frozen in the script):
  random states, demand mixes spanning the workload archetypes'
  kind profiles (log-uniform rates), SLO classes cycling, deployed
  default weights (α1 β2 γ0.5) and cluster limits, interference zero,
  `fairness_weight = 0.5` for every tenant — the uniform-fairness scoping
  condition that makes the coordinate step's implied global objective
  well-defined: G = α·Σcost/COST_SCALE + β·Σ log1p-excess + γ·(1−Jain) +
  SWITCH_PENALTY·switches.
- Both solvers score G on identical candidate sets, horizons (single
  observation ⇒ flat horizon, excluding forecast noise by construction),
  and feasibility (budget, cluster cache/replica limits, move clamps).
  The exact side enumerates the full product lattice (≤ 60 candidates
  per tenant); the CD side is the deployed `JCACController.plan`.
- Instance handling, fixed in advance: instances with an infeasible
  lattice, or where CD lands on the shed-cost fallback, are discarded
  and counted (the fallback answers a different question than
  coordination quality); all rows land in `coord_gap.csv`.
- Scope, stated: N ≤ 3 is where exactness is computable; the measured
  gap does not bound N = 8 portfolios, but a large gap at N ≤ 3 would
  falsify the "coordination is cheap" reading everywhere. Tenant-order
  dependence at fixed N is not varied here (sorted order is the deployed
  behavior) and is named as the follow-up if CG-H1 fails.

## Hypothesis (frozen)

- **CG-H1:** at every N ∈ {2, 3}, the median relative gap
  (G_cd − G_opt)/|G_opt| over scored instances is ≤ 1% **and** the max
  is ≤ 5%. Exact-match rate is reported descriptively, not gated.

## Outcome handling

Results to `COORD_GAP.md` as measured (a FAIL is published verbatim and
the controller docstring's "exactly" gets qualified in the same commit);
one execution of the frozen seed set; no widening, no reseeding.
