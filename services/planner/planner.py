"""PolyForge JCAC planner service (W31).

Exposes the W30 controller as a network service the Go operator calls every
control interval. One endpoint does the work:

    POST /v1/plan
        {"weights": {"alpha": 1, "beta": 2, "gamma": 0.5},
         "limits": {"cache_mb": 4096, "replicas": 60},
         "tenants": [{"tenant_id": "acme", "slo_class": "premium",
                      "hourly_budget_usd": 5.0, "replica_min": 1,
                      "replica_max": 10, "fairness_weight": 0.5,
                      "interference": 0.0,
                      "state": {"replicas": 2, "cache_mb": 128, "tier": "small"},
                      "demand": {"rps": {"chat": 1.5}, "crud_base_ms": 50}}]}
    ->  {"solver": "jcac-lattice-v1",
         "plans": {"acme": {"replicas": 3, "cache_mb": 256, "tier": "mid",
                            "projected_cost_usd": 0.001,
                            "projected_violation": 0.0}}}

    GET /healthz  -> 200 "ok"

Transport is JSON over HTTP rather than gRPC (deviation, ADR 0014): no
protoc toolchain on the dev machine, stdlib-only serving keeps the service
dependency-free, and the operator isolates the choice behind a PlanClient
interface so a gRPC transport can replace this file without touching the
reconciler.

The service keeps the controller (and its per-tenant demand forecasts)
alive between calls; a change in weights or limits rebuilds it. All state
is rebuilt from the next request after a restart — the operator, not the
planner, is the source of truth for applied configuration.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

def _ensure_jcac_sim_importable() -> None:
    """Make the shared simulator importable, robustly.

    The controller/model implementation lives with the research simulator so
    the offline and online planner are provably the same code (ADR 0014). We
    prefer a normal import (works when jcac_sim is installed as a package —
    see research/jcac_sim/pyproject.toml — or already on PYTHONPATH), and
    fall back to the repo-relative path only if that fails, raising a clear
    error rather than a bare ImportError if the layout is missing. This
    replaces an unconditional sys.path.insert with an explicit, testable
    bootstrap that fails loudly instead of silently importing the wrong
    thing."""
    try:
        import model  # noqa: F401
        import controller  # noqa: F401
        return
    except ImportError:
        pass
    sim_dir = Path(__file__).resolve().parents[2] / "research" / "jcac_sim"
    if not (sim_dir / "model.py").exists():
        raise ImportError(
            f"jcac_sim not importable and not found at {sim_dir}; install it "
            "(pip install ./research/jcac_sim) or run with the repo layout intact"
        )
    sys.path.insert(0, str(sim_dir))


_ensure_jcac_sim_importable()

import model  # noqa: E402
from controller import ClusterLimits, Forecast, JCACController, RealizedStep, Weights  # noqa: E402
from model import TIERS, Demand, TenantConfig, TenantState  # noqa: E402

SOLVER_NAME = "jcac-lattice-v1"


def pin_published_physics() -> None:
    """Reset the simulator's mutable economy/form globals to the published
    defaults. The live planner must ALWAYS plan against real physics; the
    set_economy/set_model_form globals exist only for the offline
    sensitivity reruns and must never leak into a serving planner. Calling
    this makes the invariant explicit and enforced rather than assumed."""
    model.set_economy()
    model.set_model_form()


class PlannerCore:
    """Request validation + a controller instance persisted across calls.

    `default_weights` are deployment-level fallbacks (Helm values / CLI
    flags) used only when the request omits a weight — a request that
    names its weights always wins, the operator stays the source of truth.
    """

    def __init__(self, default_weights: dict | None = None,
                 forecast_method: str = "trend",
                 headroom_calibration: bool = False,
                 headroom_cap: float = 4.0,
                 switch_penalty: float | None = None,
                 state_file: str | None = None) -> None:
        # The live planner always runs the published physics; a stray
        # sensitivity override in a module global would silently corrupt
        # every plan, so pin it explicitly at construction.
        pin_published_physics()
        self._controller: JCACController | None = None
        self._signature: tuple | None = None
        self._defaults = {"alpha": 1.0, "beta": 2.0, "gamma": 0.5}
        self._defaults.update(default_weights or {})
        # Deployment-selected forecaster (Wave 5): the service could
        # previously only run the controller default, so the live planner
        # was unable to exploit the measured forecasting results at all.
        self._forecast_method = forecast_method
        # Headroom calibration (B1 follow-up, session 48): the controller
        # learns the plant's capacity from the realized p95 the operator now
        # carries per kind. Off = the published jcac arm, bit-identical.
        self._headroom_calibration = bool(headroom_calibration)
        self._headroom_cap = float(headroom_cap)
        self._switch_penalty = None if switch_penalty is None else float(switch_penalty)
        # ThreadingHTTPServer serves each request on its own thread; the
        # controller and its forecast state are shared and not re-entrant.
        # One operator calling every 10 s never contends, but a second
        # caller must serialize rather than corrupt forecast history.
        self._lock = threading.Lock()
        # Failover (W31): forecast state is per-replica in-memory, so a
        # replacement pod (or a restart) cold-starts every tenant's history.
        # A snapshot pushed via POST /v1/state — or loaded from an optional
        # durable state file — is buffered here and applied to matching
        # tenants the moment the controller for them is (re)built.
        self._state_file = Path(state_file) if state_file else None
        self._pending_restore: dict | None = None
        if self._state_file and self._state_file.exists():
            try:
                self._pending_restore = json.loads(
                    self._state_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._pending_restore = None  # a corrupt file must not crash boot

    def plan(self, payload: dict) -> dict:
        with self._lock:
            result = self._plan_locked(payload)
            self._persist_state_file()
            return result

    def snapshot(self) -> dict:
        """Serializable planner state for failover/hand-off. Thread-safe."""
        with self._lock:
            return self._controller.snapshot() if self._controller else {}

    def restore(self, data: dict) -> int:
        """Buffer a snapshot; apply immediately to any tenants the live
        controller already knows, and keep it pending for tenants that
        appear on later requests. Returns how many tenants were applied now."""
        if not isinstance(data, dict):
            raise ValueError("state must be a JSON object")
        with self._lock:
            self._pending_restore = data
            applied = self._apply_pending()
            self._persist_state_file()
            return applied

    def _apply_pending(self) -> int:
        """Apply the buffered snapshot to the current controller's tenants."""
        if self._pending_restore is None or self._controller is None:
            return 0
        known = set(self._controller.forecasts)
        snap_tids = set((self._pending_restore.get("forecasts") or {}))
        self._controller.restore(self._pending_restore)
        applied = known & snap_tids
        # Drop tenants already reconciled so the buffer only carries the
        # not-yet-seen remainder (and stops re-applying stale history).
        remaining = {
            "forecasts": {t: s for t, s in (self._pending_restore.get("forecasts") or {}).items()
                          if t not in applied},
            "capacity_scale": {t: s for t, s in (self._pending_restore.get("capacity_scale") or {}).items()
                               if t not in applied},
        }
        self._pending_restore = remaining if remaining["forecasts"] else None
        return len(applied)

    def _persist_state_file(self) -> None:
        if not self._state_file or self._controller is None:
            return
        try:
            tmp = self._state_file.with_suffix(self._state_file.suffix + ".tmp")
            tmp.write_text(json.dumps(self._controller.snapshot()), encoding="utf-8")
            tmp.replace(self._state_file)  # atomic swap; no half-written file
        except OSError:
            pass  # durability is best-effort; never fail a plan on a disk error

    def _plan_locked(self, payload: dict) -> dict:
        # The body is untrusted JSON; every shape error must raise a type the
        # handler turns into 400, never an uncaught AttributeError (500).
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        weights = payload.get("weights") or {}
        limits = payload.get("limits") or {}
        if not isinstance(weights, dict) or not isinstance(limits, dict):
            raise ValueError("weights and limits must be JSON objects")
        tenants = payload.get("tenants")
        if not isinstance(tenants, list) or not tenants:
            raise ValueError("tenants must be a non-empty list")

        configs, states, demands, interference = {}, {}, {}, {}
        realized: dict[str, RealizedStep] = {}
        for entry in tenants:
            if not isinstance(entry, dict):
                raise ValueError("each tenant must be a JSON object")
            tid = entry.get("tenant_id")
            if not tid or not isinstance(tid, str):
                raise ValueError("tenant_id is required and must be a string")
            state = entry.get("state") or {}
            demand_obj = entry.get("demand") or {}
            if not isinstance(state, dict) or not isinstance(demand_obj, dict):
                raise ValueError("tenant state and demand must be JSON objects")
            tier = state.get("tier", "small")
            if tier not in TIERS:
                raise ValueError(f"unknown tier {tier!r}")
            # Cache/tier knob bounds (PREREG_WAVE4_LIVE_PLANE.md §Arms): a
            # pinned (min==max) knob makes the controller re-optimize the free
            # knob alone. Absent bounds fall back to the full envelope
            # (cache_max=None = no ceiling, tiers none..large), so a request
            # that omits them plans over exactly the lattice it did before
            # these fields existed.
            cache_max = entry.get("cache_max")
            configs[tid] = TenantConfig(
                tenant_id=tid,
                slo_class=entry.get("slo_class", "standard"),
                hourly_budget_usd=float(entry.get("hourly_budget_usd", 5.0)),
                replica_min=int(entry.get("replica_min", 1)),
                replica_max=int(entry.get("replica_max", 10)),
                fairness_weight=float(entry.get("fairness_weight", 0.5)),
                cache_min=int(entry.get("cache_min", 0)),
                cache_max=int(cache_max) if cache_max is not None else None,
                tier_min=str(entry.get("tier_min", "none")),
                tier_max=str(entry.get("tier_max", "large")),
            )
            states[tid] = TenantState(
                replicas=int(state.get("replicas", 2)),
                cache_mb=int(state.get("cache_mb", 128)),
                tier=tier,
            )
            rps = demand_obj.get("rps") or {}
            if not isinstance(rps, dict):
                raise ValueError("demand.rps must be a JSON object of kind->rate")
            demands[tid] = Demand(
                rps={str(k): float(v) for k, v in rps.items()},
                crud_base_ms=float(demand_obj.get("crud_base_ms", 50.0)),
            )
            interference[tid] = float(entry.get("interference", 0.0))
            p95 = demand_obj.get("realized_p95_ms") or {}
            if isinstance(p95, dict) and p95:
                realized[tid] = _realized_step(states[tid], demands[tid], p95)

        # Weights/limits only: the tenant *set* is deliberately not part of
        # the rebuild signature. Multi-tenant platforms churn tenants, and a
        # rebuild costs every tenant its forecast history (the seasonal
        # forecaster needs minutes of observations to re-warm) — one tenant
        # arriving must never cold-start the fleet's demand forecasts.
        signature = (
            float(weights.get("alpha", self._defaults["alpha"])),
            float(weights.get("beta", self._defaults["beta"])),
            float(weights.get("gamma", self._defaults["gamma"])),
            int(limits.get("cache_mb", 4096)),
            int(limits.get("replicas", 60)),
        )
        if self._controller is None or signature != self._signature:
            old = self._controller
            self._controller = JCACController(
                configs,
                weights=Weights(alpha=signature[0], beta=signature[1], gamma=signature[2]),
                limits=ClusterLimits(cache_mb=signature[3], replicas=signature[4]),
                forecast_method=self._forecast_method,
                headroom_calibration=self._headroom_calibration,
                headroom_cap=self._headroom_cap,
                switch_penalty=self._switch_penalty,
            )
            self._signature = signature
            if old is not None:
                # A weights/limits retune rebuilds the optimizer, not the
                # observations: surviving tenants keep their demand history
                # and learned capacity corrections.
                for tid, forecast in old.forecasts.items():
                    if tid in self._controller.forecasts:
                        self._controller.forecasts[tid] = forecast
                for tid, scale in old.capacity_scale.items():
                    if tid in self._controller.capacity_scale:
                        self._controller.capacity_scale[tid] = scale
        else:
            ctl = self._controller
            ctl.configs = configs  # budgets/SLO may be retuned live
            # Tenant churn reconciliation: arrivals get a fresh forecaster
            # (matching the fleet's method), departures are dropped,
            # survivors keep their history untouched.
            method = (next(iter(ctl.forecasts.values())).method
                      if ctl.forecasts else self._forecast_method)
            for tid in configs:
                if tid not in ctl.forecasts:
                    ctl.forecasts[tid] = Forecast(method=method)
                    ctl.capacity_scale[tid] = 1.0
            for tid in [t for t in ctl.forecasts if t not in configs]:
                del ctl.forecasts[tid]
                ctl.capacity_scale.pop(tid, None)
                ctl._projected.pop(tid, None)

        # Seed restored history before planning, so the very first plan after
        # a failover already reflects the pre-failover demand history.
        self._apply_pending()

        # What the plant served last interval, before deciding the next.
        # No-op on a controller without headroom calibration.
        if realized:
            self._controller.observe_realized(realized)
        plans = self._controller.plan(states, demands, interference=interference)
        return {
            "solver": SOLVER_NAME,
            "plans": {
                tid: {
                    "replicas": p.state.replicas,
                    "cache_mb": p.state.cache_mb,
                    "tier": p.state.tier,
                    "projected_cost_usd": round(p.projected_cost_usd, 6),
                    "projected_violation": round(p.projected_violation, 4),
                }
                for tid, p in plans.items()
            },
        }


