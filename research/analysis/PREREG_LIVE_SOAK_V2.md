# Pre-registration: live CRUD-plane soak, attempt 3 (WP14, follow-up to a FAILED sitting)

Registered 2026-08-16 (session 38). Committed and **pushed before the run
starts**; the push event is the timestamp anchor. This scores new live
measurement, so R1/R2 apply in full.

This is a **re-sit under one changed factor**, the pattern
`PREREG_LIVE_SOAK.md` §Outcome handling named in advance and the same shape
as the RB-H1 follow-up (session 30). It does not re-score, edit or soften
attempt 2: `RESULTS_LIVE_SOAK.md` stands exactly as committed, with SK-H4
FAIL as its headline.

## Why a re-sit (provenance)

Attempt 2 ran its full 24 h, injected all eight frozen faults, and was then
rejected by the harness's own post-hoc validity gate:

```
k6 reported 36.4% failed requests (>1%): the load did not land, so the low
cost/violation this run would record are artifacts of traffic that never arrived
```

**Zero metrics rows were recorded**, so SK-H1 (as frozen, a metric test),
SK-H2 and SK-H3 were all unscoreable and SK-H4 failed. The failure is
recorded in `RESULTS_LIVE_SOAK.md` and its evidence preserved under
`eval/results/wp14_attempt2/`.

Attempt 1, earlier the same session, was voided at 2 h 45 m for a different
reason: `chaos_inject.sh` detected the load window with `pgrep -x k6`, which
cannot see a native `k6.exe` under Git Bash on Windows, so all four injectors
timed out while k6 ran throughout. Fixed and verified before attempt 2.

## The changed factor: the load-delivery path is made reliable end to end

**One mechanism, two concrete defects fixed.** Everything else — duration,
cell, fault schedule and offsets, hypotheses, margins, thresholds — carries
over from `PREREG_LIVE_SOAK.md` unchanged.

**Stated plainly, because "one changed factor" must not be a phrase that
quietly covers two things:** the pre-flight smoke test (below) revealed a
*second*, independent defect in the load path — the k6 VU pool was sized for
the average request and collapsed on the latency tail. Both defects are
fixed here. They are treated as one factor because they are one mechanism —
*the demand the cell specifies actually reaches the tenants* — and because
neither touches the controller, the plant, the workload, the fault
injectors, the metrics or the scoring. A reviewer who disagrees and counts
them as two factors loses nothing: both are described below in full, and
neither was chosen after seeing a hypothesis outcome.

### Defect A — the port-forward was unsupervised

In `eval/harness/cluster_backend.py`:

| Before (attempts 1–2) | After (attempt 3) |
|---|---|
| `kubectl port-forward` spawned once via `Popen`, `stderr=DEVNULL`, never checked again for 24 h | `PortForwardSupervisor` polls `/healthz` through the forwarded port every 15 s, respawns on a dead process or confirmed-unhealthy probe, and logs every restart with timestamp and reason |
| Which pod the forward resolved to: unrecorded | Endpoints recorded at start, so a delivery collapse can be correlated with a pod restart |
| `k6-summary.json` written to a `tempfile.TemporaryDirectory`, deleted on exit — including when the gate rejected the run | Copied to `eval/results/live_soak_evidence/` **before** the delivery gate can raise, and again in the `finally` block |
| Delivery health unobserved until the run ended | Session-side probe curls the load port every 5 min, logging to `eval/results/live_soak_evidence/delivery_probe.log`, and alerts on 2 consecutive failures |

### Defect B — the k6 VU pool was sized for the average, not the tail

Found by the pre-flight smoke run, which passed the delivery check
(**0.73% failed requests**, well inside the 1% threshold) and was then
rejected by the *other* half of the same gate: **647 dropped iterations**
against a `count<1` threshold.

The arithmetic, from the preserved `k6-summary.json`:

