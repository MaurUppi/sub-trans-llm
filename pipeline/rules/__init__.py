"""Rule-based SRT processing (vendored from docs/sub_processor.py / srt_handler)."""
from __future__ import annotations

from pipeline.rules.sub_processor import (
    ProcessingConfig,
    SRTDocument,
    SRTParseError,
    SRTParser,
    SRTProcessor,
    SubtitleBlock,
    TimeCode,
)

__all__ = [
    "ProcessingConfig",
    "SRTDocument",
    "SRTParseError",
    "SRTParser",
    "SRTProcessor",
    "SubtitleBlock",
    "TimeCode",
]
