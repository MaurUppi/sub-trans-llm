from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROMPT = ROOT / "pipeline" / "prompts" / "translation.md"
DEFAULT_SUMMARY_PROMPT = ROOT / "pipeline" / "prompts" / "summary.md"
DEFAULT_GLOSSARY = None
DEFAULT_MAX_OUTPUT_TOKENS = 131072
DEFAULT_BATCH_SIZE = 50
DEFAULT_SUMMARY_MAX_OUTPUT_TOKENS = 2048

# --- 原文回显对齐（pipeline/src_align.py）---
# 归一化后相似度 >= 该值视为「回显走样」（警告）；低于则视为内容不符（错误）。
SRC_DRIFT_THRESHOLD = 0.85
# 默认严格：src 错位判为 error，从而复用既有的重试 / 拆批链路。
STRICT_SRC_DEFAULT = True

# --- 译文侧字幕约束（pipeline/subtitle_check.py）---
# 默认取 Netflix 简体中文时间轴规范；只做度量，不阻断流程。
ZH_MAX_CHARS_PER_LINE = 16
ZH_MAX_LINES = 2
ZH_MAX_CPS = 9.0
ZH_MIN_DURATION_SEC = 0.833  # 5/6 秒
ZH_MAX_DURATION_SEC = 7.0

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
