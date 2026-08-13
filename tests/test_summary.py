from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import model_client
from pipeline import orchestrator, summary
from pipeline.models import BatchOutcome, Cue, ValidateReport


def _ok_model_result(text: str = "本集摘要") -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        status="completed",
        incomplete_reason=None,
        usage=model_client.Usage(),
    )


def test_generate_episode_summary_instructions_follow_source_language(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_call(_model: str, _input_text: str, **kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _ok_model_result()

    monkeypatch.setattr(summary.model_client, "call", fake_call)

    summary.generate_episode_summary(
        "test-alias",
        [Cue(id="0", seq=1, start="00:00:01,000", end="00:00:02,000", text="Bonjour")],
        source_language="法语",
        target_language="日语",
    )

    instructions = str(captured["instructions"])
    assert "当前法语字幕" in instructions
    assert "请用日语输出" in instructions
    assert "一整集" not in instructions


def test_generate_episode_summary_includes_glossary_when_provided(
    monkeypatch, glossary_path: Path
) -> None:
    captured: dict[str, object] = {}

    def fake_call(_model: str, _input_text: str, **kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _ok_model_result()

    monkeypatch.setattr(summary.model_client, "call", fake_call)

    summary.generate_episode_summary(
        "test-alias",
        [Cue(id="0", seq=1, start="00:00:01,000", end="00:00:02,000", text="Hello")],
        glossary_path=glossary_path,
    )

    instructions = str(captured["instructions"])
    assert "## 专有名词（摘要中使用表内译名，不要另造）" in instructions
    assert "Daniel Larcher = 达尼埃尔·拉尔谢（市长）" in instructions


def test_generate_episode_summary_omits_glossary_when_absent(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_call(_model: str, _input_text: str, **kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _ok_model_result()

    monkeypatch.setattr(summary.model_client, "call", fake_call)

    summary.generate_episode_summary(
        "test-alias",
        [Cue(id="0", seq=1, start="00:00:01,000", end="00:00:02,000", text="Hello")],
    )

    assert "## 专有名词（摘要中使用表内译名，不要另造）" not in str(
        captured["instructions"]
    )


def test_run_once_forwards_source_language_and_glossary_to_summary(
    sample_srt: Path, tmp_path: Path, glossary_path: Path, monkeypatch
) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text(
        "Translate ${sourceLanguage} to ${targetLanguage}.",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_generate_summary(*_args: object, **kwargs: object):
        captured.update(kwargs)
        return "episode context", model_client.Usage(), "completed", None

    def fake_batch(**kwargs):
        cues = kwargs["batch_cues"]
        parsed = {cue.id: {"src": cue.text, "tr": f"译{cue.id}"} for cue in cues}
        return BatchOutcome(
            batch_index=kwargs["batch_index"],
            cues=cues,
            input_map={cue.id: cue.text for cue in cues},
            raw_text="{}",
            status="completed",
            incomplete_reason=None,
            usage=model_client.Usage(),
            model_id="test-model",
            alias="test-alias",
            validate=ValidateReport(
                ok=True,
                parsed=parsed,
                stats={"n_in": len(cues), "n_tr_ok": len(cues)},
            ),
        )

    monkeypatch.setattr(orchestrator, "generate_episode_summary", fake_generate_summary)
    monkeypatch.setattr(orchestrator, "call_one_batch", fake_batch)

    orchestrator.run_once(
        srt_path=sample_srt,
        model="test-alias",
        prompt_path=prompt,
        source_language="法语",
        target_language="日语",
        glossary_path=glossary_path,
        out_dir=tmp_path / "out",
        max_cues=1,
        batch_size=1,
    )

    assert captured["source_language"] == "法语"
    assert captured["target_language"] == "日语"
    assert Path(str(captured["glossary_path"])) == glossary_path
