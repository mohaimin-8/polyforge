"""Shared schema and writer for normalized PolyForge trace events (W25b).

Every ETL pipeline in this directory emits the same five-column table:

    trace_event(timestamp_ms, tenant_id, request_kind, payload_bytes,
                expected_latency_ms)

Parquet is the preferred on-disk format (pyarrow); when pyarrow is not
installed the writer falls back to gzipped CSV with an identical column
set, and the Go replay driver reads the CSV form. Both carry the same
data; parquet is the archival/EDA format, CSV the replay interchange.
"""

from __future__ import annotations

import gzip
import hashlib

import pandas as pd

COLUMNS = [
    "timestamp_ms",
    "tenant_id",
    "request_kind",
    "payload_bytes",
    "expected_latency_ms",
]

REQUEST_KINDS = {"crud_read", "crud_write", "chat", "embed", "agent", "batch"}


def validate(frame: pd.DataFrame) -> pd.DataFrame:
    """Enforce the normalized schema; raise on violations, never coerce
    silently — a malformed trace propagates into every downstream result."""
    missing = [c for c in COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"missing columns: {missing}")
    frame = frame[COLUMNS].copy()
    if frame["timestamp_ms"].isna().any():
        raise ValueError("timestamp_ms contains NaN")
    unknown = set(frame["request_kind"].unique()) - REQUEST_KINDS
    if unknown:
        raise ValueError(f"unknown request kinds: {sorted(unknown)}")
    if (frame["payload_bytes"] < 0).any() or (frame["expected_latency_ms"] < 0).any():
        raise ValueError("negative payload or latency")
    frame = frame.sort_values("timestamp_ms", kind="mergesort").reset_index(drop=True)
    frame["timestamp_ms"] = frame["timestamp_ms"].astype("int64")
    frame["payload_bytes"] = frame["payload_bytes"].astype("int64")
    frame["expected_latency_ms"] = frame["expected_latency_ms"].astype("float64")
    return frame


def write(frame: pd.DataFrame, stem: str) -> str:
    """Write the normalized trace as parquet when pyarrow is available,
    otherwise CSV.gz. Returns the path written."""
    frame = validate(frame)
    try:
        import pyarrow  # noqa: F401

        path = f"{stem}.parquet"
        frame.to_parquet(path, index=False)
    except ImportError:
        path = f"{stem}.csv.gz"
        with gzip.open(path, "wt", newline="") as fh:
            frame.to_csv(fh, index=False)
    return path


def stream_hash(frame: pd.DataFrame) -> str:
    """Deterministic digest of the exact event sequence. The Go replay
    driver computes the same digest over the same rows, which is how
    'same seed, same sequence' is proven across the language boundary."""
    frame = validate(frame)
    digest = hashlib.sha256()
    for row in frame.itertuples(index=False):
        line = f"{row.timestamp_ms},{row.tenant_id},{row.request_kind},{row.payload_bytes},{row.expected_latency_ms:.3f}\n"
        digest.update(line.encode("utf-8"))
    return digest.hexdigest()
