"""Planner service tests (W31). Run: python -m unittest -v"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from planner import PlannerCore, make_handler


def tenant(tid: str, **overrides) -> dict:
    base = {
        "tenant_id": tid,
        "slo_class": "standard",
        "hourly_budget_usd": 5.0,
        "replica_min": 1,
        "replica_max": 10,
        "state": {"replicas": 2, "cache_mb": 128, "tier": "small"},
        "demand": {"rps": {"crud_read": 1.0}, "crud_base_ms": 50.0},
    }
    base.update(overrides)
    return base


class PlannerCoreTests(unittest.TestCase):
    def test_plan_returns_entry_per_tenant(self):
        core = PlannerCore()
        out = core.plan({"tenants": [tenant("a"), tenant("b")]})
        self.assertEqual(out["solver"], "jcac-lattice-v1")
        self.assertEqual(set(out["plans"]), {"a", "b"})
        for plan in out["plans"].values():
            self.assertGreaterEqual(plan["replicas"], 1)
            self.assertIn(plan["tier"], ("none", "small", "mid", "large"))

    def test_forecast_state_survives_across_calls(self):
        core = PlannerCore()
        surge = tenant("a", demand={"rps": {"crud_read": 400.0}, "crud_base_ms": 50.0})
        replicas = []
        for _ in range(3):
            out = core.plan({"tenants": [surge]})
            surge["state"] = out["plans"]["a"] | {"tier": out["plans"]["a"]["tier"]}
            replicas.append(out["plans"]["a"]["replicas"])
        self.assertGreater(replicas[-1], 2, f"no scale-up across cycles: {replicas}")

    def test_interference_throttles_noisy_tenant(self):
        demand = {"rps": {"chat": 4.0}, "crud_base_ms": 50.0}
        clean = PlannerCore().plan({"tenants": [tenant("a", demand=demand)]})
        noisy = PlannerCore().plan(
            {"tenants": [tenant("a", demand=demand, interference=5.0)],
             "weights": {"alpha": 1, "beta": 2, "gamma": 2.0}}
        )
        self.assertLessEqual(
            noisy["plans"]["a"]["replicas"], clean["plans"]["a"]["replicas"]
        )
        self.assertLessEqual(
            noisy["plans"]["a"]["cache_mb"], clean["plans"]["a"]["cache_mb"]
        )

    def test_rejects_garbage(self):
        core = PlannerCore()
        with self.assertRaises(ValueError):
            core.plan({"tenants": []})
        with self.assertRaises(ValueError):
            core.plan({"tenants": [tenant("a", state={"tier": "colossal"})]})


class PlannerHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(PlannerCore()))
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _post(self, path: str, payload: dict):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())

    def test_healthz(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/healthz", timeout=5) as resp:
            self.assertEqual(resp.status, 200)

    def test_plan_round_trip(self):
        status, body = self._post("/v1/plan", {"tenants": [tenant("acme")]})
        self.assertEqual(status, 200)
        self.assertIn("acme", body["plans"])

    def test_bad_request_is_400_not_500(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/plan",
            data=b'{"tenants": []}',
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
