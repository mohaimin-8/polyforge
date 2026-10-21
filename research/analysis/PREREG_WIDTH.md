# Pre-registration: does coordination between knobs pay once one interval can move far? (WD; 2026-10-22)

Registered 2026-10-22. Committed and pushed **before** any run of
`eval/experiments/matrix_width_{published,coupled,live}.yaml`; the push event
is the timestamp anchor, checked by `scripts/check_prereg_timing.py`. Scorer:
`analysis_width.py`, committed with this file, tested on synthetic matrices
(`test_width.py`), not edited after the run. Every statistic is the frozen
`analysis_fair_j.py`'s.

## Why this exists

Five registered campaigns found coordination between one tenant's knobs worth
almost nothing: JN-C1 1.7% of J, QL-C1 and HO-C1 nothing. Exploratory probes
(below) suggest why. Every arm moves at most ±2 replicas and one cache level per
interval, as production autoscalers rate-limit their steps. For a move of size
δ, the first-order gain of moving is shared by joint and per-knob search alike;
what only a joint search can capture are the interaction terms, which are
second-order in δ. So under small steps the problem is locally separable, and
per-layer search finds the joint move. The claim has two testable parts:
coordination should pay once a step can be large, and only where the layers
share capacity.

## What was already seen (disclosed)

This protocol is not blind. Every registered result to date, plus three
exploratory probes (scratch scripts, seeds 4_040_404 to 4_040_408, disjoint from
every campaign; 4 AI workloads × 2 mixes × 3 sizes, 80 steps):

1. **Four plants, published lattice.** Joint and decoupled search disagree on
   0.3–1.4% of decisions; ΔJ (decoupled − joint) +0.0025 to +0.0176.
2. **Sixteen plants** (AI work on replicas 0/1/3/6 × tier work factors flat /
   measured / 2-4 / 4-8), published lattice. Disagreement 0.8–1.5%; ΔJ −0.002 to
   +0.007. Coupling alone does not make coordination pay.
3. **Narrow vs wide lattice.** The first run was **contaminated**: pooled worker
   processes kept a patched lattice between jobs, so some "narrow" rows ran wide.
   That was detected (identical rows) and rerun with a per-job reset:

   | plant | ΔJ narrow | ΔJ wide | disagreement narrow / wide |
   |---|---:|---:|---:|
   | published | +0.0050 | +0.0277 | 1.2% / 2.8% |
   | coupled (AI on replicas 3×, tiers 2×/4×) | +0.0060 | +0.0528 | 1.4% / 4.2% |
   | live-fitted | +0.0055 | +0.0013 | 0.3% / 0.2% |

   Absolute J was worse on the wide lattice in all three (joint: 0.6132 vs
   0.6358; 0.9221 vs 1.0028; 0.2625 vs 0.2821).

The coupled plant's level was chosen from probe 2's grid as a stress point. It
is not a measured system and is reported as such. No run of any arm on this
campaign's seeds exists.

## Design (frozen)

Three experiments, identical except plant form and output database:
- simulator, published economy, 120 steps, 5 reps, `shared_seeds: true`, fresh
  experiment names;
- workloads `ai_cacheable`, `ai_uncacheable`, `agentic`, `joint_stress` — the
  AI-carrying classes of the matrix family, because the cache and tier knobs act
  only where AI traffic exists — × 4 mixes × 3 sizes = 48 design points.

| plant (experiment) | form |
|---|---|
| published (`matrix_width_published`) | the published forms |
| coupled (`matrix_width_coupled`) | AI work on replicas 3×; mid/large tiers 2×/4× the small tier's work |
| live (`matrix_width_live`) | PREREG_STRUCTURAL_MISMATCH's live-fitted form: replica capacity 1123 wu, AI never on the replicas |

| arm | search | lattice |
|---|---|---|
| `jcac_joint_tracked` | joint (counts disagreements with the decoupled search; decisions identical to `jcac_converged`, tested) | published: ±2 replicas, one cache level |
| `jcac_decoupled` | decoupled | published |
| `jcac_joint_wide_tracked` | joint, tracked | wide: ±8 replicas, any cache level |
| `jcac_decoupled_wide` | decoupled | wide |

4 arms × 48 design points × 5 reps × 3 plants = 2,880 runs.

## Tests (frozen)

Per plant and design point (reps averaged, the FAIR_J listwise rule):
G_narrow = J(decoupled) − J(joint) on the published lattice, G_wide the same on
the wide lattice, DiD = G_wide − G_narrow. Every directional test is the frozen
FAIR_J rule: one-sided Wilcoxon at its Holm threshold AND the 95% bootstrap CI
(10,000 resamples, seed 20260926) on the registered side of 0.

- **WD-H1 (published) and WD-H2 (coupled): DiD > 0.** One Holm family of 2.
- **WD-H3 (live): DiD equivalent to 0** — 95% CI strictly inside ±0.025.
- **WD-H4 (each plant): G_narrow equivalent to 0** within ±0.025. Coordination is
  negligible under rate-limited steps; replicates JN-C1, QL-C1, HO-C1.
- **WD-H5 (each plant): rate-limited joint control beats wide joint control**,
  J(joint, published) − J(joint, wide) < 0. One Holm family of 3.
- **Descriptive:** per-arm means (J, cost, violation, excess, AI shed, clamp
  breaks — wide arms break the published clamps by design); disagreement shares;
  DiD per workload and cluster size.

## Predictions

From probe 3: WD-H1 PASS; WD-H2 PASS; WD-H3 EQUIVALENT; WD-H4 EQUIVALENT on all
three plants; WD-H5 PASS on all three plants.

## What each outcome means (committed before the run)

- **WD-H1/H2 PASS and WD-H3 EQUIVALENT:** coordination between knobs pays only
  when a step can move far AND the layers share capacity; on the plant fitted to
  the live system it does not.
- **WD-H4 EQUIVALENT everywhere:** under rate-limited steps, per-layer search
  finds the joint move on every plant tested (local separability).
- **WD-H5 PASS:** rate-limited steps are themselves better on J than wide ones,
  so the operating point a designer should choose is the one where coordination
  is worthless.
- **WD-H1 or WD-H2 FAIL:** wide steps do not make coordination pay on that plant,
  and the local-separability explanation is not supported there.
- **WD-H4 not shown on some plant:** coordination is not negligible under
  rate-limited steps there, and the size is quoted with its CI.
- **WD-H5 FAIL on some plant:** wide steps are not worse there, and the design
  recommendation does not hold for it.

## Stopping rule

As `PREREG_FAIR_J`: one sitting per experiment; no re-run, widening or change
of arms, plants or workloads after the first run; two transient-error retries; a
failing run is recorded and its design point dropped for every arm; an
interrupted sitting is resumed with completed runs skipped and the interruption
disclosed; a scorer defect found after the run is fixed in a new scorer and a
new record.
