# NOT EVIDENCE — the aborted first start of the B1′ sitting (2026-09-15)

This directory holds the evidence of the ONE run executed under the
pre-amendment load-distribution guard (`jcac-calibrated`, `ai_cacheable`,
rep 0, started 10:57 UTC on `i-08855845b74db1df3`) and the runner log of
that start. The run was recorded *failed* by `check_load_distribution`
("only 17 of 23 replicas served any traffic"); it produced no export and no
metric. Nine of its 23 pods lived one 30-s sample — replicas the controller
created and shed inside a control step — which is what
`PREREG_WAVE4_CALIBRATED.md` Amendment 1 describes and commit `1d71f4f`
fixes. The sitting restarted from zero at 11:22 UTC under the amended guard;
`../runs/` holds the scored runs. Nothing here enters any record.
