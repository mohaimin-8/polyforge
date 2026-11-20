"""REPLICATION.md's commands run as written: every script it names exists where the step's `cd`s
lead, and defines every flag and subcommand the command gives it (a renamed flag would otherwise
surface only at site B, hours into the run)."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import pytest

import replication_tolerances as rt   # testbed/validation, on pytest.ini's pythonpath

REPO = Path(__file__).resolve().parents[2]
RUNBOOK = REPO / "testbed" / "REPLICATION.md"
FENCE = re.compile(r"^[ \t]*```[a-z]*\n(.*?)^[ \t]*```", re.S | re.M)   # indented inside list items


def walk(lines: list[str]) -> list[tuple[Path, list[str]]]:
    """(cwd, the script and its arguments) for each `python <file>` in lines, from the root."""
    cwd, found = REPO, []
    for line in lines:
        for part in line.split("&&"):
            try:
                argv = shlex.split(part.strip())
            except ValueError:              # prose in a span, e.g. <the repository's URL>
                continue
            if argv[:1] == ["cd"]:
                cwd = (cwd / argv[1]).resolve()
            elif argv[:1] == ["python"] and len(argv) > 1 and argv[1].endswith(".py"):
                found.append((cwd, argv[1:]))
    return found


def commands(text: str) -> list[tuple[Path, list[str]]]:
    blocks = FENCE.findall(text)
    spans = re.findall(r"`([^`]+)`", FENCE.sub("", text))
    found = [c for block in blocks for c in walk(block.splitlines())]
    return found + [c for span in spans for c in walk([" ".join(span.split())])]


def test_every_command_names_a_script_with_its_flags_and_subcommands():
    found = commands(RUNBOOK.read_text(encoding="utf-8"))
    names = sorted({argv[0] for _, argv in found})
    assert names == ["after_wp11b.py", "bringup.py", "calibrate.py", "cell_schedules.py", "predict.py",
                     "provision.py", "research/cachereplay/run_all.py", "run_cells.py", "score.py",
                     "testbed/validation/replication_tolerances.py", "validate_static.py", "verify_pins.py"]
    assert len(found) == 14
    for cwd, (script, *args) in found:
        path = cwd / script
        assert path.is_file(), f"{script} is not in {cwd.relative_to(REPO)}"
        source = path.read_text(encoding="utf-8")
        for flag in (a.split("=", 1)[0] for a in args if a.startswith("--")):
            assert f'"{flag}"' in source, f"{script} has no {flag}"
        if "add_subparsers" in source:
            assert f'add_parser("{args[0]}"' in source, f"{script} has no subcommand {args[0]}"


def test_the_runbook_quotes_what_the_committed_runs_give():
    """Section 2's values and proposed tolerances, recomputed from runs_v2/ and wp12/pilot.json."""
    text = " ".join(RUNBOOK.read_text(encoding="utf-8").split())
    a, b = rt.part_a(rt.wp11b_pairs()), rt.part_b()
    assert a["cells"] == 20 and b["pairs"] == 8
    for quoted in (f"error {a['error']:.1%}", f"rank correlation {a['rho']:+.2f}",
                   f"error in [{a['error_ci'][0]:.1%}, {a['error_ci'][1]:.1%}]",
                   f"rank correlation in [{a['rho_ci'][0]:+.3f}, {a['rho_ci'][1]:+.3f}]",
                   f"= {b['mean_diff']:+.4f} with sigma_d {b['sigma_d']:.4f}, on a joint mean of "
                   f"{b['joint_mean']:.4f}, that is {b['relative']:+.2%}",
                   f"[{b['pi'][0]:+.2%}, {b['pi'][1]:+.2%}]", f"t with {b['df']} degrees of freedom"):
        assert quoted in text, quoted


def test_part_b_is_a_prediction_interval_on_n_minus_1_degrees_of_freedom(tmp_path):
    pilot = tmp_path / "pilot.json"
    pilot.write_text(json.dumps({"summary": {"low": {"J": {"pairs": 8, "mean_diff": 0.0, "sigma_d": 0.01,
                                                            "joint_mean": 0.1}}}}), encoding="utf-8")
    b = rt.part_b(pilot)
    half = 2.3646242510102993 * 0.01 * 0.5 / 0.1          # t(0.975, 7) * sigma_d * sqrt(2 / 8) / joint mean
    assert b["df"] == 7 and b["pi"] == pytest.approx((-half, half))


def test_a_second_estimates_interval_stretches_each_side_by_root_two():
    assert rt.widened(0.3, 0.2, 0.5) == pytest.approx((0.3 - 0.1 * 2 ** 0.5, 0.3 + 0.2 * 2 ** 0.5))


def test_the_runbook_reader_follows_cd_and_and_chains():
    found = walk(["cd testbed/scripts", "python a.py --x 1", "cd ../validation && python b.py sub --y"])
    assert found == [(REPO / "testbed" / "scripts", ["a.py", "--x", "1"]),
                     (REPO / "testbed" / "validation", ["b.py", "sub", "--y"])]
    assert walk(["python -m pip install x", "python --version", "git add ."]) == []
