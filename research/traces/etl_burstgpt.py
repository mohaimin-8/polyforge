"""ETL: BurstGPT LLM-serving trace -> normalized trace_event (v2 Phase 3a).

Real input (https://github.com/HPMLL/BurstGPT, 10.31M real Azure OpenAI
requests over 213 days; paper https://arxiv.org/abs/2401.17644). The public
CSV header is `Timestamp,Model,Request tokens,Response tokens,Total
tokens,Log Type`, timestamps in seconds from trace start. Mapping to the
normalized schema (V2_README Phase 3a):

- Timestamp        -> demand series (timestamp_ms = Timestamp * 1000).
- Model            -> real tier-demand mix: every row is an LLM chat
                      completion, so request_kind = "chat"; the GPT-3.5 vs
                      GPT-4 split is preserved in the token-derived latency
                      (GPT-4 rows are heavier), which is what actually
                      drives the controller's tier choice.
- Request/Response tokens -> work units: expected_latency_ms is modeled
                      from total tokens (prefill + decode), payload_bytes
                      from request tokens. Both are affine in tokens with
                      committed constants — no distribution is invented
                      where the trace already carries the number.
- session / tenant -> cache locality. The public release has no session
                      column; when one is present (`--session-col`) it maps
                      to tenant_id, else tenants are assigned deterministically
                      by hashing coarse time so a fixed tenant count shares
                      the real arrival process (documented approximation).

Usage:
    python etl_burstgpt.py --input BurstGPT_1.csv --out burstgpt_full
    python etl_burstgpt.py --synthetic --seed 42 --out burstgpt_synth

--synthetic emits raw rows in the *same columns the real CSV uses* and
pushes them through the identical normalize() path, so the transform is
exercised end to end before the multi-GB download. The generator
reproduces BurstGPT's two headline properties (Wang et al., 2024):
bursty arrivals with a genuine daily period, and a heavy-tailed request/
response token distribution with a GPT-3.5-majority model mix.
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

import common

# LLM serving latency is dominated by decode: ~prefill + tokens*per-token.
# Constants are order-of-magnitude serving figures (a few ms/token decode
# on a mid tier), committed here so the mapping is checkable; the real
# calibration is Phase 6's job. GPT-4 rows are heavier purely through
# their larger token counts, not a hand-set multiplier.
LATENCY_PREFILL_MS = 60.0
LATENCY_MS_PER_TOKEN = 8.0
PAYLOAD_BYTES_PER_TOKEN = 4  # ~4 bytes/token, BPE order of magnitude

DEFAULT_TENANTS = 8
SECONDS_PER_DAY = 86_400


def synthetic_raw(seed: int, hours: int = 168, base_rps: float = 0.35) -> pd.DataFrame:
    """Raw rows in BurstGPT's own column layout, over `hours` (default 7
    days). Arrivals follow a non-homogeneous Poisson process with a daily
    sinusoid plus a sharper mid-day burst — a genuine 24-hour period the
    seasonal forecaster detects once the demand is bucketed hourly (its
    8–48-bucket lag window). Tokens are lognormal (heavy tailed); the model
    mix is ~70% GPT-3.5 / 30% GPT-4 (trace proportions). Vectorized: the
    per-second Poisson counts are drawn in one call, then expanded.
    """
    rng = np.random.default_rng(seed)
    seconds = np.arange(hours * 3600)
    tod = (seconds % SECONDS_PER_DAY) / SECONDS_PER_DAY
    diurnal = 1.0 + 0.6 * np.sin(2 * np.pi * tod - np.pi / 2)
    burst = np.where((tod > 0.45) & (tod < 0.6), 1.8, 1.0)
    lam = base_rps * diurnal * burst  # per-second arrival rate (>= 0)
    counts = rng.poisson(lam)
    ts = np.repeat(seconds, counts)
    n = ts.size

    is_gpt4 = rng.random(n) < 0.30
    model = np.where(is_gpt4, "GPT-4", "ChatGPT")
    req = np.maximum(1, rng.lognormal(np.where(is_gpt4, 6.0, 5.4), 0.9).astype("int64"))
    resp = np.maximum(1, rng.lognormal(np.where(is_gpt4, 5.6, 5.0), 1.0).astype("int64"))
    return pd.DataFrame({
        "Timestamp": ts, "Model": model,
        "Request tokens": req, "Response tokens": resp,
        "Total tokens": req + resp, "Log Type": "Conversation log",
    })


def normalize(raw: pd.DataFrame, seed: int, tenants: int = DEFAULT_TENANTS,
              session_col: str | None = None) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ts_s = raw["Timestamp"].to_numpy(dtype="float64")
    req = raw["Request tokens"].to_numpy(dtype="float64")
    total = raw["Total tokens"].to_numpy(dtype="float64")

    if session_col and session_col in raw.columns:
        # Real session/tenant identity when the release carries it.
        sess = raw[session_col].astype(str).to_numpy()
        tenant_ids = pd.factorize(sess)[0] % tenants
    else:
        # No session column: deterministic assignment by coarse arrival
        # time so a fixed tenant set shares the real arrival process.
        tenant_ids = (np.floor(ts_s / 60.0).astype("int64")
                      + rng.integers(0, tenants, len(raw))) % tenants

    frame = pd.DataFrame({
        "timestamp_ms": (ts_s * 1000.0).astype("int64"),
        "tenant_id": [f"t{int(i):02d}" for i in tenant_ids],
        "request_kind": "chat",
        "payload_bytes": (req * PAYLOAD_BYTES_PER_TOKEN).astype("int64").clip(32, 4_000_000),
        "expected_latency_ms": (LATENCY_PREFILL_MS + total * LATENCY_MS_PER_TOKEN)
        .round(3).clip(0.5, 600_000),
    })
    return common.validate(frame)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", help="real BurstGPT CSV")
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--tenants", type=int, default=DEFAULT_TENANTS)
    parser.add_argument("--session-col", default=None,
                        help="column to map to tenant_id if the release has one")
    parser.add_argument("--out", required=True, help="output stem (no extension)")
    args = parser.parse_args()

    if args.synthetic:
        raw = synthetic_raw(args.seed)
    elif args.input:
        raw = pd.read_csv(args.input)
    else:
        parser.error("provide --input or --synthetic")

    frame = normalize(raw, args.seed, tenants=args.tenants, session_col=args.session_col)
    path = common.write(frame, args.out)
    print(f"{path}: {len(frame)} events, {frame['tenant_id'].nunique()} tenants, "
          f"hash {common.stream_hash(frame)[:16]}")


if __name__ == "__main__":
    main()
