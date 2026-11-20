"""WP13 step 5: the confirmatory campaign in CI, with the named drand round as its only input.

The plan's step 5: run the simulation campaign in CI from the frozen commit C, with the round
number as the only input, and publish the log and the result hashes. What runs is fixed in C by a
manifest, research/registration/campaign.json, written with the protocol:

    {"protocol": "research/registration/PROTOCOL.md",
     "shards": 20,
     "run":   [{"name": "sim", "cwd": "research/registration",
               "command": ["python", "campaign.py", "--master", "{master}",
                           "--shard", "{shard}", "--shards", "{shards}", "--out", "{out}"]}],
     "score": [{"name": "score", "cwd": "research/registration",
               "command": ["python", "score.py", "--results", "{results}", "--out", "{out}"]}]}

`shards` CI jobs (each must stay under GitHub's 6-hour job limit) run every `run` entry for their
shard; `score` runs once, on every shard's output. A component receives the master seed and derives
each run's seed as seeds.run_seed(master, identity), so the identities and seeds are fixed by frozen
code. Placeholders: {master} {round} {shards} {out} in both lists, {shard} in `run`, {results} in
`score`. A leading "python" means this interpreter. Beside a component's own files the driver writes
provenance.json (commit, round, master seed, interpreter, platform) and <name>.log into {out}.

    python confirm.py draw  --round N --t0-from-github --out OUT   # OUT/draw.json: seeds.py's draw, checked
    python confirm.py draw  --round N --t0 T0 --out OUT            # T0 by hand (a rehearsal)
    python confirm.py shards                           # [0, 1, ...] for the CI matrix
    python confirm.py run   --draw OUT/draw.json --shard I --out OUT/shard-I
    python confirm.py score --draw OUT/draw.json --results DIR --out DIR/score   # DIR/shard-0 ...
    python confirm.py seal  DIR                        # DIR/SHA256SUMS, sha256sum's format

Every step refuses (exit 2) to run from a checkout with modified tracked files, or with a draw made
for another protocol or commit. The draw refuses every round but the one the protocol names, the
first due at or after T0 + 24 h, before fetching anything from it: T0 is the creation time of the
commit's first GitHub run, read with gh as facts.py reads it for the registration, so a mistyped
round is refused, and so is a later commit (its first run names a later round). A component that
fails fails the step (exit 1): nothing is retried and nothing is re-rolled. Asking before the round
is due, or GitHub not answering for the runs, exits 3, as seeds.py does: run the draw again later.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import facts  # noqa: E402
import seeds  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "research" / "registration" / "campaign.json"
MAX_SHARDS = 256                       # GitHub's limit on one matrix
SUMS = "SHA256SUMS"
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
ALLOWED = {"run": {"master", "round", "shard", "shards", "out"},
           "score": {"master", "round", "shards", "results", "out"}}


class Refused(RuntimeError):
    """A precondition of the registered procedure does not hold: nothing runs."""


class Failed(RuntimeError):
    """A component exited with an error: the step fails, nothing is retried."""


def inside(repo: Path, rel: object, what: str) -> Path:
    if not isinstance(rel, str) or not rel:
        raise Refused(f"{what} must be a repository-relative path")
    p = (repo / rel).resolve()
    if not p.is_relative_to(repo.resolve()):
        raise Refused(f"{what} {rel!r} is outside the repository")
    return p


def component(entry: object, key: str, repo: Path) -> dict:
    if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not NAME.fullmatch(entry["name"]):
        raise Refused(f"every '{key}' entry needs a name made of letters, digits, '_', '.' or '-'")
    name, cmd = entry["name"], entry.get("command")
    if not isinstance(cmd, list) or not cmd or not all(isinstance(a, str) and a for a in cmd):
        raise Refused(f"{name}: command must be a non-empty list of strings")
    unknown = {m for a in cmd for m in PLACEHOLDER.findall(a)} - ALLOWED[key]
    if unknown:
        raise Refused(f"{name}: placeholders {sorted(unknown)} are not available in '{key}'")
    cwd = inside(repo, entry.get("cwd", "."), f"{name}: cwd")
    if not cwd.is_dir():
        raise Refused(f"{name}: cwd {entry.get('cwd')!r} is not a directory in this commit")
    return {"name": name, "cwd": cwd, "command": cmd}


def load_manifest(path: Path = MANIFEST, repo: Path = REPO) -> dict:
    if not path.is_file():
        raise Refused(f"no manifest at {path}: it is written with the protocol and frozen in the same commit")
    m = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(m, dict):
        raise Refused("the manifest is not a JSON object")
    protocol = inside(repo, m.get("protocol"), "protocol")
    if not protocol.is_file():
        raise Refused(f"the protocol {m['protocol']} is not in this commit")
    shards = m.get("shards")
    if not isinstance(shards, int) or isinstance(shards, bool) or not 1 <= shards <= MAX_SHARDS:
        raise Refused(f"shards must be an integer from 1 to {MAX_SHARDS}")
    steps = {}
    for key in ("run", "score"):
        entries = m.get(key)
        if not isinstance(entries, list) or not entries:
            raise Refused(f"'{key}' must be a non-empty list")
        steps[key] = [component(e, key, repo) for e in entries]
        names = [c["name"] for c in steps[key]]
        if len(set(names)) != len(names):
            raise Refused(f"'{key}' names must be distinct (each names a log file)")
    return {"protocol": protocol, "shards": shards, **steps}


def head_commit(repo: Path = REPO) -> dict:
    """This checkout's commit, its committer time, and whether any tracked file is modified."""
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    return {"commit": git("rev-parse", "HEAD"), "commit_time": int(git("log", "-1", "--format=%ct")),
            "clean": git("status", "--porcelain", "--untracked-files=no") == ""}


