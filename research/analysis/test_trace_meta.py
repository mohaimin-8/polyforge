"""The three real-trace replay records rebuild WITHOUT the raw traces.

Audit 2026-09-26: RESULTS_TRACE, RESULTS_TRACE2 and RESULTS_TRACE_AZURE sat
outside the reproduction gate because `--analyze` loaded the raw BurstGPT and
Azure traces -- to print the demand scale k, the segment count and the
per-window stream share. Every statistic comes from the committed run CSVs.
Those three trace-derived inputs are now frozen in trace_replay_meta.json,
cross-checked against the raw traces wherever they are present.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import trace_meta  # noqa: E402

RECORDS = [("trace_matrix", "RESULTS_TRACE.md"),
           ("trace_matrix2", "RESULTS_TRACE2.md"),
           ("trace_matrix_azure", "RESULTS_TRACE_AZURE.md")]


@pytest.mark.parametrize("module, record", RECORDS)
def test_record_rebuilds_byte_for_byte_with_the_raw_traces_hidden(module, record, tmp_path, monkeypatch):
    monkeypatch.setenv("POLYFORGE_ANALYSIS_OUT", str(tmp_path))
    # Reload FIRST so the output path picks up the env var, THEN hide the raw
    # traces: a reload re-executes the module and would restore its TRACE.
    # Hidden under their real file names, as on a clean clone, because the
    # records print the trace's name.
    mod = importlib.reload(importlib.import_module(module))
    import trace_matrix
    import trace_matrix_azure

    hidden = tmp_path / "no-raw-traces"
    for m in (trace_matrix, trace_matrix_azure):
        monkeypatch.setattr(m, "TRACE", hidden / m.TRACE.name)
    assert not trace_meta.raw_available("burstgpt") and not trace_meta.raw_available("azure")
    monkeypatch.setattr(sys, "argv", [module, "--analyze"])
    mod.main()
    got = (tmp_path / record).read_bytes().replace(b"\r\n", b"\n")
    want = (HERE / record).read_bytes().replace(b"\r\n", b"\n")
    assert got == want


def test_committed_meta_matches_the_raw_traces_when_present():
    if not trace_meta.raw_available("burstgpt") or not trace_meta.raw_available("azure"):
        pytest.skip("raw traces not present (clean clone)")
    assert trace_meta.compute("burstgpt") == trace_meta.committed()["burstgpt"]
    assert trace_meta.compute("azure") == trace_meta.committed()["azure"]


def test_a_stale_meta_is_refused_when_the_raw_trace_disagrees(monkeypatch):
    monkeypatch.setattr(trace_meta, "raw_available", lambda section: True)
    monkeypatch.setattr(trace_meta, "compute", lambda section: {"k": 1.0, "segments": []})
    monkeypatch.setattr(trace_meta, "committed", lambda: {"burstgpt": {"k": 2.0, "segments": []}})
    with pytest.raises(RuntimeError, match="stale"):
        trace_meta.load("burstgpt")
