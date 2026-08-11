"""
字幕翻译模块：SRT → JSON input、外部文件拼 instructions、校验、双语 SRT。

实现已拆至 ``pipeline/`` 包；本文件保持稳定 re-export，兼容 main.py。
具体调用方式与当前默认值见 README.md。
"""

from __future__ import annotations

from pipeline.batch_client import call_one_batch as _call_one_batch
from pipeline.config import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_GLOSSARY,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_PROMPT,
    DEFAULT_SUMMARY_MAX_OUTPUT_TOKENS,
    ELLIPSIS_BAD as _ELLIPSIS_BAD,
    ELLIPSIS_OK as _ELLIPSIS_OK,
    ROOT as _ROOT,
)
from pipeline.logging_util import log as _log
from pipeline.models import BatchOutcome, Cue, TranslateResult, ValidateReport
from pipeline.orchestrator import run_once
from pipeline.persist import write_outputs as _write_outputs
from pipeline.prompt import (
    SUMMARY_INSTRUCTIONS,
    build_instructions,
    build_summary_input,
    compact_glossary,
)
from pipeline.repair import repair_run_dir
from pipeline.retry import is_retryable_exception as _is_retryable_exception
from pipeline.retry import should_retry_result as _should_retry_result
from pipeline.selfcheck import self_check_offline
from pipeline.srt_io import (
    build_bilingual_srt,
    build_input_json,
    chunk_cues,
    parse_srt,
    reindex_cues,
    slice_cues,
    sum_usage,
)
from pipeline.summary import generate_episode_summary
from pipeline.validate import validate_response
from pipeline.json_repair import repair_model_json, strip_code_fence as _strip_code_fence

__all__ = [
    "Cue",
    "ValidateReport",
    "TranslateResult",
    "parse_srt",
    "reindex_cues",
    "slice_cues",
    "chunk_cues",
    "build_input_json",
    "build_bilingual_srt",
    "build_instructions",
    "compact_glossary",
    "validate_response",
    "repair_model_json",
    "run_once",
    "repair_run_dir",
    "self_check_offline",
    "generate_episode_summary",
]
