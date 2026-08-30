# Completion roadmap — the two tracks that are not the thesis

Written 2026-08-30 (session 41), after WP14 closed and WP8b ran.
**Scope: everything that is not WP9–11 (the writing, user-owned).**

`docs/PUBLICATION_ROADMAP.md` remains the source of truth for what has been
done and why. This document covers only what is left, and it is written to be
executed cold: each step says what to run, what "done" looks like, and who has
to do it.

## Where the project actually stands

All sixteen work packages have run and returned a verdict. The R4 gate passes
**34/34 records byte-identical**. Two things remain that are not writing:

| track | item | blocked on |
|---|---|---|
| **A** | B1 is VOID — WL-H1 unscored | a routing defect in the gateway |
| **B** | Reproducibility + comparator gaps for a Q1 venue | mostly accounts, one open design question |

Track A is engineering and is fully agent-executable up to the re-sit.
Track B is mostly account actions with a small amount of engineering.

---

## Track A — score B1 (fix the tier pin, then re-sit)

### A0. What is actually wrong

`RESULTS_WAVE4_LIVE_PLANE.md` records the verdict: **SUBSTRATE INADEQUATE**,
WL-H1 void. The gate is not being pessimistic — one knob genuinely does not
behave:

```
pinned to `small` -> served ['small']            exclusive, correct
pinned to `mid`   -> served ['mid', 'small']     LEAKS
```

`routing_moved: false` follows by definition, and `latency_moved: false`
follows **from the leak**: the mid mean (1557.3 ms) is dragged toward small's
(1290.5 ms), leaving 266.8 ms against the 322.6 ms the margin needs. Fix the
leak and both criteria are expected to move together.

The cache knob is a **separate, milder** finding — under-powered rather than
inert: hit rate moves 0.29 against a 0.5 margin. It may or may not need its own
fix; decide that only after A1, because a routing leak inflates cache-hit
measurements too (a request served by the wrong tier is still a miss that got
answered).

### A1. Diagnose the leak — **DONE (session 41). Root cause found.**

**It is not the gateway. It is a race with the operator.**

`PolicyReconciler.Reconcile` pushes each Policy's cache and tier levers to the
AI gateway's admin endpoint — `internal/operator/controllers/gateway_knobs.go`
line 49, `PUT /admin/tenants/{id}/knobs`, called from `policy_controller.go`
line 106. **That is the same endpoint, and the same tenant, the WL-H2 probe
drives.** The failing arm was `jcac`, which is in `OPERATOR_SYSTEMS`, so the
reconciler was live throughout the probe.

Probe and operator therefore fought over one knob. The probe pins `mid`; the
next reconcile writes the Policy's tier back; the probe observes both tiers.
One mechanism explains both "inert" knobs:

- **tier** — pinned `mid`, served `['mid','small']`. Asymmetric because the
  reconciler's own value wins, so pinning *that* tier looks exclusive and
  pinning the other leaks.
- **cache** — the probe sets `cache_size_mb=0`, the reconciler writes the
  Policy's size back, so the hit rate at "0 MB" was 0.71 and the delta 0.29
  rather than the ~1.0 a genuinely disabled cache gives.

**So SUBSTRATE INADEQUATE was very likely a FALSE NEGATIVE.**

The gateway's own logic is correct:
`TestTierPinIsExclusiveAcrossManyRequests` pins each tier, sends the same eight
requests the probe does, and asserts the pin holds for every one. It passes.
The pre-existing `TestTierKnobMovesRoutingAndTelemetry` sends **one** request
per tier, which is why an intermittent leak survived it for so long.

**Eliminated on the way — do not re-test:** multi-replica knob-store split (the
harness does not override `gateway.replicaCount`; chart default is 1);
cache-hit path (a hit never sets `X-PolyForge-Tier`, so it would surface as
`""`); provider fallback or retry in `openai.go` (there is none); the circuit
breaker (wired to `CanaryProvider`, not the chat path).

