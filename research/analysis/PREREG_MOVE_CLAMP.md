# Pre-registration: clamp-fixed controller rerun (Wave 5, adjudicates the audit-caught actuation-clamp violation)

Registered 2026-07-19 (session 24). Committed and pushed **before** any
campaign run; the push event is the timestamp anchor, mirrored to
`OSF_REGISTRATION.md` prospectively per DEFENSE_QA #17.

## Question (and provenance of the finding)

The coordination-gap audit (`PREREG_COORD_GAP.md` → `COORD_GAP.md`, this
session) discovered that the deployed controller's second
coordinate-descent sweep re-anchors the per-interval move clamps at its
own sweep-1 choice: across the audit's 120 frozen instances, 24
tenant-plans moved the cache two levels and 15 moved replicas beyond ±2
(worst ±4) in a single interval. Every baseline applies its delta once
and is genuinely clamped at ±2 replicas / one cache level; the published
jcac was not, and every committed jcac run contains the behavior. The v3
overload cells were *sized by arithmetic on the shared ±2 clamp*
(PREREG_V3 §3), so this is not a cosmetic spec slip: jcac could climb a
burst at up to twice the actuation rate its comparators were allowed.
This experiment measures whether the published record's conclusions
survive the fix.

## Design (frozen)

- Fix: `controller.py` `anchor_moves=True` — both sweeps draw candidates
  from the interval-start lattice (moves and switch-hysteresis anchored
  at the applied state); coordination across tenants is otherwise
  unchanged. Default `False` preserves the published behavior
  bit-for-bit (committed campaigns replay; property tests
  `AnchorMovesTests` pin both modes' contracts).
- Experiment: `eval/experiments/matrix_clamp.yaml` — exact mirror of the
  v1 headline `full.yaml` (1,800 sim runs) with the treatment system
  `jcac_anchored` (= jcac + `anchor_moves=True`, same W34 tuning, no
  retuning) against the same five baselines.
- Audit closure: `coordination_gap.py --anchored` — the same frozen
  seeds under the fixed controller (`COORD_GAP_ANCHORED.md`). The
  two-move discard class must vanish by construction; the gap statistic
  is re-reported on whatever instances score.
- Analysis: `analysis_econ.py clamp` (treatment-aware pairing,
  mechanized before the run).
- Only a mechanics smoke (unit tests; no campaign cell) preceded this
  registration.

## Hypotheses (frozen)

Pairing/tests as the headline (300 per-cell pairs per baseline,
one-sample t, alpha 0.01).

- **MC-H1 (primary):** the clamp-fixed jcac beats **each** of hpa, keda,
  firm, static, gptcache on composite J (p < 0.01).
- **MC-H2 (secondary):** it remains the cost winner against each
  baseline (paired total_cost_usd, p < 0.01).
- **MC-H3 (violation non-inferiority — where the clamp advantage should
  bite if it was load-bearing):** PASS per baseline b ∈ {hpa, keda} iff
  the one-sided 99% upper confidence bound of the paired mean_violation
  diff (jcac_anchored − b) stays within (v1 paired diff + 0.02), the v1
  reference recomputed live from the committed `raw_sim.duckdb` (at
  registration: hpa −0.0045, keda +0.0010).
- **CG-A (audit closure):** zero two-move states over the frozen audit
  seeds under `--anchored`.
- **Declared mechanism expectations** (directional, not hypotheses):
  slower burst climbs cost jcac some violation in the bursty/overload
  cells; hysteresis now prices moves from the applied state, so plans
  should thrash less; cost effects are second-order.

## Outcome handling and stopping rule

Results to `RESULTS_MOVE_CLAMP.md` as measured. **If any of MC-H1/H2/H3
fails, the failure is published verbatim and the affected headline
claims are re-scoped to the anchored numbers in the same commit** — the
fixed controller becomes the quotable one either way; the published
matrices remain the bit-reproducible record of what was measured before
the fix. One execution of `raw_sim_clamp.duckdb`, valid only at
1,800/1,800; crash-resume allowed; no widening; no retuning pass for the
anchored controller inside this thesis (a tuned-anchored variant is
named future work if MC-H1 fails).
