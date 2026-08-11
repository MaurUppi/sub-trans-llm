from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def sample_srt(tmp_path: Path) -> Path:
    p = tmp_path / "sample.srt"
    p.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nHello world\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nLine A\nLine B\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def glossary_path(tmp_path: Path) -> Path:
    p = tmp_path / "glossary.csv"
    p.write_text(
        "source,target,note\n"
        "Daniel Larcher,达尼埃尔·拉尔谢,市长\n",
        encoding="utf-8",
    )
    return p
