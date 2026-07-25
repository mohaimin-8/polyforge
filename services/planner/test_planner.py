"""Planner service tests (W31). Run: python -m unittest -v"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from planner import PlannerCore, make_handler, resolve_auth_token


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

    def test_state_export_import_round_trip(self):
        # Warm one tenant, export the snapshot, import into a fresh core via HTTP.
        self._post("/v1/plan", {"tenants": [tenant("acme")]})
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/v1/state", timeout=5) as r:
            snap = json.loads(r.read())
        self.assertIn("acme", snap.get("forecasts", {}))
        status, body = self._post("/v1/state", snap)
        self.assertEqual(status, 200)
        self.assertIn("restored_tenants", body)

    def test_bad_request_is_400_not_500(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/plan",
            data=b'{"tenants": []}',
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 400)


class PlannerAuthTests(unittest.TestCase):
    """The planner's /v1/* routes steer capacity for every tenant and
    export/overwrite per-tenant demand history. NetworkPolicy is inert on
    non-enforcing CNIs, so the bearer token is the defence-in-depth layer:
    when configured it must be enforced on every /v1/ route and verb, and
    /healthz must stay open for kubelet probes."""

    TOKEN = "s3cret-planner-token"

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(PlannerCore(), cls.TOKEN))
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _request(self, path: str, token: str | None, method: str = "POST"):
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps({"tenants": [tenant("acme")]}).encode() if method == "POST" else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data,
            headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status
        except urllib.error.HTTPError as err:
            return err.code

    def test_healthz_stays_open_for_probes(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/healthz", timeout=5) as resp:
            self.assertEqual(resp.status, 200)

    def test_plan_rejects_missing_token(self):
        self.assertEqual(self._request("/v1/plan", None), 401)

    def test_plan_rejects_wrong_token(self):
        self.assertEqual(self._request("/v1/plan", "wrong-token"), 401)

    def test_plan_accepts_correct_token(self):
        self.assertEqual(self._request("/v1/plan", self.TOKEN), 200)

    def test_state_export_is_guarded(self):
        # GET /v1/state discloses every tenant's demand history.
        self.assertEqual(self._request("/v1/state", None, method="GET"), 401)
        self.assertEqual(self._request("/v1/state", self.TOKEN, method="GET"), 200)

    def test_state_import_is_guarded(self):
        # POST /v1/state overwrites the forecast state the controller plans on.
        self.assertEqual(self._request("/v1/state", None), 401)

    def test_unknown_route_does_not_leak_existence_before_auth(self):
        # An unauthenticated caller gets 401, not a 404 route oracle.
        self.assertEqual(self._request("/v1/secret-admin", None), 401)


class ResolveAuthTokenTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop("POLYFORGE_PLANNER_TOKEN", None)

    def tearDown(self):
        os.environ.pop("POLYFORGE_PLANNER_TOKEN", None)
        if self._saved is not None:
            os.environ["POLYFORGE_PLANNER_TOKEN"] = self._saved

    def test_none_when_unconfigured(self):
        # The historical posture the frozen eval harness runs against.
        self.assertIsNone(resolve_auth_token())

    def test_reads_env(self):
        os.environ["POLYFORGE_PLANNER_TOKEN"] = "from-env"
        self.assertEqual(resolve_auth_token(), "from-env")

    def test_file_overrides_env(self):
        os.environ["POLYFORGE_PLANNER_TOKEN"] = "from-env"
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write("  from-file\n")
            path = fh.name
        try:
            self.assertEqual(resolve_auth_token(path), "from-file")
        finally:
            os.unlink(path)

    def test_empty_file_fails_loudly(self):
        # Never silently fall back to "open" because a Secret mounted empty.
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write("   \n")
            path = fh.name
        try:
            with self.assertRaises(ValueError):
                resolve_auth_token(path)
        finally:
            os.unlink(path)


class PlannerLifecycleTests(unittest.TestCase):
    """W31 hardening: tenant churn and weight retunes must not cost the
    fleet its forecast history, and concurrent callers must serialize."""

    def _cycle(self, core, tenants, n=3):
        out = None
        for _ in range(n):
            out = core.plan({"tenants": tenants})
        return out

    def test_tenant_arrival_preserves_survivor_forecasts(self):
        core = PlannerCore()
        self._cycle(core, [tenant("a"), tenant("b")])
        history_a = list(core._controller.forecasts["a"].history)
        self.assertGreaterEqual(len(history_a), 2)
        out = core.plan({"tenants": [tenant("a"), tenant("b"), tenant("c")]})
        self.assertIn("c", out["plans"])
        # Survivor history grew by exactly the new observation — not reset.
        self.assertEqual(len(core._controller.forecasts["a"].history),
                         min(len(history_a) + 1, 3))
        self.assertIn("c", core._controller.forecasts)

    def test_tenant_departure_drops_state_keeps_survivors(self):
        core = PlannerCore()
        self._cycle(core, [tenant("a"), tenant("b")])
        core.plan({"tenants": [tenant("a")]})
        self.assertNotIn("b", core._controller.forecasts)
        self.assertNotIn("b", core._controller.capacity_scale)
        self.assertGreaterEqual(len(core._controller.forecasts["a"].history), 2)

    def test_weight_retune_rebuilds_optimizer_not_observations(self):
        core = PlannerCore()
        self._cycle(core, [tenant("a")])
        forecast_a = core._controller.forecasts["a"]
        first = core._controller
        core.plan({"tenants": [tenant("a")],
                   "weights": {"alpha": 1, "beta": 2, "gamma": 1.5}})
        self.assertIsNot(core._controller, first)  # optimizer rebuilt
        self.assertIs(core._controller.forecasts["a"], forecast_a)  # history kept

    def test_concurrent_plans_serialize_without_corruption(self):
        core = PlannerCore()
        errors = []

        fleet = [tenant("a"), tenant("b"), tenant("c")]

        def hammer():
            try:
                for _ in range(10):
                    core.plan({"tenants": fleet})
            except Exception as err:  # noqa: BLE001
                errors.append(err)

        threads = [threading.Thread(target=hammer) for _ in range(3)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(errors, [])
        # All three tenants ended with live forecast state.
        self.assertEqual(set(core._controller.forecasts), {"a", "b", "c"})


class ForecastMethodPlumbingTests(unittest.TestCase):
    """Wave 5: the service can select the forecaster at deployment time
    (previously the live planner could only ever run the trend default)."""

    def test_forecast_method_reaches_controller_and_arrivals(self):
        core = PlannerCore(forecast_method="holt")
        core.plan({"tenants": [tenant("a")]})
        self.assertEqual(core._controller.forecasts["a"].method, "holt")
        core.plan({"tenants": [tenant("a"), tenant("b")]})  # churn arrival
        self.assertEqual(core._controller.forecasts["b"].method, "holt")

    def test_seasonal_mr_is_a_valid_service_method(self):
        core = PlannerCore(forecast_method="seasonal_mr")
        out = core.plan({"tenants": [tenant("a")]})
        self.assertIn("a", out["plans"])
        self.assertEqual(core._controller.forecasts["a"].method, "seasonal_mr")


class PlannerFailoverTests(unittest.TestCase):
    """W31 failover: a replacement PlannerCore must resume forecast history
    from a snapshot, whether pushed via restore() or loaded from a state
    file, and the very first plan after failover must reflect it."""

    def _warm(self, core, tid="a", n=6):
        surge = tenant(tid, demand={"rps": {"crud_read": 300.0}, "crud_base_ms": 50.0})
        for _ in range(n):
            core.plan({"tenants": [surge]})
        return core

    def test_snapshot_restore_round_trip(self):
        warm = self._warm(PlannerCore())
        snap = warm.snapshot()
        self.assertIn("a", snap["forecasts"])

        cold = PlannerCore()
        applied = cold.restore(snap)  # no controller yet -> buffered
        self.assertEqual(applied, 0)
        out = cold.plan({"tenants": [tenant("a")]})  # buffer applied on build
        self.assertIn("a", out["plans"])
        self.assertEqual(len(cold._controller.forecasts["a"].history),
                         len(warm._controller.forecasts["a"].history))

    def test_restore_applies_immediately_when_tenant_known(self):
        warm = self._warm(PlannerCore())
        snap = warm.snapshot()
        cold = PlannerCore()
        cold.plan({"tenants": [tenant("a")]})  # controller exists, 1 obs
        applied = cold.restore(snap)
        self.assertEqual(applied, 1)
        self.assertEqual(len(cold._controller.forecasts["a"].history),
                         len(warm._controller.forecasts["a"].history))

    def test_state_file_survives_restart(self):
        import tempfile, os
        d = tempfile.mkdtemp()
        path = os.path.join(d, "state.json")
        core = PlannerCore(state_file=path)
        self._warm(core)
        self.assertTrue(os.path.exists(path))

        # A brand-new process (same PVC) loads the file and resumes history.
        replacement = PlannerCore(state_file=path)
        out = replacement.plan({"tenants": [tenant("a")]})
        self.assertIn("a", out["plans"])
        self.assertGreater(len(replacement._controller.forecasts["a"].history), 1)

    def test_corrupt_state_file_does_not_crash_boot(self):
        import tempfile, os
        d = tempfile.mkdtemp()
        path = os.path.join(d, "state.json")
        with open(path, "w") as f:
            f.write("{ this is not json")
        core = PlannerCore(state_file=path)  # must not raise
        out = core.plan({"tenants": [tenant("a")]})
        self.assertIn("a", out["plans"])


class PublishedPhysicsTests(unittest.TestCase):
    """4.3: the live planner must always plan against published physics,
    even if a sensitivity override was left in a module global by other
    code sharing the process."""

    def tearDown(self):
        import model
        model.set_economy()
        model.set_model_form()

    def test_planner_core_pins_published_physics_on_construction(self):
        import model
        # Simulate contamination: another consumer left an override active.
        model.set_economy(cache_hit_max=0.285, cache_half_mb=19.0)
        model.set_model_form(mixture_p95=True, congestion_exponent=0.86)
        self.assertEqual(model.CACHE_HIT_MAX, 0.285)

        PlannerCore()  # construction must reset the globals
        self.assertEqual(model.CACHE_HIT_MAX, 0.85)   # published default
        self.assertEqual(model.CACHE_HALF_MB, 256.0)
        self.assertFalse(model.MIXTURE_P95)
        self.assertEqual(model.CONGESTION_EXPONENT, 1.0)
        self.assertIsNone(model.P95_TAIL)

    def test_plan_is_identical_regardless_of_prior_override(self):
        import model
        clean = PlannerCore().plan({"tenants": [tenant("a", demand={
            "rps": {"chat": 4.0}, "crud_base_ms": 50.0})]})
        # Contaminate, then a fresh core must reproduce the clean plan.
        model.set_economy(cache_hit_max=0.1)
        dirty = PlannerCore().plan({"tenants": [tenant("a", demand={
            "rps": {"chat": 4.0}, "crud_base_ms": 50.0})]})
        self.assertEqual(clean["plans"], dirty["plans"])


class PlannerFuzzTests(unittest.TestCase):
    """4.5c fuzz: PlannerCore.plan parses an untrusted JSON body. The HTTP
    handler turns (ValueError, KeyError, TypeError) into 400 and lets
    anything else become a 500, so plan() must raise ONLY those three on
    malformed input (or return a valid dict) - never an uncaught
    AttributeError/IndexError/ZeroDivisionError/etc."""

    ALLOWED = (ValueError, KeyError, TypeError)

    def _fuzz_values(self, rng):
        pool = [None, 0, -1, 1, 1e18, -1e18, float("inf"), float("nan"),
                "", "x", "tier", "small", chr(0), True, False, [], {},
                [1, 2, 3], {"a": 1}, {"rps": None}, {"rps": "no"},
                {"rps": {"chat": "notnum"}}, {"rps": {"chat": float("nan")}}]
        return rng.choice(pool)

    def _random_payload(self, rng):
        # Build a plausibly-shaped but often-broken tenant entry.
        entry = {}
        for field in ("tenant_id", "slo_class", "hourly_budget_usd", "replica_min",
                      "replica_max", "fairness_weight", "interference", "state", "demand"):
            if rng.random() < 0.7:
                entry[field] = self._fuzz_values(rng)
        tenants = self._fuzz_values(rng)
        if rng.random() < 0.5:
            tenants = [entry] * rng.randint(0, 3)
        payload = {}
        if rng.random() < 0.8:
            payload["tenants"] = tenants
        for k in ("weights", "limits"):
            if rng.random() < 0.5:
                payload[k] = self._fuzz_values(rng)
        return payload

    def test_malformed_payloads_only_raise_allowed_types(self):
        import random
        core = PlannerCore()
        for seed in range(2000):
            rng = random.Random(seed)
            payload = self._random_payload(rng)
            try:
                out = core.plan(payload)
            except self.ALLOWED:
                continue  # a clean 400
            except Exception as err:  # noqa: BLE001
                self.fail(f"seed {seed}: plan() raised {type(err).__name__} "
                          f"({err}) on {payload!r} - would be a 500")
            else:
                # If it returned, it must be a well-formed response.
                self.assertIn("plans", out)
                self.assertIsInstance(out["plans"], dict)

    def test_non_dict_bodies_are_rejected_cleanly(self):
        core = PlannerCore()
        for body in [None, [], "string", 42, True, [1, 2]]:
            with self.assertRaises(self.ALLOWED):
                core.plan(body)


if __name__ == "__main__":
    unittest.main()