CRUD_FAMILY = ("crud_read", "crud_write", "batch")
AI_FAMILY = ("chat", "embed", "agent")


def _realized_step(state: TenantState, demand: Demand, p95: dict) -> RealizedStep:
    """Fold the operator's per-kind realized p95 into the two families the
    plant model predicts (worst kind per family; a family with no
    observation reads 0, which the controller treats as unobserved)."""
    def worst(kinds) -> float:
        vals = [float(p95[k]) for k in kinds if k in p95 and float(p95[k]) > 0.0]
        return max(vals) if vals else 0.0
    return RealizedStep(state, demand, worst(CRUD_FAMILY), worst(AI_FAMILY))


def resolve_auth_token(cli_token_file: str | None = None) -> str | None:
    """Resolve the shared bearer token guarding /v1/*, or None if unset.

    Precedence: --auth-token-file (a mounted k8s Secret) over
    POLYFORGE_PLANNER_TOKEN (env). Returns None when neither is configured,
    which leaves the service open — the historical posture the frozen eval
    harness and every closed campaign run against, so enabling auth can
    never retroactively change a published number. The Helm chart sets the
    token by default, so the *deployed* posture is authenticated while the
    research path is untouched (ADR 0014 defence-in-depth follow-up).
    """
    if cli_token_file:
        token = Path(cli_token_file).read_text(encoding="utf-8").strip()
        if not token:
            raise ValueError(f"auth token file {cli_token_file} is empty")
        return token
    return os.environ.get("POLYFORGE_PLANNER_TOKEN") or None


