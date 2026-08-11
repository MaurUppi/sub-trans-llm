from __future__ import annotations

import json
from pathlib import Path

import model_client

from pipeline.repair import repair_run_dir


def _write_source(path: Path) -> None:
    path.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nFirst\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nSecond\n\n"
        "3\n00:00:05,000 --> 00:00:06,000\nThird\n",
        encoding="utf-8",
    )


def test_repair_preserves_timeline_for_a_cue_offset_run(tmp_path: Path) -> None:
    source = tmp_path / "source.srt"
    _write_source(source)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "input.json").write_text(
        json.dumps({"0": "Second", "1": "Third"}), encoding="utf-8"
    )
    (run_dir / "instructions.txt").write_text("Translate", encoding="utf-8")
    (run_dir / "parsed.json").write_text(
        json.dumps(
            {
                "0": {"src": "Second", "tr": "第二"},
                "1": {"src": "Third", "tr": "第三"},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "meta.json").write_text(
        json.dumps(
            {
                "batch_size": 50,
                "cue_offset": 1,
                "max_cues": 2,
                "api_mode": "chat_completions",
            }
        ),
        encoding="utf-8",
    )

    result = repair_run_dir(
        run_dir=run_dir,
        srt_path=source,
        model="qwen3.7-plus",
        batch_indices=[],
        temperature=0.7,
        top_p=model_client.OMIT,
    )

    assert result.ok is True
    assert result.cue_offset == 1
    assert result.max_cues == 2
    assert result.sampling == {
        "temperature": {"sent": True, "value": 0.7},
        "top_p": {"sent": False, "value": None},
    }
    assert "00:00:03,000 --> 00:00:04,000\n第二\nSecond" in result.bilingual_srt
    assert "00:00:05,000 --> 00:00:06,000\n第三\nThird" in result.bilingual_srt
    assert "First" not in result.bilingual_srt
