from __future__ import annotations

from pipeline.preprocess.bridge import document_to_cues, parse_to_document, write_srt
from pipeline.srt_io import parse_srt


def test_roundtrip_sample(sample_srt, tmp_path):
    doc = parse_to_document(sample_srt)
    cues = document_to_cues(doc)
    assert len(cues) == 2
    assert cues[0].id == "0"
    out = tmp_path / "out.srt"
    write_srt(out, cues)
    again = parse_srt(out)
    assert len(again) == 2
    assert "Hello" in again[0].text
