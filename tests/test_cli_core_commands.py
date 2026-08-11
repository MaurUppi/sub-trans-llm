from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import main


def test_main_help_describes_multilingual_to_simplified_chinese() -> None:
    assert "多语言译简中字幕工具" in main.build_parser().format_help()


def test_ping_forwards_selected_models_and_minimal_request_options(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_smoke_test(models, **kwargs):
        captured.update({"models": models, **kwargs})
        return [
            SimpleNamespace(
                ok=True,
                alias="qwen3.7-plus",
                status="completed",
                text="OK",
                usage=SimpleNamespace(total_tokens=2),
            )
        ]

    monkeypatch.setattr(main.model_client, "smoke_test", fake_smoke_test)
    args = main.build_parser().parse_args(
        [
            "ping",
            "--models",
            "qwen3.7-plus",
            "--max-output-tokens",
            "8",
            "--prompt",
            "Reply OK",
            "--APImode",
            "Responses",
        ]
    )

    assert main.cmd_ping(args) == 0
    assert captured == {
        "models": ["qwen3.7-plus"],
        "max_output_tokens": 8,
        "prompt": "Reply OK",
        "api_mode": "responses",
    }


def test_selfcheck_exposes_only_relevant_inputs_and_forwards_them(
    tmp_path: Path, monkeypatch
) -> None:
    prompt = tmp_path / "prompt.md"
    glossary = tmp_path / "glossary.csv"
    captured: dict[str, object] = {}

    def fake_selfcheck(srt_path, **kwargs):
        captured.update({"srt_path": srt_path, **kwargs})

    monkeypatch.setattr(main.translate, "self_check_offline", fake_selfcheck)
    parser = main.build_parser()
    args = parser.parse_args(
        [
            "selfcheck",
            "--srt",
            "input.srt",
            "--source-language",
            "法语",
            "--target-language",
            "简体中文",
            "--prompt",
            str(prompt),
            "--glossary",
            str(glossary),
        ]
    )

    assert main.cmd_selfcheck(args) == 0
    assert captured == {
        "srt_path": Path("input.srt"),
        "source_language": "法语",
        "target_language": "简体中文",
        "prompt_path": prompt,
        "glossary_path": glossary,
    }
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["selfcheck", "--srt", "input.srt", "--APImode", "Responses"]
        )


def test_repair_resolves_model_child_and_reuses_recorded_api_mode(
    tmp_path: Path, monkeypatch
) -> None:
    parent = tmp_path / "run"
    model_dir = parent / "qwen3.7-plus"
    model_dir.mkdir(parents=True)
    (model_dir / "meta.json").write_text(
        json.dumps(
            {
                "api_mode": "responses",
                "sampling": {
                    "temperature": {"sent": True, "value": 0.3},
                    "top_p": {"sent": False, "value": None},
                },
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_repair_run_dir(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            ok=True,
            validate=SimpleNamespace(
                stats={"n_out": 2, "n_in": 2}, errors=[]
            ),
        )

    monkeypatch.setattr(main.translate, "repair_run_dir", fake_repair_run_dir)
    args = main.build_parser().parse_args(
        [
            "repair",
            "--srt",
            "input.srt",
            "--run-dir",
            str(parent),
            "--model",
            "qwen3.7-plus",
        ]
    )

    assert args.api_mode is None
    assert main.cmd_repair(args) == 0
    assert captured["run_dir"] == model_dir
    assert captured["api_mode"] == "responses"
    assert captured["temperature"] == 0.3
    assert captured["top_p"] is main.model_client.OMIT


def test_repair_rejects_api_mode_that_conflicts_with_recorded_run(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    run_dir = tmp_path / "qwen3.7-plus"
    run_dir.mkdir()
    (run_dir / "meta.json").write_text(
        json.dumps({"api_mode": "responses"}), encoding="utf-8"
    )
    called = False

    def fake_repair_run_dir(**_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(main.translate, "repair_run_dir", fake_repair_run_dir)
    args = main.build_parser().parse_args(
        [
            "repair",
            "--srt",
            "input.srt",
            "--run-dir",
            str(run_dir),
            "--model",
            "qwen3.7-plus",
            "--APImode",
            "ChatCompletion",
        ]
    )

    assert main.cmd_repair(args) == 2
    assert called is False
    assert "api mode" in capsys.readouterr().err.lower()


def test_repair_rejects_non_object_meta(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "qwen3.7-plus"
    run_dir.mkdir()
    (run_dir / "meta.json").write_text("[]", encoding="utf-8")
    args = main.build_parser().parse_args(
        [
            "repair",
            "--srt",
            "input.srt",
            "--run-dir",
            str(run_dir),
            "--model",
            "qwen3.7-plus",
        ]
    )

    assert main.cmd_repair(args) == 2
    assert "invalid run meta" in capsys.readouterr().err.lower()


def test_repair_rejects_unused_out_option() -> None:
    with pytest.raises(SystemExit):
        main.build_parser().parse_args(
            [
                "repair",
                "--srt",
                "input.srt",
                "--run-dir",
                "run-dir",
                "--model",
                "qwen3.7-plus",
                "--out",
                "unused",
            ]
        )


def test_smoke_rejects_an_empty_model_selection(monkeypatch, capsys) -> None:
    called = False

    def fake_dispatch(*_args, **_kwargs):
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(main, "_dispatch", fake_dispatch)
    args = main.build_parser().parse_args(
        ["smoke", "--srt", "input.srt", "--models", ","]
    )

    assert main.cmd_smoke(args) == 2
    assert called is False
    assert "model" in capsys.readouterr().err.lower()


def test_smoke_records_a_model_exception_instead_of_crashing(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    def fail_run(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(main, "_run_one", fail_run)
    args = main.build_parser().parse_args(
        [
            "smoke",
            "--srt",
            "input.srt",
            "--models",
            "qwen3.7-plus",
            "--APImode",
            "Responses",
            "--out",
            str(tmp_path),
        ]
    )

    assert main.cmd_smoke(args) == 1
    assert "provider unavailable" in capsys.readouterr().out
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary[0]["ok"] is False
    assert "provider unavailable" in summary[0]["status"]
    assert summary[0]["api_mode"] == "responses"
