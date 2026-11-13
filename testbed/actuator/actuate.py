"""Apply a planner's decision to the WP10 testbed (programme 2).

A decision is one control step of a planner, as JSON:

    {"step": 12, "t_s": 120.0,
     "replicas": {"small": 2, "mid": 1},
     "tenants": {"t00": {"model_tier": "mid", "cache_size_mb": 64}}}

* replicas: each tier's Deployment is scaled through the Kubernetes API
  (`kubectl scale`, the Deployment's scale subresource), clamped to the tier's
  [min, max] from bounds.json, the guardrail the operator's PolicyReconciler
  applies (clampReplicas). A decision whose cores exceed the core budget is
  refused, not repaired: the plant enforces the same cap, so a planner that
  breaks it has a bug the testbed must not hide.
* tenants: the gateway's knob endpoint, PUT /admin/tenants/{id}/knobs (the
  operator's push target), then a GET to read the knobs back: a 200 on the
  write is not proof of posture (cluster_backend.verify_default_knobs).

Every application is logged as one JSON line with its due and applied times;
`replay` also logs when each scaled tier reaches its new ready count, so the
replica knob's actuation delay is measured, not assumed.

    python actuate.py apply --decision d.json
    python actuate.py replay --plan plan.jsonl --start now
    python actuate.py status
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

HERE = Path(__file__).resolve().parent
DEFAULT_BOUNDS = HERE / "bounds.json"
DEFAULT_GATEWAY = "http://127.0.0.1:30081"
UNPINNED = "none"
READY_POLL_S = 1.0


class DecisionError(ValueError):
    """A decision that cannot be applied as written."""


@dataclass(frozen=True)
class TierBounds:
    deployment: str
    min: int
    max: int
    cores_per_pod: int


@dataclass(frozen=True)
class Bounds:
    namespace: str
    core_budget: int
    tiers: Mapping[str, TierBounds]

    @classmethod
    def load(cls, path: Path) -> "Bounds":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        tiers = {}
        for name, t in raw["tiers"].items():
            tb = TierBounds(deployment=str(t["deployment"]), min=int(t["min"]), max=int(t["max"]),
                            cores_per_pod=int(t["cores_per_pod"]))
            if tb.min < 0 or tb.max < tb.min or tb.cores_per_pod < 1:
                raise ValueError(f"tier {name}: need 0 <= min <= max and cores_per_pod >= 1")
            tiers[name] = tb
        return cls(namespace=str(raw["namespace"]), core_budget=int(raw["core_budget"]), tiers=tiers)


@dataclass(frozen=True)
class Knobs:
    model_tier: str | None = None
    cache_size_mb: int | None = None


@dataclass(frozen=True)
class Decision:
    step: int | None
    t_s: float
    replicas: Mapping[str, int] = field(default_factory=dict)
    tenants: Mapping[str, Knobs] = field(default_factory=dict)


def parse_decision(obj: Mapping[str, Any], bounds: Bounds) -> Decision:
    """Validate a decision's shape and names; values are clamped later."""
    if not isinstance(obj, Mapping):
        raise DecisionError("a decision must be a JSON object")
    unknown = set(obj) - {"step", "t_s", "replicas", "tenants", "note"}
    if unknown:
        raise DecisionError(f"unknown decision fields: {sorted(unknown)}")
    replicas = {}
    for tier, n in (obj.get("replicas") or {}).items():
        if tier not in bounds.tiers:
            raise DecisionError(f"unknown tier {tier!r} (have {sorted(bounds.tiers)})")
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise DecisionError(f"replicas for {tier} must be a non-negative integer, got {n!r}")
        replicas[tier] = n
    tenants = {}
    for tenant, k in (obj.get("tenants") or {}).items():
        if not isinstance(k, Mapping) or set(k) - {"model_tier", "cache_size_mb"}:
            raise DecisionError(f"tenant {tenant}: knobs are model_tier and cache_size_mb only")
        tier = k.get("model_tier")
        if tier is not None and tier != UNPINNED and tier not in bounds.tiers:
            raise DecisionError(f"tenant {tenant}: model_tier {tier!r} has no tier backend")
        cache = k.get("cache_size_mb")
        if cache is not None and (not isinstance(cache, int) or isinstance(cache, bool) or cache < 0):
            raise DecisionError(f"tenant {tenant}: cache_size_mb must be a non-negative integer")
        tenants[str(tenant)] = Knobs(model_tier=tier, cache_size_mb=cache)
    step = obj.get("step")
    return Decision(step=int(step) if step is not None else None, t_s=float(obj.get("t_s", 0.0)),
                    replicas=replicas, tenants=tenants)


