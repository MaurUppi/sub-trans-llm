from __future__ import annotations

from datetime import timedelta

from pipeline.preprocess.detect import detect_overlaps, should_fix_overlaps
from pipeline.rules.sub_processor import SRTDocument, SubtitleBlock, TimeCode


def _blk(i: int, start_s: float, end_s: float, text: str = "hi") -> SubtitleBlock:
    return SubtitleBlock(
        index=i,
        time_code=TimeCode(
            start=timedelta(seconds=start_s),
            end=timedelta(seconds=end_s),
        ),
        lines=[text],
    )


def test_detect_no_overlap():
    doc = SRTDocument(blocks=[_blk(1, 0, 1), _blk(2, 1.1, 2)])
    stats = detect_overlaps(doc)
    assert stats.overlap_count == 0
    assert should_fix_overlaps(stats, mode="auto") is False


def test_detect_overlap():
    doc = SRTDocument(blocks=[_blk(1, 0, 2.0), _blk(2, 1.0, 3.0)])
    stats = detect_overlaps(doc)
    assert stats.overlap_count == 1
    assert should_fix_overlaps(stats, mode="auto") is True


def test_force_modes():
    doc = SRTDocument(blocks=[_blk(1, 0, 1), _blk(2, 1.1, 2)])
    stats = detect_overlaps(doc)
    assert should_fix_overlaps(stats, mode="on") is True
    assert should_fix_overlaps(stats, mode="off") is False
