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
| vCPU / RAM | **≥ 8** / ≥ 16 GB | the same box runs kind, the operator, the planner, the gateway and k6. **8 is measured sufficient** (session 48): the 12.2–12.4% that sessions 45–47 attributed to host concurrency was the AI gateway's hard-coded 600 RPM per-tenant limiter answering 429, plus three substrate defects — with those fixed (`4e499ef`) the frozen cell ran `valid` on an 8-vCPU `c6i.2xlarge`, 37,269 requests, 1 failure. See the prereg's session-48 pre-run note and `wave4_jointstress_probe_evidence/2026-09-15_ec2_ladder/` |
| Disk | ≥ 60 GB | three model downloads plus images |
| Cost | ~$2–5/hr, **~$10–25 total** | setup plus 16 runs at ~708 s each (~3.2 h; cluster setup/teardown dominates, not the 300 s window). A 40 GB GPU with 16–32 vCPU on one box costs more per hour than the GPU alone |

Both halves run **on this one box**. That is what makes session-33 clauses 2–3
void by its own clause 4, removing the tunnel and its latency inflation.

## 1. Repo and dependencies

```sh
# The repository is PRIVATE: a fresh host has no GitHub credential, and none
# should be put on it. Ship the tree from the operator's machine instead
# (the session-48 dry run did exactly this; evidence comes back by scp and
# is committed from the operator's machine):
#   git archive --format=tar.gz -o /tmp/polyforge-tree.tar.gz HEAD
#   scp /tmp/polyforge-tree.tar.gz ubuntu@<ip>:~/
# then on the box:
mkdir polyforge && tar -xzf polyforge-tree.tar.gz -C polyforge && cd polyforge
./scripts/phase7_bootstrap.sh          # python, kubectl, helm, kind, k6, harness deps (idempotent)
docker info >/dev/null && nproc && nvidia-smi --query-gpu=name,memory.total --format=csv
./scripts/b1_images.sh                 # build 4 polyforge images, pull 3 third-party (~5 min)
```

Two Linux-only settings that Windows rehearsals never exercised, both cheap:

```sh
# `docker info` above failed with "permission denied"? The harness calls
# docker without sudo:
sudo usermod -aG docker "$USER" && newgrp docker
# kind's documented "too many open files" failure on Ubuntu defaults
# (fs.inotify.max_user_instances=128); one 2-node cluster with ~25 pods
# per run for 16 runs is exactly the case it names:
sudo sysctl -w fs.inotify.max_user_watches=524288 fs.inotify.max_user_instances=512
```

k6 needs no `ulimit` change: it is a Go binary, and Go raises its own
open-file soft limit to the hard limit at start (Go 1.19+), which on an
Ubuntu 22.04 SSH session is 524288 -- far above the 9,600 sockets the
`joint_stress` VU pool holds. The AWS Deep Learning Base OSS NVIDIA Driver
GPU AMI (Ubuntu 22.04 / Python 3.10; driver, CUDA, Docker and the NVIDIA
container toolkit preinstalled) is what `scripts/aws_box.py` launches;
every box-side script parses under 3.10 (checked with
`ast.parse(feature_version=(3, 10))`, session 48), so the system Python is
fine. The box is **g6e.2xlarge** (1x L40S 44 GB, 8 vCPU, 64 GiB, ~$2.24/h
us-east-1), which the 8-vCPU "Running On-Demand G and VT instances" quota AWS
granted this account allows; **g6e.4xlarge** (16 vCPU) is the step-up if
step 1c ever fails at 8, and needs that quota at 16 (`aws_box.py quota`).

The `docker info` line is the host check against §0: a daemon that answers, a
core count of 16 or more, and a card with 40 GB or more. If any of the three
is wrong, this is the moment to release the box.

**`b1_images.sh` is not optional.** The harness side-loads seven images into
every per-run kind cluster with `docker save` / `kind load`, neither of which
pulls or builds. On the laptop they have existed since phase 7; a fresh host
has none, and until session 48 nothing in any runbook created them -- the
run would have died at the side-load step, after the box was paid for, before
the step-1c mock probe. `execute()` now refuses up front and names the script.

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

## 1c. Concurrency preflight — **before any model is loaded** (binding, session-47 amendment clause 2)

`joint_stress` failed on every arm on an 8-core host for host concurrency,
not tier throughput (§`joint_stress` is the cell that decides this, below).
Whether 16–32 cores serve its 9,600 VUs is **untested**, so it is tested
here, with the mock, in ~12 minutes, before a single model download.

```sh
# terminal 1 — the mock, no GPU, no tunnel
python research/calibration/kaggle_tier_server.py --mock --no-tunnel

# terminal 2 — the joint_stress probe against it (writes ONLY to *_PROBE_MOCK.*)
cd eval
export POLYFORGE_EVAL_SHARED_PG=1 POLYFORGE_EVAL_LIVE_AI=1
export POLYFORGE_EVAL_TIER_BACKENDS='<the mock's printed line>'
python -m harness.runner experiments/wave4_jointstress_probe.yaml
```

