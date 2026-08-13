from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import model_client
from model_client import Usage

from pipeline.config import DEFAULT_SUMMARY_MAX_OUTPUT_TOKENS
from pipeline.logging_util import log
from pipeline.models import Cue
from pipeline.prompt import build_summary_input, build_summary_instructions
from pipeline.retry import is_retryable_exception


def generate_episode_summary(
    model: str,
    cues: list[Cue],
    *,
    source_language: str = "英语",
    target_language: str = "简体中文",
    glossary_path: Optional[Path | str] = None,
    max_output_tokens: int = DEFAULT_SUMMARY_MAX_OUTPUT_TOKENS,
    timeout: float = 180.0,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    max_retries: int = 2,
    retry_backoff_sec: float = 3.0,
    out_dir: Optional[Path] = None,
    api_mode: str = model_client.DEFAULT_API_MODE,
) -> tuple[str, Usage, str, Optional[str]]:
    """
    全量字幕通读 → 摘要。

    Returns
    -------
    summary, usage, status, error_message
    """
    summary_input = build_summary_input(cues)
    instructions = build_summary_instructions(
        source_language=source_language,
        target_language=target_language,
        glossary_path=glossary_path,
    )
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "episode_summary_input.txt").write_text(
            summary_input, encoding="utf-8"
        )

    attempts = 1 + max(0, max_retries)
    last_err: Optional[str] = None
    usage = Usage()
    raw = ""
    status = "error"

    for attempt in range(1, attempts + 1):
        try:
            log(f"📖 通读摘要 attempt {attempt}/{attempts} cues={len(cues)} ...")
            mr = model_client.call(
                model,
                summary_input,
                instructions=instructions,
                temperature=temperature,
                top_p=top_p,
                max_output_tokens=max_output_tokens,
                timeout=timeout,
                api_mode=api_mode,
            )
            raw = (mr.text or "").strip()
            status = mr.status
            usage = mr.usage
            if out_dir:
                (out_dir / "episode_summary.raw.txt").write_text(raw, encoding="utf-8")

            if status == "completed" and raw and not mr.incomplete_reason:
                log(
                    f"   ✓ 摘要完成 chars={len(raw)} "
                    f"tokens={usage.input_tokens}/{usage.output_tokens}/{usage.total_tokens}"
                )
                if out_dir:
                    (out_dir / "episode_summary.txt").write_text(raw + "\n", encoding="utf-8")
                    (out_dir / "episode_summary.meta.json").write_text(
                        json.dumps(
                            {
                                "ok": True,
                                "status": status,
                                "chars": len(raw),
                                "usage": {
                                    "input_tokens": usage.input_tokens,
                                    "output_tokens": usage.output_tokens,
                                    "reasoning_tokens": usage.reasoning_tokens,
                                    "total_tokens": usage.total_tokens,
                                },
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                return raw, usage, status, None

            last_err = (
                f"status={status} incomplete={mr.incomplete_reason} "
                f"empty={not bool(raw)}"
            )
            log(f"   ⚠ 摘要不理想: {last_err}")
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            log(f"   ✗ 摘要异常: {last_err}")
            if attempt >= attempts or not is_retryable_exception(e):
                break
            time.sleep(retry_backoff_sec * (2 ** (attempt - 1)))
            continue
        if attempt < attempts:
            time.sleep(retry_backoff_sec * (2 ** (attempt - 1)))

    if out_dir:
        (out_dir / "episode_summary.meta.json").write_text(
            json.dumps(
                {
                    "ok": False,
                    "status": status,
                    "error": last_err,
                    "usage": {
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                        "reasoning_tokens": usage.reasoning_tokens,
                        "total_tokens": usage.total_tokens,
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if raw:
            (out_dir / "episode_summary.txt").write_text(raw + "\n", encoding="utf-8")
    return raw, usage, status, last_err or "summary failed"
