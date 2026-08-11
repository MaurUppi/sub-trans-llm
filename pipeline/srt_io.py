from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from model_client import Usage

from pipeline.models import Cue


def parse_srt(path: Path | str) -> list[Cue]:
    """解析 SRT；id 暂用序号字符串，切片后由 reindex_cues 重编号。"""
    path = Path(path)
    raw = path.read_bytes()
    while raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text.strip())
    cues: list[Cue] = []
    for block in blocks:
        lines = block.split("\n")
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if len(lines) < 2:
            continue
        seq_line = lines[0].strip()
        time_line = lines[1].strip()
        if not seq_line.isdigit() or "-->" not in time_line:
            continue
        m = re.match(
            r"(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})",
            time_line,
        )
        if not m:
            continue
        start = m.group(1).replace(".", ",")
        end = m.group(2).replace(".", ",")
        body = "\n".join(lines[2:]).strip("\n")
        seq = int(seq_line)
        cues.append(Cue(id=str(seq), seq=seq, start=start, end=end, text=body))
    return cues


def reindex_cues(cues: list[Cue]) -> list[Cue]:
    """切片后使用稳定 id \'0\'..\'n-1\'。"""
    out: list[Cue] = []
    for i, c in enumerate(cues):
        out.append(
            Cue(id=str(i), seq=c.seq, start=c.start, end=c.end, text=c.text)
        )
    return out


def slice_cues(
    cues: list[Cue],
    *,
    cue_offset: int = 0,
    max_cues: Optional[int] = None,
) -> list[Cue]:
    end = None if max_cues is None else cue_offset + max_cues
    sliced = cues[cue_offset:end]
    return reindex_cues(sliced)


def chunk_cues(cues: list[Cue], batch_size: int) -> list[list[Cue]]:
    """
    按批切分。保留各 Cue 已有的全局 id（调用前应对全集 reindex 为 "0".."n-1"）。
    batch_size <= 0 表示单批整包。
    """
    if not cues:
        return []
    if batch_size is None or batch_size <= 0 or batch_size >= len(cues):
        return [cues]
    return [cues[i : i + batch_size] for i in range(0, len(cues), batch_size)]


def sum_usage(parts: list[Usage]) -> Usage:
    u = Usage()
    for p in parts:
        u.input_tokens += p.input_tokens
        u.output_tokens += p.output_tokens
        u.reasoning_tokens += p.reasoning_tokens
        u.total_tokens += p.total_tokens
    return u


def build_input_json(cues: list[Cue]) -> tuple[str, dict[str, str]]:
    mapping = {c.id: c.text for c in cues}
    s = json.dumps(mapping, ensure_ascii=False, separators=(",", ":"))
    return s, mapping


def build_bilingual_srt(
    cues: list[Cue],
    translations: dict[str, str],
) -> str:
    """译文在上、原文在下；原文用本地 Cue.text。"""
    blocks: list[str] = []
    for i, c in enumerate(cues, start=1):
        tr = translations.get(c.id, "").strip()
        src = c.text
        body = f"{tr}\n{src}" if src else tr
        blocks.append(f"{i}\n{c.start} --> {c.end}\n{body}")
    return "\n\n".join(blocks) + "\n"
