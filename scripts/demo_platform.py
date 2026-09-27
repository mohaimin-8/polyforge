"""The live platform demo: PolyForge's services started and exercised in one command.

Builds the control plane and the AI gateway, starts them on this machine
with three mock model tiers (research/security/mock_llm.py: small, mid and
large answer after 100, 300 and 800 ms), then walks through six acts:
tenant onboarding, tenant isolation, the semantic cache, per-tenant cache
isolation, the model-tier knob, and the workload classifier, plus metrics.
Every act checks its own result, so a rehearsal that exits 0 is a demo
that will work. No Docker, GPU or network is needed; the services use a
fresh SQLite file under tmp/demo/ and stop when the demo ends.

Usage (from the repository root):
    python scripts/demo_platform.py                 # rehearsal: every act, every check
    python scripts/demo_platform.py --pause         # presentation: Enter between acts
    python scripts/demo_platform.py --pause --keep-running   # then open the dashboard
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORK = REPO / "tmp" / "demo"
EXE = ".exe" if os.name == "nt" else ""
ADMIN_KEY = "pf_admin_demo"
CONTROL_PLANE = "http://127.0.0.1:8080"
GATEWAY = "http://127.0.0.1:8081"
SERVICE_PORTS = (8080, 8081)
#: tier -> (port, miss delay in ms): stand-ins for a 0.5B, 3B and 7B model.
MOCK_TIERS = (("small", (11501, 100)), ("mid", (11502, 300)), ("large", (11503, 800)))
CLASSIFIER_WAIT_S = 45
HEALTH_WAIT_S = 30
QUESTION = "How do I reset a forgotten password on PostgreSQL?"
NEAR_DUPLICATE = "how do i reset a forgotten password on postgresql"
TIER_QUESTIONS = {
    "small": "Translate 'good morning' into French.",
    "mid": "Write a haiku about Kubernetes autoscaling.",
    "large": "Explain the proof of Fermat's little theorem step by step.",
}
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never proxy localhost


def tier_backends_json() -> str:
    """POLYFORGE_TIER_BACKENDS for the gateway (knobs.go BuildTierProviders)."""
    return json.dumps({tier: {"base_url": f"http://127.0.0.1:{port}/v1", "model": f"mock-{tier}"}
                       for tier, (port, _) in MOCK_TIERS})


def busy_ports(ports, host: str = "127.0.0.1") -> list[int]:
    busy = []
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex((host, port)) == 0:
                busy.append(port)
    return busy


def latency_order_ok(latencies: dict) -> bool:
    tiers = [t for t, _ in MOCK_TIERS]
    if any(t not in latencies for t in tiers):
        return False
    return all(latencies[a] < latencies[b] for a, b in zip(tiers, tiers[1:]))


class Checks:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def record(self, name: str, ok: bool, detail: str) -> bool:
        self.results.append((name, ok, detail))
        print(f"    [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        return ok

    def exit_code(self) -> int:
        return 0 if all(ok for _, ok, _ in self.results) else 1

    def summary(self) -> None:
        failed = [(n, d) for n, ok, d in self.results if not ok]
        print("\n" + "=" * 72)
        if not failed:
            print(f"All {len(self.results)} checks passed.")
            return
        print(f"{len(failed)} of {len(self.results)} checks failed:")
        for name, detail in failed:
            print(f"  - {name}: {detail}")


@dataclass(frozen=True)
class Reply:
    status: int
    body: dict
    headers: dict
    seconds: float


def call(method: str, url: str, headers: dict | None = None, body: dict | None = None) -> Reply:
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {**({"Content-Type": "application/json"} if data else {}), **(headers or {})}
    request = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    started = time.perf_counter()
    try:
        with _OPENER.open(request, timeout=10) as resp:
            raw, status, got = resp.read(), resp.status, resp.headers
    except urllib.error.HTTPError as err:
        raw, status, got = err.read(), err.code, err.headers
    seconds = time.perf_counter() - started
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        parsed = {"text": raw.decode(errors="replace")}
    return Reply(status, parsed, {k.lower(): v for k, v in got.items()}, seconds)


class Services:
    """The processes the demo owns; stop() always leaves the machine clean."""

    def __init__(self) -> None:
        self.procs: list[tuple[str, subprocess.Popen, object]] = []

    def start(self, name: str, argv: list[str], env: dict) -> subprocess.Popen:
        log = open(WORK / f"{name}.log", "w", encoding="utf-8")  # noqa: SIM115 (closed in stop)
        proc = subprocess.Popen(argv, cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT)
        self.procs.append((name, proc, log))
        return proc

    def stop(self) -> None:
        for name, proc, log in reversed(self.procs):
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            except Exception as err:  # one bad handle must not orphan the rest
                print(f"  warning: could not stop {name} cleanly: {err}")
            finally:
                log.close()
        self.procs.clear()


def service_env() -> dict:
    """The caller's environment minus any POLYFORGE_* setting (a Postgres or
    Redis URL would silently change the storage path), plus the demo's own."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("POLYFORGE_")}
    env.update({
        "POLYFORGE_DB_PATH": str(WORK / "demo.db"),
        "POLYFORGE_ADMIN_KEY": ADMIN_KEY,
        "POLYFORGE_LLM_BASE_URL": f"http://127.0.0.1:{MOCK_TIERS[0][1][0]}/v1",
        "POLYFORGE_TIER_BACKENDS": tier_backends_json(),
        # The limiter is demonstrated by its own tests; a rehearsal must never trip it.
        "POLYFORGE_RATE_LIMIT_RPM": "6000",
        "POLYFORGE_RATE_LIMIT_BURST": "600",
    })
    return env


