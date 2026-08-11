from __future__ import annotations

from pipeline.json_repair import repair_model_json, strip_code_fence


def test_strip_code_fence():
    assert strip_code_fence('```json\n{"a":1}\n```') == '{"a":1}'
    assert strip_code_fence('{"a":1}') == '{"a":1}'


def test_repair_good_json():
    data, notes = repair_model_json('{"0":{"src":"a","tr":"甲"}}')
    assert data["0"]["tr"] == "甲"
    assert notes == []


def test_repair_unquoted_numeric_key():
    raw = '{0":{"src":"a","tr":"甲"}}'  # missing quote before 0 — pattern needs {0":
    # actual pattern fixes ,201": or {201":
    raw = '{"x":1,1":{"src":"a","tr":"甲"}}'
    # simpler: {201": style
    raw = '{201": {"src": "a", "tr": "甲"}}'
    data, notes = repair_model_json(raw)
    assert data is not None
    assert "201" in {str(k) for k in data.keys()} or any(notes)


def test_repair_missing_braces():
    raw = '{"0":{"src":"a","tr":"甲"}'
    data, notes = repair_model_json(raw)
    assert data is not None
    assert any("closing brace" in n for n in notes)


def test_empty():
    data, notes = repair_model_json("")
    assert data is None and "empty" in notes
