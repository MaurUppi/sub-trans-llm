from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.prompt import (
    build_instructions,
    build_summary_input,
    build_summary_instructions,
    compact_glossary,
)
from pipeline.models import Cue
from pipeline.srt_io import parse_srt, slice_cues


def test_compact_glossary(glossary_path):
    text = compact_glossary(glossary_path)
    assert "Daniel Larcher" in text
    assert " = " in text


def test_compact_glossary_supports_csv_source_target_note(tmp_path: Path) -> None:
    glossary = tmp_path / "glossary.csv"
    glossary.write_text(
        "\ufeffsource,target,note\n"
        'Daniel Larcher,达尼埃尔·拉尔谢,"市长, 大夫"\n'
        "Villeneuve,维勒纳夫,故事主要发生地\n",
        encoding="utf-8",
    )

    text = compact_glossary(glossary)

    assert "Daniel Larcher = 达尼埃尔·拉尔谢（市长, 大夫）" in text
    assert "Villeneuve = 维勒纳夫（故事主要发生地）" in text


def test_compact_glossary_rejects_csv_without_required_header(tmp_path: Path) -> None:
    glossary = tmp_path / "glossary.csv"
    glossary.write_text("english,chinese\nVilleneuve,维勒纳夫\n", encoding="utf-8")

    with pytest.raises(ValueError, match="source,target,note"):
        compact_glossary(glossary)


def test_build_instructions_replaces_vars(glossary_path):
    inst = build_instructions(
        source_language="英语",
        target_language="简体中文",
        glossary_path=glossary_path,
    )
    assert "英语" in inst or "简体中文" in inst
    assert "${sourceLanguage}" not in inst
    assert "专有名词" in inst


def test_build_summary_input(sample_srt):
    cues = slice_cues(parse_srt(sample_srt), max_cues=2)
    s = build_summary_input(cues)
    assert "0\t" in s and "Hello" in s


def test_build_summary_instructions_follows_source_language() -> None:
    text = build_summary_instructions(source_language="法语")
    assert "当前法语字幕" in text
    assert "一整集" not in text
    assert "${sourceLanguage}" not in text


def test_build_summary_instructions_follows_target_language() -> None:
    text = build_summary_instructions(target_language="日语")
    assert "请用日语输出" in text
    assert "请用简体中文输出" not in text
    assert "${targetLanguage}" not in text


def test_build_summary_instructions_default_uses_cli_languages() -> None:
    text = build_summary_instructions()
    assert "当前英语字幕" in text
    assert "请用简体中文输出" in text
    assert "一整集英文字幕" not in text


def test_build_summary_instructions_is_genre_agnostic_and_hard_capped() -> None:
    text = build_summary_instructions()
    assert "字幕分析助手" in text
    assert "影视字幕分析助手" not in text
    assert "供后续分批翻译时作为语境参考" in text
    assert "不得超过 400 字" in text
    assert "内容概述" in text
    assert "关键说话人与指称" in text
    assert "语气基调与未决信息" in text
    assert "保留原文" in text
    assert "临时译法，待确认" in text
    assert "一句话梗概" not in text
    assert "关键冲突" not in text
    assert "伏笔" not in text


def test_build_summary_instructions_includes_glossary_when_provided(
    glossary_path: Path,
) -> None:
    text = build_summary_instructions(glossary_path=glossary_path)
    assert "## 专有名词（摘要中使用表内译名，不要另造）" in text
    assert "必须遵守，不得另译" not in text
    assert "Daniel Larcher = 达尼埃尔·拉尔谢（市长）" in text
    assert "使用表内译名" in text


def test_build_summary_instructions_omits_glossary_when_absent() -> None:
    text = build_summary_instructions(source_language="英语")
    assert "## 专有名词（摘要中使用表内译名，不要另造）" not in text
    assert "保留原文" in text


def test_build_summary_instructions_reads_prompt_file(tmp_path: Path) -> None:
    prompt = tmp_path / "summary.md"
    prompt.write_text(
        "下面是当前${sourceLanguage}字幕，请用${targetLanguage}输出。\n",
        encoding="utf-8",
    )
    text = build_summary_instructions(
        source_language="德语",
        target_language="韩语",
        prompt_path=prompt,
    )
    assert "当前德语字幕" in text
    assert "请用韩语输出" in text


def test_build_summary_instructions_loads_shipped_summary_prompt() -> None:
    from pipeline.config import DEFAULT_SUMMARY_PROMPT

    shipped = DEFAULT_SUMMARY_PROMPT.read_text(encoding="utf-8")
    text = build_summary_instructions()
    assert "Welcome to China" in shipped
    assert "Welcome to China" in text
    assert "当调用方传入词库" not in text
