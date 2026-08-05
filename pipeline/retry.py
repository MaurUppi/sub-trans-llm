from __future__ import annotations

from typing import Optional

from pipeline.validate import validate_response


def is_retryable_exception(exc: BaseException) -> bool:
    """网络/限流/5xx 等可重试异常。"""
    name = type(exc).__name__
    text = str(exc).lower()
    retry_names = (
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
        "APIStatusError",
        "TimeoutError",
        "RemoteProtocolError",
        "ConnectError",
        "ReadTimeout",
    )
    if name in retry_names:
        return True
    for needle in ("429", "500", "502", "503", "504", "timeout", "rate limit", "overloaded"):
        if needle in text:
            return True
    status = getattr(exc, "status_code", None)
    if status in (408, 409, 429, 500, 502, 503, 504):
        return True
    return False


def should_retry_result(
    status: str,
    incomplete: Optional[str],
    raw_text: str,
    input_map: dict[str, str],
) -> tuple[bool, str]:
    """根据 API 结果决定是否重试，并返回原因。"""
    if status != "completed":
        return True, f"api status={status}"
    if incomplete:
        reason = str(incomplete)
        hint = ""
        if "length" in reason.lower():
            hint = " (可能触顶 max_output_tokens 或输出被截断)"
        return True, f"incomplete: {reason}{hint}"
    vr = validate_response(raw_text, input_map)
    if not vr.ok:
        return True, "validate hard fail: " + "; ".join(vr.errors[:3])
    return False, ""
