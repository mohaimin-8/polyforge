# Wave 3 live runbook — the deferred over-the-wire campaigns, step by step

Everything in Wave 3 needs a Docker-capable host (a GitHub Codespace built
from `.devcontainer/`, or any Linux box with kind/kubectl/helm/k6 — the
session-19 machine has no local Docker). The protocols are frozen and pushed
(`PREREG_WIRE_ATTACK.md`, `PREREG_LIVE_CHAOS_P99.md`); the analysis/attack code
is desk-tested. This runbook is the ordered execution once a cluster host is
available, so the live session is push-button rather than improvised. All of
it reuses the session-19 Phase 7 harness that already ran green.

## 0. Bring up the host (session-19 procedure, recorded for reuse)

```
gh codespace ssh -c <name>            # or ssh to the VM
# in the repo, login shell so GITHUB_TOKEN exists:
bash -lc 'cd /workspaces/polyforge && git pull'
(cd eval && python -c "from harness import cluster_backend as c; print(c.preflight() or 'tools ok')")
```

Session-19 gotchas that cost time, pre-solved here: `gh codespace cp` is broken
on Windows → stream files with `base64` over ssh; nested quotes are stripped
through `gh codespace ssh` from PowerShell → ship base64-decoded scripts run
under `bash -l`; a Codespace clone can lose its `.git` → fresh clone via
`x-access-token:$GITHUB_TOKEN`.

## 1. Over-the-wire cache side-channel (`PREREG_WIRE_ATTACK.md`)

The attack client is `research/security/wire_attack.py` (stdlib only; verified
offline with `--selftest`). It runs **once per cache posture**.

**One piece of setup code is required first (be honest about it):** the gateway
today is *unconditionally per-tenant* — `SemanticCache.Lookup` calls
`index.Search(tenantID, …)`, so isolation is not a toggle, it is the design.
That means:

- **WA-H1 (the per-tenant defense, the load-bearing claim) needs no code** —
  the deployed gateway already is the defended posture; run the client against
  it as-is.
- **WA-H2 (the SHARED / insecure baseline) needs a deliberate insecure mode
  added** before it can be measured: a build/chart flag
  (e.g. `POLYFORGE_CACHE_SHARED=1`) that makes `Lookup`/`Store` use one fixed
  partition key instead of `tenantID`. This is the honest shape of the attack —
  you must intentionally *break* isolation to demonstrate the leak the design
  prevents. It is ~20 lines in `cache.go` + a chart value; it does not exist
  yet and is the first task of the live session (wire it first).

```
# provision the two tenants + keys (both postures)
VICTIM_KEY=$(curl -s -XPOST $BASE/v1/tenants/victim/api-keys   -d '{"name":"k","scope":"full"}' | jq -r .key)
ATTACKER_KEY=$(curl -s -XPOST $BASE/v1/tenants/attacker/api-keys -d '{"name":"k","scope":"full"}' | jq -r .key)

# 1a. PER-TENANT posture (deployed default) — measures WA-H1 now.
GATEWAY_URL=$BASE VICTIM_KEY=$VICTIM_KEY ATTACKER_KEY=$ATTACKER_KEY \
  WIRE_POSTURE=per-tenant python research/security/wire_attack.py

# 1b. SHARED posture — only after the insecure mode above is wired; redeploy
#     the gateway with it on, re-mint keys, run again with WIRE_POSTURE=shared.
```

Reads: WA-H1 (per-tenant AUC ≈ chance 0.50, the load-bearing defense claim),
WA-H2 (shared AUC over the wire — no magnitude pre-committed; real RTT jitter
is expected to sit below the sim's 0.88), WA-H3 (measured hit/miss timing gap
vs the sim's 20/800 ms). The client writes
`eval/results/security/RESULTS_WIRE_ATTACK.md`.

## 2. Live chaos + p99 (`PREREG_LIVE_CHAOS_P99.md`)

Reuses the two cells that already validated live in session 19 (`crud_bursty`,
`ai_cacheable`, jcac arm) via `scripts/phase7_kind_run.sh`.

```
bash scripts/phase7_kind_run.sh --jcac-smoke     # confirm the arm still comes up green
```

Then inject the two faults during a fresh jcac run, at the pre-declared offsets:

- **Planner crash mid-burst (T = 8 min):** `kubectl delete pod -l app=polyforge-planner`
  — the Deployment reschedules; the operator holds last-known-good. This is the
  live analogue of the sim's `chaos_planner_outage` (which already passed
  CH-H1/CH-H2 in `RESULTS_CHAOS_SIM.md`).
- **apiserver throttle (T = 14 min):** apply a transient APF / admission delay
  for 60 s so the reconcile loop is starved.

`eval-export` now emits `crud_p99_ms` / `ai_p99_ms` beside p95 (landed and
unit-tested at the desk this wave). Read both from the same run for LC-H1
(returns to within 0.05 mean-violation of pre-fault within 5 min) and P99-H1
(live p95 vs p99 per family per arm; p99 stays a live-only number — never
back-fitted into a sim table). Write `RESULTS_LIVE_CHAOS_P99.md`.

## 3. After the run

- Pull the duckdb / export JSON down via `base64` over ssh (not `gh codespace cp`).
- Commit the RESULTS_*.md and run-level CSVs; the raw duckdbs stay gitignored
  (Zenodo bundle carries them).
- **Stop the Codespace** to conserve quota (session-19 practice).
- Paste each frozen prereg into OSF and record the DOI in `OSF_REGISTRATION.md`.

## Not in Wave 3 (Wave 4 live, separately gated)

The three-knob *live* data plane (real cache/tier levers, not just the replica
axis the session-19 ordinal check exercised) and GPU rental are Wave 4 and need
their own pre-registration and a paid/GPU host; they are not part of this
runbook. The planning-cell scaling half of Wave 4 is local and already
measured (`PLANNER_CELLS.md`, `PLANNER_CELLS_DEALIAS.md`).
