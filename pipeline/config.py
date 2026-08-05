from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROMPT = ROOT / "docs" / "translation_prompt.md"
DEFAULT_GLOSSARY = ROOT / "docs" / "Un_Village_francais_Glossary.md"
DEFAULT_MAX_OUTPUT_TOKENS = 131072
DEFAULT_BATCH_SIZE = 50
DEFAULT_SUMMARY_MAX_OUTPUT_TOKENS = 2048

ELLIPSIS_OK = "\u2026"  # …
ELLIPSIS_BAD = "\u22ef"  # ⋯


@dataclass
class RunConfig:
    batch_size: int = DEFAULT_BATCH_SIZE
    batch_jobs: int = 1
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_retries: int = 2
    retry_backoff_sec: float = 3.0
    timeout: float = 300.0
    use_episode_summary: bool = True
    sub_batch_size: int = 10
    sub_batch_sizes: tuple[int, ...] = (10, 5, 2, 1)
    enable_sub_batch_on_content_filter: bool = True
    summary_max_output_tokens: int = DEFAULT_SUMMARY_MAX_OUTPUT_TOKENS
