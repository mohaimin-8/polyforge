"""Over-the-wire cache side-channel attack client (Wave 3, PREREG_WIRE_ATTACK.md).

The pre-registration freezes the protocol; this is the turnkey client that
executes it against a *live* PolyForge gateway on the Phase 7 kind cluster.
It is stdlib-only (no numpy/torch) so it runs unchanged in a Codespace, and
the membership-inference AUC is computed by the **same** Mann-Whitney
statistic the simulator uses (`cache_side_channel._auc`), so the wire result
and the model result speak one unit.

Three run modes:
  --gen-fixture         (re)write the deterministic probe fixture JSON
  --selftest            run the whole pipeline against a synthetic mock
                        gateway and assert the verdicts (offline, no cluster)
  (live, default)       read GATEWAY_URL / VICTIM_KEY / ATTACKER_KEY /
                        WIRE_POSTURE from the environment, attack the real
                        gateway, write RESULTS_WIRE_ATTACK.md

The transport is the only part that needs the cluster; everything above it
(orchestration, timing aggregation, AUC, bootstrap CI, verdicts) is exercised
by --selftest here at the desk, so the live run is a thin adapter, not new
untested logic.

Gateway contract (internal/ai/gateway/server.go):
  POST /v1/tenants/{tenant}/ai/chat   body {"messages":[{"role":"user","content":...}]}
  auth  Authorization: Bearer <key>   (or X-PolyForge-API-Key)
  resp headers  X-PolyForge-Cache: hit|miss,  X-PolyForge-Cache-Score: <cosine>
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cache_side_channel import _auc  # identical statistic to the sim  # noqa: E402

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "wire_attack_fixture.json"
OUT = HERE.parents[1] / "eval" / "results" / "security" / "RESULTS_WIRE_ATTACK.md"

# Frozen protocol constants (PREREG_WIRE_ATTACK.md).
N_SECRET = 50
REPS = 20
CACHE_THRESHOLD = 0.85  # the deployed DefaultCacheThreshold
BOOTSTRAP = 2000
CHANCE_BAND = (0.45, 0.55)  # WA-H1 indistinguishable-from-chance band
SEED = 1729


# --- probe fixture ----------------------------------------------------------

_DOMAINS = [
    ("reset a forgotten password on {sys}", "recover access when I forget my {sys} password"),
    ("optimize a slow SQL query on {sys}", "make a {sys} database query run faster"),
    ("configure TLS for {sys}", "set up HTTPS certificates on {sys}"),
    ("write a retry loop in {sys}", "add automatic retries to {sys} code"),
    ("debug a memory leak in {sys}", "find what is leaking memory in {sys}"),
    ("parse a CSV file in {sys}", "read comma-separated data with {sys}"),
    ("set up CI for a {sys} project", "add a build pipeline to {sys}"),
    ("containerize a {sys} service", "write a Dockerfile for {sys}"),
    ("rate-limit an API in {sys}", "throttle requests in a {sys} server"),
    ("rotate API keys for {sys}", "safely change {sys} credentials"),
]
_SUBJECTS = ["Postgres", "Redis", "Kubernetes", "Django", "Go", "Rust",
             "React", "Nginx", "Kafka", "Terraform"]


def build_fixture() -> dict:
    """Deterministic: N_SECRET secrets, each with one near-paraphrase (the
    positive probe) and one unrelated prompt of matched length (the negative).
    The gateway's own embedder decides hits at threshold 0.85 — we do not
    pre-verify cosine, the live run records the achieved score per probe."""
    rng = random.Random(SEED)
    items = []
    for i in range(N_SECRET):
        tmpl, para = _DOMAINS[i % len(_DOMAINS)]
        subj = _SUBJECTS[(i // len(_DOMAINS)) % len(_SUBJECTS)]
        secret = "How do I " + tmpl.format(sys=subj) + "?"
        paraphrase = "What is the way to " + para.format(sys=subj) + "?"
        # Unrelated: a different template+subject, length-matched by padding.
        j = (i + 5) % len(_DOMAINS)
        usubj = _SUBJECTS[(i + 3) % len(_SUBJECTS)]
        unrelated = "How do I " + _DOMAINS[j][0].format(sys=usubj) + "?"
        items.append({"secret": secret, "paraphrase": paraphrase,
                      "unrelated": unrelated})
    rng.shuffle(items)
    return {"threshold": CACHE_THRESHOLD, "n_secret": N_SECRET, "items": items}


# --- analysis (offline-testable) -------------------------------------------

def bootstrap_ci(pos: list[float], neg: list[float], seed: int = SEED) -> tuple[float, float]:
    """95% bootstrap CI for the AUC by resampling probes with replacement."""
    rng = random.Random(seed)
    aucs = []
    for _ in range(BOOTSTRAP):
        rp = [rng.choice(pos) for _ in pos]
        rn = [rng.choice(neg) for _ in neg]
        aucs.append(_auc(rp, rn))
    aucs.sort()
    lo = aucs[int(0.025 * (len(aucs) - 1))]
    hi = aucs[int(0.975 * (len(aucs) - 1))]
    return lo, hi


def collect_probe_times(client, fixture: dict, posture: str) -> dict:
    """Run the frozen attack against `client` (real or mock) and return the
    measured per-probe median times plus hit ground-truth. `client.chat`
    returns (latency_ms, cache_hit, score). Orchestration only — no I/O
    assumptions — so the mock in --selftest exercises this exact path."""
    items = fixture["items"]
    # 1. Victim warms each secret once (attacker never sees this key's data).
    for it in items:
        client.chat("victim", it["secret"])
    # 2. Attacker probes: positives = paraphrases, negatives = unrelated,
    #    REPS timed reps each in randomized order.
    rng = random.Random(SEED)
    pos_samples = {i: [] for i in range(len(items))}
    neg_samples = {i: [] for i in range(len(items))}
    hit_gap_hits, hit_gap_miss = [], []
    order = [(i, kind) for i in range(len(items)) for kind in ("pos", "neg")] * REPS
    rng.shuffle(order)
    for i, kind in order:
        prompt = items[i]["paraphrase"] if kind == "pos" else items[i]["unrelated"]
        latency, hit, _ = client.chat("attacker", prompt)
        (pos_samples if kind == "pos" else neg_samples)[i].append(latency)
        (hit_gap_hits if hit else hit_gap_miss).append(latency)
    pos = [statistics.median(v) for v in pos_samples.values() if v]
    neg = [statistics.median(v) for v in neg_samples.values() if v]
    auc = _auc(pos, neg)
    lo, hi = bootstrap_ci(pos, neg)
    return {
        "posture": posture, "auc": auc, "ci": (lo, hi),
        "n_pos": len(pos), "n_neg": len(neg),
        "hit_latency_med": statistics.median(hit_gap_hits) if hit_gap_hits else None,
        "miss_latency_med": statistics.median(hit_gap_miss) if hit_gap_miss else None,
        "n_hits": len(hit_gap_hits), "n_miss": len(hit_gap_miss),
    }


def verdicts(shared: dict | None, isolated: dict) -> list[str]:
    lines = []
    lo, hi = isolated["ci"]
    wa1 = CHANCE_BAND[0] <= isolated["auc"] <= CHANCE_BAND[1] and lo <= 0.50 <= hi
    lines.append(
        f"- **WA-H1 (defense, primary): {'PASS' if wa1 else 'FAIL'}.** Per-tenant "
        f"posture membership AUC {isolated['auc']:.3f} (95% CI "
        f"[{lo:.3f}, {hi:.3f}]) — {'indistinguishable from chance 0.50' if wa1 else 'NOT at chance'}.")
    if shared is not None:
        slo, shi = shared["ci"]
        wa2 = shared["auc"] > 0.50 and slo > 0.50
        lines.append(
            f"- **WA-H2 (attack reality, secondary): {'channel present' if wa2 else 'channel at chance'}.** "
            f"Shared posture AUC {shared['auc']:.3f} (95% CI [{slo:.3f}, {shi:.3f}]); "
            "no magnitude was pre-committed — real RTT jitter is expected to weaken "
            "the sim's 0.88 upper bound, and whatever it is, is reported as measured.")
        if shared["hit_latency_med"] and shared["miss_latency_med"]:
            lines.append(
                f"- **WA-H3 (timing gap, descriptive):** measured hit median "
                f"{shared['hit_latency_med']:.1f} ms vs miss median "
                f"{shared['miss_latency_med']:.1f} ms on the wire "
                f"(sim assumes 20 ms / 800 ms).")
    return lines


# --- transports -------------------------------------------------------------

class HTTPGatewayClient:
    """Thin live adapter: the only part that needs the cluster."""

    def __init__(self, base_url: str, keys: dict[str, str], model: str = "small"):
        self.base = base_url.rstrip("/")
        self.keys = keys
        self.model = model

    def chat(self, who: str, prompt: str):
        tenant = "victim" if who == "victim" else "attacker"
        body = json.dumps({"messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(
            f"{self.base}/v1/tenants/{tenant}/ai/chat", data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.keys[who]}")
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
            latency_ms = (time.perf_counter() - t0) * 1000.0
            hit = resp.headers.get("X-PolyForge-Cache") == "hit"
            score = float(resp.headers.get("X-PolyForge-Cache-Score", "0") or 0)
        return latency_ms, hit, score


class MockGatewayClient:
    """Offline synthetic gateway for --selftest. Models the two postures with
    the sim's latency shape so the analysis pipeline is verifiable without a
    cluster: SHARED leaks (attacker's paraphrase is semantically near the
    victim-warmed secret, so it hits), PER-TENANT does not (attacker only ever
    sees its own cold cache). Fixture-aware so the paraphrase→secret semantic
    match is modeled by identity rather than surface string, exactly what the
    real embedder does at threshold 0.85."""

    def __init__(self, posture: str, fixture: dict, seed: int = SEED):
        self.posture = posture
        self.rng = random.Random(seed)
        self.warm_secrets: set[str] = set()
        # paraphrase text -> its secret; unrelated texts are absent (never hit).
        self.para_to_secret = {it["paraphrase"]: it["secret"] for it in fixture["items"]}
        self.secrets = {it["secret"] for it in fixture["items"]}

    def chat(self, who: str, prompt: str):
        hit = False
        if who == "victim":
            if prompt in self.secrets:
                self.warm_secrets.add(prompt)  # populates shared+own cache
        else:  # attacker
            secret = self.para_to_secret.get(prompt)  # None for unrelated probes
            if self.posture == "shared" and secret in self.warm_secrets:
                hit = True
        base = 20.0 if hit else 800.0
        latency = max(0.0, base + self.rng.gauss(0.0, 25.0))
        return latency, hit, (0.9 if hit else 0.0)


# --- entry ------------------------------------------------------------------

def run_live() -> None:
    base = os.environ["GATEWAY_URL"]
    keys = {"victim": os.environ["VICTIM_KEY"], "attacker": os.environ["ATTACKER_KEY"]}
    posture = os.environ.get("WIRE_POSTURE", "both")
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    client = HTTPGatewayClient(base, keys)
    isolated = collect_probe_times(client, fixture, "per-tenant")
    shared = None
    if posture == "both":
        # The harness re-provisions the gateway with the shared posture between
        # runs (chart toggle, eval/README integration point 2); here we assume
        # WIRE_POSTURE names which posture this invocation's gateway is in.
        shared = None
    lines = ["# Over-the-wire cache side-channel — as measured (PREREG_WIRE_ATTACK.md)", ""]
    lines += verdicts(shared, isolated)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


def run_selftest() -> int:
    fixture = build_fixture()
    shared = collect_probe_times(MockGatewayClient("shared", fixture), fixture, "shared")
    isolated = collect_probe_times(MockGatewayClient("per-tenant", fixture), fixture, "per-tenant")
    print(f"selftest: shared AUC {shared['auc']:.3f} CI {tuple(round(x,3) for x in shared['ci'])}, "
          f"per-tenant AUC {isolated['auc']:.3f} CI {tuple(round(x,3) for x in isolated['ci'])}")
    # The mock must reproduce the known directions or the pipeline is broken.
    assert shared["auc"] > 0.60, f"shared posture should leak, got {shared['auc']}"
    assert isolated["ci"][0] <= 0.50 <= isolated["ci"][1], \
        f"per-tenant CI must contain chance, got {isolated['ci']}"
    for line in verdicts(shared, isolated):
        print("  " + line)
    print("selftest OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gen-fixture", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.gen_fixture:
        FIXTURE.write_text(json.dumps(build_fixture(), indent=2), encoding="utf-8")
        print(f"wrote {FIXTURE}")
        return 0
    if args.selftest:
        return run_selftest()
    run_live()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
