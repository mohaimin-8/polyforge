"""LMSYS-Chat-1M first user turns as tenant streams, with a disk cache of their
vectors (programme 2, WP6 steps 1-2).

Prompts are read and embedded locally and never written out: the vector cache
under data/ (git-ignored) holds vectors, byte lengths, the gateway doc ID and
class flags, not text. The dataset licence forbids redistributing the text.

File order is NOT time order: llama-2-13b-chat (released July 2023) has the
same share (0.029-0.031) in every tenth of the file order, so the files are
shuffled and a contiguous chunk of the file order is a random sample.
"""

from __future__ import annotations

import glob
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from engine import cache_key_text, doc_id, go_normalize, tie_key

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
LMSYS_GLOB = str(REPO / "research" / "traces" / "data" / "lmsys-chat-1m" / "data" / "*.parquet")
DATA = HERE / "data"
ENCODER = "sentence-transformers/all-MiniLM-L6-v2"
KEY_FORMATS = ("gateway", "raw")

# Workload classes. `code` is a stated, imprecise content rule (code syntax, a
# programming language or tool, or the words code/coding/programming); it
# mirrors the code/conversation split of the Azure LLM traces in WP8's
# population. `english` uses the dataset's own language column.
CODE_RE = re.compile(
    r"```|#include\s*<|\bdef\s+\w+\s*\(|\bclass\s+\w+\s*[:({]|\bfunction\s*\w*\s*\("
    r"|\bconsole\.log\(|\bprint\(|\bimport\s+[\w.]+|\bpublic\s+static\b|</?(?:div|html|script|body)\b"
    r"|\b(?:python|javascript|typescript|java|c\+\+|c#|golang|rust|kotlin|php|ruby|perl|sql|html|css"
    r"|bash|powershell|regex|json|yaml|pandas|numpy|pytorch|tensorflow|react|django|flask|node\.?js"
    r"|code|coding|programming|algorithm|compile|debug)\b",
    re.IGNORECASE)
SQL_RE = re.compile(r"\bSELECT\b[\s\S]{1,200}\bFROM\b")
CLASSES = ("all", "conversation", "code", "english", "other_language")


def key_text(content: str, key_format: str) -> str:
    """The text the cache embeds: the gateway's cacheKeyText of a one-message
    request ("user: <prompt>\\n"), or the bare prompt (the old study)."""
    if key_format == "gateway":
        return cache_key_text([("user", content)])
    if key_format == "raw":
        return content
    raise ValueError(f"unknown key format {key_format!r}; have {KEY_FORMATS}")


def is_code(content: str) -> bool:
    return bool(CODE_RE.search(content) or SQL_RE.search(content))


def _first_turn(convo) -> tuple[str, str] | None:
    """(prompt, completion) of the first user message: the completion is the
    next message if it is the assistant's, else empty (nothing to cache)."""
    for i, msg in enumerate(convo):
        if msg.get("role") != "user":
            continue
        content = msg.get("content") or ""
        if not content:
            return None
        nxt = convo[i + 1] if i + 1 < len(convo) else {}
        reply = (nxt.get("content") or "") if nxt.get("role") == "assistant" else ""
        return content, reply
    return None


def first_turns(path: str, key_format: str, limit: int | None = None) -> tuple[pd.DataFrame, list[str]]:
    """One row per conversation with a non-empty first user turn, in file
    order: metadata (no text) and the key texts to embed."""
    rows, texts, row_no = [], [], -1
    pf = pq.ParquetFile(path)
    for batch in pf.iter_batches(batch_size=4096, columns=["conversation", "language"]):
        for convo, lang in zip(batch.column("conversation").to_pylist(),
                               batch.column("language").to_pylist()):
            row_no += 1
            turn = _first_turn(convo or [])
            if turn is None:
                continue
            content, reply = turn
            key = key_text(content, key_format)
            rows.append({"row": row_no, "language": str(lang),
                         "prompt_bytes": len(content.encode("utf-8")),
                         "key_bytes": len(key.encode("utf-8")),
                         "completion_bytes": len(reply.encode("utf-8")),
                         "code": is_code(content), "doc": doc_id(key)})
            texts.append(key)
            if limit is not None and len(texts) >= limit:
                return pd.DataFrame(rows), texts
    return pd.DataFrame(rows), texts