| quantity | value |
|---|---|
| peak per-tenant arrival rate (this cell) | **94 req/s** |
| `http_req_duration` average | 23 ms → Little's law: ~2 VUs |
| `http_req_duration` **max** | **3,418 ms** → ~322 VUs at peak rate |
| `preAllocatedVUs` (before) | **50** |
| VUs the run actually grew to | **968** across 8 tenants (832 peak in use) |

Latency is 17 ms at p95 but the tail reaches 3.4 s. When it spikes, VU demand
jumps roughly 150×, k6 must allocate VUs *dynamically*, and it drops
iterations while doing so. The generator, not the cluster, failed to deliver
the demand.

Fix: `preAllocatedVUs` 50 → **150**, `maxVUs` 500 → **1000** per tenant.

**150 is derived, not tuned to pass a gate.** The smoke run's own pool grew
to 968 VUs across 8 tenants; 8 × 150 = 1200 pre-allocates slightly above what
the load demonstrably needed, so k6 no longer discovers it mid-excursion.
Had I instead relaxed the `dropped_iterations` threshold, that would have
been moving the goalposts to make a run pass — the threshold is untouched,
and it is the thing that caught this.

**Why one mechanism.** Defect A is delivery *to the cluster*; defect B is
delivery *from the generator*. Both are "the demand does not reach the
tenants", both are invisible in pod health and memory, and both produce
exactly the artefact `check_k6_delivery`'s docstring warns about: a
complete-looking run with artificially low cost and violation. Neither
touches the controller, plant, workload, injectors, metrics or scoring. The
published arms and every committed record replay unchanged; `reproduce.py`
holds at 26/26 byte-identical with both changes in.

**Pre-flight, executed before this prereg was pushed** (`live_smoke_pf.yaml`,
non-scored, its own output DB; evidence in
`eval/results/live_smoke_pf_evidence/`). A 15-minute live run on `small`,
with the delivery path deliberately broken mid-load:

| test | result |
|---|---|
| Kill the `kubectl port-forward` process outright | **RESTART #1 logged 6 s later** (`process exited`), new forward spawned, delivery returned `200` — one failed probe total |
| Delete 3 control-plane pods mid-load (endpoint churn) | delivery held `200` across 6 consecutive probes; the forward was not pinned to those pods, so the supervisor was not exercised |

Under the pre-fix code the first test would have black-holed every
subsequent request for the remainder of the run while k6 kept posting and
exited 0 — the attempt-2 signature exactly.

**An unplanned observation from the smoke log, recorded because it may
matter more than the tested fix.** The forward attached at 22:32:04 with
`endpoints=10.244.1.13 10.244.1.9` — **two** endpoints — and by the restart
47 s later the Service had **sixteen**. A forward established while the
deployment is still scaling up therefore attaches to a near-empty endpoint
set. That is a second plausible contributor to attempt 2's 36.4%, distinct
from the single-pod-restart lead, and it is *not* something this prereg's
changed factor was designed to address — the supervisor merely happens to
heal it by re-resolving on restart. It is recorded here as an observation,
not as a diagnosis, and SK-H5 is where the attempt-3 evidence about it will
land.

## Design (frozen — inherited unchanged from PREREG_LIVE_SOAK.md)

**Duration.** 12 h minimum, **24 h target**, one kind cluster, one
uninterrupted run. If the machine sleeps the run is **VOID** — reported as
void, never spliced with a second half.

24 h is retained deliberately. It is the reason this work package exists for
a Transactions-tier pitch — "ran live once" versus "ran live for a day,
through faults" — and shrinking it after two failed sittings would be
optimising for finishing rather than for the argument.

**Cell.** `crud_bursty` / `uniform` / `medium`, CRUD-only load
(`POLYFORGE_EVAL_LIVE_AI` unset), `POLYFORGE_EVAL_SHARED_PG=1` (R5). If
Docker cannot hold `medium` for the duration, drop to `small` and disclose;
duration outranks width.

**Faults (frozen schedule).** B2's two injectors unchanged — planner crash
and apiserver throttle — at **T+2 h, T+5 h, T+8 h, T+11 h**, alternating,
continuing at the same 3 h spacing to T+23 h if the run reaches 24 h. That
is 4 crashes and 4 throttles, ≥2 of each well inside the 12 h minimum.

