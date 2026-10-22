"""fig22 adds no reading: every chain row whose campaign has a frozen scorer
reproduces that scorer's registered difference exactly, on the committed
exports, and the two pre-audit rows reproduce REANALYSIS_MATRIX."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _mod(name: str):
    spec = importlib.util.spec_from_file_location(f"t_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def chain():
    fc = _mod("fig_adjudication_chain")
    data = fc.frames()
    return fc, data, fc.rows(data)


def test_scored_rows_match_their_records(chain):
    fc, data, rs = chain
    by = {(r["frame"], r["a"], r["b"]): r for r in rs}
    checked = set()

    def check(frame, treatment, comp, mean, hid):
        key = (frame, treatment, comp)
        if key in by:                              # a scorer's hypothesis the chain does not chart
            assert by[key]["dJ"] == mean, hid
            checked.add(key)

    fj, rt = _mod("analysis_fair_j"), _mod("analysis_retuned")
    fres = fj.score(data["fair_j"], {**fj.PRIMARY})
    for hid, comp in fj.PRIMARY.items():
        check("fair_j", fj.TREATMENT, comp, fres[hid]["mean"], hid)
    rres = rt.score(data["retuned"], rt.PRIMARY)
    for hid, comp in rt.PRIMARY.items():
        check("retuned", rt.TREATMENT, comp, rres[hid]["mean"], hid)
    for name, frame in (("analysis_jointness", "jointness"), ("analysis_actuation", "actuation"),
                        ("analysis_heldout", "heldout")):
        m = _mod(name)
        res = m.score(data[frame], m.CONFIRMATORY)
        for hid, comp in m.CONFIRMATORY.items():
            check(frame, m.TREATMENT, comp, res[hid]["mean"], (name, hid))
    q = _mod("analysis_quality")
    qres = q.score(data["quality"])
    for hid, comp in q.CONFIRMATORY.items():
        check("quality", q.TREATMENT, comp, qres[hid]["mean"], hid)
    scored = {k for k, r in by.items() if r["source"] != "WIDTH" and not r["source"].startswith("REANALYSIS")}
    assert checked == scored and len(scored) == 13


def test_width_rows_match_the_width_scorer(chain):
    fc, data, rs = chain
    aw = _mod("analysis_width")
    for plant in aw.PLANTS:
        g = aw.gains(data[f"width_{plant}"])
        narrow = next(r for r in rs if r["frame"] == f"width_{plant}" and r["a"] == aw.NARROW_JOINT)
        wide = next(r for r in rs if r["frame"] == f"width_{plant}" and r["a"] == aw.WIDE_JOINT)
        assert narrow["dJ"] == pytest.approx(-float(g.g_narrow.mean()), abs=1e-12)
        assert wide["dJ"] == pytest.approx(-float(g.g_wide.mean()), abs=1e-12)


def test_verdict_reads_the_ci():
    fc = _mod("fig_adjudication_chain")
    assert fc.verdict({"lo": -3.0, "hi": -0.1}) == "joint better"
    assert fc.verdict({"lo": 0.2, "hi": 4.0}) == "comparator better"
    assert fc.verdict({"lo": -1.0, "hi": 1.0}) == "CI spans 0"


def test_regimes_are_flagged_and_the_caption_quotes_each_rows_ci(chain):
    fc, data, rs = chain
    assert {(r["frame"], r["a"], r["b"]) for r in rs if r["regime"]} == fc.REGIMES
    cap = fc.caption_for(rs)
    for key in (("actuation", "jcac_converged", "hpa_fair_capped"),
                ("heldout", "jcac_converged", "layered_small_tuned"),
                ("heldout", "jcac_converged", "jcac_decoupled")):
        r = next(x for x in rs if (x["frame"], x["a"], x["b"]) == key)
        assert f"{r['pct']:+.1f}% ({fc.verdict(r)})" in cap, key


def test_pre_audit_rows_reproduce_reanalysis_matrix(chain):
    fc, data, rs = chain
    rm = _mod("reanalysis_matrix")
    for frame, a, b in (("headline", "jcac", "hpa"), ("eviction", "jcac", "hpa_fair")):
        r = next(x for x in rs if (x["frame"], x["a"], x["b"]) == (frame, a, b))
        assert r["dJ"] == pytest.approx(rm.compare(data[frame], a, b, "J")["mean_diff"], abs=1e-12)
