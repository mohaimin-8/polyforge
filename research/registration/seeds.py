"""WP13: the confirmatory campaign's seeds, from a public random round named in advance (programme 2).

The mechanical part of the plan's procedure (WP13, "Verifiable seeds"):
  1. D = SHA-256 of the frozen protocol file; T0 = when the commit that freezes it is pushed.
  2. The randomness is drand quicknet's first round at or after T0 + 24 h. Its number
     follows from the chain's genesis time and period, read from /{chain-hash}/info.
  3. After that time the round is fetched from two relays. They must return the same
     round, randomness and signature, and the randomness must be SHA-256 of the
     signature (drand's rule).
  4. Master seed = SHA-256(D || randomness); seed of a run = the first 8 bytes of
     SHA-256(master || run identity in UTF-8), read as an unsigned big-endian integer.

No alternate round and no re-roll. A response that breaks drand's rules, or relays that
disagree, stop the draw (exit 2) for a person to look at. Asking before the round is due,
or fewer than two relays answering, means the same round is fetched again later (exit 3).
`draw` never overwrites an earlier draw. The BLS signature itself is not verified here:
the plan asks for two relays that agree.

    python seeds.py digest PROTOCOL.md
    python seeds.py round --t0 2026-11-20T12:00:00Z          # the round the protocol names
    python seeds.py draw --protocol PROTOCOL.md --round N --out draw.json
    python seeds.py run-seed --master HEX "wp14|pop017|joint" ...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

QUICKNET = "52db9ba70e0cc0f6eaf7803dd07447a1f5477735fd3f661792ba94600c84e971"
RELAYS = ("https://api.drand.sh", "https://api2.drand.sh", "https://api3.drand.sh",
          "https://drand.cloudflare.com")
DELAY_S = 24 * 3600
TIMEOUT_S = 20.0
NEED = 2

Fetch = Callable[[str], dict]
UNREACHABLE = (urllib.error.URLError, TimeoutError, OSError, ValueError)   # ValueError: not JSON


class DrawError(RuntimeError):
    """A response breaks drand's rules or the relays disagree: no draw, no re-roll."""


class NotYet(RuntimeError):
    """The round is not due, or too few relays answered: fetch the same round later."""


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "seeds/1", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_time(text: str) -> int:
    """An ISO 8601 time with its UTC offset (or Z), as Unix seconds rounded up."""
    t = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError(f"{text!r} has no UTC offset")
    return math.ceil(t.timestamp())