def frozen(repo: Path = REPO) -> dict:
    head = head_commit(repo)
    if not head["clean"]:
        raise Refused("tracked files are modified: the campaign runs only from the frozen commit as committed")
    return head


def named_round(t0: str, fetch: seeds.Fetch | None = None) -> int:
    """The round the protocol names (section 7): quicknet's first round due at or after T0 + 24 h."""
    info = seeds.chain_info(fetch=fetch)
    return seeds.round_at_or_after(seeds.parse_time(t0) + seeds.DELAY_S, info["genesis_time"], info["period"])


def do_draw(rnd: int, out: Path, manifest: dict, head: dict, fetch: seeds.Fetch | None = None,
            now: float | None = None, *, t0: str) -> dict:
    """Round rnd's draw, once it is due, if rnd is the round T0 names.

    T0 is GitHub's clock, not the committer's: the commit's own time may run ahead of GitHub's, so
    it is recorded and not checked."""
    if rnd < 1:
        raise Refused("drand rounds start at 1")
    path = out / "draw.json"
    if path.exists():
        raise Refused(f"{path} exists: a draw is made once and never repeated")
    named = named_round(t0, fetch)
    if rnd != named:
        raise Refused(f"round {rnd} is not the named round: T0, the creation time of commit {head['commit'][:12]}'s "
                      f"first GitHub run, is {t0}, which names round {named} (the first due at or after T0 + 24 h)")
    record = seeds.draw(manifest["protocol"], rnd, fetch=fetch, now=now)
    record = {**record, "t0": t0, "commit": head["commit"], "commit_time_utc": seeds.utc(head["commit_time"])}
    out.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def load_draw(path: Path, manifest: dict, head: dict) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    digest = seeds.file_digest(manifest["protocol"])
    if not isinstance(d, dict) or d.get("protocol_sha256") != digest:
        raise Refused("the draw was made for another protocol than this commit's")
    if d.get("commit") != head["commit"]:
        raise Refused(f"the draw was made on commit {d.get('commit')}, this checkout is {head['commit']}")
    try:
        seeds.checked({k: d.get(k) for k in ("round", "randomness", "signature")}, d.get("round"), path.name)
    except seeds.DrawError as exc:
        raise Refused(str(exc)) from exc
    if seeds.master_seed(digest, d["randomness"]) != d.get("master_seed"):
        raise Refused("the draw's master seed does not follow from its protocol and randomness")
    return d


def fresh(out: Path) -> None:
    if out.exists() and any(out.iterdir()):
        raise Refused(f"{out} is not empty: every step writes into a new directory")
    out.mkdir(parents=True, exist_ok=True)


def expand(cmd: Sequence[str], values: dict[str, str]) -> list[str]:
    argv = [PLACEHOLDER.sub(lambda m: values[m.group(1)], a) for a in cmd]
    return [sys.executable, *argv[1:]] if argv[0] == "python" else argv


