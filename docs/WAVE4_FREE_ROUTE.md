# B1 on free infrastructure — the split-host route

`PREREG_WAVE4_LIVE_PLANE.md` assumes "a GPU host". A rented one costs
$20–60 for the run. This document is the $0 alternative: the GPU half on a
free Kaggle kernel, the cluster half on a free Codespace, joined by a free
tunnel. **Both halves are already proven in this repository** — Kaggle ran the
tier bench twice (`research/calibration/TIER_BENCH.md`), and Codespaces ran
the Phase 7 live campaign and the live chaos run (sessions 19, 22, 23).

The pre-registration is frozen and is **not** edited by this route. What the
split changes about the substrate is disclosed in §4, which is the text the
RESULTS file must carry.

---

## 0a. What this route CANNOT do (session 41, measured)

**A sequential preflight passing does not license a concurrent matrix.**

`tunnel_preflight.py` issued eight sequential probes per tier and measured
round-trip latency. It never created queueing, so it could not observe it.
**Fixed session 42** — the probe now runs a second, *open-loop* concurrent
stage at the cell's arrival rate and fails on k6's own `rate<0.01` threshold
or on a p95 past the premium AI SLO; `--skip-concurrent` restores the old
sequential-only behaviour and is recorded in the report so a scored matrix
cannot rest on one. The paragraph below is kept as the record of what the
sequential-only probe let through. On
2026-08-31 it returned TUNNEL OK — tier gap 551.8 ms against a 482.1 ms
threshold — and the matrix run behind it then failed:

```
48.3% of requests failed (2,880 of 5,963)
iteration duration: median 58 ms, max 60 s   <- a timeout tail, not slow service
aborted by http_req_failed rate<0.01 at 20% of the load window
```

A free Kaggle P100 **serialises generation**. The frozen `wave4_live_plane`
cell drives roughly 218 concurrent virtual users, which queue behind one GPU
until they hit the 60 s timeout. The median request stays fast — those are
cache hits and CRUD — so aggregate latency looks healthy right up until the
tail swallows the run.

**Consequence:** this route is adequate for the WL-H2 knob-liveness gate, which
is sequential, and for the tier bench. It is **not** adequate for the scored
4x4 matrix at the cell's frozen concurrency **as the tier server is written
today** — see `docs/ZERO_COST_ROADMAP.md`, which finds the 0.5 req/s ceiling
is `kaggle_tier_server.py`'s batch-1 lock rather than the P100, and phases the
$0 fix. Scoring B1 needs a substrate that
serves that concurrency — a rented GPU, batched serving on the free pool, or a
smaller cell under a NEW pre-registration (the current cell is frozen, so that
is a different experiment, not an adjustment).

The quota table below discusses GPU-hours and round-trip latency. Neither is
the binding constraint; **throughput under concurrency is**.

---

## 0. Does the route apply to you?

| | rented GPU host | this route |
|---|---|---|
| cost | $20–60 | **$0** |
| tiers | up to 3 (7B if VRAM allows) | 2 (`small`, `mid`) — see §4.1 |
| network | in-host, ~0 ms | tunnel round-trip — see §4.2 |
| quota | none | Kaggle 30 GPU-h/week, Codespaces 120 core-h/month |

B1 needs roughly 2–4 GPU-hours. Both quotas are comfortable.

If you have Google Cloud's $300 free trial or Azure for Students ($100) **and
GPU quota is granted on the account**, prefer a single GPU VM: it is also $0
out of pocket and avoids §4.2 entirely. New trial accounts frequently ship
with GPU quota 0 and the increase request is sometimes refused — check the
quota page before planning around it, then fall back here.

---

## 1. Start the GPU half (Kaggle)

New notebook → Settings → **Accelerator: GPU**, **Internet: On**. Paste
`research/calibration/kaggle_tier_server.py`, or push it as a script kernel.

```
python kaggle_tier_server.py            # loads both tiers, opens a tunnel
```

It prints the public URL and the exact export line for step 2:

