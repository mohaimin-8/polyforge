# WP14 remediation roadmap — making attempt 5 the last one

**Status:** written 2026-08-19, after attempt 4 closed INVALID (third SK-H4
failure). Source of truth for every fix between now and the next 24 h sitting.

## The premise this roadmap is built on

Four attempts failed. Each one fixed the previous failure and was then
validated *by the next 24 h run*. That is the actual defect in the method: a
24 h run is a terrible test harness for a one-line change, and it is why four
days were spent discovering four independent problems serially.

**Nothing here is validated by the 24 h run.** Every fix is proved by a short,
cheap run that can only fail in the specific way that fix addresses, and the
24 h sitting is the *last* step, entered only when every instrument has
already been shown to work.

Second premise, equally important: **three of five hypotheses could not fail**
in attempt 4 — not because the system was good, but because their instruments
were absent or saturated. So every hypothesis now needs a **positive control**:
a deliberate perturbation that makes it FAIL on demand. A hypothesis that has
never been observed failing is not a test, and Phase 6 refuses to proceed
until each one has been.

---

## Phase 1 — the load path (problems #1, #11)

### 1.1 Replace `kubectl port-forward` with a NodePort on a kind port mapping

**Problem.** `kubectl port-forward service/X` resolves to one pod at start and
never follows the Service. All ~300 req/s landed on one replica of sixteen,
which reached its 256 Mi limit, was OOM-killed, and took the forward down with
it — 282 times. Fifteen replicas were idle throughout. The industry-standard
alternatives are an in-cluster generator or a NodePort; port-forward is a
development convenience, not a load path.

**Change.**

1. `eval/harness/cluster_backend.py:140` builds the kind config. Add a port
   mapping to the control-plane node:

   ```yaml
   kind: Cluster
   apiVersion: kind.x-k8s.io/v1alpha4
   nodes:
     - role: control-plane
       extraPortMappings:
         - containerPort: 30080
           hostPort: 30080
           protocol: TCP
     - role: worker
   ```

2. Add a NodePort Service (`nodePort: 30080`) targeting the control-plane
   Deployment. Apply it as a harness step, not in the product chart — this is
   evaluation scaffolding and must not ship.

3. Point k6's base URL at `http://127.0.0.1:30080`. Delete
   `PortForwardSupervisor` and its restart bookkeeping — with no userspace
   proxy there is nothing to supervise, and keeping it would leave SK-H5
   measuring a mechanism that no longer exists.

Traffic then goes host → docker port publish → kube-proxy iptables → all 16
endpoints. Balancing happens in the kernel, and no single pod carries the run.

**Fallback if 1.1 proves unstable in Stage A:** run k6 **inside** the cluster
as a Job against the ClusterIP, script in a ConfigMap, summary written to a
mounted volume. This is the approach the k6 documentation and the k6-operator
take, and it removes the host hop entirely. It is second choice only because
it changes how the harness collects `k6-summary.json`, which is otherwise
working code.

### 1.2 Assert the load actually spread — the check that would have caught this in attempt 1

**Problem.** Nothing in four attempts ever verified that requests reached more
than one pod. Every health signal was satisfied by one working replica.

**Change.** New gate `check_load_distribution` in `cluster_backend.py`, run at
the end of the load window:

- read per-pod request counts (control-plane `/metrics`, or per-pod row counts
  in `telemetry_events` if the schema carries a pod label — add one if not);
- **FAIL the run if any single pod served > 25% of total requests**, or if
  fewer than 80% of replicas served any traffic at all.

With 16 replicas an even split is 6.25% each; 25% is a generous ceiling that
still catches pinning instantly. This runs in *every* stage, including the
10-minute smoke, so pinning can never again survive to hour 24.

### 1.3 Memory headroom, measured rather than guessed

**Problem.** 256 Mi was adequate for an idle pod (8–16 Mi) and fatal for one
carrying the whole workload (209 Mi and climbing).

**Change.** Keep the 256 Mi limit — changing it would break comparability with
the committed B2 runs, and with load spread each pod should sit near its idle
figure. Instead **measure and gate**: record peak per-pod RSS in Stage C and
fail if any pod exceeds 60% of its limit. If that trips, raise the limit as a
disclosed, pre-registered change rather than discovering it at hour 17.

---

