# The final roadmap — 100% of what is left that is not writing

Written 2026-08-31 (session 42). Supersedes nothing; it **absorbs**
`docs/ZERO_COST_ROADMAP.md` (as workstream L) and `docs/COMPLETION_ROADMAP.md`
Track A/B, and adds the items neither covers.

**Scope rule, from the author, standing:** every workstream below is
engineering, measurement or packaging. **No writing.** WP9/WP10/WP11, the
thesis fill-ins and the paper reframe are listed once, in §9, and are not
started until the author says so.

**Cost rule:** nothing here requires a rented GPU, a paid API, or a cloud
credit. Where a paid option would be faster it is named and declined.

> Execute cold. `PUBLICATION_ROADMAP.md` §0.1 gate battery before and after
> every item. R4 (`scripts/reproduce.py` byte-identical) after any change to
> `research/jcac_sim/`, `eval/harness/`, or an analysis script. One item =
> one prereg where it measures something = one commit series.

---

## §0 What "100%" means here

The project has sixteen work packages done and a **35/35** R4 gate (36 records once T1 landed; see WP13 step 3). What remains
is not "unfinished features" — it is **five named weaknesses a Transactions
reviewer will raise**, plus packaging. Everything below is traceable to one:

| id | weakness | closed by |
|---|---|---|
| **W1** | No cost advantage (BP-H1 FAIL both traces) | **M2+M3 DONE session 42.** Still not closable — the FAILs stand — but the mechanism is now measured: the cost win and the SLO penalty are the **same 45 windows**, and 79.5% of the overshoot is the budget cap, not control error. `RESULTS_WINDOW_CHARACTER.md` |
| **W2** | Two live FAILs (SK-H1 rule artifact, SK-H3 by 0.001–0.21 ms) | **Still not closable — the FAILs stand — but SK-H1's is now MEASURED rather than asserted (`RESULTS_SKH1_DISCRIMINABILITY.md`, session 43).** V8 called it a rule artifact in prose; applying V8's own frozen statistic at every fault-free minute of the same 24 h run gives a **39.2% false-positive rate over 1049 windows**, which predicts **3.1** failures across 8 injections from the metric's oscillation alone — against the **1** observed. The failing injection's +75.4% sits at the **95th percentile of minutes in which nothing was injected**. So the rule fires more readily on quiet windows than on faults and cannot resolve recovery on this metric; the FAIL is evidence about the instrument. Nothing is re-scored: V8 stands exactly as committed. L6 removes the confound for future sittings; V8 pre-committed attempt 10 as the last on this machine |
| **W3** | Theorem is single-tenant; MT extension falsified (S2 FAIL, S3 not evaluable) | **T1 — DONE session 42.** Floor computed (0.126189); S3 **FAIL**. Scope unchanged, reason now measured |
| **W4** | No real trace has ever driven a real cluster | **CLOSED session 42.** Two arms x 2.00 h of BurstGPT window 0 on a real cluster, scored against a prereg. TL-H1 PASS (the sim's cost ordering transferred), TL-H2 VACUOUS, TL-H3 PASS. `RESULTS_TRACE_LIVE.md` |
| **W5** | Live latency is CPU service time, not service latency | **L6 — DONE session 43.** The instrument existed and was never scraped; `soak_observer.sh` captures it and the capture is now **verified against a live server** (180/180 requests). The gap is measured: **+21.0% mean, +238.7% p95** on that host, same 172 requests both sides. Still not closable *for the published sittings* — attempt 10's pods are gone and it cannot be re-read — but the caveat the thesis carries is no longer an assertion |
| **W6** | Stage C is a precondition, not a scored result | **CLOSED session 42.** L5 is a scored 2 h sitting under its own prereg, so the paper no longer needs to lean on an unscored precondition |
| **W7** | Single machine, single cluster | **Premise corrected, session 43: it was never blocked on demand.** The claim below said multi-node behaviour needs a high-demand cell and therefore the GPU-blocked B1 matrix. Two things are wrong with that. The CRUD path needs **no GPU at all** — the 24 h soak drove 298 rps across sixteen pods — so high demand was always reachable. And more basic: `LoadDistributionSampler` reads `kubectl top pods`, which carries a pod name and a CPU figure **and nothing else**, so node placement was never collected and *no sitting at any demand could have produced node-level evidence*. W7 was blocked on a measurement that did not exist, not on a GPU. The sampler now resolves each pod's node and writes `node_of` + `node_shares` into `load_distribution.json` (5 tests, the unresolved-pod grouping mutation-checked). **Campaign run and CLOSED UNRESOLVED (`PREREG_MULTINODE.md`, two sittings, both VOID).** Sitting 1 died in provisioning — my prereg said the AI env gates did not apply, which is true of `LIVE_AI` and false of `POLYFORGE_EVAL_SHARED_PG=1`; with 19 replicas on per-pod SQLite the tenant was created on one pod and its key requested from another. Sitting 2 cleared that, reached the load window, and was rejected by the harness's own guard: the replica sampler covered 7% of the window, because k6 aborted at 63 s with **10.6% of requests failing** — `crud_bursty` at `medium` asks 1200–1443 VUs *per tenant* across eight tenants and eight cores cannot serve it. The stopping rule was two voids, so the campaign is closed and MN-H1/H2/H3 are **not evaluated**; no record is written and nothing is salvaged from a 63-second fragment. **What stands:** the instrumentation gap was real and is fixed, the GPU was never the obstacle (both sittings ran the CRUD path with no GPU and got further than the AI path ever has locally), and the actual blocker is **host capacity** — the same shape as B1's, reached from the opposite direction. **One more structural fact, read off `kind_config` and needing no sitting at all:** the topology is `control-plane` + `nodes - 1` workers, so `small` = 2 nodes = **one worker**. Every application pod lands on the same node by construction there, whatever the demand or the pod count. The earlier note that "L5 already ran multi-NODE on `small`" is true of the cluster and misleading about placement — that sitting could not have shown spread even at a thousand pods. Only `medium` (3 workers) and up can answer this question at all. **v2 RAN AND PASSED (`PREREG_MULTINODE_V2.md`, `RESULTS_MULTINODE.md`).** `crud_steady` at `medium` with a 420 s warm-up: `valid_runs: 1, ok: true`, 40 samples, **zero** attribution failures, **16 pods across all three workers at 0.3165 / 0.3815 / 0.3019**. **MN-H1 PASS** (3 nodes, busiest 0.3815 against the frozen 0.90) and **MN-H2 PASS**. **W7's residue is now "single machine", not "single node"** — which §8 already lists as permanent and which no sitting on this hardware can remove. Note what actually fixed it: not a GPU, not more demand, but a lighter cell and a longer *discarded* warm-up, because v1's failure was a cluster still starting rather than one saturated |
| **W8** | Cache precision 0.313 at τ=0.85 | **C1 — DONE session 42.** Closed *with evidence*: retrieval contributes only +0.047; the ceiling is response stochasticity |
| **W9** | Coverage 60% vs 80% target | **R1 — DONE session 42.** The 60% figure was wrong (CI measures with Postgres); business logic is **79.5%** excluding generated deepcopy and `cmd/` wiring. Gate raised 60 -> 65 |

---

## §1 Workstream T — restore the formal contribution (**start here**)

### T1. Make S3 exactly evaluable — **DONE (session 42)**

**Outcome.** The walk ran: **268,435,456 offset vectors in 119.9 minutes**, and
the eight-tenant floor is **0.126189** (`RESULTS_SEPARATION_MT_V3.md`,
artifact `separation_mt_v3_walk.json`). Verdicts: **S1 PASS, S2 FAIL (carried
forward), S3 FAIL, S5 PASS**. The exact series is 0.000000 (n≤4) → 0.016514 →
0.049058 → 0.090102 → **0.126189**.

**S3 FAIL was predicted in the script's docstring before the walk finished**
and landed as predicted. Two arms sit below the floor, and *not in the same
way*: `hpa` at 0.126042 misses by **0.12%** (essentially on the floor) while
`keda` at 0.001886 misses by **67x**. A floor a reactive replica autoscaler
meets to a fraction of a percent and an event-driven one misses by two orders
of magnitude is pricing a contention `keda` does not pay. Naming that mechanism
is future work; the record reports the falsification and does not repair it.

**What it bought.** v2's limitation sentence — *"the eight-tenant floor is not
computed"* — is now **wrong and must not be repeated**. The theorem's scope is
unchanged (single tenant); the reason moved from a compute budget to a
measurement. That is the difference between "they ran out of steam" and "they
tested their own extension and published the failure."

*Original plan, kept for the record:*

`RESULTS_SEPARATION_MT_V2.md` reports S3 **NOT EVALUABLE**: the eight-tenant
floor needs an ordered walk over **268,435,456 offset vectors** (16^7, tenant 0
already pinned) and "that is beyond what this analysis will spend."

**That is a statement about the loop, not about the problem.**
`guarantee._trajectory_violation` is *deterministic* given an offset vector:

```python
for step in range(periods * length):
    actual = [needs[(off + step) % length] for off in offsets]
    target = [max(needs[(off + step + k) % length] for k in range(lead + 1)) ...]
    held   = incremental_allocation(target, cap, held, replica_max)
```

Every offset vector runs the *same* fixed number of steps through the *same*
branch-free arithmetic. **So all 268M trajectories can be advanced in
lockstep**: `held` becomes an array of shape `(chunk, tenants)`, and each step
is a handful of vectorised operations over the chunk.

**What must NOT be vectorised away:** `incremental_allocation` sweeps tenants
**in index order** and each tenant sees `others = sum(chosen) - chosen[i]`.
That sequential dependency is the coupling S2 proved is real. So the tenant
loop stays sequential (8 iterations) and the **offset-vector axis** is the one
that goes wide. This is the *ordered* walk, exactly — not the multiset
shortcut S2 falsified, and not a sampling approximation.

**Work:**
1. `guarantee.trajectory_violation_batch(needs, offsets_block, ...)` — numpy,
   `int8`/`int16` state, chunked so peak RSS stays bounded (a chunk of 4M
   vectors x 8 tenants x int8 = 32 MB).
2. **The equivalence gate, which is the whole basis for trusting it:** for
   `tenants` in 1..6 the scalar ordered walk is affordable (1 … 1,048,576
   vectors). The batch implementation must return **bit-identical** floors on
   every one. `test_batched_walk_matches_ordered_walk_exactly`.
3. Calibrate cost on n=6 and n=7 (16.7M), **publish the extrapolation before
   running n=8**, then run n=8.
4. `analysis_separation_mt_v3.py` -> `RESULTS_SEPARATION_MT_V3.md`, registered
   in `reproduce.py`, R4 green.

**Pre-committed reading, before any number exists.** S3 asks whether the
measured arms sit above the floor. **A floor the arms violate is a FAILED
derivation, not a failed controller** — report it as such and do not tune the
model to fit. If the extrapolation says n=8 costs more than ~12 h on this
machine, report the measured cost curve and stop; that is still a strictly
better record than "not evaluable."

**Why this is first:** W3 is the one weakness I would call disqualifying for
TPDS and seriously damaging at TCC/TSC — the paper's only theorem does not
cover the paper's own setting, and the cap first binds at **5 tenants** while
the published cell has **8**. It needs no GPU, no money, and no author action.

### T2. ~~Re-open the M3 mechanism row under the corrected model~~ — **PREMISE WITHDRAWN (session 42)**

**Second premise error of this roadmap; kept on record like C1's.** The plan
below assumed the corrected allocation model could reach the retracted -5.9%
row. It cannot: that row is a **single-tenant** orbit-construction claim, while
`incremental_allocation` is the **multi-tenant contention rule**. Different
layer, no contact.

`RESULTS_SEPARATION.md` also already records ~20 attempted reconstructions
across four families (epsilon-de-aliasing on `crud_base_ms` and on `rps` at
five magnitudes x three periods, deduplication, de-aliased `flash`, monotone
ramps), and replaced the row with a runnable de-aliasing test. There is no
engineering left here — the retraction is complete and documented. **Do not
re-open it.**

*Original, withdrawn:*

### T2-withdrawn. Re-open the M3 mechanism row under the corrected model

`RESULTS_SEPARATION.md` replaced the non-reproducible −5.9% all-distinct row
with a de-aliasing test. The corrected allocation model (`incremental_allocation`,
WP13 step 2) did not exist when that was written. Re-derive the row under the
corrected model. If it reproduces, the retraction is narrowed to "wrong under
the step-1 model"; if not, it stays retracted with a second independent reason.

### T3 — **DONE (session 42), and stronger than planned.**

`RESULTS_DOMINANCE.md`. The original plan was a Pareto frontier over
(alpha, beta, gamma). That would still have been a statement about a *weighted*
objective, which is the thing being objected to. **Weight-free dominance
answers it outright:** an arm no other arm beats on every raw objective cannot
be beaten by ANY weighting, including ones nobody has proposed.

| arm | non-dominated cells (of 60) | share |
|---|---:|---:|
| **`jcac`** | **48** | **80.0%** |
| `gptcache` | 32 | 53.3% |
| `static` / `keda` | 30 | 50.0% |
| `hpa` | 19 | 31.7% |
| `firm` | 3 | 5.0% |

**In 48 of 60 cells the choice of weights is not load-bearing at all** — no
weighting of cost, violation and fairness can put a baseline ahead of jcac.
That is a materially stronger answer to "your weights are self-chosen" than
`SENSITIVITY_J.md`'s 25-cell sweep, and it has **no free parameters** to tune.

**The 12 exceptions are reported in full and are structured:**

| workload | AI rps | shape | jcac dominated |
|---|---:|---|---:|
| `agentic` / `ai_cacheable` / `ai_uncacheable` | 2.7 / 7.0 / 3.2 | bursty / wave / sawtooth | 0/12 each |
| `crud_bursty` | 0.0 | bursty | **0/12** |
| `crud_steady` | 0.0 | steady | **12/12** |

Always by the same pair (`gptcache`, `hpa`). The obvious reading — "no AI, so
the knobs are inert" — is **incomplete, and `crud_bursty` is the control that
says so**: equally CRUD-only, and jcac is non-dominated in all 12. The loss is
specific to demand that is *both* AI-free *and* variation-free — the one regime
where neither the AI knobs nor the forecaster has anything to do, so the joint
controller is pure overhead. Where either has something to act on, jcac is
non-dominated.

Registered in `reproduce.py`; R4 **37/37**.

*Original plan:*

### T3-original. Weight-regime Pareto analysis

`SENSITIVITY_J.md` has 425 perturbed cells and direction never flips. Add the
Pareto frontier over (α, β, γ) so the answer to "is your operating point
special?" is a figure, not a paragraph. Desk-only, no prereg (descriptive).

---

## §2 Workstream L — live evidence (absorbs ZERO_COST_ROADMAP)

| id | item | state |
|---|---|---|
| **L1** | Concurrent `tunnel_preflight` stage | **DONE session 42** — 16 tests green |
| **L2** | Micro-batching tier server + batched-equals-serial exactness test | **DONE session 42** — per-tier lock replaced by a batching queue; mock-measured **22.57s -> 1.94s for 12 concurrent (11.6x)**; 11 tests pass, the real-model exactness check is gated on `POLYFORGE_TIER_TEST_MODEL` for the Kaggle smoke |
| **L3** | **ATTEMPTED session 42 — BLOCKED, and not on throughput.** `kaggle_tier_bench_batched.py` was written (batch 1/8/32/64 x output 48/96, plus a batched-vs-serial exactness check on the real models) and pushed and run on Kaggle. **All 16 cells failed with `cudaErrorNoKernelImageForDevice`.** A probe kernel identified the cause: Kaggle's image now ships **torch 2.10 (arch list sm_70+)** and the free pool grants a **Tesla P100, sm_60** — PyTorch dropped Pascal, so no torch op runs at all. The same P100 served requests on 2026-08-30 17:54; the image moved under us. **RESOLVED session 44 — `GPU T4 x2` is NOT UI-gated.** Session 42 re-tested `--accelerator` with four spellings (`gpu-t4x2`, `GpuT4x2`, `gpu-t4-x2`, `TPU_OR_GPU_T4X2`), watched the server normalise every one to the generic `Gpu`, and concluded it could not be set from the API. The flaw was in the spellings, not the flag: **all four are invalid enum values**, and an invalid `machine_shape` falls back to `Gpu` silently. The value `kagglesdk` documents is **`NvidiaTeslaT4`** (`kagglesdk/kernels/types/kernels_api_service.py`, alongside `NvidiaTeslaP100` and `Tpu1VmV38`), it is stored verbatim by the server, and the allocator honours it. Probe `polyforge-t4-probe`: `device count: 2`, `GPU0/GPU1 Tesla T4 -- cuda capability 7.5`, matmul executing on both cards — the `cudaErrorNoKernelImageForDevice` that killed all 16 cells is gone. The bench was re-pushed on T4 x2. See `WAVE4_FREE_ROUTE.md` 0b. | — |
| **L4** | Score B1 (frozen 4x4 matrix, WL-H1/H2/H3) | blocked behind L3 |

See `docs/ZERO_COST_ROADMAP.md` for L1–L4 in full, including the pre-committed
decision points and the P100-vs-T4x2 fallback.

### L5 — demand half **DONE (session 42)**, and it needs no GPU

`eval/harness/trace_demand.py` + 9 tests. It **calls the sim's own
`trace_matrix.window_buckets`** rather than reimplementing it, so "the live
plane saw the demand the simulator replayed" is true *by construction* — one
projection, not two that agree today. `trace_matrix.py` is untouched, so
`RESULTS_TRACE_PARITY.md` and `RESULTS_BUDGET_PARITY.md` still replay
byte-identically.

**The finding that reorders this roadmap.** Measured on window 0 of the
committed BurstGPT trace:

| quantity | value |
|---|---:|
| peak aggregate arrival rate | **1.377 rps** |
| mean aggregate | 0.316 rps |
| max single tenant | 0.501 rps |
| buckets (6 h at 10 s) | 2160, 1320 non-zero |
| kinds | **chat only** |

`scale_factor` targets **work units** — mean per-tenant demand equals one
replica's capacity — not requests/second, and chat is expensive per request. So
the trace-driven cell needs **~1.4 rps, roughly 80x below the synthetic
`joint_stress` cell's ~110 AI rps**.

**Therefore L5 does not need the GPU throughput B1 is blocked on.** The
calibrated mock (`kaggle_tier_server.py --mock`, tier latencies straight from
`TIER_BENCH.md`) serves 1.4 rps trivially on this machine, and Docker is up.
**L5 is executable now while L3/L4 wait on Kaggle's UI-gated accelerator** —
the reverse of the priority order this document was written with. Pinned by
`test_the_windows_arrival_rate_is_modest`, which fails loudly if the projection
ever rescales and voids that conclusion.

**Disclose, do not paper over:** the trace is chat-only, so this cell exercises
the AI path and NOT the CRUD path — the opposite bias to every synthetic cell
used so far. Mixing in synthetic CRUD to look balanced would forfeit the point.

**Still needed:** a NEW pre-registration (new scored comparison — folding it
into `PREREG_WAVE4_LIVE_PLANE` would widen a frozen prereg after seeing
results), the cluster-backend wiring, and the sitting itself.

*Original plan:*

### L5-original. Drive the live plane from a real trace (**closes W4**)

The sharpest un-conceded objection: traces are replayed in *simulation*
(bit-for-bit), and the live plane drives *synthetic* cells (`crud_bursty`,
`ai_cacheable`, `tier_mixed`, `joint_stress`). **No real trace has ever driven
a real cluster.**

**Work:** a trace-derived demand source for `cluster_backend.k6_script` — take
BurstGPT window demand, project it onto the per-tenant `Demand` shape the
harness already emits, and drive k6 from that instead of a `WorkloadClass`.
The plumbing exists; what is new is the trace->bucket projection and a prereg.

**Cost control:** this needs the AI path live. Two honest substrates:
- the batched Kaggle server from L2 (free, real generation), or
- the **calibrated mock** (`MOCK_DELAY_S = {"small": 1.24, "mid": 1.88}`,
  which *is* `TIER_BENCH.md`), disclosed as "tier latency injected from the
  committed bench rather than generated." Weaker, free, and still the first
  real-trace live sitting the project has.

**Needs a NEW pre-registration** — it is a new scored comparison. Do not fold
it into `PREREG_WAVE4_LIVE_PLANE`.

### L6 — **DONE (session 43). Verified live, and it found a security defect.**

**Session 43 closed the open half.** The scrape was checked against a real
control-plane under load (`eval/results/l6_scrape_verification_evidence/`):
`observer.csv` reached `http_dur_count=180` for exactly the 180 requests sent,
and `metrics_http_duration.csv` carries the bucket rows a percentile needs.

**The W5 gap is measured rather than asserted.** Over the 172 served requests,
CPU service time — what every live record's `crud_p95` reports — is mean
**1.1496 ms / p95 1.4105 ms**, and end-to-end is mean **1.3911 ms / p95
4.7778 ms**: **+21.0% on the mean and +238.7% on p95**. Same 172 requests on
both sides. The mean gap is the telemetry write; the p95 gap is that write's
own tail, and it more than triples the percentile. **Not transferable** — one
host, SQLite, no concurrency, 180 requests. The sign is structural, the size is
not this number, and attempt 10 stays closed: the histogram was never scraped
there and V8 pre-committed it as the last sitting.

**It also found a remotely triggerable memory leak**, which is the argument for
running instruments rather than reasoning about them. 8 of the 180 requests
were rate-limited and appeared under the **raw path** as a route label, because
`ServeMux` sets `r.Pattern` only after it matches and `rateLimit` answers 429
above the mux. That label keys two unbounded maps, one allocating a histogram
per key — so any caller could mint permanent series, unauthenticated, by
tripping the rate limiter or requesting a 404. Fixed (`routePattern` resolves
through the mux, falls back to one constant; `metrics.boundRoute` caps labels),
4 tests, mutation-checked. `docs/SECURITY.md` §Session-43.

*Session 42's half, kept for the record:*

### L6 — **PARTLY DONE (session 42). The instrument already existed.**

**What I found.** W5 is not missing instrumentation. `server.go`'s middleware
already times the whole handler — telemetry write and response included — and
exports it as **`polyforge_http_request_duration_seconds{method,route}`**.
`replay.go` computes its own `latencyMS` *before* the telemetry write
(line 76 vs the write at line 94), and that narrower number is what lands in
the telemetry table and therefore in every `crud_p95`.

So the fix is not "measure it" but "capture it": **nothing ever scraped
`/metrics`.** No file in `live_soak_attempt10_evidence/` contains it, and no
`eval/scripts` entry fetches it.

**Consequence, stated plainly: attempt 10 cannot be re-read against the correct
metric.** The histogram lived in the pods and went with the cluster. SK-H3's
FAIL stands as scored on the CPU-time metric, and no retrospective analysis can
change that.

**Done:** `scripts/soak_observer.sh` now scrapes it every sample —
`observer.csv` gains `http_dur_sum_s` / `http_dur_count` (cumulative, so
consecutive samples give an interval mean) and full bucket rows go to
`metrics_http_duration.csv`, which is what a p95 actually needs. Scraped over
the **NodePort the load already uses, not a port-forward** — a port-forward is
what pinned every request to one pod of sixteen in attempt 4 — and `/metrics`
bypasses the rate limiter, so the scrape cannot consume a tenant's budget. It
fails to empty like every other field in that script.

**Verified offline:** `bash -n` clean, and the awk extraction checked against a
realistic exposition payload (filters the replay route, excludes `/healthz`,
87.5 s / 12,000 -> 7.29 ms mean). **Not verified against a live cluster** —
that happens at the next sitting, and until then the scrape is unproven.

**Still open:** deciding which metric the live records should quote. That is an
L5 decision, not this one, and it must be stated rather than silently switched.

*Original plan:*

### L6-original. Fix the live latency semantics (**closes W5, and W2's confound**)

`internal/platform/replay.go` stops its clock before the telemetry write, so
`crud_p95`/`crud_p99` in **every** live record measure CPU service time, not
service latency. Pinned by `replay_latency_semantics_test.go`, disclosed
pre-run — but it is a caveat that follows every live number in the paper.

**Work:** emit *both* — keep the existing metric under its current name so
committed records stay comparable, and add a true end-to-end
`crud_service_p95`/`p99` measured to response completion. Then SK-H3's
0.001–0.21 ms exceedances can be read against a metric that means what its
name says.

**This does not re-open attempt 10.** V8 pre-committed it as the last sitting
on this machine and SK-H1/SK-H3 stand as scored. L6 is for L5's sitting.

### L7. Multi-node kind topology (**partial W7**)

`cluster_backend.NODES_BY_SIZE` is already `{"small": 2, "medium": 4,
"large": 6}` — multi-node is *already available locally* and the soak ran on
`small`. Run L5's sitting on `medium` (4 nodes) so the record is not
single-node. This does not buy multi-*host* or multi-zone; say so.

---

## §3 Workstream M — close the measurement gaps

### M1. ~~p99 and token-level latency~~ — **WITHDRAWN / RE-SCOPED (session 42)**

**Third premise error in this document.** The plan below proposed adding a p99
estimator to `model.py`. That would have **violated a frozen pre-registration**.
`PREREG_LIVE_CHAOS_P99.md` Part B says the opposite, and says it deliberately:

> the *sim* is a p95 estimator by construction (`P95_FACTOR`), so p99 is a
> **live-only** number and is reported only for the live cluster, **never
> back-fitted into the sim tables**.

`RESULTS_MASTER.md` ground rule 5 repeats it. And the live half is **already
implemented**: `cmd/control-plane/evalexport.go` emits `CrudP99MS` / `AIP99MS`
by the same order-statistic path as p95, with the prereg cited at line 112.

**So there is no p99 work.** The p95-not-p99 item is not an open gap; it is a
stated, pre-registered modelling boundary with the live number already exported.

**What survives, re-scoped to a GPU task.** TTFT/TPOT cannot be derived from the
committed bench: `TIER_BENCH.md`'s protocol is *"fixed 48-token completions"* —
a single output length, and separating prefill from per-token decode needs at
least two. **Folded into L3**, which is already a GPU session: bench at 2+
output lengths and the decomposition falls out at no extra sitting cost.

*Original, withdrawn:*

### M1-withdrawn. p99 and token-level latency

The roadmap promised p99; the model produces p95 via `P95_FACTOR`, and latency
is request-level with no TTFT/TPOT. For an LLM-serving paper at Transactions
this is the most substantive modelling gap left.

**Work:** add a p99 estimator alongside `P95_FACTOR`, and decompose AI latency
into prefill (TTFT) and decode (TPOT) using the tier bench's own token counts
(`MAX_NEW_TOKENS = 48` is already fixed, so TPOT is directly derivable).
**Default OFF**, so every published arm replays byte-identically (R4).

### M2 — **DONE (session 42), folded into `RESULTS_WINDOW_CHARACTER.md`**

`jcac_nobudget` is a pre-registered arm, so lifting the per-tenant cap is a
*designed* counterfactual rather than a post-hoc feature. jcac's overshoot gap
against `hpa_fair` decomposes as:

| component | mean excess | share |
|---|---:|---:|
| total gap | 0.504588 | 100% |
| **budget-induced** (cap forces AI shed) | 0.400933 | **79.5%** |
| **genuine control error** | 0.103656 | **20.5%** |

Crossed with M3's classes, the result is sharper than either alone:

| class | windows | total gap |
|---|---:|---:|
| sheds AI | 45 | 1.076453 |
| does not shed | 51 | **0.000002** |

**In the 51 non-shedding windows the overshoot gap is 2e-6** — jcac is
indistinguishable from the fair baseline. The entire SLO penalty lives in the
same 45 windows that carry the entire cost advantage. Induced component vs shed
share: Spearman **+0.986**.

So A4 (severity reverses on real demand) and A5 (cost concentrated, not
pervasive) are **not two weaknesses**. They are one trade, measured: where
PolyForge sheds AI it is much cheaper and much worse on overshoot; where it
does not shed it is neither. Four fifths of that overshoot is a constraint no
replica-only controller can satisfy at all — which is exactly the feasibility
result.

*Original plan:*

### M2-original. Decompose the severity reversal (**explains W1 and A4**)

BP-H2 established the overshoot is *partly* constraint-induced: lifting the
budget takes jcac's excess 4.83 -> 3.15, against `hpa_fair`'s 0.19. So some is
genuine. **Quantify the split** — a shed-attribution pass over the BurstGPT
windows separating (a) violation caused by `tier="none"` shedding under the
budget filter from (b) violation from control error. Descriptive, desk-only.

This is the analysis that turns "the severity claim reverses on real demand"
from an embarrassment into the paper's mechanism section.

### M3. Characterise *when* joint control pays (**closes A5**)

Only 42.7% of BurstGPT windows favour jcac; the mean is −35.586 and the median
+2.232. Rather than concede "concentrated in outliers", **classify the
windows**: fit window features (burstiness, cache reuse, tier mix, cap binding)
against the sign of the cost delta. If the favourable windows are the ones
where cache and tier are load-bearing — which is the paper's own claim — that
is a *prediction confirmed*, not a caveat.

Descriptive, no prereg, desk-only. Potentially the strongest positive result
still available at zero cost.

---

## §4 Workstream C — the semantic cache (**closes W8**)

### C1 — **DONE (session 42). Retrieval is not the binding constraint.**

`RESULTS_CACHE_CEILING.md`. On **exactly duplicated prompt text** retrieval is
perfect by construction, so any surviving disagreement is the model answering
the same question differently. 120,000 sampled pairs, 4,008 duplicate groups,
10,441 scored pairs (capped at 20 per group so `"hi"` cannot become the
estimate).

| stratum | pairs | mean | median | precision @0.70 |
|---|---:|---:|---:|---:|
| all duplicate pairs | 10,441 | 0.5075 | 0.5302 | **0.3324** |
| **same-model** (cleanest) | 3,193 | 0.6089 | 0.6891 | **0.4898** |
| cross-model | 7,248 | 0.4628 | 0.4677 | 0.2631 |

**Verdict against a threshold fixed on disk before the number was read**
(> 0.60 would mean retrieval binds): **0.4898 -> retrieval is NOT binding.**

Decomposition of the published 0.313:

- **retrieval error: +0.047** — all that perfect retrieval recovers (same-model
  0.443 -> 0.4898). Overall it is +0.019 (0.313 -> 0.3324).
- **cross-model style divergence: ~0.227** (0.4898 vs 0.2631) — roughly **5x
  the entire retrieval component**.
- **irreducible response stochasticity:** even at identical prompt AND identical
  model the responses disagree **51.0%** of the time at rho=0.70.

Both of `CACHE_PRECISION.md`'s declared readings are now confirmed by
measurement rather than asserted, and the embedder-swap idea is retired on
evidence. **Not registered in `reproduce.py`** — LMSYS is gated, and
`cache_hit_precision.py` is not gated either; the record carries its reproduce
command instead. `cache_hit_precision.py` is untouched.

*The original C1 was based on a misreading and is kept below so the error is on
record:*

### C1-withdrawn. ~~Replace the lexical embedder~~ — **PREMISE WITHDRAWN**

**The original C1 below was wrong and is kept only so the error is on record.**
It claimed the 0.313 precision was caused by a lexical embedder. It is not:
`CACHE_PRECISION.md` states its protocol used **MiniLM-L6-v2 on both sides**,
i.e. a real sentence embedder. The "deployed lexical embedder" I took that from
is `internal/ai/embed.Local` (hashed character trigrams), which
`cluster_backend.py` uses *deliberately* for the live-plane prompt pool so that
distinct pool prompts never collide — a controlled hit rate is exactly what the
cache-SIZE knob needs. It is documented as lexical in the package docstring.
Swapping it would corrupt the knob, not improve it.

**The real question, which the record asserts but does not measure.**
`CACHE_PRECISION.md` declares that low agreement is response stochasticity
rather than a retrieval failure. That is testable with an oracle: on **exactly
duplicated prompt text**, retrieval is perfect by construction, so any
remaining disagreement is purely the model answering the same question
differently. If oracle precision is near the published 0.313, no embedder can
raise it and the "relative readings only" caveat is *proven* rather than
claimed. If oracle precision is far higher, retrieval IS the binding constraint
and the caveat is understated.

Either outcome is publishable and neither needs a GPU. New record, default-OFF,
`cache_hit_precision.py` is frozen and is not edited.

*Original, withdrawn:*

### C1-withdrawn. Replace the lexical embedder

Response-agreement precision is **0.313** at τ=0.85, and near-duplicate prompts
agree only 33.8%. `cluster_backend.py` states the deployed embedder is
**lexical** ("Pool prompts are high-entropy token strings so distinct prompts
never collide under the deployed lexical embedder"). A lexical embedder is a
sufficient explanation for low semantic precision.

**Work:** swap in a real sentence embedder (MiniLM-class, CPU-fast, free),
re-run the precision study, and report the delta. **Default OFF** behind a
config flag so committed cache records replay unchanged; the new number is a
new record, not an edit.

If precision does not improve materially, that is also a result — it would mean
the ceiling is response stochasticity, exactly as currently claimed, and the
claim gets evidence instead of an assertion.

---

## §5 Workstream R — engineering debt

| id | item | closes |
|---|---|---|
| **R1** | **DONE session 42.** The audit's framing was wrong twice. (a) Coverage was **not** 60% — CI measures **with** a `postgres:18` service and read 66.5%; my first local run showed 64.0% only because I had no database. (b) The gap was **not** in `internal/operator/controllers`, which is at **90.9%**. The real hole was `internal/storage/postgres` — the **production** backend — at **32.7%**, with tenant/project/API-key CRUD, the outbox and saga state completely untested, while `internal/storage/sqlite` (not used in production) sat at 97.3%. Added 10 integration tests; that package is now **63.2%**. Totals: **66.4% -> 68.8%** overall, **76.7% -> 79.5%** on business logic (excluding generated deepcopy and `cmd/` main() wiring, which is what the 80% target actually means). CI gate raised **60% -> 65%** to hold the gain. One test I wrote FAILED and the code was right: `RecentByTenant` deliberately returns the newest N in *chronological* order, and sqlite does the identical reversal — the test now pins both halves. | W9 |
| **R2** | **DONE session 42.** Premise CONFIRMED: `cmd/control-plane/main.go` built the client straight from `redis.ParseURL`, which leaves the timeout fields zero, so `redis.NewClient` filled them with go-redis defaults — **5 s dial, 3 s read/write, 3 retries**. `server.go`'s fallback to the local bucket is correct but is only reached *after* that budget, so an outage became a multi-second stall on every request. `applyRedisTimeouts` now fills **only the fields the URL left unset** (200 ms dial/IO, 1 retry, env-overridable), so an explicit setting still wins. 6 tests. | E3 |
| **R3** | **DONE session 43, and the premise was wrong about the adapter.** The audit's E4 reading was right — detector implemented, feed missing — but "Pixie/Hubble adapter" names two things that cannot supply the signals. **Hubble observes network flows and carries no CPU stall, syscall or memory-pressure signal at all**, so no Hubble adapter can fill one field of `Sample`; Pixie could supply syscalls, but only by deploying a second data plane for one of three axes. Two of the three fields are **kernel PSI**, which cAdvisor already exports per container and this repo already scrapes — so the feed that can actually run is PromQL. `fairness.PSIFeed` (`psi_feed.go`) polls Prometheus on the plan interval and drives `Detector.Observe`; wired in `cmd/operator/main.go` behind `POLYFORGE_PSI_PROMETHEUS_URL` and plumbed through the operator chart (`interference.*`), **off by default**, so every published campaign's behaviour is unchanged. Two disclosures kept in the code, not just here: (a) **there is no syscall rate** — no standard exporter publishes one, the default query is empty, and the detector scores on two of three axes until an eBPF exporter is configured; (b) `MemPressureEvents` says *events* and PSI publishes *stalled seconds* — the detector scores ratios to the peer median, which is unit-invariant, but `ratioExcess`'s epsilon floor is not, so for that signal the floor is 1 second of stall per window. A failed scrape **fails the whole poll** rather than reporting zeros: a tenant zeroed while its peers are measured would read as the quietest in the cluster. 9 tests (mapping, unconfigured signal, scrape outage, upstream error envelope, unlabelled series, range-vector rejection, URL escaping, and the ticker flagging a persistently noisy tenant). **Unverified against a live cluster with PSI enabled** — the series names are defaults to check, overridable per signal. | E4 |
| **R5** | **DONE session 43 — the reproduction gate had been RED IN CI since 2026-08-31 and nobody was looking.** Four records failed on a clean checkout while passing on the author's machine, which is the worst shape a byte-identity claim can take. Four independent causes, all fixed at the source: (1) **`pandas.read_csv` is not exact.** Its default float parser returns 414.3570178904792 for the exact text `414.35701789047914`; one ulp, and a rank test turned it into p=1.26e-11 against the record's 1.17e-11. Every two-path loader now passes `float_precision="round_trip"` — max |db − csv| goes from 1.5e-11 to **exactly 0**. Single-path CSV loaders are deliberately untouched: their records were generated through the default parser. (2) **`analysis_separation_mt.py`'s export fallback pointed at the wrong file** — `metrics_full.csv.gz` is the v1 matrix and carries no `flash_crud` rows, so on a clean clone the record silently dropped its **entire V2 falsification section** and printed "Not evaluable" instead. Corrected to `metrics_matrix_v3_overload.csv.gz`, which holds exactly the record's five numbers. (3) **`RESULTS_TRACE_LIVE.md` could not rebuild at all** — it needs the 56 MB BurstGPT trace, which is deliberately not vendored. The window-0 projection is 5 KB gzipped and is now committed (`eval/harness/trace_cache/`), with a staleness test that re-derives it wherever the raw trace exists. (4) **S5 compared a documented-unstable float exactly** — `coupled_floor_incremental_batched`'s own docstring says its chunked sum can differ in the last bits, and on Linux row 6 came back one ulp off, failing the gate. Now compared at 1e-12 relative, six orders tighter than the reported six decimals, and the record says so. Also pinned: `eval/requirements-reproduce.txt` scopes byte-identity to the versions that produced the records (CI was installing pandas 3.0.5 against the 2.2.3 that wrote them) — prophylactic, not the diagnosed cause. Plus three failures the local battery never ran, because golangci-lint and a Linux runner are not in it: an errcheck in the new PSI feed, a pre-existing SA4004 in `evalagg.go` that was hiding a missing `rows.Err()`, and `setup-envtest@latest` resolving to v0.25.0, which **requires Go >= 1.26 against this repo's deliberately pinned 1.25.12** — an upstream release breaking a gate with nothing in the tree changing, so it is now pinned to the v0.22.4 that `go.mod` already carries. `test_batched_generation_matches_serial_exactly` imported `torch` unguarded, turning "this environment cannot run the check" into a failure; it now `importorskip`s like the model load below it already did. **And the trace-cache fix reintroduced the very defect it closed**: the committed cache stored an absolute *Windows* path, and `Path(...).name` does not split one on Linux, so the record quoted `C:\\Users\\...` there and matched here. Now stored repo-relative and POSIX, pinned by a test that asserts the property rather than the value. CI also prints the drifted record's diff on failure now — "N changed lines" is exactly what you cannot act on when the machine that reproduces is not the machine that failed.  **And the ordering itself was the defect.** Fixing envtest let the Go job reach its last three steps for the first time in a week: the coverage gate and the OpenAPI lint passed, and `govulncheck` reported **seven reachable stdlib CVEs** on the pinned Go 1.25.12, all fixed in 1.25.13. The pin exists to carry current stdlib fixes and the scan is what proves it still does — so a scan sitting behind a fragile step is a pin nobody can audit. `go.mod`'s own comment records this happening once before at 1.25.5. Bumped to 1.25.14 and verified here rather than in CI: seven reachable vulnerabilities to zero, full Go suite green on the new toolchain. | CI credibility |
| **R8** | **DONE session 43 — a fourth instance, and it sat under the paper's figures.** `scripts/reproduce.py` verified 40 records byte-identically and **never compared a single figure**: it listed which figures were *rebuilt* and printed "19/19". All nineteen could have changed — different data, different axes, a different result — and the gate would still have passed, while the thesis cites them. Both formats turn out to be fully verifiable: **PNGs byte-for-byte (19/19)** and **PDFs identical once matplotlib's embedded `/CreationDate` is stripped (19/19)**, which is the whole normalisation. The gate now compares all **38 files**, prints `figure content 38/38`, and **fails** on drift. Mutation-checked by corrupting one PNG. Caught while writing it: the new counters shadowed the record-drift variables, which would have broken the record check itself — the reason the fix is verified rather than assumed. | gate integrity |
| **R9** | **DONE session 43 - R8's own check was platform-bound, and one CI run proved it.** R8 made the gate compare all 38 figure files byte for byte; it was mutation-tested and green locally. On Linux it reported **0 of 38 identical**, in the same run that reported **40 of 40 records identical**, with matplotlib, numpy and pandas at the pinned versions on both sides - so not dependency drift. The cause is structural and was never going to hold: `figures.save()` writes with `bbox_inches="tight"`, which sizes the output from the **rendered extents of the text**, so every PDF's page geometry and every PNG's pixel dimensions are a function of the local font stack. **A rasterisation is not a claim.** What a figure asserts is the numbers it plots, so `save()` now also emits a canonical JSON of every line's vertices, every bar's geometry, every scatter offset and segment, plus scales, limits, tick labels, colours and legend labels (`eval/results/figures/content/`, 173 KB for 19 figures) - and *that* is what the gate fails on, everywhere, CI included. The byte comparison is kept and kept honest: it fails only where a renderer stamp matches the machine that drew the files, and prints why it is not comparable otherwise. Six mutation tests hold both directions - a **1e-7 relative** change to one plotted value moves the dump, a colour swap moves it, re-rendering at another DPI does not. Deleting the check, or scoping it to the author's machine, would have reproduced R7 exactly: a gate wired to nothing. | gate integrity |
| **R10** | **DONE session 43 - the gate said "byte-identical" and did not compare bytes.** `diff_record` compared `.splitlines()` lists and printed `MATCH (byte-identical)`. Measured: **14 of 40 records are not literally byte-identical** on the author's machine - `Path.write_text` emits CRLF on Windows while `.gitattributes` checks the committed copies out as LF - and the label said otherwise on every one of them. It was true in CI only by accident of the Linux checkout. `splitlines()` is also **blind to a missing final newline** (`"a" + LF` and `"a"` compare equal) and splits on form feed and the Unicode separators, so it was weaker than advertised in ways that have nothing to do with line endings. The comparison is now on bytes, with the CRLF-to-LF canonicalisation named explicitly instead of happening as a side effect, and the summary reports both numbers: how many records are byte-for-byte and how many differ only in line endings. Same family as R5-R9, one step further in: not a gate that failed to run, but a gate whose label claimed more than its check. | gate integrity |
| **R11** | **DONE session 43 - the cardinal rule was published as a claim and verified by nothing.** The supplement told reviewers, in generated boilerplate, that each pre-registration "was committed and pushed before its campaign ran, and none has been edited since". That is the load-bearing claim of the whole method - frozen rules are what stop a published FAIL being rewritten into a PASS - and it rested on the author's discipline alone. Checked against git for all **44** preregs: **37 have exactly one commit, 7 carry amendments**, so "none has been edited since" was simply false; and **one campaign had its prereg, its record, its experiment config and its metrics export added in a single commit (`33bd83c`, eviction parity)** - no independent timestamp anchor at all. The rule itself survives the audit: **no prereg was edited at or after its record's first commit**. `scripts/check_preregs.py` now derives that from history on every CI run and the page states what is true instead. Two traps avoided inside the check: `--follow` walked `RESULTS_LIVE_SOAK_V5.md` back into V4's history and manufactured a false violation (V5's prereg precedes its own record by a day), and a `fetch-depth: 1` checkout has no history at all, so the gate **refuses a shallow clone** rather than passing on one commit. The one real exception is named in the script and on the page rather than absorbed, and a disclosed exception that stops applying fails the gate too, so the allowlist cannot rot. Six mutation tests, including an injected post-hoc edit. | method integrity |
| **R12** | **DONE session 43 - the secret scan read one commit and printed green.** `secret-scan` asked for `fetch-depth: 0` - the entire history - and then ran `gitleaks detect --log-opts=-1`, which is **one commit per run**. Its own log said so every time: `1 commits scanned.` History from before the action was added had never been scanned at all, while the README lists gitleaks among the security hardening and this repository is headed for public artifact review - where a credential deleted from the working tree but still in history is exactly the exposure that matters. **Scanned offline first, deliberately, so that a finding would not surface in a CI log: 378 commits, 233 MB, `no leaks found`.** The history is clean. CI now installs a pinned gitleaks and scans all of it (12.6 s), and because "it ran" is what the last five findings were about, the job **asserts its own coverage**: the scan must report at least `git rev-list --count HEAD` commits or the job fails. Replaying the old one-commit output against that assertion fails it, as does an empty log. Considered in the same pass and recorded as a non-defect: the `sbom` job produces a CycloneDX artifact nothing consumes, but it is published as supply-chain *evidence* and claims nothing more, and a policy over it would duplicate `govulncheck`, which already gates Go CVEs. | gate integrity |
| **R13** | **DONE session 43 - the coverage gate was ordered behind the step that broke for a week, and R6 said it was not.** In the `test` job the order was: run the tests (writes `coverage.out`), then **envtest**, then the coverage gate. The gate reads `coverage.out` and has nothing to do with envtest - so during the week envtest was broken by an upstream `setup-envtest` release (R5), the coverage threshold **did not run at all** and nothing said so. R6 examined this exact placement and recorded it as legitimate: "the coverage gate stays inside `test` because it reads `coverage.out` from the step before it - a real dependency, not an ordering accident." envtest *was* the step before it. Fixed by moving the gate to sit directly after the step it actually depends on, rather than with `if: always()`, which would run it even with no profile to read. | gate integrity |
| **R14** | **DONE session 43 - the helm job rendered both charts to `/dev/null`, and the application chart could never have installed.** `helm template ... > /dev/null` catches a chart that fails to render and nothing else. Asked what the charts actually render, the answer was a live bug: the application chart defaulted to **`polyforge/control-plane:dev`** - unqualified, so it resolves to Docker Hub, where nothing of that name exists - with `ai-gateway` the same. This is **D2's defect verbatim** ("an install from a clean machine could never have worked"), fixed then in the operator chart and left standing in the application chart, because nothing ever looked at the output. Both defaults now name the images `release.yml` publishes and the tag falls back to `Chart.AppVersion` (also corrected: 0.5.0 against the 0.9.0 actually released), following the operator chart's convention. The eval harness ran on the old defaults by coincidence - kind side-loads `polyforge/control-plane:dev` - so it now **pins those names explicitly** in `HELM_EVAL_BASE_VALUES` instead of inheriting whatever the chart happens to say; 165 harness tests pass and the harness render still yields the kind-local images. CI now asserts what it renders: `planner.enabled=false` must remove the planner and the default must include it, and **every rendered image must be one release.yml publishes**. Mutation-checked by restoring the old default - it fails. Caught while writing it: the first version of the edit left the old `run:` key in the same step, and YAML's last-key-wins meant the new block was dead while `yaml.safe_load` still reported the file valid; the check now rejects duplicate keys. | gate integrity |
| **R15** | **DONE session 43 - a smoke test could overwrite a published record with numbers from a different dataset.** `semantic_cache_eval.py` wrote `SEMANTIC_CACHE.md` **unconditionally**, and its own docstring lists `python semantic_cache_eval.py` - synthetic prompts through the lexical fallback embedder - as the first usage. The committed record reports LMSYS-Chat-1M through `all-MiniLM-L6-v2`, so a smoke run replaces it with numbers of an entirely different provenance. CI ran exactly that and then `git checkout -- SEMANTIC_CACHE.md` to undo it: the hazard was known and compensated for rather than removed. Measured here - on a machine with `sentence-transformers` installed the smoke run scores **adaptive@0.85 = 1.000 against the published 0.296**, which is the record's headline. Provenance now chooses the destination: the published record is written only by a run with real conversations through the MiniLM encoder, anything else goes to a temp file and says so, and `--out` is explicit. CI asserts `git diff --exit-code` on the record instead of restoring it. Pinned in the same pass: `npx --yes @redocly/cli` was floating - the shape that broke `setup-envtest` in R5 - now `@redocly/cli@2.51.2`, the version the spec was verified against. Checked and found sound: `TestOpenAPIContractMatchesRoutes` really does hold the spec to the implemented routes, and `supplement` runs real tests. **Disclosed, not fixed:** `SEMANTIC_CACHE.md` sits outside the reproduction gate because its input is a licence-gated dataset. | record integrity |
| **R16** | **DONE session 43 - two shipped-path tests skipped on every CI run and printed `ok`.** `TestLivePlannerContract` is the only check that crosses the Go/Python boundary to the real planner service; `TestCellsScaleOnShippedPath` is the only one that exercises the shipped cell-scaling path. Both gate on `POLYFORGE_TEST_PLANNER_URL`, and **CI never set it** - so both skipped on every run while `go test` reported `ok` for their packages. Demonstrated rather than argued: with the URL set, `--- PASS` twice; without it, `--- SKIP` twice and `ok` twice. The planner is stdlib-only by ADR 0014, so CI now starts it (`python3 services/planner/planner.py --port 8090`, health-checked) before the test step, and both tests run for real. The skip is closed at the source too, with **`POLYFORGE_REQUIRE_PLANNER=1`** - the guard R6 gave the RLS suite - so a service that fails to start makes the tests fail instead of quietly reporting `ok`. Verified in both directions. | gate integrity |
| **R17** | **DONE session 43 - the last skip in the tree: a test that skipped *itself* when the thing it measures did not happen.** `TestEnvtestAuditSurvivesStatusConflict` asserts that a spec change is always audited even while the status subresource is contended. If `gather` legitimately dropped the tenant on that cycle the planner was never called and there was nothing to audit, so the test called `t.Skip` - reasonable in isolation, and its comment was honest about why. But a skip is invisible in `go test` output, so a regression that made `gather` always drop the tenant would leave the conflict path **permanently unasserted with CI green**: R16's shape, reached from the opposite direction. It now retries the contended cycle up to five times and **fails** if no attempt ever gave the planner input. Retrying is safe precisely because it only happens when nothing was planned - the spec is unchanged, so a successful cycle is never repeated. Sweep complete: all 7 `t.Skip` sites in the tree are now either guarded by a REQUIRE flag CI sets, satisfied by CI, or an explicitly opt-in probe. | gate integrity |
| **R18** | **DONE session 43 - `unittest discover` passes when it discovers nothing, and it guarded the largest suite in the repo.** The `python` job ran `python -m unittest discover -s research/jcac_sim -q`, which is **167 tests**: the simulator and baselines, and the only cover the simulator has. Measured directly - a discovery pattern matching no file prints `NO TESTS RAN` and **exits 0**. So a rename, a moved directory, or an import error at the discovery root would have removed all 167 from CI behind a green tick. The step now asserts its own count (at least 150 must run, a floor set below the measurement for the same reason the coverage ratchet is) and fails otherwise; verified against the real run at 167 and against a replayed `NO TESTS RAN` log. **pytest does not share the flaw** - measured at exit 5 on an empty collection - so the three pytest steps need nothing. The first attempt to measure that was itself wrong (`$?` after a pipe reports `tail`'s status, not pytest's) and was redone. Checked and cleared in the same pass: `research/security/cache_side_channel.py` writes only when `--out` is given and CI gives none, so unlike R15 it cannot touch a committed artifact. | gate integrity |
| **R19** | **DONE session 43 - the prereg gate checked 32 of its 44 preregs and reported nothing wrong.** R11's ordering check matched prereg to record by name, so 12 preregs whose campaign names its record differently fell into a "no matching record" line and were never ordering-checked - a gate covering three quarters of its domain while printing OK. Each of the 12 was mapped from the prereg's **own registered hypothesis prefix** (the tags in its hypothesis headings) to the record reporting that prefix. The looser rule of "first record the prereg mentions" was tried first and produced **two false violations**: `PREREG_VIOLATION_PARITY` cites TP-* while registering VP-*, and `PREREG_LIVE_SOAK_V6` cites the previous attempt's record. Both were withdrawn before they reached a commit. Two preregs legitimately have no record and are now named with why, rather than sitting in an unmatched count: `PREREG_VIOLATION_PARITY` (its frozen beta ladder was measured **inert**, 4.8319 -> 4.8314 across a 32x increase, `b184aee`; disclosed in `PREREG_BUDGET_PARITY`) and `PREREG_LIVE_SOAK_V6` (attempt 8 produced no scoreable sitting; its evidence is quoted inside V7's record). Result: **44/44 accounted for, 0 violations**. No campaign in the tree is unrun. | method integrity |
| **R20** | **DONE session 43 - eight published records were outside the reproduction gate and nothing said so.** The gate covered 40 records; the repository holds 48 in the `RESULTS_*` family. Omission and deliberate exclusion looked identical from the outside. Two of the eight were **pure oversight** and reproduced byte-identically the moment they were registered (`RESULTS_MOVE_CLAMP`, `RESULTS_LIVE_CHAOS_P99`), taking the gate to **42/42**. Four more could not be gated for a reason that was itself a defect: `live_chaos_p99.py`, `trace_matrix.py`, `trace_matrix2.py` and `trace_matrix_azure.py` wrote to `Path(__file__).parent` instead of `stats.record_path()`, so the committed record was the only place they could write - running one **overwrote the published record**, the R15 hazard again, and the gate could not redirect them into a scratch directory to compare. All four converted. The rest carry written reasons (`RESULTS_CACHE_CEILING` regenerates identically but needs sentence-transformers, absent from the pinned environment, and 565 s to encode 16,422 responses). A **coverage audit** now fails the gate if a `RESULTS_*` record is neither gated nor listed with its reason: 42 gated, 7 excused, **0 unaccounted for**. | gate integrity |
| **R21** | **DONE session 43 - the roadmap quoted a number the record does not contain, and the master record's completeness claim was false.** The records are gated byte-for-byte; this roadmap is prose that copies from them, and **no gate reads prose**. A node share appears here as `0.3166` in three places for a record that reports **`0.3165`**. Corrected, and `scripts/test_roadmap_claims.py` now holds 13 quoted headline figures to their records in CI - one-directional, so dropping a claim is free and changing a measured value is not. Separately, `RESULTS_MASTER.md` opened with "the single reading entry point for **every** measured result" while its campaign ledger stopped at session 27, missing **seven** records including the 24 h soak, the S3 floor and the multi-node sitting. Authoring those summaries is the author's work, so the fix is mechanical instead: `records_index.py` generates `RECORDS_INDEX.md` - all 67 records, the hypotheses each names, and whether the gate regenerates it - and **that index is itself gated**, so the set of records cannot drift again. Verdicts are deliberately excluded: a PASS misattributed by a regex is worse than no index. `RESULTS_MASTER`'s claim is corrected to what it actually does. | record integrity |
| **R22** | **DONE session 43 - a clean clone regenerated `VTC_FAIRNESS.md` as a table of zeros.** Probing the records outside the gate found ten generators writing to their own directory instead of through `stats.record_path`, so running one **overwrote the published record** - and four produced different content on a clean clone, silently. The sharp case: `load_worst_tenant()` hardcoded `agg_fairness_v2_worst_tenant.csv.gz` as its fallback, and `analysis_vtc.py` calls that same loader with `vtc_fairness.duckdb`. Without the archive DB, VTC scored **the fairness campaign's 200 rows**, whose run_ids do not match its own, and every measured value regenerated as **0** with p=1 and d=0 - a table that still read like a result. That is R5's wrong-fallback defect alive in a second script. The fallback is now keyed to the campaign being scored, VTC's own aggregate is exported and committed, and the record reproduces identically on a clean clone with its real verdicts (HV1 PASS, HV2 PASS). All ten generators converted, so none can overwrite a committed record again. Two further defects in the aggregate exporter, both breaking "re-export and diff": two queries had **no `ORDER BY`** (DuckDB promises no order) and gzip stamped the current time into every header. Both fixed; two consecutive exports are now byte-identical with all content unchanged. | record integrity |
| **R23** | **DONE session 43 - record coverage widened from one family to every record.** The audit added in R20 policed only the `RESULTS_*` family, so **19 Wave 1-5 records** sat outside both the gate and the audit - which is how `COORD_GAP` could be hand-extended and `VTC_FAIRNESS` could regenerate as zeros with nothing noticing either. Each was probed on a real clean clone rather than judged by inspection. **Seven reproduce identically and are now gated** (`CELLS_SHIPPED`, `DEGRADE_PROBE`, `EFFECT_SIZES`, `OBJECTIVE_FORM`, `PLANNER_CELLS_DEALIAS`, `VTC_FAIRNESS`, `COORD_GAP_ANCHORED`), taking the gate to **50 records**. The rest carry written reasons - and two are findings in themselves. `PLANNER_CELLS` and `PLANNER_SCALING` are **wall-clock benchmark tables**, so they cannot reproduce byte-identically on any other machine and never could. **`COORD_GAP.md` is hand-extended beyond its generator**: re-wrapped prose, an added heading, and a whole section diagnosing the 37 discarded instances that `coordination_gap.py` does not write - against a repository rule that records are machine-written. Its numbers were checked mechanically against a clean-clone regeneration and agree exactly (both data rows verbatim, CG-H1 PASS both ways), so no result is misstated; what fails is the provenance claim, and it is now stated rather than assumed. Final position: **50 gated, 21 excused with a reason, 0 unaccounted for**, and the audit fails if that ever stops being true. | record integrity |
| **R7** | **DONE session 43 — a third instance of the same class, and the largest.** `research/analysis/` holds four test files — `test_stats.py`, `test_trace_parity.py`, `test_budget_parity.py` and the new `test_skh1_discriminability.py` — **23 tests that CI has never run**. They existed, they passed locally, and they gated nothing: no job in `ci.yml` referenced the directory. This is the shape R6 hunted, one level up — not a gate ordered behind a fragile step, and not a skip that prints `ok`, but a gate that was never wired at all. Now its own job (`analysis-tests`), on the pinned dependencies, for the same reason `vulncheck` and `supplement` have one. | gate integrity |
| **R6** | **DONE session 43 — the gate audit the CVE finding forced.** The seven reachable CVEs were not really a stale-pin problem; they were an **ordering** problem. `setup-envtest` broke on an upstream release, the Go job stopped there, and the three gates behind it — coverage, OpenAPI lint, **vulnerability scan** — did not run for a week, with nothing to say so: a job that fails early looks exactly like a job that fails late. The same shape had already hit the `python` job in the same run, where an unguarded `torch` import skipped the runner smoke, the security study and D3's supplement gate. **Structural fix:** the CVE gate, the OpenAPI lint and the supplement gate are now their own jobs, and the two steps in `python` that depend on nothing above them carry `if: always()`. The coverage gate stays inside `test` because it reads `coverage.out` from the step before it — a real dependency, not an ordering accident. **Then the same class was grepped for rather than waited for**, and turned up twice more: `internal/storage/postgres` (the production backend, and the ONLY cover RLS tenant isolation has) and `cmd/control-plane`'s SQLite/PostgreSQL export parity both **skip** when the fixture URLs are unset — and a skip prints `ok`, so a CI service that failed to start would take both claims with it into a green build. `POLYFORGE_REQUIRE_POSTGRES=1` (set in CI) now turns those skips into failures; verified in both directions and against the real fixture. The gate battery had warned about this in prose since session 36, which is not a mechanism. | gate integrity |
| **R4s** | **DONE session 42 — v2's walk: ~36 min CPU -> 33 s, record byte-identical.** Original note: `analysis_separation_mt_v2.py` burns **~36 min of CPU** per gate run on the *scalar* ordered walk, and T1's `coupled_floor_incremental_batched` computes the same thing 20-50x faster. `BatchedOrderedWalkTests` already proves per-vector bit-identity at exactly the tenant counts v2 uses (n<=6). Switch v2's internals and **verify the record is byte-identical before committing** — the aggregate is a chunked sum, so last-bit float drift is the one real risk, and at 6dp it should not surface. Do NOT change v2's record. | gate cost |

