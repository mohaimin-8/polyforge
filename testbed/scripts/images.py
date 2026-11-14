"""Pull, build and side-load the WP10 testbed's images (programme 2).

Every third-party image is pulled BY DIGEST (images.lock), tagged with its
lock tag, checked, saved as a single-platform archive and loaded into the kind
node. The manifests reference the tags with imagePullPolicy: Never, so a pod
whose image was not side-loaded fails at once (ErrImageNeverPull) instead of
pulling something else from the network while a run waits: the harness's
lesson (cluster_backend.py, session 44). `docker save --platform` because
Docker 29's containerd store otherwise exports the multi-platform index and
`kind load` fails on blobs never pulled.

    python images.py plan               # print every command, run nothing
    python images.py pull               # third-party images, by digest
    python images.py build              # polyforge/ai-gateway:dev, polyforge/control-plane:dev
    python images.py load               # save + kind load into the testbed cluster
    python images.py verify             # what the kind node holds, by tag and digest
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

HERE = Path(__file__).resolve().parent
TESTBED = HERE.parent
REPO = TESTBED.parent
LOCK = TESTBED / "images.lock"
ARCHIVES = TESTBED / "out" / "images"
CLUSTER = "polyforge-testbed"
PLATFORM = "linux/amd64"


@dataclass(frozen=True)
class Image:
    role: str
    repository: str
    tag: str
    index_digest: str
    amd64_digest: str
    compressed_bytes: str
    source: str

    @property
    def ref(self) -> str:
        return f"{self.repository}:{self.tag}"

    @property
    def pinned(self) -> str:
        return f"{self.repository}@{self.index_digest}"

    @property
    def archive(self) -> Path:
        safe = self.ref.replace("/", "_").replace(":", "_")
        return ARCHIVES / f"{safe}.tar"


def read_lock(path: Path = LOCK) -> list[Image]:
    images = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) != 7:
            raise ValueError(f"{path}:{n}: expected 7 tab-separated columns, got {len(cols)}")
        image = Image(*cols)
        if image.role not in ("cluster", "workload", "build"):
            raise ValueError(f"{path}:{n}: unknown role {image.role!r}")
        if image.role != "build" and not (image.index_digest.startswith("sha256:")
                                          and image.amd64_digest.startswith("sha256:")):
            raise ValueError(f"{path}:{n}: {image.ref} needs index and linux/amd64 digests")
        images.append(image)
    return images


def pull_steps(images: Sequence[Image]) -> list[list[str]]:
    steps = []
    for im in images:
        if im.role == "build":
            continue
        steps.append(["docker", "pull", "--platform", PLATFORM, im.pinned])
        if im.role == "workload":
            steps.append(["docker", "tag", im.pinned, im.ref])
    return steps


def build_steps(images: Sequence[Image]) -> list[list[str]]:
    return [["docker", "build", "-f", im.source, "-t", im.ref, "."] for im in images if im.role == "build"]


def load_steps(images: Sequence[Image], cluster: str) -> list[list[str]]:
    steps = []
    for im in images:
        if im.role == "cluster":
            continue
        steps.append(["docker", "save", "--platform", PLATFORM, im.ref, "-o", str(im.archive)])
        steps.append(["kind", "load", "image-archive", str(im.archive), "--name", cluster])
    return steps


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


def run_cmd(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), cwd=REPO, capture_output=True, text=True)


def check_local_pins(images: Sequence[Image], run: Runner = run_cmd) -> list[str]:
    """Problems with the host's copies: a workload tag must carry its pinned
    index digest among its RepoDigests (i.e. it was pulled by that digest)."""
    problems = []
    for im in images:
        if im.role != "workload":
            continue
        proc = run(["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", im.ref])
        if proc.returncode != 0:
            problems.append(f"{im.ref}: not present locally (run `images.py pull`)")
            continue
        digests = json.loads(proc.stdout.strip() or "[]")
        if normalise_digest(im.pinned) not in {normalise_digest(d) for d in digests}:
            problems.append(f"{im.ref}: RepoDigests {digests} lack the pinned {im.pinned}")
    return problems


def node_images(cluster: str, run: Runner = run_cmd) -> dict[str, list[str]]:
    """repo:tag -> repo digests, as the kind node's containerd reports them."""
    proc = run(["docker", "exec", f"{cluster}-control-plane", "crictl", "images", "-o", "json"])
    if proc.returncode != 0:
        raise RuntimeError(f"crictl on {cluster}-control-plane failed: {proc.stderr.strip()}")
    out = {}
    for img in json.loads(proc.stdout).get("images", []):
        for tag in img.get("repoTags") or []:
            out[tag] = list(img.get("repoDigests") or [])
    return out


def _qualified(name: str) -> str:
    if "/" not in name:
        name = f"library/{name}"
    if "." not in name.split("/")[0] and name.split("/")[0] != "localhost":
        name = f"docker.io/{name}"
    return name


def normalise(ref: str) -> str:
    """containerd's fully qualified form of a reference (docker.io/library/...)."""
    name, _, tag = ref.rpartition(":")
    return f"{_qualified(name)}:{tag}"


def normalise_digest(ref: str) -> str:
    """The same for a digest reference: Docker reports Docker Hub images by
    their familiar name (postgres@sha256:...), the lock pins the full one."""
    name, _, digest = ref.partition("@")
    return f"{_qualified(name)}@{digest}"


def verify_node(images: Sequence[Image], present: dict[str, list[str]]) -> tuple[list[str], dict]:
    """(missing image refs, record of what the node holds for each)."""
    missing, record = [], {}
    for im in images:
        if im.role == "cluster":
            continue
        key = normalise(im.ref)
        if key not in present:
            missing.append(im.ref)
        else:
            record[im.ref] = present[key]
    return missing, record


def execute(steps: Sequence[Sequence[str]], run: Runner = run_cmd) -> int:
    for cmd in steps:
        print("+ " + " ".join(cmd), flush=True)
        proc = run(cmd)
        if proc.returncode != 0:
            print(proc.stderr.strip() or proc.stdout.strip(), file=sys.stderr)
            return proc.returncode
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=("plan", "pull", "build", "load", "verify"))
    ap.add_argument("--cluster", default=CLUSTER)
    args = ap.parse_args(argv)
    images = read_lock()
    if args.action == "plan":
        for cmd in pull_steps(images) + build_steps(images) + load_steps(images, args.cluster):
            print(" ".join(cmd))
        return 0
    if args.action == "pull":
        return execute(pull_steps(images))
    if args.action == "build":
        return execute(build_steps(images))
    if args.action == "load":
        problems = check_local_pins(images)
        if problems:
            print("refusing to load:\n  " + "\n  ".join(problems), file=sys.stderr)
            return 1
        ARCHIVES.mkdir(parents=True, exist_ok=True)
        return execute(load_steps(images, args.cluster))
    missing, record = verify_node(images, node_images(args.cluster))
    print(json.dumps(record, indent=1))
    if missing:
        print("missing on the node: " + ", ".join(missing), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
