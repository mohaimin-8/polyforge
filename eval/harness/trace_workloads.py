"""Real-trace workloads for the sim backend (PREREG_TRACE_AUDITED, 2026-10-31).

`burstgpt96_wNN` is window NN of the 96 six-hour BurstGPT v2.0 windows that
PREREG_TRACE2 replayed (two coverage segments x 48 windows). The demand is the
committed projection, *called* rather than reimplemented: per-tenant 600 s
arrival counts of the real trace, scaled by `trace_matrix.scale_factor` to the
matrix's operating point and held piecewise-constant over the control
intervals (`trace_matrix.window_buckets`), at the starts of
`trace_matrix2.window_starts`. It is chat-only, because BurstGPT records LLM
arrivals and nothing else.

Through `workloads.build`, a trace window becomes an ordinary workload: the
tenant mix supplies SLO classes and budgets, the cluster size supplies the
per-tenant replica cap and the cluster limits, and `sim_backend.execute` builds
every arm exactly as on the synthetic matrix. That is why the replay goes
through the harness instead of a bespoke runner such as `trace_parity.py`: an
audited arm (per-size tuning, cluster caps, convergent solver) means the same
thing here as in the FAIR_J..WIDTH campaigns.

The raw trace (56 MB) is not vendored, so the 96 projections are committed
under `trace_cache/` (a few KB each) and read from there. With the raw trace
present, `python -m harness.trace_workloads --build-cache` regenerates them
and `test_trace_workloads.py` checks that they still match.

Import-light on purpose: `config.py` imports this module to validate workload
names, so pandas and the trace code load only when a window is projected.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

PREFIX = "burstgpt96_w"
WINDOWS = 96  # PREREG_TRACE2: 2 coverage segments x 48 windows
CACHE_DIR = Path(__file__).resolve().parent / "trace_cache"
REPO_ROOT = Path(__file__).resolve().parents[2]

# Parsed cache payloads, per process. Plain JSON values only: every call
# builds fresh Demand objects from them, so no run can see another run's
# objects (pooled workers keep this dict across runs).
_PAYLOADS: dict[str, dict] = {}


def names() -> list[str]:
    return [f"{PREFIX}{i:02d}" for i in range(WINDOWS)]


def is_trace(workload: str) -> bool:
    return workload.startswith(PREFIX)


def window_index(workload: str) -> int:
    """The window a trace workload names. Raises on anything malformed."""
    suffix = workload[len(PREFIX):] if is_trace(workload) else ""
    if len(suffix) != 2 or not suffix.isdigit() or int(suffix) >= WINDOWS:
        raise ValueError(f"not a trace workload: {workload!r} "
                         f"(expected {PREFIX}00..{PREFIX}{WINDOWS - 1})")
    return int(suffix)


def cache_path(workload: str) -> Path:
    return CACHE_DIR / f"{workload}.json.gz"


def _trace_modules():
    for sub in ("research/analysis", "research/jcac_sim"):
        path = str(REPO_ROOT / sub)
        if path not in sys.path:
            sys.path.insert(0, path)
    import trace_matrix as tm
    import trace_matrix2 as tm2

    return tm, tm2


def serialise(tenant_ids: list[str], buckets: list[dict], meta: dict) -> bytes:
    """Canonical bytes of one projection: sorted keys and no spaces, so two
    builds of the same window compare byte for byte."""
    payload = {
        "tenant_ids": list(tenant_ids),
        "meta": meta,
        "buckets": [{t: {"rps": dict(d.rps), "crud_base_ms": d.crud_base_ms}
                     for t, d in bucket.items()} for bucket in buckets],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def project_all(events=None) -> dict[str, bytes]:
    """Every window, projected from the raw trace (loaded once)."""
    tm, tm2 = _trace_modules()
    df = tm.load_events() if events is None else events
    segs = tm.segments_of(df)
    starts = tm2.window_starts(segs)
    if len(starts) != WINDOWS:
        raise RuntimeError(f"the trace affords {len(starts)} windows; "
                           f"PREREG_TRACE2 froze {WINDOWS}")
    k = tm.scale_factor(df, segs)
    tenant_ids = [f"t{i:02d}" for i in range(tm.TENANTS)]
    try:
        trace = tm.TRACE.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:  # trace configured outside the repo
        trace = tm.TRACE.name
    out = {}
    for name in names():
        idx = window_index(name)
        buckets = tm.window_buckets(df, starts[idx], k)
        meta = {
            "trace": trace, "window_index": idx, "window_start_s": int(starts[idx]),
            "window_hours": tm.WINDOW_H, "windows": WINDOWS,
            "windows_per_segment": tm2.WINDOWS_PER_SEGMENT, "scale_factor": float(k),
            "rate_bucket_s": tm.RATE_BUCKET_S, "tenants": tm.TENANTS,
            "buckets": len(buckets),
        }
        out[name] = serialise(tenant_ids, buckets, meta)
    return out


def _payload(workload: str) -> dict:
    window_index(workload)
    if workload not in _PAYLOADS:
        path = cache_path(workload)
        if not path.exists():
            raise FileNotFoundError(
                f"{path.name} is missing. With the raw BurstGPT trace present "
                f"(`python research/traces/fetch_burstgpt.py`), build it with "
                f"`python -m harness.trace_workloads --build-cache`.")
        _PAYLOADS[workload] = json.loads(gzip.decompress(path.read_bytes()).decode())
    return _PAYLOADS[workload]


def meta(workload: str) -> dict:
    return dict(_payload(workload)["meta"])


def window(workload: str, steps: int) -> tuple[list[str], list[dict]]:
    """`(tenant_ids, buckets)` for a run of `steps` scored steps, i.e. the
    window's first `steps + 1` buckets (the engine scores against the next
    bucket, so the last one is never scored, as in `workloads.build`)."""
    from model import Demand  # research/jcac_sim, on the harness path

    payload = _payload(workload)
    stored = payload["buckets"]
    if steps + 1 > len(stored):
        raise ValueError(f"{workload} has {len(stored)} buckets; "
                         f"a {steps}-step run needs {steps + 1}")
    buckets = [{t: Demand(rps=dict(f["rps"]), crud_base_ms=f["crud_base_ms"])
                for t, f in bucket.items()} for bucket in stored[: steps + 1]]
    return list(payload["tenant_ids"]), buckets


def build_cache() -> list[Path]:
    """Regenerate every committed projection from the raw trace."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for name, blob in project_all().items():
        path = cache_path(name)
        path.write_bytes(gzip.compress(blob, 9, mtime=0))
        written.append(path)
    return written


if __name__ == "__main__":
    if sys.argv[1:] == ["--build-cache"]:
        paths = build_cache()
        print(f"wrote {len(paths)} windows to {CACHE_DIR} "
              f"({sum(p.stat().st_size for p in paths)} bytes)")
    else:
        raise SystemExit("usage: python -m harness.trace_workloads --build-cache")