## Phase 2 — instrumentation (problems #5, #9, #10)

### 2.1 Make `eval-export` emit time buckets

**Problem.** `cmd/control-plane/evalexport.go` emits run-level scalars and has
no timeseries field at all, so `cluster_backend`'s
`export.get("timeseries", [])` is always empty. `live_soak.yaml`'s
`timeseries_reps: 0` leaves the DuckDB table empty too. SK-H3 needed hour
buckets that no code path produced, and they had to be rescued from Postgres
by hand before teardown destroyed them.

**Change.** Add `--bucket-seconds=N` to the `eval-export` flag set. When set,
emit a `buckets` array alongside the scalars, one row per window:

```
bucket_start_utc, n_events, crud_p95_ms, crud_p99_ms,
ai_p95_ms, ai_p99_ms, mean_violation, violation_step_share
```

Reuse `evalPercentile` unchanged so bucket percentiles are the same
nearest-rank order statistic as the scalars — `percentile_disc`, not
interpolation. Set `--bucket-seconds=3600` for the soak and
`--bucket-seconds=60` for short stages, so a 10-minute smoke still produces
ten scoreable buckets.

Harness: persist `buckets` into the DuckDB regardless of `timeseries_reps`,
which controls per-step tenant traces and is a different concern.

**This retires `export_soak_buckets.sh`.** Keep the script for reproducing
attempt 4's record, mark it superseded.

### 2.2 Stop destroying the evidence before it is read

**Problem.** `cluster_backend.execute`'s step list ends `eval-export` →
`kind delete cluster`, and the `finally` block deletes again on every exit
path. The end-of-run capture lost that race and hours 17–24 are gone forever.

**Change.**

- Honour `POLYFORGE_EVAL_KEEP_CLUSTER=1`: skip both deletes, print the cluster
  name and the command to remove it manually. Default stays delete-on-exit so
  CI does not leak clusters.
- Set it for every soak. Delete the cluster **after** the record is generated
  and the reproduction gate passes, as an explicit manual step.
- With 2.1 landed this is belt-and-braces rather than load-bearing, which is
  the right relationship to have with a teardown race.

### 2.3 Make the run observable while it runs

**Problem.** The harness runs k6 with `capture_output=True`, so the runner log
was 0 bytes for 24 hours. `http_req_failed` was only knowable at the end — the
gate that decides the whole run was invisible for the entire run.

**Change.**

- Stream k6 stdout/stderr to `evidence_dir/k6-live.log` with `Popen` writing
  straight to a file handle rather than buffering to memory.
- Add `--out csv=evidence_dir/k6-metrics.csv` so failures carry a **timeline**,
  not just a total. Attempt 2's record explicitly could not say whether its
  36.4% failures began at minute one or accumulated late; this answers that.
- Watcher parses the CSV every 5 minutes and alerts when cumulative
  `http_req_failed` crosses **0.5%** — half the gate — so a doomed run is
  known while there is still time to stop it.

---

## Phase 3 — hypothesis repair (problems #2, #3, #4, #6)

### 3.1 SK-H1: score an observable that actually moves

**Problem.** SK-H1 scores post-fault `mean_violation` returning within
`CHAOS_TOL = 0.05` of pre-fault. The CRUD target is 150 ms × 2.5 = 375 ms and
the observed p99 is ~8 ms — roughly 47× headroom — so violation was 0.00 in
sixteen of seventeen hours. The rule could not fail.

**Change.** Do **not** touch `SLO_BASE_MS`: it mirrors
`research/jcac_sim/model.py` and changing it voids every live/sim parity claim
in the thesis.

Instead re-register SK-H1 on **p95 deviation**, which demonstrably has range
(hourly p95 moved 2.03 → 3.62 ms across attempt 4, and rose measurably in
throttle hours):

> Within 5 minutes of each injection, `crud_p95_ms` over the post-fault window
> returns to within **20%** of the pre-fault window's value.

Report `mean_violation` alongside as a secondary, explicitly noting its lack of
range so the next reader is not misled the way this one was.

**Positive control (mandatory).** In Stage B, inject a deliberate latency
fault sized to push p95 past the 20% band. **SK-H1 must be observed FAILING.**
If it cannot be made to fail, the metric is still vacuous and Phase 3 is not
done.