```
PUBLIC URL: https://<random>.trycloudflare.com
export POLYFORGE_EVAL_TIER_BACKENDS='{"small":{...},"mid":{...}}'
```

Leave the kernel running. `--hours` (default 8) bounds the window so an
abandoned session stops burning quota.

## 2. Check the path is good enough — **before** standing up the cluster

This is the step that protects the scarce resource. It talks straight to the
tunnel, needs no cluster, and applies the same materiality rule WL-H2 will:

```
export POLYFORGE_EVAL_TIER_BACKENDS='<the line Kaggle printed>'
python eval/scripts/tunnel_preflight.py
```

Read three numbers:

* **`gap` vs `threshold`** — the tier knob must stay measurable. A constant
  round-trip cancels out of the gap and only raises the threshold by
  0.25 × RTT, so this survives a lot of latency.
* **`tolerates ~N ms more round-trip`** — headroom before WL-H2's tier probe
  would fail. With the committed tier table this is around 1.3 s.
* **`slowest tier … vs premium AI SLO 2500 ms`** — **usually the binding
  constraint, and the one to actually worry about.** The `mid` tier measured
  1883 ms, so roughly **600 ms** of added round-trip puts it over the premium
  target. Past that, premium tenants violate no matter what the controller
  does, the SLO term flattens, and the arms lose the dimension they are
  separated on. The probe warns loudly when this happens.

Exit 0 → proceed. Exit 1 → **do not start the run.** Reduce round-trip (a
Codespace region nearer the Kaggle pool), or use a rented/credit GPU host.

### 2b. Verified on the real host (session 33)

The wiring above was exercised end-to-end on the actual Codespace
(`fantastic-waffle`, 4-core standardLinux32gb) with the tier server in
`--mock` mode, before any GPU hour was spent:

```
 small: mean 1241.3 ms   mid: mean 1881.3 ms
  gap 640.0 ms vs threshold 310.3 ms -> MATERIAL
  tolerates ~1319 ms more round-trip before WL-H2's tier probe would fail
  slowest tier 1881 ms vs premium AI SLO 2500 ms -> 619 ms of headroom
verdict: TUNNEL OK
```

So on this host the budget is **~619 ms of tunnel round-trip** before the
premium SLO saturates (amendment clause 3 voids the run past that), against
~1319 ms before WL-H2's tier probe would fail. The SLO binds first, by
roughly 2x — measured, not estimated. A cloudflared quick tunnel typically
adds 50–300 ms, so the route has real margin, but it is not unlimited and
step 2 is what checks it on the day.

Codespace gotchas met this session, on top of the session-19 list:

* **Use a login shell.** `gh codespace ssh -- 'bash -lc "…"'`; a plain
  non-login shell has no `GITHUB_TOKEN`, and `gh` is not installed inside.
* **A dormant Codespace's git credential expires.** `git pull` fails with
  "Invalid username or token". Rather than push a token in, copy a delta
  bundle: `git bundle create delta.bundle <remote-sha>..main`, then
  `gh codespace cp -c <name> delta.bundle remote:/tmp/`, then
  `git fetch /tmp/delta.bundle main:refs/remotes/origin/main --force`.
  (3.2 MB for ~40 commits.)
* **`gh codespace cp` works from Windows** on gh 2.96 — the session-19
  base64-over-ssh workaround is no longer needed for file transfer, though
  base64 is still the reliable way to ship a multi-line *script* through the
  nested quoting.

## 3. Start the cluster half (Codespace) and run

Follow `PREREG_WAVE4_LIVE_PLANE.md` §Substrate and the existing
`docs/WAVE3_LIVE_RUNBOOK.md` mechanics, with these three settings:

```
export POLYFORGE_EVAL_SHARED_PG=1        # session-23 fix: per-pod SQLite
                                         # reads ~empty under scaling and
                                         # silently yields 0-latency metrics
export POLYFORGE_EVAL_LIVE_AI=1
export POLYFORGE_EVAL_TIER_BACKENDS='<same line>'
```

