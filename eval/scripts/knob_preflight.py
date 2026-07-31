"""WL-H2 preflight knob-liveness gate (PREREG_WAVE4_LIVE_PLANE.md).

The pre-registration's validity gate, made executable: before any three-knob
comparison is scored, this probe must prove on the provisioned substrate that
  (i)  toggling the cache-MB knob moves the measured hit rate by a material
       margin, and
  (ii) forcing a tier change moves measured per-request latency (and the tier
       actually hit, which is what eval-export meters $-cost from).
If either knob is inert, **WL-H1 is void and the substrate is reported
inadequate** — no joint-vs-single claim may be made from an inert-knob run.
Honesty over a result.

Runs against any live gateway: the kind cluster (port-forwarded), a GPU host,
or a local desk stack (gateway binary + two mock backends with different
delays), which is how the gate itself is verified before the host exists.

    python knob_preflight.py --gateway http://127.0.0.1:18081 \
        --tenant acme --api-key pf_... --admin-key ... --tiers small,mid

Exit 0 = both knobs live (WL-H2 holds). Exit 1 = substrate inadequate.
A JSON report is written next to the harness results for the run record.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_REPORT = Path(__file__).resolve().parents[1] / "results" / "knob_preflight.json"


def _request(url: str, method: str, headers: dict, body: dict | None = None,
             timeout: float = 60.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json", **headers})
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        # resp.headers stays an email.message.Message: its .get is
        # case-insensitive, which matters because Go canonicalizes
        # X-PolyForge-* to X-Polyforge-* on the wire.
        return resp.status, resp.headers, resp.read(), elapsed_ms


class Gateway:
    def __init__(self, base: str, tenant: str, api_key: str, admin_key: str):
        self.base = base.rstrip("/")
        self.tenant = tenant
        self.api_key = api_key
        self.admin_key = admin_key

    def put_knobs(self, model_tier: str, cache_size_mb: int) -> None:
        status, _, body, _ = _request(
            f"{self.base}/admin/tenants/{self.tenant}/knobs", "PUT",
            {"X-PolyForge-Admin-Key": self.admin_key},
            {"model_tier": model_tier, "cache_size_mb": cache_size_mb})
        if status != 200:
            raise RuntimeError(f"knob PUT -> {status}: {body[:300]}")

    def chat(self, prompt: str) -> tuple[str, str, float]:
        """Returns (cache_header, tier_header, latency_ms)."""
        _, headers, _, elapsed_ms = _request(
            f"{self.base}/v1/tenants/{self.tenant}/ai/chat", "POST",
            {"X-PolyForge-API-Key": self.api_key},
            {"messages": [{"role": "user", "content": prompt}]})
        return (headers.get("X-PolyForge-Cache", ""),
                headers.get("X-PolyForge-Tier", ""), elapsed_ms)


def _prompt(tag: str, i: int) -> str:
    """High-entropy token prompt: distinct prompts never collide under a
    lexical embedder (the wire-attack fixture's construction)."""
    return " ".join(
        hashlib.sha256(f"preflight:{tag}:{i}:{w}".encode()).hexdigest()[:8]
        for w in range(12))


def probe_cache(gw: Gateway, tier: str, n: int, margin: float) -> dict:
    """Hit rate on a re-sent prompt set: budgeted vs zero-budget."""
    def replay(tag: str, cache_mb: int) -> float:
        gw.put_knobs(tier, cache_mb)
        prompts = [_prompt(tag, i) for i in range(n)]
        for p in prompts:            # warm pass (all misses by construction)
            gw.chat(p)
        hits = sum(1 for p in prompts if gw.chat(p)[0] == "hit")
        return hits / n

    hit_budgeted = replay("budgeted", 64)
    hit_zero = replay("zero", 0)
    delta = hit_budgeted - hit_zero
    return {
        "hit_rate_budgeted": hit_budgeted,
        "hit_rate_zero": hit_zero,
        "delta": delta,
        "margin": margin,
        "live": delta >= margin,
    }


def probe_tier(gw: Gateway, tier_a: str, tier_b: str, n: int,
               rel_margin: float, abs_margin_ms: float) -> dict:
    """Mean miss latency + observed tier header under each tier pin. The
    cache is disabled (budget 0) so every request pays a real backend."""
    def measure(tier: str, tag: str) -> tuple[float, set]:
        gw.put_knobs(tier, 0)
        latencies, tiers_seen = [], set()
        for i in range(n):
            cache, tier_header, ms = gw.chat(_prompt(f"tier:{tag}", i))
            if cache == "hit":
                raise RuntimeError("cache hit at budget 0 — cache knob is broken")
            latencies.append(ms)
            tiers_seen.add(tier_header)
        return statistics.mean(latencies), tiers_seen

    mean_a, seen_a = measure(tier_a, tier_a)
    mean_b, seen_b = measure(tier_b, tier_b)
    routed = seen_a == {tier_a} and seen_b == {tier_b}
    delta_ms = abs(mean_b - mean_a)
    material = delta_ms >= max(abs_margin_ms, rel_margin * min(mean_a, mean_b))
    # Direction matters as much as size. Tiers are named in ascending
    # capability, so tier_b runs the larger model and must be the slower one;
    # an inverted gap means the tiers are misrouted or the measurement is
    # cold-start contaminated, not that the knob works. Testing |delta| alone
    # scores that case identically to a healthy substrate — which is not
    # hypothetical: measured naively against a real P100, the 3B tier came out
    # 174 ms *faster* than the 0.5B tier and the abs() rule returned PASS.
    # Added before any WL-H1 comparison number existed; it makes the gate
    # strictly harder to pass, never easier.
    ordered = mean_b > mean_a
    return {
        "tier_a": tier_a, "mean_ms_a": mean_a, "tiers_seen_a": sorted(seen_a),
        "tier_b": tier_b, "mean_ms_b": mean_b, "tiers_seen_b": sorted(seen_b),
        "delta_ms": delta_ms,
        "rel_margin": rel_margin, "abs_margin_ms": abs_margin_ms,
        "routing_moved": routed,
        "latency_moved": material,
        "ordering_correct": ordered,
        "live": routed and material and ordered,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gateway", required=True, help="gateway base URL")
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--api-key", required=True)
    ap.add_argument("--admin-key", required=True)
    ap.add_argument("--tiers", default="small,mid",
                    help="two configured tier names to probe, comma-separated")
    ap.add_argument("--cache-probes", type=int, default=24)
    ap.add_argument("--tier-probes", type=int, default=12)
    ap.add_argument("--cache-margin", type=float, default=0.5,
                    help="required hit-rate delta budgeted-vs-zero")
    ap.add_argument("--tier-rel-margin", type=float, default=0.25,
                    help="required relative latency delta between tiers")
    ap.add_argument("--tier-abs-margin-ms", type=float, default=20.0)
    ap.add_argument("--report", default=str(DEFAULT_REPORT))
    args = ap.parse_args()

    tier_a, tier_b = (t.strip() for t in args.tiers.split(",", 1))
    gw = Gateway(args.gateway, args.tenant, args.api_key, args.admin_key)

    report = {"gateway": args.gateway, "tenant": args.tenant,
              "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        report["cache"] = probe_cache(gw, tier_a, args.cache_probes, args.cache_margin)
        report["tier"] = probe_tier(gw, tier_a, tier_b, args.tier_probes,
                                    args.tier_rel_margin, args.tier_abs_margin_ms)
    except (urllib.error.URLError, RuntimeError) as err:
        report["error"] = str(err)
        report["verdict"] = "SUBSTRATE INADEQUATE (probe failed)"
        Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"knob_preflight: {report['verdict']}: {err}", file=sys.stderr)
        return 1

    ok = report["cache"]["live"] and report["tier"]["live"]
    report["verdict"] = "WL-H2 PASS (both knobs live)" if ok else \
        "SUBSTRATE INADEQUATE — WL-H1 is void (inert knob)"
    Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")

    c, t = report["cache"], report["tier"]
    print(f"cache knob: hit {c['hit_rate_budgeted']:.2f} @64MB vs "
          f"{c['hit_rate_zero']:.2f} @0MB (delta {c['delta']:.2f}, "
          f"margin {c['margin']}) -> {'LIVE' if c['live'] else 'INERT'}")
    print(f"tier knob: {t['tier_a']} {t['mean_ms_a']:.1f} ms vs "
          f"{t['tier_b']} {t['mean_ms_b']:.1f} ms (delta {t['delta_ms']:.1f} ms), "
          f"routing moved: {t['routing_moved']} -> {'LIVE' if t['live'] else 'INERT'}")
    print(f"verdict: {report['verdict']}  (report: {args.report})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
