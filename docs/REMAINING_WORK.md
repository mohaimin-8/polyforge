# Remaining work — the honest ledger of what is left and who owns it

> **Execution route as of session 42: `docs/FINAL_ROADMAP.md`.** It maps the
> nine named weaknesses to the workstream that closes each (§0) and carries
> the execute order (§7). `PUBLICATION_ROADMAP.md` below remains accurate as
> the WP1–WP15 history; it no longer names what is next.
>
> **Session 43 closed D3, R3 and L6, and fixed a red CI nobody was watching.**
> The reproduction gate had been failing on clean checkouts since
> 2026-08-31 while reading 38/38 locally — four causes, all fixed at source
> (`FINAL_ROADMAP.md` §5 R5). L6's live verification measured the W5 gap
> (+21.0% mean, +238.7% p95) and turned up two metric-cardinality memory
> leaks, both fixed (`SECURITY.md` §Session-43). What is left that is not
> writing: B1 (Kaggle `GPU T4 x2`, author-gated), the Zenodo publish, GHCR and
> Pages visibility.

> **Execution route: `docs/PUBLICATION_ROADMAP.md`** (added end of session
> 35) — work packages WP1–WP11 with exact steps, preregs, gates and a
> progress table. This ledger stays the owner-split *view*; the roadmap is
> what a session actually executes. Two evidence gaps found in the session-35
> full-verification pass are WP1 (the −70%/−42% trace headlines carry the
> 1.4581× LRU confound; raw traces are on disk) and WP2 (`RESULTS_MASTER.md`
> scoreboard is stale vs the V-series adjudications).

> **Top-level route: `docs/MAIN_WORKING_PATH.md`** (added 2026-08-05). That
> file orders every item below into milestones M1–M6. This ledger remains the
> owner-split *view*; the main path is the *order*. Session 2026-08-05: goal
> raised to **Transactions-level**; a **formal SLO guarantee** (M3 / T17) added
> as the primary in-repo strengthener; the **leakage-budget controller spun off
> to a separate project** (TDSC) — it is NOT in these buckets. Full gap audit:
> `PolyForge_Research_Gap_Analysis.docx` (Downloads).

Last updated 2026-08-05 (main-path consolidation; prior body last touched
session 30). This is the single place that answers
"what is left and who does it." It reconciles `docs/RELEASE_CHECKLIST.md`
(manual items), the deferred live campaigns, and the thesis fill-ins into
one owner-split view. When it disagrees with a campaign file, the campaign file
wins.

## Session 38 (2026-08-12) — WP15: the budget constraint is infeasible for replica-only control

**The finding that reframes the contribution.** Per-tenant budget cap is
**$0.013889 per step**. Measured on a standard tenant under 40 rps chat:

| tier | replicas | tier spend | infra spend | total | affordable? |
|---|---:|---:|---:|---:|---|
| `none` | 1 | $0.000000 | $0.000140 | $0.000140 | **yes** |
| `none` | 10 | $0.000000 | $0.001340 | $0.001340 | **yes** |
| `small` | 1 | $0.030933 | $0.000140 | $0.031074 | no (2.2×) |
| `mid` | 1 | $0.309333 | $0.000140 | $0.309474 | no (22×) |
| `large` | 1 | $3.093333 | $0.000140 | $3.093474 | no (223×) |

Infra spend spans $0.00014–$0.00134 across the **whole** replica range; tier
spend spans $0–$3.09. **Tier dominates by three orders of magnitude, so the
replica knob cannot move affordability at all.** Therefore **a replica-only
reactive controller cannot satisfy the per-tenant budget under AI load by
any scaling decision available to it** — structurally, not by mis-tuning.

**This is now the primary framing of the joint-control contribution** (the
chosen option, session 38): a *feasibility* argument that follows from the
price table rather than from a benchmark, so it cannot be answered with
"you tuned the baseline badly". Cost percentages become supporting detail.

**It also closes WP1's explanation.** jcac is not "efficient" on BurstGPT —
it is the only arm that *can* meet the constraint, the only way to meet it
is to shed AI (`tier="none"`), and shedding is exactly what produced the
overshoot that failed TP-H3. The cost advantage and the severity failure are
one mechanism: the tier knob's price, not control quality.

**Two mechanisms measured inert/inadequate before scoring, both disclosed:**
`PREREG_VIOLATION_PARITY`'s β ladder moves `mean_excess` 4.8319 → 4.8314
across a 32× weight increase (`f045e68`) — a weight cannot buy what a filter
forbids, so that prereg stays frozen with its premise falsified and its
ladder unrun. `PREREG_BUDGET_PARITY`'s first baseline rule ("hold when
unaffordable") only blocked increases and left `hpa_budget` 2.5% under
`hpa_fair`; corrected to the controller's own best-affordable-candidate
semantics as Amendment 1, before any hypothesis was scored.

Mechanisms landed default-OFF at `365ac40`/`38f12ff`; R4 held 21/21
byte-identical throughout, 143 sim tests green. BP-H2 (is the overshoot
constraint-induced?) is scoreable as frozen and its campaign is running;
early single-window evidence says **partly** — lifting the budget takes
jcac's excess 4.83 → 3.15, still short of `hpa_fair`'s 0.19, so some of the
overshoot is genuine.

