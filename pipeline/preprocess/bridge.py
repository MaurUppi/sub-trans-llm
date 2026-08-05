from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from pipeline.models import Cue
from pipeline.rules.sub_processor import SRTDocument, SRTParser, SubtitleBlock, TimeCode


def parse_to_document(path: Path | str, encoding: str | None = None) -> SRTDocument:
    return SRTParser().parse_file(str(path), encoding=encoding)


def document_to_cues(document: SRTDocument) -> list[Cue]:
    cues: list[Cue] = []
    for i, block in enumerate(document.blocks):
        tc = block.time_code.to_srt_format()
        start, end = [p.strip() for p in tc.split("-->")]
        text = "\n".join(block.lines)
        cues.append(Cue(id=str(i), seq=block.index, start=start, end=end, text=text))
    return cues


def cues_to_srt(cues: list[Cue]) -> str:
    blocks: list[str] = []
    for i, c in enumerate(cues, start=1):
        body = c.text
        blocks.append(f"{i}\n{c.start} --> {c.end}\n{body}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def write_srt(path: Path, cues: list[Cue]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cues_to_srt(cues), encoding="utf-8")
