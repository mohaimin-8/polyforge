# WP14 soak — fault journal (live verification log)

Written outside the repo, next to the injector logs, so it survives a lost
session. This is the *verified* record: what I checked against the live
cluster at the moment of each fault, beyond what the injector logs alone say.

Feeds `RESULTS_LIVE_SOAK.md` (SK-H1) when the run ends.

**Run:** attempt 2, T0 = 2026-08-13 16:49:45 local, 24 h, finishes ~16:50 on
2026-08-14. Attempt 1 voided (injector could not see k6 on Windows).

---

## Fault 1 — planner crash — RECOVERED

| | |
|---|---|
| Due / fired | 18:50 / **18:49:53** local |
| Pod killed | `polyforge-operator-planner-697c74cf95-ggx4p` |
| Replacement | `-4dvgh`, Running, **0 restarts** |
| Operator intervention | none |
| Verified by | `kubectl get pods` — replacement age 6m43s, restarts 0 |
| Note | watcher swallowed this event (its own bug, since fixed); fault itself was clean |

## Fault 2 — apiserver throttle — RECOVERED

| | |
|---|---|
| Due / fired | 21:50 / **21:49:51** local |
| Mechanism | APF `PriorityLevelConfiguration` + `FlowSchema` on the operator's ServiceAccount, 60 s |
| During | operator alive, **0 restarts**; `Reconciler error` on t00, t02, t04, t07 — retrying, not dying |
| Cleared | 21:50:53 |
| After | `pf-chaos-throttle` objects NotFound; **all 8 policies `Applied=True`** |
| Verified by | flowschema deleted, policy conditions, operator restart count |
| Significance | harder case than a crash — the controller had to hold state and reconcile rather than restart into a clean slate |

## Fault 3 — planner crash — RECOVERED

| | |
|---|---|
| Due / fired | 00:50 / **00:49:51** local |
| Pod killed | `polyforge-operator-planner-697c74cf95-4dvgh` |
| Replacement | `-l6szj`, Running, **0 restarts**, age 68 s |
| Policies | 8/8 `Applied=True` |
| Load during recovery | **3,547 requests in 30 s** — traffic never stopped |
| Verified by | pod age/restarts, policy conditions, control-plane request count |

## Fault 4 — apiserver throttle — RECOVERED

| | |
|---|---|
| Due / fired | 03:50 / **03:49:50** local |
| Held / cleared | 60 s, cleared **03:50:51** |
| Operator | Running, **0 restarts** — never died |
| Planner | Running, 0 restarts |
| Reconciler errors in window | 9 — retried rather than crashed |
| After | throttle objects cleared |
| Verified by | flowschema absence, operator/planner restart counts, error count in a 60 s window |
| Note | second occurrence of this fault type; same signature as fault 2 |

## Fault 5 — planner crash — RECOVERED

| | |
|---|---|
| Due / fired | 06:50 / **06:49:53** local |
| Pod killed | `polyforge-operator-planner-697c74cf95-l6szj` |
| Replacement | `-5qnb8`, Running, **0 restarts**, age 68 s |
| Policies | 8/8 `Applied=True` |
| Load during recovery | **6,392 requests in 30 s** — traffic never stopped |
| Verified by | pod age/restarts, policy conditions, control-plane request count |
| Note | third planner crash; replacement came up in the same 68 s as fault 3, six hours apart. Higher load than fault 3 (6,392 vs 3,547 per 30 s) — recovered during a burst phase, a slightly harder case |

## Fault 6 — apiserver throttle — RECOVERED

| | |
|---|---|
| Due / fired | 09:50 / **09:49:51** local |
| Held / cleared | 60 s, cleared **09:50:52** |
| Errors DURING the window | **0** |
| Errors immediately AFTER | **8, in a 2-second burst** (03:51:33–34 UTC) |
| Operator / planner | Running, **0 restarts** |
| Policies | 8/8 `Applied=True` |
| Throttle objects | cleared |

**Different signature from faults 2 and 4, and worth recording.** Those hit
while the operator was mid-reconcile, so it failed loudly *during* the
window. This one landed in a quiet moment — nothing in flight, nothing to
fail — and the errors appeared when the next reconcile cycle woke up against
the policy being torn down. Same outcome (retry, no restart, all policies
re-applied), different timing.

**Observer correction:** the first check reported 0 reconciler errors and I
flagged the bite as unconfirmed rather than recording a recovery I had not
verified. That was a sampling-window artefact — a 60 s log window that ended
before the errors appeared. A 180 s window found them. The flag was correct;
the initial reading was too narrow.

