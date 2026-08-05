from __future__ import annotations

from pathlib import Path

from pipeline.prompt import build_instructions, build_summary_input, compact_glossary
from pipeline.models import Cue
from pipeline.srt_io import parse_srt, slice_cues


def test_compact_glossary(glossary_path=None):
    from tests.conftest import GLOSSARY
    text = compact_glossary(GLOSSARY)
    assert "Daniel Larcher" in text or "达尼埃尔" in text
    assert " = " in text


def test_build_instructions_replaces_vars():
    from tests.conftest import PROMPT, GLOSSARY
    inst = build_instructions(
        source_language="英语",
        target_language="简体中文",
        prompt_path=PROMPT,
        glossary_path=GLOSSARY,
    )
    assert "英语" in inst or "简体中文" in inst
    assert "${sourceLanguage}" not in inst
    assert "专有名词" in inst


def test_build_summary_input(sample_srt):
    cues = slice_cues(parse_srt(sample_srt), max_cues=2)
    s = build_summary_input(cues)
    assert "0\t" in s and "Hello" in s
