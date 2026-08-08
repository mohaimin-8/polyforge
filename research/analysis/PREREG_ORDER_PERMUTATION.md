# Pre-registration: sweep-order permutation (V-series, adjudicates the audited fairness confound)

Registered 2026-08-08 (session 35). Committed and pushed **before** any
campaign run; the push event is the timestamp anchor, mirrored to
`OSF_REGISTRATION.md` prospectively per DEFENSE_QA #17.

## Question (and provenance of the finding)

The session-35 validity audit found that the controller's coordinate-descent
sweep order is confounded with tenant priority class.

`controller.py` iterates `sorted(self.configs)` for both sweeps. The sweep is
first-come-first-served on the shared cluster caps — `_best_for_tenant`
checks `others_cache + candidate.cache_mb > limits.cache_mb` against whatever
the earlier tenants have already claimed — so an earlier tenant claims
contended capacity first.

Tenant ids are assigned by slot: `workloads.py` builds
`[f"t{i:02d}" for i in range(len(slots))]`, so sorted order **is** slot order.
And the mixes place the high-priority tenants in the low slots:

- `premium_heavy`: 4 premium slots first (`t00`–`t03`), best-effort last.
- `besteffort_heavy`: premium at `t00`, best-effort at `t03`–`t07`.
- `whale` — described in its own comment as *the fairness stressor* — puts
  the whale at `t00` and the minnows last.

So in every non-uniform mix the largest and highest-priority tenants
systematically move first, and this is a property of the naming scheme rather
than of any deliberate policy. The controller advertises a fairness objective,
the operator exports `polyforge_operator_fairness_jain`, and the campaigns
report `mean_jain` — all measured on the single permutation most favorable to
the large tenants. `COORD_GAP.md` states that order-dependence at fixed N was
never varied.

A reviewer reproduces this in one line by renaming `t00` to `t99`. This
experiment measures whether the published fairness reading survives it.

## Design (frozen)

One factor changes: **the order of the coordinate-descent sweep.** The
controller, objective, lattice, clamps, forecaster, seeds, workloads, mixes,
cluster sizes and reps are untouched. `tenant_order_seed` draws a fixed
permutation per run; `None` (the published default) is `sorted()`.

The permutation is a **per-run constant, not per-cycle**. The question is
whether *an* order biases the outcome; reshuffling each cycle would average
the bias away and answer a different question.

**Arms (frozen).** Six: `jcac` (published, `sorted()`) plus `jcac_perm1`
through `jcac_perm5` — the identical controller under five fixed alternative
permutations.

**Cells (frozen).** The mixes where the confound can bind, at the cluster
sizes where the caps are tight enough to contend: workloads
`crud_bursty, ai_cacheable, agentic`; mixes `premium_heavy,
besteffort_heavy, whale`; cluster sizes `small, medium`; 5 reps; 120 steps.
`uniform` is deliberately **excluded**: with identical tenants there is no
priority to confound with, so it cannot answer the question. Its exclusion is
declared here, before the run, rather than justified afterwards.

**Metrics (frozen).** `mean_jain` (primary), plus `total_cost_usd`,
`mean_violation`, `mean_excess` and per-tenant satisfaction spread.

## Hypotheses (frozen)

**OP-H1 (primary).** The published `sorted()` arm's `mean_jain` lies **within
the range spanned by the five permutation arms** on every cell class. If
`sorted()` is systematically the most favorable — i.e. its Jain exceeds the
permutation maximum — the published fairness reading is an artifact of tenant
naming. *Directional prediction: sorted() sits at or near the favorable end.*

**OP-H2 (magnitude).** The spread in `mean_jain` across the six orders is
**≤ 0.01** on the `uniform`-equivalent control and **> 0.01** on `whale`.
This separates "order does not matter" from "order matters where priority is
heterogeneous". *Directional prediction: yes.*

**OP-H3 (cost neutrality).** Cost is **not** materially order-dependent:
the spread in `total_cost_usd` across orders is < 5% of the mean. If cost is
also order-dependent, the headline comparison inherits the confound too.
*Directional prediction: cost is roughly neutral, fairness is not.*

Decision rules are fixed now: OP-H1 and OP-H2 are descriptive range
comparisons over cell classes, reported with the full per-order table so a
reader can check them directly. OP-H3 is a coefficient-of-variation
threshold. All three are reported PASS or FAIL whichever way they land.

## Outcome handling and stopping rule

The campaign runs **once** over the frozen matrix. No arm, cell, metric or
threshold is added or altered after the first result is seen.

**A FAIL of OP-H1 is a publishable outcome and is reported as the headline
finding of this record.** If the published order is systematically favorable,
the honest consequence is that the fairness claims are restated as
order-conditional and the permutation spread is reported alongside every
future Jain number. The published record is not edited; it is superseded in
the open, exactly as `RESULTS_MOVE_CLAMP.md` superseded the unclamped
controller.

`reproduce.py` must re-derive every previously committed record
byte-identically after this work lands (R4) — `tenant_order_seed=None` keeps
`sorted()`, so the published arms are unchanged. The new record joins the
reproduction gate.