The printed line advertises the host's **primary IP** (e.g. `172.31.2.13`),
never `127.0.0.1`. The AI gateway that dials it is a pod inside kind, and from
a pod `127.0.0.1` is the pod itself; the host is reachable over the docker
bridge on its own address. Measured in the session-48 dry run on EC2: a
container completed a chat request against the mock on the primary IP.
`b1_tier_host.sh` prints its export line the same way for the real servers,
which it binds on `0.0.0.0` for the same reason (uvicorn's default is
loopback only). Until session 48 both printed `127.0.0.1` -- and the mock
printed nothing at all on `--no-tunnel` -- so the single-host route would have
failed WL-H2 with an unreachable substrate after the models were loaded.

**Pass** = the run reports `valid` — k6 `http_req_failed` under its frozen
`rate<0.01` for the full window and sampler coverage ≥ 90%. Nothing about
those guards is relaxed for this step.

**Fail** = **stop; do not proceed to step 2.** Copy the k6 summary from
`eval/results/wave4_jointstress_probe_evidence/` into a dated subdirectory
beside the session-45 one, with the same NOT EVIDENCE header, commit it, and
release the box. The decision between a larger host and a smaller cell under
a **new** prereg is then made with the run unspent. The prereg does not
pre-authorise a smaller cell.

This step produces **no comparison number**: the mock's delays are fixed
sleeps that never enter a record, and only the `jcac` arm runs. It is a
substrate gate, like WL-H2, and passing it is not evidence for WL-H1.

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

Every run (the step-1c probe included) writes `host_facts.json` beside its
k6 summary: GPU name / VRAM / driver from `nvidia-smi`, vCPU count, RAM,
kernel, tool versions. That file, not the terminal, is how the record shows
the §0 floor was met -- commit the evidence directory with it.

## 7. B1′ — the calibrated-controller matrix (executed 2026-09-15, 39/40 valid)

Same host class, same tiers, same steps 1–4. Differences from B1 (all
disclosed in `PREREG_WAVE4_CALIBRATED.md` and its amendments):

```sh
export POLYFORGE_EVAL_SHARED_PG=1 POLYFORGE_EVAL_LIVE_AI=1
export POLYFORGE_EVAL_FINE_BUCKET_SECONDS=10      # one export bucket per control step
export POLYFORGE_EVAL_TIER_BACKENDS='<the line b1_tier_host.sh printed>'
python -u -m harness.runner experiments/wave4_calibrated_plane.yaml   # from eval/
```

5 arms × 4 cells × 2 reps = **40 runs, ~10 min each, 6 h 14 min** on
`g6e.2xlarge` (the whole sitting, tiers and setup included, 10.5 h ≈ $23.5).
Per-run evidence lands under `wave4_calibrated_plane_evidence/runs/<arm>__<cell>__uniform__small__rep<N>/`
(both exports, the in-run histogram, the k6 summary, the load distribution
with per-pod presence, host facts). Score with
`research/analysis/analysis_wave4_calibrated.py`; `fig_wave4_calibrated.py`
draws fig20 from the same functions.

Three things this sitting taught, each of which cost a restart or a
re-scoring pass:

1. **`python -u`.** The runner's `[k/N]` progress lines are block-buffered
   into a file and appear only every ten runs or at exit; the failures are
   in the DuckDB, not the log. Read progress from the database (copy the
   `.duckdb` and its `.wal`, open the copy read-only — the runner holds the
   lock) and read a failed run's `error` column **before** anything can
   overwrite it. A resume re-executes every non-valid run and replaces the
   row.
2. **The load-distribution guard judges replicas that lived.** A controller
   that sheds replicas inside a 10-s control step leaves pods that
   `kubectl top` sees once at 1 millicore; the guard's population now
   excludes pods seen in fewer than `MIN_PRESENCE_SAMPLES` (3) samples
   (commit `86c29c5`). The pinning ceiling is still calibrated on a static
   Deployment: an HPA arm whose first pod is alone for the early window can
   trip it (one `replica-only` run did, both attempts) — that is a void, not
   a bug to repair after the fact.
3. **The metrics-server manifest is fetched from GitHub on every run.**
   One run of forty died at zero seconds on an HTTP 500 from
   `github.com/kubernetes-sigs/metrics-server/releases/...`. The runner's
   resume re-executed it. The manifest is now vendored, unmodified, at
   `eval/harness/manifests/metrics-server-v0.9.0.yaml` (its header records
   the upstream sha256; a test re-hashes the body), so a scored run's
   critical path touches no third party.

The fine export spans the WL-H2 preflight (five buckets, ~$0.13 of `large`
tier spend that every arm pays identically inside `total_cost_usd`) and the
teardown seconds; the scorer pairs buckets inside the load window (the span
from the first to the last bucket at ≥ half the run's median event count,
troughs included). A dead-man `sudo shutdown -h +N` on the box, re-armed
as the run outlasts it, is what makes an operator-side outage cost the box
and not the evidence: copy and hash-verify the evidence **before**
terminating, never the other way round.