def build() -> None:
    for name in ("control-plane", "ai-gateway"):
        say(f"go build ./cmd/{name}")
        subprocess.run(["go", "build", "-buildvcs=false", "-o", str(WORK / f"{name}{EXE}"),
                        f"./cmd/{name}"], cwd=REPO, check=True)


def wait_healthy(base: str, name: str, proc: subprocess.Popen) -> None:
    deadline = time.time() + HEALTH_WAIT_S
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"{name} exited early; see {WORK / (name + '.log')}")
        try:
            if call("GET", f"{base}/healthz").status == 200:
                return
        except OSError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{name} not healthy after {HEALTH_WAIT_S}s; see {WORK / (name + '.log')}")


def start_all(services: Services) -> None:
    env = service_env()
    for tier, (port, delay) in MOCK_TIERS:
        proc = services.start(f"mock-{tier}", [sys.executable, "research/security/mock_llm.py",
                                               "--port", str(port)],
                              {**env, "MOCK_LLM_DELAY_MS": str(delay)})
        wait_healthy(f"http://127.0.0.1:{port}", f"mock-{tier}", proc)  # mock answers any GET
        say(f"mock {tier:5s} model on :{port} (answers after {delay} ms)")
    for name, base in (("control-plane", CONTROL_PLANE), ("ai-gateway", GATEWAY)):
        proc = services.start(name, [str(WORK / f"{name}{EXE}")], env)
        wait_healthy(base, name, proc)
        say(f"{name} healthy at {base}")


def say(text: str) -> None:
    print(f"  {text}")


def act(number: int, title: str, meaning: str, pause: bool) -> None:
    if pause:
        input("\n[Enter] for the next act ... ")
    print(f"\n--- Act {number}: {title} " + "-" * max(0, 56 - len(title)))
    print(f"  What it shows: {meaning}\n")


def admin() -> dict:
    return {"X-PolyForge-Admin-Key": ADMIN_KEY}


def chat(tenant: str, key: str, text: str) -> Reply:
    return call("POST", f"{GATEWAY}/v1/tenants/{tenant}/ai/chat", {"X-PolyForge-API-Key": key},
                {"messages": [{"role": "user", "content": text}]})


def cache_state(reply: Reply) -> str:
    score = reply.headers.get("x-polyforge-cache-score")
    state = reply.headers.get("x-polyforge-cache", "?")
    return f"{state} (similarity {score})" if score else state


def act_onboard(checks: Checks) -> dict[str, str]:
    keys = {}
    for tid, name in (("acme", "Acme Research"), ("globex", "Globex Labs")):
        r = call("POST", f"{CONTROL_PLANE}/v1/tenants", admin(), {"id": tid, "name": name})
        if checks.record(f"create tenant {tid}", r.status == 201, f"HTTP {r.status}"):
            key = r.body.get("bootstrap_api_key") or {}
            if not key.get("secret"):
                raise RuntimeError(f"onboarding reply for {tid} carries no API key: {r.body}")
            keys[tid] = key["secret"]
            say(f"{tid}: isolation mode '{r.body.get('tenant', {}).get('isolation_mode')}', "
                f"one-time API key {key.get('prefix')}...")
    if len(keys) != 2:
        raise RuntimeError("tenant onboarding failed; nothing else can run")
    return keys


