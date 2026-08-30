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

### A1. Diagnose the leak (agent, ~1–2 h, no GPU needed)

The relevant code is `internal/ai/gateway/server.go: pickProvider()`, which
honours the pin **only if `s.tierProviders[tier]` exists** and otherwise falls
through to the router.

**Two hypotheses are already eliminated — do not re-test them:**

- *Multi-replica knob-store split.* `knobStore` is an in-memory `map` per
  process, so a PUT to one replica would not reach others. But the harness does
  **not** override `gateway.replicaCount`, so the chart default of **1** is
  used. Single replica; not this.
- *Cache-hit path.* A cache hit never sets `X-PolyForge-Tier` at all, so a hit
  would surface as `""` in the probe, not `"small"`. Not this either.

**Remaining candidates, in the order worth testing:**

1. **`tierProviders` is missing the `mid` key.** If the map is built from
   `POLYFORGE_EVAL_TIER_BACKENDS` under a different key spelling, every `mid`
   request falls through to the router — which would then have to be picking
   small *sometimes*, so this alone does not explain intermittency. Check how
   `tierProviders` is populated and log its keys at startup.
2. **Provider-level fallback on error or timeout.** The 3B tier answers in
   ~1.5–2.0 s through the tunnel. If a slow or failed `mid` call falls back to
   another provider, the leak would be intermittent and load-dependent —
   which matches the observation. Look for retry/fallback in
   `internal/ai/gateway/openai.go` and the router.
3. **The router overriding the pin for some requests.** `Route()` matches on
   `plan` and `promptChars`; confirm the pinned branch really does return
   before any rule evaluation on every path.

**Reproduce without burning a GPU hour:** `kaggle_tier_server.py --mock`
serves both tiers with tier-shaped delays and no GPU. Point
`POLYFORGE_EVAL_TIER_BACKENDS` at it and run `knob_preflight.py` directly
against a locally-run gateway. That is a minutes-long loop, not a 6-minute
cluster rebuild.

**Done when:** a failing test reproduces the leak deterministically, in
`internal/ai/gateway/*_test.go`, without a cluster.

### A2. Fix it (agent, ~1 h)

Fix the cause A1 identifies. Two rules, both from this project's own history:

- **Do not touch WL-H2's margins.** The gate failing is the finding; widening
  it converts a defect into a false pass. `PREREG_WAVE4_LIVE_PLANE` forbids it
  explicitly.
- **The test comes first and must fail before the fix.** Four defects in this
  path existed only because it had never executed; a regression test is the
  only thing that stops the fifth.

**Done when:** the new test passes, `go test ./internal/ai/...` is green, and
`knob_preflight.py` against the mock reports `routing_moved: true`.

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

### B4. Azure replay — **needs scoping**

WP1 and WP15 already score against Azure and BurstGPT traces
(`RESULTS_TRACE_PARITY.md`, `RESULTS_BUDGET_PARITY.md`). Before treating
"Azure replay" as an open gap, **read those two records and state precisely
what is missing** — a longer window, a different trace slice, or the live
plane rather than the simulator. Writing that sentence is the first task; it
may turn out to be already satisfied, in which case the gap closes for free.

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
