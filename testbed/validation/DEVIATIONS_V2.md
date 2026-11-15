# WP11b: deviations, as they happened

Protocol: `testbed/hvpa/after_wp11.py` (WP11b), predictions committed in 175ff38 before any run.

| when (local) | what | effect on the runs |
|---|---|---|
| 2026-11-15 09:24 | The session that had started the chain ended; every process it started was terminated with it (run_cells, k6, the chain, the CPU queue, Docker Desktop), and the laptop then slept until 13:18. 6 of 60 runs were complete and valid; cell 10 run 0 was cut off. | The cut-off run has no `run.json` and is not a measurement; its partial files were moved to `testbed/out/interrupted/validation_v2_cell10_run0_killed_20261115T0924` and the run is redone in its place in the order. |
| 2026-11-15 13:30 | Docker Desktop and the kind node restarted; every pod came back after one restart (Postgres's emptyDir and the tenants' keys survived; the knobs read back at default and are set by each run's posture step, as always). | None by design: each run restarts the gateway, sets and reads back its posture and checks its own validity. Runs 1-6 were on the cluster before the restart, the rest after it. |
| 2026-11-15 13:37 | The chain relaunched outside the session's job (`tools/detach.py`). It resumed at the runs without regenerating the committed cells, request tables or predictions (`wp11b_prepared`, bc83e16). | None: the same committed inputs and predictions, the same seeded run order. |
