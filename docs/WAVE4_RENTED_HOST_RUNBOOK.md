# B1 on a rented GPU host — push-button runbook

The route `PREREG_WAVE4_LIVE_PLANE.md`'s **session-44 amendment** declares:
one host, three tiers, vLLM, corrected latency metric. Read that amendment
before running; this file is only the mechanics.

**Why not the free route.** Measured, session 44: the cell needs ~64 AI rps at
base and ~110 at peak; `GPU T4 x2` delivers 26.5–46.5. Amendment clause 3
vetoes a run whose slowest tier exceeds 2500 ms, and `mid` on a T4 is 2310 ms
pinned (190 ms of headroom) or 3081 ms sharded (over target outright). The
batching that would close the throughput gap pushes `mid` to 3285 ms at batch
32. **No batch size satisfies both gates** — see `WAVE4_FREE_ROUTE.md` and
`TIER_BENCH.md`. The free route is infeasible for this cell, not merely slow.

---

## 0. Host

| requirement | value | why |
|---|---|---|
| GPU VRAM | **≥ 40 GB** (A100 40/80, L40S) | fp16 weights are ~1 + 6 + 15 = 22 GB before any KV cache; 24 GB cannot serve three tiers |
| vCPU / RAM | ≥ 8 / ≥ 32 GB | the same box runs kind, the operator, the planner, the gateway and k6 |
| Disk | ≥ 60 GB | three model downloads plus images |
| Cost | ~$1–2/hr, **~$5–15 total** | setup plus 16 runs at `steps: 30` |

Both halves run **on this one box**. That is what makes session-33 clauses 2–3
void by its own clause 4, removing the tunnel and its latency inflation.

## 1. Repo and dependencies

```sh
git clone <repo> && cd polyforge
# docker, kind, helm, k6, go, python per docs/REPRODUCE.md
```

## 1b. Which route — READ THIS FIRST

Two shapes, and they need different hosts.

| | rented box must be | clauses 2-3 | setup on the clock |
|---|---|---|---|
| **single-host** | real VM, root, **Docker** (kind needs a daemon), sm_80+, >=40 GB | **void** by session-33 clause 4 | more |
| **split-host** | any GPU container, sm_80+, >=40 GB | **APPLY** | less |

**Split-host is cheaper and opens up container-based providers**, because the
rented box then runs only vLLM and never needs Docker. It is **already
pre-registered** (session-33 amendment), so it costs nothing scientifically --
but clauses 2 and 3 come back into force, which means `tunnel_preflight.py` is
mandatory and an over-target slowest tier **voids the run**.

On split-host the cluster half runs on your own machine. Rehearse it there
first against `kaggle_tier_server.py --mock` before renting anything; session
44 found seven defects that way at zero cost, including a WL-H2 gate that
certified only two tiers of three.

### Split-host: what changes

```sh
./scripts/b1_tier_host.sh up-split     # tiers + one cloudflared tunnel EACH
```

A quick tunnel exposes ONE port and vLLM serves ONE model per server, so three
tiers need three tunnels. The script prints the `TIER_BACKENDS` line with the
public URLs. Then, **before** the cluster half:

```sh
python eval/scripts/tunnel_preflight.py     # clauses 2-3 gate
```

It fails if the tier gap does not survive the round-trip, if the slowest tier
exceeds the 2500 ms premium SLO, or if the failure rate under load exceeds 1%.
**Do not start the matrix on a failure** -- that is what clause 3 means.

Two traps measured in rehearsal, both specific to this route:

* **`--no-tunnel` is for the LOCAL mock only.** The tier server opens a
  cloudflared tunnel by default and dies on Windows (`WinError 193`, it fetches
  a linux-amd64 binary). The flag you need locally is the opposite of the one
  you need on the box.
* **Model aliases differ between rehearsal and the real run.** The mock serves
  `qwen2.5-0.5b-instruct`; vLLM's `--served-model-name` serves `small`. The
  `TIER_BACKENDS` JSON is **not** copy-pasteable between them.

## 2. Tier substrate

```sh
./scripts/b1_tier_host.sh up
```

