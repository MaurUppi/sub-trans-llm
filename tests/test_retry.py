from __future__ import annotations

from pipeline.retry import is_retryable_exception, should_retry_result


def test_retryable_timeout():
    assert is_retryable_exception(TimeoutError("timeout"))


def test_should_retry_incomplete():
    ok, reason = should_retry_result("completed", "max_output_tokens", "{}", {"0": "a"})
    assert ok and "incomplete" in reason


def test_should_retry_bad_json():
    ok, reason = should_retry_result("completed", None, "not-json", {"0": "a"})
    assert ok and "validate" in reason
