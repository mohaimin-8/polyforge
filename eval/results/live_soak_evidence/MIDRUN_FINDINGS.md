# Attempt 4 — findings from a mid-run inspection at T+14 h

Written 2026-08-19 ~06:10 local (T+14 h 20 m), while the soak was still
running. Nothing here changed the running configuration: every command was a
read (`kubectl get/logs/exec psql SELECT`, `grep` over source). The run
continued untouched, which is the only way these observations stay admissible.

Recorded now rather than at hour 24 because two of them affect what the run
can be *scored* for, and a reader who meets them for the first time in
`RESULTS_LIVE_SOAK_V2.md` deserves to see when they were found.

## 1. SK-H2 has no evidence source in this deployment (decisive)

`PREREG_LIVE_SOAK_V2.md` freezes SK-H2 as: *"count of degraded cycles equals
count of audit records for them, exactly. Any gap fails it."*

The operator publishes plan audit entries through `PlanRunner.audit`
(`internal/operator/controllers/plan_runner.go:405`), which returns
immediately when `p.Audit == nil`. `Audit` is assigned in exactly one place,
`cmd/operator/main.go:178`, and only inside `if natsURL :=
os.Getenv("POLYFORGE_NATS_URL"); natsURL != ""`.

`POLYFORGE_NATS_URL` is set **nowhere**: not in `deploy/helm/`, not in
`eval/`, and not on any live deployment in the namespace (checked directly
against the running cluster). There is no NATS in the chart at all.

Cross-checked against the other candidate sink — the Postgres transactional
outbox — which holds 16 rows for the whole run, all from setup:

    apikey.created       8
    tenant.provisioned   8

Zero `polyforge.audit.operator.plan.*` subjects. So the audit stream SK-H2
measures does not exist in this configuration, and did not exist in attempts
1–3 either. `RESULTS_LIVE_SOAK.md` called SK-H2 "UNSCOREABLE — no metrics
exist"; that was true but incidental. It was unscoreable for this independent
and more fundamental reason as well.

**Consequence, stated before the numbers are in.** Combined with finding 2
below, the frozen rule will evaluate `0 degraded cycles == 0 audit records`
and return a **vacuous pass**. That must not be reported as "SK-H2 passed".
It is a hypothesis whose instrument was never connected, and the record will
say exactly that.

## 2. Zero degraded cycles observed across five planner kills (unresolved)

`fallback()` logs `planner unavailable, holding last good plan` whenever a
plan call fails. Across the complete operator log — 4,269 lines spanning
T0 (09:50:03Z) to the time of writing, no rotation loss — that string appears
**zero** times, as does `plan cycle failed`.

This is not what the fault design predicts. The planner runs at **1 replica**
(confirmed), `chaos_inject.sh` deletes its pod, replacement took ~69 s at
fault 5, and `DefaultPlanInterval` is 10 s with `Log` wired at
`cmd/operator/main.go:152`. That window should have produced roughly seven
fallback warnings per fault, and produced none in five.

The plan loop is demonstrably alive: all 8 policies report
`lastPlanSource=planner` with `lastPlanTime` 6 s old at the time of checking.

**One of the two candidate explanations is now eliminated.** The suspicion
that the deployed `polyforge/operator:dev` predates this working tree is
**false**. containerd in the kind node reports the running image as built
`2026-08-13T07:26:01.907970007Z`; the host image carries the identical
timestamp to the nanosecond, so they are one build. Extracting that image's
filesystem (a container created and removed without ever being started —
the cluster was not touched) confirms the binary contains every relevant
string:

    planner unavailable, holding last good plan   PRESENT
    plan cycle failed                             PRESENT
    JCAC plan loop enabled                        PRESENT
    POLYFORGE_NATS_URL                            PRESENT
    audit publish                                 PRESENT

So the fallback path exists in the running binary and simply never executed.

**What remains** is that the plan calls genuinely did not fail — which
requires the planner's *endpoint* gap to be shorter than the 10 s plan
interval on essentially every fault. That is plausible: `kubectl delete pod`
lets the ReplicaSet schedule a replacement immediately, the image is already
resident on the node, and at fault 5 the new pod was `Running` 69 s after
injection — an upper bound on the outage, not a measurement of it. The
Service may have carried a ready endpoint again far sooner.

**This is measurable without disturbing the run.** At fault 7 (planner crash,
11:50 local) the `polyforge-operator-planner` Endpoints object will be polled
once a second through the injection window to record how long it holds zero
ready addresses. A gap materially under 10 s explains the missing fallbacks
and makes the four planner-crash occurrences much weaker tests than the
prereg assumed; a gap well over 10 s means something else is wrong and the
fallback path is not being reached when it should be. Both outcomes are
recorded either way. Registering the observation here, before the fault, so
the interpretation is fixed in advance rather than chosen once the number is
known.

## 3. Two live defects visible in the operator log (independent of the soak)

Message breakdown of the same 4,269 lines — 14 info, 4,253 error:

| count | error |
|---:|---|
| 3,701 | `Operation cannot be fulfilled on policies.polyforge.io "tNN": the object has been modified` |
| 552 | `roles.rbac.authorization.k8s.io "pf-tenant-tNN" is forbidden … attempting to grant RBAC permissions not currently held` |

**3,701 optimistic-concurrency conflicts** on `Policy` status writes — about
one every 13 seconds, sustained for 14 h. controller-runtime retries them, so
every health signal the watcher tracks stays green and the contention is
invisible from outside. It is still a real write-contention pathology between
the plan loop's status updates and the policy controller's own.

**552 RBAC privilege-escalation denials.** The operator tries to create a
per-tenant Role granting `pods: get/list/watch`, but its own ServiceAccount
does not hold those verbs, so the apiserver blocks the grant — Kubernetes
forbids escalating beyond your own permissions. The missing rule is in the
operator chart's RBAC. This has never worked in any run; per-tenant Roles
have never been created. It is a shipped-chart defect, not something the soak
caused, and it is not covered by any current test.

## Disposition

The run continues to 24 h unmodified. Fixing any of this mid-run would mean
an unregistered amendment and, for the RBAC and NATS items, an operator
restart — operator intervention, which is precisely what SK-H1 measures the
absence of. Findings 1 and 3 become work items *after* the soak completes;
finding 2 gets a targeted post-run test.
