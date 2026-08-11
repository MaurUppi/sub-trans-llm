from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.preprocess.config import PreprocessConfig
from pipeline.preprocess.orchestrate_a import run_preprocess
from pipeline.preprocess import vc_optimize_adapter


def test_preprocess_identity_on_clean(sample_srt, tmp_path):
    r = run_preprocess(
        sample_srt,
        PreprocessConfig(work_dir=tmp_path / "w", fix_overlaps="auto", resplit="off"),
    )
    assert r.clean_srt_path is not None and r.clean_srt_path.is_file()
    assert len(r.cues) == 2
    assert r.meta["counts"]["in"] == 2
    assert r.meta["steps"]["fix_overlaps"]["applied"] is False


def test_preprocess_fixes_overlap(tmp_path):
    srt = tmp_path / "overlap.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nHello\n\n"
        "2\n00:00:01,000 --> 00:00:03,000\nWorld\n",
        encoding="utf-8",
    )
    r = run_preprocess(
        srt,
        PreprocessConfig(work_dir=tmp_path / "w", fix_overlaps="auto", resplit="off"),
    )
    assert r.meta["steps"]["fix_overlaps"]["applied"] is True
    assert r.meta["steps"]["fix_overlaps"]["detected"] >= 1
    # SRT timestamps are zero-padded, so lexical order matches chronological order.
    assert r.cues[0].end <= r.cues[1].start
    assert len(r.cues) == 2


def test_requested_optimize_failure_is_not_silently_skipped(
    sample_srt,
    tmp_path,
    monkeypatch,
):
    def fail_optimize(*_args, **_kwargs):
        raise RuntimeError("provider failed")

    monkeypatch.setattr(vc_optimize_adapter, "optimize_document", fail_optimize)

    with pytest.raises(RuntimeError, match="provider failed"):
        run_preprocess(
            sample_srt,
            PreprocessConfig(
                work_dir=tmp_path / "w",
                optimize=True,
                model="qwen3.7-plus",
                resplit="off",
            ),
        )
