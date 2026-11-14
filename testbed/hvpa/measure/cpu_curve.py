"""WP17 step 3, inside the cluster: the CPU service's load curve at one CPU level
(programme 2).

The WP2 congestion protocol of testbed/measure/load_curve.py (mounted beside
this file), with the request replaced by `GET /work` on the service: an
unloaded baseline of sequential requests, the closed-loop capacity before and
after (2 x 4 x replicas workers: more than enough to saturate a pod of up to
2 cores with requests of tens of CPU-milliseconds), and ten open-loop Poisson
levels in two passes. The results go to stdout between markers, as the two
files research/plant2/fits.py reads, for the host to collect with
`kubectl logs` (kind maps no host port for this service).

    python cpu_curve.py --base-url http://cpuburn.pf-hvpa --cpu 0.5 --level-seconds 60
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
import urllib.request

import load_curve as lc

BEGIN, END = "-----BEGIN WP17 CURVE-----", "-----END WP17 CURVE-----"


def get_work(base_url: str) -> lc.PostFn:
    """The protocol's request function: GET /work, no payload."""
    def send(_path: str, _payload: dict, timeout: float) -> tuple[float, dict]:
        t0 = time.perf_counter()
        with urllib.request.urlopen(base_url.rstrip("/") + "/work", timeout=timeout) as resp:  # noqa: S310
            resp.read()
            work_s = float(resp.headers.get("X-Work-Seconds", "nan"))
        return time.perf_counter() - t0, {"timings": {"predicted_ms": work_s * 1000}}
    return send


def emit(rows: list, meta: dict, out=sys.stdout) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=lc.FIELDS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    out.write(f"{BEGIN}\n{json.dumps(meta)}\n{buf.getvalue()}{END}\n")
    out.flush()


def parse(text: str) -> tuple[dict, str]:
    """(meta, requests CSV) from a pod's log: the host side of emit()."""
    start, stop = text.index(BEGIN) + len(BEGIN) + 1, text.index(END)
    meta_line, _, body = text[start:stop].partition("\n")
    return json.loads(meta_line), body


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--cpu", type=float, required=True, help="the pod's CPU limit, for the record")
    ap.add_argument("--replicas", type=int, default=1)
    ap.add_argument("--level-seconds", type=float, default=60.0)
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args(argv)
    lc_args = lc.parse_args(["--tier", "small", "--replicas", str(args.replicas), "--threads", str(args.cpu),
                             "--base-url", args.base_url, "--out", "/tmp/unused", "--level-seconds",
                             str(args.level_seconds)] + (["--quick"] if args.quick else []))
    captured = {}

    def keep(out_dir, rows, meta) -> None:   # load_curve.run writes files; here they go to stdout
        captured.update(rows=rows, meta=meta)

    original = lc.write_outputs
    lc.write_outputs = keep
    try:
        meta = lc.run(lc_args, post=get_work(args.base_url))
    finally:
        lc.write_outputs = original
    meta.update(service="cpuburn", cpu=args.cpu, tier=None, protocol="WP2 protocol on GET /work (WP17 step 3)")
    emit(captured["rows"], meta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
