from __future__ import annotations

from model_client import Usage
from pipeline.models import TranslateResult, ValidateReport
from pipeline.preprocess.deliver import default_zh_path, write_zh_srt


def test_default_zh_path():
    p = default_zh_path("/tmp/foo/bar_eng.srt")
    assert p.name == "bar_eng_zh.srt"
    assert str(p.parent) == "/tmp/foo" or p.parent.as_posix().endswith("foo")


def test_write_zh(tmp_path):
    src = tmp_path / "ep.srt"
    src.write_text("x", encoding="utf-8")
    r = TranslateResult(
        model_alias="m", model_id="id", usage=Usage(), status="completed",
        incomplete_reason=None, validate=ValidateReport(ok=True),
        bilingual_srt="1\n00:00:00,000 --> 00:00:01,000\n中\nEn\n",
        raw_text="", elapsed_sec=0.0,
    )
    out = write_zh_srt(r, src)
    assert out is not None and out.name == "ep_zh.srt"
    assert "中" in out.read_text(encoding="utf-8")
