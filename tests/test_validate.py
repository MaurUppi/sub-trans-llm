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


def test_src_misalignment_is_error():
    """键 0 回显了键 1 的原文：ok 必须为 False，交给重试/拆批。"""
    mp = {"0": "The train leaves at dawn.", "1": "We can't stay here."}
    payload = {
        "0": {"src": "We can't stay here.", "tr": "我们不能待在这儿"},
        "1": {"src": "We can't stay here.", "tr": "我们不能待在这儿"},
    }
    vr = validate_response(json.dumps(payload, ensure_ascii=False), mp)
    assert not vr.ok
    assert any("misalign" in e for e in vr.errors)


def test_src_cosmetic_drift_stays_warning():
    mp = {"0": "The train leaves at dawn."}
    payload = {"0": {"src": "The train leaves at down.", "tr": "火车黎明出发"}}
    vr = validate_response(json.dumps(payload, ensure_ascii=False), mp)
    assert vr.ok
    assert any("src drift" in w for w in vr.warnings)


def test_src_punctuation_variant_is_silent():
    """「Marcel ?」→「Marcel?」这类回显走样不该产生警告噪音。"""
    mp = {"0": "Marcel ?"}
    payload = {"0": {"src": "Marcel?", "tr": "马塞尔？"}}
    vr = validate_response(json.dumps(payload, ensure_ascii=False), mp)
    assert vr.ok
    assert not any("src" in w for w in vr.warnings)


def test_src_strict_can_be_disabled():
    mp = {"0": "The train leaves at dawn.", "1": "We can't stay here."}
    payload = {"0": {"src": "We can't stay here.", "tr": "译文"},
               "1": {"src": "We can't stay here.", "tr": "译文"}}
    vr = validate_response(
        json.dumps(payload, ensure_ascii=False), mp, strict_src=False
    )
    assert vr.ok
    assert any("src" in w for w in vr.warnings)