**The fault must be CPU starvation, NOT `tc netem` (session 39).** This
paragraph originally offered either. The netem option cannot work, and the
reason matters beyond this control: `crud_p95_ms` comes from the `latency_ms`
written in `internal/platform/replay.go`, which starts its clock immediately
before `burnCPU` and stops it immediately after. Request parse, authorisation,
queueing ahead of the handler, the telemetry write and the entire network path
fall OUTSIDE the measured window, so the metric is the duration of a CPU spin.
Network delay lands in the outside region and cannot move it by construction —
the control would have reported "does not move" and been misread as the metric
still being vacuous, when in fact the fault never reached it.
`internal/platform/replay_latency_semantics_test.go` demonstrates the gap:
250 ms injected outside the burn, 1 ms recorded. Starvation lands *inside* the
window because `burnCPU` spins against a wall-clock deadline, and `crud_read`
is one work unit at 1 ms of CPU, so a few hundred microseconds of scheduling
delay clears the band. Implemented in `scripts/stage_b_controls.sh` as two
spinners on one worker — not a node-wide throttle and not every node, because
the box shares 8 logical CPUs with the Windows-side k6 process and starving the
generator manufactures dropped iterations.

**Open, and deliberately not decided here.** The same definition means SK-H3's
hourly gate and every `crud_p95`/`crud_p99` in the committed live records
measure CPU service time rather than service latency, and attempt 4's
"server-side 2.23 ms vs k6 1032 ms" gap is partly just that. Widening the
measured window would change what every committed record means, so it needs its
own stated reading rather than a quiet edit.

### 3.2 SK-H2: give the audit stream somewhere to go

**Problem.** `PlanRunner.audit` returns immediately when `Audit` is nil.
`Audit` is assigned only under `POLYFORGE_NATS_URL`
(`cmd/operator/main.go:171`), and that variable is set nowhere — not in
`deploy/helm/`, not in `eval/`, not on any live deployment. The Postgres
outbox held 16 setup rows and no operator subjects. SK-H2 computed 0 == 0.

**Change.** Deploy NATS with JetStream into the eval namespace — a single
Deployment plus Service, `nats-server -js`, no persistence needed for a soak —
and set `POLYFORGE_NATS_URL` on the operator Deployment in the eval values.

Then add `check_audit_stream` to the harness: after the run, read
`Backbone.MessageCount` and fail the run if it is zero. An audit hypothesis
scored against an empty stream is the failure mode that must never recur.

**Positive control.** In Stage B, force at least one degraded cycle (3.3) and
assert audit records for it are **> 0** and equal the degraded-cycle count.
Until that equality has been observed holding, SK-H2 is not instrumented.

**Alternative if NATS proves fiddly:** implement `PostgresAuditPublisher`
writing to the existing `outbox` table via the admin role, reusing the
transactional-outbox pattern already in ADR 0006. More code, less infra, and
it survives without a broker.

### 3.3 SK-H1/fault design: make the planner actually stop

**Problem.** Across **seven** planner-crash injections in four attempts,
`fallback()` produced zero log lines. Fault 7's measured 26 s endpoint gap —
2.6× the 10 s plan interval — ruled out the registered "gap is shorter than the
interval" explanation. The probe showed the pod reporting **1/1 ready
throughout termination**: Kubernetes delists a deleting pod from `Endpoints`
immediately but does not close established connections, and the operator
reaches the planner over a reused HTTP connection. It kept talking to the dying
pod.

**Change.** Replace pod deletion in `scripts/chaos_inject.sh` with a scale-down
window, which is unambiguous — no endpoints *and* no process:

```bash
kubectl -n "$NS" scale deploy/polyforge-operator-planner --replicas=0
sleep "$PLANNER_OUTAGE_S"     # 60s, comfortably > the 10s plan interval
kubectl -n "$NS" scale deploy/polyforge-operator-planner --replicas=1
```

**Positive control.** The injector asserts `planner unavailable, holding last
good plan` appears in the operator log at least once per injection, and
**aborts the stage** if it does not. Seven silent no-ops across four attempts
happened because nothing ever checked.

Optionally also shorten the planner client's idle timeout, so it cannot hold a
connection across an outage even if one is somehow still served.

### 3.4 SK-H5: retire it

