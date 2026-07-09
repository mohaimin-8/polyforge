"""ETL: LMSYS-Chat-1M -> normalized trace_event.

Real input (https://huggingface.co/datasets/lmsys/lmsys-chat-1m, gated —
accept the license, then `huggingface-cli download lmsys/lmsys-chat-1m`):
parquet with one row per conversation:

    conversation_id, model, conversation (list of {role, content}),
    turn, language, openai_moderation, redacted, tstamp

Flattening: each *user* turn becomes one `chat` event; assistant turns
carry the response cost, so their content length feeds the latency proxy
of the preceding user turn. The dataset is anonymized with no user ids;
tenants are assigned by hashing conversation_id into --tenants buckets
(stable across runs — the same conversation always lands in the same
tenant). Turn timestamps inside a conversation are spaced by a seeded
think-time lognormal because the dataset stores one tstamp per
conversation, not per turn — both proxies are documented limitations.

Proxies:
  payload_bytes       <- UTF-8 length of the user turn
  expected_latency_ms <- assistant response length / 30 tokens-per-sec
                         serving rate * 4 chars-per-token (INFERENCE_BENCH
                         local-tier figures), floor 80ms TTFT.

Usage:
    python etl_lmsys_chat1m.py --input lmsys-chat-1m/*.parquet --out lmsys
    python etl_lmsys_chat1m.py --synthetic --seed 42 --out lmsys_synth
"""

from __future__ import annotations

import argparse
import glob
import hashlib

import numpy as np
import pandas as pd

import common

CHARS_PER_TOKEN = 4.0
LOCAL_TOKENS_PER_SEC = 30.0
TTFT_FLOOR_MS = 80.0


def synthetic_raw(seed: int, conversations: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    models = ["vicuna-13b", "llama-2-7b-chat", "gpt-3.5-turbo", "koala-13b"]
    rows = []
    for i in range(conversations):
        turns = int(np.clip(rng.geometric(0.45), 1, 12))  # LMSYS: most chats 1-2 turns
        convo = []
        for _ in range(turns):
            prompt_len = int(np.clip(rng.lognormal(5.2, 1.0), 8, 12_000))
            reply_len = int(np.clip(rng.lognormal(6.3, 0.8), 16, 16_000))
            convo.append({"role": "user", "content": "u" * prompt_len})
            convo.append({"role": "assistant", "content": "a" * reply_len})
        rows.append({
            "conversation_id": f"conv{i:06d}",
            "model": models[int(rng.integers(len(models)))],
            "conversation": convo,
            "turn": turns,
            "language": "English",
            "tstamp": 1_680_000_000 + int(rng.uniform(0, 6 * 3600)),
        })
    return pd.DataFrame(rows)


def tenant_bucket(conversation_id: str, tenants: int) -> str:
    digest = hashlib.sha256(str(conversation_id).encode("utf-8")).digest()
    return f"lmsys{int.from_bytes(digest[:4], 'big') % tenants:02d}"


def normalize(raw: pd.DataFrame, seed: int, tenants: int = 16) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    events = []
    for row in raw.itertuples(index=False):
        tenant = tenant_bucket(row.conversation_id, tenants)
        base_ms = int(row.tstamp) * 1000
        offset_ms = 0.0
        convo = list(row.conversation)
        for i, message in enumerate(convo):
            if message.get("role") != "user":
                continue
            content = message.get("content") or ""
            reply = ""
            if i + 1 < len(convo) and convo[i + 1].get("role") == "assistant":
                reply = convo[i + 1].get("content") or ""
            latency = max(
                TTFT_FLOOR_MS,
                len(reply) / CHARS_PER_TOKEN / LOCAL_TOKENS_PER_SEC * 1000.0,
            )
            events.append((
                int(base_ms + offset_ms),
                tenant,
                "chat",
                len(content.encode("utf-8")),
                round(latency, 3),
            ))
            # Seeded think time between turns; the dataset records only one
            # timestamp per conversation.
            offset_ms += latency + rng.lognormal(8.5, 0.7)  # median ~4.9s
    frame = pd.DataFrame(events, columns=common.COLUMNS)
    return common.validate(frame)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", help="parquet file or glob")
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--tenants", type=int, default=16)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    if args.synthetic:
        raw = synthetic_raw(args.seed)
    elif args.input:
        paths = sorted(glob.glob(args.input))
        if not paths:
            parser.error(f"no files match {args.input}")
        raw = pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)
    else:
        parser.error("provide --input or --synthetic")

    frame = normalize(raw, args.seed, args.tenants)
    path = common.write(frame, args.out)
    print(f"{path}: {len(frame)} events, {frame['tenant_id'].nunique()} tenants, hash {common.stream_hash(frame)[:16]}")


if __name__ == "__main__":
    main()
