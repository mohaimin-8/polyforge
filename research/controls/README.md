# research/controls: WP22, falsification tests and controls (programme 2)

Status (13 November 2026): built and tested. Only smoke runs have been made,
because a CPU campaign was using the machine. Everything here is an
exploratory instrument check, not a registered result. Item 4 (blind
predictions) is not built yet (see below).

| file | what it is |
|---|---|
| `controls_common.py` | import paths, J per tenant-step, arms registered in-process for one call and removed after it, paired joint/decoupled runs |
| `negative_control.py` | item 1; writes `NEGATIVE_CONTROL[_SMOKE].md` and `data/negative_control[_smoke].csv` |
| `positive_control.py` | item 2; writes `POSITIVE_CONTROL.md` and `data/positive_control_regimes.csv` |
| `adversarial_search.py` | item 3; writes `ADVERSARIAL_<TAG>.md` and `data/adversarial_<tag>_{points,regimes}.csv`, `_args.json` |
| `test_*.py` | 22 tests, about 75 s in total |

Rules kept. Published code behaves as before (R4): there are no new flags on
`JCACController`. New behaviour lives in subclasses inside this directory
(`IndexedWidthController`, `SpikedController`, the probe's recording
controller). Arms are registered in `harness.systems.SYSTEMS` only for the
duration of one call (`controls_common.registered_arms`). No file outside
this directory was changed.

## 1. Negative control

**Claim.** If the planner's objective is exactly additive across one
tenant's knobs, so that phi(r, c, t) = F(r) + G(c) + H(t), then the remainder
R is zero on the whole box. The interaction index is therefore zero, and
per-knob search and joint search choose the same point.

**Failure.** Any of the following is a failure:

- an index above 1e-12 x max(1, |phi|) on any decision;
- a per-knob choice that differs from the joint choice on any decision;
- a planner-level disagreement, counted with `track_coordination` at every
  best-response call from identical inputs;
- any difference in the two arms' outcome metrics.

Two separations are allowed because they lie outside Theorem 1's hypotheses.
Both are constructed in `unit_exceptions`:

- a feasible set that is not a product of per-knob sets, such as a budget on
  a sum of spends;
- an infeasible anchor.

Every other separation is a bug.

**What makes the simulator's objective non-additive.** Line numbers refer to
`research/jcac_sim`.

| term | where | couples |
|---|---|---|
| tier spend = AI misses x tier price | model.py:866 | cache x tier |
| AI miss latency = tier latency x the tenant's replica congestion | model.py:806 | replicas x tier, even with no AI work on the replicas (the case mode A describes as "no shared capacity") whenever CRUD traffic congests them |
| AI latency mixes cache hits with misses (mean, mixture p95, attainment) | model.py:802-832, 855-861 | cache x tier |
| AI work on the replicas, net of cache hits, scaled by tier | model.py:605-613 | replicas x cache x tier |
| dollar admission gate (spend refused beyond budget minus infrastructure) | model.py:766, 898-921 | all three |
| log1p(excess) of the sum of the CRUD and AI overshoots | controller.py:1008 | across traffic families |
| Jain fairness of 1 - violation | controller.py:1429 | every knob that moves violation |
| budget filter, cost <= budget | controller.py:1422 | feasibility (not a product set) |
| cluster caps, memory and replica cost, switching penalty | controller.py:1404-1407, model.py:864-865, controller.py:1424-1428 | none (per knob, or additive) |

A consequence: in this simulator an exactly additive configuration has **at
most one knob with a service-level effect**. Every service-level quantity
multiplies the tier's miss path by the cache's hit share or by the replicas'
congestion. So the other two knobs can enter only through linear
infrastructure cost and the switching penalty. Each control below therefore
names its carrier knob and removes every other coupling by a stated setting.
Each also makes the carrier move, because a control in which nothing moves
cannot catch a search bug.

| configuration | carrier | settings |
|---|---|---|
| `crud_only` | replicas | published plant and planner, CRUD-only workloads (the cells of the committed JOINTNESS check) |
| `crud_active` | replicas | as above on the overload classes, with the half-replica switching penalty, so replicas follow the bursts |
| `shared_capacity` | replicas | published plant, AI work on the replicas (kappa 2, width 8), no cache hits, one latency for every real tier, no tier "none", no budget filter |
| `mode_a_tier` | tier | plant 2 mode A on chat-only population workloads (no congestion), no cache hits, no gate, no budget filter, answer quality priced (weight 2) |
| `mode_a_tier_free` | tier | as above with no switching penalty: all three knobs move |
| `mode_a_cache` | cache | mode A, every real tier at the large tier's latency and price, no tier "none", no gate, no budget filter |
| `mode_a_published`, `mode_a_no_gate` | (sensitivity) | mode A as published, and without its gate: the index must be positive |

**Result (smoke, 30 steps, one cell each).** All six controls pass. The index
is at most 1.4e-13 (relative 2e-14), with no disagreement at any level and
bit-identical runs. The carrier knob moves in 2-73% of decisions. On the
same pipeline, mode A as published shows a mean index of 0.36 (maximum 4.2).
In the committed JOINTNESS export, all 120 CRUD-only pairs are bit-identical
and every AI workload differs. The full run (`--workers 6`) is still to be
made.

**Defect found: the convergence gates of the two arms are asymmetric.**
`cap_binding_probe` uses a cluster replica cap that binds from the first plan
and no switching penalty. The objective stays additive. In this setting the
decoupled and joint best responses disagree from identical inputs. Every
disagreement is classified, and none is unexplained. One is an infeasible
anchor, which is expected. The others share one cause:

