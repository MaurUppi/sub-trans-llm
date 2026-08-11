from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.prompt import build_instructions, build_summary_input, compact_glossary
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
