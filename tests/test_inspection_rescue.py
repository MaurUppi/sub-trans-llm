from __future__ import annotations

import json
from pathlib import Path

import model_client
from pipeline.inspection_rescue import rescue_inspection_run_dir
from pipeline.models import BatchOutcome, ValidateReport


def test_inspection_rescue_masks_then_restores_frozen_glossary_term(
    tmp_path: Path,
) -> None:
    source = tmp_path / "episode.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n"
        "I know the Communists well!\n",
        encoding="utf-8",
    )
    run_dir = tmp_path / "attempt-001"
    run_dir.mkdir()
    original = {"0": "I know the Communists well!"}
    (run_dir / "input.json").write_text(
        json.dumps(original), encoding="utf-8"
    )
    (run_dir / "parsed.json").write_text("{}", encoding="utf-8")
    (run_dir / "instructions.txt").write_text(
        "Translate faithfully.", encoding="utf-8"
    )
    (run_dir / "meta.json").write_text(
        json.dumps(
            {
                "model_id": "qwen-test",
                "batch_size": 50,
                "batch_jobs": 1,
                "batch_reports": [
                    {"batch_index": 0, "ok": False, "errors": ["blocked"]}
                ],
                "sampling": {
                    "temperature": {"sent": True, "value": 0.3},
                    "top_p": {"sent": False, "value": None},
                },
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            }
        ),
        encoding="utf-8",
    )
    calls: list[dict[str, object]] = []

    def fake_call(**kwargs: object) -> BatchOutcome:
        calls.append(kwargs)
        cue = kwargs["batch_cues"][0]
        assert cue.text == "I know the __TQA_GLOSSARY_COMMUNISTS__ well!"
        return BatchOutcome(
            batch_index=0,
            cues=kwargs["batch_cues"],
            input_map={"0": cue.text},
            raw_text=(
                '{"0":{"src":"I know the '
                '__TQA_GLOSSARY_COMMUNISTS__ well!",'
                '"tr":"我很了解__TQA_GLOSSARY_COMMUNISTS__！"}}'
            ),
            status="completed",
            incomplete_reason=None,
            usage=model_client.Usage(
                input_tokens=8, output_tokens=4, total_tokens=12
            ),
            model_id="qwen-test",
            alias="qwen3.7-plus",
            validate=ValidateReport(
                ok=True,
                parsed={
                    "0": {
                        "src": cue.text,
                        "tr": "我很了解__TQA_GLOSSARY_COMMUNISTS__！",
                    }
                },
            ),
        )

    result = rescue_inspection_run_dir(
        run_dir=run_dir,
        srt_path=source,
        model="qwen3.7-plus",
        temperature=0.3,
        top_p=model_client.OMIT,
        call_fn=fake_call,
    )

    assert result.ok
    assert len(calls) == 1
    assert calls[0]["temperature"] == 0.3
    assert calls[0]["top_p"] is model_client.OMIT
    assert calls[0]["api_mode"] == "responses"
    assert result.api_mode == "responses"
    assert "我很了解共产党！" in result.bilingual_srt
    assert "I know the Communists well!" in result.bilingual_srt
    assert result.validate.parsed["0"] == {
        "src": "I know the Communists well!",
        "tr": "我很了解共产党！",
    }
    assert (run_dir / "instructions.txt").read_text(
        encoding="utf-8"
    ) == "Translate faithfully."
    assert json.loads((run_dir / "parsed.json").read_text(encoding="utf-8")) == {}
    manifest = json.loads(
        next((run_dir / "inspection_rescue").glob("pass-*/manifest.json")).read_text(
            encoding="utf-8"
        )
    )
    assert manifest["status"] == "completed"
    assert manifest["transformations"][0]["source_term"] == "Communists"
    assert manifest["transformations"][0]["restored_term"] == "共产党"
    assert (run_dir / "inspection_rescue" / "pass-001" / "assembled" / "bilingual.srt").is_file()
