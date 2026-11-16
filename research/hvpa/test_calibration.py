"""The WP17 plant fit on synthetic curves with known constants."""

import json

import pandas as pd
import pytest

import calibration
import service_plant as plant


def _curve(d, cpu, w=0.05, a=0.9, slow=1.0):
    mu = cpu / w * slow
    s = w / min(cpu, 1.0)
    rows = [{"phase": "baseline", "level": "", "pass_no": "", "seq": i, "scheduled_s": "", "latency_s": s}
            for i in range(30)]
    for rho in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9):
        for i in range(20):
            rows.append({"phase": "load", "level": rho, "pass_no": i % 2, "seq": i, "scheduled_s": i,
                         "latency_s": s * (1 - rho) ** (-a)})
    d.mkdir(parents=True)
    pd.DataFrame(rows).to_csv(d / "load_curve_requests.csv", index=False)
    (d / "load_curve_meta.json").write_text(json.dumps({"cpu": cpu, "l0_s": s, "mu_pre_rps": mu, "mu_post_rps": mu}))


def test_a_linear_service_is_recovered(tmp_path):
    for c in (0.25, 0.5, 1.0, 2.0):
        _curve(tmp_path / f"c{int(c * 1000)}m", c)
    cal = calibration.fit(tmp_path)
    assert cal["cpu_per_request_s"] == pytest.approx(0.05) and cal["linear_within_10pct"]
    assert cal["congestion_exponent"] == pytest.approx(0.9, abs=1e-9)
    assert cal["slo_s"] == pytest.approx(10 * 0.05)
    out = tmp_path / "cal.json"
    out.write_text(json.dumps(cal))
    p = plant.from_calibration(out)
    assert p.cpu_per_request_s == pytest.approx(0.05) and p.status.startswith("measured")


def test_a_sublinear_level_fails_the_plans_check(tmp_path):
    for c in (0.25, 0.5, 1.0):
        _curve(tmp_path / f"c{int(c * 1000)}m", c)
    _curve(tmp_path / "c2000m", 2.0, slow=0.7)    # 2 cores deliver 70 % of twice one core
    cal = calibration.fit(tmp_path)
    assert not cal["linear_within_10pct"] and cal["worst_w_deviation"] > 0.1


def test_no_curves_is_an_error(tmp_path):
    with pytest.raises(ValueError):
        calibration.fit(tmp_path)


def test_the_node_size_is_carried_into_the_plant(tmp_path):
    for c in (0.25, 0.5, 1.0, 2.0):
        _curve(tmp_path / f"c{int(c * 1000)}m", c)
    nf = tmp_path / "node_cpu.json"
    nf.write_text(json.dumps({"node_cpu": 4.75}))
    cal = calibration.fit(tmp_path, nf)
    out = tmp_path / "cal.json"
    out.write_text(json.dumps(cal))
    assert cal["node_cpu"] == 4.75 and plant.from_calibration(out).node_cpu == 4.75


def _point(curves, r, c, mu, gen=0.2):
    d = curves / "replicas" / f"r{r}c{int(c * 1000)}m"
    d.mkdir(parents=True)
    meta = {"replicas": r, "cpu": c, "mu_pre_rps": mu, "mu_post_rps": mu}
    if gen is not None:
        meta["generator_cpu_cores"] = gen
    (d / "load_curve_meta.json").write_text(json.dumps(meta))


def _levels(curves):
    for c in (0.25, 0.5, 1.0, 2.0):
        _curve(curves / f"c{int(c * 1000)}m", c)


def test_pods_within_ten_percent_of_linear_keep_the_linear_plant(tmp_path):
    _levels(tmp_path)
    _point(tmp_path, 2, 1.0, 2 / 0.05 * 0.97)
    _point(tmp_path, 4, 1.0, 4 / 0.05 * 0.92)
    cal = calibration.fit(tmp_path)
    assert cal["linear_in_replicas_within_10pct"] and "efficiency" not in cal
    assert [round(e, 3) for _, e in cal["efficiency_measured"]] == [0.97, 0.92]
    assert cal["linear_within_10pct"] and len(cal["levels"]) == 4    # the points are not CPU levels


