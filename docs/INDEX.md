# Documentation index

One entry point for every document in this repository. Start here rather than
browsing directories.

The repository holds ~180 markdown files. That number is not accidental clutter:
**72 of them are the evidence base** (38 pre-registrations + 34 records), and 31
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
| [PUBLICATION_ROADMAP.md](PUBLICATION_ROADMAP.md) | **Source of truth.** Work packages, execute order (§2), progress table (§6) |
| [MAIN_WORKING_PATH.md](MAIN_WORKING_PATH.md) | Milestone narrative. Context, not scheduling — the roadmap governs order |
| [COMPLETION_ROADMAP.md](COMPLETION_ROADMAP.md) | **What is left that is not the thesis.** Track A: fix the gateway tier pin and re-sit B1. Track B: Zenodo, images, baseline, Azure scoping |
| [REMAINING_WORK.md](REMAINING_WORK.md) | Open items and known gaps |
| [WP14_SOAK_RESTORE.md](WP14_SOAK_RESTORE.md) | How to restore the live-soak apparatus |

> If these disagree, `PUBLICATION_ROADMAP.md` wins.

## 3. Evidence base — `../research/analysis/`

The methodological core. Two file families, both load-bearing:

**`PREREG_*.md` (38)** — pre-registrations. Each was committed **and pushed
before** its run; the push event is the timestamp anchor. They are what makes
the results pre-registered rather than post-hoc. Never edit one after its run.

**`RESULTS_*.md` (34)** — generated records. **31 are in the reproduction gate**
and must regenerate byte-identically. They are written by their generator
scripts, never by hand.

```
python scripts/reproduce.py      # regenerates and diffs every gated record
```

A record is only edited by changing its generator and re-running it. A record
whose verdict was FAIL or INVALID **stands as committed** — superseding runs get
a new record, they do not overwrite the old one.

Supporting analyses in the same directory (not all gated): `THEORY_V2.md`,
`VTC_FAIRNESS.md`, `EFFECT_SIZES.md`, `SENSITIVITY_J.md`, `COORD_GAP*.md`,
`FORECAST_*.md`, `PLANNER_*.md`, `SEMANTIC_CACHE.md`, `OSF_REGISTRATION.md`.

> `SEMANTIC_CACHE.md` is **not** in the gate: its generator needs the LMSYS-Chat-1M
> dataset, which is not vendored. Do not run `semantic_cache_eval.py` expecting
> to reproduce the committed file.

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