---

## §6 Workstream D — distribution

| id | item | owner |
|---|---|---|
| **D1** | Zenodo deposit — bundle **built and verified session 42** (404 files, 41 preregs, all records) | author publishes -> DOI |
| **D2** | **DONE session 42 (desk half).** The roadmap said "built at the desk; pushing needs the author's token" — wrong on both counts. `release.yml` already builds, signs (cosign keyless) and SBOM-attests on a version tag, so no agent build is needed; but it covered **only `control-plane`** of the repo's **four** deployable images, and `helm install polyforge-operator` pulls the operator AND planner. **An install from a clean machine could never have worked, token or not.** The charts were also linted on every CI run and published nowhere. Now: a 4-way matrix (control-plane / ai-gateway / operator / planner), each signed + attested, plus a `charts` job that packages both charts and `helm push`es them to `oci://ghcr.io/<repo>/charts` after the images land. Operator chart defaults corrected from `polyforge-operator` to `polyforge/operator` so they match what is actually published; **verified the eval harness still overrides them** (`--set=operator.image.repository=polyforge/operator`), so B1 is unaffected. Caught while writing it: the planner Dockerfile `COPY`s `research/jcac_sim/`, so it needs the repo root as context, not `services/planner` — a narrower context would have failed the first release. **RELEASED session 42 as `v0.9.0`** — workflow green, all five jobs: control-plane, ai-gateway, operator, planner (each cosign-signed + CycloneDX SBOM attested) and both charts to `oci://ghcr.io/mohaimin-8/polyforge/charts`. Version matches the operator chart's `appVersion`; the repo had no prior tags. **One gap remains and it is a disclosure decision, not an engineering one: the repo is PRIVATE, so the packages inherited private visibility and an anonymous `helm pull` returns 401.** Artifact reviewers need them public. Flipping package visibility on a pre-submission thesis repo is the author's call. | **user: make the GHCR packages public when ready** |
| **D3** | **DONE session 43.** `scripts/build_pages.py` + `scripts/pages_markdown.py` build a static supplement — 46 records, 42 pre-registrations, 19 figures, cross-linked — and `.github/workflows/pages.yml` deploys it. **The gate badge is imported from `reproduce.py`, not re-declared**, so "verified byte-identically" is the gate's own list (same principle as `trace_demand.py` calling the simulator's `window_buckets`). Three things the build surfaced rather than hid: (a) a `RESULTS_*` prefix glob **silently dropped four gated records** including the headline `RESULTS.md` — the document set is now the union with the gate, and a test fails if any gated record has no page; (b) `RESULTS.md`, `RESULTS_V2.md` and `RESULTS_V3.md` contain **38 table rows with unescaped `|dz|`**, which split into surplus cells and strand a `**` — GitHub renders them equally wrong, the records are frozen, so the supplement reproduces the source rather than guessing; (c) **fig18 and fig19 have no caption in `FIGURES.md`** — they are published carrying that reason, and `--strict` refuses the build. 14 tests: gate parity, dead links, byte-identical rebuilds, and that the build writes nothing into the evidence base. `site/` is gitignored; the generator is the artifact. | **user: Settings -> Pages -> Source: GitHub Actions (one-time), and the same public/private decision as D2** |

