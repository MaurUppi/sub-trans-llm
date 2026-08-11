from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import config
from pipeline.selfcheck import self_check_offline


def test_self_check_offline_ok(sample_srt, monkeypatch):
    monkeypatch.setattr(
        config,
        "DEFAULT_GLOSSARY",
        sample_srt.parent / "private-glossary-does-not-exist.csv",
    )
    self_check_offline(sample_srt)


def test_selfcheck_supports_custom_source_language_and_glossary(
    sample_srt: Path, tmp_path: Path
) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text(
        "Translate ${sourceLanguage} to ${targetLanguage}.", encoding="utf-8"
    )
    glossary = tmp_path / "glossary.csv"
    glossary.write_text(
        "source,target,note\nbonjour,你好,问候语\n", encoding="utf-8"
    )

    self_check_offline(
        sample_srt,
        source_language="法语",
        target_language="简体中文",
        prompt_path=prompt,
        glossary_path=glossary,
    )


def test_selfcheck_rejects_an_explicit_missing_glossary(sample_srt: Path) -> None:
    with pytest.raises(FileNotFoundError, match="glossary"):
        self_check_offline(
            sample_srt,
            glossary_path=sample_srt.parent / "missing.csv",
        )
