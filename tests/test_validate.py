from __future__ import annotations

import json

from pipeline.validate import validate_response


def test_validate_ok():
    mp = {"0": "Hello", "1": "World"}
    payload = {
        "0": {"src": "Hello", "tr": "你好"},
        "1": {"src": "World", "tr": "世界"},
    }
    vr = validate_response(json.dumps(payload, ensure_ascii=False), mp)
    assert vr.ok
    assert vr.stats["n_tr_ok"] == 2
    assert vr.parsed["0"]["tr"] == "你好"


def test_validate_missing_key():
    mp = {"0": "Hello", "1": "World"}
    payload = {"0": {"src": "Hello", "tr": "你好"}}
    vr = validate_response(json.dumps(payload), mp)
    assert not vr.ok
    assert any("missing keys" in e for e in vr.errors)


def test_validate_empty_tr():
    mp = {"0": "Hello"}
    payload = {"0": {"src": "Hello", "tr": "  "}}
    vr = validate_response(json.dumps(payload), mp)
    assert not vr.ok


def test_validate_netflix_warnings():
    mp = {"0": "Hello"}
    payload = {"0": {"src": "Hello", "tr": "你好，世界。"}}
    vr = validate_response(json.dumps(payload, ensure_ascii=False), mp)
    assert vr.ok  # warnings only
    assert any("，" in w or "。" in w for w in vr.warnings)


def test_validate_fence():
    mp = {"0": "Hello"}
    body = json.dumps({"0": {"src": "Hello", "tr": "你好"}}, ensure_ascii=False)
    vr = validate_response(f"```json\n{body}\n```", mp)
    assert vr.ok