### A2. Fix it — **DONE (session 41).**

WL-H2 measures the **substrate**, not the arm's controller, so the reconciler
is paused for the probe and restored immediately after (`_operator_paused` in
`cluster_backend.py`). The pause waits for the operator pod to actually be
gone, because a probe racing a terminating reconciler is the same bug in a
smaller window. The probe runs before the load window, so this changes no
scored behaviour — the operator reconciles again before k6 sends a request.

Regression test added. `go test ./internal/ai/gateway/` is green.

**This does NOT re-open WL-H1.** `RESULTS_WAVE4_LIVE_PLANE.md` stands as
committed with its VOID. Re-scoring requires a fresh sitting on real tiers
under a new pre-registration — A3 onward.

### A3. Re-run the gate on real tiers (agent, ~30 min + GPU)

Stand the Kaggle route back up (`docs/WAVE4_FREE_ROUTE.md`; the mechanics are
now automated — see A6) and run `--limit 1` **only**, to reach the gate.

**Two outcomes, decided now:**

- **Gate passes** → proceed to A4.
- **Gate still fails** → report SUBSTRATE INADEQUATE again, amend
  `RESULTS_WAVE4_LIVE_PLANE.md` with the second reading, and **stop**. Do not
  iterate against the gate until it passes; that is fitting the substrate to
  the test.

### A4. Freeze a new pre-registration (agent, ~30 min)

The existing `PREREG_WAVE4_LIVE_PLANE` is frozen and its sitting is void. A
re-sit needs its own registration, committed **and pushed** before the run
(R1/R2), stating: the one changed factor (the gateway fix), that hypotheses,
cells, arms and margins carry over unchanged, and that the previous VOID stands
as recorded.

### A5. Run the matrix (agent, ~3 h wall-clock + GPU)

4 arms × 4 cells, `--workers 1`, one execution, per the prereg's stopping rule.

**Real timings, measured this session — the "2–4 GPU-hours" estimate in
`WAVE4_FREE_ROUTE.md` is about right but the shape matters:** each run deletes
and recreates the kind cluster and reloads four images. A failing run takes
~4 min; a full 30-step run should take ~10. So budget **~2.5–3 h**, and note
the Kaggle kernel serves 5 h per push, so one session is enough **only if
nothing goes wrong**. The runner resumes (`already-valid=N pending=M`), so a
lost tunnel costs the in-flight run, not the matrix.

**Done when:** `valid_runs: 16`, then export → `analysis_wave4_live_plane.py`
extended to score WL-H1 → register → R4 gate green.

### A6. Already built this session — reuse, do not rebuild

- **The Kaggle route is automated.** Kaggle's API returns nothing from a
  running kernel (verified three ways: CLI, `/api/v1/kernels/output` returning
  `log: ""`, and `kernelSessions.get` denying API-key auth). The solution is
  `--announce-url`: the kernel POSTs its tunnel URL outward to a rendezvous the
  orchestrator polls. Push with a preamble injecting `--hours` and
  `--announce-url`, **after** the `from __future__` import.
- **`.dockerignore` exists now.** The gateway image builds in seconds instead
  of shipping 4.9 GB of context.
- **`knob_preflight.json` is preserved** into the evidence dir.
- **Tiers order by capability**, not alphabetically.

---

## Track B — the Q1 gaps that are not B1

`docs/RELEASE_CHECKLIST.md` already enumerates the human-action items. This
track adds only what is needed for a Q1 submission and says who can do each.

### B1. Zenodo deposit — **agent prepares, user publishes**

Tooling exists: `eval/scripts/archive_zenodo.py`.

1. *(agent)* `cd eval && python scripts/archive_zenodo.py` → bundle, manifest
   and `deposit.json` land in `eval/results/zenodo/`. Verify the bundle
   actually contains the records and figures the paper cites.
