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
