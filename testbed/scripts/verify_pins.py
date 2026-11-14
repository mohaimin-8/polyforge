"""Re-read every pin of the WP10 testbed from public metadata (network, no pull).

images.lock: each image's index digest from its registry's OCI distribution API
(anonymous pull token, manifest HEAD), and the linux/amd64 entry of that index.
models.lock: each file's size, and for large (LFS) files its sha256, from the
Hugging Face resolve endpoint's X-Linked-Size / X-Linked-ETag headers (HEAD,
redirects not followed, nothing downloaded).

    python verify_pins.py            # exit 0 when every pin still matches
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

HERE = Path(__file__).resolve().parent
TESTBED = HERE.parent
sys.path.insert(0, str(HERE))
import images as images_lock  # noqa: E402  (sibling module)

MODELS_LOCK = TESTBED / "k8s" / "base" / "models.lock"
ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])
REGISTRIES = {
    "ghcr.io": ("https://ghcr.io", "https://ghcr.io/token?scope=repository:{repo}:pull"),
    "docker.io": ("https://registry-1.docker.io",
                  "https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull"),
}

Fetch = Callable[[str, Mapping[str, str], str], tuple[int, Mapping[str, str], bytes]]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # report the 3xx itself
        return None


def fetch(url: str, headers: Mapping[str, str], method: str = "GET") -> tuple[int, Mapping[str, str], bytes]:
    req = urllib.request.Request(url, headers=dict(headers), method=method)
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=30) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as err:  # 3xx and 4xx land here without NoRedirect following
        return err.code, {k.lower(): v for k, v in err.headers.items()}, err.read()


def split_repo(repository: str) -> tuple[str, str]:
    host, _, repo = repository.partition("/")
    return host, repo


@dataclass(frozen=True)
class Check:
    item: str
    ok: bool
    detail: str


def challenged_get(url: str, headers: Mapping[str, str], get: Fetch = fetch,
                   hops: int = 3) -> tuple[int, Mapping[str, str], bytes]:
    """GET that follows a redirect (registry.k8s.io sends manifest requests to
    a regional mirror with a 307) and answers a Bearer challenge with an
    anonymous token from the challenge's realm."""
    status, resp_headers, body = get(url, headers, "GET")
    if status in (301, 302, 307, 308) and hops > 0 and "location" in resp_headers:
        return challenged_get(resp_headers["location"], headers, get, hops - 1)
    challenge = resp_headers.get("www-authenticate", "")
    if status == 401 and challenge.lower().startswith("bearer") and hops > 0:
        params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        query = urllib.parse.urlencode({k: v for k, v in params.items() if k in ("service", "scope")})
        t_status, _, t_body = get(f"{params.get('realm', '')}?{query}", {}, "GET")
        if t_status != 200:
            return t_status, {}, b""
        token = json.loads(t_body)
        token = token.get("token") or token.get("access_token", "")
        return challenged_get(url, {**headers, "Authorization": f"Bearer {token}"}, get, hops - 1)
    return status, resp_headers, body


def check_image(image: images_lock.Image, get: Fetch = fetch) -> Check:
    host, repo = split_repo(image.repository)
    if host == "registry.k8s.io":
        status, headers, body = challenged_get(f"https://{host}/v2/{repo}/manifests/{image.tag}",
                                               {"Accept": ACCEPT}, get)
        if status != 200:
            return Check(image.ref, False, f"manifest -> {status}")
        return _compare(image, headers, body)
    if host not in REGISTRIES:
        return Check(image.ref, False, f"no registry API known for {host}")
    api, token_url = REGISTRIES[host]
    status, _, body = get(token_url.format(repo=repo), {}, "GET")
    if status != 200:
        return Check(image.ref, False, f"token request -> {status}")
    auth = {"Authorization": f"Bearer {json.loads(body)['token']}", "Accept": ACCEPT}
    status, headers, body = get(f"{api}/v2/{repo}/manifests/{image.tag}", auth, "GET")
    if status != 200:
        return Check(image.ref, False, f"manifest -> {status}")
    return _compare(image, headers, body)


def _compare(image: images_lock.Image, headers: Mapping[str, str], body: bytes) -> Check:
    digest = headers.get("docker-content-digest", "")
    index = json.loads(body)
    amd64 = [m["digest"] for m in index.get("manifests", [])
             if m.get("platform", {}).get("os") == "linux" and m.get("platform", {}).get("architecture") == "amd64"]
    ok = digest == image.index_digest and image.amd64_digest in amd64
    return Check(image.ref, ok, f"tag now -> {digest}; linux/amd64 {amd64[:1]}")


def read_models(path: Path = MODELS_LOCK) -> list[list[str]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            rows.append(line.split("\t"))
    return rows


def check_model(row: Sequence[str], get: Fetch = fetch) -> Check:
    _set, path, sha, size, url = row
    status, headers, _ = get(url, {}, "HEAD")
    if status not in (200, 302, 307):
        return Check(path, False, f"HEAD -> {status}")
    linked_size = headers.get("x-linked-size")
    etag = headers.get("x-linked-etag", "").strip('"')
    ok = linked_size == size
    detail = f"size {linked_size}"
    if len(etag) == 64:  # LFS files: the ETag is the sha256
        ok = ok and etag == sha
        detail += f", sha256 {etag[:12]}..."
    else:
        detail += " (git blob: sha256 checked by the in-cluster loader)"
    return Check(path, ok, detail)


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lock", type=Path, default=images_lock.LOCK)
    ap.add_argument("--no-models", action="store_true", help="images only (the WP17 lock has no models)")
    args = ap.parse_args(argv)
    checks = [check_image(i) for i in images_lock.read_lock(args.lock) if i.role != "build"]
    if not args.no_models:
        checks += [check_model(r) for r in read_models()]
    for c in checks:
        print(f"{'ok ' if c.ok else 'BAD'}  {c.item}: {c.detail}")
    bad = [c for c in checks if not c.ok]
    print(f"{len(checks) - len(bad)}/{len(checks)} pins match their public metadata")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