def test_sublinear_pods_give_the_plant_the_measured_table(tmp_path):
    _levels(tmp_path)
    _point(tmp_path, 2, 1.0, 2 / 0.05 * 0.9)
    _point(tmp_path, 6, 1.0, 6 / 0.05 * 0.6)
    _point(tmp_path, 3, 2.0, 6 / 0.05 * 0.7)       # the same total: the mean is used
    _point(tmp_path, 4, 1.0, 4 / 0.05 * 0.5, gen=0.6)   # the generator was the bottleneck: kept out
    _point(tmp_path, 8, 0.5, 4 / 0.05 * 0.5, gen=None)  # no generator record: kept out
    cal = calibration.fit(tmp_path)
    assert not cal["linear_in_replicas_within_10pct"]
    assert cal["efficiency"] == [[2.0, pytest.approx(0.9)], [6.0, pytest.approx(0.65)]]
    out_of = {(p["replicas"], p["cpu"]): p for p in cal["replica_points"] if not p["used"]}
    assert set(out_of) == {(4, 1.0), (8, 0.5)} and "half its core" in out_of[(4, 1.0)]["why"]
    out = tmp_path / "cal.json"
    out.write_text(json.dumps(cal))
    p = plant.from_calibration(out)
    assert p.efficiency == ((2.0, pytest.approx(0.9)), (6.0, pytest.approx(0.65)))
    assert p.eta(6.0) == pytest.approx(0.65)


def test_without_horizontal_points_there_is_no_replica_record(tmp_path):
    _levels(tmp_path)
    cal = calibration.fit(tmp_path)
    assert not any(k.startswith(("replica", "efficiency", "linear_in_replicas")) for k in cal)


def _sublinear_levels(curves):
    for c in (0.25, 0.5, 1.0):
        _curve(curves / f"c{int(c * 1000)}m", c)
    _curve(curves / "c2000m", 2.0, slow=0.7)    # one pod at 2 cores: 28 rps, not 40


def test_a_sublinear_level_gives_the_plant_the_measured_curve(tmp_path, capsys):
    _sublinear_levels(tmp_path)
    out = tmp_path / "cal.json"
    assert calibration.main(["--curves", str(tmp_path), "--out", str(out)]) == 0
    assert "the plant uses the measured curve" in capsys.readouterr().out
    cal = json.loads(out.read_text())
    assert cal["capacity_curve"] == [[0.25, pytest.approx(5.0)], [0.5, pytest.approx(10.0)],
                                     [1.0, pytest.approx(20.0)], [2.0, pytest.approx(28.0)]]
    p = plant.from_calibration(out)
    assert p.capacity_rps(2.0) == pytest.approx(28.0) and p.capacity_rps(0.5) == pytest.approx(10.0)


def test_a_linear_service_has_no_measured_curve(tmp_path):
    _levels(tmp_path)
    assert "capacity_curve" not in calibration.fit(tmp_path)


def test_on_the_measured_curve_pods_are_measured_against_one_pod_at_the_curve(tmp_path):
    _sublinear_levels(tmp_path)
    _point(tmp_path, 2, 2.0, 2 * 28.0 * 0.8)    # two pods at 2 cores: 80 % of two on the curve
    _point(tmp_path, 4, 1.0, 4 * 20.0 * 0.6)    # the same 4 cores in all: the mean, 0.7
    cal = calibration.fit(tmp_path)
    assert cal["efficiency"] == [[4.0, pytest.approx(0.7)]]
    out = tmp_path / "cal.json"
    out.write_text(json.dumps(cal))
    assert plant.from_calibration(out).capacity_rps(2.0, total_cpu=4.0) == pytest.approx(28.0 * 0.7)
