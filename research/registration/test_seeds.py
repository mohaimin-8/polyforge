"""WP13 seeds: round arithmetic, drand's rules, relay agreement, the derivation's byte layout."""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import seeds  # noqa: E402

GENESIS, PERIOD = 1692803367, 3          # quicknet's /info, read 2026-11-16 from four relays
ROUND_1000 = {"round": 1000,             # quicknet round 1000, identical on all four relays
              "randomness": "fe290beca10872ef2fb164d2aa4442de4566183ec51c56ff3cd603d930e54fdd",
              "signature": "b44679b9a59af2ec876b1a6b1ad52ea9b1615fc3982b19576350f93447cb1125"
                           "e342b73a8dd2bacbe47e4b6b63ed5e39"}
INFO = {"hash": seeds.QUICKNET, "genesis_time": GENESIS, "period": PERIOD}
RELAYS = ("https://r1", "https://r2", "https://r3")


def fake(answers: dict[str, object]):
    """A fetch that answers per relay: a dict, or an exception to raise."""
    def fetch(url: str) -> dict:
        relay = url.split("/" + seeds.QUICKNET)[0]
        a = answers[relay]
        if isinstance(a, Exception):
            raise a
        return INFO if url.endswith("/info") else a
    return fetch


def test_round_arithmetic_matches_drand():
    assert seeds.round_at_or_after(GENESIS - 5, GENESIS, PERIOD) == 1
    assert seeds.round_at_or_after(GENESIS, GENESIS, PERIOD) == 1
    assert seeds.round_at_or_after(GENESIS + 1, GENESIS, PERIOD) == 2
    assert seeds.round_at_or_after(GENESIS + 3, GENESIS, PERIOD) == 2      # round 2 is emitted at genesis + 3
    assert seeds.round_at_or_after(GENESIS + 4, GENESIS, PERIOD) == 3
    t = seeds.round_time(1000, GENESIS, PERIOD)
    assert t == GENESIS + 2997 and seeds.round_at_or_after(t, GENESIS, PERIOD) == 1000


def test_the_named_round_is_the_first_at_or_after_t0_plus_a_day():
    t0 = seeds.parse_time("2026-11-20T12:00:00Z")
    assert t0 == 1795176000 == seeds.parse_time("2026-11-20T13:00:00+01:00")
    r = seeds.round_at_or_after(t0 + seeds.DELAY_S, GENESIS, PERIOD)
    assert r == 34153012 and seeds.round_time(r, GENESIS, PERIOD) == t0 + seeds.DELAY_S
    assert seeds.parse_time("2026-11-20T12:00:00.2Z") == t0 + 1             # rounded up, never early
    with pytest.raises(ValueError):
        seeds.parse_time("2026-11-20T12:00:00")                              # no offset: ambiguous


def test_a_real_round_obeys_drands_rule():
    assert seeds.checked(dict(ROUND_1000), 1000, "x") == ROUND_1000


@pytest.mark.parametrize("change", [{"round": 999}, {"randomness": "00" * 32}, {"signature": "zz"}])
def test_a_round_that_breaks_the_rules_stops_the_draw(change):
    with pytest.raises(seeds.DrawError):
        seeds.checked({**ROUND_1000, **change}, 1000, "x")


def test_two_agreeing_relays_give_the_round():
    got = seeds.fetch_round(1000, RELAYS, fetch=fake({r: ROUND_1000 for r in RELAYS}))
    assert got["randomness"] == ROUND_1000["randomness"] and got["relays"] == ["https://r1", "https://r2"]


def test_an_unreachable_relay_is_skipped_not_rerolled():
    answers = {"https://r1": urllib.error.URLError("down"), "https://r2": ROUND_1000, "https://r3": ROUND_1000}
    assert seeds.fetch_round(1000, RELAYS, fetch=fake(answers))["relays"] == ["https://r2", "https://r3"]


def test_one_answer_is_not_enough():
    answers = {"https://r1": urllib.error.URLError("down"), "https://r2": TimeoutError(), "https://r3": ROUND_1000}
    with pytest.raises(seeds.NotYet):
        seeds.fetch_round(1000, RELAYS, fetch=fake(answers))


def test_relays_that_disagree_stop_the_draw():
    sig = "11" * 48
    other = {"round": 1000, "signature": sig, "randomness": hashlib.sha256(bytes.fromhex(sig)).hexdigest()}
    with pytest.raises(seeds.DrawError):
        seeds.fetch_round(1000, RELAYS, fetch=fake({"https://r1": ROUND_1000, "https://r2": other,
                                                    "https://r3": ROUND_1000}))


def test_a_relay_on_another_chain_is_refused():
    def fetch(url: str) -> dict:
        return {**INFO, "hash": "00" * 32}
    with pytest.raises(seeds.DrawError):
        seeds.chain_info(RELAYS, fetch=fetch)