**Problem.** SK-H5 counted port-forward supervisor restarts. With Phase 1 the
supervisor no longer exists, and in attempt 4 the count measured the OOM cycle
plus observer contention rather than anything about the system.

**Change.** Drop SK-H5. Replace it with a descriptive **SK-H6: load
distribution** — the per-pod request share from 1.2, reported as a
distribution. That measures something real about the deployment, and it is the
property whose absence invalidated attempt 4.

---

## Phase 4 — product defects found along the way (problems #7, #8)

### 4.1 RBAC — done

The operator's ClusterRole omitted `pods` while `ensureRBAC` creates per-tenant
Roles granting `pods: get/list/watch`, so the apiserver rejected every one —
552 times in 14 h — and **no per-tenant Role has ever existed in any
deployment**. Fixed in both shipped manifests, rules extracted to
`TenantRoleRules()`, invariant pinned by
`TestOperatorClusterRoleCoversTenantRoleRules`, verified RED before GREEN.
**Complete (`908cd0d`).**

### 4.2 Policy status write contention

**Problem.** 3,701 optimistic-concurrency conflicts on `Policy` status writes
in 14 h — about one every 13 s — between the plan loop's status updates and the
policy controller's. controller-runtime retries them, so nothing surfaces, but
it is real contention and it inflates apiserver load during exactly the windows
the throttle fault is meant to measure.

**Change.** Move the plan runner's status writes to **server-side apply** with
its own field manager (`polyforge-plan-runner`), so the two writers own
disjoint field sets and stop colliding. Where SSA is impractical, wrap the
status update in `retry.RetryOnConflict` with a bounded backoff instead of
relying on the reconciler's outer retry.

**Acceptance.** Conflict rate over a 1-hour rehearsal drops below 60/h — a 10×
improvement — measured from the operator log.

---

## Phase 5 — protocol and tooling (problems #10–#17)

| # | Fix | Detail |
|---|---|---|
| 5.1 | **No local compute during a run** | Harness writes `.soak-running` at load start and removes it at teardown. A `pre-commit` hook and a `go test` wrapper refuse to run while it exists. This is the rule attempt 4 did not have, and a marker file is enforceable where a note in a doc is not. |
| 5.2 | **Fix the watcher's exit-code capture** | `rc=$?` after a command substitution reads `date`'s status, not the script's. Capture into a variable on the line immediately following the call. |
| 5.3 | **Probe cadence and content** | Delivery probe every 30 s, not 5 min — attempt 4's probe reported healthy for 100 minutes while pods died every 2 minutes. Add first-class alerts on **any** `OOMKilled`, on pod restarts > 0, and on the load-distribution check from 1.2. |
| 5.4 | **Evidence copied continuously** | Injector logs and probe logs sync into `eval/results/<run>_evidence/` every 30 min, not snapshotted once. Attempt 4's snapshot was taken at hour 17 and held only 5 of 8 faults. |
| 5.5 | **Analysis discipline** | No trend claims from two data points — attempt 4 produced two wrong readings that way ("OOM has not reproduced", "restarts are observer contention"). Register predictions before measurements, as `MIDRUN_FINDINGS.md` did successfully for fault 7. |

---

## Phase 6 — the validation ladder (the part that prevents attempt 6)

Each stage has explicit pass criteria. **Failing a stage means fix and repeat
that stage.** Never proceed on "it will probably be fine at longer duration" —
that assumption is what produced four failed sittings.

### Stage A — 10-minute smoke (~15 min per iteration)

| Check | Pass criterion |
|---|---|
| Delivery | `http_req_failed` = 0%, `dropped_iterations` = 0 (needs `preAllocatedVUs` >= 400: at 150 the generator itself dropped 26 iterations at t+3161s, session 39) |
| **Load distribution** | no pod > 25% of requests; ≥ 80% of replicas served traffic |
| Buckets | `eval-export --bucket-seconds=60` returns 10 non-empty buckets |
| Audit | `MessageCount` > 0 |
| k6 live log | non-empty, CSV parses |
| OOM kills | 0 |

### Stage B — 60-minute rehearsal, all fault types, **positive controls**

Compress all four fault kinds into an hour. This stage exists to prove each
hypothesis **can fail**, not to pass them.