Install the operator chart with `--set planner.auth.enabled=false` (the
session-31 bearer token defaults on; the frozen harness posture is
unauthenticated), **or** export the chart's generated token as
`POLYFORGE_PLANNER_TOKEN` in the harness environment. Either is fine; the
protocol is untouched by the choice.

Then run the real gate before scoring anything:

```
python eval/scripts/knob_preflight.py --gateway http://127.0.0.1:18081 ...
```

WL-H2 must pass here. `tunnel_preflight.py` predicts this verdict but does
not replace it — only `knob_preflight.py` exercises the cache knob through
the real gateway.

---

## 4. Substrate deviations — **already declared in the prereg**

Correction to an earlier draft of this file, which said these deviations
belong in the runbook and the RESULTS file. They belong in the
**pre-registration**, pushed before any comparison number: its §Outcome
handling requires that "any substrate change forced by the provisioned host
... is declared as a pushed pre-run amendment in this file", following
`PREREG_WIRE_ATTACK.md` §Amendment. Putting them only here would have
invalidated the run.

That amendment is **already written and pushed** —
`PREREG_WAVE4_LIVE_PLANE.md` §Amendment (session 33, commit 791f96a). Nothing
further is needed before the run; the summary below is orientation, and the
amendment is authoritative wherever the two differ. Restate it in the RESULTS
file too, but the prereg push is what makes the run valid.

### 4.1 Two tiers, not three

The `large` (7B) tier is absent. `TIER_BENCH.md` measured 7B fp16 spilling to
CPU on the free pool's 16 GB card in two independent sessions; a CPU-offloaded
tier measures the offload, not the tier. The prereg's amendment rule covers
this exactly: *"if the host cannot hold 7B in GPU memory, the large tier is
dropped and the run is a two-tier run, declared as such."* **This is
protocol-legal, and it applies to a rented single-16 GB host too.**

### 4.2 The model tiers are reached over a public tunnel

The prereg says the tiers are "served on the GPU host"; here they are served
on a *different* free host and reached over the network. What that does, and
does not, change:

* **Does not change the tier gap.** Round-trip is added to both tiers alike,
  so the small/mid separation — the thing WL-H2 tests and the tier knob
  actuates on — is preserved. Measured and reported by step 2.
* **Does not change $-cost.** Cost is metered from the tier a request
  actually hit, not from its latency, so WL-H1's cost-at-iso-fairness reading
  is unaffected in its cost term.
* **Does change absolute latency**, and therefore the SLO term and the
  fairness index built on it. Live absolutes from this route are **not**
  comparable to `TIER_BENCH.md`'s in-host numbers and must never be quoted
  as tier latencies. Record the measured per-tier means from
  `eval/results/tunnel_preflight.json` alongside the result.
* **Could saturate the SLO term** if round-trip is large (§2). If the run
  proceeds with the slowest tier over the premium target, say so prominently
  and read WL-H1 knowing the fairness side is compressed.

### 4.3 What is unchanged

Arms, cells, hypotheses, falsifier, stopping rule, and the WL-H2 gate are
exactly as frozen. The run is scored once. A WL-H2 failure voids WL-H1 and is
reported as an inadequate substrate — that guard is what makes attempting a
free route safe: the worst outcome is a declared void, never a quiet result
from a bad substrate.

The amendment adds one guard on top: **clause 3**, an over-target slowest
tier voids the comparison on the same terms as an inert knob. A saturated
SLO term is the failure that would otherwise pass WL-H2 and quietly produce
a meaningless WL-H1.

---

## 5. Order of operations (why this order)

1. Kaggle up → tunnel URL.
2. **`tunnel_preflight.py`** — cheap, no cluster, protects GPU quota.
3. Codespace up, chart installed, `knob_preflight.py` — the real WL-H2 gate.
4. Only then: the four cells × four arms.
5. Stop the Kaggle kernel and the Codespace (both bill by wall-clock quota).

Steps 2 and 3 exist so that an inadequate substrate is discovered for free,
in that order of cost.