def act_isolation(checks: Checks, keys: dict) -> None:
    r = call("POST", f"{CONTROL_PLANE}/v1/tenants/globex/projects",
             {"X-PolyForge-API-Key": keys["globex"]}, {"id": "p1", "name": "Secret roadmap"})
    checks.record("globex creates its own project", r.status == 201, f"HTTP {r.status}")
    r = call("GET", f"{CONTROL_PLANE}/v1/tenants/globex/projects",
             {"X-PolyForge-API-Key": keys["acme"]})
    checks.record("acme's key cannot read globex's projects", r.status in (401, 403),
                  f"HTTP {r.status}")
    r = call("GET", f"{CONTROL_PLANE}/v1/tenants/globex/projects",
             {"X-PolyForge-API-Key": keys["globex"]})
    names = [p.get("name") for p in r.body.get("projects", r.body.get("items", []))]
    checks.record("globex reads its own project", r.status == 200, f"HTTP {r.status} {names}")


def act_cache(checks: Checks, keys: dict) -> None:
    first = chat("acme", keys["acme"], QUESTION)
    say(f"acme asks: '{QUESTION}'")
    say(f"  -> {first.seconds * 1000:6.1f} ms, cache {cache_state(first)} (paid a model call)")
    second = chat("acme", keys["acme"], QUESTION)
    say(f"acme asks the same again -> {second.seconds * 1000:6.1f} ms, cache {cache_state(second)}")
    near = chat("acme", keys["acme"], NEAR_DUPLICATE)
    say(f"acme asks '{NEAR_DUPLICATE}' -> {near.seconds * 1000:6.1f} ms, cache {cache_state(near)}")
    checks.record("first question is a miss", first.headers.get("x-polyforge-cache") == "miss",
                  cache_state(first))
    checks.record("repeat is a hit and faster", second.headers.get("x-polyforge-cache") == "hit"
                  and second.seconds < first.seconds,
                  f"{second.seconds * 1000:.1f} ms vs {first.seconds * 1000:.1f} ms")
    checks.record("near-duplicate is a semantic hit", near.headers.get("x-polyforge-cache") == "hit",
                  cache_state(near))


def act_cache_isolation(checks: Checks, keys: dict) -> None:
    r = chat("globex", keys["globex"], QUESTION)
    say(f"globex asks acme's exact question -> {r.seconds * 1000:6.1f} ms, cache {cache_state(r)}")
    checks.record("another tenant's cache entry is invisible",
                  r.headers.get("x-polyforge-cache") == "miss", cache_state(r))


def act_tier_knob(checks: Checks, keys: dict) -> None:
    latencies = {}
    for tier, question in TIER_QUESTIONS.items():
        put = call("PUT", f"{GATEWAY}/admin/tenants/acme/knobs", admin(),
                   {"model_tier": tier, "cache_size_mb": 64})
        r = chat("acme", keys["acme"], question)
        latencies[tier] = r.seconds
        say(f"knob tier={tier:5s} (HTTP {put.status}) -> new question answered in "
            f"{r.seconds * 1000:6.1f} ms, cache {cache_state(r)}")
    knobs = call("GET", f"{GATEWAY}/admin/tenants/acme/knobs", admin()).body
    say(f"gateway now holds for acme: {json.dumps(knobs)}")
    checks.record("tier knob changes the serving model live", latency_order_ok(latencies),
                  " < ".join(f"{t} {s * 1000:.0f} ms" for t, s in latencies.items()))


def _generate_traffic(keys: dict) -> None:
    for i in range(30):
        chat("acme", keys["acme"], f"question number {i % 5}")
    for i in range(40):
        call("POST", f"{CONTROL_PLANE}/v1/tenants/globex/workloads/replay",
             {"X-PolyForge-API-Key": keys["globex"]},
             {"kind": "crud_write" if i % 4 == 0 else "crud_read"})


def _labels() -> dict:
    body = call("GET", f"{CONTROL_PLANE}/v1/admin/workloads", admin()).body
    return {t["tenant_id"]: t for t in body.get("tenants", [])}


