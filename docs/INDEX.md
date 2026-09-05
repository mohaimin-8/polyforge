# Documentation index

One entry point for every document in this repository. Start here rather than
browsing directories.

The repository holds ~180 markdown files. That number is not accidental clutter:
**91 of them are the evidence base** (44 pre-registrations + 47 records), and 39
records are verified byte-identically by `scripts/reproduce.py`. Those cannot be
merged or rewritten without destroying the reproducibility gate. What *was*
clutter — superseded planning documents — now lives in [archive/](archive/).

---

## 1. Start here

| Document | What it is |
|---|---|
| [../README.md](../README.md) | Project overview, quick start |
| [../ARCHITECTURE.md](../ARCHITECTURE.md) | System design: control plane, operator, simulator |
| [REPRODUCE.md](REPRODUCE.md) | **How to verify every published number** — run this first |

## 2. Live status — what is being worked on

| Document | What it is |
|---|---|
| [PUBLICATION_ROADMAP.md](PUBLICATION_ROADMAP.md) | Work packages WP1-WP15: execute order (§2), progress table (§6). Accurate as history; it no longer names what is next |
| [MAIN_WORKING_PATH.md](MAIN_WORKING_PATH.md) | Milestone narrative. Context, not scheduling — the roadmap governs order |
| [COMPLETION_ROADMAP.md](COMPLETION_ROADMAP.md) | **What is left that is not the thesis.** Track A: fix the gateway tier pin and re-sit B1. Track B: Zenodo, images, baseline, Azure scoping |
| [FINAL_ROADMAP.md](FINAL_ROADMAP.md) | **100% of what is left that is not writing.** Absorbs ZERO_COST + COMPLETION Track A/B; adds theory (T1 S3 exact walk), real-trace live sitting, latency semantics, p99/TTFT, cache embedder. Writing held in §9 |
| [ZERO_COST_ROADMAP.md](ZERO_COST_ROADMAP.md) | **The $0 route to a scored B1.** Why the free-route block is our batch-1 tier server, not the P100; phased plan (preflight fix -> batched server -> re-bench -> score); writing HELD until the author says |
| [REMAINING_WORK.md](REMAINING_WORK.md) | Open items and known gaps |
| [WP14_SOAK_RESTORE.md](WP14_SOAK_RESTORE.md) | How to restore the live-soak apparatus |

> If these disagree, `FINAL_ROADMAP.md` wins — it is the current execution
> document (§0 maps the nine named weaknesses to the workstream that closes
> each; §7 is the execute order). `PUBLICATION_ROADMAP.md` remains the record
> of how WP1-WP15 landed.

## 3. Evidence base — `../research/analysis/`

The methodological core. Two file families, both load-bearing:

**`PREREG_*.md` (44)** — pre-registrations. Each was committed **and pushed
before** its run; the push event is the timestamp anchor. They are what makes
the results pre-registered rather than post-hoc. Never edit one after its run.

**`RESULTS_*.md` (43)** — generated records. The gate verifies **39** records
byte-identically: 35 of these, plus `RESULTS.md`, `ADVANCED.md`,
`FAIRNESS_V2.md` and `PHASE7_ORDINAL.md`, which predate the naming
convention. Every one of the 39 must regenerate byte-identically. Records are
written by their generator scripts, never by hand.

```
pip install -r eval/requirements-reproduce.txt   # PINNED: byte-identity is
python scripts/reproduce.py                     # scoped to these versions
```

The same evidence base is published as a static site by
`scripts/build_pages.py` (deployed by `.github/workflows/pages.yml`), so a
reviewer can read a record without cloning. The site's "in gate" badge is read
from `reproduce.py` at build time, and `scripts/test_build_pages.py` fails if a
gated record has no page, if a link is dead, or if two builds differ.

A record is only edited by changing its generator and re-running it. A record
whose verdict was FAIL or INVALID **stands as committed** — superseding runs get
a new record, they do not overwrite the old one.

