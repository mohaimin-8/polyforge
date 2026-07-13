"""ETL: Azure LLM inference traces (2023) -> normalized trace_event.

Second real LLM demand trace, for external validity (DEFENSE_QA.md #12
named this the highest-value addition; RELATED_WORK descoped Azure
*Functions* — FaaS, not LLM — but these are LLM inference request logs).

Real input: https://github.com/Azure/AzurePublicDataset
(`data/AzureLLMInferenceTrace_conv.csv`, `_code.csv`) — one week of
production request arrivals (Nov 2023) from two Azure OpenAI services:
a conversation workload and a code-completion workload. Public CSV
header: `TIMESTAMP,ContextTokens,GeneratedTokens`, ISO timestamps.
Both files together are ~1 MB; this script downloads them into
`data/azure-llm/` (gitignored, like every raw trace) when absent.

Mapping to the normalized schema — deliberately identical constants to
`etl_burstgpt.py` so the two real traces are processed in lockstep:

- TIMESTAMP -> timestamp_ms since trace start.
- workload  -> tenant_id ("conv" / "code"): the two services are two real,
  independent demand streams; no synthetic tenant split is invented.
- every row is an LLM completion -> request_kind = "chat".
- expected_latency_ms = LATENCY_PREFILL_MS + total_tokens *
  LATENCY_MS_PER_TOKEN; payload_bytes = context_tokens *
  PAYLOAD_BYTES_PER_TOKEN (same committed constants, same clips).

    python etl_azure_llm.py        # download if needed, write out/azure_llm_2023.csv.gz
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
OUT = HERE / "out" / "azure_llm_2023.csv.gz"
BASE = ("https://raw.githubusercontent.com/Azure/AzurePublicDataset/master/"
        "data/AzureLLMInferenceTrace_")
WORKLOADS = ("conv", "code")
FIELDS = ("timestamp_ms", "tenant_id", "request_kind",
          "expected_latency_ms", "payload_bytes")


def fetch(workload: str) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"AzureLLMInferenceTrace_{workload}.csv"
    if not path.exists():
        url = BASE + workload + ".csv"
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
