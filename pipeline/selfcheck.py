from __future__ import annotations

import json
from pathlib import Path

from model_client import Usage

from pipeline.prompt import build_instructions
from pipeline.srt_io import (
    build_bilingual_srt,
    build_input_json,
    chunk_cues,
    parse_srt,
    reindex_cues,
    slice_cues,
    sum_usage,
)
from pipeline.validate import validate_response


def self_check_offline(srt_path: Path | str) -> None:
    """无 API 的快速自检。"""
    cues = parse_srt(srt_path)
    assert len(cues) > 0, "no cues"
    sliced = slice_cues(cues, max_cues=8)
    assert len(sliced) == 8
    assert sliced[0].id == "0"
    js, mp = build_input_json(sliced)
    assert json.loads(js) == mp
    inst = build_instructions()
    assert "英语" in inst or "${sourceLanguage}" not in inst
    assert "简体中文" in inst or "${targetLanguage}" not in inst
    assert " = " in inst  # glossary lines

    # good fixture
    good = {
        k: {"src": v, "tr": "测试译文"}
        for k, v in mp.items()
    }
    vr = validate_response(json.dumps(good, ensure_ascii=False), mp)
    assert vr.ok, vr.errors

    # fence
    fenced = "```json\n" + json.dumps(good, ensure_ascii=False) + "\n```"
    assert validate_response(fenced, mp).ok

    # missing key
    bad = {k: good[k] for k in list(good)[:-1]}
    vr2 = validate_response(json.dumps(bad), mp)
    assert not vr2.ok

    tr_map = {k: "中文一行" for k in mp}
    srt = build_bilingual_srt(sliced, tr_map)
    assert "中文一行" in srt
    assert sliced[0].text.split("\n")[0] in srt

    # chunking: 747 / 50 → 15 batches (14*50 + 47)
    full = reindex_cues(cues)
    chunks = chunk_cues(full, 50)
    assert len(chunks) == (len(full) + 49) // 50
    assert sum(len(c) for c in chunks) == len(full)
    assert chunks[0][0].id == "0"
    assert chunks[1][0].id == "50"
    assert chunk_cues(full, 0) == [full]
    assert sum_usage([Usage(1, 2, 0, 3), Usage(4, 5, 1, 10)]).total_tokens == 13

    print(
        f"offline self-check OK: total_cues={len(cues)} sample={len(sliced)} "
        f"batches_50={len(chunks)}"
    )
