# Trace Acquisition & Normalization (W25b)

Three public traces cover the workload spectrum the thesis studies:

| Trace | Covers | Raw format | Source |
|---|---|---|---|
| Azure Functions 2019 | bursty serverless CRUD | gzipped per-day CSVs, per-minute counts | github.com/Azure/AzurePublicDataset |
| Alibaba cluster v2018 | batch/steady infra | headerless multi-table CSV, cross-table joins | github.com/alibaba/clusterdata |
| LMSYS-Chat-1M | real LLM chat | parquet on HuggingFace (gated: accept license first) | huggingface.co/datasets/lmsys/lmsys-chat-1m |

Every pipeline normalizes to the same table:

```
trace_event(timestamp_ms, tenant_id, request_kind, payload_bytes, expected_latency_ms)
```

written as parquet when pyarrow is installed, else CSV.gz (same columns —
the Go replay driver consumes the CSV form). Each script prints a
`stream_hash` — SHA-256 over the canonical row encoding — and
`internal/research/replay` computes the identical digest in Go, which is
how "same seed → same exact event sequence" is proven across the
language boundary.

## Synthetic mode

```
python etl_azure_functions.py --synthetic --seed 42 --out out/azure_synth
python etl_alibaba_v2018.py   --synthetic --seed 42 --out out/alibaba_synth
python etl_lmsys_chat1m.py    --synthetic --seed 42 --out out/lmsys_synth
```

`--synthetic` fabricates input in the *raw format of the real dataset*
(heavy-tailed Azure invocation counts, diurnal Alibaba arrivals, LMSYS
turn-count geometry) and pushes it through the identical `normalize()`
path. It exists so the transform code, the schema contract, and the replay
driver are all exercised and CI-testable before the multi-GB downloads
happen. Fixture statistics are **not** research results; every figure in
the paper must come from the real traces.

## Real-data runs

```
# Azure (per-day invocation files, ~1GB each)
python etl_azure_functions.py --input invocations_per_function_md.anon.d01.csv --out out/azure_d01

# Alibaba (270GB full trace; for fixture-scale extracts pandas suffices,
# for the full files do the join in DuckDB first):
#   duckdb -c "COPY (SELECT bt.* FROM read_csv('batch_task.csv', names=[...]) bt) TO 'batch_task_slim.csv'"
python etl_alibaba_v2018.py --batch-task batch_task.csv --container-meta container_meta.csv --out out/alibaba

# LMSYS (needs accepted license + huggingface-cli login)
python etl_lmsys_chat1m.py --input "lmsys-chat-1m/data/*.parquet" --out out/lmsys
```

Documented proxies (cited wherever the traces appear in the paper):
Azure carries no payload/duration per invocation → seeded lognormals;
Alibaba has no request payloads → `plan_mem` proxy, task makespan as
latency; LMSYS has no per-turn timestamps or user ids → seeded think-time
spacing, conversation-hash tenant buckets.

## Replay

```
go run ./cmd/replay -trace research/traces/out/lmsys_synth.csv.gz -dry-run
go run ./cmd/replay -trace ... -mix mix.json -speed 2.0
```

`-dry-run` prints `trace_hash` (must equal the ETL's `stream_hash`) and
`schedule_hash` (covers tenant mapping + due times; identical across runs
with the same seed/speed/mix). The mix file maps trace tenants onto
PolyForge tenants by weight; one source tenant never splits across
targets. Replay lag is reported, never silently absorbed.
