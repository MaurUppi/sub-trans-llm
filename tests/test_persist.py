from __future__ import annotations

from model_client import Usage
from pipeline.models import TranslateResult, ValidateReport
from pipeline.persist import write_outputs


def test_write_outputs_success_and_partial(tmp_path):
    vr = ValidateReport(ok=True, parsed={"0": {"src": "a", "tr": "甲"}}, stats={"n_in": 1, "n_out": 1, "n_tr_ok": 1})
    r = TranslateResult(
        model_alias="x", model_id="m", usage=Usage(), status="completed",
        incomplete_reason=None, validate=vr, bilingual_srt="1\n00:00:00,000 --> 00:00:01,000\n甲\na\n",
        raw_text="{}", elapsed_sec=0.1, instructions="inst",
    )
    write_outputs(tmp_path, r, '{"0":"a"}')
    assert (tmp_path / "bilingual.srt").is_file()
    assert (tmp_path / "meta.json").is_file()
    assert not (tmp_path / "bilingual.PARTIAL.txt").exists()

    r2 = TranslateResult(
        model_alias="x", model_id="m", usage=Usage(), status="error",
        incomplete_reason=None, validate=ValidateReport(ok=False, errors=["e"]),
        bilingual_srt=None, raw_text="", elapsed_sec=0.0, instructions="inst",
    )
    write_outputs(tmp_path / "fail", r2, "{}")
    assert (tmp_path / "fail" / "bilingual.PARTIAL.txt").is_file()
