# Fault 7: the planner-crash fault does not interrupt planning

Registered in `MIDRUN_FINDINGS.md` at T+14 h, **before fault 7 fired**, with
both possible readings interpreted in advance so that neither could be
retrofitted around whatever number arrived. Raw data:
`fault7_endpoint_gap.log` (576 samples, ~1 Hz, 11:48:03 → 11:58:00 local).

## The registered prediction, and its failure

The question was why five planner kills had produced **zero**
`planner unavailable, holding last good plan` lines when the plan interval is
10 s and the binary demonstrably contains that string. The registered
prediction was that the planner's endpoint gap is shorter than 10 s, so the
plan loop never lands inside an outage.

**That prediction is wrong.**

| measurement | value |
|---|---|
| endpoint outage | **26 s**, one contiguous span (11:50:33 → 11:50:59) |
| plan interval | 10 s |
| fallback lines expected | 2–3 |
| `planner unavailable, holding last good plan` | **0** |
| `plan cycle failed` | **0** |
| policies still `lastPlanSource=planner` | 8 / 8 |

The outage is 2.6x the plan interval and the fallback path still did not run.
Per the registration, the alternative reading applies: *"a gap well over 10 s
means something else is wrong and the fallback path is not being reached when
it should be."*

## The mechanism, from the probe's own state column

The pod's readiness during the gap is the clue:

```
11:48:03  ready=1 notready=0  Running      1/1
11:50:33  ready=0 notready=0  Terminating  1/1   <- endpoint removed
11:50:48  ready=0 notready=1  Terminating  1/1
11:51:02  ready=1 notready=0  Terminating  1/1
11:51:07  ready=1 notready=0  Error        0/1
11:51:08  ready=1 notready=0  Running      1/1
```

The pod reports **1/1 ready throughout its termination**. Kubernetes drops a
deleting pod from `Endpoints` at once, but that does not close established
TCP connections, and the process serves until SIGTERM completes. The operator
reaches the planner through an HTTP client with connection reuse, so it was
almost certainly still talking to the dying pod across the whole window.

From the operator's side there was no outage at all. The fault removes a
Service endpoint that this particular client does not consult per request.

**This is inference, not proof.** Confirming it needs a connection-level test
— forcing the client to re-resolve, or killing the pod with no grace period —
and neither can be run against a live soak. It is recorded as the
best-supported explanation, with the falsification of the registered one
recorded as fact.

## What is established regardless of mechanism

Across **seven** planner-crash injections spanning four attempts, `fallback()`
has never executed. SK-H1 scores post-fault recovery through the last-good-plan
path; that path has no evidence behind it in any run to date.

This is independent of, and additional to, SK-H1 already being vacuous because
`mean_violation` has no dynamic range (375 ms target against an ~8 ms p99).
Two unrelated reasons the same hypothesis has never tested anything.

A planner-crash fault that actually exercises the fallback path needs
`--grace-period=0`, or scaling the Deployment to zero for a fixed window, so
that the planner genuinely stops answering rather than being quietly delisted
while it keeps serving.