---

## §7 Execute order

**As of session 43 this order is spent.** Everything on it is DONE, WITHDRAWN
with its premise on record, or waiting on the author. Kept below with its
outcomes so the sequence is auditable rather than deleted.

```
T1  S3 exact walk           DONE s42   floor 0.126189, S3 FAIL published
L2  batched tier server     DONE s42   11.6x on the mock
M2  severity decomposition  DONE s42   79.5% of overshoot is the budget cap
M3  window characterisation DONE s42   the cost win and the SLO penalty are
                                       the same 45 windows
C1  real embedder           WITHDRAWN  retrieval adds +0.047; the ceiling is
                                       response stochasticity
L3  re-bench (~30 min GPU)  DONE s44   T4 x2 set from the API; 16/16 cells,
                                       0 errors, exactness True both tiers
L4  score B1 (2-4 h GPU)    USER       free route MEASURED infeasible for the
                                       frozen cell; needs a rented GPU (~$5-15)
                                       -- substrate + amendment prepped, s44
L6  latency semantics       DONE s43   verified live; W5 gap +21% / +239%
M1  p99 + TTFT/TPOT         WITHDRAWN  PREREG_LIVE_CHAOS_P99 Part B forbids it
L5  trace-driven sitting    DONE s42   TL-H1 PASS, TL-H3 PASS
L7  multi-node              SUPERSEDED by PREREG_MULTINODE_V2: MN-H1 PASS,
                                       3 nodes, 0.3165/0.3815/0.3019
T2  WITHDRAWN   T3 DONE s42   R1 DONE s42   R2 DONE s42   R3 DONE s43
R4s DONE s42    R5 DONE s43   R6 DONE s43   D2 DONE s42   D3 DONE s43
R7 DONE s43     R8 DONE s43   R9 DONE s43   R10 DONE s43  R11 DONE s43  R12 DONE s43  R13 DONE s43  R14 DONE s43  R15 DONE s43  R16 DONE s43  R17 DONE s43  R18 DONE s43  R19 DONE s43  R20 DONE s43  R21 DONE s43  R22 DONE s43  R23 DONE s43
D1  Zenodo bundle built ------------------- AUTHOR: publish for the DOI
-------------------------------------------------------------
§9  writing                 HELD
```