def make_handler(core: PlannerCore, auth_token: str | None = None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet: the operator logs calls
            pass

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            """Constant-time bearer check. /healthz stays open for probes;
            every /v1/* route is guarded because GET /v1/state exports and
            POST /v1/state overwrites per-tenant demand history, and
            POST /v1/plan steers capacity for every tenant at once."""
            if auth_token is None:
                return True
            header = self.headers.get("Authorization", "")
            scheme, _, presented = header.partition(" ")
            if scheme.lower() != "bearer":
                return False
            return hmac.compare_digest(presented.strip(), auth_token)

        def _deny(self) -> None:
            # Drain the request body before replying. Rejecting a POST without
            # consuming what the peer already sent makes the OS reset the
            # connection (WinError 10053) instead of delivering our 401, so an
            # unauthorized client would see a transport error rather than the
            # status. Cap the drain so an unauthenticated peer cannot make us
            # read an unbounded body.
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length > 0:
                self.rfile.read(min(length, 1 << 20))
            body = json.dumps({"error": "unauthorized"}).encode()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("WWW-Authenticate", "Bearer")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def do_GET(self):
            if self.path == "/healthz":
                self._send(200, b"ok", "text/plain")
                return
            if not self._authorized():
                self._deny()
                return
            if self.path == "/v1/state":
                # Export the forecast snapshot for hand-off/backup.
                body = json.dumps(core.snapshot()).encode()
                self._send(200, body, "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if not self._authorized():
                self._deny()
                return
            if self.path == "/v1/plan":
                self._handle(core.plan)
            elif self.path == "/v1/state":
                # Import a snapshot into a replacement replica.
                self._handle(lambda p: {"restored_tenants": core.restore(p)})
            else:
                self._send(404, b"not found", "text/plain")

        def _handle(self, fn):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                response = fn(payload)
            except (ValueError, KeyError, TypeError) as err:
                body = json.dumps({"error": str(err)}).encode()
                self._send(400, body, "application/json")
                return
            self._send(200, json.dumps(response).encode(), "application/json")

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description="JCAC planner service")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--alpha", type=float, default=1.0,
                    help="default cost weight when a request omits weights")
    ap.add_argument("--beta", type=float, default=2.0,
                    help="default SLO-violation weight when a request omits weights")
    ap.add_argument("--gamma", type=float, default=0.5,
                    help="default fairness weight when a request omits weights "
                         "(0 disables the fairness objective, e.g. for ablation)")
    ap.add_argument("--forecast", default="trend",
                    choices=("persistence", "trend", "holt", "seasonal", "seasonal_mr"),
                    help="demand forecaster (holt is the evidence-based "
                         "recommendation; seasonal_mr adds the multi-resolution "
                         "layer that can see daily cycles live)")
    ap.add_argument("--state-file", default=None,
                    help="optional path for durable forecast state; loaded on "
                         "boot and atomically rewritten after each plan, so a "
                         "restart or a PVC-backed replacement pod resumes "
                         "demand history instead of cold-starting")
    ap.add_argument("--headroom-calibration", action="store_true",
                    help="learn the plant's capacity from the realized p95 the "
                         "operator reports (B1 follow-up); off = the published "
                         "jcac arm")
    ap.add_argument("--headroom-cap", type=float, default=4.0,
                    help="upper bound on the learned capacity scale")
    ap.add_argument("--switch-penalty", type=float, default=None,
                    help="hysteresis per changed knob in objective units; "
                         "unset = the published 0.05. The corrected arm passes "
                         "half a replica-step (SWITCH_PENALTY_HALF_REPLICA)")
    ap.add_argument("--auth-token-file", default=None,
                    help="path to a file holding the shared bearer token that "
                         "guards /v1/plan and /v1/state; overrides the "
                         "POLYFORGE_PLANNER_TOKEN env var. When neither is set "
                         "the service is unauthenticated (the research-harness "
                         "posture) and says so loudly at boot")
    args = ap.parse_args()
    auth_token = resolve_auth_token(args.auth_token_file)
    core = PlannerCore(
        default_weights={"alpha": args.alpha, "beta": args.beta, "gamma": args.gamma},
        forecast_method=args.forecast,
        state_file=args.state_file,
        headroom_calibration=args.headroom_calibration,
        headroom_cap=args.headroom_cap,
        switch_penalty=args.switch_penalty,
    )
    server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(core, auth_token))
    print(f"jcac planner ({SOLVER_NAME}) listening on :{args.port}")
    if auth_token is None:
        print("WARNING: planner is UNAUTHENTICATED — /v1/state exports and "
              "overwrites per-tenant demand history and /v1/plan steers every "
              "tenant's capacity. Set --auth-token-file or "
              "POLYFORGE_PLANNER_TOKEN, and do not rely on NetworkPolicy "
              "alone (it is inert on non-enforcing CNIs).", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
