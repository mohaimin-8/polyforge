"""The WP17 arm objects: what each arm puts in the cluster."""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hvpa_arms as arms  # noqa: E402  (not `arms`: research/comparators has one)


def test_a1_is_a_cpu_hpa_with_an_in_place_cpu_vpa():
    hpa, v = arms.render("A1", 0.55)
    metric = hpa["spec"]["metrics"][0]["resource"]
    assert metric["name"] == "cpu" and metric["target"]["averageUtilization"] == 55
    assert (hpa["spec"]["minReplicas"], hpa["spec"]["maxReplicas"]) == (1, 8)
    policy = v["spec"]["resourcePolicy"]["containerPolicies"][0]
    assert v["spec"]["updatePolicy"]["updateMode"] == "InPlaceOrRecreate"
    assert policy["controlledResources"] == ["cpu"] and policy["minAllowed"]["cpu"] == "250m"


def test_a2_scales_on_the_request_rate_through_keda():
    so, v = arms.render("A2", 12.5)
    trig = so["spec"]["triggers"][0]
    assert trig["type"] == "prometheus" and trig["metricType"] == "AverageValue"
    assert trig["metadata"]["threshold"] == "12.5" and "cpuburn_arrivals_total" in trig["metadata"]["query"]
    assert so["spec"]["pollingInterval"] == 30 and v["kind"] == "VerticalPodAutoscaler"


def test_planner_arms_install_the_planner_pod_with_least_privilege():
    sa, role, binding, deploy = arms.render("A4", 2)
    cmd = deploy["spec"]["template"]["spec"]["containers"][0]["command"]
    assert cmd[cmd.index("--mode") + 1] == "joint" and cmd[cmd.index("--width") + 1] == "2"
    assert arms.render("A3", 1)[3]["spec"]["template"]["spec"]["containers"][0]["command"][4] == "perknob"
    verbs = {(r["resources"][0], tuple(r["verbs"])) for r in role["rules"]}
    assert verbs == {("deployments", ("get",)), ("deployments/scale", ("get", "patch")),
                     ("pods", ("get", "list")), ("pods/resize", ("patch",))}
    assert binding["subjects"][0]["name"] == sa["metadata"]["name"] == "planner"


def test_bad_targets_are_refused():
    with pytest.raises(ValueError):
        arms.hpa_cpu(1.5)
    with pytest.raises(ValueError):
        arms.keda_rate(0)
    with pytest.raises(ValueError):
        arms.planner("joint", 3)


def test_the_cli_emits_valid_yaml(capsys):
    assert arms.main(["A1", "--target", "0.4"]) == 0
    docs = list(yaml.safe_load_all(capsys.readouterr().out))
    assert [d["kind"] for d in docs] == ["HorizontalPodAutoscaler", "VerticalPodAutoscaler"]