**Baseline values (frozen FROM the committed record, not invented).** Read
off `eval/results/live_chaos_p99_runs.csv`, row `phase7_chaos_p99` /
`crud_bursty`: `crud_p95_ms` 2.9827, `crud_p99_ms` 8.0072, `mean_violation`
0.0, 135 steps. `CHAOS_TOL = 0.05` inherited from B2's LC-H1.

## Hypotheses (frozen — identical to PREREG_LIVE_SOAK.md)

**SK-H1 (recovery, binary).** Every injected fault recovers **without
operator intervention**: within 5 minutes of each injection the run returns
to planning, and post-fault `mean_violation` is within `CHAOS_TOL = 0.05` of
the pre-fault level. Scored per occurrence; PASSES only if **every**
occurrence passes.

*Disclosure carried forward:* attempt 2 produced operational evidence for
this (7 of 8 injections showing observed perturbation and recovery, pod-level
verified) but could not score the frozen metric form. That evidence is
**not** carried into attempt 3's scoring. Attempt 3 scores its own run.

**SK-H2 (audit continuity).** Every degraded cycle is audited across the
whole soak. Scored as: count of degraded cycles equals count of audit
records for them, exactly. Any gap fails it.

**SK-H3 (latency stability).** Every **hour-bucketed** `crud_p95_ms` stays at
or below the committed **p99** of **8.0072 ms**. The threshold is
deliberately the short run's *tail*, not its p95: a sustained run is allowed
to be worse than a 135-step p95, and using a recorded value beats a margin
invented today. A bucket above it fails SK-H3 and names the hour.

**SK-H4 (validity, binary).** Zero invalid-run signatures over the whole
soak: nonzero p95, nonzero `n_events`, sampler coverage ≥ 90%, and k6
delivery thresholds met — the existing `check_metrics`,
`check_sampler_coverage` and `check_k6_delivery` gates, unchanged.

**SK-H5 (NEW — the changed factor, descriptive, no pass/fail).** Report how
many times the port-forward supervisor restarted the forward, when, and with
what reason, from `port_forward_summary.json` and `delivery_probe.log`.

This is registered as *descriptive on purpose*. It answers "did the new
mechanism do anything, and was attempt 2's diagnosis right?" — and both
possible outcomes are informative. **Zero restarts alongside a passing
SK-H4** would mean the delivery failure had a different cause that this run
happened not to hit, and the attempt-2 root-cause lead stays unconfirmed;
saying so is the honest reading, not a disappointment. Restarts alongside a
passing SK-H4 would confirm the mechanism mattered. Making it a scored
hypothesis would invite reading a convenient number as a result.

## Outcome handling and stopping rule

The soak runs **once**. No hypothesis, threshold, fault time, duration or
margin is altered after the first result is seen.

- **Any hypothesis FAILS** — that is the finding and the headline of the
  record. Instability surfacing mid-soak is a **result, not a nuisance**:
  reported, fixed forward, re-sat under a further prereg with one changed
  factor.
- **SK-H4 FAILS again** — the run is again invalid, and the record says so.
  A third failure would make "the local harness cannot sustain a 24 h live
  run on this machine" itself the finding worth reporting, rather than
  something to keep retrying past.
- **The record is never truncated or spliced.** A run ending early is
  reported at the length it reached, with the reason.
- **A void run is reported as void.** Machine sleep is the named risk; power
  settings are changed before the run and restored after, with the prior
  values recorded first (`docs/WP14_SOAK_RESTORE.md`).
- **The early-warning probe may stop a doomed run.** If the delivery probe
  shows sustained failure in the first hour, the run is stopped, reported at
  the length it reached, and fixed forward. Stopping early on a *known
  invalid* delivery path is not cherry-picking: the alternative is spending
  23 more hours to reach the same rejection. Any such stop is disclosed in
  the record with the probe log as evidence.

`reproduce.py` must re-derive every previously committed record
byte-identically after this work lands (R4). The new record joins the gate.
