from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Optional

from pipeline.config import DEFAULT_PROMPT
from pipeline.models import Cue


def _compact_csv_glossary(path: Path) -> str:
    """Load the supported CSV contract: source,target,note."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = [name.strip() for name in (reader.fieldnames or [])]
        required = ["source", "target", "note"]
        if any(name not in fieldnames for name in required):
            raise ValueError(
                f"CSV glossary must use header source,target,note: {path}"
            )

        lines_out: list[str] = []
        seen: set[str] = set()
        for row in reader:
            source = " ".join((row.get("source") or "").split())
            target = " ".join((row.get("target") or "").split())
            note = " ".join((row.get("note") or "").split())
            if not source or not target or source in seen:
                continue
            seen.add(source)
            suffix = f"（{note}）" if note else ""
            lines_out.append(f"{source} = {target}{suffix}")
        return "\n".join(lines_out)


def compact_glossary(glossary_path: Path | str) -> str:
    """Extract a compact source → target glossary from CSV or Markdown."""
    path = Path(glossary_path)
    if not path.is_file():
        return ""
    if path.suffix.lower() == ".csv":
        return _compact_csv_glossary(path)
    lines_out: list[str] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        if re.match(r"^\|[\s\-:|]+\|$", line):
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) < 2:
            continue
        zh, src = parts[0], parts[1]
        if zh in ("中文译名", "中文") or "原名" in src or src in ("法文/英文原名",):
            continue
        if not zh or not src:
            continue
        aliases = re.split(r"[/／]", src)
        for alias in aliases:
            alias = alias.strip()
            alias_clean = re.sub(r"\s*\([^)]*\)\s*", " ", alias).strip()
            if not alias_clean or alias_clean in seen:
                continue
            if re.fullmatch(r"[\u4e00-\u9fff·]+", alias_clean):
                continue
            seen.add(alias_clean)
            lines_out.append(f"{alias_clean} = {zh}")
            if alias != alias_clean and alias not in seen:
                seen.add(alias)
                lines_out.append(f"{alias} = {zh}")
    return "\n".join(lines_out)


def build_instructions(
    prompt_path: Path | str = DEFAULT_PROMPT,
    glossary_path: Optional[Path | str] = None,
    source_language: str = "英语",
    target_language: str = "简体中文",
    episode_summary: Optional[str] = None,
) -> str:
    prompt = Path(prompt_path).read_text(encoding="utf-8")
    prompt = prompt.replace("${sourceLanguage}", source_language)
    prompt = prompt.replace("${targetLanguage}", target_language)
    parts = [prompt.rstrip()]
    if glossary_path:
        g = compact_glossary(glossary_path)
        if g.strip():
            parts.append("\n\n## 专有名词（必须遵守，不得另译）\n" + g)
    if episode_summary and episode_summary.strip():
        parts.append(
            "\n\n## 本集剧情摘要（翻译时请参考语境与人物状态，勿写入输出 JSON）\n"
            + episode_summary.strip()
        )
    return "\n".join(parts).strip() + "\n"


def build_summary_input(cues: list[Cue]) -> str:
    """通读用 input：仅 id + 原文，紧凑，无时间码。"""
    lines = [f"{c.id}\t{c.text.replace(chr(10), ' / ')}" for c in cues]
    return "\n".join(lines)


SUMMARY_INSTRUCTIONS = """你是影视字幕分析助手。下面是一整集英文字幕（每行：id<TAB>原文）。
请用简体中文输出本集「翻译用摘要」，控制在 400 字以内，包含：
1) 一句话梗概
2) 主要人物及其关系/立场（本集内）
3) 关键冲突与情绪走向
4) 翻译时需注意的称谓、潜台词、伏笔或专有名词线索

要求：只输出摘要正文，不要 JSON，不要条目译文，不要 Markdown 标题堆砌。"""
