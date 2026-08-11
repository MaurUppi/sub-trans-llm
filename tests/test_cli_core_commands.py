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


def test_preprocess_help_exposes_only_stage_a_options(capsys) -> None:
    parser = main.build_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["preprocess", "--help"])

    assert exc_info.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "YouTube 滚动窗口自动字幕" in help_text
    assert "50ms" in help_text
    assert "英文单行超过 42 字符或超过 2 行" in help_text
    assert "非 Netflix" in help_text
    assert "简中交付校验" in help_text
    assert "必须同时指定 --model" in help_text
    assert "失败即终止" in help_text
    for option in (
        "--srt",
        "--out",
        "--fix-overlaps",
        "--remove-sdh",
        "--remove-disfluency",
        "--optimize",
        "--resplit",
        "--words",
        "--model",
        "--APImode",
    ):
        assert option in help_text
    for unused in (
        "--source-language",
        "--target-language",
        "--prompt",
        "--glossary",
        "--max-cues",
        "--cue-offset",
        "--max-output-tokens",
        "--timeout",
        "--jobs",
        "--max-retries",
        "--retry-backoff",
        "--batch-size",
        "--batch-jobs",
        "--no-summary",
        "--temperature",
        "--top-p",
    ):
        assert unused not in help_text


def test_run_help_explains_automatic_process_artifact_lifecycle(capsys) -> None:
    parser = main.build_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["run", "--help"])

    assert exc_info.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "成功时仅保留最终 SRT" in help_text
    assert "失败时保留过程证据" in help_text


def test_default_output_workspaces_are_unique() -> None:
    first = main._default_out("run_qwen3.7-plus")
    second = main._default_out("run_qwen3.7-plus")

    assert first != second


@pytest.mark.parametrize("command", ["preprocess", "run"])
@pytest.mark.parametrize(
    "conflicting",
    [
        ["--fix-overlaps", "--no-fix-overlaps"],
        ["--resplit", "--no-resplit"],
    ],
)
def test_stage_a_force_switches_are_mutually_exclusive(
    command: str,
    conflicting: list[str],
) -> None:
    argv = [command, "--srt", "input.srt"]
    if command == "run":
        argv.extend(["--model", "qwen3.7-plus", "--preprocess"])

    with pytest.raises(SystemExit):
        main.build_parser().parse_args([*argv, *conflicting])


def test_run_rejects_stage_a_options_without_preprocess(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    dispatched = False

    def fake_dispatch(*_args, **_kwargs):
        nonlocal dispatched
        dispatched = True
        return 0

    monkeypatch.setattr(main, "_dispatch", fake_dispatch)
    monkeypatch.setattr(main, "_default_out", lambda _prefix: tmp_path / "out")
    args = main.build_parser().parse_args(
        [
            "run",
            "--srt",
            "input.srt",
            "--model",
            "qwen3.7-plus",
            "--remove-sdh",
        ]
    )

    assert main.cmd_run(args) == 2
    assert dispatched is False
    assert "require --preprocess" in capsys.readouterr().err


def test_standalone_and_run_build_the_same_stage_a_config(tmp_path: Path) -> None:
    parser = main.build_parser()
    words = tmp_path / "words.json"
    words.write_text("[]", encoding="utf-8")
    common_flags = [
        "--srt",
        "input.srt",
        "--APImode",
        "Responses",
        "--fix-overlaps",
        "--remove-sdh",
        "--remove-disfluency",
        "--optimize",
        "--resplit",
        "--words",
        str(words),
        "--model",
        "qwen3.7-plus",
    ]
    standalone = parser.parse_args(["preprocess", *common_flags])
    linked = parser.parse_args(["run", *common_flags, "--preprocess"])

    standalone_config = main._preprocess_config_from_args(
        standalone,
        work_dir=tmp_path,
    )
    linked_config = main._preprocess_config_from_args(
        linked,
        work_dir=tmp_path,
    )

    assert standalone_config == linked_config


def test_standalone_preprocess_rejects_a_missing_words_file(
    tmp_path: Path, capsys
) -> None:
    source = tmp_path / "input.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello.\n\n",
        encoding="utf-8",
    )
    output = tmp_path / "out"
    args = main.build_parser().parse_args(
        [
            "preprocess",
            "--srt",
            str(source),
            "--words",
            str(tmp_path / "missing.json"),
            "--out",
            str(output),
        ]
    )

    assert main.cmd_preprocess(args) == 2
    assert not output.exists()
    assert "words file not found" in capsys.readouterr().err


def test_run_preprocess_rejects_a_missing_words_file_before_dispatch(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    dispatched = False

    def fake_dispatch(*_args, **_kwargs):
        nonlocal dispatched
        dispatched = True
        return 0

    monkeypatch.setattr(main, "_dispatch", fake_dispatch)
    monkeypatch.setattr(main, "_default_out", lambda _prefix: tmp_path / "out")
    args = main.build_parser().parse_args(
        [
            "run",
            "--srt",
            "input.srt",
            "--model",
            "qwen3.7-plus",
            "--preprocess",
            "--words",
            str(tmp_path / "missing.json"),
        ]
    )

    assert main.cmd_run(args) == 2
    assert dispatched is False
    assert "words file not found" in capsys.readouterr().err


def test_standalone_optimize_requires_model_before_writing(
    tmp_path: Path, capsys
) -> None:
    source = tmp_path / "input.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello.\n\n",
        encoding="utf-8",
    )
    output = tmp_path / "out"
    args = main.build_parser().parse_args(
        [
            "preprocess",
            "--srt",
            str(source),
            "--optimize",
            "--out",
            str(output),
        ]
    )

    assert main.cmd_preprocess(args) == 2
    assert not output.exists()
    assert "--optimize requires --model" in capsys.readouterr().err


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


def test_bench_all_uses_one_profile_without_inline_experiment_overrides() -> None:
    parser = main.build_parser()

    args = parser.parse_args(
        ["bench", "--all", "--profile", "bench-profile.yaml"]
    )

    assert args.bench_all is True
    assert args.profile == "bench-profile.yaml"
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "bench",
                "--all",
                "--profile",
                "bench-profile.yaml",
                "--models",
                "qwen3.7-plus",
            ]
        )