**What is left that is not writing: three things, all the author's.**

An earlier draft of this paragraph (written mid-session 44, before the
measurements below existed) said the GPU was no longer one of them. That was
half right and is corrected here. `GPU T4 x2` was indeed never UI-gated —
session 44 set it from the API with `machine_shape: NvidiaTeslaT4` — and that
**did** unblock L3, which is now DONE. It did **not** unblock B1.

**B1's frozen cell is measurably out of reach on the free route**, which is a
stronger statement than "slow": the cell needs ~64 AI rps at base and ~110 at
peak against a measured 26.5–46.5 on T4 x2, while Amendment clause 3 vetoes a
run whose slowest tier exceeds 2500 ms and `mid` measures 2310 ms pinned
(190 ms of headroom, against a tunnel that costs 50–300 ms) or 3081 ms sharded
(over target outright). The batching that would close the throughput gap
pushes `mid` to 3285 ms at batch 32. **No batch size satisfies both gates** —
see `WAVE4_FREE_ROUTE.md` and `TIER_BENCH.md`.

So the three are: **rent a GPU host for B1** (~$5–15; substrate, pre-run
amendment and runbook are prepped and committed — `WAVE4_RENTED_HOST_RUNBOOK.md`,
`scripts/b1_tier_host.sh`), **publish the Zenodo bundle**, and **make the GHCR
packages public / set `PAGES_ENABLED=true`** if the supplement should be
reachable.

