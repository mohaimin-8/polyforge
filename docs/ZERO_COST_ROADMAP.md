# The $0 route to a scored B1 — and everything else that is not writing

Written 2026-08-31 (session 42), answering one question the author asked
directly: **without spending money, is the same type of result achievable?**

**Answer: yes, for WL-H1/WL-H2/WL-H3 — the scored B1 matrix — and the block
is a software limit in our own tier server, not the free GPU.** The
qualification is real and is stated in §7. Nothing below requires a rented
GPU, a cloud credit, or an account upgrade.

> Written to be executed cold. Same conventions as
> `docs/PUBLICATION_ROADMAP.md`: run the §0.1 gate battery before and after
> every phase, one phase = one commit series, never commit on a red battery.
> **Phase 6 (writing) does not start until the author says so.**

---

## §1 What actually blocked the 2026-08-31 run

`docs/WAVE4_FREE_ROUTE.md` §0a records the failure and attributes it to the
card:

```
48.3% of requests failed (2,880 of 5,963)
iteration duration: median 58 ms, max 60 s
aborted by http_req_failed rate<0.01 at 20% of the load window
```

and `docs/COMPLETION_ROADMAP.md` A4/A5 concludes:

> "the cell wants ~100 req/s and a P100 doing 48 greedy tokens serves roughly
> 0.5 req/s. Two orders of magnitude is not a batching problem."

**That conclusion compares the cell's demand against our server's throughput,
not against the card's.** `research/calibration/kaggle_tier_server.py:113-146`
serves every request at **batch size 1**, one at a time per tier, behind a
`threading.Lock`:

```python
self.locks = {tier: threading.Lock() for tier, _ in TIERS.values()}
...
with self.locks[tier]:
    inputs = tok([text], return_tensors="pt").to(model.device)
    out = model.generate(**inputs,
                         max_new_tokens=MAX_NEW_TOKENS,   # 48
                         min_new_tokens=MAX_NEW_TOKENS,   # 48
                         do_sample=False, ...)
```

0.5 req/s is what that code does. It is not what a P100 does.

**The roofline check.** Autoregressive decode is memory-bandwidth-bound: each
step reads the whole weight matrix once. Qwen2.5-3B in fp16 is ~6 GB; a P100
has 549-732 GB/s, so one decode step costs ~8-11 ms and 48 tokens should cost
**~400-530 ms**. `TIER_BENCH.md` measured **1881 ms**. The card is already
running ~3.5x off its own roofline at batch 1, because per-call HF `generate`
overhead dominates a 48-token completion.

**And a batched step reads those weights once for the entire batch.** Batch of
N costs approximately the same wall time as batch of 1 until it becomes
compute-bound. That is the throughput multiplier the A4/A5 conclusion did not
account for.

---

## §2 Why this cell batches perfectly

Static batching usually fails on ragged completion: sequences finish at
different steps and the batch stalls on its slowest member. **This cell cannot
have that problem**, by construction already in the committed server:

| property | value | consequence for batching |
|---|---|---|
| `min_new_tokens` | 48 | no sequence stops early |
| `max_new_tokens` | 48 | no sequence runs long |
| `do_sample` | `False` (greedy) | deterministic, reproducible |

Every request decodes **exactly 48 steps**. There is no EOS variance, no
sampling variance, and no ragged tail. Left-pad the prompts, run one
`generate()` over the batch, scatter the results. This is the easiest possible
batching case, and it is easy *because* of a decision already made and
documented for a different reason (`kaggle_tier_server.py:125-134`).

---

## §3 The arithmetic — how much throughput is actually needed

From `eval/harness/workloads.py` and `cluster_backend.py`:

- **8 tenants per cluster, every mix** (`workloads.py:148`)
- `joint_stress` per-tenant `base_rps`: `chat 5.0 + embed 2.0 + agent 1.0 +
  crud_read 8.0` = 16 rps
- AI kinds are `("chat", "embed", "agent")` (`cluster_backend.py:151`)

| quantity | value |
|---|---|
| AI rps per tenant, scale 1.0 | 8.0 |
| **AI rps for the cell, scale 1.0** | **64** |
| CRUD rps (never touches the GPU) | 64 |
| burst peak (k6 `~218` VUs observed) | ~1.7x base |
| prompt pool per tenant (`joint_stress`) | 96 -> cache hits cut GPU load further |