| Positive control | Must be observed |
|---|---|
| Planner scale-down | ≥ 1 `planner unavailable` line per injection |
| Audit continuity | **Restated (session 39).** The old rule compared 14 degraded cycles against 2,888 records and passed: the operator audits every cycle for every tenant, so a 60-minute run puts 8 x 360 = 2,880 records on the stream whether or not anything degrades. Two sides differing by two orders of magnitude under healthy operation is not a continuity test. Now: during a planner outage every cycle falls back, so the stream's growth ACROSS the outage window must equal **degraded cycles x tenants** (three cycles of slack above, none below), both > 0. Like compared with like, and it fails when records are dropped. |
| Latency fault (CPU starvation, **not** netem — see 3.1) | **SK-H1 FAILS** — p95 deviation exceeds 20% |
| Throttle | **RETIRE — cannot bite (session 39).** Measured with `scripts/diagnose_throttle.sh`: the FlowSchema *does* match (40/40 impersonated operator-identity requests dispatched through it), but the level carries **3 concurrency seats** and has **never rejected a single request**. `nominalConcurrencyShares: 1` is already the smallest APF accepts, so the limit cannot be lowered to meet the operator's concurrency, which never approaches 3 in flight. This is not a selector bug and not a threshold to lower: the fault has no reachable mechanism on this apiserver. Any hypothesis scored on it is untested. |
| Load distribution | holds under fault, no re-pinning |

**If a hypothesis cannot be made to fail here, it is not instrumented, and the
24 h run must not start.** This single gate would have caught SK-H1, SK-H2 and
SK-H5 before any of the four attempts.

### Stage C — 4-hour endurance

| Check | Pass criterion |
|---|---|
| OOM kills | **0** |
| Pod restarts | 0 |
| Peak per-pod RSS | < 60% of the 256 Mi limit |
| Container memory | flat after hour 1 (no monotonic climb) |
| Delivery | `http_req_failed` < 0.1% |
| Hour buckets | 4 present, p95 stable |

Four hours is chosen deliberately: attempt 3 hit its OOM cascade at 12 h 45 m
and attempt 4 at ~17 h 30 m, but with load spread across sixteen replicas the
per-pod fill rate should be ~16× slower, so any residual leak shows as a
*trend* within four hours even if it would not reach the limit.

### Stage D — the 24-hour sitting

Entered only when A, B and C have all passed on the **same** commit. Nothing is
changed between C passing and D starting — a fix after C invalidates C.

---

## Phase 7 — pre-registration and execution

1. **`PREREG_LIVE_SOAK_V3.md`**, committed and pushed before Stage D. Carries:
   SK-H1 re-registered on p95 deviation; SK-H2 unchanged but now instrumented;
   SK-H3 unchanged; SK-H4 unchanged; SK-H5 dropped, SK-H6 (load distribution)
   added; the Stage A–C gate results quoted as preconditions; and the
   no-local-compute protocol rule.
2. **Disclose everything from attempt 4**, including the observer contention
   and both corrected attributions. The V3 prereg inherits attempt 4's record
   the way V2 inherited attempt 2's.
3. **Run Stage D.** Machine awake, power settings recorded first
   (`docs/WP14_SOAK_RESTORE.md`), no local compute for the duration.
4. **Generate the record, run `reproduce.py`** — the gate must stay green at
   28/28.

---

## Effort and ordering

| Phase | Work | Depends on |
|---|---|---|
| 1 load path | ~4 h | — |
| 2 instrumentation | ~4 h | — (parallel with 1) |
| 3 hypothesis repair | ~5 h | 2.1 for bucket scoring |
| 4.2 SSA contention | ~2 h | independent |
| 5 protocol | ~2 h | — |
| 6 A→B→C | ~1 h + ~1.5 h + ~4.5 h wall, plus fix iterations | 1–5 |
| 7 prereg + 24 h | 24 h wall | 6 green |

Roughly **two focused days of engineering**, then a day of laddered validation,
then the sitting. Longer than "just re-run it" — and it is the only version of
the plan where the sitting is likely to be the last one.

## What this cannot promise

Attempt 5 could still fail on something none of the four attempts has
exhibited. What it will not do is fail on any of the seventeen problems already
found, because each has a gate that fires in minutes rather than at hour 24 —
and no hypothesis reaches the sitting without having been watched to fail on
demand first.