Installs vLLM if absent, serves `small`/`mid`/`large` on ports 9101–9103 with
explicit `--gpu-memory-utilization` splits, waits for readiness, then
**verifies**: `nvidia-smi` residency, and a real `chat/completions` decode per
tier — not a `/v1/models` ping, because an endpoint that lists a model it
cannot decode would pass a liveness check and void WL-H2. It exits non-zero
and tells you not to score if any tier fails.

It prints the `POLYFORGE_EVAL_TIER_BACKENDS` line. Export it.

> **Binding obligation from the amendment.** If `large` cannot be held in GPU
> memory on the day, it is **dropped and the run declared a two-tier run**. A
> CPU-offloaded tier measures the offload, not the tier. Do not quote it.

## 3. Cluster half

```sh
export POLYFORGE_EVAL_SHARED_PG=1     # per-pod SQLite reads ~empty under
                                      # scaling and yields 0-latency metrics
export POLYFORGE_EVAL_LIVE_AI=1
export POLYFORGE_EVAL_TIER_BACKENDS='<the line step 2 printed>'
```

Install the operator chart with `--set planner.auth.enabled=false`, or export
the chart's generated token as `POLYFORGE_PLANNER_TOKEN`. Either is fine; the
protocol is untouched by the choice. Mechanics in
`docs/WAVE3_LIVE_RUNBOOK.md`.

## 4. The WL-H2 gate — **before** any comparison number

```sh
python eval/scripts/knob_preflight.py   --gateway http://127.0.0.1:18081   --tenant <tenant> --api-key <key> --admin-key <admin-key>   --tiers small,mid,large
```

> **`--tiers` defaults to `small,mid`.** On a three-tier run you MUST pass
> `--tiers small,mid,large` explicitly, or the gate silently certifies only
> two of the three tiers and the `large` tier enters the scored matrix
> unverified. This is the one place the two-tier history is still wired in as
> a default.

WL-H2 must **pass** here. `tunnel_preflight.py` is not required on this route
(there is no tunnel) and never substituted for this: only `knob_preflight.py`
exercises the cache knob through the real gateway.

**A WL-H2 failure voids WL-H1.** Report it; do not proceed and do not soften.

## 5. Run

```sh
python -m harness.runner experiments/wave4_live_plane.yaml   # from eval/
```

4 arms × 4 cells × 1 rep = **16 runs**, `steps: 30`, `retries: 0`.

**Budget ~3.2 hours, not 90 minutes.** Session 44 measured **708 s per
run** against the ~300 s that `30 steps x 10 s` implies -- per-run cluster
setup and teardown dominate. On split-host the GPU box is up for that
whole window (~$4-6). The runner RESUMES: valid run_ids are skipped, so a
crash costs only the run in flight, never the ones already banked. The prereg's
stopping rule is one execution per arm per cell — no second attempt.

## 6. Export and score

Read live p95/p99 from **`polyforge_http_request_duration_seconds`** — the
amendment makes the histogram primary — and carry `replay.go`'s `crud_p95`
alongside every figure. **Never compare the two across that boundary**, the
same rule the ground rules already apply to absolutes across substrates.
`scripts/soak_observer.sh` scrapes the histogram over the NodePort the load
already uses (never a port-forward, which pinned all load to one pod of
sixteen in attempt 4).

Results to `RESULTS_WAVE4_LIVE_PLANE.md` **as measured**, WL-H1 failure
included and headlined if it occurs.

---

## What voids the run

1. **WL-H2 fails** — a knob is inert; WL-H1 is not interpretable.
2. **`large` offloads to CPU** — drop it, declare a two-tier run, continue.
3. **Any post-hoc change** to arms, cells, the 0.01 iso-fairness margin, or
   the metric choice. All are frozen by the prereg and its amendments; the
   metric was fixed in advance precisely so it could not be chosen after
   seeing the arms.

## Expected posture, stated before the run

Session 44 measured `jcac` at 78.8% `small` / 14.1% `mid` / 0.0% `large` in
sim, against `gptcache` at 15.9% `large`. If the live tier histogram is wildly
different from that, suspect the tier knob before believing the result.