So the GPU must serve on the order of **64 AI rps at base, ~110 at burst
peak, minus cache hits** — against 0.5 req/s today. **The gap is ~130x at
peak, and the lever is batch size.** A batch of 64 with cache hits removing a
material share is the right order of magnitude. It is not obviously enough and
it is not obviously short: **it must be measured, and §5 Phase 3 is where.**

Run cost: `steps: 30` x `interval_s: 10` = **300 s of load per (arm, cell)**,
4 arms x 4 cells = **80 minutes of load**, 2-4 GPU-hours with cluster churn.
Kaggle's free quota is **30 GPU-h/week**. That affords several attempts.

---

## §4 Why this does not need a new pre-registration

`PREREG_WAVE4_LIVE_PLANE.md` freezes the **cell, arms, workloads, steps and
hypotheses**. None of them change here. What changes is how the tier host
serves a request, and the prereg's Amendment already governs exactly that:

- **Amendment clause 2** already states live absolutes from the split-host
  route are **"not comparable to `TIER_BENCH.md`'s in-host tier latencies and
  are never quoted as tier latencies."** So per-request latency shifting under
  batching breaks nothing the prereg pins.
- **What must still hold** is WL-H2 materiality (the small/mid gap stays
  measurable) and **Amendment clause 3** (the slowest tier stays inside the
  2500 ms premium AI SLO). **The "`mid` benched 1881 ms, so 619 ms of
  headroom" figure is a P100 number and is SUPERSEDED (session 44):** on the
  T4 x2 this route now requires, `mid` is **2310 ms pinned to one card
  (190 ms headroom)** and **3081 ms sharded (over target — clause 3 voids
  the run)**. Batching past 8 spends what is left: batch 32 = 3285 ms, batch
  64 = 4657 ms. **So batching is NOT free here** — it trades directly against
  the clause-3 veto, and no batch size reaches ~64 rps while staying inside
  2500 ms. Both gates are measured pre-run and both remain absolute vetoes.
  See `WAVE4_FREE_ROUTE.md` and `TIER_BENCH.md`.
- `docs/COMPLETION_ROADMAP.md` A4/A5 already lists "batched serving on the
  free pool" as route 2 and reserves the new-prereg requirement for route 3
  (shrinking the cell). Batching is prereg-neutral by the project's own
  standing judgement.

**The one honest departure**, which must be written into the RESULTS file:
`kaggle_tier_server.py:87-91` justifies the lock as *"the honest behaviour of
a single card - and the sim's own tier model is single-stream."* Batching
departs from that. The defence is that **every production LLM server batches**,
so a batched substrate is *more* representative of the system being modelled,
not less — but it is a change of substrate character and is disclosed as one,
alongside the tunnel round-trip already disclosed in clause 2.

---

## §5 Phases

### Phase 1 — Fix `tunnel_preflight.py` to probe under concurrency (NO GPU)

**This is blocking and comes first.** `docs/WAVE4_FREE_ROUTE.md` §0a: the
preflight "issues eight *sequential* probes per tier and measures round-trip
latency. It never creates queueing, so it cannot observe it." It returned
**TUNNEL OK** minutes before the run that died at 48.3% failures. A sequential
preflight cannot certify a concurrent matrix.

1. Add a concurrent stage: drive the tier backends at the cell's **measured**
   peak concurrency (~218 VUs / ~110 AI rps) for 60 s.
2. Report, into `tunnel_preflight.json`: sustained rps, failure rate, p95 and
   **max** latency per tier, and the SLO headroom **under load**.
3. Exit non-zero if failure rate > 1%, or if the slowest tier's p95 exceeds
   the 2500 ms premium AI target (clause 3, now measured where it binds).
4. Keep the existing sequential stage — it still measures the tier *gap*.

**Done when:** the preflight reproduces the 48.3% failure against the current
unbatched server and exits 1. **A preflight that cannot fail on the run that
already failed has not been fixed.** Test it against the old server first.

### Phase 2 — Micro-batching tier server — **DONE (session 42)**

**Landed.** The per-tier `threading.Lock` is replaced by a per-tier
micro-batching worker (`BATCH_MAX=64`, `BATCH_WINDOW_MS=50`), and
`pad_batch` does the LEFT padding. `generate()` still blocks, so the
handler is unchanged and callers cannot tell they were batched.

