"""Streams: first-turn extraction, key text, classes, tenant split, vector cache."""

from __future__ import annotations

import zlib

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import streams
from engine import doc_id


def write_lmsys(path, conversations, languages):
    msg = pa.struct([("content", pa.string()), ("role", pa.string())])
    table = pa.table({"conversation": pa.array(conversations, type=pa.list_(msg)),
                      "language": pa.array(languages, type=pa.string())})
    pq.write_table(table, path)


class FakeEncoder:
    calls = 0

    def encode(self, texts):
        FakeEncoder.calls += 1
        rng = [np.random.default_rng(zlib.crc32(t.encode())).normal(size=8) for t in texts]
        return streams.go_normalize(np.array(rng, dtype=np.float32))


def test_key_text_formats():
    assert streams.key_text("hi", "gateway") == "user: hi\n"
    assert streams.key_text("hi", "raw") == "hi"
    with pytest.raises(ValueError):
        streams.key_text("hi", "other")


@pytest.mark.parametrize("text,code", [
    ("write a python function that sorts a list", True), ("```js\nlet x = 1\n```", True),
    ("SELECT name FROM users WHERE id = 1", True), ("def f(x):\n    return x", True),
    ("what is the capital of France?", False), ("I will select a dress from the shop", False),
    ("Tell me a story about a dragon", False)])
def test_code_rule(text, code):
    assert streams.is_code(text) is code


def test_first_turn_rules():
    u, a, s = ({"role": r, "content": c} for r, c in (("user", "q"), ("assistant", "ans"), ("system", "sys")))
    assert streams._first_turn([s, u, a]) == ("q", "ans")
    assert streams._first_turn([u, u]) == ("q", "")
    assert streams._first_turn([{"role": "user", "content": ""}, a]) is None
    assert streams._first_turn([a]) is None


def test_first_turns_metadata_has_lengths_not_text(tmp_path):
    path = tmp_path / "f.parquet"
    conv = [[{"content": "héllo", "role": "user"}, {"content": "ok", "role": "assistant"}],
            [{"content": "", "role": "user"}],
            [{"content": "print(1) in python?", "role": "user"}]]
    write_lmsys(path, conv, ["English", "English", "Portuguese"])
    meta, texts = streams.first_turns(str(path), "gateway")
    assert texts == ["user: héllo\n", "user: print(1) in python?\n"]
    assert meta.row.tolist() == [0, 2]
    assert meta.prompt_bytes.tolist() == [6, 19] and meta.key_bytes.tolist() == [13, 26]
    assert meta.completion_bytes.tolist() == [2, 0] and meta.code.tolist() == [False, True]
    assert meta.doc.tolist() == [doc_id(t) for t in texts]
    assert not any(c in meta.columns for c in ("text", "content", "prompt"))


def test_tenant_streams_are_contiguous_chunks_and_classes_partition():
    mask = np.array([1, 0, 1, 1, 1, 0, 1, 1], dtype=bool)
    chunks = streams.tenant_streams(mask, 2)
    assert [c.tolist() for c in chunks] == [[0, 2], [3, 4], [6, 7]]
    assert streams.tenant_streams(mask, 4)[0].tolist() == [0, 2, 3, 4] and len(streams.tenant_streams(mask, 4)) == 1
    import pandas as pd
    meta = pd.DataFrame({"code": [True, False, False], "language": ["English", "Russian", "English"]})
    m = streams.class_masks(meta)
    assert (m["code"] ^ m["conversation"]).all() and (m["english"] ^ m["other_language"]).all()


def test_vector_cache_roundtrip_and_limit_across_files(tmp_path, monkeypatch):
    monkeypatch.setattr(streams, "DATA", tmp_path / "data")
    src = tmp_path / "src"
    src.mkdir()
    for i in range(2):
        conv = [[{"content": f"q{i}-{j}", "role": "user"}] for j in range(3)]
        write_lmsys(src / f"train-0000{i}.parquet", conv, ["English"] * 3)
    pattern = str(src / "*.parquet")
    FakeEncoder.calls = 0
    first = streams.load_stream("gateway", 4, FakeEncoder, pattern)
    assert len(first.meta) == 4 and first.embedded == 4 and FakeEncoder.calls == 2
    assert first.meta.file.tolist() == ["train-00000"] * 3 + ["train-00001"]
    again = streams.load_stream("gateway", 4, FakeEncoder, pattern)
    assert again.embedded == 0 and FakeEncoder.calls == 2
    np.testing.assert_array_equal(first.vectors, again.vectors)