## Fault 7 — planner crash — RECOVERED

| | |
|---|---|
| Due / fired | 12:50 / **12:49:53** local |
| Pod killed | `polyforge-operator-planner-697c74cf95-5qnb8` |
| Replacement | `-52hp2`, Running, **0 restarts**, age 38 s |
| Policies | 8/8 `Applied=True` |
| k6 | alive since 16:49:45 — never stopped |
| Load across all 16 pods | 336 req/60 s (workload in its trough phase) |
| Verified by | pod age/restarts, policy conditions, k6 process, summed request counts |

**MEASUREMENT CORRECTION — applies to every earlier fault entry.** The
per-fault "load during recovery" figures (12,365 / 3,547 / 6,392) came from
`kubectl logs deploy/polyforge-control-plane`, which samples **one pod out of
sixteen** and does not pick the same one twice. They are therefore not
comparable across checks and must not be quoted as a trend. This fault's
first reading was 9 req/30 s, which looked like load had stopped; summing
across all 16 pods gave 336 req/60 s, and k6 had been alive throughout.

The claim those numbers supported — *traffic never stopped during recovery* —
still holds, but it rests on k6 liveness and the summed count, not on the
per-fault single-pod figures. Corrected here rather than left to be
discovered.

## Fault 8 — apiserver throttle — INJECTED, NO PERTURBATION OBSERVED

| | |
|---|---|
| Due / fired | 15:50 / **15:49:51** local |
| Held / cleared | 60 s, cleared **15:50:52** (injector logged both) |
| Reconciler errors, 180 s window | **0** |
| Reconciler errors, **300 s** window | **0** |
| Operator / planner | Running, **0 restarts** |
| Policies | 8/8 `Applied=True` |

**Not scored as a recovery.** The throttle was applied and removed, but
nothing in the operator reacted to it — zero errors in a window five times
wider than the one that caught fault 6's delayed burst. You cannot recover
from something that did not perturb you.

Two readings, not distinguishable from the evidence available:
1. **Benign** — the operator had no reconcile work in that window; the
   throttle only bites when it actually calls the API.
2. **Weaker** — the APF policy did not match this time, so the operator was
   never starved. B2 marked this injector *best-effort* for exactly this
   reason.

**Honest scoring: 7 observed perturbation-and-recovery events out of 8
injections.** That still clears the prereg's >=2-of-each requirement:
4 planner crashes and 3 throttles with observed effect, all recovered
without intervention.


---

## Run health across the soak

| Time | Elapsed | Pods | Memory | Note |
|---|---|---|---|---|
| 16:57 | 0h07m | 19/19 | 1.85 GiB | start |
| 18:25 | 1h35m | 19/19 | 2.36 GiB | cache warming |
| 18:57 | 2h08m | 19/19 | 1.46 GiB | drop = fault 1 pod replacement |
| 21:00 | 4h10m | 19/19 | 1.49 GiB | |
| 22:32 | 5h42m | 19/19 | 1.54 GiB | |
| 00:14 | 7h24m | 19/19 | 1.59 GiB | after the terminal session exited — run unaffected |
| 00:51 | 8h01m | 19/19 | 1.64 GiB | fault 3 |
| 01:47 | 8h57m | 19/19 | 1.63 GiB | |
| 03:50 | 11h00m | 19/19 | 1.64 GiB | fault 4 |
| 04:51 | 12h01m | 19/19 | 1.65 GiB | 12 h minimum cleared |
| 06:51 | 14h00m | 19/19 | 1.70 GiB | fault 5 |
| 08:56 | 16h06m | 19/19 | 1.68 GiB | |
| 09:51 | 17h01m | 19/19 | 1.68 GiB | fault 6 |
| 11:58 | 19h08m | 19/19 | 1.69 GiB | |
| 12:50 | 20h00m | 19/19 | 1.75 GiB | fault 7 |
| 14:29 | 21h39m | 19/19 | 1.73 GiB | |
| 15:51 | 23h01m | 19/19 | 1.74 GiB | fault 8 |

Pods never dropped below 19 outside injected windows. Memory range
1.46–2.36 GiB against a 9.71 GiB ceiling — no leak trajectory.

## Interruptions to REPORTING (not to the run)

- 23:34 → 00:14: the terminal session exited, watcher died. Runner, k6,
  containers and all four injectors survived; no fault fell in the gap.
