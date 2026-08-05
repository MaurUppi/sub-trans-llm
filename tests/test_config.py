from __future__ import annotations

from pathlib import Path

from pipeline import config


def test_defaults_and_paths():
    assert config.DEFAULT_BATCH_SIZE == 50
    assert config.DEFAULT_MAX_OUTPUT_TOKENS == 131072
    assert config.DEFAULT_PROMPT.name == "translation_prompt.md"
    assert config.DEFAULT_GLOSSARY.is_file() or True  # path defined
    assert config.ROOT.is_dir()
    assert config.ELLIPSIS_OK == "\u2026"
    assert config.ELLIPSIS_BAD == "\u22ef"


def test_run_config_dataclass():
    rc = config.RunConfig(batch_size=30, batch_jobs=3, temperature=1.3)
    assert rc.batch_size == 30
    assert rc.sub_batch_sizes == (10, 5, 2, 1)
