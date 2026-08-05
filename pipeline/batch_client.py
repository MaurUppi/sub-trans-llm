from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import model_client
from model_client import Usage

from pipeline.logging_util import log
from pipeline.models import BatchOutcome, Cue, ValidateReport
from pipeline.retry import is_retryable_exception, should_retry_result
from pipeline.srt_io import build_input_json
from pipeline.validate import validate_response


def call_one_batch(
    *,
    model: str,
    batch_index: int,
    batch_cues: list[Cue],
    instructions: str,
    max_output_tokens: int,
    timeout: float,
    temperature: Optional[float],
    top_p: Optional[float],
    max_retries: int,
    retry_backoff_sec: float,
    batch_out: Optional[Path],
) -> BatchOutcome:
    """对一批 cue 调用模型（JSON 键使用全局 id）。"""
    input_json, input_map = build_input_json(batch_cues)
    if batch_out:
        batch_out.mkdir(parents=True, exist_ok=True)
        (batch_out / "input.json").write_text(input_json, encoding="utf-8")

    attempts = 1 + max(0, max_retries)
    last_exc: Optional[BaseException] = None
    raw_text = ""
    status = "error"
    incomplete: Optional[str] = None
    usage = Usage()
    model_id = ""
    alias = model
    attempt_notes: list[str] = []

    for attempt in range(1, attempts + 1):
        try:
            log(
                f"   → batch {batch_index:02d} API attempt {attempt}/{attempts} "
                f"(cues={len(batch_cues)} ids={batch_cues[0].id}..{batch_cues[-1].id})"
            )
            mr = model_client.call(
                model,
                input_json,
                instructions=instructions,
                temperature=temperature,
                top_p=top_p,
                max_output_tokens=max_output_tokens,
                timeout=timeout,
            )
            raw_text = mr.text or ""
            status = mr.status
            incomplete = mr.incomplete_reason
            usage = mr.usage
            model_id = mr.model
            alias = mr.alias
            last_exc = None

            if batch_out:
                (batch_out / "raw_output.txt").write_text(raw_text, encoding="utf-8")
                if attempt > 1:
                    (batch_out / f"raw_output.attempt{attempt}.txt").write_text(
                        raw_text, encoding="utf-8"
                    )

            retry, why = should_retry_result(status, incomplete, raw_text, input_map)
            if not retry:
                log(
                    f"   ✓ batch {batch_index:02d} ok "
                    f"tokens={usage.input_tokens}/{usage.output_tokens}/{usage.total_tokens}"
                )
                break

            attempt_notes.append(f"batch{batch_index} attempt{attempt}: retry — {why}")
            log(f"   ⚠ batch {batch_index:02d} attempt {attempt} 需重试: {why}")
            if attempt >= attempts:
                break
            sleep_s = retry_backoff_sec * (2 ** (attempt - 1))
            log(f"   … 退避 {sleep_s:.1f}s")
            time.sleep(sleep_s)

        except Exception as e:  # noqa: BLE001
            last_exc = e
            attempt_notes.append(
                f"batch{batch_index} attempt{attempt}: {type(e).__name__}: {e}"
            )
            log(
                f"   ✗ batch {batch_index:02d} attempt {attempt} "
                f"异常: {type(e).__name__}: {e}"
            )
            if batch_out:
                (batch_out / "last_exception.txt").write_text(
                    f"{type(e).__name__}: {e}\n", encoding="utf-8"
                )
            if attempt >= attempts or not is_retryable_exception(e):
                status = f"error: {type(e).__name__}: {e}"
                break
            sleep_s = retry_backoff_sec * (2 ** (attempt - 1))
            log(f"   … 可重试异常，退避 {sleep_s:.1f}s")
            time.sleep(sleep_s)

    if last_exc is not None and not raw_text:
        vr = ValidateReport(
            ok=False,
            errors=[f"api error: {type(last_exc).__name__}: {last_exc}"]
            + attempt_notes,
            stats={"n_in": len(input_map), "n_out": 0, "n_tr_ok": 0},
        )
    else:
        vr = validate_response(raw_text, input_map)
        if status != "completed":
            vr.errors.append(f"api status={status}")
            vr.ok = False
        if incomplete:
            msg = f"incomplete: {incomplete}"
            if "length" in str(incomplete).lower():
                msg += f" — 可能截断；max_output_tokens={max_output_tokens}"
            vr.errors.append(msg)
            vr.ok = False
        if any("json.loads failed" in e for e in vr.errors):
            vr.errors.append("JSON 解析失败：可能输出被截断；见 raw_output.txt")
        for note in attempt_notes:
            vr.warnings.append(note)

    if batch_out:
        (batch_out / "validate.json").write_text(
            json.dumps(vr.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if vr.parsed:
            (batch_out / "parsed.json").write_text(
                json.dumps(vr.parsed, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    return BatchOutcome(
        batch_index=batch_index,
        cues=batch_cues,
        input_map=input_map,
        raw_text=raw_text,
        status=status,
        incomplete_reason=incomplete,
        usage=usage,
        model_id=model_id,
        alias=alias,
        validate=vr,
        attempt_notes=attempt_notes,
    )