### WP8a — live actuation dry-run: run, and it found a pin that never reached the CRs

Executed against a real kube-apiserver (kind, 2 nodes, the committed CRDs),
not a mock. `eval/scripts/live_actuation_dryrun.py` is committed so it can be
re-run before WP8b.

**What passed.** All four frozen arms' Policy/Budget/Tenant CRs admit. The
CRD's CEL bound rules are **live**, proven by negative test: a Policy with
`replicaMin 9 > replicaMax 3` and one with `cacheSizeMBMin 512 >
cacheSizeMBMax 64` are both rejected by the apiserver.

**What failed.** `replica-only` rendered Policy CRs **byte-identical to
`jcac`'s** (sha256 `43daddb3…` for both) — the full three-knob envelope on
an arm `PREREG_WAVE4_LIVE_PLANE` §Arms describes as "cache/tier held fixed".
`_arm_knob_bounds` had branches for `cache-only` and `tier-only` only;
everything else fell through to the all-free jcac case.

**Severity, stated accurately.** This did **not** corrupt any measurement,
and no result changes: the live plane has never been run (WP8b is still
author-gated), and the arm's pin was in fact enforced by a different mechanism
— `replica-only` runs `planner.enabled=false` and takes its knobs from
`push_default_knobs`, so nothing was moving cache or tier regardless of what
its CRs allowed. The defect is that the invariant lived only in a helm value
and a code comment: invisible in the manifests, unenforceable by the
apiserver, and silently lost if that arm ever gained a planner. It is now
declared in the CRs as well, so the arm's own manifests state what the arm
is.

Pinned by two tests in `eval/tests/test_harness.py::TestWave4ArmPins`: every
arm pins exactly the knobs its name claims, and no ablation arm may render
CRs identical to `jcac`'s. `reproduce.py` 23/23 byte-identical after the
change (the cluster backend feeds no committed record).

### WP8a actuation half — RUN, diagnosed, fixed, and now PASSING

The CR half passed earlier this session; the actuation half needed the three
images, which now build. Run end to end on kind
(`eval/experiments/live_dryrun.yaml`, non-scored, its own output DB so the
frozen WP8b matrix is never pre-consumed). It failed three times, and each
failure was worth more than the run.

**Finding 1 — the harness cannot read its own tools on Windows (FIXED).**
`subprocess.run(..., text=True)` decodes with the OS default codec, cp1252
here, while `kind`/`helm`/`kubectl` emit UTF-8. The first run died on byte
`0x8f`. Three call sites in `cluster_backend.py` now decode UTF-8 with
`errors="replace"`. This would have hit **any** live run on this machine,
WP8b's scored sitting included.

**Finding 2 — the eval cluster points at a Redis that does not exist (FIXED;
this was the blocker).** `POST /v1/tenants` hung; `GET /healthz` answered
instantly throughout. The control-plane logs name the cause outright:

```
redis: connection pool: failed to dial after 5 attempts:
  dial tcp: lookup redis on 10.96.0.10:53: server misbehaving
WARN  primary rate limiter unavailable; degraded to local bucket
```