def utc(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def round_at_or_after(t: int, genesis: int, period: int) -> int:
    """drand's first round emitted at or after Unix time t (round 1 at genesis, then one per period)."""
    if period <= 0:
        raise ValueError("period must be positive")
    return 1 if t <= genesis else -(-(t - genesis) // period) + 1


def round_time(r: int, genesis: int, period: int) -> int:
    if r < 1:
        raise ValueError("drand rounds start at 1")
    return genesis + (r - 1) * period


def chain_info(relays: Sequence[str] = RELAYS, chain: str = QUICKNET, fetch: Fetch | None = None) -> dict:
    """The chain's genesis time and period from the first relay that answers for this chain."""
    fetch = fetch or fetch_json
    failures = []
    for relay in relays:
        try:
            info = fetch(f"{relay}/{chain}/info")
        except UNREACHABLE as exc:
            failures.append(f"{relay}: {exc}")
            continue
        if not isinstance(info, dict) or info.get("hash") != chain:
            raise DrawError(f"{relay} serves chain {info.get('hash')!r}, not {chain}")
        return {"genesis_time": int(info["genesis_time"]), "period": int(info["period"])}
    raise NotYet("no relay answered for the chain's info (" + "; ".join(failures) + ")")


def checked(beacon: dict, r: int, relay: str) -> dict:
    """One relay's answer for round r, held to drand's rules."""
    if not isinstance(beacon, dict):
        raise DrawError(f"{relay} did not answer with a JSON object for round {r}")
    if beacon.get("round") != r:
        raise DrawError(f"{relay} returned round {beacon.get('round')!r} when asked for round {r}")
    sig, rnd = str(beacon.get("signature", "")), str(beacon.get("randomness", ""))
    try:
        ok = hashlib.sha256(bytes.fromhex(sig)).hexdigest() == rnd
    except ValueError:
        ok = False
    if not ok:
        raise DrawError(f"{relay}: round {r}'s randomness is not SHA-256 of its signature")
    return {"round": r, "randomness": rnd, "signature": sig}


def fetch_round(r: int, relays: Sequence[str] = RELAYS, chain: str = QUICKNET, fetch: Fetch | None = None,
                need: int = NEED) -> dict:
    """Round r from the first `need` relays that answer; their answers must be identical."""
    fetch = fetch or fetch_json
    got, failures = [], []
    for relay in relays:
        try:
            beacon = fetch(f"{relay}/{chain}/public/{r}")
        except UNREACHABLE as exc:
            failures.append(f"{relay}: {exc}")
            continue
        got.append((relay, checked(beacon, r, relay)))
        if len(got) == need:
            break
    if len(got) < need:
        raise NotYet(f"round {r}: {len(got)} of {need} relays answered (" + "; ".join(failures) + ")")
    (first_relay, first), rest = got[0], got[1:]
    for relay, answer in rest:
        if answer != first:
            raise DrawError(f"relays disagree on round {r}: {first_relay} and {relay}")
    return {**first, "relays": [relay for relay, _ in got]}


def master_seed(protocol_digest: str, randomness: str) -> str:
    return hashlib.sha256(bytes.fromhex(protocol_digest) + bytes.fromhex(randomness)).hexdigest()


def run_seed(master: str, identity: str) -> int:
    digest = hashlib.sha256(bytes.fromhex(master) + identity.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def draw(protocol: Path, r: int, relays: Sequence[str] = RELAYS, chain: str = QUICKNET,
         fetch: Fetch | None = None, now: float | None = None) -> dict:
    """The master seed from round r, once it is due, with everything needed to check it."""
    info = chain_info(relays, chain, fetch)
    due = round_time(r, info["genesis_time"], info["period"])
    now = time.time() if now is None else now
    if now < due:
        raise NotYet(f"round {r} is due at {utc(due)}; it is {utc(now)}")
    beacon = fetch_round(r, relays, chain, fetch)
    d = file_digest(protocol)
    return {"protocol": protocol.name, "protocol_sha256": d, "chain": chain, "round": r,
            "round_due_utc": utc(due), "randomness": beacon["randomness"], "signature": beacon["signature"],
            "relays": beacon["relays"], "master_seed": master_seed(d, beacon["randomness"]),
            "fetched_utc": utc(now)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("digest", help="SHA-256 of the protocol file (D)")
    p.add_argument("protocol", type=Path)
    p = sub.add_parser("round", help="the first round at or after T0 + 24 h")
    p.add_argument("--t0", required=True, help="ISO 8601 with offset, e.g. 2026-11-20T12:00:00Z")
    p.add_argument("--delay-s", type=int, default=DELAY_S)
    p.add_argument("--relays", nargs="+", default=list(RELAYS))
    p = sub.add_parser("draw", help="fetch the named round from two relays and derive the master seed")
    p.add_argument("--protocol", type=Path, required=True)
    p.add_argument("--round", type=int, required=True)
    p.add_argument("--relays", nargs="+", default=list(RELAYS))
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("run-seed", help="the seed of each named run")
    p.add_argument("--master", required=True)
    p.add_argument("identity", nargs="+")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "digest":
            print(file_digest(args.protocol))
        elif args.cmd == "round":
            info = chain_info(args.relays)
            t = parse_time(args.t0) + args.delay_s
            r = round_at_or_after(t, info["genesis_time"], info["period"])
            print(f"round {r}, due {utc(round_time(r, info['genesis_time'], info['period']))} "
                  f"(T0 + {args.delay_s} s = {utc(t)}; chain {QUICKNET[:12]}..., period {info['period']} s)")
        elif args.cmd == "draw":
            if args.out.exists():
                print(f"{args.out} exists: a draw is made once and never repeated", file=sys.stderr)
                return 2
            result = draw(args.protocol, args.round, args.relays)
            args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            print(f"round {result['round']} from {', '.join(result['relays'])}: master seed {result['master_seed']}")
        else:
            for identity in args.identity:
                print(f"{identity}\t{run_seed(args.master, identity)}")
    except DrawError as exc:
        print(f"draw stopped: {exc}", file=sys.stderr)
        return 2
    except NotYet as exc:
        print(f"not yet: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
