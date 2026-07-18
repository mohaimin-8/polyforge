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
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# The controller implementation lives with the research simulator so the
# offline and online planner are provably the same code.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research" / "jcac_sim"))

from controller import ClusterLimits, Forecast, JCACController, Weights  # noqa: E402
from model import TIERS, Demand, TenantConfig, TenantState  # noqa: E402

SOLVER_NAME = "jcac-lattice-v1"


class PlannerCore:
    """Request validation + a controller instance persisted across calls.

    `default_weights` are deployment-level fallbacks (Helm values / CLI
    flags) used only when the request omits a weight — a request that
    names its weights always wins, the operator stays the source of truth.
    """

    def __init__(self, default_weights: dict | None = None,
                 forecast_method: str = "trend") -> None:
        self._controller: JCACController | None = None
        self._signature: tuple | None = None
        self._defaults = {"alpha": 1.0, "beta": 2.0, "gamma": 0.5}
        self._defaults.update(default_weights or {})
        # Deployment-selected forecaster (Wave 5): the service could
        # previously only run the controller default, so the live planner
        # was unable to exploit the measured forecasting results at all.
        self._forecast_method = forecast_method
        # ThreadingHTTPServer serves each request on its own thread; the
        # controller and its forecast state are shared and not re-entrant.
        # One operator calling every 10 s never contends, but a second
        # caller must serialize rather than corrupt forecast history.
        self._lock = threading.Lock()

    def plan(self, payload: dict) -> dict:
        with self._lock:
            return self._plan_locked(payload)

    def _plan_locked(self, payload: dict) -> dict:
        weights = payload.get("weights") or {}
        limits = payload.get("limits") or {}
        tenants = payload.get("tenants")
        if not isinstance(tenants, list) or not tenants:
            raise ValueError("tenants must be a non-empty list")

        configs, states, demands, interference = {}, {}, {}, {}
        for entry in tenants:
            tid = entry.get("tenant_id")
            if not tid:
                raise ValueError("tenant_id is required")
            state = entry.get("state") or {}
            tier = state.get("tier", "small")
            if tier not in TIERS:
                raise ValueError(f"unknown tier {tier!r}")
            configs[tid] = TenantConfig(
                tenant_id=tid,
                slo_class=entry.get("slo_class", "standard"),
                hourly_budget_usd=float(entry.get("hourly_budget_usd", 5.0)),
                replica_min=int(entry.get("replica_min", 1)),
                replica_max=int(entry.get("replica_max", 10)),
                fairness_weight=float(entry.get("fairness_weight", 0.5)),
            )
            states[tid] = TenantState(
                replicas=int(state.get("replicas", 2)),
                cache_mb=int(state.get("cache_mb", 128)),
                tier=tier,
            )
            demand = entry.get("demand") or {}
            demands[tid] = Demand(
                rps={str(k): float(v) for k, v in (demand.get("rps") or {}).items()},
                crud_base_ms=float(demand.get("crud_base_ms", 50.0)),
            )
            interference[tid] = float(entry.get("interference", 0.0))

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


def make_handler(core: PlannerCore):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet: the operator logs calls
            pass

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/healthz":
                self._send(200, b"ok", "text/plain")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/v1/plan":
                self._send(404, b"not found", "text/plain")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                response = core.plan(payload)
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
    args = ap.parse_args()
    core = PlannerCore(
        default_weights={"alpha": args.alpha, "beta": args.beta, "gamma": args.gamma},
        forecast_method=args.forecast,
    )
    server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(core))
    print(f"jcac planner ({SOLVER_NAME}) listening on :{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
