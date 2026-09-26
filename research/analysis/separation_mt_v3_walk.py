#!/usr/bin/env python3
"""WP13 step 3 — run the exact ordered walk and commit it as an artifact.

`RESULTS_SEPARATION_MT_V2.md` reported S3 NOT EVALUABLE because the published
eight-tenant cell needs 16^7 = 268,435,456 offset vectors. It is evaluable:
`guarantee.coupled_floor_incremental_batched` walks the SAME ordered
enumeration with the offset-vector axis vectorised (the tenant sweep stays
sequential, because S2 proved that order matters). Measured on this machine:

    n=5     65,536 vectors    1.3 s
    n=6  1,048,576 vectors   29.9 s
    n=7 16,777,216 vectors  499.9 s   ->  ~33,500 vectors/s
    n=8 268,435,456 vectors  ~133 min  (extrapolated before running)

Two hours is affordable once and NOT affordable inside `scripts/reproduce.py`,
which re-runs every analysis on every gate. So the expensive rows are computed
here, once, into a committed JSON; `analysis_separation_mt_v3.py` reads it and
**re-derives the cheap rows itself to verify the artifact is not stale**. A
tampered or out-of-date walk file therefore fails the gate rather than passing
silently.

    python separation_mt_v3_walk.py                 # full walk, n=1..8
    python separation_mt_v3_walk.py --max-tenants 6 # cheap, for development
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analysis_separation as sep  # noqa: E402
import analysis_separation_mt as mt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jcac_sim"))
import guarantee  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))
from harness import workloads  # noqa: E402

DEFAULT_OUT = Path(__file__).resolve().parent / "separation_mt_v3_walk.json"
MIN_OUT = Path(__file__).resolve().parent / "separation_mt_v3_walk_min.json"


def cell():
    """The published cell, read from the same place every other record reads
    it. Nothing here is a literal."""
    size = workloads.CLUSTER_SIZES[mt.CELL["cluster_size"]]
    return {
        "config": mt.config_for(mt.CELL["cluster_size"]),
        "orbit": sep.flash_orbit(),
        "cap": size.limits_replicas,
        "tenants": len(workloads.TENANT_MIXES[mt.CELL["tenant_mix"]]),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--max-tenants", type=int, default=None,
                    help="stop early (development); default is the cell's own "
                         "tenant count")
    ap.add_argument("--chunk", type=int, default=1 << 20)
    ap.add_argument("--out", type=Path, default=None,
                    help="default: separation_mt_v3_walk.json for the published "
                         "bound, separation_mt_v3_walk_min.json for --bound min")
    ap.add_argument("--bound", choices=("worst", "min"), default="worst",
                    help="need-level table bound: 'worst' is the published "
                         "construction (an upper bound); 'min' is a valid floor "
                         "(guarantee._violation_table, audit 2026-09-26)")
    args = ap.parse_args()
    if args.out is None:
        args.out = DEFAULT_OUT if args.bound == "worst" else MIN_OUT

    c = cell()
    cfg, orbit, cap = c["config"], c["orbit"], c["cap"]
    upto = args.max_tenants or c["tenants"]

    needs = guarantee.orbit_replica_needs(cfg, orbit)
    length = len(needs)

    rows = []
    for n in range(1, upto + 1):
        started = time.time()
        r = guarantee.coupled_floor_incremental_batched(
            cfg, orbit, n, cap, chunk=args.chunk, bound=args.bound)
        elapsed = time.time() - started
        rows.append({
            "tenants": n,
            "vectors": r["states_walked"],
            "coupled_violation": r["coupled_violation"],
            "uncoupled_violation": r["uncoupled_violation"],
            "gap": r["gap"],
            "cap_can_bind": n * cfg.replica_max > cap,
            "seconds": elapsed,
        })
        print(f"n={n:<2} {r['states_walked']:>13,} vectors  "
              f"{elapsed:>8.1f}s  floor {r['coupled_violation']:.6f}",
              flush=True)

    payload = {
        "generated_by": Path(__file__).name,
        "mode": "ordered-batched",
        **({"bound": args.bound} if args.bound != "worst" else {}),
        "cell": mt.CELL,
        "cap": cap,
        "replica_max": cfg.replica_max,
        "orbit_length": length,
        "needs": list(needs),
        "ramp_lead": guarantee.ramp_lead(cfg.replica_max),
        "tenants_walked_to": upto,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "rows": rows,
    }
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
