# Pre-registration: model mismatch (V-series, the MPC with a wrong plant)

Registered 2026-08-13 (session 38). The mechanism (`belief_scale`) is
committed first because the roadmap's WP6 says so explicitly — *"the
MECHANISM is engineering; the CAMPAIGN is what gets frozen"* — but this
prereg is committed and **pushed before any campaign run**, and the push
event is the timestamp anchor.

## Question (and provenance)

`controller.py::_project` calls the same `model.evaluate_step` the engine
scores with. With the realism flags off, the controller's model is
**bit-identical to the plant**: it knows the true tier prices, the true
replica capacity and the true cache curve. `model.py:144-147` states the
consequence in its own words — an override "moves the *world* and every
controller's *beliefs* together". Every sensitivity campaign in the repo
does exactly that.

So the repository contains no experiment in which the controller believes
wrong constants while the world keeps the published ones. For an MPC paper
that is the first structural question a TPDS/TCC reviewer asks, and
"PolyForge was tuned on its own simulator" is the sharpest available attack
on every result in `RESULTS_MASTER.md`.

## Mechanism (committed before this prereg, `belief_scale`)

`JCACController(belief_scale={...})`, applied **only** inside `_project`:

| key | meaning of value `b` | implementation |
|---|---|---|
| `replica_capacity` | a replica delivers `b ×` its true capacity | the projection plans for demand `d / b` — planning for `d` against capacity `b·C` is identical to planning for `d/b` against `C`, so it composes with the existing `capacity_scale` by multiplication |
| `tier_cost` | inference costs `b ×` its true price | projected cost is `cost_infra + b · cost_tier`, using the decomposition `StepMetrics` already carries |
| `cache_half` | the hit-rate curve saturates at `b ×` the true half-point | the projection evaluates at cache size `c / b`, which is exactly `hit_rate` with a `b`-scaled half — **not** a module-global override, because that would move the plant too |

`belief_scale=None` takes the published branch **verbatim** rather than an
equivalent one with neutral multipliers, because `cost_usd` and
`cost_infra + cost_tier` are equal in real arithmetic but need not be in
floating point, and R4 is a byte-identity gate. Verified: `reproduce.py`
23/23 byte-identical with the mechanism in place.

**Disclosed impurity.** Scaling the believed cache size also scales the
*memory* cost the projection believes it pays. That term is
`MEM_COST_USD_GB_HR = 0.005`: $1.8e-6 per step at 128 MB, against a
$0.0139 per-step budget and ~$0.031 of tier spend at `small` — five orders
of magnitude below anything a hypothesis here reads. It is stated rather
than engineered away because removing it would require mutating a module
global, which would contaminate the plant.

## Design (frozen)

One factor: **which constant the controller is wrong about, and by how
much.** Cells, steps, reps, mixes, cluster size, demand construction, seeds,
scoring and every other mechanism are inherited unchanged from
`ablations.yaml`'s design.

**Arms (frozen).** 15 = the 1.0 control + 12 mismatch arms + 2 fair
comparators. One constant is wrong at a time; no arm carries two wrong
beliefs.

| arm | belief |
|---|---|
| `jcac` | none — the 1.0 control |
| `jcac_belief_cap{5,75,125,20}` | `replica_capacity` ∈ {0.5, 0.75, 1.25, 2.0} |
| `jcac_belief_tier{5,75,125,20}` | `tier_cost` ∈ {0.5, 0.75, 1.25, 2.0} |
| `jcac_belief_cache{5,75,125,20}` | `cache_half` ∈ {0.5, 0.75, 1.25, 2.0} |
| `hpa_fair`, `keda_fair` | the WP1 fair-accounting comparators |

The fair comparators are inherited from WP1 deliberately: this campaign is
scored against the accounting the V-series established, not against the
withdrawn published one.

**Cells (frozen).** 5 workloads (`crud_bursty`, `crud_steady`,
`ai_cacheable`, `ai_uncacheable`, `agentic`) × 4 mixes (`uniform`,
`premium_heavy`, `besteffort_heavy`, `whale`) × `medium` × 5 reps = 100
matched cells per arm, 1,500 runs. New output DB; no earlier campaign's
output is reopened (R1/R4).

**Metrics (frozen).** The published set plus `mean_excess`.

## Hypotheses (frozen)

Scored over the 100 matched cells, one-sided paired Wilcoxon as in
WP1/WP3/WP15, paired t reported alongside. **Holm family of 12**
(`stats.holm_bonferroni`, α = 0.05): the six MM-H1 sub-tests and the six
MM-H2 sub-tests are registered together because they answer one question —
does the controller survive being wrong?

**MM-H1 (primary).** At **±25% mismatch on every constant** — the six arms
`{cap,tier,cache}{75,125}` — jcac retains its cost win over `keda_fair`.
Declared PASS only if **all six** sub-tests pass; any single failure fails
MM-H1 and names the constant that broke it.

**MM-H2 (primary).** At the same six arms, `mean_excess` is non-inferior to
`hpa_fair` at the **0.05 margin** inherited from `PREREG_EVICTION_PARITY`.
Same all-six rule.

**MM-H3 (descriptive, no test).** Degradation is graceful: mean `J` plotted
against `|scale − 1|` for each constant, over the full {0.5, 0.75, 1.0,
1.25, 2.0} ladder, reported with the sign and size of every step. A *cliff*
— a step change far larger than its neighbours — is the finding if it
appears, and the ladder exists to make one visible.

## Outcome handling and stopping rule

The campaign runs **once** over the frozen arms and cells. No arm, cell,
metric, margin or test is added or altered after the first result is seen.

- **MM-H1 PASSES** — the surviving cost claim is mismatch-robust at ±25%,
  and this record is the answer to "why believe an MPC tuned on its own
  simulator". It is quoted with its scope: ±25%, one constant at a time.
- **MM-H1 FAILS** — the cost claim depends on the controller knowing a
  constant it would not know in production. That is a **material limitation
  of the contribution**, reported as the headline of this record and carried
  into `RESULTS_MASTER.md`; the constant that broke it is named. No arm is
  retuned in response.
- **MM-H2 PASSES / FAILS** — same handling on the severity side. A FAIL says
  the SLO behaviour is belief-sensitive, which is the more serious of the
  two for an operator, and is reported as such.
- **A cliff in MM-H3** — reported as the operating boundary of the
  controller, with the constant and the scale at which it appears. Per the
  roadmap's risk register this is "a contribution, not an embarrassment":
  it is the *when does this controller apply* section.

`reproduce.py` must re-derive every previously committed record
byte-identically after this work lands (R4). The new record joins the gate.
