from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRT = ROOT / "A.French.Village.S01E03.Passer.la.ligne_eng.srt"
PROMPT = ROOT / "docs" / "translation_prompt.md"
GLOSSARY = ROOT / "docs" / "Un_Village_francais_Glossary.md"


@pytest.fixture
def srt_path() -> Path:
    assert SRT.is_file()
    return SRT


@pytest.fixture
def sample_srt(tmp_path: Path) -> Path:
    p = tmp_path / "sample.srt"
    p.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nHello world\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nLine A\nLine B\n",
        encoding="utf-8",
    )
    return p