def test_the_derivation_has_the_plans_byte_layout(tmp_path):
    protocol = tmp_path / "PROTOCOL.md"
    protocol.write_bytes(b"protocol v1\n")
    d = seeds.file_digest(protocol)
    assert d == "67b31188d06dbdd0170a9f9b49ce7da925db6510216323bebc2321f4d8cd429d"
    master = seeds.master_seed(d, ROUND_1000["randomness"])
    assert master == hashlib.sha256(bytes.fromhex(d) + bytes.fromhex(ROUND_1000["randomness"])).hexdigest()
    assert master == "8d5e855bca040a95993c79d2a6a6a500e6101f680f55e9be1892cd147313ab9d"
    s = seeds.run_seed(master, "wp14|pop017|joint")
    assert s == 17957517787252599942 and 0 <= s < 2 ** 64
    assert s == int.from_bytes(hashlib.sha256(bytes.fromhex(master) + b"wp14|pop017|joint").digest()[:8], "big")
    assert seeds.run_seed(master, "wp14|pop017|per_knob") != s


def test_draw_waits_for_the_round_and_records_everything(tmp_path):
    protocol = tmp_path / "PROTOCOL.md"
    protocol.write_bytes(b"protocol v1\n")
    fetch = fake({r: ROUND_1000 for r in RELAYS})
    due = seeds.round_time(1000, GENESIS, PERIOD)
    with pytest.raises(seeds.NotYet):
        seeds.draw(protocol, 1000, RELAYS, fetch=fetch, now=due - 1)
    out = seeds.draw(protocol, 1000, RELAYS, fetch=fetch, now=due)
    assert out["master_seed"] == "8d5e855bca040a95993c79d2a6a6a500e6101f680f55e9be1892cd147313ab9d"
    assert out["round"] == 1000 and out["signature"] == ROUND_1000["signature"] and len(out["relays"]) == 2


def test_the_cli_never_overwrites_a_draw(tmp_path, monkeypatch, capsys):
    protocol, out = tmp_path / "PROTOCOL.md", tmp_path / "draw.json"
    protocol.write_bytes(b"protocol v1\n")
    monkeypatch.setattr(seeds, "fetch_json", fake({r: ROUND_1000 for r in RELAYS}))
    argv = ["draw", "--protocol", str(protocol), "--round", "1000", "--out", str(out), "--relays", *RELAYS]
    assert seeds.main(argv) == 0               # round 1000 is long past
    first = out.read_text(encoding="utf-8")
    assert seeds.main(argv) == 2 and out.read_text(encoding="utf-8") == first
    assert "never repeated" in capsys.readouterr().err


T0_1000 = seeds.utc(seeds.round_time(1000, GENESIS, PERIOD) - seeds.DELAY_S)   # names round 1000


def test_the_named_round_comes_from_the_chains_info():
    assert seeds.named_round(T0_1000, RELAYS, fetch=fake({r: ROUND_1000 for r in RELAYS})) == 1000
    later = seeds.utc(seeds.parse_time(T0_1000) + 1)
    assert seeds.named_round(later, RELAYS, fetch=fake({r: ROUND_1000 for r in RELAYS})) == 1001


@pytest.mark.parametrize("rnd", ["999", "1001"])
def test_the_cli_draw_with_t0_refuses_another_round_before_fetching_it(tmp_path, monkeypatch, capsys, rnd):
    protocol, out = tmp_path / "PROTOCOL.md", tmp_path / "draw.json"
    protocol.write_bytes(b"protocol v1\n")
    asked, answer = [], fake({r: ROUND_1000 for r in RELAYS})

    def fetch(url: str) -> dict:
        asked.append(url)
        return answer(url)
    monkeypatch.setattr(seeds, "fetch_json", fetch)
    argv = ["draw", "--protocol", str(protocol), "--round", rnd, "--t0", T0_1000, "--out", str(out), "--relays", *RELAYS]
    assert seeds.main(argv) == 2 and "names round 1000" in capsys.readouterr().err
    assert not out.exists() and asked and all(url.endswith("/info") for url in asked)


def test_the_cli_draw_with_t0_records_it(tmp_path, monkeypatch):
    protocol, out = tmp_path / "PROTOCOL.md", tmp_path / "draw.json"
    protocol.write_bytes(b"protocol v1\n")
    monkeypatch.setattr(seeds, "fetch_json", fake({r: ROUND_1000 for r in RELAYS}))
    argv = ["draw", "--protocol", str(protocol), "--round", "1000", "--t0", T0_1000, "--out", str(out), "--relays", *RELAYS]
    assert seeds.main(argv) == 0
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["t0"] == T0_1000 and d["master_seed"] == "8d5e855bca040a95993c79d2a6a6a500e6101f680f55e9be1892cd147313ab9d"


@pytest.mark.parametrize("argv", [["round", "--t0", "2026-11-20T12:00:00"],
                                  ["run-seed", "--master", "not-hex", "wp14|x"]])
def test_the_cli_refuses_a_t0_without_offset_and_a_master_that_is_not_hex(argv, capsys, monkeypatch):
    monkeypatch.setattr(seeds, "fetch_json", fake({r: ROUND_1000 for r in RELAYS}))
    assert seeds.main([*argv, *(["--relays", *RELAYS] if argv[0] == "round" else [])]) == 2
    assert capsys.readouterr().err.startswith("refused: ")