Rationale: the four desk items at the top need no external resource and two of
them (T1, M3) can produce *positive* results. L3 is the cheapest thing that
resolves the biggest uncertainty, so it sits right after the desk work that
does not depend on it.

---

## §8 What stays open at 100% — the honest residue

Finishing everything above does **not** produce:

1. **A cost advantage.** BP-H1 stays FAIL on both traces. Nothing here targets
   it, and nothing should: the feasibility result is the finding.
2. **Multi-host or multi-zone evidence.** Multi-NODE is no longer in this
   list — `RESULTS_MULTINODE.md` measured 16 pods across three workers at
   0.3165 / 0.3815 / 0.3019, so W7's residue is *single machine*. What
   stays open is exactly that: every node is a container sharing one
   kernel, one disk and one NIC, so nothing here speaks to partitions,
   zone failure or cross-host latency, and nothing killed a node.
3. **Production scale.** SageServe's 10M served requests remains conceded; the
   10.63M-request demand-side replay is the analog.
4. ~~**Three model tiers**, on the free route. `large` needs VRAM the free
   pool does not have.~~ **RETIRED session 44.** The free pool does have the
   VRAM — `GPU T4 x2` is 2 x 16 GB and the 7B at fp16 is ~15 GB. Re-run with
   `machine_shape: NvidiaTeslaT4`, all three tiers reported
   `modules offloaded to cpu/disk: 0` and `large` went 20665.1 ms -> 3171.5 ms
   (6.52x, 2.32 -> 15.13 tok/s). What the item got right is that the *earlier
   allocation* could not hold the model; what it got wrong is calling that a
   property of the free route. `research/calibration/tier_bench_t4.csv`.
5. **Attempt 10's SK-H1/SK-H3 FAILs.** Scored, closed, reported as measured.

That residue is the paper's limitations section, and it is short and defensible.

---

## §9 Writing — HELD

WP9 (reframe around feasibility), WP10 (RB-H1 family declaration in the stats
section), WP11 (OSF DOIs, Zenodo DOI into §Reproducibility, title-page macros,
proofread, arXiv, submission), thesis author/supervisor/roll fill-ins,
`references.bib` placeholders.

**Not started until the author says to start.** Listed so the sequence is
complete, not so it gets picked up.
