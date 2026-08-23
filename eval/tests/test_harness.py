"""Harness unit tests (W33/W35a). Run from eval/: python -m pytest tests -q

Covers the properties the smoke week exists to protect: run identity is
deterministic and collision-free, resume skips only valid runs, invalid
results are recorded not dropped, replaying a run_id reproduces its
metrics exactly, and the cluster backend refuses politely without Docker.
"""

from __future__ import annotations

import sys
import time
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
                    # Tenant count is the mix's slot count (8 for the matrix
                    # mixes; 32/64 for the PREREG_TENANT_SCALE slices).
                    assert len(ids) == len(workloads.TENANT_MIXES[mix])
                    assert len(buckets) == 6
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
        # Exact, not a subset: an arm named `jcac_no*` claims to be "PolyForge
        # minus one contribution", and a stray one would be read as an
        # ablation result. Every addition here is deliberate and dated.
        assert {s for s in SYSTEMS if s.startswith("jcac_no")} == {
            # W36 ablation set.
            "jcac_noclassifier", "jcac_nojoint", "jcac_noeviction", "jcac_nofairness",
            # Session 38, PREREG_BUDGET_PARITY: the proposal with its budget
            # filter lifted, to measure how much of the WP1 severity gap the
            # constraint accounts for.
            "jcac_nobudget",
            # Session 38, PREREG_LAYERED_FIX: the −joint-control ablation
            # against a tier rule that is not absorbing.
            "jcac_nojoint_v2",
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

    def test_check_metrics_rejects_a_run_that_measured_nothing(self):
        # The exact signature of the committed phase7 rows that were marked
        # `valid`: plausible cost from the replica sampler, latencies of 0.0
        # because telemetry never reached the exporter. This is also the
        # signature of the shared-PG bug, which the old contract could not
        # see because it only tested finiteness.
        silent = {
            "total_cost_usd": 0.258133, "mean_violation": 0.0,
            "violation_step_share": 0.0, "mean_jain": 1.0,
            "cache_hit_rate": 0.0, "crud_p95_ms": 0.0, "ai_p95_ms": 0.0,
            "steps": 30,
        }
        assert results.check_metrics(silent, 30), \
            "a run with a zero p95 must be rejected, not recorded as valid"

        healthy = {**silent, "crud_p95_ms": 3.1}
        assert results.check_metrics(healthy, 30) is None

        # On an AI-bearing workload a zero AI p95 is equally a non-measurement.
        assert results.check_metrics(healthy, 30, expects_ai=True)
        assert results.check_metrics({**healthy, "ai_p95_ms": 20.0}, 30,
                                     expects_ai=True) is None

        # n_events is the direct witness when the exporter supplies it.
        assert results.check_metrics({**healthy, "n_events": 0}, 30)
        assert results.check_metrics({**healthy, "n_events": 8412}, 30) is None

    def test_workload_has_ai_is_derived_from_the_taxonomy(self):
        assert workloads.workload_has_ai("ai_cacheable")
        assert workloads.workload_has_ai("agentic")
        assert workloads.workload_has_ai("joint_stress")
        assert not workloads.workload_has_ai("crud_bursty")
        assert not workloads.workload_has_ai("crud_steady")
        assert not workloads.workload_has_ai("nonexistent")

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

    def test_wave4_ablation_arms_pin_the_right_knobs(self):
        # cache-only: replicas + tier pinned (min==max), cache free.
        cache_only = cluster_backend.operator_crs(
            expand(tiny_spec(systems=["cache-only"]))[0])
        assert cache_only.count("replicaMin: 2") == 8
        assert cache_only.count("replicaMax: 2") == 8      # replicas pinned
        assert cache_only.count("modelTierMin: small") == 8
        assert cache_only.count("modelTierMax: small") == 8  # tier pinned
        assert cache_only.count("cacheSizeMBMax: 0") == 8    # cache free (no ceiling)

        # tier-only: replicas + cache pinned, tier free.
        tier_only = cluster_backend.operator_crs(
            expand(tiny_spec(systems=["tier-only"]))[0])
        assert tier_only.count("replicaMin: 2") == 8
        assert tier_only.count("replicaMax: 2") == 8
        assert tier_only.count("cacheSizeMBMin: 128") == 8
        assert tier_only.count("cacheSizeMBMax: 128") == 8   # cache pinned
        assert tier_only.count("modelTierMin: none") == 8
        assert tier_only.count("modelTierMax: large") == 8   # tier free

        # jcac still spans the full envelope (replicaMax is the cluster ceiling).
        jcac = cluster_backend.operator_crs(expand(tiny_spec(systems=["jcac"]))[0])
        assert jcac.count("replicaMax: 6") == 8
        assert jcac.count("modelTierMax: large") == 8

    def test_cluster_backend_refuses_parallel_workers(self):
        from harness.runner import run_experiment

        spec = tiny_spec(backend="cluster")
        with pytest.raises(ValueError, match="workers"):
            run_experiment(spec, workers=4)

    def test_resume_is_scoped_to_the_substrate(self, tmp_path):
        # A cluster spec pointed at a DB holding the same experiment's sim
        # rows must not treat them as done.
        db = tmp_path / "mixed.duckdb"
        con = results.connect(db)
        run = expand(tiny_spec(systems=["hpa"]))[0]
        outcome = sim_backend.execute(run)
        results.record(con, run, "valid", 1, outcome, None)

        assert run.run_id in results.valid_run_ids(con, run.experiment, "sim")
        assert run.run_id not in results.valid_run_ids(con, run.experiment, "cluster"), \
            "a sim row must not satisfy a cluster run"
        con.close()

    def test_k6_script_declares_delivery_thresholds(self):
        run = expand(tiny_spec(systems=["hpa"]))[0]
        script = cluster_backend.k6_script(run)
        assert "thresholds" in script
        assert "http_req_failed" in script
        assert "dropped_iterations" in script

    def test_k6_delivery_check_rejects_a_run_that_did_not_land(self, tmp_path):
        import json as _json

        good = tmp_path / "ok.json"
        good.write_text(_json.dumps({"metrics": {
            "http_req_failed": {"rate": 0.0},
            "dropped_iterations": {"count": 0}}}), encoding="utf-8")
        cluster_backend.check_k6_delivery(good)  # must not raise

        # Every request failed but k6 still exits 0 — the exact signature the
        # harness used to record as a valid, cheap, low-violation run.
        bad = tmp_path / "failed.json"
        bad.write_text(_json.dumps({"metrics": {
            "http_req_failed": {"rate": 1.0},
            "dropped_iterations": {"count": 0}}}), encoding="utf-8")
        with pytest.raises(RuntimeError, match="failed requests"):
            cluster_backend.check_k6_delivery(bad)

        # The generator could not keep up: tenants did not get the demand.
        dropped = tmp_path / "dropped.json"
        dropped.write_text(_json.dumps({"metrics": {
            "http_req_failed": {"rate": 0.0},
            "dropped_iterations": {"count": 512}}}), encoding="utf-8")
        with pytest.raises(RuntimeError, match="dropped"):
            cluster_backend.check_k6_delivery(dropped)

        with pytest.raises(RuntimeError, match="missing"):
            cluster_backend.check_k6_delivery(tmp_path / "absent.json")

    def test_sampler_coverage_check_catches_a_dead_sampler(self):
        s = cluster_backend.ReplicaSampler(interval_s=10.0)
        s.samples = [2] * 30           # 300 s of a 300 s window
        cluster_backend.check_sampler_coverage(s, 300.0)

        s.samples = [2] * 4            # sampler died ~40 s in
        s.failures = 3
        with pytest.raises(RuntimeError, match="covered"):
            cluster_backend.check_sampler_coverage(s, 300.0)

    def test_knob_preflight_is_wired_into_the_run_path(self):
        import inspect

        # The audited defect: knob_preflight.py existed, was documented as the
        # WL-H2 gate, and was called from no code path at all — so a live
        # campaign could complete with both knobs inert and every row valid.
        src = inspect.getsource(cluster_backend)
        assert "run_knob_preflight(" in src
        assert src.count("run_knob_preflight(") >= 2, \
            "preflight must be defined AND invoked, not just defined"
        assert hasattr(cluster_backend, "run_knob_preflight")

        # It must fail loudly rather than skip when tiers are unconfigured.
        import pathlib
        with pytest.raises(RuntimeError, match="two configured tiers"):
            cluster_backend.run_knob_preflight(
                "http://127.0.0.1:1", "t00", "key", pathlib.Path("."))

    def test_wave4_arms_registered_and_operator_wiring(self):
        for arm in ("replica-only", "cache-only", "tier-only"):
            assert arm in SYSTEMS
        # The two-knob ablations run the operator; replica-only is reactive HPA.
        assert {"cache-only", "tier-only"} <= cluster_backend.OPERATOR_SYSTEMS
        assert "replica-only" not in cluster_backend.OPERATOR_SYSTEMS

    def test_wave4_crs_are_admissible_against_the_real_crds(self):
        """Every field the harness emits must be a property the CRD declares,
        inside its declared bounds. This is the guard the ablation arms most
        need: the API server *prunes* fields a structural schema does not
        declare rather than rejecting them, so a bound field that is mistyped
        or missing from the CRD would apply cleanly, silently leave the knob
        free, and turn a `cache-only` run into a second `jcac` run — voiding
        WL-H1 with nothing to flag it. Checked against the committed CRDs, so
        it also fails if the CRDs stop being regenerated from the Go types."""
        import yaml

        crd_dir = EVAL_DIR.parent / "deploy" / "operator" / "crds"
        schemas = {}
        for path in sorted(crd_dir.glob("polyforge.io_*.yaml")):
            crd = yaml.safe_load(path.read_text(encoding="utf-8"))
            schema = crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]
            schemas[crd["spec"]["names"]["kind"]] = schema["properties"]["spec"]["properties"]

        checked = 0
        for arm in sorted(cluster_backend.OPERATOR_SYSTEMS):
            run = expand(tiny_spec(systems=[arm]))[0]
            for doc in yaml.safe_load_all(cluster_backend.operator_crs(run)):
                props = schemas[doc["kind"]]
                for key, value in doc.get("spec", {}).items():
                    assert key in props, (
                        f"{arm}: {doc['kind']}.spec.{key} is not declared by the "
                        f"CRD — the API server would prune it and the knob would "
                        f"silently stay free")
                    field = props[key]
                    if "enum" in field:
                        assert value in field["enum"], \
                            f"{arm}: {key}={value!r} not in {field['enum']}"
                    if isinstance(value, int):
                        assert value >= field.get("minimum", value), \
                            f"{arm}: {key}={value} below minimum {field['minimum']}"
                        assert value <= field.get("maximum", value), \
                            f"{arm}: {key}={value} above maximum {field['maximum']}"
                    checked += 1
        # Guard the guard: the loop must actually have inspected the pins.
        assert checked >= len(cluster_backend.OPERATOR_SYSTEMS) * 8

    def test_eviction_band_matches_the_committed_csv(self):
        from harness.systems import eviction_sensitivity_band, lru_miss_cost_factor

        band = eviction_sensitivity_band()
        # The published factor is the LRU comparator, and it must stay exactly
        # what the committed campaigns were scored with (R4).
        assert band["lru"] == pytest.approx(lru_miss_cost_factor())
        assert band["lru"] == pytest.approx(1.4581, abs=1e-3)
        assert band["none"] == 1.0
        # The audited point: GDSF beats the proposed cost-aware policy, so the
        # factor would run AGAINST the proposal under that comparator.
        assert band["gdsf"] < 1.0, "gdsf should disadvantage the proposal"
        assert band["arc"] < band["lru"], "lru is the most favorable comparator"

    def test_fair_arms_drop_the_charge_and_presize_the_cache(self):
        for arm in ("hpa_fair", "keda_fair"):
            spec = SYSTEMS[arm]
            assert spec.lru_eviction is False, f"{arm} must not pay the LRU charge"
            assert spec.static_cache_mb == 512
        # The published comparators are untouched — the record still stands.
        assert SYSTEMS["hpa"].lru_eviction is True
        assert SYSTEMS["hpa"].static_cache_mb is None
        # jcac pays its own eviction overhead only in the charged arm.
        assert SYSTEMS["jcac_evictcharged"].params["evict_overhead_us"] == 1104.9
        assert "evict_overhead_us" not in SYSTEMS["jcac"].params

    def test_static_cache_pins_a_reactive_baseline(self):
        # hpa carries state.cache_mb forward, so a pre-sized initial cache is
        # held for the whole run — that is what makes hpa_fair a competent
        # comparator rather than one bolted to 128 MB.
        run = expand(tiny_spec(systems=["hpa_fair"], workloads=["ai_cacheable"]))[0]
        out = sim_backend.execute(run)
        assert out["metrics"]["cache_hit_rate"] > 0.0
        plain = sim_backend.execute(
            expand(tiny_spec(systems=["hpa"], workloads=["ai_cacheable"]))[0])
        assert out["metrics"]["cache_hit_rate"] > plain["metrics"]["cache_hit_rate"], \
            "512 MB must beat the 128 MB default on hit rate"

    def test_severity_metrics_are_reported_for_every_arm(self):
        run = expand(tiny_spec(systems=["jcac"], workloads=["ai_cacheable"]))[0]
        m = sim_backend.execute(run)["metrics"]
        assert "mean_excess" in m and "tier_none_step_share" in m
        assert m["mean_excess"] >= m["mean_violation"] - 1e-9
        assert 0.0 <= m["tier_none_step_share"] <= 1.0

    def test_sim_freeze_knobs_pins_only_named_knobs(self):
        from model import TenantConfig

        configs = {"a": TenantConfig(tenant_id="a")}
        frozen = sim_backend._freeze_knobs(configs, frozenset({"replicas", "tier"}))
        c = frozen["a"]
        assert c.replica_min == c.replica_max == 2   # replicas pinned at init
        assert c.tier_min == c.tier_max == "small"   # tier pinned at init
        assert c.cache_max is None                   # cache still free


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


