# WP14 attempt 2 — preserved evidence (session 38, 2026-08-13/14)

Raw session-side evidence from the second soak sitting, moved into the repo
because the first home for these files — `%LOCALAPPDATA%\Temp` — is exactly
the kind of self-cleaning location that made the run's own `k6-summary.json`
unrecoverable. Evidence for a scored record does not belong in a folder the
OS is allowed to empty.

| File | What it is |
|---|---|
| `FAULT_JOURNAL.md` | The live-verified fault timeline: 7 of 8 injected faults with pod-level recovery evidence (pod ages, restart counts, policy conditions, throughput) checked against the cluster at each occurrence, plus fault 8 disclosed as injected-with-no-observed-perturbation, plus the observer corrections (single-pod sampling, log-window width) |
| `RECOVERY_NOTE.md` | The cold-session recovery note written mid-run: schedule, health-check commands, what-to-do-on-finish |
| `wp14_inject_7200.log` | Injector log: fault 1 (planner crash 18:50) + fault 2 (throttle 21:50) — **UTC timestamps, local = UTC+6** |
| `wp14_inject_28800.log` | Injector log: fault 3 (crash 00:50) + fault 4 (throttle 03:50) |
| `wp14_inject_50400.log` | Injector log: fault 5 (crash 06:50) + fault 6 (throttle 09:50) |
| `wp14_inject_72000.log` | Injector log: fault 7 (crash 12:50) + fault 8 (throttle 15:50) |
| `runner_attempt2.log` | The harness runner's final report: `valid_runs: 0, failed_runs: 1` — the SK-H4 delivery-gate failure (36.4% failed requests) |

Attempt 1's runner log is not here: it was empty (stdout buffering; the
process was killed before any flush). Its story — voided at 2h45m because
`pgrep` cannot see a native Windows k6 — is recorded in the roadmap's WP14
attempt log and in commit history.

These files feed `RESULTS_LIVE_SOAK.md` (the attempt-2 failure record) and
are referenced by `docs/PUBLICATION_ROADMAP.md` §WP14. They are evidence of
a FAILED sitting and are kept per the prereg's rule that a FAIL is reported,
not discarded.
