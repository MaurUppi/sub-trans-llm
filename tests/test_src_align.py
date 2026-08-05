from __future__ import annotations

from pipeline.src_align import build_src_index, classify_src


def _idx(mp):
    return build_src_index(mp)


def test_exact_match_is_ok():
    mp = {"0": "Hello there", "1": "Goodbye"}
    v = classify_src("0", "Hello there", mp, _idx(mp))
    assert v.kind == "ok"


def test_punctuation_and_case_variants_are_ok():
    """大小写/空白/标点变体归一化后即相等，不该产生任何噪音。"""
    mp = {"0": "Marcel ?", "1": "Goodbye"}
    assert classify_src("0", "Marcel?", mp, _idx(mp)).kind == "ok"
    assert classify_src("0", "  marcel  ?  ", mp, _idx(mp)).kind == "ok"


def test_slight_content_drift_is_drift_not_error():
    """个别词走样（听写/改写）→ 警告级，不该阻断整批。"""
    mp = {"0": "The train leaves at dawn.", "1": "Goodbye"}
    v = classify_src("0", "The train leaves at down.", mp, _idx(mp))
    assert v.kind == "drift"
    assert v.ratio >= 0.85


def test_echoing_another_cue_is_misaligned():
    """最危险的情形：键 0 回显了键 1 的原文 —— 译文极可能整体错位。"""
    mp = {"0": "The train leaves at dawn.", "1": "We can't stay here."}
    v = classify_src("0", "We can't stay here.", mp, _idx(mp))
    assert v.kind == "misaligned"
    assert v.other_id == "1"


def test_unrelated_text_is_mismatch():
    mp = {"0": "The train leaves at dawn.", "1": "We can't stay here."}
    v = classify_src("0", "Completely unrelated sentence about bicycles.", mp, _idx(mp))
    assert v.kind == "mismatch"


def test_duplicate_source_lines_not_flagged():
    """字幕里重复台词很常见，不能因为文本相同就误判错位。"""
    mp = {"0": "Marcel ?", "1": "Marcel ?", "2": "Yes."}
    for kid in ("0", "1"):
        assert classify_src(kid, "Marcel ?", mp, _idx(mp)).kind == "ok"


def test_empty_actual_is_missing():
    mp = {"0": "Hello"}
    assert classify_src("0", "", mp, _idx(mp)).kind == "missing"
    assert classify_src("0", None, mp, _idx(mp)).kind == "missing"
