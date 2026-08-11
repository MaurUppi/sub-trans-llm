from __future__ import annotations

import json
from datetime import timedelta

import pytest

import model_client
from pipeline.preprocess import vc_optimize_adapter
from pipeline.rules.sub_processor import SRTDocument, SubtitleBlock, TimeCode


def _document(size: int) -> SRTDocument:
    return SRTDocument(
        blocks=[
            SubtitleBlock(
                index=i,
                time_code=TimeCode(
                    start=timedelta(seconds=i),
                    end=timedelta(seconds=i + 1),
                ),
                lines=[f"cue {i}"],
            )
            for i in range(1, size + 1)
        ]
    )


def _result(text: str, *, status: str = "completed") -> model_client.ModelResult:
    return model_client.ModelResult(
        text=text,
        model="qwen3.7-max",
        alias="qwen3.7-max",
        status=status,
        usage=model_client.Usage(),
        max_output_tokens=8192,
        api_mode="chat_completions",
    )


def test_optimize_batches_large_document_and_preserves_one_to_one(monkeypatch) -> None:
    calls: list[dict[str, str]] = []

    def fake_call(_model, user, **_kwargs):
        payload = json.loads(user)
        calls.append(payload)
        return _result(json.dumps({key: value.upper() for key, value in payload.items()}))

    monkeypatch.setattr(vc_optimize_adapter.model_client, "call", fake_call)

    source = _document(205)
    optimized, meta = vc_optimize_adapter.optimize_document(
        source,
        model="qwen3.7-max",
    )

    assert [len(payload) for payload in calls] == [100, 100, 5]
    assert list(calls[0]) == [str(i) for i in range(1, 101)]
    assert list(calls[2]) == [str(i) for i in range(201, 206)]
    assert optimized.total_blocks == source.total_blocks
    assert optimized.blocks[0].text == "CUE 1"
    assert optimized.blocks[-1].text == "CUE 205"
    assert [block.time_code for block in optimized.blocks] == [
        block.time_code for block in source.blocks
    ]
    assert meta["batch_size"] == 100
    assert meta["batch_count"] == 3


def test_optimize_rejects_batch_with_missing_cue(monkeypatch) -> None:
    def fake_call(_model, user, **_kwargs):
        payload = json.loads(user)
        payload.pop(next(iter(payload)))
        return _result(json.dumps(payload))

    monkeypatch.setattr(vc_optimize_adapter.model_client, "call", fake_call)

    with pytest.raises(ValueError, match="key mismatch"):
        vc_optimize_adapter.optimize_document(_document(2), model="qwen3.7-max")


def test_optimize_rejects_non_completed_model_result(monkeypatch) -> None:
    def fake_call(_model, user, **_kwargs):
        return _result(user, status="incomplete")

    monkeypatch.setattr(vc_optimize_adapter.model_client, "call", fake_call)

    with pytest.raises(RuntimeError, match="status=incomplete"):
        vc_optimize_adapter.optimize_document(_document(1), model="qwen3.7-max")


def test_optimize_prompt_forbids_dropping_pure_sdh_cues(monkeypatch) -> None:
    captured_instructions = ""

    def fake_call(_model, user, **kwargs):
        nonlocal captured_instructions
        captured_instructions = kwargs["instructions"]
        return _result(user)

    monkeypatch.setattr(vc_optimize_adapter.model_client, "call", fake_call)

    vc_optimize_adapter.optimize_document(_document(1), model="qwen3.7-max")

    assert "Do not omit any input key" in captured_instructions
    assert "pure non-verbal or SDH cue" in captured_instructions
    assert "--remove-sdh" in captured_instructions


def test_optimize_bypasses_and_preserves_pure_sdh_cues(monkeypatch) -> None:
    source = _document(2)
    source.blocks[0].lines = ["-[ Sighs ]"]
    payloads: list[dict[str, str]] = []

    def fake_call(_model, user, **_kwargs):
        payload = json.loads(user)
        payloads.append(payload)
        return _result(
            json.dumps({key: value.upper() for key, value in payload.items()})
        )

    monkeypatch.setattr(vc_optimize_adapter.model_client, "call", fake_call)

    optimized, meta = vc_optimize_adapter.optimize_document(
        source,
        model="qwen3.7-max",
    )

    assert payloads == [{"2": "cue 2"}]
    assert optimized.blocks[0].text == "-[ Sighs ]"
    assert optimized.blocks[1].text == "CUE 2"
    assert meta["bypassed_sdh_cues"] == 1
