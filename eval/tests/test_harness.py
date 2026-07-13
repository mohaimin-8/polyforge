"""Harness unit tests (W33/W35a). Run from eval/: python -m pytest tests -q

Covers the properties the smoke week exists to protect: run identity is
deterministic and collision-free, resume skips only valid runs, invalid
results are recorded not dropped, replaying a run_id reproduces its
metrics exactly, and the cluster backend refuses politely without Docker.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[1]
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

from harness import cluster_backend, demo, results, sim_backend, workloads  # noqa: E402
from harness.config import ExperimentSpec, expand, load, run_identity  # noqa: E402
from harness.systems import SYSTEMS, global_mix_transform, lru_miss_cost_factor  # noqa: E402


def tiny_spec(**overrides) -> ExperimentSpec:
    base = dict(
        name="test",
        backend="sim",
        steps=12,
        reps=2,
        systems=["hpa", "static"],
        workloads=["crud_steady"],
        tenant_mixes=["uniform"],
        cluster_sizes=["small"],
        output="results/test.duckdb",
        timeseries_reps=1,
    )
    base.update(overrides)
    return ExperimentSpec(**base)


class TestConfig:
    def test_matrix_size_matches_arithmetic(self):
        spec = tiny_spec()
        assert len(expand(spec)) == spec.total_runs() == 4

    def test_run_ids_unique_and_deterministic(self):
        runs_a = expand(tiny_spec())
        runs_b = expand(tiny_spec())
        ids_a = [r.run_id for r in runs_a]
        assert len(set(ids_a)) == len(ids_a)
        assert ids_a == [r.run_id for r in runs_b]
        assert [r.seed for r in runs_a] == [r.seed for r in runs_b]

    def test_identity_changes_with_any_factor(self):
        spec = tiny_spec()
        base, _ = run_identity(spec, "hpa", "crud_steady", "uniform", "small", 0)
        assert base != run_identity(spec, "jcac", "crud_steady", "uniform", "small", 0)[0]
        assert base != run_identity(spec, "hpa", "agentic", "uniform", "small", 0)[0]
        assert base != run_identity(spec, "hpa", "crud_steady", "whale", "small", 0)[0]
        assert base != run_identity(spec, "hpa", "crud_steady", "uniform", "large", 0)[0]
        assert base != run_identity(spec, "hpa", "crud_steady", "uniform", "small", 1)[0]

    def test_load_rejects_unknown_names(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("name: x\nsystems: [nonsense]\n", encoding="utf-8")
        with pytest.raises(ValueError, match="unknown system"):
            load(bad)
        bad.write_text("name: x\ntypo_key: 1\n", encoding="utf-8")
        with pytest.raises(ValueError, match="unknown keys"):
            load(bad)


class TestWorkloads:
    def test_build_is_deterministic_in_seed(self):
        a = workloads.build("agentic", "whale", "small", seed=42, steps=10)
        b = workloads.build("agentic", "whale", "small", seed=42, steps=10)
        c = workloads.build("agentic", "whale", "small", seed=43, steps=10)
        assert [x["t00"].rps for x in a[1]] == [x["t00"].rps for x in b[1]]
        assert [x["t00"].rps for x in a[1]] != [x["t00"].rps for x in c[1]]

    def test_every_class_mix_size_combination_builds(self):
        for wl in workloads.WORKLOAD_CLASSES:
            for mix in workloads.TENANT_MIXES:
                for size in workloads.CLUSTER_SIZES:
                    ids, buckets, configs, limits = workloads.build(wl, mix, size, 1, 5)
                    assert len(ids) == 8 and len(buckets) == 6
                    assert set(configs) == set(ids)
                    assert limits.replicas > 0

    def test_whale_mix_is_heterogeneous(self):
        _, buckets, configs, _ = workloads.build("crud_steady", "whale", "medium", 1, 5)
        whale_rps = buckets[0]["t00"].total_rps()
        minnow_rps = buckets[0]["t04"].total_rps()
        assert whale_rps > 3 * minnow_rps
        assert configs["t00"].hourly_budget_usd > configs["t04"].hourly_budget_usd


class TestSystems:
    def test_registry_covers_baselines_and_ablations(self):
        assert {"jcac", "hpa", "keda", "firm", "static", "gptcache"} <= set(SYSTEMS)
        assert {s for s in SYSTEMS if s.startswith("jcac_no")} == {
            "jcac_noclassifier", "jcac_nojoint", "jcac_noeviction", "jcac_nofairness",
        }

    def test_lru_factor_comes_from_w28_data(self):
        factor = lru_miss_cost_factor()
        assert 1.2 < factor < 1.8  # W28 measured 1.32 and 1.62 at the two capacities

    def test_global_mix_transform_preserves_volume_not_mix(self):
        from model import Demand

        demands = {
            "a": Demand(rps={"chat": 10.0}),
            "b": Demand(rps={"crud_read": 30.0}),
        }
        blinded = global_mix_transform(demands)
        assert blinded["a"].total_rps() == pytest.approx(10.0)
        assert blinded["b"].total_rps() == pytest.approx(30.0)
        assert blinded["a"].rps["crud_read"] == pytest.approx(7.5)  # sees global mix
        assert blinded["b"].rps["chat"] == pytest.approx(7.5)


class TestSimBackend:
    def test_execute_produces_contract_metrics(self):
        run = expand(tiny_spec())[0]
        outcome = sim_backend.execute(run)
        assert results.check_metrics(outcome["metrics"], run.steps) is None
        assert outcome["timeseries"]  # rep 0 keeps timeseries

    def test_replay_is_bit_identical(self):
        # The W35c spot-check contract: re-executing a run_id must match the
        # original within +/-3%; the sim backend is fully deterministic, so
        # it must match exactly.
        run = expand(tiny_spec(systems=["firm"]))[0]
        first = sim_backend.execute(run)["metrics"]
        second = sim_backend.execute(run)["metrics"]
        assert first == second

    def test_reps_differ_but_share_the_scenario(self):
        runs = expand(tiny_spec(systems=["hpa"], reps=2))
        m0 = sim_backend.execute(runs[0])["metrics"]
        m1 = sim_backend.execute(runs[1])["metrics"]
        assert m0 != m1  # jitter makes reps independent draws
        assert m0["total_cost_usd"] == pytest.approx(m1["total_cost_usd"], rel=0.35)


class TestResults:
    def _db(self, tmp_path):
        return results.connect(tmp_path / "t.duckdb")

    def test_record_and_resume_roundtrip(self, tmp_path):
        con = self._db(tmp_path)
        run = expand(tiny_spec())[0]
        outcome = sim_backend.execute(run)
        results.record(con, run, "valid", 1, outcome)
        assert results.valid_run_ids(con, "test") == {run.run_id}
        # Re-recording the same run replaces, never duplicates.
        results.record(con, run, "valid", 2, outcome)
        assert con.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM metrics").fetchone()[0] == 1

    def test_failed_run_is_recorded_without_metrics(self, tmp_path):
        con = self._db(tmp_path)
        run = expand(tiny_spec())[0]
        results.record(con, run, "failed", 3, None, error="boom")
        assert results.valid_run_ids(con, "test") == set()
        status, err = con.execute(
            "SELECT status, error FROM runs WHERE run_id = ?", [run.run_id]
        ).fetchone()
        assert (status, err) == ("failed", "boom")
        assert con.execute("SELECT count(*) FROM metrics").fetchone()[0] == 0

    def test_check_metrics_rejects_bad_aggregates(self):
        good = {
            "total_cost_usd": 1.0, "mean_violation": 0.1, "violation_step_share": 0.2,
            "mean_jain": 0.9, "cache_hit_rate": 0.3, "crud_p95_ms": 100.0,
            "ai_p95_ms": 900.0, "steps": 11,
        }
        assert results.check_metrics(good, 11) is None
        assert results.check_metrics({**good, "mean_jain": 1.5}, 11)
        assert results.check_metrics({**good, "total_cost_usd": 0.0}, 11)
        assert results.check_metrics({**good, "steps": 10}, 11)
        assert results.check_metrics({**good, "ai_p95_ms": float("nan")}, 11)

    def test_validate_flags_shortfall_and_passes_when_complete(self, tmp_path):
        con = self._db(tmp_path)
        spec = tiny_spec()
        runs = expand(spec)
        for run in runs[:-1]:
            results.record(con, run, "valid", 1, sim_backend.execute(run))
        assert not results.validate(con, "test", len(runs))["ok"]
        results.record(con, runs[-1], "valid", 1, sim_backend.execute(runs[-1]))
        report = results.validate(con, "test", len(runs))
        assert report["ok"] and report["valid_runs"] == 4


class TestDemo:
    def _comparison(self, **kw):
        return demo.compare(steps=12, **kw)

    def test_default_cast_runs_and_reports_every_metric(self):
        c = self._comparison()
        assert [o.system for o in c.outcomes] == list(demo.DEFAULT_SYSTEMS)
        for o in c.outcomes:
            assert set(o.metrics) == {attr for attr, *_ in demo.METRIC_ROWS}

    def test_deltas_are_relative_to_the_baseline(self):
        c = self._comparison()
        assert c.delta("hpa", "total_cost_usd") == 0.0
        static_delta = c.delta("static", "total_cost_usd")
        assert static_delta is not None and static_delta > 0  # over-provision costs more

    def test_near_zero_baseline_suppresses_percentages(self):
        c = self._comparison(workload="crud_steady")  # no AI traffic -> hit rate ~ 0
        assert c.delta("jcac", "cache_hit_rate") is None

    def test_rejects_unknown_system_and_foreign_baseline(self):
        with pytest.raises(ValueError, match="unknown systems"):
            demo.compare(systems=("hpa", "nope"), steps=12)
        with pytest.raises(ValueError, match="baseline"):
            demo.compare(systems=("hpa", "jcac"), baseline="static", steps=12)

    def test_renderings_are_ascii_and_computed(self):
        c = self._comparison()
        table = demo.render_table(c)
        takeaway = demo.render_takeaway(c)
        (table + takeaway).encode("ascii")  # cp1252 terminals must never crash
        assert "PolyForge" in table and "$" in takeaway

    def test_to_dict_round_trips_through_json(self):
        import json

        payload = json.loads(json.dumps(self._comparison().to_dict()))
        assert payload["baseline"] == "hpa"
        assert set(payload["systems"]) == set(demo.DEFAULT_SYSTEMS)


class TestClusterBackend:
    def test_preflight_reports_missing_tools(self):
        missing = cluster_backend.preflight()
        assert isinstance(missing, list)  # on this machine docker is expected missing

    def test_unavailable_raises_not_crashes(self):
        if not cluster_backend.preflight():
            pytest.skip("all cluster tools present; run the real smoke instead")
        run = expand(tiny_spec(backend="cluster"))[0]
        with pytest.raises(cluster_backend.BackendUnavailable):
            cluster_backend.execute(run)

    def test_kind_config_scales_with_cluster_size(self):
        small = cluster_backend.kind_config("small")
        large = cluster_backend.kind_config("large")
        assert small.count("role: worker") == 1
        assert large.count("role: worker") == 5

    def test_k6_script_has_one_scenario_per_tenant(self):
        run = expand(tiny_spec())[0]
        script = cluster_backend.k6_script(run)
        assert script.count("ramping-arrival-rate") == 8
        assert run.run_id in script

    def test_command_plan_provisions_deploys_drives_tears_down(self, tmp_path):
        run = expand(tiny_spec())[0]
        plan = cluster_backend.command_plan(run, tmp_path)
        joined = [" ".join(c[:2]) for c in plan]
        assert joined[0] == "kind delete"  # idempotent pre-clean
        assert "kind create" in joined and "helm install" in joined
        # The locally built image must be side-loaded after the cluster
        # exists and before helm references it, or pods ImagePullBackOff.
        assert joined.index("kind load") == joined.index("kind create") + 1
        assert joined.index("kind load") < joined.index("helm install")
        assert "k6 run" in joined
        assert joined[-1] == "kind delete"  # deterministic teardown

    def test_capacity_parity_is_a_cell_property_not_a_system_property(self, tmp_path):
        """Every arm starts from the sim's initial world and faces the sim's
        cluster-size ceiling — otherwise the live ordinal comparison would
        hand one arm more capacity than the other."""
        run = expand(tiny_spec())[0]  # hpa, uniform (8 tenants), small
        plan = cluster_backend.command_plan(run, tmp_path)
        install = next(c for c in plan if c[:2] == ["helm", "install"])
        assert "--set=replicaCount=16" in install  # 8 tenants x sim initial 2
        assert "--set=autoscaling.hpa.maxReplicas=24" in install  # small cap

    def test_jcac_command_plan_arms_and_gates_the_operator(self, tmp_path):
        run = expand(tiny_spec(systems=["jcac"]))[0]
        plan = cluster_backend.command_plan(run, tmp_path)
        flat = [" ".join(c) for c in plan]

        # Both extra images are side-loaded before any helm install.
        loads = [i for i, c in enumerate(plan) if c[:2] == ["kind", "load"]]
        first_install = next(i for i, c in enumerate(plan) if c[:2] == ["helm", "install"])
        assert len(loads) == 3 and max(loads) < first_install

        installs = [c for c in plan if c[:2] == ["helm", "install"]]
        assert len(installs) == 2, "control plane chart, then the operator chart"
        operator = installs[1]
        assert "polyforge-operator" in operator[2]
        assert "--set=features.url=http://polyforge-control-plane.polyforge.svc:80" in operator
        assert "--set=features.adminKeySecret.name=polyforge-admin" in operator
        # Planner ceilings mirror the sim's small cluster (workloads.CLUSTER_SIZES).
        assert "--set=planner.limits.replicas=24" in operator
        assert "--set=planner.limits.cacheMB=2048" in operator

        # The operator authenticates with the same admin key the chart mounts.
        secret = next(c for c in plan if "secret" in c)
        assert f"--from-literal=admin-key={cluster_backend.ADMIN_KEY}" in secret

        # Order: operator install -> CRs -> executable actuation gate -> k6.
        idx = {name: next(i for i, s in enumerate(flat) if name in s)
               for name in ("polyforge-operator", "operator-crs.yaml",
                            "--for=condition=Applied", "k6 run")}
        assert (idx["polyforge-operator"] < idx["operator-crs.yaml"]
                < idx["--for=condition=Applied"] < idx["k6 run"])

        # hpa runs must not pay for (or depend on) any of this.
        hpa_plan = cluster_backend.command_plan(expand(tiny_spec())[0], tmp_path)
        assert not any("polyforge-operator" in " ".join(c) for c in hpa_plan)

    def test_operator_crs_mirror_the_sim_world(self):
        run = expand(tiny_spec(systems=["jcac"]))[0]
        docs = cluster_backend.operator_crs(run)
        # Policies and Budgets must precede Tenants: ensureDefaults leaves
        # existing objects alone, so applying the eval spec first wins the
        # race against the default per-tenant-gateway Policy.
        assert docs.index("kind: Policy") < docs.index("kind: Tenant")
        assert docs.index("kind: Budget") < docs.index("kind: Tenant")
        assert docs.count("kind: Policy") == 8
        assert docs.count("kind: Tenant") == 8
        assert docs.count("targetDeployment: polyforge/polyforge-control-plane") == 8
        # Sim parity: initial state 2 replicas / 128 MB / small tier; the
        # per-tenant ceiling is the small cluster's replica_max; budgets
        # come from the run's own tenant configs ($5/h uniform standard).
        assert docs.count("replicas: 2") == 8
        assert docs.count("replicaMax: 6") == 8
        assert docs.count("cacheSizeMB: 128") == 8
        assert docs.count("modelTier: small") == 8
        assert docs.count("hourlyCapMilliUSD: 5000") == 8
        assert docs.count("sloClass: standard") == 8


class TestV3OverloadCells:
    """PREREG_V3 §3 structural guarantees: the overload classes are defined
    by arithmetic on the committed model constants, and these tests pin
    that arithmetic so a drive-by constant change cannot silently unmake
    the regime the v3 matrix claims to measure."""

    @staticmethod
    def _wu(cls, factor: float) -> float:
        import model  # research/jcac_sim via harness sys.path

        return sum(rate * factor * model.WORK_UNITS[k] for k, rate in cls.base_rps.items())

    def test_flash_burst_outruns_the_actuation_clamp(self):
        import math

        import model

        cls = workloads.WORKLOAD_CLASSES["flash_crud"]
        need = lambda f: math.ceil(self._wu(cls, f) / (model.REPLICA_CAPACITY_WU * 0.85))
        # Burst onset requires a climb beyond two intervals of the shared
        # +-2 clamp: any purely reactive policy spends >= 2 scored
        # intervals under-provisioned, every cycle.
        assert need(6.0) - need(0.5) > 4

    def test_ramp_gentle_never_binds_the_clamp(self):
        import model

        cls = workloads.WORKLOAD_CLASSES["ramp_gentle"]
        factors = [workloads._shape_factor("slowwave", s, 0.0) for s in range(121)]
        max_delta_wu = max(
            abs(self._wu(cls, factors[i + 1]) - self._wu(cls, factors[i]))
            for i in range(120)
        )
        # Steepest per-step demand change stays well inside one interval of
        # actuation, so reactive controllers can track it: the control cell.
        assert max_delta_wu < 2 * model.REPLICA_CAPACITY_WU * 0.85

    def test_seasonal_locks_on_flash_cells_under_poisson_jitter(self):
        import math

        import simulate
        from controller import Forecast

        _, buckets, _, _ = workloads.build("flash_crud", "uniform", "medium", seed=7, steps=120)
        series = [b["t00"] for b in simulate.jitter_buckets(buckets, seed=99)]
        rmse = {}
        for method in ("seasonal", "trend"):
            f, sq, n = Forecast(method=method), 0.0, 0
            for t in range(len(series) - 1):
                f.observe(series[t])
                if t >= 48:  # past the detector's 2-period lock threshold
                    pred = f.horizon()[0].rps.get("crud_read", 0.0)
                    actual = series[t + 1].rps.get("crud_read", 0.0)
                    sq += (pred - actual) ** 2
                    n += 1
            rmse[method] = math.sqrt(sq / n)
        # On a locked square wave the period-aware forecast must beat the
        # trend fallback decisively, jitter included.
        assert rmse["seasonal"] < 0.5 * rmse["trend"]
