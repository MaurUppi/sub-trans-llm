from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from pipeline.models import Cue


@dataclass
class OverlapStats:
    pair_count: int
    overlap_count: int
    max_overlap_ms: float

    @property
    def overlap_ratio(self) -> float:
        if self.pair_count <= 0:
            return 0.0
        return self.overlap_count / self.pair_count


@dataclass
class PreprocessResult:
    cues: list[Cue]
    clean_srt_path: Optional[Path]
    source_srt_path: Path
    meta: dict[str, Any] = field(default_factory=dict)
    report: dict[str, Any] = field(default_factory=dict)
