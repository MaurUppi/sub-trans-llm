from __future__ import annotations

from pipeline.models import Cue
from pipeline.subtitle_check import check_subtitle_quality, parse_ts_to_sec


def _cue(cid: str, start: str, end: str, text: str = "src") -> Cue:
    return Cue(id=cid, seq=int(cid) + 1, start=start, end=end, text=text)


def test_parse_ts():
    assert parse_ts_to_sec("00:00:01,500") == 1.5
    assert parse_ts_to_sec("01:02:03,000") == 3723.0


def test_cps_within_limit_is_clean():
    # 2 秒 / 10 字 → 5.0 cps，低于默认 9.0
    cues = [_cue("0", "00:00:00,000", "00:00:02,000")]
    rep = check_subtitle_quality(cues, {"0": "一二三四五六七八九十"})
    assert rep.n_cps_over == 0
    assert rep.per_cue[0].cps == 5.0


def test_cps_over_limit_flagged():
    # 1 秒 / 20 字 → 20.0 cps
    cues = [_cue("0", "00:00:00,000", "00:00:01,000")]
    rep = check_subtitle_quality(cues, {"0": "一二三四五六七八九十一二三四五六七八九十"})
    assert rep.n_cps_over == 1
    assert "cps" in rep.per_cue[0].issues[0]


def test_line_too_long_flagged():
    cues = [_cue("0", "00:00:00,000", "00:00:10,000")]
    long_line = "字" * 25  # > 默认 16
    rep = check_subtitle_quality(cues, {"0": long_line})
    assert rep.n_line_over == 1
    assert rep.per_cue[0].max_line_chars == 25


def test_too_many_lines_flagged():
    cues = [_cue("0", "00:00:00,000", "00:00:10,000")]
    rep = check_subtitle_quality(cues, {"0": "第一行\n第二行\n第三行"})
    assert rep.n_lines_over == 1
    assert rep.per_cue[0].lines == 3


def test_zero_duration_does_not_crash():
    cues = [_cue("0", "00:00:05,000", "00:00:05,000")]
    rep = check_subtitle_quality(cues, {"0": "有字"})
    assert rep.per_cue[0].cps is None
    assert rep.n_bad_duration == 1


def test_report_aggregates_and_serializes():
    cues = [
        _cue("0", "00:00:00,000", "00:00:02,000"),
        _cue("1", "00:00:02,000", "00:00:03,000"),
    ]
    rep = check_subtitle_quality(
        cues, {"0": "短句", "1": "一二三四五六七八九十一二三四五"}
    )
    assert rep.n == 2
    d = rep.to_dict()
    assert d["n"] == 2 and "max_cps" in d and "n_cps_over" in d
    # to_dict 只带聚合值，不把每条都塞进 meta
    assert "per_cue" not in d


def test_missing_translation_counted_not_crashed():
    cues = [_cue("0", "00:00:00,000", "00:00:02,000")]
    rep = check_subtitle_quality(cues, {})
    assert rep.n_missing == 1