**Measured in mock** (one sleep per batch, so the mock has the real
server's throughput shape): 12 concurrent `mid` requests take
**22.57 s serialised (`batch_max=1`) vs 1.94 s batched — 11.6x**. The
timing test was checked against `batch_max=1` and FAILS there, so it is
not vacuous.

**11 tests pass, 1 skipped.** The skipped one is
`test_batched_generation_matches_serial_exactly`, gated on
`POLYFORGE_TIER_TEST_MODEL` — greedy decode at fixed length is
deterministic, so batched output must be token-identical to serial.
**Run it in the Kaggle smoke before Phase 3 writes anything.**

*Original plan, kept for the record:*

Replace the per-tier lock with a batching queue in
`research/calibration/kaggle_tier_server.py`:

- A per-tier request queue; a worker collects up to `BATCH_MAX` requests or
  waits `BATCH_WINDOW_MS`, whichever first; one padded `generate()` per batch;
  scatter replies to waiters.
- **Left-pad** (`tok.padding_side = "left"`) and pass the correct
  `attention_mask`. Right padding silently corrupts decoder output — this is
  the classic trap and Phase 2's test exists for it.
- Start `BATCH_MAX=64`, `BATCH_WINDOW_MS=50`. Both are tunable **before** the
  scored run and frozen at the value the Phase 3 bench certifies.
- Keep `min_new_tokens == max_new_tokens == 48`, `do_sample=False`. They are
  what make this correct; do not touch them.

**The correctness gate — cheap and decisive.** Greedy decoding at fixed length
is deterministic, so **batched output must be token-identical to serial
output** for the same prompts. Add to `eval/tests/test_tier_server.py`:

```
test_batched_generation_matches_serial_exactly
test_left_padding_mask_is_correct_for_ragged_prompt_lengths
test_batch_window_flushes_on_timeout_not_only_on_fullness
test_single_request_still_answers_within_the_window
```

The first is the one that matters: an exact-match assertion over a batch of
mixed-length prompts. If it fails, padding is wrong and every AI number the
run produces would be silently garbage.

**Done when:** the four tests pass on CPU with a tiny model and on the real
models in a Kaggle smoke, and the `eval` suite stays green.

### Phase 3 — Re-bench the tiers under batching (GPU, ~30 min)

`research/calibration/kaggle_tier_bench.py` measured single-stream. Re-run it
against the batched server at batch sizes 1, 8, 32, 64, and record:

| what | why it matters |
|---|---|
| sustained rps per tier | does it clear ~110 AI rps at peak? |
| p95 and **max** latency per tier | clause 3 has **619 ms** of headroom over `mid` |
| small/mid **gap** | WL-H2 materiality must survive |

Write `research/calibration/TIER_BENCH_BATCHED.md` next to the existing bench;
**do not overwrite `TIER_BENCH.md`** — the committed table stays as the
single-stream record it is.

**Decision point, pre-committed here before any number exists:**
- Clears ~110 rps **and** `mid` p95 stays under 2500 ms -> **go to Phase 4.**
- Clears throughput but `mid` p95 crosses 2500 ms -> clause 3 territory. Try
  `BATCH_WINDOW_MS=20` and a larger `BATCH_MAX`. If it still crosses, the
  substrate is inadequate and is **reported as such** — do not score WL-H1
  from a saturated substrate.
- Does not clear throughput on P100 -> **switch the Kaggle accelerator to
  `T4 x2`** (also free): two cards means one tier per card and no cross-tier
  contention, and T4 is Turing (CC 7.5) so vLLM's continuous batching becomes
  available as a second lever. P100 is Pascal (CC 6.0) and vLLM does not
  support it — this is why the P100 route must be HF batching, not vLLM.
- Neither free path clears it -> **that is the honest finding**, and §7 is
  what the paper says.

### Phase 4 — Score B1 (GPU, 2-4 h)

Unchanged from `docs/WAVE4_FREE_ROUTE.md` steps 1-3, with three additions:

1. Run the **fixed** preflight (Phase 1). Both stages must exit 0.
2. Keep `_operator_paused` for the WL-H2 probe — session 41's finding: the
   Policy reconciler races the probe (`gateway_knobs.go:49` from
   `policy_controller.go:106`) and manufactures a false negative.
3. Run the frozen 4x4 matrix. Commit `tunnel_preflight.json` and
   `knob_preflight.json` into the evidence dir with the run.

Score WL-H1/H2/H3 **as they land.** WL-H1's falsifier is pre-committed: if
jcac does not beat the best single-knob arm on cost-at-iso-fairness, that
headlines the limitations and is **not re-run, re-tuned, or widened.**

### Phase 5 — Everything else that is not writing (NO GPU, NO money)

| # | item | state |
|---|---|---|
| 5a | Commit the `archive_zenodo.py` fix + attempt-10 evidence | fix done session 42, uncommitted |
| 5b | Zenodo bundle | **built and verified** — 404 files, 41 preregs, all records, manifest consistent. User publishes -> DOI |
| 5c | GHCR images + Helm chart (Track B2) | built at the desk; pushed with the author's token |
| 5d | Coverage 60% -> 70%+ | `internal/operator/controllers` conflict paths are envtest-only |
| 5e | WP10 stats family decision | RB-H1 p=0.0073 survives Holm in its own 6-family (0.00833), fails against all 48 (0.00104). Author declares the family |
| 5f | Fold session-41/42 corrections into `PUBLICATION_ROADMAP.md` §6 | mechanical |

### Phase 6 — Writing — **HELD**

WP9 reframe, WP10 write-up, WP11 submission mechanics, thesis fill-ins.
**Do not start any of this until the author says to start.** Recorded here so
the sequence is complete, not so it gets picked up.

---

## §6 Execute order

```
Phase 1 (preflight fix, no GPU)      <- blocking, do first
Phase 2 (batched server, no GPU)     <- build + tests offline
Phase 5a/5b/5f (desk work, no GPU)   <- run in any gap
Phase 3 (re-bench, ~30 min GPU)      <- decision point
Phase 4 (score B1, 2-4 h GPU)        <- only if Phase 3 clears
Phase 5c/5d/5e                       <- after or alongside
Phase 6 (writing)                    <- ONLY on the author's word
```

---

## §7 What $0 does NOT buy — state these, do not concede more

Being precise about the limit is what makes the rest defensible.

1. **Two tiers, not three.** The free route serves `small` and `mid`
   (`WAVE4_FREE_ROUTE.md` §4.1). `large` needs VRAM the free pool does not
   have. Already disclosed in the Amendment; unchanged by batching.
2. **Tunnel round-trip stays in every live absolute.** Clause 2 already
   forbids quoting these as tier latencies. Unchanged.
3. **A batched substrate is not the single-stream substrate `TIER_BENCH.md`
   measured.** Disclosed per §4. This is the one new disclosure batching adds.
4. **One machine, one kind cluster, 16 pods.** No multi-node or multi-zone
   result is reachable at $0. Transactions readers will notice; disclose it.
5. **No real trace has ever driven the live plane** — traces are replayed in
   simulation, the live plane drives synthetic cells. Closing this is
   WP14-sized and is *not* made cheaper by anything here.
6. **None of this touches BP-H1.** The cost claim stays withdrawn on both
   traces. B1 cannot restore it and is not being asked to.

---

## §8 The answer to "is the same type of result achievable"

**For B1 — yes.** WL-H1, WL-H2 and WL-H3 are scoreable at $0 if Phase 3
clears, and they are the same hypotheses, on the same frozen cell, with the
same falsifiers. Nothing is weakened to fit the hardware. The prior conclusion
that batching could not close the gap measured our own batch-1 server, not the
card, and the cell's `min==max` greedy decode makes batching unusually safe
here.

**Its honest probability.** The needed multiplier is ~130x at burst peak.
Batch-64 plus cache hits is the right order of magnitude, and the P100 is
already 3.5x off its own roofline at batch 1, so there is headroom in two
independent directions. But **this is an argument from the roofline, not a
measurement** — Phase 3 is where it becomes one, and Phase 3 is deliberately
cheap (~30 min of a 30 h/week quota) so the answer arrives before anything
expensive is committed.

**What $0 cannot fix** is §7 — and none of §7 is fixed by $20-60 either. The
rented GPU buys three tiers and removes the tunnel; it does not buy multi-node,
a real trace on the live plane, or a cost advantage. **The gap between the free
route and the paid route is smaller than the gap between either and the
remaining §7 limitations.** That is the real reason not to spend money here.