Supporting analyses in the same directory (not all gated): `THEORY_V2.md`,
`VTC_FAIRNESS.md`, `EFFECT_SIZES.md`, `SENSITIVITY_J.md`, `COORD_GAP*.md`,
`FORECAST_*.md`, `PLANNER_*.md`, `SEMANTIC_CACHE.md`, `OSF_REGISTRATION.md`.

> `SEMANTIC_CACHE.md`, `CACHE_PRECISION.md` and `RESULTS_CACHE_CEILING.md` are
> **not** in the gate: their generators need the LMSYS-Chat-1M dataset, which is
> not vendored. Do not run `semantic_cache_eval.py`, `cache_hit_precision.py` or
> `analysis_cache_ceiling.py` expecting to reproduce the committed file on a
> clean clone.

**Session-42 additions.** `RESULTS_SEPARATION_MT_V3.md` (the eight-tenant
coupled floor, computed at last — S3 FAIL); `RESULTS_WINDOW_CHARACTER.md`
(M3+M2: the cost win and the SLO penalty are the same 45 windows);
`RESULTS_CACHE_CEILING.md` (retrieval contributes only +0.047 to cache
precision — the ceiling is response stochasticity).

> **`RESULTS_SEPARATION_MT_V3.md` reads a committed artifact.** The exact
> ordered walk over 268,435,456 offset vectors takes ~2 h, which cannot sit in
> the gate, so it lives in `separation_mt_v3_walk.json` and the record
> **re-derives rows 1-6 at gate time and diffs them** (S5). A stale or edited
> artifact fails the gate rather than replaying quietly. Regenerate it with
> `python separation_mt_v3_walk.py`.

## 4. Reference

| Document | What it is |
|---|---|
| [DEFENSE_QA.md](DEFENSE_QA.md) | Anticipated examiner questions and answers |
| [RELATED_WORK.md](RELATED_WORK.md) | Literature positioning |
| [SECURITY.md](SECURITY.md) | Threat model, security posture |
| [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) | Pre-release verification |
| [CLICKHOUSE_DESIGN.md](CLICKHOUSE_DESIGN.md) | Telemetry store design |
| [INFERENCE_BENCH.md](INFERENCE_BENCH.md) | Inference benchmark method |
| [V2_README.md](V2_README.md) | v2 simulator notes |

## 5. Runbooks

| Document | What it is |
|---|---|
| [WAVE3_LIVE_RUNBOOK.md](WAVE3_LIVE_RUNBOOK.md) | Live-plane run procedure |
| [WAVE4_FREE_ROUTE.md](WAVE4_FREE_ROUTE.md) | Free-tier route for wave 4 |
| [PHASE7_JCAC_PLAN.md](PHASE7_JCAC_PLAN.md) | Phase 7 JCAC plan |
| [EXECUTION_PLAN.md](EXECUTION_PLAN.md) | Original execution plan (historical) |

## 6. Design records

[adr/](adr/) — 16 architecture decision records.
[milestones/](milestones/) — M0 setup, M1 backend core, M2 observability.

## 7. Archive

[archive/](archive/) — superseded planning documents, kept for provenance and
not maintained: `Q1_ROADMAP.md`, `Q1_EXECUTION_MAP.md` (both superseded by
`PUBLICATION_ROADMAP.md`), `V3_SEGMENT_WIN_PLAN.md`, `WP14_REMEDIATION_ROADMAP.md`
(WP14 is closed), `SESSION_LOG.md`, `SERVICE_TEMPLATE.md`, `SLO.md`.

---

## What is safe to change

| Category | Safe to edit? |
|---|---|
| `PREREG_*.md` after its run | **No** — breaks the timestamp anchor |
| Gated `RESULTS_*.md` by hand | **No** — edit the generator, re-run, verify the gate |
| `docs/` planning and reference | Yes |
| `docs/archive/` | Leave alone; supersede rather than update |
