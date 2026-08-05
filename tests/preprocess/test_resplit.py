from __future__ import annotations

from pipeline.preprocess.config import PreprocessConfig
from pipeline.preprocess.orchestrate_a import run_preprocess


def test_resplit_long_english(tmp_path):
    long = " ".join(["word"] * 80)
    srt = tmp_path / "long.srt"
    srt.write_text(
        f"1\n00:00:00,000 --> 00:00:10,000\n{long}\n",
        encoding="utf-8",
    )
    r = run_preprocess(
        srt,
        PreprocessConfig(
            work_dir=tmp_path / "w",
            fix_overlaps="off",
            resplit="auto",
            english_char_limit=42,
        ),
    )
    assert r.meta["steps"]["resplit"]["applied"] is True
    assert len(r.cues) >= 2