def clamp_replicas(requested: Mapping[str, int], current: Mapping[str, int],
                   bounds: Bounds) -> tuple[dict[str, int], list[str]]:
    """The replica counts to apply: requested tiers clamped to their bounds,
    the other tiers left at their current count. Refuses a result over the
    core budget. Returns (target per tier, tiers that were clamped)."""
    target, clamped = {}, []
    for tier, tb in bounds.tiers.items():
        if tier in requested:
            want = requested[tier]
            value = min(max(want, tb.min), tb.max)
            if value != want:
                clamped.append(tier)
        else:
            value = current.get(tier, tb.min)
        target[tier] = value
    cores = sum(target[t] * bounds.tiers[t].cores_per_pod for t in target)
    if cores > bounds.core_budget:
        raise DecisionError(f"decision needs {cores} cores, budget is {bounds.core_budget}: {target}")
    return target, clamped


class Kube(Protocol):
    def replicas(self, deployment: str) -> tuple[int, int]: ...
    def scale(self, deployment: str, replicas: int) -> None: ...


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


def run_kubectl(args: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl", *args], capture_output=True, text=True, timeout=60)


@dataclass
class KubectlKube:
    """The Kubernetes API through kubectl, so the actuator inherits the user's
    kubeconfig and context (kind-polyforge-testbed) and needs no client library."""

    namespace: str
    context: str | None = None
    run: Runner = run_kubectl

    def _args(self, *args: str) -> list[str]:
        prefix = ["--context", self.context] if self.context else []
        return [*prefix, "--namespace", self.namespace, *args]

    def _check(self, proc: subprocess.CompletedProcess, what: str) -> str:
        if proc.returncode != 0:
            raise RuntimeError(f"kubectl {what} failed ({proc.returncode}): {proc.stderr.strip()}")
        return proc.stdout

    def replicas(self, deployment: str) -> tuple[int, int]:
        """(spec replicas, ready replicas) of a Deployment."""
        out = self._check(self.run(self._args("get", "deployment", deployment, "-o", "json")),
                          f"get deployment {deployment}")
        obj = json.loads(out)
        return int(obj["spec"].get("replicas", 0)), int(obj.get("status", {}).get("readyReplicas", 0))

    def scale(self, deployment: str, replicas: int) -> None:
        self._check(self.run(self._args("scale", f"deployment/{deployment}", f"--replicas={replicas}")),
                    f"scale {deployment}")


Opener = Callable[[urllib.request.Request, float], Any]


def default_opener(req: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 (operator-configured URL)


class KnobError(RuntimeError):
    """The gateway refused or could not confirm a knob write."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class GatewayKnobs:
    base_url: str
    admin_key: str = field(repr=False)
    opener: Opener = default_opener

    def _call(self, method: str, tenant: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/admin/tenants/{tenant}/knobs", data=data, method=method,
            headers={"Content-Type": "application/json", "X-PolyForge-Admin-Key": self.admin_key})
        try:
            with self.opener(req, 15.0) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            detail = err.read()[:300].decode("utf-8", "replace")
            raise KnobError(f"{method} knobs for {tenant} -> {err.code}: {detail}", err.code) from err

    def apply(self, tenant: str, knobs: Knobs) -> dict:
        """Write the given knobs and read them back.

        The endpoint replaces both fields at once, so a decision that sets only
        one keeps the other from the gateway's current value. A tenant the
        gateway has no knobs for must get both: its cache is then unbounded,
        and no knob value expresses "unbounded" (0 disables the cache)."""
        tier, cache = knobs.model_tier, knobs.cache_size_mb
        if tier is None or cache is None:
            try:
                have = self._call("GET", tenant)
            except KnobError as err:
                if err.status == 404:
                    raise KnobError(f"tenant {tenant} has no knobs yet: set model_tier and "
                                    "cache_size_mb together the first time", 404) from err
                raise
            tier = have.get("model_tier", UNPINNED) if tier is None else tier
            cache = have["cache_size_mb"] if cache is None else cache
        self._call("PUT", tenant, {"model_tier": tier, "cache_size_mb": cache})
        back = self._call("GET", tenant)
        # The gateway stores "none" as written; "" also means unpinned.
        accepted = (tier, "") if tier == UNPINNED else (tier,)
        ok = back.get("model_tier") in accepted and back.get("cache_size_mb") == cache
        return {"ok": ok, "model_tier": back.get("model_tier"), "cache_size_mb": back.get("cache_size_mb")}


@dataclass
class Actuator:
    bounds: Bounds
    kube: Kube | None
    knobs: GatewayKnobs | None
    log: Callable[[dict], None]
    clock: Callable[[], float] = time.time

    def current(self) -> dict[str, tuple[int, int]]:
        if self.kube is None:
            return {}
        return {tier: self.kube.replicas(tb.deployment) for tier, tb in self.bounds.tiers.items()}

    def apply(self, decision: Decision, due_unix: float | None = None) -> dict:
        record: dict[str, Any] = {"event": "decision", "step": decision.step, "t_s": decision.t_s,
                                  "due_unix": due_unix, "requested": dict(decision.replicas), "errors": []}
        if decision.replicas:
            if self.kube is None:
                raise DecisionError("decision sets replicas but replica actuation is off")
            now = self.current()
            spec = {tier: s for tier, (s, _ready) in now.items()}
            target, clamped = clamp_replicas(decision.replicas, spec, self.bounds)
            record.update(previous=spec, applied=target, clamped=clamped)
            for tier, n in target.items():
                if spec.get(tier) != n:
                    self.kube.scale(self.bounds.tiers[tier].deployment, n)
        record["knobs"] = {}
        if decision.tenants and self.knobs is None:
            raise DecisionError("decision sets tenant knobs but knob actuation is off")
        for tenant, k in decision.tenants.items():
            try:
                result = self.knobs.apply(tenant, k)
            except (KnobError, OSError) as err:
                record["errors"].append(f"{tenant}: {err}")
                continue
            record["knobs"][tenant] = result
            if not result["ok"]:
                record["errors"].append(f"{tenant}: read-back {result} differs from the decision")
        record["applied_unix"] = self.clock()
        if due_unix is not None:
            record["lag_s"] = record["applied_unix"] - due_unix
        self.log(record)
        return record


def read_plan(path: Path, bounds: Bounds) -> list[Decision]:
    """Decisions of a JSON-lines plan, sorted by t_s; blank lines skipped."""
    decisions = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            decisions.append(parse_decision(json.loads(line), bounds))
        except (json.JSONDecodeError, DecisionError) as err:
            raise DecisionError(f"{path}:{n}: {err}") from err
    return sorted(decisions, key=lambda d: d.t_s)


def watch_ready(actuator: Actuator, changes: Mapping[str, tuple[int, int]], since: float,
                until: float, step: int | None, sleep: Callable[[float], None] = time.sleep) -> None:
    """Poll until each scaled tier reports its target ready count or `until`
    passes; log when each got there. `changes` maps tier -> (before, target).
    A scale-down reports at once (terminating pods leave the ready count
    immediately) although its pods drain for the preStop delay."""
    pending = dict(changes)
    while pending and actuator.clock() < until and actuator.kube is not None:
        for tier in list(pending):
            before, target = pending[tier]
            _spec, ready = actuator.kube.replicas(actuator.bounds.tiers[tier].deployment)
            if ready == target:
                now = actuator.clock()
                actuator.log({"event": "ready", "step": step, "tier": tier, "from": before,
                              "replicas": target, "direction": "up" if target > before else "down",
                              "ready_unix": now, "after_s": now - since})
                del pending[tier]
        if pending:
            sleep(min(READY_POLL_S, max(0.0, until - actuator.clock())))
    for tier, (before, target) in pending.items():
        actuator.log({"event": "not_ready", "step": step, "tier": tier, "from": before,
                      "replicas": target, "waited_s": actuator.clock() - since})


def replay(actuator: Actuator, decisions: Iterable[Decision], start_unix: float,
           sleep: Callable[[float], None] = time.sleep, fail_fast: bool = False) -> int:
    """Apply each decision at start + t_s; between decisions, watch readiness.
    Returns how many decisions were refused or failed a knob write (each is
    logged; the run goes on unless fail_fast)."""
    failed = 0
    plan = list(decisions)
    for i, decision in enumerate(plan):
        due = start_unix + decision.t_s
        wait = due - actuator.clock()
        if wait > 0:
            sleep(wait)
        before = {t: s for t, (s, _r) in actuator.current().items()}
        try:
            record = actuator.apply(decision, due_unix=due)
        except DecisionError as err:
            failed += 1
            actuator.log({"event": "refused", "step": decision.step, "t_s": decision.t_s,
                          "due_unix": due, "error": str(err)})
            if fail_fast:
                raise
            continue
        if record["errors"]:
            failed += 1
            if fail_fast:
                raise DecisionError(f"step {decision.step}: {record['errors']}")
        changed = {t: (before.get(t, 0), n) for t, n in record.get("applied", {}).items()
                   if before.get(t) != n}
        next_due = start_unix + plan[i + 1].t_s if i + 1 < len(plan) else due + 300.0
        if changed:
            watch_ready(actuator, changed, since=record["applied_unix"], until=next_due,
                        step=decision.step, sleep=sleep)
    return failed


def jsonl_logger(path: Path | None) -> Callable[[dict], None]:
    def log(record: dict) -> None:
        line = json.dumps(record, sort_keys=True)
        print(line, flush=True)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    return log


def admin_key(path: str | None) -> str:
    key = os.environ.get("PF_ADMIN_KEY", "")
    if path:
        key = Path(path).read_text(encoding="utf-8").strip()
    if not key:
        raise SystemExit("no admin key: pass --admin-key-file or set PF_ADMIN_KEY")
    return key


def build(args: argparse.Namespace) -> Actuator:
    bounds = Bounds.load(Path(args.bounds))
    kube = None if args.skip_replicas else KubectlKube(bounds.namespace, context=args.context)
    needs_gateway = getattr(args, "cmd", "") in ("apply", "replay")
    knobs = GatewayKnobs(args.gateway, admin_key(args.admin_key_file)) if needs_gateway and not args.skip_knobs else None
    return Actuator(bounds=bounds, kube=kube, knobs=knobs,
                    log=jsonl_logger(Path(args.log) if args.log else None))


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bounds", default=str(DEFAULT_BOUNDS))
    ap.add_argument("--context", default="kind-polyforge-testbed", help="kubectl context")
    ap.add_argument("--gateway", default=DEFAULT_GATEWAY, help="gateway base URL (NodePort)")
    ap.add_argument("--admin-key-file", default=None, help="file holding the gateway admin key")
    ap.add_argument("--log", default=None, help="append JSON lines here as well as stdout")
    ap.add_argument("--skip-replicas", action="store_true", help="mode A: knobs only")
    ap.add_argument("--skip-knobs", action="store_true", help="replicas only")
    sub = ap.add_subparsers(dest="cmd", required=True)
    one = sub.add_parser("apply", help="apply one decision now")
    one.add_argument("--decision", required=True, help="decision JSON file")
    rep = sub.add_parser("replay", help="apply a JSON-lines plan on its own clock")
    rep.add_argument("--plan", required=True)
    rep.add_argument("--start", default="now", help="'now' or a unix time shared with the load run")
    rep.add_argument("--fail-fast", action="store_true", help="stop at the first refused decision")
    sub.add_parser("status", help="print spec and ready replicas per tier")
    return ap.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    actuator = build(args)
    try:
        if args.cmd == "status":
            for tier, (spec, ready) in actuator.current().items():
                print(f"{tier}: spec {spec}, ready {ready}")
            return 0
        if args.cmd == "apply":
            obj = json.loads(Path(args.decision).read_text(encoding="utf-8"))
            record = actuator.apply(parse_decision(obj, actuator.bounds))
            return 1 if record["errors"] else 0
        start = time.time() if args.start == "now" else float(args.start)
        refused = replay(actuator, read_plan(Path(args.plan), actuator.bounds), start,
                         fail_fast=args.fail_fast)
        return 1 if refused else 0
    except (DecisionError, RuntimeError) as err:
        print(f"actuate: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
