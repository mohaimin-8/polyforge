# Trace EDA — Pipeline Validation (W25b)

**Status: synthetic fixtures only.** This machine has neither the
multi-GB raw downloads nor the gated LMSYS access token, so the numbers
below validate the ETL *pipelines* (shape, determinism, schema), not the
workloads. The real-data EDA replaces this file's tables 1:1 once the
downloads run; the commands are identical minus `--synthetic`.

## Fixture summary (seed 42)

| Pipeline | Events | Tenants | stream_hash (16) | Determinism |
|---|---|---|---|---|
| Azure Functions | 19,265 | 12 | `b3ede67a6b7bfc8c` | re-run identical |
| Alibaba v2018 | 300 | 21 (15.3% unmapped) | `d03a12ebb23f8bd2` | re-run identical |
| LMSYS-Chat-1M | 879 | 16 | `bdc34cb3e565554e` | re-run identical, Go replay driver reproduces the same digest |

## What the synthetic shapes encode (and must be re-verified on real data)

- **Azure burstiness**: per-function Pareto(1.5) base rates → most
  functions idle in most minutes, a few dominating. Real-data check:
  reproduce the Shahrad et al. (ATC '20) finding that ~19% of apps
  average ≥ 1 invocation/min while the tail is orders sparser.
- **Alibaba diurnality**: two-peak arrival density over the day. Real-data
  check: batch utilization rhythm from the cluster-trace-v2018 analyses
  (Lu et al.), plus the unmapped-tenant rate after the DAG-prefix join —
  fixture yields 15.3% by construction; the real join quality must be
  reported, not assumed.
- **LMSYS turn geometry**: geometric(0.45) turns capped at 12 (most
  conversations 1–2 turns), lognormal prompt/reply lengths. Real-data
  check: turn distribution and prompt-length CDF against the dataset
  card; these drive the cache-hit signature the W26 classifier learns.

## Replay validation (fixture-scale)

- `cmd/replay -dry-run` on the LMSYS fixture: `trace_hash` equals the
  Python `stream_hash`; two consecutive runs print identical
  `schedule_hash` (seed 42, 1.0×).
- End-to-end: the test suite replays the cross-language fixture against
  an in-process control plane at 1000× — all events accepted, per-target
  tenant counts recorded (`internal/research/replay`).
- **Pending real-scale**: the 5,000 RPS sustained-replay gate needs the
  real traces and a running deployment; it is a stress-run item tracked
  with the ClickHouse plan in `docs/CLICKHOUSE_DESIGN.md`.