def act_classifier(checks: Checks, keys: dict) -> None:
    say("sending 30 chat requests as acme and 40 CRUD requests as globex ...")
    _generate_traffic(keys)
    deadline = time.time() + CLASSIFIER_WAIT_S
    labels = _labels()
    while time.time() < deadline and not {"acme", "globex"} <= labels.keys():
        time.sleep(2)  # the online classifier relabels every 10 s
        labels = _labels()
    for tid, expected in (("acme", "AI-"), ("globex", "CRUD-")):
        seen = labels.get(tid, {})
        label = seen.get("label", "none yet")
        policy = call("GET", f"{CONTROL_PLANE}/v1/tenants/{tid}/policy-recommendation",
                      {"X-PolyForge-API-Key": keys[tid]}).body
        say(f"{tid:6s}: labelled {label} (confidence {seen.get('confidence', 0):.3f}) -> "
            f"recommend {policy.get('replicas')} replicas, {policy.get('cache_mb')} MB cache, "
            f"tier {policy.get('model_tier')}")
        checks.record(f"{tid} classified as {expected}*", str(label).startswith(expected), label)


def act_observability(checks: Checks) -> None:
    r = call("GET", f"{CONTROL_PLANE}/metrics")
    series = [line for line in r.body.get("text", "").splitlines() if line.startswith("polyforge_")]
    say(f"control-plane /metrics: {len(series)} PolyForge series, e.g.")
    for line in [s for s in series if "requests_total" in s][:3]:
        say(f"  {line[:100]}")
    say(f"every response carries X-Request-ID {r.headers.get('x-request-id', '?')[:16]}... "
        f"and traceparent {r.headers.get('traceparent', '?')[:30]}...")
    checks.record("metrics exposed", any("polyforge_http_requests_total" in s for s in series),
                  f"{len(series)} series")


def run_acts(checks: Checks, pause: bool) -> None:
    act(1, "two customers sign up", "multi-tenancy: each tenant gets its own identity and keys",
        pause)
    keys = act_onboard(checks)
    act(2, "tenant isolation", "one tenant's key can never read another tenant's data", pause)
    act_isolation(checks, keys)
    act(3, "the semantic cache", "a repeated AI question is answered from cache, skipping "
        "the model", pause)
    act_cache(checks, keys)
    act(4, "per-tenant cache", "the cache is partitioned, so response time cannot leak "
        "what another tenant asked", pause)
    act_cache_isolation(checks, keys)
    act(5, "the model-tier knob", "the API the operator uses every 10 s to apply the "
        "controller's plan", pause)
    act_tier_knob(checks, keys)
    act(6, "the workload classifier", "live traffic is labelled per tenant and mapped to a "
        "resource policy", pause)
    act_classifier(checks, keys)
    act(7, "observability", "Prometheus metrics and trace headers on every request", pause)
    act_observability(checks)


def _parse(argv) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pause", action="store_true", help="wait for Enter between acts")
    ap.add_argument("--keep-running", action="store_true",
                    help="leave the services up at the end until Enter is pressed")
    ap.add_argument("--no-build", action="store_true", help="reuse binaries in tmp/demo/")
    return ap.parse_args(argv)


def _keep_running() -> None:
    print(f"\nServices are still running. Open {CONTROL_PLANE}/admin/workloads in a browser "
          f"and enter the admin key {ADMIN_KEY}.")
    input("[Enter] stops everything ... ")


def main(argv=None) -> int:
    args = _parse(argv)
    ports = [*SERVICE_PORTS, *(port for _, (port, _) in MOCK_TIERS)]
    if busy := busy_ports(ports):
        print(f"Ports {busy} are already in use: stop whatever holds them (a previous "
              "demo, or scripts/run-control-plane.ps1) and rerun.")
        return 2
    WORK.mkdir(parents=True, exist_ok=True)
    for stale in WORK.glob("demo.db*"):
        stale.unlink()
    print("PolyForge live platform demo\n\nStarting the services:")
    missing = any(not (WORK / f"{n}{EXE}").exists() for n in ("control-plane", "ai-gateway"))
    if missing or not args.no_build:
        build()
    checks, services = Checks(), Services()
    try:
        start_all(services)
        run_acts(checks, args.pause)
        checks.summary()
        if args.keep_running:
            _keep_running()
    except Exception as err:  # reported as a FAIL, never a bare traceback mid-demo
        checks.record("demo aborted", False, f"{type(err).__name__}: {err}")
        checks.summary()
    finally:
        services.stop()
    return checks.exit_code()


if __name__ == "__main__":
    sys.exit(main())
