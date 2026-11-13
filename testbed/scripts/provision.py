"""Create the testbed's tenants and API keys, and the initial knob decision
(programme 2, WP10).

For each tenant of a schedule (schedule.meta.json): create it on the control
plane with its SLO class as plan (an absent plan reads as "standard",
cluster_backend.provision_tenants' lesson) and mint one full-scope API key.
Writes tokens.json (tenant -> key; a secret, under testbed/out/) for k6, and
initial_decision.json, the starting posture for actuator/actuate.py: every
tenant on --tier with --cache-mb (the simulator's TenantState default is
small / 128 MB).

    python provision.py --meta ../out/schedules/pop007/schedule.meta.json \
        --admin-key-file ../out/admin-key.txt --out-dir ../out
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Sequence

DEFAULT_CONTROL_PLANE = "http://127.0.0.1:30080"
ATTEMPTS = 5

Opener = Callable[[urllib.request.Request, float], Any]


def default_opener(req: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 (operator-given URL)


class ProvisionError(RuntimeError):
    pass


def post_json(base: str, path: str, body: dict, admin_key: str, opener: Opener = default_opener,
              sleep: Callable[[float], None] = time.sleep) -> dict | None:
    """POST with the admin key. Returns the decoded body, or None for 409
    (already exists). Transport failures are retried; an HTTP status is the
    server's answer and is not."""
    req = urllib.request.Request(base.rstrip("/") + path, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "X-PolyForge-Admin-Key": admin_key})
    last: Exception | None = None
    for attempt in range(ATTEMPTS):
        try:
            # 60 s: a cold control plane migrates its schema on the first write.
            with opener(req, 60.0) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as err:
            if err.code == 409:
                return None
            detail = err.read()[:300].decode("utf-8", "replace")
            raise ProvisionError(f"POST {path} -> {err.code}: {detail}") from err
        except (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError) as err:
            last = err
            sleep(2.0 * (attempt + 1))
    raise ProvisionError(f"POST {path} failed after {ATTEMPTS} attempts: {last}") from last


def tenants_from_meta(path: Path) -> list[dict]:
    meta = json.loads(Path(path).read_text(encoding="utf-8"))
    tenants = meta.get("tenants") or []
    if not tenants:
        raise ProvisionError(f"{path} lists no tenants")
    return [{"id": t["id"], "plan": t.get("slo_class", "standard")} for t in tenants]


def provision(base: str, tenants: Sequence[dict], admin_key: str, opener: Opener = default_opener,
              sleep: Callable[[float], None] = time.sleep) -> dict[str, str]:
    """tenant -> API key secret. A tenant that already exists keeps its data
    and gets a fresh key."""
    tokens = {}
    for t in tenants:
        post_json(base, "/v1/tenants", {"id": t["id"], "name": t["id"], "plan": t["plan"]},
                  admin_key, opener, sleep)
        key = post_json(base, f"/v1/tenants/{t['id']}/api-keys", {"name": "wp10", "scope": "full"},
                        admin_key, opener, sleep)
        if not key or not key.get("secret"):
            raise ProvisionError(f"no key secret returned for tenant {t['id']}")
        tokens[t["id"]] = key["secret"]
    return tokens


def initial_decision(tenant_ids: Sequence[str], tier: str, cache_mb: int) -> dict:
    return {"step": 0, "t_s": 0,
            "tenants": {tid: {"model_tier": tier, "cache_size_mb": cache_mb} for tid in tenant_ids},
            "note": "initial posture written by scripts/provision.py"}


def write_secret_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # best effort on Windows; the directory is git-ignored either way


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--meta", required=True, help="schedule.meta.json naming the tenants")
    ap.add_argument("--control-plane", default=DEFAULT_CONTROL_PLANE)
    ap.add_argument("--admin-key-file", required=True)
    ap.add_argument("--out-dir", required=True, help="tokens.json and initial_decision.json go here")
    ap.add_argument("--tier", default="small")
    ap.add_argument("--cache-mb", type=int, default=128)
    args = ap.parse_args(argv)
    admin_key = Path(args.admin_key_file).read_text(encoding="utf-8").strip()
    if not admin_key:
        print("provision: the admin key file is empty", file=sys.stderr)
        return 2
    try:
        tenants = tenants_from_meta(Path(args.meta))
        tokens = provision(args.control_plane, tenants, admin_key)
    except ProvisionError as err:
        print(f"provision: {err}", file=sys.stderr)
        return 1
    out = Path(args.out_dir)
    write_secret_json(out / "tokens.json", tokens)
    (out / "initial_decision.json").write_text(
        json.dumps(initial_decision(list(tokens), args.tier, args.cache_mb), indent=1), encoding="utf-8")
    print(f"provisioned {len(tokens)} tenants ({', '.join(tokens)}); keys in {out / 'tokens.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
