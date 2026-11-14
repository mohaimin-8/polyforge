"""Bring the WP10 testbed up, step by step, stopping at the first failure.

Each step is one command of the runbook (testbed/README.md); output goes to
testbed/out/bringup/<step>.log, and testbed/out/bringup/status.json records
which steps passed, so a rerun skips them. The `secret` step writes a random
gateway admin key to testbed/out/admin-key.txt (git-ignored) unless one is
there, and stores it in the cluster as the pf-admin Secret the gateway and
control plane read; the manifests do not carry it.

    python bringup.py [--mode mode-b|mode-a] [--from STEP]
"""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Union

HERE = Path(__file__).resolve().parent
TESTBED = HERE.parent
OUT = TESTBED / "out" / "bringup"
KEY_FILE = TESTBED / "out" / "admin-key.txt"
CONTEXT = "kind-polyforge-testbed"
NAMESPACE = "pf-testbed"

Step = Union[list[str], Callable[[Path], int]]


def admin_secret(log_path: Path) -> int:
    """The namespace and the pf-admin Secret, from a key made once."""
    if not KEY_FILE.exists():
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEY_FILE.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    kc = ["kubectl", "--context", CONTEXT]
    with log_path.open("w", encoding="utf-8") as log:
        for make in (kc + ["create", "namespace", NAMESPACE, "--dry-run=client", "-o", "yaml"],
                     kc + ["-n", NAMESPACE, "create", "secret", "generic", "pf-admin",
                           f"--from-file=admin-key={KEY_FILE}", "--dry-run=client", "-o", "yaml"]):
            manifest = subprocess.run(make, capture_output=True, text=True)
            if manifest.returncode != 0:
                log.write(manifest.stderr)
                return manifest.returncode
            applied = subprocess.run(kc + ["apply", "-f", "-"], input=manifest.stdout, capture_output=True,
                                     text=True)
            log.write(applied.stdout + applied.stderr)  # names only: apply never echoes the key
            if applied.returncode != 0:
                return applied.returncode
    return 0


def steps(mode: str) -> list[tuple[str, Step, Path]]:
    py = sys.executable
    return [
        ("cluster", ["kind", "create", "cluster", "--config", str(TESTBED / "kind" / "kind-config.yaml")], TESTBED),
        ("pull", [py, "images.py", "pull"], HERE),
        ("build", [py, "images.py", "build"], HERE),
        ("load", [py, "images.py", "load"], HERE),
        ("verify", [py, "images.py", "verify"], HERE),
        ("secret", admin_secret, TESTBED),
        ("apply", ["kubectl", "--context", CONTEXT, "apply", "-k", str(TESTBED / "k8s" / mode)], TESTBED),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", default="mode-b", choices=("mode-b", "mode-a"))
    ap.add_argument("--from", dest="start", default=None)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    status_path = OUT / "status.json"
    status = json.loads(status_path.read_text()) if status_path.exists() else {}
    started = args.start is None
    for name, step, cwd in steps(args.mode):
        started = started or name == args.start
        if not started or (status.get(name) == "ok" and args.start != name):
            continue
        t0 = time.time()
        log_path = OUT / f"{name}.log"
        if callable(step):
            rc = step(log_path)
        else:
            with log_path.open("w", encoding="utf-8") as log:
                rc = subprocess.call(step, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
        status[name] = "ok" if rc == 0 else f"failed ({rc})"
        status[f"{name}_seconds"] = round(time.time() - t0)
        status_path.write_text(json.dumps(status, indent=1))
        print(f"{name}: {status[name]} in {status[f'{name}_seconds']} s", flush=True)
        if rc != 0:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