class Encoder:
    """The fixed open encoder (MiniLM-L6-v2, 384-d), vectors normalised as the
    gateway's embed.Normalize does and stored as float32."""

    def __init__(self, threads: int | None = None, batch_size: int = 128) -> None:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")  # the model is cached locally
        import torch
        from sentence_transformers import SentenceTransformer

        if threads:
            torch.set_num_threads(threads)
        self.batch_size = batch_size
        self.model = SentenceTransformer(ENCODER, device="cpu")
        self.dims = int(self.model.get_sentence_embedding_dimension())
        self.max_seq_length = int(self.model.max_seq_length)

    def encode(self, texts: list[str]) -> np.ndarray:
        vecs = self.model.encode(texts, batch_size=self.batch_size, show_progress_bar=False,
                                 convert_to_numpy=True)
        return go_normalize(vecs)


@dataclass(frozen=True)
class Stream:
    meta: pd.DataFrame      # one row per first turn, global file order
    vectors: np.ndarray     # float32 (n, dims)
    embed_seconds: float    # time spent embedding in this call (0 if all cached)
    embedded: int           # prompts embedded in this call


def _cache_paths(key_format: str, src: str, n: int | None) -> tuple[Path, Path, Path]:
    stem = Path(src).stem + (f".first{n}" if n is not None else "")
    d = DATA / "vectors" / key_format
    return d / f"{stem}.f32.npy", d / f"{stem}.meta.parquet", d / f"{stem}.json"


def _source_id(src: str) -> dict:
    st = os.stat(src)
    return {"source": Path(src).name, "size": st.st_size, "mtime": int(st.st_mtime), "encoder": ENCODER}


def _load_cached(paths, src: str):
    vec_p, meta_p, side_p = paths
    if not (vec_p.exists() and meta_p.exists() and side_p.exists()):
        return None
    side = json.loads(side_p.read_text(encoding="utf-8"))
    if {k: side.get(k) for k in _source_id(src)} != _source_id(src):
        return None
    vecs = np.load(vec_p, allow_pickle=False)
    meta = pd.read_parquet(meta_p)
    return (meta, vecs) if len(meta) == len(vecs) == side.get("rows") else None


def _embed_file(src: str, key_format: str, limit: int | None, encoder_factory, paths):
    meta, texts = first_turns(src, key_format, limit)
    t0 = time.perf_counter()
    vecs = encoder_factory().encode(texts) if texts else np.zeros((0, 384), np.float32)
    seconds = time.perf_counter() - t0
    paths[0].parent.mkdir(parents=True, exist_ok=True)
    np.save(paths[0], vecs, allow_pickle=False)
    meta.to_parquet(paths[1], index=False)
    side = {**_source_id(src), "rows": len(meta), "key_format": key_format, "limit": limit,
            "embed_seconds": seconds}
    paths[2].write_text(json.dumps(side, indent=1), encoding="utf-8")
    return meta, vecs, seconds


def load_stream(key_format: str, limit: int | None, encoder_factory, pattern: str = LMSYS_GLOB) -> Stream:
    """First turns of all parquet files in sorted (file) order, embedded once
    and cached per file; `limit` keeps only the first `limit` first turns."""
    paths = sorted(glob.glob(pattern))
    if not paths:
        raise SystemExit(f"no LMSYS parquet matched {pattern}")
    metas, vecs, seconds, embedded = [], [], 0.0, 0
    remaining = limit
    for src in paths:
        cache = _cache_paths(key_format, src, remaining)
        hit = _load_cached(cache, src)
        if hit is None:
            meta, v, s = _embed_file(src, key_format, remaining, encoder_factory, cache)
            seconds, embedded = seconds + s, embedded + len(meta)
        else:
            meta, v = hit
        metas.append(meta.assign(file=Path(src).stem))
        vecs.append(v)
        if remaining is not None:
            remaining -= len(meta)
            if remaining <= 0:
                break
    meta = pd.concat(metas, ignore_index=True)
    return Stream(meta=meta, vectors=np.concatenate(vecs), embed_seconds=seconds, embedded=embedded)


def class_masks(meta: pd.DataFrame) -> dict[str, np.ndarray]:
    code = meta["code"].to_numpy(dtype=bool)
    english = (meta["language"] == "English").to_numpy()
    return {"all": np.ones(len(meta), dtype=bool), "conversation": ~code, "code": code,
            "english": english, "other_language": ~english}


def tenant_streams(mask: np.ndarray, length: int) -> list[np.ndarray]:
    """Contiguous chunks of `length` prompts of one class, in file order; the
    remainder that does not fill a chunk is dropped."""
    idx = np.flatnonzero(mask)
    return [idx[s:s + length] for s in range(0, len(idx) - length + 1, length)]


def doc_tiekeys(docs) -> np.ndarray:
    return np.array([tie_key(d) for d in docs], dtype=np.uint64)