def run_component(c: dict, values: dict[str, str], logs: Path) -> None:
    """Run one component, its output both to the CI log and to logs/<name>.log."""
    argv = expand(c["command"], values)
    with (logs / f"{c['name']}.log").open("w", encoding="utf-8") as log, \
            subprocess.Popen(argv, cwd=c["cwd"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace") as proc:
        for line in proc.stdout:
            log.write(line)
            sys.stdout.write(line)
            sys.stdout.flush()
        rc = proc.wait()
    if rc != 0:
        raise Failed(f"{c['name']} exited {rc}; see {c['name']}.log")


def cpu_model() -> str:
    """The processor's model name (Linux's /proc/cpuinfo), else what the platform module reports.

    The shards of one campaign can land on different processors, and numpy picks its SIMD code per
    processor, so a float's last bits can differ between shards: this names each shard's machine."""
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def provenance(out: Path, head: dict, draw: dict, **extra: object) -> None:
    record = {"commit": head["commit"], "round": draw["round"], "master_seed": draw["master_seed"],
              "python": platform.python_version(), "platform": platform.platform(), "cpu": cpu_model(), **extra}
    (out / "provenance.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def common(draw: dict, manifest: dict, out: Path) -> dict[str, str]:
    return {"master": draw["master_seed"], "round": str(draw["round"]), "shards": str(manifest["shards"]),
            "out": str(out.resolve())}


def do_run(draw_path: Path, shard: int, out: Path, manifest: dict, head: dict) -> None:
    if not 0 <= shard < manifest["shards"]:
        raise Refused(f"shard {shard} is not one of 0..{manifest['shards'] - 1}")
    draw = load_draw(draw_path, manifest, head)
    fresh(out)
    provenance(out, head, draw, shard=shard, shards=manifest["shards"])
    for c in manifest["run"]:
        run_component(c, {**common(draw, manifest, out), "shard": str(shard)}, out)


def do_score(draw_path: Path, results: Path, out: Path, manifest: dict, head: dict) -> None:
    draw = load_draw(draw_path, manifest, head)
    for i in range(manifest["shards"]):
        prov = results / f"shard-{i}" / "provenance.json"
        if not prov.is_file():
            raise Refused(f"shard {i} has no output in {results}: a partial campaign is not scored")
        p = json.loads(prov.read_text(encoding="utf-8"))
        if (p.get("commit"), p.get("master_seed"), p.get("shard")) != (head["commit"], draw["master_seed"], i):
            raise Refused(f"shard {i} ran on another commit, seed or shard number")
    fresh(out)
    provenance(out, head, draw, step="score")
    for c in manifest["score"]:
        run_component(c, {**common(draw, manifest, out), "results": str(results.resolve())}, out)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def seal(root: Path) -> list[str]:
    """SHA-256 of every file under root, in sha256sum's format, written once to root/SHA256SUMS."""
    sums = root / SUMS
    if sums.exists():
        raise Refused(f"{sums} exists: the results are sealed once")
    files = sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.relative_to(root).as_posix())
    if not files:
        raise Refused(f"{root} has no files to seal")
    lines = [f"{sha256_file(p)}  {p.relative_to(root).as_posix()}" for p in files]
    sums.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")   # LF everywhere: sha256sum -c
    return lines


def t0_arg(text: str) -> str:
    """--t0 as written, once it reads as ISO 8601 with its UTC offset."""
    try:
        seeds.parse_time(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{exc}; write T0 in UTC with Z, e.g. 2026-11-21T08:00:03Z") from None
    return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("draw", help="fetch the named round and derive the master seed")
    p.add_argument("--round", type=int, required=True)
    p.add_argument("--out", type=Path, required=True)
    t0 = p.add_mutually_exclusive_group(required=True)
    t0.add_argument("--t0-from-github", action="store_true",
                    help="T0 from GitHub's record of this commit's runs (gh run list), as in CI")
    t0.add_argument("--t0", type=t0_arg, help="T0 by hand, ISO 8601 in UTC with Z (a rehearsal)")
    sub.add_parser("shards", help="the shard numbers, as JSON, for the CI matrix")
    p = sub.add_parser("run", help="run one shard of the campaign")
    p.add_argument("--draw", type=Path, required=True)
    p.add_argument("--shard", type=int, required=True)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("score", help="score every shard's output with the frozen scorer")
    p.add_argument("--draw", type=Path, required=True)
    p.add_argument("--results", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("seal", help="write SHA256SUMS over every file in a directory")
    p.add_argument("root", type=Path)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "seal":
            print("\n".join(seal(args.root)))
            return 0
        manifest = load_manifest(MANIFEST, REPO)
        if args.cmd == "shards":
            print(json.dumps(list(range(manifest["shards"]))))
            return 0
        head = frozen(REPO)
        if args.cmd == "draw":
            t0 = args.t0 or facts.first_run(head["commit"], REPO, subprocess.run)
            d = do_draw(args.round, args.out, manifest, head, t0=t0)
            print(f"round {d['round']} (due {d['round_due_utc']}, named by T0 {d['t0']}) from "
                  f"{', '.join(d['relays'])}; commit {d['commit'][:12]}; master seed {d['master_seed']}")
        elif args.cmd == "run":
            do_run(args.draw, args.shard, args.out, manifest, head)
        else:
            do_score(args.draw, args.results, args.out, manifest, head)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except seeds.DrawError as exc:
        print(f"draw stopped: {exc}", file=sys.stderr)
        return 2
    except seeds.NotYet as exc:
        print(f"not yet: {exc}", file=sys.stderr)
        return 3
    except facts.NoT0 as exc:
        print(f"not yet: {exc}; run the draw again (in CI, re-run the failed jobs: the draw job reads the "
              "runs with the workflow's token, which needs actions: read)", file=sys.stderr)
        return 3
    except Failed as exc:
        print(f"failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