The chain, verified from code: `deploy/helm/polyforge/values.yaml:20`
defaults `redis.url` to `redis://redis:6379/0` (its own comment: *"Empty
disables Redis; limiter and idempotency fall back in-process"*);
`HELM_EVAL_BASE_VALUES` blanks the two postgres URLs for eval deployments
but **never blanked `redis.url`**; the eval cluster deploys no Redis, so the
name does not resolve. The limiter *does* degrade to a local bucket on a
Redis error — but the **dial** is what fails, five attempts behind a DNS
timeout, so degradation is correct and slow. `/healthz` is exempt from the
limiter (`test_rate_limit_bypasses_healthz`), which is exactly why the
health check passed while every real request stalled: the two take different
paths through the middleware stack. Fixed with `"redis.url": ""` in the eval
base values, mirroring how postgres is already handled.

**Correction to the previous entry.** An earlier version of this section
gave a different explanation — that `replicaCount = n_tenants × 2` = 16
control-plane pods could not schedule on a 2-node `small` cluster. **That was
wrong.** It was labelled a hypothesis, but it should not have been recorded
at all: the evidence already contradicted it, since the failure occurred
*after* `helm install --wait`, `kubectl rollout status` and
`kubectl wait --for=condition=Applied policies --all` had all passed. Sixteen
replicas do schedule on this cluster, and the operator does actuate. The
lesson is the ordinary one — read the logs before writing the cause down.

**Result after the fixes: PASS.** 1/1 valid run, and the live numbers sit
next to the committed B2 baseline:

| metric | dry-run (10 steps) | B2 committed (135 steps) |
|---|---:|---:|
| `crud_p95_ms` | 2.0334 | 2.9827 |
| `crud_p99_ms` | **8.0071** | **8.0072** |
| `mean_violation` | 0.0 | 0.0 |
| `mean_jain` | 1.0 | 1.0 |

**Secondary observation, not fixed and out of scope here.** A Redis outage
costs seconds per request before the documented fallback engages, because the
dial timeout is what dominates. On the eval cluster that was a broken
dry-run; in production it is every rate-limited request stalling through a
Redis blip. It deserves its own look at dial timeouts.

**Consequence for WP14.** Unblocked. The plumbing the soak stands on is
proven end to end on this machine.

### WP4 / audit C6 — adjudicated **FALSE**, closed without a code change

**What the audit claimed.** With planning cells enabled (`PLANNER_CELLS.md`,
the PS-H1 1024-tenant scaling result), each re-partition wipes per-tenant
forecast history, degrading the forecaster to persistence — which would gut
the TPDS scalability story. The claim was never independently verified; the
roadmap's WP4 says VERIFY FIRST, then fix *or* adjudicate.

**What the code actually does.** Verified by reading, file:line:

- `services/planner/planner.py:245-256` — the rebuild signature is
  `(alpha, beta, gamma, cache_mb, replicas)`. The comment states the intent
  outright: *"the tenant set is deliberately not part of the rebuild
  signature … a rebuild costs every tenant its forecast history … one tenant
  arriving must never cold-start the fleet's demand forecasts."* Tenant
  churn cannot trigger a rebuild.
- `services/planner/planner.py:266-275` — even when a rebuild *does* happen
  (a weights/limits retune), surviving tenants' `forecasts` and
  `capacity_scale` are carried across to the new controller explicitly.
- `services/planner/planner.py:277-289` — on the no-rebuild path, churn is
  reconciled per tenant: arrivals get a fresh forecaster, departures are
  dropped, **survivors keep their history untouched**.
- `research/analysis/planner_cells.py:81-88, 126-144` — "planning cells" is
  a harness-level wrapper, not a production feature. The partition is
  round-robin **by index** under a frozen rule, and the per-cell
  `PlannerCore`s are constructed **once** (line 129) and reused. A tenant
  cannot migrate between cells, so the re-partition the audit describes
  does not occur anywhere in the repo.

**Demonstration run (committed, not throwaway).** `PlanningCellTests` in
`services/planner/test_planner.py`: four tenants planned five cycles
partitioned and unpartitioned end with **identical per-tenant forecast
history lengths**; a cell's controller identity survives re-planning the
same portfolio (no rebuild); and swapping one tenant out of a cell costs
only that tenant. Three existing `PlannerLifecycleTests` (W31) already
covered the single-core half of the same property.

**Disposition.** Nothing to fix. The alleged defect is the exact failure
mode `planner.py` was hardened against in W31, and the mechanism that would
cause it (re-partitioning) does not exist. WP4 is closed as an adjudication;
per the roadmap's own rule, *do not fix what is not broken*.

## Session 37 (2026-08-11) — WP7 closed; WP1 opened with its anchor intact

**WP7 is done.** `v-series-validity-remediation` is pushed to `origin`
(26 commits: `main` was 14 ahead of `origin/main`, the branch 12 further,
linear — `origin/main` had not moved since 2026-07-31). Every prereg
registered from here forward carries a real push-event anchor.

**Disclosure, permanent (the WP7 follow-up).** *The two session-35 preregs
(`PREREG_EVICTION_PARITY.md`, `PREREG_ORDER_PERMUTATION.md`) were committed
before their campaigns ran but pushed only afterwards, because the branch
had never been pushed. The registration anchor for those two is the commit
hash, not the push event.* The frozen files are not edited to say so — that
would defeat the purpose of freezing them; the disclosure lives here and in
`RESULTS_MASTER.md`'s adjudication notes. `PREREG_TRACE_PARITY.md` (97f5879)
was pushed before its implementation existed, let alone its run.

**Second finding, recorded rather than fixed silently:** the three trace
records — `RESULTS_TRACE.md`, `RESULTS_TRACE2.md`, `RESULTS_TRACE_AZURE.md`
— are **not registered in `scripts/reproduce.py`**. The "19/19 records
byte-identical" gate does not cover them. They *do* reproduce: all three
were regenerated byte-identically from their committed CSVs via
`--analyze` in session 37 (git clean afterwards). Registering them is a
small mechanical task that belongs with WP2.

**Third finding — the M3 formal result is not reproducible by any committed
command, and one of its three rows does not reproduce at all.**
`guarantee.py` is imported by `test_guarantee.py` and nothing else: there is
no `RESULTS_SEPARATION.md`, no analysis script, no `reproduce.py` entry, and
none of its 27 tests pin a published number. The whole M3 cost-separation
result — the paper's only formal contribution — lives as prose in
`MAIN_WORKING_PATH.md` §3 M3. Session 37 reconstructed it by hand:

- `flash` +49.0% (floor $0.012800, cycle $0.006533) — **reproduces exactly**.
- `ramp_gentle` +2.7% ($0.009733 / $0.009467) — **reproduces exactly**, at
  `gentle_orbit(period=20)`; the test file's default period 40 gives
  0.019333 / 0.018800 / +2.8%, so the period was never written down.
- all-distinct **−5.9%** ($0.004533 / $0.004800) — **does NOT reproduce.**
  ~20 constructions were tried. De-aliasing a smooth orbit provably yields
  floor == cycle (gap +0.0%) — verified at periods 10/12/20 perturbing
  either `crud_base_ms` or `rps` at five magnitudes — so a negative gap is
  structurally impossible in the family the prose describes. Monotone ramps
  (distinct **and** sharp) do produce negative gaps (−8.3% / −7.1% / −5.6%),
  none equal to −5.9%.

This is the *mechanism* row — the one whose job is to show the +49.0% gap is
caused by observational aliasing rather than arithmetic. It is not currently
backed by runnable code. Full detail and the instruction not to silently
substitute a reproducing number: `PUBLICATION_ROADMAP.md` WP13 step 0.

**WP13 step 0 CLOSED (`67a32a5`).** `analysis_separation.py` now regenerates
the whole table from `guarantee.py` alone — `RESULTS_SEPARATION.md`,
registered in `reproduce.py`. The unreproducible row was not patched to
match the prose: it is reported as unreproducible in the record itself, with
a replacement mechanism test (the ramp family, aliasing-free by
construction) that shows the same sign inversion the prose claimed, computed
rather than asserted. `flash` (+49.0%) and `ramp_gentle` (+2.7%, at period
20 — previously undocumented) both reproduce exactly. The multi-tenant
extension proper (WP13 steps 1–5) is separate and NOT started.

**WP1 in flight.** The finding is verified, not inherited:
`trace_matrix.run_one` charged `lru_miss_cost_factor()` = 1.4581 to every
`lru_eviction` arm *and never threaded `spec.static_cache_mb`*, so
`hpa_fair`/`keda_fair` could not enter the trace substrate at all — which is
why the −70.4% / −42.5% real-demand headlines were never adjudicated. The
symptom was visible in the published records the whole time: every reactive
baseline reports `cache_hit_rate` **0.1133** on *both* traces, identical to
four decimals, because they are pinned at 128 MB for the whole run, while
PolyForge reports 0.1703 / 0.2182. Wiring fixed and R4-verified at `077be71`
(19/19 byte-identical; the three trace records byte-identical via
`--analyze`); campaign running.

**WP1 CLOSED (`5c75e8a`), verdict split by trace.** `RESULTS_TRACE_PARITY.md`
is in and registered (`reproduce.py`: 21/21 byte-identical). Replication of
the five published arms is bit-for-bit exact on both traces before any new
arm is scored — the substrate is sound. Then: **Azure** shrinks from the
published −42.5%/window to a real, pervasive **−7.1%** against
`hpa_fair`/`keda_fair` (TP-H1a/b PASS, p≤9.5e-05; 69.4% of windows favour
jcac; severity non-inferior, TP-H3a/b PASS). **BurstGPT** shrinks from
published −70.4% to −52.2% but **FAILS its own pre-registered Wilcoxon
test** (TP-H1a p=0.0133 against the Holm threshold 0.01250) — the mean is
pulled by a minority of extreme windows (median diff **+2.232**: jcac is
*more expensive* than the fair comparator in 57.3% of windows), and severity
**reverses**: jcac's unbounded `mean_excess` is 0.5134 against `hpa_fair`'s
0.008818 — **58× worse**, with AI shed on 9.3% of tenant-steps against 0.0%
for every reactive arm (TP-H3a/b FAIL). This directly damages the "half the
SLO overshoot" fallback framing, which now holds only on the synthetic
matrix and Azure, not universally. WP2 (RESULTS_MASTER reconciliation) has
been carried out reflecting all of this — see its scoreboard's Cost/SLO rows
and the "which number to cite" table. The venue-decision rule in
`PUBLICATION_ROADMAP.md` §5 governs what this means for WP9; it is not
re-litigated here.

## Session 36 (2026-08-09) — Docker is available locally; two gates closed

**The long-standing "no Docker on this machine" constraint is GONE.** Docker
Desktop 4.85.0 + WSL2 are installed and verified (engine 29.6.2). The fault
that blocked it was `com.docker.service` shipping as `DEMAND_START` while
running as LocalSystem — it never started, so the engine could not provision
its WSL distro. Set to Automatic + Started; check that service first if
Docker ever fails to come up again.

`kind`, `helm` and `k6` are installed (`GrafanaLabs.k6`, **not** `k6.k6`);
`kubectl` ships with Docker Desktop. `cluster_backend.preflight()` now
reports **no missing tools**, and a kind cluster was created, scheduled a
pod, and torn down cleanly — so the **B1 live actuation dry-run is no longer
blocked on infrastructure**. `~/.wslconfig` raises Docker from 7.6 GB to
9.7 GB with the sizing rationale in its comments (`large`/6-node clusters
remain tight locally; prefer Codespaces for the full matrix).

Two things that were previously unverifiable are now verified:

| Gate | Before | Now |
|---|---|---|
| PostgreSQL RLS / tenant isolation | skipped locally, CI-only — "a skip is not a pass" | **3/3 PASS** against real PostgreSQL 18; `scripts/pg-test-up.sh` makes it a one-liner |
| OWASP ZAP pen test | "**Not yet executed**: no Docker" | **Executed**, findings fixed, re-scan 118 PASS / 0 FAIL |

The ZAP run also exposed that the *default* baseline scan is near-worthless
here — it reached 2 URLs, both 404, because the control plane is a JSON API
with no root route, so 66 rules "passed" against nothing. `--api` mode
(OpenAPI-driven) is the one that matters. Full detail and the fixed findings
are in `docs/SECURITY.md` §Penetration test status.

## Session 35 (2026-08-08) — V-series validity remediation

A four-perspective audit found eleven defects. Status of each:

| # | defect | status |
|---|---|---|
| D1 | 1.4581× LRU charge on 16 baseline arms, none on `jcac` | **MEASURED** — `RESULTS_EVICTION_PARITY.md`: EP-H1a FAILS, 36.8 pp of the headline was accounting |
| D2 | no fair-cache comparator existed | **DONE** — `hpa_fair`/`keda_fair` (512 MB, no charge) |
| D3 | `mean_violation` saturates at 1.0, hiding shed AI | **DONE** — `mean_excess` + `tier_none_step_share` reported for every arm |
| D4 | `RPSWindow` never populated → live planner saw zero demand and froze | **DONE** — rate tracker on both emitters, `-race` clean; AI kinds no longer folded into `crud_read` |
| D5 | `knob_preflight.py` called from no code path | **DONE** — executed by `cluster_backend.execute()` before load, raises on inert substrate |
| D6 | `check_metrics` could not detect "measured nothing" | **DONE** — rejects zero p95 / zero `n_events`; `n_events` now captured |
| D7 | 48 hypotheses, no multiple-comparison correction | **DONE** — `holm_bonferroni()` in `stats.py` (6 tests), applied within each V-series prereg's own family. **Precise statement of the RB-H1 concern:** p=0.0073 *survives* Holm inside a small family (0.05/6 = 0.00833) but *fails* against the full 48-hypothesis set (0.05/48 = 0.00104). Whether a marginal result stands is therefore a claim about which family it belongs to — which is why each prereg now declares its own. Deciding RB-H1's family is author-owned |
| D8 | sweep order confounded with priority class | **MEASURED, IMMATERIAL** — `RESULTS_ORDER_PERMUTATION.md` (540/540): OP-H1 fails strictly, but the largest Jain excursion is **0.0001**, an order of magnitude under the pre-registered 0.01 threshold; OP-H2 FAILS (spread < 0.01 on every mix incl. `whale`); OP-H3 PASSES (cost order-independent to 0.91%). The published order is best on one mix and *worst* on another — sensitivity, not bias. **Published fairness results stand**; sweep order is now a seeded parameter (`tenant_order_seed`) with a measured spread on record |
| D9 | no envtest; fake clients hide conflicts | **CLOSED — EXECUTED AND PASSING.** `envtest_conflict_test.go` (build tag `envtest`), 3/3 pass against a real kube-apiserver 1.31.0: CEL rejects inverted replica and cache bands (asserting on the CEL message, so it cannot pass vacuously), an audit record survives a genuine status 409 with a competing writer racing the plan cycle, and pinned knobs hold through a full apiserver round-trip. Wired into CI. `go test ./...` stays green without the binaries |
| D10 | `reproduce.py` returned 0 on total failure | **DONE** — exits nonzero on drift or failed campaign scripts |
| D11 | audit record dropped exactly when a knob moved | **DONE** — audit emitted before the status write; every degraded cycle audited |

**Two committed live rows in `phase7_live.duckdb` were marked `valid` with
`crud_p95_ms = 0.0`** — they measured nothing. Retro-invalidated in the
committed export and `PHASE7_ORDINAL.md` regenerated; `check_metrics` now
rejects the signature.

### Second wave — the rest of the audit's live-plane findings

The eleven above were the tracked subset; the four reviewers raised more.
These are the ones that would have corrupted B1, all now closed:

| finding | status |
|---|---|
| Planner requested no time window, so the feature API applied its 15-minute default — a **10-second control loop planning on a 15-minute moving average** (~45 intervals of lag), which makes a "predictive" controller strictly worse than a reactive one | **FIXED** — `FeatureDemandSource.Window` defaults to the control interval and is sent as `?since=` |
| `FeatureSet.Truncated` was reported by the server and decoded by nobody, so above 10k events the demand estimate silently became a prefix of the window and **fell as load rose** | **FIXED** — truncation is now an error; the caller holds the last good plan |
| Live tenants were created with **no SLO class**, so the store defaulted every one to `standard` — premium graded 2.5× too leniently, best-effort 3.2× too strictly, voiding any non-uniform-mix live/sim parity reading | **FIXED** — the mix's classes are sent at provisioning, and `eval-export` now **errors** on an unrecognised plan instead of defaulting |
| Any Budget `Get` error (timeout, RBAC, APF throttle — which the chaos campaign *deliberately induces*) silently replaced the tenant's real cap with the $5/hr default, i.e. perturbed the cost arm's independent variable with no log | **FIXED** — only `NotFound` uses the default; other errors skip the tenant for that cycle |
| The CRD accepted `replicaMin > replicaMax`, and the planner's apply path open-coded a clamp **without** the inversion guard `clampReplicas` has — so actuated and recorded values diverged permanently | **FIXED** — CEL rules reject inverted bands at admission; the inline clamp now calls `clampReplicas` |
| k6 exits 0 when every request fails; the summary file was written by every run and **read by none**, and the script ignored response status | **FIXED** — k6 `thresholds` + `check_k6_delivery()` fails the run on >1% failures or any dropped iteration |
| `ReplicaSampler` swallowed failures and a `TimeoutExpired` killed its daemon thread unnoticed, under-pricing a run whose sampler died partway (infra cost **is** total cost on CRUD cells) | **FIXED** — failures counted, exceptions caught, `check_sampler_coverage()` fails below 90% coverage |
| `run_identity` omits `backend`, so a `backend: cluster` spec pointed at the same experiment's sim DB skipped every run and reported success having executed nothing | **FIXED** — resume is filtered by substrate; `validate()` now fails on mixed backends or harness versions (ids unchanged, so provenance holds) |
| `--workers N` on the cluster backend had each worker delete the others' shared kind cluster mid-run | **FIXED** — refused with a clear error |

## Where the project stands

The research is effectively complete and honestly reported. All five contested
segments are resolved (cost WON, SLO closed-honest, fairness WON, forecasting
WON, cache CLOSED) plus the security/isolation segment WON and confirmed over
the wire; the Wave 1–4 leak-fill closed the six examiner-audit gaps; the thesis
compiles (44-page PDF) and the discussion chapter is reconciled with Waves
2–4. **Session 24 added the Wave 5 structural-form program**: the simulator's
functional forms (measured latency model, mixture-percentile p95, tier-scaled
work units) stress-tested by three pre-registered full-matrix reruns — all
PASS with SLO non-inferiority held; a solver audit that measured the
coordination gap at zero (120/120) and *caught the published controller
exceeding its per-interval actuation clamps*, adjudicated by a pre-registered
clamp-fixed rerun (all PASS, slightly stronger — `anchor_moves` is now the
quotable controller); plus live-path engineering (churn-safe/thread-safe
planner, selectable forecasters, multi-resolution `seasonal_mr` for day-scale
periodicity). DEFENSE_QA #24–25 carry the new record. **Session 27 closed four more
fronts in one sitting, every campaign pre-registered and pushed before its
run:** (1) the B2/B3 formal write-up (`RESULTS_LIVE_CHAOS_P99.md` — LC-H1
PASS, first live p99 on record); (2) the **B1 harness prep is
desk-complete and WL-H2-verified** (tier routing + cache byte budgets on
the production gateway, operator knob push under the Applied gate, live-AI
harness mode, preflight gate PASS against the real binary — B1 now needs
only the GPU host); (3) the **second real demand trace replayed**
(`RESULTS_TRACE_AZURE.md` — HT-AZ PASS-with-disclosure, d_z −1.64…−1.85,
n=72); (4) the **2026-stack concurrency baseline** added, tuned into the
strongest reactive arm, and beaten at violation parity
(`RESULTS_CONCURRENCY.md` — CQ-H1/H2 PASS, d_z=−0.96); plus (5) the
**end-to-end 32/64-tenant slice** (`RESULTS_TENANT_SCALE.md` — TS-H1a
PASS, TS-H1b honest FAIL direction-consistent, margin grows with width).
DEFENSE_QA #12/#13/#22 carry the new records. What remains is **not new
desk measurement** — it is one gated confirmatory experiment (B1), plus
submission mechanics and thesis polish. **Session 28 closed the
artifact-evaluation front**: `python scripts/reproduce.py` re-derives every
generated record and figure from the committed data (verified: 5/5 records
byte-identical, 17/17 figures; CI proves the clean-clone tier on every
push — `docs/REPRODUCE.md`), every campaign now has a committed run-level
csv.gz export, and `research/paper/main.tex` is a compiling FGCS scaffold
awaiting the author's manuscript carve. **Session 29 closed the
learned-control front** — the sharpest remaining *mechanism* objection
("why a hand-designed MPC and not a learned policy?"). A strong,
offline-trained RL controller over the *identical* joint action space and
objective was pre-registered (`PREREG_LEARNED_CONTROL.md`, pushed at
896c896 before any run), trained, and beaten: MPC J −0.376, p=2.9e-28,
d_z=−0.708 over 300 matched cells **at zero training cost**, with the
learner's lower violation bought at 2.61× the spend — the same
attainment-for-spend trade the reactive scalers make
(`RESULTS_LEARNED.md`, DEFENSE_QA #26). **Session 30 closed the risk-control
line** with the disciplined follow-up its own published null called for
(`PREREG_RISK_BUDGET.md`, pushed 701d29b before any run; one changed factor,
the null never re-run): **RB-H1 PASS** — the reading the null failed *with
the sign reversed* now lands as designed (−0.00232 violation, p=0.0073),
confirming the published diagnosis was mechanism and not story — while
**RB-H2 and RB-H3 FAIL honestly** (an interior optimum at q=0.90 rather than
a monotone frontier; 3 of 6 domination conjuncts, cost only, reported as
partial). A 24-cell probe bounds the mechanism: the knob buys attainment only
where a capacity lever still has headroom with a real return. Under the
published weights the corrected arm is net worse on J, so the point-forecast
controller **remains** the quotable configuration and the campaign stands as
evidence for that default (`RESULTS_RISK_BUDGET.md`, DEFENSE_QA #27).

The three buckets below are ordered by owner, not by priority. The single
highest-*value* remaining item is in bucket B: the three-knob live plane, which
is the only thing that would add live evidence for the joint controller — the
project's central novelty and its sharpest open weakness.

---

## Bucket A — closeable now, at the desk (desk-doable, no gate)

These need no account, host, or payment. They are the natural next desk tasks.

| Item | What it is | Where |
|---|---|---|
| Title-page macros | placeholder author/roll/supervisor/date macros on the title page | `thesis/report/main.tex` |
| ~~Harness prep for the Wave 4 live plane~~ | **DONE (session 27):** tier routing + cache byte budgets on the gateway, operator knob push under the Applied gate, live-AI harness mode, `tier_mixed`/`joint_stress` cells, and the executable WL-H2 preflight gate — desk-verified end-to-end (WL-H2 PASS against the real gateway binary with mock-latency tier backends). B1 now needs only the GPU host | `PREREG_WAVE4_LIVE_PLANE.md` §Status update |
| Slides ↔ thesis consistency pass | ensure the deck's numbers match the reconciled discussion chapter (−70% not −76%; eight nulls; over-the-wire done) | `thesis/slides/`, `PolyForge_Pre-defence_Presentation.pptx` |
| Thesis ↔ Wave 5 reconciliation | fold DEFENSE_QA #24–25 into the discussion/limitations chapters: structural-form robustness, the clamp disclosure + anchored-controller quotability, the coordination-gap result | `thesis/report/`, sources in `RESULTS_MASTER.md` §13 |
| ~~B2/B3 formal write-up~~ | **DONE (session 27):** `RESULTS_LIVE_CHAOS_P99.md` generated by `live_chaos_p99.py` from the committed CSV — LC-H1 PASS (zero violation through both live faults), P99-H1 recorded (SLO verdict unchanged at p99) | `research/analysis/RESULTS_LIVE_CHAOS_P99.md` |
| **Formal SLO guarantee** (M3 / T17 — REDIRECTED 2026-08-06) | **The specified theorem is vacuous on this plant and that is committed (b55f91b):** memoryless plant + hold-still actuation + cheap replicas make any SLO-clearing configuration trivially control-invariant, so recursive feasibility here is true and empty. Per the author's call the target became the **reactive-vs-predictive cost separation**, and it is **DONE and executable (bec0f62)**: a floor on reactive cost vs a realised predictive cycle gives **+49.0% on the `flash` orbit** (where the onset climb of 5 exceeds the ±2 authority) against **+2.7%** on the `ramp_gentle` control cell and **−5.9%** once the observational aliasing is removed — i.e. 49.0% derived from the plant constants alone, against the campaigns' measured −44…−50% cost at violation parity. **M3 is closed** (`DEFENSE_QA` #28). Campaign replay done per matched cell on the `uniform` mix across hpa/keda/firm (5 parity pairs): medium/`spike_agentic` derived +43.4% vs measured +53.1%, large/`flash_ai` +42.5% vs +79.1%; small/`flash_crud` not computable; both `ramp_gentle` pairs outside the theorem's scope. **An earlier "all three signs agree" claim is RETRACTED** — it came from comparing one derived cell against a twelve-cell measured average, and `flash_crud` flips once the cells are matched. **The multi-tenant coupling is load-bearing, not optional** (no-cap gives the wrong sign; equal-share makes the cell infeasible; independent per-tenant phases mean neither is right). Status: *suggestive structural corroboration at one operating point*, not a validated correspondence — the open item is the multi-tenant extension. Table, soundness rule and detail in `docs/MAIN_WORKING_PATH.md` §3 M3. Original spec, now superseded: the primary Transactions/TPDS strengthener: terminal invariant set + recursive-feasibility condition over the MPC's already-clamped actuation lattice → a bounded-violation guarantee, shipped as an executable checker + a validation script asserting measured violation ≤ bound on every closed campaign. Theorem prose author-owned (R8). Must be a real proof, not a heuristic | `research/jcac_sim/controller.py`, `research/jcac_sim/test_invariants.py`; spec in `docs/MAIN_WORKING_PATH.md` §3 |
| ~~B1 live ablation arms + CRD bounds (M1)~~ | **DESK-COMPLETE (session 34, 2026-08-06):** live `replica-only`/`cache-only`/`tier-only` arms + Policy-CRD `cacheSizeMBMin/Max` and `modelTierMin/Max` (min==max pins a knob), clamped at the actuation point, forwarded to the planner, mirrored in the sim, with the frozen 4×4 matrix in `eval/experiments/wave4_live_plane.yaml`. R4 holds (16/16 records byte-identical). The **live actuation dry-run is deferred into M2** (no Docker here) | `eval/harness/cluster_backend.py`, `internal/operator/**`, `eval/experiments/` |

## Bucket B — author-gated live-cluster experiments (prepared at the desk; the author opens the gate)

All are blocked on a Docker/GPU host this machine does not have. The protocols
are frozen and pushed; the runbooks are push-button. Each is **confirmatory** —
the mechanism is already closed in simulation — so none is load-bearing for the
thesis, but B1 is the highest-value because it is the only live evidence for
the joint controller.

| Item | Status | Gate | Spec |
|---|---|---|---|
| **B1. Three-knob live plane** (joint controller, all knobs live) | **UNBLOCKED at the desk (session 34, 2026-08-06).** Session 33's correction — `OPERATOR_SYSTEMS = {"jcac"}`, no CRD bounds, so the prereg's `cache-only`/`tier-only` ablations ("the sharpest test of the central claim") had no live implementation and WL-H1 was not evaluable — is **resolved**: all four frozen arms are wired, the CRD pins a knob at min==max, and every rendered CR validates against the committed CRDs. The GPU half was already solved and free (`docs/WAVE4_FREE_ROUTE.md`; T4a gate PASSED on a real Kaggle P100, gap 647.7 ms vs the bench's 641 ms). **Still owed before scoring, in this order:** (1) the live actuation dry-run of the four arms (deferred from M1 — needs a cluster), (2) `knob_preflight.py` WL-H2 liveness gate, then (3) the frozen matrix once. An inert knob VOIDS WL-H1 — report, do not fake | GPU-capable host (free Kaggle + Codespace route) | `PREREG_WAVE4_LIVE_PLANE.md` §Status update, `docs/MAIN_WORKING_PATH.md` §3 M1 status |
| **B2. Live chaos campaign** (planner crash + apiserver throttle) | **EXECUTED** (session 23, Codespace, shared-PG data plane): both faults injected live under load, run valid, violations 0 through both — data in `eval/results/live_chaos_p99_runs.csv` (commit 7bdbd4f). Remaining: the formal RESULTS write-up against the prereg's frozen readings | done (write-up = Bucket A) | `PREREG_LIVE_CHAOS_P99.md`, `eval/results/live_chaos_p99_runs.csv` |
| **B3. Live p99 number** | **EXECUTED** (same sitting): first real live p99 — ai 20.043 ms / crud 1.191 ms (ai_cacheable), crud 8.01 ms (crud_bursty) — same CSV; write-up rides with B2's | done (write-up = Bucket A) | `PREREG_LIVE_CHAOS_P99.md` Part B |

Notes: B2/B3 share one Codespace sitting and reuse the proven session-19
harness. B1 additionally needs the Bucket-A harness prep and real model tiers
on the GPU (tier-bench pair; drop the 7B tier if VRAM is short, per the
prereg's amendment rule). When the author opens the gate, the ordered steps are
in `docs/WAVE3_LIVE_RUNBOOK.md` (for B2/B3) and `PREREG_WAVE4_LIVE_PLANE.md`
§Substrate (for B1).

## Bucket C — human-action only (account / camera / payment / browser)

Cannot be automated from this repo. Full detail in `docs/RELEASE_CHECKLIST.md`.

| Item | One-line action |
|---|---|
| OSF registration submit | paste the frozen preregs into OSF, record the DOIs in `OSF_REGISTRATION.md` (the git-push anchor stands until then) |
| Zenodo deposit | `cd eval && python scripts/archive_zenodo.py`, upload, paste `deposit.json`, publish, copy DOI |
| Helm chart → Artifact Hub | `helm package`, host on `gh-pages`, register the repo |
| Container images → GHCR | build + push `polyforge-operator` / `polyforge-planner` (release.yml signs with cosign) |
| Demo video | ~5 min OBS capture: kind up → `helm install` → load → JCAC adapts on Grafana |
| Paper-supplement site | GitHub Pages from `docs/` with the figure gallery + Zenodo/OSF links |
| First cloud smoke (~€10) | `terraform apply` on Hetzner, run `smoke.yaml` with `backend: cluster`, `terraform destroy` |
| Submission | arXiv preprint + venue submission + artifact-track application (W37–W40) |
| Visual PDF proofread | a read-through of the compiled thesis PDF  |
| Rotate pasted credentials | rotate the Kaggle + HF tokens pasted in chat during data work |

---

## Suggested order

1. **Bucket A now** (this session and next): the B1 ablation arms + CRD bounds
   landed 2026-08-06, so the next desk item is the **formal SLO guarantee**
   (M3 / T17) — the only remaining desk-doable item on the main path. The
   title page finishes the thesis's own debts (bib authors were filled and
   verified in session 23).
2. **Bucket B1 next time a GPU host is opened** — the one experiment that
   materially strengthens the thesis by adding live joint-controller evidence.
   B2/B3 can share a cheaper CPU Codespace sitting whenever convenient.
3. **Bucket C on your own schedule** — submission mechanics; none blocks the
   defense, but OSF submit and the Zenodo DOI are worth doing before the paper
   goes out.
