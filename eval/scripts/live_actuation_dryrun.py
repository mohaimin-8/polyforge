#!/usr/bin/env python3
"""WP8a — live actuation dry-run for the Wave 4 three-knob plane.

Non-scored. It proves the plumbing the scored B1 sitting (WP8b) depends on,
against a real kube-apiserver rather than against a mock:

  1. every frozen arm's Policy/Budget/Tenant CRs **admit** against the
     committed CRDs;
  2. the CRD's CEL bound rules are **live** — malformed bounds are rejected
     by the apiserver, not silently accepted;
  3. each arm's CRs actually **encode the pin** its name claims (min == max
     on the frozen knobs, a real span on the free one).

(3) is the one that matters. `PREREG_WAVE4_LIVE_PLANE` §Arms says the
ablations isolate *jointness* by pinning two knobs per arm; an arm whose pin
does not reach the CR is an unlabelled copy of the full controller, and its
result would mean nothing. The roadmap says so explicitly: "if a pin leaks,
that is a finding, not a nuisance".

Requires a cluster with the CRDs applied and `kubectl` pointed at it:

    kind create cluster --name polyforge-eval --config <kind-small.yaml>
    kubectl apply -f deploy/helm/polyforge-operator/crds/
    python eval/scripts/live_actuation_dryrun.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness import workloads  # noqa: E402
from harness.cluster_backend import _arm_knob_bounds, operator_crs  # noqa: E402
from harness.config import run_identity  # noqa: E402
from harness.config import RunSpec, load  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC = REPO_ROOT / "eval" / "experiments" / "wave4_live_plane.yaml"
NS = "default"


def kubectl(*args: str, stdin: str | None = None) -> tuple[int, str]:
    p = subprocess.run(["kubectl", *args], input=stdin, capture_output=True,
                       text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def run_for(spec, system: str, workload: str) -> RunSpec:
    mix, cluster = spec.tenant_mixes[0], spec.cluster_sizes[0]
    run_id, seed = run_identity(spec, system, workload, mix, cluster, 0)
    return RunSpec(run_id=run_id, experiment=spec.name, system=system,
                   workload=workload, tenant_mix=mix, cluster_size=cluster,
                   rep=0, seed=seed, steps=spec.steps, backend=spec.backend,
                   store_timeseries=False)


def apply(manifest: str) -> tuple[int, str]:
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(manifest)
        path = fh.name
    try:
        return kubectl("apply", "-n", NS, "-f", path)
    finally:
        Path(path).unlink(missing_ok=True)


def bounds_report(system: str, cluster_size: str) -> dict:
    """What the arm's CRs *claim* about each knob, read from the same
    function the live backend renders with — so this cannot drift from what
    a real run would apply."""
    from model import TenantState  # noqa: E402  (via harness sys.path)

    size = workloads.CLUSTER_SIZES[cluster_size]
    (rmin, rmax), (cmin, cmax), (tmin, tmax) = _arm_knob_bounds(
        system, TenantState(), size)
    return {
        "replicas": {"min": rmin, "max": rmax, "pinned": rmin == rmax},
        # cacheSizeMBMax 0 means "no ceiling" in the CRD, so a free cache
        # knob is (0, 0) and is NOT a pin — the one place min == max has to
        # be read against the CRD's own sentinel rather than literally.
        "cache": {"min": cmin, "max": cmax,
                  "pinned": cmin == cmax and not (cmin == 0 and cmax == 0)},
        "tier": {"min": tmin, "max": tmax, "pinned": tmin == tmax},
    }


# What each arm's NAME promises: which knobs must be pinned in its CRs.
EXPECTED_PINS = {
    "jcac": set(),
    "replica-only": {"cache", "tier"},
    "cache-only": {"replicas", "tier"},
    "tier-only": {"replicas", "cache"},
}


def main() -> int:
    spec = load(str(SPEC))
    workload = spec.workloads[0]
    failures: list[str] = []

    print(f"spec: {spec.name}  arms={spec.systems}  cell={workload}/"
          f"{spec.tenant_mixes[0]}/{spec.cluster_sizes[0]}\n")

    print("=== 1. CR admission, per arm ===")
    for system in spec.systems:
        run = run_for(spec, system, workload)
        code, out = apply(operator_crs(run))
        n = len([ln for ln in out.splitlines() if " created" in ln or " configured" in ln])
        status = "ADMITTED" if code == 0 else "REJECTED"
        print(f"  {system:14s} {status:9s} ({n} objects)")
        if code != 0:
            failures.append(f"{system}: CRs rejected -- {out.splitlines()[-1]}")

    print("\n=== 2. CEL bound rules are live (negative tests) ===")
    negatives = [
        ("replicaMin > replicaMax", """apiVersion: polyforge.io/v1alpha1
kind: Policy
metadata:
  name: cel-probe-replicas
spec:
  tenantRef: t00
  targetDeployment: polyforge/polyforge-control-plane
  replicas: 2
  replicaMin: 9
  replicaMax: 3
  cacheSizeMB: 128
  modelTier: small
"""),
        ("cacheSizeMBMin > cacheSizeMBMax", """apiVersion: polyforge.io/v1alpha1
kind: Policy
metadata:
  name: cel-probe-cache
spec:
  tenantRef: t00
  targetDeployment: polyforge/polyforge-control-plane
  replicas: 2
  replicaMin: 1
  replicaMax: 6
  cacheSizeMB: 128
  cacheSizeMBMin: 512
  cacheSizeMBMax: 64
  modelTier: small
"""),
    ]
    for label, manifest in negatives:
        code, out = apply(manifest)
        rejected = code != 0
        print(f"  {label:34s} {'REJECTED (rule live)' if rejected else 'ACCEPTED -- RULE IS DEAD'}")
        if not rejected:
            failures.append(f"CEL rule for {label} did not fire")
            kubectl("delete", "-n", NS, "policy",
                    "cel-probe-replicas" if "replica" in label else "cel-probe-cache")

    print("\n=== 3. Does each arm encode the pin its name claims? ===")
    print(f"  {'arm':14s} {'replicas':>18s} {'cache':>18s} {'tier':>18s}   verdict")
    for system in spec.systems:
        b = bounds_report(system, spec.cluster_sizes[0])
        pinned = {k for k, v in b.items() if v["pinned"]}
        expected = EXPECTED_PINS[system]
        ok = pinned == expected
        cells = []
        for knob in ("replicas", "cache", "tier"):
            v = b[knob]
            mark = "PIN" if v["pinned"] else "free"
            cells.append(f"{v['min']}..{v['max']} {mark}")
        verdict = "ok" if ok else f"LEAK: expected pins {sorted(expected) or '[]'}, got {sorted(pinned) or '[]'}"
        print(f"  {system:14s} " + " ".join(f"{c:>18s}" for c in cells) + f"   {verdict}")
        if not ok:
            failures.append(f"{system}: pin mismatch -- expected {sorted(expected)}, "
                            f"CRs encode {sorted(pinned)}")

    print("\n=== summary ===")
    if failures:
        for f in failures:
            print(f"  FINDING: {f}")
        print(f"\n{len(failures)} finding(s). Per the roadmap these are results, "
              "not nuisances -- record them.")
        return 1
    print("  all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