1. A tenant fell back earlier in the cycle to `(replica_min, 0 MB, none)`
   (controller.py:1137-1138). That state is outside its anchored lattice:
   128 MB to 0 MB is two cache levels.
2. `_best_for_tenant` keeps an incumbent only if the incumbent is a lattice
   member (controller.py:1104-1121).
3. `_best_for_tenant_decoupled` (controller.py:1280-1284) and
   `_best_for_tenant_knobwise` (controller.py:1330-1334) keep any feasible
   incumbent.

So the decoupled arm keeps a move that the one-level cache clamp forbids,
while the joint arm returns inside the clamps. This contradicts the
docstring's "the same strict-improvement gate". When the gate is made
symmetric (diagnosis only, `symmetric_gate=True`), that class of
disagreement vanishes. With the published 0.05 switching penalty, the probe
shows no disagreement. Committed campaigns are untouched: the fix would be a
new arm, not an edit (R4).

To reproduce: `test_binding_caps_disagreements_are_all_explained`, or
`cap_binding_probe(steps=3)`.

## 2. Positive control

**Claim.** Where an interaction is strong by construction, the index and the
gain are both large. It is tested at three levels:

- **Unit level.** T3 with rho from 1 to 1e6: index = rho, loss = rho - eta,
  bound = rho, and loss / index = 0.99 at eta = rho / 100. The same ridge
  embedded in a planner-sized box keeps index and loss. In a width-16 box it
  covers 4 of 792 points, so the 5%-trimmed quantile index is **0**, while the
  worst-case index is rho. This is a blind spot of the quantile index to keep
  in mind when H2 picks its predictor.
- **Spike-in.** A T3 ridge (rho 1e4, eta 1e3) is added to the real planner's
  score on the `crud_only` control. The logged index is exactly 1e4 at
  120 of 120 decisions. The one-step loss is 9000.05 to 9004.12 (rho - eta
  plus a native spread). The per-knob choice lands on the ridge every time,
  and there are 0 Theorem 1 violations. The ridge is placed one step down
  from the anchor: one step up lets the planner chase the bonus into the
  cache cap within a cycle, after which the ridge is infeasible and, by
  definition, outside the index.
- **Strong regime.** kappa 2 with width 16 is compared with kappa 0 with
  width 1, on the dose-response pilot's own seed for agentic/uniform/large,
  over 30 steps. The strong regime has mean index 2.80 against 0.096,
  one-step loss 0.189 against 0.0005, and gain +0.158 J against +0.002, with
  0 Theorem 1 violations.

**Failure.** Any of the following is a failure: a recovered index other than
rho; a Theorem 1 violation; a strong-regime index below 10x the weak
regime's; or a non-positive strong-regime gain.

## 3. Adversarial search

**Target.** The search looks for the case that would refute H2: a regime with
a small mean index and a large gain. Theorem 1 bounds each decision's loss by
its index, so such a case can only come from what the theorem does not
cover:

- trajectories that separate and compound;
- an infeasible anchor;
- a repaired per-knob combination;
- noise.

**Regime.** A regime is the width, kappa, the plant's form and economy, a
workload family and a cluster size: 24 parameters in all, whose ranges span
the published plant, modes A and B and the pilot's range. Each regime is
evaluated on a panel of P design points, with both arms on the same demand
seed per point and the same seeds for every regime. Population workloads come
from the **tuning split only**.

**Objective (`--objective`).** Let G be the mean of J(decoupled) - J(joint)
over the panel, and I the mean of the joint arm's per-decision worst-case
index.

- `constrained` (the default) ranks by the key (1, G) when I <= tau, and
  (0, -I) otherwise. With tau = 0.25 this means: the largest gain among
  small-index regimes. Over the threshold, a regime ranks by how close it is
  to tau.
- `ratio` ranks by G / (I + eps). The pilot's regimes sit near 0.03.
- `--two-sided` uses |G|.

A refutation candidate must satisfy I <= tau and G >= g* (`--large-gain`,
default 0.03), and it counts only if it survives re-evaluation on **fresh
seeds** (`--confirm`), because the search selects on noise.

**Fixed before the real search (2026-11-13, committed before any run with
`--budget` above the smoke size):** tau = 0.25, g* = 0.03, objective
`constrained`, one-sided, `--budget 200 --panel 4 --steps 60 --max-width 16`,
seed as in the script. A search with any other value is reported as a
separate, labelled exploration, never in place of this one.

**Procedure.** Random search runs first. Stochastic hill-climbing then
starts from the best regimes: each step evaluates a batch of neighbours, in
which one or two parameters move. Everything is seeded.

**Cost.** A regime evaluation is panel x 2 runs of `--steps` steps. A
60-step run takes about 9 s at width 1 and about 75 s at width 16. With
`--budget 200 --panel 4` this comes to roughly 6-7 CPU-hours, about 70
minutes on 6 workers. Only the smoke has been run (3 regimes, 10 steps, 0
Theorem 1 violations). It is not evidence.

## 4. Blind predictions: not built

The plan's step is: for the held-out regimes, write the gains the index
predicts, commit the file, then run. That needs the held-out regime list and
a fitted index-to-gain calibration, both of which come later. Nothing here
touches the population's evaluation split.

## How to run

From this directory:

```bash
python -m pytest -q                                  # about 75 s
python negative_control.py --smoke                   # about 70 s
python negative_control.py --workers 6               # full: about 35 CPU-minutes
python positive_control.py                           # about 40 s
python adversarial_search.py --smoke                 # about 5 s
python adversarial_search.py --budget 200 --workers 6 --tag search
```
