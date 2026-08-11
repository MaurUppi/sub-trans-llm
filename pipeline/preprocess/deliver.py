from __future__ import annotations

from pathlib import Path
from typing import Optional

from pipeline.models import TranslateResult


def default_zh_path(source_srt: Path | str) -> Path:
    """{stem}_zh.srt next to the source subtitle file."""
    p = Path(source_srt)
    return p.with_name(f"{p.stem}_zh.srt")


def write_zh_srt(
    result: TranslateResult,
    source_srt: Path | str,
    *,
    output: Optional[Path | str] = None,
) -> Optional[Path]:
    """Write final bilingual delivery SRT. Returns path or None if no bilingual."""
    if not result.bilingual_srt:
        return None
    out = Path(output) if output else default_zh_path(source_srt)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result.bilingual_srt, encoding="utf-8")
    return out
