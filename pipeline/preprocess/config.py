from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

# auto | on | off
ForceMode = Literal["auto", "on", "off"]


@dataclass
class PreprocessConfig:
    """Stage A switches — no named profiles."""

    fix_overlaps: ForceMode = "auto"
    remove_sdh: bool = False
    remove_disfluency: bool = False
    optimize: bool = False
    resplit: ForceMode = "auto"
    words_path: Optional[Path] = None
    model: Optional[str] = None  # for VC LLM steps
    work_dir: Optional[Path] = None
    # rules thresholds
    overlap_min_ms: float = 50.0
    english_char_limit: int = 42
    max_lines: int = 2
