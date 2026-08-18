# WP14 attempt 3 — evidence (2026-08-17)

**Outcome: STOPPED EARLY at ~16 h 30 m of a 24 h target**, at the operator's
request (laptop needed). Not void — it passed the prereg's 12 h minimum at
12:13 — but **no campaign metrics exist**, because the harness runs
`eval-export` only after k6 completes its full window. Killing k6 early means
no export, so SK-H2 / SK-H3 / SK-H4 are unscoreable, the same outcome as
attempt 2 by a different route.

What this attempt *did* establish is operational, and it is substantial.

## Faults: 5 of 8 fired, all 5 recovered without intervention

| # | Fault | Fired (local) | Outcome |
|---|---|---|---|
| 1 | planner crash | 02:13:03 | replaced, 0 restarts, 8/8 policies re-applied |
| 2 | apiserver throttle | 05:13:03 | 24 reconciler errors, 0 restarts, recovered |
| 3 | planner crash | 08:13:03 | replaced in 34 s, 0 restarts |
| 4 | apiserver throttle | 11:13:03 | 24 reconciler errors, 0 restarts, recovered |
| 5 | planner crash | 14:13:03 | replaced in 50 s, 0 restarts |
| 6–8 | — | not reached | run stopped at 16 h 30 m |

## The port-forward supervisor was vindicated by an unplanned failure

From `port_forward.log`:

```
00:12:58  forward started, endpoints=10.244.2.6 10.244.3.6   (2 endpoints)
12:58:21  RESTART #1 (process exited)                        (16 endpoints)
13:01:29  RESTART #2 (health probe failed)
```

**The port-forward died unprompted at 12 h 45 m into the run.** That is the
exact failure hypothesised for attempt 2's 36.4% delivery collapse — occurring
naturally this time, and repaired within one 15 s poll. Under attempt 2's
unsupervised code it would have black-holed traffic for the remaining
11 hours. Delivery probes show `200` continuously across both events.

Note the endpoint counts: the forward attaches to **2** endpoints while the
deployment is still scaling toward 16. Observed in all three runs (both smoke
tests and this one).

## NEW FINDING: control-plane OOM crash-loop under sustained load

| | |
|---|---|
| Container memory limit | **256 Mi** (`container_limits.txt`) |
| Total pod restarts by 16 h 30 m | **56** |
| Cause | `OOMKilled`, exit 137 (`oom_detail.txt`) |
| Rate | steady ~0.5/min from about hour 7 onward |
| Node `MemoryPressure` | False — per-container limits, not node starvation |

Pods grow past 256 Mi under sustained load, are OOM-killed, restart, and grow
again. **A 135-step (22 min) run cannot reach this; a 16 h run does.** This is
`PREREG_LIVE_SOAK` §Contingencies' *"instability surfacing mid-soak IS the
finding"* case, and the clearest argument yet for why the duration matters.

**The fleet absorbed it.** With 16 replicas, a measured window showed
13,957 × 202 + 336 × 200 and **zero errors**, 16/16 pods ready. Errors were
bursty (one window showed 152 × 500 ≈ 0.83%, the next showed zero), never
sustained. So there are two results here at once: a real resource defect, and
graceful degradation around it.

## Observer errors during this run, recorded rather than hidden

1. **Memory readings were wrong for ~15 h.** The watcher parsed `docker stats`
   with `awk -F'MiB'`, silently mis-reading any container reporting in GiB.
   Real usage was 5.6 GiB while reports said 1.7, and the 7.0 GiB warning
   could never have fired. Fixed at 15:00.
2. **The delivery probe's alert could never fire.** Counters were incremented
   inside a command substitution — a subshell — so `probe_fail_streak` never
   advanced in the parent. The early-warning mechanism built specifically to
   catch a doomed run was itself broken. Fixed at 01:15 and verified against a
   dead port.
3. **A stale watcher survived `TaskStop`** and wrote into `delivery_probe.log`
   for 13 h. Its entries are the `failed=0/1` series and must be filtered when
   scoring.
4. **Over-read a two-point trend.** Called the OOM rate "accelerating sharply"
   from rising *cumulative* counts (the increments were steady), and the error
   rate "rising" from two samples before a third showed zero.

## Files

| File | Contents |
|---|---|
| `port_forward.log` | supervisor start + 2 unplanned restarts |
| `delivery_probe.log` | 5-min delivery probes (filter the stale `0/1` series) |
| `oom_detail.txt` | per-pod restart counts, OOM reasons, timestamps |
| `container_limits.txt` | the 256 Mi limit |
| `final_pods.txt`, `final_deployments.txt`, `final_policies.txt` | cluster state at stop |
| `final_docker_stats.txt` | per-container memory at stop |
| `wp14_inject_*.log` | the four injector logs (UTC timestamps; local = UTC+6) |

## For attempt 4

The 24 h target stands. Before re-running, decide what to do about the OOM
finding: raising the 256 Mi limit changes a factor and needs its own prereg
amendment, while leaving it means attempt 4 reproduces the crash-loop — which
may be the more interesting result. That is a judgement call for the operator,
not one to make silently.

Harness state is ready either way: the supervisor, the evidence persistence,
the VU pool sizing and the delivery probe are all committed and smoke-tested.
