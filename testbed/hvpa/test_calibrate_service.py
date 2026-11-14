"""The WP17 step-3 Job: what runs where, with which limits."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import calibrate_service as cal  # noqa: E402


def test_the_job_runs_the_curve_tool_on_one_core_against_the_service():
    j = cal.job(0.25, 60)
    c = j["spec"]["template"]["spec"]["containers"][0]
    assert j["metadata"]["name"] == "wp17-curve-c250m"
    assert c["image"] == cal.PYTHON_IMAGE and c["imagePullPolicy"] == "Never"
    assert c["command"][2:] == ["/m/cpu_curve.py", "--base-url", "http://cpuburn.pf-hvpa", "--cpu", "0.25",
                                "--level-seconds", "60"]
    assert c["resources"]["limits"]["cpu"] == "1" and c["resources"]["requests"]["cpu"] == "1"
    assert j["spec"]["backoffLimit"] == 0


def test_the_levels_are_the_plans():
    assert cal.LEVELS == (0.25, 0.5, 1.0, 2.0)


def test_the_python_image_is_the_one_wp10_side_loads():
    lock = (Path(__file__).resolve().parents[1] / "images.lock").read_text(encoding="utf-8")
    repo, tag = cal.PYTHON_IMAGE.rsplit(":", 1)
    assert f"{repo}\t{tag}\t" in lock
