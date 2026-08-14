# WP14 soak — recovery note for a fresh Claude session

Deliberately OUTSIDE the repo (sits next to the injector logs in Temp) so it
pollutes nothing and survives this session ending.

## The run

| | |
|---|---|
| Experiment | `eval/experiments/live_soak.yaml` (24 h, 8640 steps) |
| Started | 2026-08-13, k6 load window **T0 = 16:49:45 local** (= 10:49:45 UTC) |
| Finishes | **~16:50 local on 2026-08-14** |
| Prereg | `research/analysis/PREREG_LIVE_SOAK.md` |
| Attempt | 2 (attempt 1 voided — injector could not see k6 on Windows) |

## Fault schedule (local time)

| # | Fault | Due | Status as of last known check |
|---|---|---|---|
| 1 | planner crash | 18:50 | FIRED 18:49:53, recovered unaided |
| 2 | apiserver throttle | 21:50 | pending |
| 3 | planner crash | 00:50 | pending |
| 4 | apiserver throttle | 03:50 | pending |
| 5 | planner crash | 06:50 | pending |
| 6 | apiserver throttle | 09:50 | pending |
| 7 | planner crash | 12:50 | pending |
| 8 | apiserver throttle | 15:50 | pending |

Injector logs (they timestamp in **UTC**, six hours behind local):
`C:/Users/DARKR/AppData/Local/Temp/wp14_inject_{7200,28800,50400,72000}.log`

## Is it still alive?

```bash
docker ps --format '{{.Names}}' | grep polyforge-eval          # want 4
kubectl get pods -n polyforge --no-headers | grep -c Running   # want 19
tasklist //FI "IMAGENAME eq python.exe" | grep -i python       # runner
tasklist //FI "IMAGENAME eq k6.exe" | grep -i k6               # load
grep -h "=== FAULT" /c/Users/DARKR/AppData/Local/Temp/wp14_inject_*.log
```

The soak, k6 and the four injectors are **detached OS processes**. They do not
depend on Claude Code, and they keep running through a usage limit, a closed
terminal, or a closed session. Only the watcher and the reporting stop.

## Restarting the watcher in a new session

The watcher script lived in the old session's scratchpad and will be gone.
Re-create it or just poll with the commands above. It was read-only: a
60-second loop emitting a heartbeat every 30 min plus immediate events on
fault / death / pod loss / memory past 7.0 GiB, self-exiting when the runner
disappears or at T0+25 h.

## When the run finishes

1. `cd eval && python scripts/export_metrics_csv.py experiments/live_soak.yaml`
2. Write `research/analysis/analysis_live_soak.py` → `RESULTS_LIVE_SOAK.md`
   via `stats.record_path()`
3. Score **SK-H1** (every fault recovers unaided), **SK-H2** (audit continuity),
   **SK-H3** (hourly `crud_p95` ≤ the committed p99 of 8.0072 ms),
   **SK-H4** (zero invalid-run signatures)
4. Register in `scripts/reproduce.py`, run the gate (currently 25/25)
5. Restore power settings — see `docs/WP14_SOAK_RESTORE.md`
   (`powercfg /change standby-timeout-ac 300`, same for `-dc`)

## If the machine slept mid-run

The run is **VOID** per the prereg. Report it as void; do not splice two
halves. Check by comparing elapsed wall time against k6's progress.
