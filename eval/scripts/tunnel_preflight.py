"""Will the tunnelled tier backends survive WL-H2? Answer before spending GPU hours.

The B1 free route (`docs/WAVE4_FREE_ROUTE.md`) puts the GPU on a free Kaggle
kernel and the cluster on a free Codespace, joined by a tunnel. That adds
network round-trip to every request, and the pre-registration's WL-H2 gate
requires the tier knob to move latency by a *material* margin. If the tunnel
were slow enough to blur the tiers together, the run would be void.

`knob_preflight.py` is the real gate, but it needs the whole stack — kind,
operator, gateway, tenants. Standing that up to discover the substrate was
inadequate would waste the scarce thing (GPU quota). This probe talks
straight to the tier backends over the tunnel, needs nothing else running,
and applies **the same materiality rule with the same defaults** as
`knob_preflight.probe_tier`:

    material  <=>  delta_ms >= max(abs_margin_ms, rel_margin * min(mean_a, mean_b))

Why a constant round-trip is survivable, and how much of it: the tunnel adds
roughly the same latency to both tiers, so `delta_ms` is unchanged while the
threshold grows at only `rel_margin` x RTT. With the committed tier table
(small 1242 ms, mid 1883 ms, delta 641 ms) and the defaults (rel 0.25, abs
20 ms), the tier probe keeps passing until the tunnel adds about **1.3
seconds** per request. The probe reports the headroom it actually measured,
so the decision is made on this host's numbers rather than on that estimate.

    export POLYFORGE_EVAL_TIER_BACKENDS='{"small":{...},"mid":{...}}'
    python eval/scripts/tunnel_preflight.py

Exit 0 = tunnelled substrate looks adequate; run `knob_preflight.py` for the
real gate once the cluster is up. Exit 1 = inadequate, and the run should not
be started. This probe never scores a hypothesis; it only protects the quota.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_REPORT = Path(__file__).resolve().parents[1] / "results" / "tunnel_preflight.json"
# Kept equal to knob_preflight.py's defaults on purpose: this probe is only
# useful if it predicts that gate's verdict.
REL_MARGIN = 0.25
ABS_MARGIN_MS = 20.0
# research/jcac_sim/model.py: SLO_BASE_MS["ai"] x SLO_CLASS_FACTOR["premium"].
# The *binding* constraint on this route is usually not WL-H2 at all -- the
# tier gap survives a lot of round-trip -- but this target: once a tier's
# latency crosses it, that tier violates for premium tenants and the live SLO
# landscape stops resembling the one the controller was tuned against.
AI_SLO_PREMIUM_MS = 2500.0


def _prompt(tag: str, i: int) -> str:
    """High-entropy per request: a shared semantic cache anywhere on the path
    would otherwise return a hit and we would time the cache, not the tier."""
    digest = hashlib.sha256(f"tunnel-preflight:{tag}:{i}".encode()).hexdigest()[:12]
    return f"Reply with the word ok. Nonce {digest}."


def chat(base_url: str, model: str, prompt: str, timeout: float) -> tuple[float, str]:
    body = json.dumps({"model": model, "stream": False,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions",
                                 data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read())
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    served = str(payload.get("model", ""))
    if not payload.get("choices"):
        raise RuntimeError(f"{model}: response carried no choices")
    return elapsed_ms, served


def measure(name: str, spec: dict, n: int, timeout: float) -> dict:
    base_url, model = spec["base_url"], spec["model"]
    samples, served_models = [], set()
    for i in range(n + 1):  # first call absorbs connection + model warm-up
        ms, served = chat(base_url, model, _prompt(name, i), timeout)
        if i:
            samples.append(ms)
            served_models.add(served)
    return {"tier": name, "model": model, "base_url": base_url,
            "n": len(samples), "mean_ms": statistics.mean(samples),
            "min_ms": min(samples), "max_ms": max(samples),
            "stdev_ms": statistics.pstdev(samples) if len(samples) > 1 else 0.0,
            "served_models": sorted(served_models),
            "echoes_requested_model": served_models == {model}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--backends", default=os.environ.get("POLYFORGE_EVAL_TIER_BACKENDS", ""),
                    help="tier backends JSON (default: $POLYFORGE_EVAL_TIER_BACKENDS)")
    ap.add_argument("--tiers", default="small,mid",
                    help="the two tiers to contrast (default: small,mid)")
    ap.add_argument("--probes", type=int, default=8)
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = ap.parse_args()

    if not args.backends.strip():
        print("no tier backends: set POLYFORGE_EVAL_TIER_BACKENDS or pass "
              "--backends (the Kaggle server prints the exact export line)",
              file=sys.stderr)
        return 1
    try:
        backends = json.loads(args.backends)
    except json.JSONDecodeError as err:
        print(f"tier backends JSON is malformed: {err}", file=sys.stderr)
        return 1

    tier_a, tier_b = [t.strip() for t in args.tiers.split(",")]
    for tier in (tier_a, tier_b):
        if tier not in backends:
            print(f"tier {tier!r} missing from the backends JSON "
                  f"(have: {sorted(backends)})", file=sys.stderr)
            return 1

    report: dict = {"tiers": [tier_a, tier_b], "probes": args.probes,
                    "rel_margin": REL_MARGIN, "abs_margin_ms": ABS_MARGIN_MS}
    try:
        a = measure(tier_a, backends[tier_a], args.probes, args.timeout)
        b = measure(tier_b, backends[tier_b], args.probes, args.timeout)
    except (urllib.error.URLError, TimeoutError, RuntimeError, KeyError) as err:
        report["verdict"] = f"UNREACHABLE ({type(err).__name__}: {err})"
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"tunnel_preflight: {report['verdict']}", file=sys.stderr)
        return 1

    delta_ms = abs(b["mean_ms"] - a["mean_ms"])
    slower = min(a["mean_ms"], b["mean_ms"])
    threshold = max(ABS_MARGIN_MS, REL_MARGIN * slower)
    material = delta_ms >= threshold
    # How much *more* constant round-trip the gate would still tolerate:
    # raising both means by r raises the threshold by rel_margin * r, so the
    # slack is (delta - threshold) / rel_margin. Reported in the probe's own
    # numbers rather than assumed from the committed tier table.
    headroom_ms = (delta_ms - threshold) / REL_MARGIN if material else 0.0
    routed = a["echoes_requested_model"] and b["echoes_requested_model"]

    # Headroom against the premium AI SLO, which usually binds first.
    slowest = max(a["mean_ms"], b["mean_ms"])
    slo_headroom_ms = AI_SLO_PREMIUM_MS - slowest
    report.update({tier_a: a, tier_b: b, "delta_ms": delta_ms,
                   "threshold_ms": threshold, "latency_moved": material,
                   "models_echoed": routed,
                   "extra_rtt_tolerance_ms": headroom_ms,
                   "ai_slo_premium_ms": AI_SLO_PREMIUM_MS,
                   "slowest_tier_mean_ms": slowest,
                   "slo_headroom_ms": slo_headroom_ms,
                   "slowest_tier_within_premium_slo": slo_headroom_ms > 0.0})
    ok = material and routed
    report["verdict"] = ("TUNNEL OK (tier gap survives the round-trip)" if ok else
                         "SUBSTRATE INADEQUATE (do not start the run)")

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")

    for row in (a, b):
        print(f"{row['tier']:>6}: mean {row['mean_ms']:8.1f} ms  "
              f"(min {row['min_ms']:.1f}, max {row['max_ms']:.1f}, "
              f"sd {row['stdev_ms']:.1f}, n={row['n']})  model={row['model']}")
    print(f"  gap {delta_ms:.1f} ms vs threshold {threshold:.1f} ms -> "
          f"{'MATERIAL' if material else 'BLURRED'}")
    print(f"  models echoed correctly: {routed}")
    if ok:
        print(f"  tolerates ~{headroom_ms:.0f} ms more round-trip before "
              "WL-H2's tier probe would fail")
    else:
        print("  the tiers are not separable on this path: fix the tunnel or "
              "move the GPU closer, and do NOT score a run from it")
    print(f"  slowest tier {slowest:.0f} ms vs premium AI SLO "
          f"{AI_SLO_PREMIUM_MS:.0f} ms -> "
          f"{f'{slo_headroom_ms:.0f} ms of headroom' if slo_headroom_ms > 0 else 'OVER TARGET'}")
    if slo_headroom_ms <= 0:
        print("  WARNING: the slowest tier already violates the premium AI SLO "
              "on this path. WL-H2 can still pass -- the tier gap is intact -- "
              "but every premium tenant violates regardless of what the "
              "controller does, which flattens the SLO term the arms are "
              "separated on. Reduce round-trip, or record this prominently "
              "and read WL-H1 (cost at iso-fairness) knowing the fairness "
              "side is saturated.", file=sys.stderr)
    print(f"verdict: {report['verdict']}  (report: {args.report})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
