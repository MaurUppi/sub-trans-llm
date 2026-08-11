from __future__ import annotations

from pipeline.preprocess.config import ForceMode
from pipeline.preprocess.types import OverlapStats
from pipeline.rules.sub_processor import SRTDocument


def detect_overlaps(document: SRTDocument, *, min_overlap_ms: float = 50.0) -> OverlapStats:
    """Count consecutive cue time overlaps (YouTube rolling window etc.)."""
    blocks = document.blocks
    pair_count = max(0, len(blocks) - 1)
    overlap_count = 0
    max_overlap_ms = 0.0
    for i in range(pair_count):
        end = blocks[i].time_code.end
        start = blocks[i + 1].time_code.start
        if end > start:
            ms = (end - start).total_seconds() * 1000.0
            if ms >= min_overlap_ms:
                overlap_count += 1
                if ms > max_overlap_ms:
                    max_overlap_ms = ms
    return OverlapStats(
        pair_count=pair_count,
        overlap_count=overlap_count,
        max_overlap_ms=max_overlap_ms,
    )


def should_fix_overlaps(stats: OverlapStats, *, mode: ForceMode) -> bool:
    if mode == "on":
        return True
    if mode == "off":
        return False
    return stats.overlap_count > 0


def needs_resplit_rules(document: SRTDocument, *, char_limit: int = 42, max_lines: int = 2) -> bool:
    """Heuristic: any block with >max_lines or a line longer than char_limit."""
    for block in document.blocks:
        non_empty = [ln for ln in block.lines if ln.strip()]
        if len(non_empty) > max_lines:
            return True
        for ln in non_empty:
            if len(ln) > char_limit:
                return True
    return False
