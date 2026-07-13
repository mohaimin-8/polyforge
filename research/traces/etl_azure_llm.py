"""ETL: Azure LLM inference traces (2024 release) -> normalized trace_event.

Second real LLM demand trace, for external validity (DEFENSE_QA.md #12
named this the highest-value addition; RELATED_WORK descoped Azure
*Functions* — FaaS, not LLM — but these are LLM inference request logs).

Real input: https://github.com/Azure/AzurePublicDataset — the 2024
release assets `AzureLLMInferenceTrace_{conv,code}_1week.csv` (May 10-19
2024; the dataset behind DynamoLLM, HPCA 2025, CC-BY): production request
arrivals from two Azure LLM services, a conversation workload and a
code-completion workload. Public CSV header:
`TIMESTAMP,ContextTokens,GeneratedTokens`, ISO timestamps. Downloaded
into `data/azure-llm/` (gitignored, like every raw trace) when absent.

AMENDMENT (declared 2026-07-13 before any ablation ran): the first
committed revision fetched the 2023 in-repo files, which turn out to hold
only ~1 hour of traffic — one hourly bucket, unusable for the frozen
forecast protocol. The 2024 release assets are the one-week dataset the
protocol was written for; same schema, same mapping, same constants.
Nothing about the analysis changed — only which Azure release actually
contains a week.

Mapping to the normalized schema — deliberately identical constants to
`etl_burstgpt.py` so the two real traces are processed in lockstep:

- TIMESTAMP -> timestamp_ms since trace start.
- workload  -> tenant_id ("conv" / "code"): the two services are two real,
  independent demand streams; no synthetic tenant split is invented.
- every row is an LLM completion -> request_kind = "chat".
- expected_latency_ms = LATENCY_PREFILL_MS + total_tokens *
  LATENCY_MS_PER_TOKEN; payload_bytes = context_tokens *
  PAYLOAD_BYTES_PER_TOKEN (same committed constants, same clips).

    python etl_azure_llm.py        # download if needed, write out/azure_llm_2024.csv.gz
"""

from __future__ import annotations

import csv
import gzip
import urllib.request
from datetime import datetime
from pathlib import Path

from etl_burstgpt import LATENCY_MS_PER_TOKEN, LATENCY_PREFILL_MS, PAYLOAD_BYTES_PER_TOKEN

HERE = Path(__file__).resolve().parent
RAW_DIR = HERE / "data" / "azure-llm"
OUT = HERE / "out" / "azure_llm_2024.csv.gz"
BASE = ("https://github.com/Azure/AzurePublicDataset/releases/download/"
        "dataset-llm-2024/AzureLLMInferenceTrace_")
WORKLOADS = ("conv", "code")
FIELDS = ("timestamp_ms", "tenant_id", "request_kind",
          "expected_latency_ms", "payload_bytes")


def fetch(workload: str) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"AzureLLMInferenceTrace_{workload}_1week.csv"
    if not path.exists():
        url = BASE + workload + "_1week.csv"
        print(f"downloading {url}")
        urllib.request.urlretrieve(url, path)
    return path


def parse_ts(raw: str) -> datetime:
    # "2023-11-16 18:15:46.6805900" — 7 fractional digits; fromisoformat
    # takes at most 6.
    head, _, frac = raw.partition(".")
    return datetime.fromisoformat(f"{head}.{frac[:6]}" if frac else head)


def main() -> None:
    rows = []
    for workload in WORKLOADS:
        with open(fetch(workload), newline="", encoding="utf-8") as f:
            for rec in csv.DictReader(f):
                ts = parse_ts(rec["TIMESTAMP"])
                ctx = int(float(rec["ContextTokens"]))
                gen = int(float(rec["GeneratedTokens"]))
                rows.append((ts, workload, ctx, gen))
    rows.sort(key=lambda r: r[0])
    t0 = rows[0][0]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for ts, workload, ctx, gen in rows:
            total = ctx + gen
            writer.writerow({
                "timestamp_ms": int((ts - t0).total_seconds() * 1000),
                "tenant_id": workload,
                "request_kind": "chat",
                "expected_latency_ms": round(
                    min(LATENCY_PREFILL_MS + total * LATENCY_MS_PER_TOKEN, 600_000.0), 3),
                "payload_bytes": max(32, min(ctx * PAYLOAD_BYTES_PER_TOKEN, 4_000_000)),
            })
    span_h = (rows[-1][0] - rows[0][0]).total_seconds() / 3600
    print(f"wrote {OUT}: {len(rows)} requests, {span_h:.1f} h span, "
          f"tenants {WORKLOADS}")


if __name__ == "__main__":
    main()
