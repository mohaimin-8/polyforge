"""The WP17 arms' Kubernetes objects, rendered from their parameters (programme 2).

  A1  autoscaling/v2 HorizontalPodAutoscaler on CPU utilisation (target
      averageUtilization) + a VerticalPodAutoscaler on CPU;
  A2  a KEDA ScaledObject on the service's request rate (Prometheus trigger,
      AverageValue per pod, KEDA's default 30 s polling) + the same VPA;
  A3, A4  no autoscaler object: the planner pod (planner.py) acts through the
      scale subresource and in-place pod resize.
The VPA: updateMode InPlaceOrRecreate (GA in VPA 1.6), CPU only, between the
twin's CPU levels (minAllowed 250m, maxAllowed 2); the recommender's flags
(default or fast, research/hvpa/vpa.SETTINGS) are set where the VPA is
installed (install.py), not here. Replica bounds match the twin: 1 to 8.

    python hvpa_arms.py A1 --target 0.5 > a1.yaml
"""

from __future__ import annotations

import argparse
import sys

import yaml

NAMESPACE = "pf-hvpa"
TARGET = {"apiVersion": "apps/v1", "kind": "Deployment", "name": "cpuburn"}
RMIN, RMAX = 1, 8
CPU_MIN, CPU_MAX = "250m", "2"
PROMETHEUS = f"http://prometheus.{NAMESPACE}:9090"
RATE_QUERY = "sum(rate(cpuburn_arrivals_total[30s]))"   # offered requests, as the planner reads them


def vpa() -> dict:
    return {"apiVersion": "autoscaling.k8s.io/v1", "kind": "VerticalPodAutoscaler",
            "metadata": {"name": "cpuburn", "namespace": NAMESPACE},
            "spec": {"targetRef": TARGET,
                     "updatePolicy": {"updateMode": "InPlaceOrRecreate"},
                     "resourcePolicy": {"containerPolicies": [{
                         "containerName": "cpuburn", "controlledResources": ["cpu"],
                         "minAllowed": {"cpu": CPU_MIN}, "maxAllowed": {"cpu": CPU_MAX}}]}}}


def hpa_cpu(target: float) -> dict:
    if not 0 < target <= 1:
        raise ValueError("the CPU utilisation target is a share in (0, 1]")
    return {"apiVersion": "autoscaling/v2", "kind": "HorizontalPodAutoscaler",
            "metadata": {"name": "cpuburn", "namespace": NAMESPACE},
            "spec": {"scaleTargetRef": TARGET, "minReplicas": RMIN, "maxReplicas": RMAX,
                     "metrics": [{"type": "Resource", "resource": {
                         "name": "cpu", "target": {"type": "Utilization",
                                                   "averageUtilization": int(round(target * 100))}}}]}}


def keda_rate(target_rps: float) -> dict:
    if target_rps <= 0:
        raise ValueError("the rate target per pod must be positive")
    return {"apiVersion": "keda.sh/v1alpha1", "kind": "ScaledObject",
            "metadata": {"name": "cpuburn", "namespace": NAMESPACE},
            "spec": {"scaleTargetRef": {"name": "cpuburn"}, "minReplicaCount": RMIN, "maxReplicaCount": RMAX,
                     "pollingInterval": 30,
                     "triggers": [{"type": "prometheus", "metricType": "AverageValue",
                                   "metadata": {"serverAddress": PROMETHEUS, "query": RATE_QUERY,
                                                "threshold": f"{target_rps:g}"}}]}}


PYTHON_IMAGE = "docker.io/library/python:3.13.17-slim-bookworm"   # WP10's, side-loaded


def planner(mode: str, width: int) -> list[dict]:
    """The planner pod (planner/planner_pod.py) and the least it may do: read
    the Deployment and its pods, scale it, resize its pods in place. Its code
    and the fitted plant come from the ConfigMap wp17-planner."""
    if mode not in ("joint", "perknob") or width not in (1, 2, 4):
        raise ValueError("mode joint|perknob, width 1, 2 or 4")
    meta = {"name": "planner", "namespace": NAMESPACE}
    role = {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "metadata": meta, "rules": [
        {"apiGroups": ["apps"], "resources": ["deployments"], "resourceNames": ["cpuburn"], "verbs": ["get"]},
        {"apiGroups": ["apps"], "resources": ["deployments/scale"], "resourceNames": ["cpuburn"],
         "verbs": ["get", "patch"]},
        {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list"]},
        {"apiGroups": [""], "resources": ["pods/resize"], "verbs": ["patch"]}]}
    binding = {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding", "metadata": meta,
               "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "planner"},
               "subjects": [{"kind": "ServiceAccount", "name": "planner", "namespace": NAMESPACE}]}
    deploy = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {**meta, "labels": {"app.kubernetes.io/name": "planner"}},
              "spec": {"replicas": 1, "strategy": {"type": "Recreate"},
                       "selector": {"matchLabels": {"app.kubernetes.io/name": "planner"}},
                       "template": {"metadata": {"labels": {"app.kubernetes.io/name": "planner"}}, "spec": {
                           "serviceAccountName": "planner", "enableServiceLinks": False,
                           "securityContext": {"runAsNonRoot": True, "runAsUser": 65532,
                                               "seccompProfile": {"type": "RuntimeDefault"}},
                           "containers": [{
                               "name": "planner", "image": PYTHON_IMAGE, "imagePullPolicy": "Never",
                               "command": ["python", "-u", "/m/planner_pod.py", "--mode", mode, "--width", str(width),
                                           "--calibration", "/m/calibration.json"],
                               "workingDir": "/m",
                               "resources": {"requests": {"cpu": "250m", "memory": "128Mi"},
                                             "limits": {"cpu": "250m", "memory": "128Mi"}},
                               "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                                   "capabilities": {"drop": ["ALL"]}},
                               "volumeMounts": [{"name": "m", "mountPath": "/m", "readOnly": True}]}],
                           "volumes": [{"name": "m", "configMap": {"name": "wp17-planner"}}]}}}}
    sa = {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": meta}
    return [sa, role, binding, deploy]


def render(arm: str, target: float) -> list[dict]:
    """`target`: A1 utilisation share, A2 requests/s per pod, A3/A4 the lattice width."""
    if arm == "A1":
        return [hpa_cpu(target), vpa()]
    if arm == "A2":
        return [keda_rate(target), vpa()]
    if arm in ("A3", "A4"):
        return planner("perknob" if arm == "A3" else "joint", int(target))
    raise ValueError(f"unknown arm {arm!r}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("arm", choices=("A1", "A2", "A3", "A4"))
    ap.add_argument("--target", type=float, required=True, help="A1: utilisation share; A2: requests/s per pod")
    args = ap.parse_args(argv)
    sys.stdout.write(yaml.safe_dump_all(render(args.arm, args.target), sort_keys=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
