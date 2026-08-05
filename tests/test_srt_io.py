from __future__ import annotations

import json

from pipeline.srt_io import (
    build_bilingual_srt,
    build_input_json,
    chunk_cues,
    parse_srt,
    reindex_cues,
    slice_cues,
    sum_usage,
)
from model_client import Usage
from pipeline.models import Cue


def test_parse_sample_srt(sample_srt):
    cues = parse_srt(sample_srt)
    assert len(cues) == 2
    assert cues[0].id == "1"
    assert cues[0].text == "Hello world"
    assert cues[1].text == "Line A\nLine B"
    assert cues[0].start == "00:00:01,000"


def test_parse_full_episode(srt_path):
    cues = parse_srt(srt_path)
    assert len(cues) == 747
    assert cues[0].text.startswith("CROSSING")


def test_reindex_and_slice(sample_srt):
    cues = parse_srt(sample_srt)
    re = reindex_cues(cues)
    assert re[0].id == "0" and re[1].id == "1"
    sl = slice_cues(cues, max_cues=1)
    assert len(sl) == 1 and sl[0].id == "0"


def test_chunk_cues():
    cues = [Cue(id=str(i), seq=i, start="0", end="1", text=str(i)) for i in range(10)]
    assert len(chunk_cues(cues, 3)) == 4
    assert chunk_cues(cues, 0) == [cues]
    assert chunk_cues([], 5) == []


def test_build_input_json_and_bilingual(sample_srt):
    cues = slice_cues(parse_srt(sample_srt), max_cues=2)
    s, mp = build_input_json(cues)
    assert json.loads(s) == mp
    bi = build_bilingual_srt(cues, {"0": "你好", "1": "第二行"})
    assert "你好" in bi and "Hello world" in bi
    assert bi.strip().startswith("1\n")


def test_sum_usage():
    u = sum_usage([Usage(input_tokens=1, output_tokens=2, total_tokens=3),
                   Usage(input_tokens=4, output_tokens=5, total_tokens=9)])
    assert u.input_tokens == 5 and u.total_tokens == 12
