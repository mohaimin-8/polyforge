# Pre-registration: live CRUD-plane soak, attempt 6 — one changed factor

Drafted 2026-08-24 (session 39). **Committed and pushed before the run
starts**; the push event is the timestamp anchor. This scores new live
measurement, so R1/R2 apply in full.

## Why there is a sixth sitting at all

`PREREG_LIVE_SOAK_V3` fired its stopping rule on attempt 5 and
`RESULTS_LIVE_SOAK_V3.md` reported the run INVALID. That record stands exactly
as committed, including its FAIL and its conclusion.

**The stopping rule was then applied too broadly, and this prereg says so
plainly.** It forbids retrying *the same apparatus* — "the finding is not 'try
a sixth time'" was written against the case where nothing had changed. It does
not forbid a re-sit under a genuinely changed factor; that is precisely what
V2→V3 was, and the V2 rule was honoured the same way. Attempt 5 left a
concrete, un-actioned defect on the table, and stopping while holding a fix is
not what the rule protects against.

## The changed factor: the load generator, sized from its own measured tail

**One factor, and it is not a threshold.** Attempt 5 died at T+34m with
`tenant_t07` pinned at **401/401 VUs**. The arithmetic is not close:

| quantity | value |
|---|---:|
| peak per-tenant arrival rate (this cell) | 94 req/s |
| worst request the run recorded | **4.365 s** |
| VUs that demands at peak rate | **410** |
| VUs the pool had | **400** |
| shortfall | **10** |

The generator was sized at 1.02x the tail it had *already measured on the same
stack*. That is the same defect as the 150-VU pool that failed earlier in this
work package, one order of magnitude down, and it is a defect in the
measurement apparatus rather than in the system under test — which delivered
604,349 requests at **0.0000% failed** and a client p95 of 11.98 ms right up to
the moment the generator ran out of virtual users.

`preAllocatedVUs` 400 → **1200**, `maxVUs` 1000 → **2000**. 1200 covers a
**12.8 s** stall at the peak rate: three times the worst ever observed on this
stack, not 1.02 times. Cost is 1.22 GB across eight tenants at the measured
0.13 MB per pre-allocated VU.

**Nothing else changes.** Duration, cell, fault schedule and offsets,
hypotheses, margins and thresholds all carry over from `PREREG_LIVE_SOAK_V3`
unchanged. In particular:

**`dropped_iterations` remains absolute, cumulative, and abort-on-fail. The
threshold is NOT relaxed.** One drop in ~26 million iterations still fails
SK-H4. That has been the commitment since V2 and it survives this prereg
intact — the whole point of fixing the generator is that the gate can stay
where it is.

## What this does NOT fix, stated before the run

**The stall itself is not solved.** Its cause remains unidentified. What is
known: it is not pod restarts (zero), not node memory pressure (8%), not
autovacuum, and not the fault schedule. `synchronous_commit=off`,
`max_wal_size=4GB` and `checkpoint_timeout=30min` reduced its frequency — two
stalls in three hours before, one in roughly five and a half hours after — and
attempt 5's worst request (4.365 s) was smaller than the 5.696 s that aborted
an earlier hour. It was not eliminated.

So this attempt does not claim the stall is gone. It claims the **generator can
now absorb one** without dropping an iteration. If a stall longer than 12.8 s
occurs, SK-H4 fails again and that is the honest outcome.

## Ladder precondition

Stage B is re-run on the changed commit before the sitting, because the pool
change alters what the system under test experiences during an excursion: with
a larger pool, more concurrent requests reach it instead of being dropped.

Stage C's four-hour endurance result is **carried forward with its reason
stated**: it recorded zero excursions in 241 minutes, so the VU pool was never
reached and could not have influenced pod memory, restarts or the hourly p95.
Carrying it is a judgement, it is disclosed here rather than assumed, and a
reader who rejects it loses only the endurance rung — Stage B and the sitting
itself are run fresh.

## Hypotheses, design and stopping rule

Inherited verbatim from `PREREG_LIVE_SOAK_V3`: SK-H1 (recovery on 60 s
buckets), SK-H2 (audit continuity as a healthy-vs-degraded difference), SK-H3
(hourly `crud_p95` at or below 8.0072 ms), SK-H4 (validity, absolute), SK-H6
(load distribution), SK-H7 (client latency, descriptive). Cell `crud_bursty` /
`uniform` / `medium`, CRUD-only, shared PostgreSQL, dual export at 3600 s and
60 s. Eight faults at T+2/5/8/11/14/17/20/23 h, alternating planner
scale-to-zero and CPU starvation.

The V3 disclosures carry too, unchanged: `crud_p95`/`crud_p99` measure CPU
service time and not service latency, and the host is prepared before the
sitting (build cache reclaimed, WSL2 reset, no start below ~6 GB free).

## Outcome handling — the stopping rule, restated so it is not over-read again

The soak runs **once** under this prereg. No hypothesis, threshold, fault time,
duration or margin is altered after the first result is seen.

- **SK-H4 fails on a stall longer than 12.8 s** → the generator is no longer
  the limiting factor and the stall is. The finding is then about the host's
  storage, the evidence for it is already gathered, and the next step is
  different hardware — not a seventh sitting on this one.
- **SK-H4 fails for any other reason** → reported as such, and this apparatus
  is done. Five invalid sittings with the generator provably not at fault is
  the limitation, and it is registered here so that reporting it cannot look
  like a decision made after seeing data.
- **The run completes** → the hypotheses are scored as written, and a failing
  hypothesis is the finding rather than a nuisance.

`reproduce.py` must re-derive every previously committed record
byte-identically after this work lands (R4). The new record joins the gate and
does **not** replace `RESULTS_LIVE_SOAK_V3.md`, which stands with its INVALID
verdict.
