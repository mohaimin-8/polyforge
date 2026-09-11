"""Drive the B1 rented host through Lambda's cloud API, without a browser.

The runbook (docs/WAVE4_RENTED_HOST_RUNBOOK.md) is vendor-neutral; this is
the vendor-specific hand that launches, watches and terminates the one box
the session-48 decision named: 1x A100 40 GB, 30 vCPU. Every billable action
is its own explicit subcommand; nothing here loops or retries a launch.

The API key is read from the file $LAMBDA_API_KEY_FILE (default
~/.lambda/api_key), never from an argument or the environment, and never
printed -- error output is the API's response body, which does not echo it.

Usage (from the repo root):
    python scripts/lambda_box.py types                 # A100/H100 capacity by region (free)
    python scripts/lambda_box.py key-add NAME PUBFILE  # register a public key (free)
    python scripts/lambda_box.py launch REGION [--type gpu_1x_a100] [--key NAME]   # BILLABLE
    python scripts/lambda_box.py status [ID]           # instances: status, ip
    python scripts/lambda_box.py wait ID               # poll until active, print ip
    python scripts/lambda_box.py terminate ID          # stop the meter
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://cloud.lambda.ai/api/v1"
DEFAULT_KEY_FILE = Path.home() / ".lambda" / "api_key"
DEFAULT_TYPE = "gpu_1x_a100"     # verify against `types` before launching
DEFAULT_KEY_NAME = "polyforge-b1"
WANTED = ("a100", "h100")        # the runbook's box and its fallback
POLL_SECONDS = 20
WAIT_MAX_SECONDS = 20 * 60


def api_key(path: Path | None = None) -> str:
    path = path or Path(os.environ.get("LAMBDA_API_KEY_FILE", DEFAULT_KEY_FILE))
    try:
        key = path.read_text(encoding="utf-8").strip()
    except OSError:
        raise SystemExit(f"no API key at {path}: paste the key from "
                         "cloud.lambda.ai -> API keys into that file (one line)")
    if not key:
        raise SystemExit(f"{path} is empty")
    return key


def request(method: str, path: str, body: dict | None = None,
            key: str | None = None) -> dict:
    """One API call. Raises SystemExit carrying the API's error body; the key
    never appears in the message."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Authorization": f"Bearer {key or api_key()}",
                 "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:2000]
        raise SystemExit(f"{method} {path} -> HTTP {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise SystemExit(f"{method} {path} -> {e.reason}") from None


def wanted_types(payload: dict) -> list[dict]:
    """Rows for the GPU classes the runbook accepts, cheapest first, with the
    regions that have capacity right now. `payload` is GET /instance-types."""
    rows = []
    for name, item in payload.get("data", {}).items():
        if not any(w in name for w in WANTED):
            continue
        it = item["instance_type"]
        specs = it.get("specs", {})
        rows.append({
            "name": name,
            "usd_per_hour": it["price_cents_per_hour"] / 100,
            "vcpus": specs.get("vcpus"),
            "memory_gib": specs.get("memory_gib"),
            "gpus": specs.get("gpus"),
            "arch": it.get("architecture"),
            "gpu": it.get("gpu_description"),
            "regions": [r["name"] for r in item.get("regions_with_capacity_available", [])],
        })
    return sorted(rows, key=lambda r: r["usd_per_hour"])


def launch_body(region: str, instance_type: str, key_name: str,
                name: str = DEFAULT_KEY_NAME) -> dict:
    return {"region_name": region, "instance_type_name": instance_type,
            "ssh_key_names": [key_name], "name": name}


def instance_row(inst: dict) -> str:
    it = inst.get("instance_type", {}).get("name", "?")
    reg = inst.get("region", {}).get("name", "?")
    return f"{inst['id']}  {inst['status']:<11} {it:<18} {reg:<14} ip={inst.get('ip') or '-'}"


def cmd_types(_a) -> None:
    for r in wanted_types(request("GET", "/instance-types")):
        cap = ", ".join(r["regions"]) or "NO CAPACITY"
        print(f"{r['name']:<24} ${r['usd_per_hour']:.2f}/h  {r['vcpus']} vCPU  "
              f"{r['memory_gib']} GiB  {r['arch']}  {r['gpu']}")
        print(f"{'':24} capacity: {cap}")


def cmd_key_add(a) -> None:
    pub = Path(a.pubfile).read_text(encoding="utf-8").strip()
    out = request("POST", "/ssh-keys", {"name": a.name, "public_key": pub})
    print(f"registered ssh key {out['data']['name']} ({out['data']['id']})")


def cmd_launch(a) -> None:
    body = launch_body(a.region, a.type, a.key)
    print(f"launching {a.type} in {a.region} with key {a.key} -- BILLING STARTS "
          "once the instance is active", flush=True)
    out = request("POST", "/instance-operations/launch", body)
    ids = out["data"]["instance_ids"]
    print("instance id:", *ids)


def cmd_status(a) -> None:
    if a.id:
        print(instance_row(request("GET", f"/instances/{a.id}")["data"]))
        return
    data = request("GET", "/instances")["data"]
    print("\n".join(instance_row(i) for i in data) if data else "no instances")


def cmd_wait(a) -> None:
    deadline = time.time() + WAIT_MAX_SECONDS
    while True:
        inst = request("GET", f"/instances/{a.id}")["data"]
        print(instance_row(inst), flush=True)
        if inst["status"] == "active" and inst.get("ip"):
            print("ip:", inst["ip"])
            return
        if inst["status"] in ("terminated", "terminating", "unhealthy", "preempted"):
            raise SystemExit(f"instance is {inst['status']}")
        if time.time() > deadline:
            raise SystemExit("not active after 20 min; check the console")
        time.sleep(POLL_SECONDS)


def cmd_terminate(a) -> None:
    out = request("POST", "/instance-operations/terminate", {"instance_ids": [a.id]})
    for inst in out["data"]["terminated_instances"]:
        print("terminating:", instance_row(inst))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("types").set_defaults(fn=cmd_types)
    k = sub.add_parser("key-add")
    k.add_argument("name")
    k.add_argument("pubfile")
    k.set_defaults(fn=cmd_key_add)
    l = sub.add_parser("launch")
    l.add_argument("region")
    l.add_argument("--type", default=DEFAULT_TYPE)
    l.add_argument("--key", default=DEFAULT_KEY_NAME)
    l.set_defaults(fn=cmd_launch)
    s = sub.add_parser("status")
    s.add_argument("id", nargs="?")
    s.set_defaults(fn=cmd_status)
    w = sub.add_parser("wait")
    w.add_argument("id")
    w.set_defaults(fn=cmd_wait)
    t = sub.add_parser("terminate")
    t.add_argument("id")
    t.set_defaults(fn=cmd_terminate)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main(sys.argv[1:])