2. *(user)* zenodo.org → New upload → attach the zip → paste metadata from
   `deposit.json`, filling in the real creator name → Publish → copy the DOI.

**Done when:** a DOI exists and is quoted in the paper's Reproducibility
section. **Blocked on:** a Zenodo account. Nothing else.

### B2. Container images + Helm chart — **agent builds, user pushes**

Steps 2 and 3 of `RELEASE_CHECKLIST.md`. The agent can build and package
locally; pushing to GHCR and registering on Artifact Hub needs the user's
account and tokens.

**Done when:** `helm install polyforge-operator polyforge/polyforge-operator`
works from a clean machine.

### B3. Modern baseline — **needs a decision before any work**

`eval/harness/systems.py` already carries `gptcache` and `gptcache_v2` arms.
"Add a modern baseline" is not actionable until it names one.

**The open question, which the roadmap does NOT decide:** *which* comparator,
and on what grounds it is the fair one. A reviewer's objection is not "you have
no baseline" but "you did not compare against X". Pick X deliberately — from
the related-work set in `docs/RELATED_WORK.md` — and record the reasoning
before implementing, so the choice is not read as chosen for a favourable
result.

Once named, the work is ordinary: a `SystemSpec` in `systems.py`, an arm in the
relevant experiment, a prereg if it changes any scored comparison, then the
usual record → register → R4 loop.

### B4. Azure replay — **SCOPED (session 41). Mostly already satisfied.**

Checked rather than assumed. `RESULTS_TRACE_PARITY.md` already carries **Azure
LLM 2024, 72 x 3 h windows**, 360 per-window rows, with the published arms
replicating **bit-for-bit** (worst relative deviation 0.00e+00 on every column)
and the hypotheses scored. `RESULTS_BUDGET_PARITY.md` scores BP-H1/BP-H2 on the
same trace. So "we have not replayed Azure" is **false** and should not be
conceded to a reviewer.

**What is actually missing, stated precisely:** trace replay is
**simulator-only**. The trace campaigns are driven by
`research/analysis/trace_matrix{,2,_azure}.py` over `research/jcac_sim`. Every
experiment with `backend: cluster` — `live_soak`, `wave4_live_plane`, the
`phase7_*` set — drives **synthetic cells** (`crud_bursty`, `ai_cacheable`,
`tier_mixed`, `joint_stress`), never a real trace. **No real trace has ever
been replayed against the live plane.**

**Whether to close it is a judgement, not a task.** Closing it means driving the
cluster from trace-derived demand instead of the synthetic cells — new load
generation, a new prereg, and a live sitting. That is comparable in size to
WP14. The cheaper and defensible alternative is to state the split plainly in
the paper: traces are replayed in simulation with bit-for-bit replication, and
the live plane demonstrates duration and fault behaviour under synthetic load.
Reviewers object to unstated gaps far more than to stated ones.

**Recommendation:** do not open this before B3 and the B1 re-sit. It is the
most expensive of the remaining items and the least likely to change a
conclusion.

---

## Suggested order

1. **A1–A2** — diagnose and fix the tier pin. No GPU, no accounts, highest
   value: it is the only thing between the project and a scored B1.
2. **B1** — the Zenodo bundle. Agent-preparable today; the user's part is
   ten minutes.
3. **A3–A5** — re-sit B1 once the fix is tested.
4. **B4** — scope the Azure gap (may close for free).
5. **B2**, **B3** — release mechanics and the baseline decision.

## The rules that do not change

Carried from `PUBLICATION_ROADMAP.md` §7 and honoured throughout WP14 and
WP8b:

- A failing hypothesis is the finding. Never widen a margin after seeing data.
- Pre-registrations are committed **and pushed** before the run they govern.
- An interrupted or contaminated run is VOID, disclosed, and never spliced.
- Every record must regenerate byte-identically under `scripts/reproduce.py`.
- If a record drifts under the gate: stop, find the cause, never re-freeze a
  prereg to match an outcome.