class TestEconomyOverride:
    """Wave 2 economy overrides (PREREG_TIER_RATIO / PREREG_HK_ADOPTION):
    the override must reach world and planner alike, reset statelessly, and
    never move a pre-existing run identity."""

    # Ground truth from the committed v1 headline campaign (raw_sim.duckdb,
    # produced before economy overrides existed). If this breaks, resume and
    # spot-check replay of every closed campaign break with it.
    KNOWN_FULL_RUN = ("bc7525d3a011b1e3", 1891929049)

    def test_headline_run_identity_is_unchanged(self):
        spec = ExperimentSpec(
            name="full", backend="sim", steps=120, reps=5,
            systems=["jcac", "hpa", "keda", "firm", "static", "gptcache"],
            workloads=["crud_bursty", "crud_steady", "ai_cacheable",
                       "ai_uncacheable", "agentic"],
            tenant_mixes=["uniform", "premium_heavy", "besteffort_heavy", "whale"],
            cluster_sizes=["small", "medium", "large"],
        )
        rid, seed = run_identity(spec, "jcac", "crud_bursty", "uniform", "small", 0)
        assert (rid, seed) == self.KNOWN_FULL_RUN

    def test_economy_changes_identity_only_when_set(self):
        base, _ = run_identity(tiny_spec(), "hpa", "crud_steady", "uniform", "small", 0)
        assert base == run_identity(tiny_spec(economy={}), "hpa", "crud_steady",
                                    "uniform", "small", 0)[0]
        econ = tiny_spec(economy={"tier_cost_mid": 1.52e-4})
        assert base != run_identity(econ, "hpa", "crud_steady", "uniform", "small", 0)[0]

    def test_economy_validation(self, tmp_path):
        bad = tmp_path / "e.yaml"
        bad.write_text("name: x\neconomy: {bogus_knob: 1}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="unknown economy keys"):
            load(bad)
        bad.write_text("name: x\nbackend: cluster\neconomy: {cache_hit_max: 0.3}\n",
                       encoding="utf-8")
        with pytest.raises(ValueError, match="sim-only"):
            load(bad)
        bad.write_text("name: x\neconomy: {cache_hit_max: -0.3}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="non-negative"):
            load(bad)

    def test_set_economy_moves_model_and_resets_exactly(self):
        import model
        from model import Demand, TenantConfig, TenantState, evaluate_step

        cfg = TenantConfig(tenant_id="t")
        state = TenantState(replicas=2, cache_mb=128, tier="mid")
        demand = Demand(rps={"chat": 4.0, "crud_read": 5.0})
        before = evaluate_step(cfg, state, demand)
        default_hit = model.hit_rate(128)

        model.set_economy(tier_cost_usd_per_req={"mid": 1.52e-4},
                          cache_hit_max=0.285, cache_half_mb=19.0)
        overridden = evaluate_step(cfg, state, demand)
        assert model.hit_rate(128) == pytest.approx(0.285 * 128 / (128 + 19.0))
        assert overridden.cost_tier_usd < before.cost_tier_usd
        assert overridden.cache_hit_rate < before.cache_hit_rate

        model.set_economy()  # reset must restore the published economy exactly
        after = evaluate_step(cfg, state, demand)
        assert after == before
        assert model.hit_rate(128) == default_hit
        assert model.TIER_COST_USD_PER_REQ == {"none": 0.0, "small": 0.0001,
                                               "mid": 0.001, "large": 0.01}

    def test_execute_applies_and_clears_economy(self):
        spec = tiny_spec(systems=["hpa"], workloads=["ai_cacheable"],
                         economy={"cache_hit_max": 0.285, "cache_half_mb": 19.0})
        run_e = expand(spec)[0]
        run_d = expand(tiny_spec(systems=["hpa"], workloads=["ai_cacheable"]))[0]
        import model

        res_e = sim_backend.execute(run_e)
        hit_after_e = model.CACHE_HIT_MAX  # still overridden right after
        assert hit_after_e == 0.285
        res_d = sim_backend.execute(run_d)  # default run must reset it
        assert model.CACHE_HIT_MAX == 0.85
        assert res_e["metrics"]["cache_hit_rate"] < res_d["metrics"]["cache_hit_rate"]


class TestChaosArms:
    """Wave 2 chaos hooks (PREREG_CHAOS_SIM): engine-level, controller-blind,
    default-off."""

    def _run(self, system: str, steps: int = 30):
        spec = tiny_spec(systems=[system], workloads=["crud_bursty"], reps=1,
                         steps=steps)
        return sim_backend.execute(expand(spec)[0])

    def test_chaos_params_never_reach_controller(self):
        # Would raise TypeError in JCACController(**params) if they leaked.
        res = self._run("jcac_outage_1m", steps=50)
        assert res["metrics"]["steps"] == 50

    def test_default_chaos_is_bit_identical_noop(self):
        import simulate

        _, buckets, configs, limits = workloads.build(
            "crud_bursty", "uniform", "small", seed=11, steps=20)
        tids = sorted(configs)
        a = simulate.run("hpa", tids, buckets, configs=configs, limits=limits,
                         jitter_seed=3)
        b = simulate.run("hpa", tids, buckets, configs=configs, limits=limits,
                         jitter_seed=3, chaos_planner_outage=None,
                         chaos_replica_kill=None)
        assert a.summary() == b.summary()

    def test_outage_holds_last_known_good(self):
        import simulate

        _, buckets, configs, limits = workloads.build(
            "crud_bursty", "uniform", "small", seed=11, steps=20)
        tids = sorted(configs)
        res = simulate.run("jcac", tids, buckets, configs=configs, limits=limits,
                           jitter_seed=3, chaos_planner_outage=(0, 999))
        # Planner dead from step 0: nothing may ever move off the initial state.
        assert {r["replicas"] for r in res.rows} == {2}
        assert {r["cache_mb"] for r in res.rows} == {128}

    def test_replica_kill_degrades_serving_not_billing(self):
        import simulate

        _, buckets, configs, limits = workloads.build(
            "crud_bursty", "uniform", "small", seed=11, steps=20)
        tids = sorted(configs)
        base = simulate.run("static", tids, buckets, configs=configs,
                            limits=limits, jitter_seed=3)
        kill = simulate.run("static", tids, buckets, configs=configs,
                            limits=limits, jitter_seed=3,
                            chaos_replica_kill=(5, 0.9, 3))
        by_step = lambda rows: {(r["step"], r["tenant"]): r for r in rows}
        b, k = by_step(base.rows), by_step(kill.rows)
        assert b.keys() == k.keys()
        for key, row in k.items():
            # Billing follows the nominal configuration in the kill window
            # and everything is untouched outside it.
            assert row["cost_usd"] == pytest.approx(b[key]["cost_usd"], abs=1e-9)
            if not 5 <= key[0] < 8:
                assert row["violation"] == b[key]["violation"]
        assert kill.mean_violation > base.mean_violation


class TestModelFormOverride:
    """Wave 5 structural model-form overrides (PREREG_LM_ADOPTION /
    PREREG_MIXTURE_P95 / PREREG_TIER_WU): same contract as economy —
    reaches world and planner alike, resets statelessly, never moves a
    pre-existing run identity."""

    def test_model_form_changes_identity_only_when_set(self):
        base, _ = run_identity(tiny_spec(), "hpa", "crud_steady", "uniform", "small", 0)
        assert base == run_identity(tiny_spec(model_form={}), "hpa", "crud_steady",
                                    "uniform", "small", 0)[0]
        formed = tiny_spec(model_form={"congestion_exponent": 0.86})
        assert base != run_identity(formed, "hpa", "crud_steady", "uniform", "small", 0)[0]
        # And the economy-tagged identity is orthogonal to the form tag.
        econ = tiny_spec(economy={"cache_hit_max": 0.285})
        both = tiny_spec(economy={"cache_hit_max": 0.285},
                         model_form={"congestion_exponent": 0.86})
        assert (run_identity(econ, "hpa", "crud_steady", "uniform", "small", 0)[0]
                != run_identity(both, "hpa", "crud_steady", "uniform", "small", 0)[0])

    def test_model_form_validation(self, tmp_path):
        bad = tmp_path / "f.yaml"
        bad.write_text("name: x\nmodel_form: {bogus_form: 1}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="unknown model_form keys"):
            load(bad)
        bad.write_text("name: x\nbackend: cluster\nmodel_form: {mixture_p95: 1}\n",
                       encoding="utf-8")
        with pytest.raises(ValueError, match="sim-only"):
            load(bad)
        bad.write_text("name: x\nmodel_form: {p95_tail_f0: 1.69}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="set together"):
            load(bad)

    def test_execute_applies_and_clears_model_form(self):
        import model

        spec = tiny_spec(systems=["hpa"], workloads=["crud_steady"],
                         model_form={"congestion_exponent": 0.86,
                                     "p95_tail_f0": 1.6909, "p95_tail_b": 0.1303})
        run_f = expand(spec)[0]
        run_d = expand(tiny_spec(systems=["hpa"], workloads=["crud_steady"]))[0]

        sim_backend.execute(run_f)
        assert model.CONGESTION_EXPONENT == 0.86  # still set right after
        assert model.P95_TAIL == (1.6909, 0.1303)
        sim_backend.execute(run_d)  # default run must reset the form
        assert model.CONGESTION_EXPONENT == 1.0
        assert model.P95_TAIL is None
        assert model.MIXTURE_P95 is False

    def test_form_moves_outcomes_in_the_measured_direction(self):
        """The measured tail factor is >= 1.59 everywhere the flat form
        said 1.4: under identical demand a lean state must report worse
        (or equal) violation, never better."""
        import model
        from model import Demand, TenantConfig, TenantState, evaluate_step

        cfg = TenantConfig(tenant_id="t", slo_class="premium")
        state = TenantState(replicas=1, cache_mb=0, tier="small")
        demand = Demand(rps={"crud_read": 60.0, "chat": 1.0})
        flat = evaluate_step(cfg, state, demand)
        model.set_model_form(congestion_exponent=0.86, p95_tail=(1.6909, 0.1303))
        measured = evaluate_step(cfg, state, demand)
        model.set_model_form()
        assert measured.crud_p95_ms > 0 and flat.crud_p95_ms > 0
        # tail rises (>=1.59 vs 1.4) while congestion softens (a 0.86):
        # the two measured corrections push in opposite directions and both
        # must be live for this ratio to differ from 1.
        assert measured.crud_p95_ms != flat.crud_p95_ms


class TestWave4ArmPins:
    """WP8a: every Wave 4 arm's CRs must encode the pin its name claims.

    `PREREG_WAVE4_LIVE_PLANE` §Arms isolates *jointness* by pinning two knobs
    per ablation. An arm whose pin does not reach its Policy CR is an
    unlabelled copy of the full controller, and its result would mean
    nothing. Session 38 found `replica-only` rendering CRs byte-identical to
    `jcac`'s; these keep that from recurring silently.
    """

    EXPECTED_PINS = {
        "jcac": set(),
        "replica-only": {"cache", "tier"},
        "cache-only": {"replicas", "tier"},
        "tier-only": {"replicas", "cache"},
    }

    def _pins(self, system):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research" / "jcac_sim"))
        from model import TenantState

        from harness import workloads
        from harness.cluster_backend import _arm_knob_bounds

        size = workloads.CLUSTER_SIZES["small"]
        (rmin, rmax), (cmin, cmax), (tmin, tmax) = _arm_knob_bounds(
            system, TenantState(), size)
        pins = set()
        if rmin == rmax:
            pins.add("replicas")
        # cacheSizeMBMax 0 is the CRD's "no ceiling" sentinel, not a pin.
        if cmin == cmax and not (cmin == 0 and cmax == 0):
            pins.add("cache")
        if tmin == tmax:
            pins.add("tier")
        return pins

    def test_every_arm_pins_exactly_what_its_name_claims(self):
        for system, expected in self.EXPECTED_PINS.items():
            assert self._pins(system) == expected, (
                f"{system}: CRs pin {sorted(self._pins(system))}, "
                f"name claims {sorted(expected)}")

    def test_no_ablation_arm_renders_the_same_crs_as_jcac(self):
        """The sharpest form of the same check: identical manifests mean an
        identical deployment, whatever the arm is called."""
        import hashlib
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from harness.cluster_backend import operator_crs
        from harness.config import RunSpec, load, run_identity

        spec = load(str(Path(__file__).resolve().parents[1]
                        / "experiments" / "wave4_live_plane.yaml"))

        def digest(system):
            rid, seed = run_identity(spec, system, "ai_cacheable", "uniform",
                                     "small", 0)
            run = RunSpec(run_id=rid, experiment=spec.name, system=system,
                          workload="ai_cacheable", tenant_mix="uniform",
                          cluster_size="small", rep=0, seed=seed,
                          steps=spec.steps, backend=spec.backend,
                          store_timeseries=False)
            return hashlib.sha256(operator_crs(run).encode()).hexdigest()

        jcac = digest("jcac")
        for system in ("replica-only", "cache-only", "tier-only"):
            assert digest(system) != jcac, (
                f"{system} renders CRs identical to jcac -- it is an "
                "unlabelled copy of the full controller")


class TestPortForwardSupervisor:
    """WP14: the port-forward must survive the pod it was pinned to.

    Attempt 2 lost a 24 h soak to an unsupervised forward — `check_k6_delivery`
    rejected the run at 36.4% failed requests. `kubectl port-forward
    service/X` resolves to ONE pod and does not follow the Service, so a
    single pod restart black-holes every later request while k6 keeps posting
    and exits 0.

    These exercise the supervisor's decision logic with the subprocess and
    health probe stubbed, because the real failure takes a day to reproduce
    and must not need a cluster to test.
    """

    def _supervisor(self, tmp_path, healthy_seq, alive_seq):
        from harness.cluster_backend import PortForwardSupervisor

        sup = PortForwardSupervisor("polyforge", "svc", 18080,
                                    evidence_log=tmp_path / "pf.log",
                                    interval_s=0.01)
        health = iter(healthy_seq)
        alive = iter(alive_seq)

        class FakeProc:
            def __init__(self):
                self.pid = 999
                self.terminated = False

            def poll(self):
                return None if next(alive, True) else 1

            def terminate(self):
                self.terminated = True

        sup._healthy = lambda: next(health, True)
        sup._spawn = lambda: setattr(sup, "proc", FakeProc())
        sup._resolve_endpoints = lambda: "10.0.0.1"
        sup._spawn()
        return sup

    def test_healthy_forward_is_never_restarted(self, tmp_path):
        sup = self._supervisor(tmp_path, [True] * 20, [True] * 20)
        sup.start()
        time.sleep(0.15)
        sup.stop()
        sup.join(timeout=5)
        assert sup.restarts == 0, "a healthy forward must be left alone"

    def test_dead_process_triggers_a_restart(self, tmp_path):
        # poll() returns non-None (exited) -> restart without needing a
        # second health probe, because a dead process cannot recover.
        sup = self._supervisor(tmp_path, [False] * 20, [False] * 20)
        sup.start()
        time.sleep(0.15)
        sup.stop()
        sup.join(timeout=5)
        assert sup.restarts >= 1, "an exited port-forward must be respawned"

    def test_transient_probe_blip_does_not_restart(self, tmp_path):
        """The soak deliberately kills the planner pod four times. A single
        failed probe during that must not trigger a restart stampede — the
        supervisor confirms twice before acting."""
        # process stays alive; health alternates fail/pass, so the confirm
        # probe always succeeds.
        sup = self._supervisor(tmp_path, [False, True] * 20, [True] * 40)
        sup.start()
        time.sleep(0.15)
        sup.stop()
        sup.join(timeout=5)
        assert sup.restarts == 0, "a confirmed-healthy blip must not restart"

    def test_restarts_are_logged_to_the_evidence_file(self, tmp_path):
        sup = self._supervisor(tmp_path, [False] * 20, [False] * 20)
        sup.start()
        time.sleep(0.15)
        sup.stop()
        sup.join(timeout=5)
        log = (tmp_path / "pf.log").read_text(encoding="utf-8")
        assert "RESTART" in log, "a restart must leave a post-mortem trail"
        assert sup.summary()["restarts"] >= 1


class TestEvidenceDirIsNotTemp:
    r"""WP14, twice-learned: evidence for a scored record must not live where
    the OS may delete it. The harness lost `k6-summary.json` to a
    self-deleting TemporaryDirectory, and the session fault journal was first
    written to %LOCALAPPDATA%\Temp."""

    def test_evidence_dir_is_inside_the_repo_results_tree(self):
        from harness.cluster_backend import evidence_dir_for
        from harness.config import RunSpec

        run = RunSpec(run_id="abc", experiment="live_soak", system="jcac",
                      workload="crud_bursty", tenant_mix="uniform",
                      cluster_size="medium", rep=0, seed=1, steps=10,
                      backend="cluster", store_timeseries=False)
        path = evidence_dir_for(run)
        parts = [p.lower() for p in path.parts]
        assert "results" in parts and "eval" in parts
        assert "temp" not in parts and "tmp" not in parts


class TestNodePortLoadPath:
    """WP14 attempt 5: the load path is a NodePort, not a port-forward.

    Attempts 2-4 tried progressively harder to keep `kubectl port-forward`
    alive and attempt 4 still lost its 24 h sitting to it, at 282 restarts and
    4.567% failed requests. The forward resolves to ONE pod and never follows
    the Service, so the whole workload landed on one replica of sixteen until
    it OOMed on its 256 Mi limit. A NodePort is balanced by kube-proxy across
    every ready endpoint, in the kernel, with no proxy process to die.
    """

    def test_kind_config_publishes_the_nodeport_on_the_host(self):
        from harness.cluster_backend import NODE_PORT, kind_config

        cfg = kind_config("medium")
        assert "extraPortMappings:" in cfg
        assert f"containerPort: {NODE_PORT}" in cfg
        assert f"hostPort: {NODE_PORT}" in cfg
        # kind cannot add a port mapping to a running cluster, so the mapping
        # has to sit under the control-plane node at create time. If it drifts
        # onto a worker the host port silently stops reaching kube-proxy.
        head = cfg.split("- role: worker")[0]
        assert "extraPortMappings:" in head

    def test_nodeport_service_selects_the_control_plane(self):
        import yaml  # noqa: F401  (pyyaml ships with the harness deps)
        from harness.cluster_backend import NODE_PORT, nodeport_service

        doc = yaml.safe_load(nodeport_service())
        assert doc["spec"]["type"] == "NodePort"
        assert doc["spec"]["ports"][0]["nodePort"] == NODE_PORT
        # Must match polyforge.selectorLabels in the chart's _helpers.tpl, or
        # the Service selects nothing and every request 503s.
        assert doc["spec"]["selector"] == {
            "app.kubernetes.io/name": "polyforge-control-plane",
            "app.kubernetes.io/instance": "polyforge",
        }
        # Evaluation scaffolding: it must never look like part of the product.
        assert doc["metadata"]["name"] != "polyforge-control-plane"


class TestLoadDistributionGate:
    """The check whose absence cost four 24 h sittings.

    Nothing in attempts 1-4 ever verified that requests reached more than one
    pod. Every health signal was satisfied by the single pinned replica
    working: pods Running, sixteen Service endpoints, /healthz answering 200.
    """

    def _sampler(self, cpu_ms, samples=20, failures=0):
        from harness.cluster_backend import LoadDistributionSampler

        s = LoadDistributionSampler()
        s.cpu_ms = dict(cpu_ms)
        s.samples = samples
        s.failures = failures
        return s

    def test_even_spread_passes(self):
        from harness.cluster_backend import check_load_distribution

        check_load_distribution(self._sampler({f"pod-{i}": 100.0 for i in range(16)}))

    def test_attempt4_signature_fails(self):
        """The real numbers: one pod at 1571m, fifteen siblings at 8-16m."""
        from harness.cluster_backend import check_load_distribution

        cpu = {"pod-hot": 1571.0 * 30}
        cpu.update({f"pod-{i}": 12.0 * 30 for i in range(15)})
        with pytest.raises(RuntimeError, match="load was pinned"):
            check_load_distribution(self._sampler(cpu))

    def test_idle_replicas_fail_even_when_no_single_pod_dominates(self):
        """Four pods sharing everything evenly still means twelve are dead
        weight — the fault tolerance of four replicas, not sixteen."""
        from harness.cluster_backend import check_load_distribution

        cpu = {f"pod-{i}": 100.0 for i in range(4)}
        cpu.update({f"pod-idle-{i}": 0.0 for i in range(12)})
        with pytest.raises(RuntimeError, match="served any traffic"):
            check_load_distribution(self._sampler(cpu), max_share=0.30)

    def test_a_blind_gate_fails_rather_than_passing_silently(self):
        """metrics-server down must not read as 'distribution fine'. A gate
        that cannot see is the failure mode this whole class exists for."""
        from harness.cluster_backend import check_load_distribution

        with pytest.raises(RuntimeError, match="unmeasured"):
            check_load_distribution(self._sampler({}, samples=0, failures=40))

    def test_shares_weight_by_time_not_final_reading(self):
        """A pod hot for half the window and idle after must not read as idle;
        cpu_ms accumulates CPU-milliseconds, so shares reflect the whole run."""
        from harness.cluster_backend import LoadDistributionSampler

        s = LoadDistributionSampler()
        s.cpu_ms = {"a": 300.0, "b": 100.0}
        assert s.shares() == {"a": 0.75, "b": 0.25}


class TestObservabilityAndTeardown:
    """WP14 Phase 2: the run must be readable while it runs, and its evidence
    must outlive it.

    Attempt 4 ran blind for 24 h (`capture_output=True` buffers until exit, so
    the runner log was 0 bytes), then deleted the cluster holding 21M telemetry
    rows seconds after reading them — losing hours 17-24 permanently when the
    end-of-run extraction lost that race.
    """

    def test_eval_export_is_asked_for_time_buckets(self, tmp_path):
        run = expand(tiny_spec())[0]
        plan = cluster_backend.command_plan(run, tmp_path)
        export = [c for c in plan if "eval-export" in c]
        # SK-H3 is frozen on hour-bucketed crud_p95. Without this flag the
        # exporter emits run-level scalars only and the hypothesis has no
        # instrument at all — which is what happened four times.
        assert export, "no export step at all"
        for cmd in export:
            assert any(a.startswith("--bucket-seconds=") for a in cmd)

    def test_eval_export_runs_at_two_granularities(self, tmp_path):
        """SK-H1 scores recovery within FIVE MINUTES; SK-H3 scores hour
        buckets. Percentiles do not aggregate, so an hourly p95 cannot be
        subdivided after the run — one export cannot serve both hypotheses.
        """
        run = expand(tiny_spec())[0]
        plan = cluster_backend.command_plan(run, tmp_path)
        widths = [
            int(a.split("=", 1)[1])
            for cmd in plan if "eval-export" in cmd
            for a in cmd if a.startswith("--bucket-seconds=")
        ]
        assert len(widths) == 2, f"expected coarse and fine exports, got {widths}"
        assert len(set(widths)) == 2, "the two exports must differ in width"
        assert widths[0] == cluster_backend.EVAL_BUCKET_SECONDS, (
            "the coarse export must run FIRST, so a failure in the fine pass "
            "cannot cost the run the export every existing consumer reads")

    def test_warmup_precedes_the_scored_load_and_cannot_abort_it(self, tmp_path):
        """Stage B attempt 3 died 60 s in, on ONE dropped iteration, because k6
        opened at full demand seconds after the deployment reached sixteen
        replicas with PostgreSQL cold. p90 was 3,063 ms against a steady-state
        23.89 ms. `dropped_iterations` is cumulative with abortOnFail, so a
        single cold-start drop poisons a 24 h sitting permanently.
        """
        import json

        run = expand(tiny_spec())[0]
        plan = cluster_backend.command_plan(run, tmp_path)
        k6_steps = [c for c in plan if c[0] == "k6"]
        assert len(k6_steps) == 2, f"expected warm-up + scored load, got {k6_steps}"
        assert k6_steps[0][-1].endswith("warmup.js"), "warm-up must run FIRST"
        assert k6_steps[1][-1].endswith("replay.js")

        # Only the scored step drives sampling, the soak marker and the gates.
        assert not cluster_backend._is_scored_load(k6_steps[0])
        assert cluster_backend._is_scored_load(k6_steps[1])

        # A warm-up carrying thresholds could abort the run it exists to
        # protect, which would be the same defect wearing a different hat.
        script = cluster_backend.k6_script(
            run, warmup_s=cluster_backend.WARMUP_SECONDS)
        options = json.loads(
            script.split("export const options = ", 1)[1].split(";\n", 1)[0])
        assert "thresholds" not in options
        executors = {sc["executor"] for sc in options["scenarios"].values()}
        assert executors == {"constant-arrival-rate"}, executors

    def test_teardown_runs_by_default_so_ci_never_leaks_clusters(self, tmp_path):
        run = expand(tiny_spec())[0]
        plan = cluster_backend.command_plan(run, tmp_path)
        deletes = [c for c in plan if c[:2] == ["kind", "delete"]]
        # One pre-clean at the head, one teardown at the tail.
        assert len(deletes) == 2
        assert plan[-1][:2] == ["kind", "delete"]

    def test_keep_cluster_preserves_the_evidence_store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cluster_backend, "EVAL_KEEP_CLUSTER", True)
        run = expand(tiny_spec())[0]
        plan = cluster_backend.command_plan(run, tmp_path)
        deletes = [c for c in plan if c[:2] == ["kind", "delete"]]
        # The idempotent pre-clean must survive — it is what makes a rerun
        # possible — but the trailing teardown must not.
        assert len(deletes) == 1
        assert plan[0][:2] == ["kind", "delete"]
        assert plan[-1][:2] != ["kind", "delete"]

    def test_k6_aborts_a_doomed_run_instead_of_finishing_it(self):
        run = expand(tiny_spec(systems=["hpa"]))[0]
        script = cluster_backend.k6_script(run)
        # Attempt 4 spent its last 6.5 h driving load in a state the delivery
        # gate would reject, because nothing told k6 to stop.
        assert "abortOnFail" in script
        # ...but not so eagerly that a cold-start blip kills a healthy run.
        assert "delayAbortEval" in script

    def test_run_step_streams_to_a_file_and_still_reports_errors(self, tmp_path):
        log = tmp_path / "k6-live.log"
        proc = cluster_backend._run_step(
            [sys.executable, "-c",
             "import sys; print('progress'); print('boom', file=sys.stderr); "
             "sys.exit(3)"],
            env=None, timeout_s=60, live_log=log)
        # Written as it happens, not buffered until exit.
        assert log.exists()
        body = log.read_text(encoding="utf-8")
        assert "progress" in body and "boom" in body
        # The caller's existing error path reads proc.stderr; that must keep
        # working now that the real stream went to a file.
        assert proc.returncode == 3
        assert "boom" in proc.stderr

    def test_run_step_without_a_log_still_captures_stdout_as_data(self, tmp_path):
        """eval-export's JSON arrives on stdout and is parsed. Streaming it to
        a file would silently empty proc.stdout and break the export."""
        proc = cluster_backend._run_step(
            [sys.executable, "-c", "print('{\"ok\": true}')"],
            env=None, timeout_s=60, live_log=None)
        assert proc.stdout.strip() == '{"ok": true}'


class TestAuditStreamInstrumented:
    """WP14 Phase 3: SK-H2 must never again score an absent instrument.

    For four attempts it computed 0 == 0 and returned true. PlanRunner.audit
    no-ops when Audit is nil, Audit is assigned only under POLYFORGE_NATS_URL,
    and that variable was set nowhere — chart, harness, or live deployment.
    """

    def test_nats_is_deployed_before_the_operator_chart(self, tmp_path):
        run = expand(tiny_spec(systems=["jcac"]))[0]
        plan = cluster_backend.command_plan(run, tmp_path)
        flat = [" ".join(c) for c in plan]
        nats = [i for i, c in enumerate(flat) if "nats-eval.yaml" in c]
        operator = [i for i, c in enumerate(flat) if "polyforge-operator" in c
                    and c.startswith("helm install")]
        assert nats, "the audit backbone is never deployed"
        assert operator, "expected an operator chart install"
        # Ordering is load-bearing, not tidiness: events.Connect runs at
        # operator startup and os.Exit(1)s if the broker is unreachable.
        assert nats[0] < operator[0]

    def test_operator_is_told_where_the_backbone_is(self, tmp_path):
        run = expand(tiny_spec(systems=["jcac"]))[0]
        plan = cluster_backend.command_plan(run, tmp_path)
        install = [c for c in plan
                   if c[:2] == ["helm", "install"] and "polyforge-operator" in c[2:]]
        assert install, "no operator install step"
        assert any("operator.natsURL=" in a for a in install[0]), (
            "without natsURL the operator's audit publisher is nil and every "
            "record is silently dropped")

    def test_empty_stream_fails_the_run(self, monkeypatch):
        import subprocess as _sp

        def fake(*a, **kw):
            return _sp.CompletedProcess(a[0], 0, stdout='{"messages": 0}', stderr="")

        monkeypatch.setattr(cluster_backend.subprocess, "run", fake)
        with pytest.raises(RuntimeError, match="EMPTY"):
            cluster_backend.check_audit_stream()

    def test_populated_stream_returns_its_count(self, monkeypatch):
        import subprocess as _sp

        def fake(*a, **kw):
            return _sp.CompletedProcess(a[0], 0, stdout='{"messages": 42}', stderr="")

        monkeypatch.setattr(cluster_backend.subprocess, "run", fake)
        assert cluster_backend.check_audit_stream() == 42

    def test_unreachable_backbone_fails_rather_than_reading_as_zero(self, monkeypatch):
        """A broker that cannot be reached must not be indistinguishable from
        a broker that carried nothing — that conflation is the whole bug."""
        import subprocess as _sp

        def fake(*a, **kw):
            return _sp.CompletedProcess(a[0], 1, stdout="", stderr="no such deploy")

        monkeypatch.setattr(cluster_backend.subprocess, "run", fake)
        with pytest.raises(RuntimeError, match="unreadable"):
            cluster_backend.check_audit_stream()


class TestPlannerFaultActuallyStopsThePlanner:
    """The planner-crash fault was cosmetic for four attempts.

    Fault 7 measured a 26 s endpoint gap — 2.6x the 10 s plan interval — yet
    zero fallback lines appeared, because the pod stayed 1/1 ready throughout
    termination and the operator's reused HTTP connection kept reaching it.
    """

    def _script(self):
        return (Path(__file__).resolve().parents[2] / "scripts"
                / "chaos_inject.sh").read_text(encoding="utf-8")

    def test_planner_is_scaled_to_zero_not_deleted(self):
        body = self._script()
        assert "--replicas=0" in body
        # Deleting the pod delists it from Endpoints while it keeps serving
        # established connections, which is why seven injections did nothing.
        assert "delete --wait=false" not in body

    def test_injector_asserts_the_fault_reached_the_controller(self):
        body = self._script()
        assert "positive control" in body
        assert "planner unavailable" in body


class TestSoakProtocolGuards:
    """WP14 Phase 5: rules that are enforceable rather than written down.

    Attempt 4 had no rule against local compute during a run. A `go test` on
    the same laptop compiled the operator package mid-soak and the restart
    cascade began within two minutes. The reasoning error was treating "does
    not touch Kubernetes objects" as equivalent to "does not affect the run".
    """

    def test_guard_script_exists_and_is_executable_shell(self):
        guard = Path(__file__).resolve().parents[2] / "scripts" / "guard-no-local-compute.sh"
        assert guard.exists()
        body = guard.read_text(encoding="utf-8")
        assert ".soak-running" in body
        # An override must exist — a guard with no escape hatch gets deleted
        # the first time someone genuinely needs to build — but it must be
        # explicit and disclosed rather than silent.
        assert "POLYFORGE_ALLOW_LOCAL_COMPUTE" in body

    def test_marker_is_gitignored(self):
        ignore = (Path(__file__).resolve().parents[2] / ".gitignore").read_text(encoding="utf-8")
        assert ".soak-running" in ignore

    def test_injector_writes_into_the_repo_not_a_temp_path(self):
        body = (Path(__file__).resolve().parents[2] / "scripts"
                / "chaos_inject.sh").read_text(encoding="utf-8")
        # Attempt 4's injector logs lived in %TEMP% and were snapshotted by
        # hand at hour 17, capturing only 5 of 8 faults.
        assert "live_soak_evidence" in body
        assert "EVIDENCE_DIR" in body