---

## `joint_stress` is the cell that decides this — measured

A full 16-cell rehearsal against the mock (session 44, free, 2.8 h) came back
**12 valid / 4 failed**, and the four failures were **every `joint_stress` run,
on all four arms**. `joint_stress` is WL-H1's PRIMARY cell — the prereg calls it
"the cell the joint claim most needs" — so on a paid box this would have burned
the whole sitting and produced nothing on the central hypothesis.

The failure is the harness's own guard, the same one that voided the multinode
campaign:

```
RuntimeError: replica sampler covered 23% of the load window
(7 samples, 0 failed attempts): infra cost would be under-counted
```

Sampler interval is 10 s, so a 300 s window expects 30 samples. It got 7, with
**zero failed attempts** — `kubectl top pods` succeeded every time but took
~33 s per call. The cluster was starved, not broken.

**What starved it, and why this is good news.** Per-cell AI demand against the
mock's `BATCH_MAX 64 / 1.88 s` ceiling of ~34 rps:

| cell | AI rps | peak | CRUD rps | result |
|---|---|---|---|---|
| crud_bursty | 0 | 0 | **300** | valid |
| tier_mixed | 36 | 90 | 40 | valid |
| ai_cacheable | 56 | 140 | 40 | valid |
| **joint_stress** | **64** | **160** | 64 | **FAILED x4** |

`crud_bursty` pushed **300 rps of CRUD** through the same laptop and passed, so
this is **not** host saturation — which matters, because host saturation is the
one failure a rented GPU could not fix. It is the **tier-backend ceiling**:
`joint_stress` has the highest AI demand *and* a 96-prompt reuse pool, so its
cache hit rate is lower than `ai_cacheable`'s and more traffic reaches the
backend. `ai_cacheable` survives 56 AI rps only because high reuse absorbs most
of it first.

### CORRECTION — the above diagnosis was WRONG. A fast GPU does not fix this.

The reasoning above (tier-backend ceiling, therefore rent a GPU) was tested and
**refuted**. Re-running `joint_stress` against a mock at **150 ms** tier latency
(a ~426 rps ceiling, 12x the slow mock) produced a **byte-identical failure**:
same 23%, same 7 samples, same 0 failed attempts. Throughput is not the
mechanism.

**What actually happens**, from the k6 summary of the failed run — reproduced
twice, 12.35% and 12.22%:

```
vus_max:            9600
http_req_failed:    12.2-12.4%  (4840 of 5514)
http_req_duration:  p(95) 186-190 ms, avg 32 ms
dropped_iterations: 0
k6: thresholds on 'http_req_failed' crossed; abortOnFail -> stopped prematurely
```

The tier backend is **comfortable** — p95 under 190 ms. What fails is the
**cluster refusing ~12% of requests under 9,600 concurrent VUs**. k6 aborts at
~70 s of a 300 s window, which stops the replica sampler, which trips the
coverage guard. **`sampler covered 23%` is a symptom three layers downstream of
the real failure.**

`joint_stress` drives four request kinds across eight tenants, and that is what
produces 9,600 VUs — the same shape that voided the multinode campaign
("1200-1443 VUs per tenant across eight tenants and eight cores cannot serve
it"). **The binding variable is CONCURRENCY, not throughput.** `crud_bursty`
passing at 300 rps does not contradict this; it is a throughput number and says
nothing about VU count.

**Consequence for the route: split-host CANNOT fix this cell**, because the
machine refusing the requests is the one running the cluster — the operator's
own laptop — not the GPU. Renting a GPU buys a fast tier backend that was never
the constraint.

**Three options, all real:**

1. **Single-host on a 16-32 core box** — cluster and tiers together, so the
   cluster gets the cores. Keeps the frozen cell. Costs more than $10 and is
   unproven at 9,600 VUs.
2. **New prereg, smaller cell** — fewer tenants or lower per-tenant demand to
   bring VU count into range. Free, now precisely quantifiable, but changes a
   pre-registered protocol.
3. **Report `joint_stress` as unevaluable on available hardware.** The
   other three cells are reported as measured, descriptively, and WL-H3 runs
   on them — but **WL-H1 is then NOT EVALUATED**, neither PASS nor FAIL,
   because the hypothesis is defined in `joint_stress` and is not re-read off
   a different cell. Declared in advance: session-47 amendment clause 3.

## Rehearse with the REHEARSAL experiment, never the scored one

`eval/experiments/wave4_live_plane_rehearsal.yaml` exists because the scored
experiment writes its evidence to `eval/results/wave4_live_plane_evidence/`,
which is **committed evidence from the WP8b run** (`0036be3`). Running the
scored yaml locally against a mock silently overwrites it — observed in session
44 and restored with `git checkout`. The rehearsal yaml redirects both the
database and the evidence directory, so mock output cannot be mistaken for, or
overwrite, real evidence.

## What voids the run

0. **The step-1c concurrency preflight fails** — the host cannot serve the
   cell; the matrix is not started (session-47 amendment clause 2).
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
