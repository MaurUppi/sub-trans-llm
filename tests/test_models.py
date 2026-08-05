from __future__ import annotations

from model_client import Usage

from pipeline.models import Cue, TranslateResult, ValidateReport


def test_cue_fields():
    c = Cue(id="0", seq=1, start="00:00:01,000", end="00:00:02,000", text="Hi")
    assert c.id == "0"
    assert c.text == "Hi"


def test_validate_report_to_dict():
    vr = ValidateReport(ok=True, parsed={"0": {"src": "a", "tr": "甲"}}, stats={"n_in": 1})
    d = vr.to_dict()
    assert d["ok"] is True
    assert d["parsed_keys"] == ["0"]
    assert d["stats"]["n_in"] == 1


def test_translate_result_ok_requires_bilingual_and_validate():
    vr = ValidateReport(ok=True)
    r = TranslateResult(
        model_alias="x",
        model_id="m",
        usage=Usage(),
        status="completed",
        incomplete_reason=None,
        validate=vr,
        bilingual_srt="1\n00:00:00,000 --> 00:00:01,000\n甲\nA\n",
        raw_text="{}",
        elapsed_sec=1.0,
    )
    assert r.ok is True
    r2 = TranslateResult(
        model_alias="x",
        model_id="m",
        usage=Usage(),
        status="completed",
        incomplete_reason=None,
        validate=vr,
        bilingual_srt=None,
        raw_text="",
        elapsed_sec=0.0,
    )
    assert r2.ok is False


def test_meta_dict_includes_usage_and_batches():
    vr = ValidateReport(ok=False, errors=["e"])
    r = TranslateResult(
        model_alias="deepseek-v4-flash",
        model_id="id",
        usage=Usage(input_tokens=1, output_tokens=2, total_tokens=3),
        status="error",
        incomplete_reason=None,
        validate=vr,
        bilingual_srt=None,
        raw_text="",
        elapsed_sec=1.5,
        batch_count=2,
        batch_size=50,
        batch_jobs=3,
    )
    meta = r.meta_dict()
    assert meta["batch_count"] == 2
    assert meta["usage"]["total_tokens"] == 3
    assert meta["ok"] is False
