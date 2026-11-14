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

    python arms.py A1 --target 0.5 > a1.yaml
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
RATE_QUERY = "sum(rate(cpuburn_requests_total[30s]))"


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


def render(arm: str, target: float) -> list[dict]:
    if arm == "A1":
        return [hpa_cpu(target), vpa()]
    if arm == "A2":
        return [keda_rate(target), vpa()]
    if arm in ("A3", "A4"):
        return []
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
