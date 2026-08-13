from __future__ import annotations

import inspect
from types import SimpleNamespace

import main
import model_client
import pytest
from main import build_parser as build_main_parser
from pipeline import batch_client, orchestrator, repair, summary
from pipeline.models import BatchOutcome, ValidateReport
from pipeline.preprocess.config import PreprocessConfig
from pipeline.preprocess import deliver, vc_optimize_adapter, vc_split_adapter


class _Recorder:
    def __init__(self, response):
        self.response = response
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _fake_client(*, chat_response=None, responses_response=None):
    chat = _Recorder(chat_response)
    responses = _Recorder(responses_response)
    return SimpleNamespace(
        chat=SimpleNamespace(completions=chat),
        responses=responses,
    ), chat, responses


def _patch_model(monkeypatch, client, *, provider="ali") -> None:
    monkeypatch.setattr(
        model_client,
        "resolve_model",
        lambda _name: {
            "provider": provider,
            "model": "provider-model-id",
            "base_url": "https://example.invalid/v1",
            "api_key_env": "TEST_API_KEY",
            "thinking": provider,
            "alias": "test-alias",
        },
    )
    monkeypatch.setattr(model_client, "_build_client", lambda _cfg, timeout: client)


def test_api_mode_normalization_accepts_cli_spellings() -> None:
    assert model_client.normalize_api_mode("ChatCompletion") == "chat_completions"
    assert model_client.normalize_api_mode("chat-completions") == "chat_completions"
    assert model_client.normalize_api_mode("Response") == "responses"
    assert model_client.normalize_api_mode("responses_api") == "responses"


def test_call_defaults_to_chat_completions_and_maps_result(monkeypatch) -> None:
    chat_response = SimpleNamespace(
        model="provider-model-id",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="OK"),
                finish_reason="stop",
            )
        ],
        usage={
            "prompt_tokens": 11,
            "completion_tokens": 2,
            "completion_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 13,
        },
    )
    client, chat, responses = _fake_client(chat_response=chat_response)
    _patch_model(monkeypatch, client, provider="ali")

    result = model_client.call(
        "test-alias",
        "hello",
        instructions="system rules",
        max_output_tokens=64,
    )

    assert len(chat.calls) == 1
    assert responses.calls == []
    assert chat.calls[0] == {
        "model": "provider-model-id",
        "messages": [
            {"role": "system", "content": "system rules"},
            {"role": "user", "content": "hello"},
        ],
        "max_completion_tokens": 64,
        "extra_body": {"enable_thinking": False},
    }
    assert result.text == "OK"
    assert result.status == "completed"
    assert result.incomplete_reason is None
    assert result.max_output_tokens == 64
    assert result.api_mode == "chat_completions"
    assert result.usage.input_tokens == 11
    assert result.usage.output_tokens == 2
    assert result.usage.reasoning_tokens == 0
    assert result.usage.total_tokens == 13


def test_chat_run_injects_csv_glossary_into_system_message(
    sample_srt, tmp_path, monkeypatch
) -> None:
    glossary = tmp_path / "glossary.csv"
    glossary.write_text(
        "\ufeffsource,target,note\n"
        "Hello world,你好世界,本测试固定译名\n",
        encoding="utf-8",
    )
    chat_response = SimpleNamespace(
        model="provider-model-id",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='{"0":{"src":"Hello world","tr":"你好世界"}}'
                ),
                finish_reason="stop",
            )
        ],
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )
    client, chat, _responses = _fake_client(chat_response=chat_response)
    _patch_model(monkeypatch, client, provider="ali")
    args = build_main_parser().parse_args(
        [
            "run",
            "--srt",
            str(sample_srt),
            "--model",
            "test-alias",
            "--glossary",
            str(glossary),
            "--no-summary",
            "--max-cues",
            "1",
            "--batch-size",
            "1",
            "--max-output-tokens",
            "8192",
            "--timeout",
            "300",
        ]
    )

    result = main._run_one("test-alias", args, tmp_path / "out")

    assert result.ok is True
    system_message = chat.calls[0]["messages"][0]
    assert system_message["role"] == "system"
    assert "## 专有名词（必须遵守，不得另译）" in system_message["content"]
    assert "Hello world = 你好世界（本测试固定译名）" in system_message["content"]


def test_call_can_select_responses_and_preserves_existing_wire_shape(monkeypatch) -> None:
    responses_response = SimpleNamespace(
        model="provider-model-id",
        output_text="OK",
        output=[],
        status="completed",
        incomplete_details=None,
        max_output_tokens=64,
        usage={
            "input_tokens": 7,
            "output_tokens": 2,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 9,
        },
    )
    client, chat, responses = _fake_client(responses_response=responses_response)
    _patch_model(monkeypatch, client, provider="ali")

    result = model_client.call(
        "test-alias",
        "hello",
        instructions="system rules",
        max_output_tokens=64,
        api_mode="Response",
    )

    assert chat.calls == []
    assert responses.calls == [
        {
            "model": "provider-model-id",
            "input": "hello",
            "instructions": "system rules",
            "max_output_tokens": 64,
            "reasoning": {"effort": "none"},
        }
    ]
    assert result.text == "OK"
    assert result.status == "completed"
    assert result.api_mode == "responses"
    assert result.usage.input_tokens == 7
    assert result.usage.output_tokens == 2


def test_chat_length_finish_reason_maps_to_incomplete(monkeypatch) -> None:
    chat_response = SimpleNamespace(
        model="provider-model-id",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="partial"),
                finish_reason="length",
            )
        ],
        usage={"prompt_tokens": 4, "completion_tokens": 8, "total_tokens": 12},
    )
    client, _chat, _responses = _fake_client(chat_response=chat_response)
    _patch_model(monkeypatch, client, provider="ark")

    result = model_client.call("test-alias", "hello", max_output_tokens=8)

    assert result.status == "incomplete"
    assert result.incomplete_reason == "length"
    assert result.ok is False


def test_both_clis_default_to_chat_and_accept_apimode_option() -> None:
    main_default = build_main_parser().parse_args(
        ["smoke", "--srt", "path/to/input.srt"]
    )
    main_responses = build_main_parser().parse_args(
        [
            "smoke",
            "--srt",
            "path/to/input.srt",
            "--APImode",
            "Responses",
        ]
    )
    client_default = model_client.build_parser().parse_args([])
    client_responses = model_client.build_parser().parse_args(
        ["--APImode", "Response"]
    )

    assert main_default.api_mode == "chat_completions"
    assert main_responses.api_mode == "responses"
    assert client_default.api_mode == "chat_completions"
    assert client_responses.api_mode == "responses"


def test_run_accepts_explicit_output_file_and_rejects_legacy_out_option() -> None:
    parser = build_main_parser()
    args = parser.parse_args(
        [
            "run",
            "--srt",
            "path/to/input.srt",
            "--model",
            "qwen3.7-plus",
            "--output",
            "path/to/xyz.srt",
        ]
    )

    assert args.output == "path/to/xyz.srt"
    assert args.out is None
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "run",
                "--srt",
                "path/to/input.srt",
                "--model",
                "qwen3.7-plus",
                "--output",
                "path/to/xyz.srt",
                "--out",
                "legacy-run-dir",
            ]
        )


def test_run_defaults_to_source_adjacent_output() -> None:
    args = build_main_parser().parse_args(
        [
            "run",
            "--srt",
            "path/to/input.srt",
            "--model",
            "qwen3.7-plus",
        ]
    )

    assert args.output is None
    assert args.api_mode == "chat_completions"
    assert args.source_language == "英语"
    assert args.target_language == "简体中文"


def test_run_applies_production_defaults_and_allows_explicit_overrides(
    tmp_path, monkeypatch
) -> None:
    captured: list[dict[str, object]] = []

    def fake_dispatch(models, args, out_dir):
        captured.append(
            {
                "models": models,
                "batch_jobs": args.batch_jobs,
                "max_output_tokens": args.max_output_tokens,
                "timeout": args.timeout,
                "max_retries": args.max_retries,
                "retry_backoff": args.retry_backoff,
                "out_dir": out_dir,
            }
        )
        model_dir = out_dir / models[0]
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "bilingual.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n你好。\nHello.\n",
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(main, "_dispatch", fake_dispatch)
    monkeypatch.setattr(main, "_default_out", lambda _prefix: tmp_path / "internal")

    defaults = build_main_parser().parse_args(
        [
            "run",
            "--srt",
            str(tmp_path / "input.srt"),
            "--model",
            "qwen3.7-plus",
            "--output",
            str(tmp_path / "a.srt"),
        ]
    )
    explicit = build_main_parser().parse_args(
        [
            "run",
            "--srt",
            str(tmp_path / "input.srt"),
            "--model",
            "qwen3.7-plus",
            "--batch-jobs",
            "4",
            "--max-output-tokens",
            "16384",
            "--timeout",
            "600",
            "--max-retries",
            "5",
            "--retry-backoff",
            "7",
            "--output",
            str(tmp_path / "b.srt"),
        ]
    )

    assert main.cmd_run(defaults) == 0
    assert main.cmd_run(explicit) == 0
    assert captured[0] == {
        "models": ["qwen3.7-plus"],
        "batch_jobs": 1,
        "max_output_tokens": 8192,
        "timeout": 300.0,
        "max_retries": 2,
        "retry_backoff": 3.0,
        "out_dir": tmp_path / "internal",
    }
    assert captured[1]["batch_jobs"] == 4
    assert captured[1]["max_output_tokens"] == 16384
    assert captured[1]["timeout"] == 600.0
    assert captured[1]["max_retries"] == 5
    assert captured[1]["retry_backoff"] == 7.0


def test_run_success_keeps_only_default_delivery_and_removes_workspace(
    tmp_path, monkeypatch, capsys
) -> None:
    source = tmp_path / "episode.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello.\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "internal"
    bilingual = (
        "1\n00:00:00,000 --> 00:00:01,000\n你好。\nHello.\n"
    )

    def fake_dispatch(models, _args, out_dir):
        model_dir = out_dir / models[0]
        model_dir.mkdir(parents=True)
        (model_dir / "bilingual.srt").write_text(bilingual, encoding="utf-8")
        (out_dir / "summary.json").write_text("{}", encoding="utf-8")
        return 0

    monkeypatch.setattr(main, "_dispatch", fake_dispatch)
    monkeypatch.setattr(main, "_default_out", lambda _prefix: workspace)
    args = build_main_parser().parse_args(
        ["run", "--srt", str(source), "--model", "qwen3.7-plus"]
    )

    assert main.cmd_run(args) == 0
    assert source.with_name("episode_zh.srt").read_text(encoding="utf-8") == bilingual
    assert not workspace.exists()
    output = capsys.readouterr().out
    assert "workspace=" in output
    assert " out=" not in output


def test_run_failure_preserves_workspace_for_repair(tmp_path, monkeypatch, capsys) -> None:
    source = tmp_path / "episode.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello.\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "internal"

    def fake_dispatch(_models, _args, out_dir):
        out_dir.mkdir(parents=True)
        (out_dir / "failure.txt").write_text("failed", encoding="utf-8")
        return 1

    monkeypatch.setattr(main, "_dispatch", fake_dispatch)
    monkeypatch.setattr(main, "_default_out", lambda _prefix: workspace)
    args = build_main_parser().parse_args(
        ["run", "--srt", str(source), "--model", "qwen3.7-plus"]
    )

    assert main.cmd_run(args) == 1
    assert (workspace / "failure.txt").is_file()
    output = capsys.readouterr().out
    assert "失败证据" in output
    assert str(workspace) in output


def test_run_preprocess_uses_and_cleans_same_temporary_workspace(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "episode.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello.\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "internal"
    resolved_paths = []

    def fake_dispatch(models, args, out_dir):
        resolved_paths.append(args._resolved_srt)
        model_dir = out_dir / models[0]
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "bilingual.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n你好。\nHello.\n",
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(main, "_dispatch", fake_dispatch)
    monkeypatch.setattr(main, "_default_out", lambda _prefix: workspace)
    args = build_main_parser().parse_args(
        [
            "run",
            "--srt",
            str(source),
            "--model",
            "qwen3.7-plus",
            "--preprocess",
            "--no-resplit",
        ]
    )

    assert main.cmd_run(args) == 0
    assert resolved_paths[0].parent == workspace / "_preprocess"
    assert not (tmp_path / ".preprocess_episode").exists()
    assert not workspace.exists()


def test_run_delivery_failure_preserves_workspace_and_returns_failure(
    tmp_path, monkeypatch, capsys
) -> None:
    source = tmp_path / "episode.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello.\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "internal"

    def fake_dispatch(models, _args, out_dir):
        model_dir = out_dir / models[0]
        model_dir.mkdir(parents=True)
        (model_dir / "bilingual.srt").write_text("translated", encoding="utf-8")
        return 0

    def fail_delivery(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(main, "_dispatch", fake_dispatch)
    monkeypatch.setattr(main, "_default_out", lambda _prefix: workspace)
    monkeypatch.setattr(deliver, "write_zh_srt", fail_delivery)
    args = build_main_parser().parse_args(
        ["run", "--srt", str(source), "--model", "qwen3.7-plus"]
    )

    assert main.cmd_run(args) == 1
    assert (workspace / "qwen3.7-plus" / "bilingual.srt").is_file()
    output = capsys.readouterr().out
    assert "交付失败" in output
    assert str(workspace) in output


def test_run_missing_delivery_is_failure_and_preserves_workspace(
    tmp_path, monkeypatch, capsys
) -> None:
    source = tmp_path / "episode.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello.\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "internal"

    def fake_dispatch(_models, _args, out_dir):
        out_dir.mkdir(parents=True)
        (out_dir / "summary.json").write_text("{}", encoding="utf-8")
        return 0

    monkeypatch.setattr(main, "_dispatch", fake_dispatch)
    monkeypatch.setattr(main, "_default_out", lambda _prefix: workspace)
    args = build_main_parser().parse_args(
        ["run", "--srt", str(source), "--model", "qwen3.7-plus"]
    )

    assert main.cmd_run(args) == 1
    assert (workspace / "summary.json").is_file()
    output = capsys.readouterr().out
    assert "未生成最终字幕" in output
    assert str(workspace) in output


def test_run_cleanup_failure_keeps_success_and_warns(tmp_path, monkeypatch, capsys) -> None:
    source = tmp_path / "episode.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello.\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "internal"

    def fake_dispatch(models, _args, out_dir):
        model_dir = out_dir / models[0]
        model_dir.mkdir(parents=True)
        (model_dir / "bilingual.srt").write_text("translated", encoding="utf-8")
        return 0

    def fail_cleanup(_path):
        raise OSError("busy")

    monkeypatch.setattr(main, "_dispatch", fake_dispatch)
    monkeypatch.setattr(main, "_default_out", lambda _prefix: workspace)
    monkeypatch.setattr(main.shutil, "rmtree", fail_cleanup)
    args = build_main_parser().parse_args(
        ["run", "--srt", str(source), "--model", "qwen3.7-plus"]
    )

    assert main.cmd_run(args) == 0
    assert source.with_name("episode_zh.srt").is_file()
    assert workspace.is_dir()
    output = capsys.readouterr().out
    assert "临时运行目录清理失败" in output
    assert str(workspace) in output


def test_run_dispatch_exception_preserves_workspace_for_repair(
    tmp_path, monkeypatch, capsys
) -> None:
    source = tmp_path / "episode.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello.\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "internal"

    def fail_dispatch(_models, _args, out_dir):
        out_dir.mkdir(parents=True)
        (out_dir / "failure.txt").write_text("failed", encoding="utf-8")
        raise RuntimeError("provider crashed")

    monkeypatch.setattr(main, "_dispatch", fail_dispatch)
    monkeypatch.setattr(main, "_default_out", lambda _prefix: workspace)
    args = build_main_parser().parse_args(
        ["run", "--srt", str(source), "--model", "qwen3.7-plus"]
    )

    assert main.cmd_run(args) == 1
    assert (workspace / "failure.txt").is_file()
    output = capsys.readouterr().out
    assert "运行失败" in output
    assert str(workspace) in output


def test_run_preprocess_exception_preserves_same_workspace(
    tmp_path, monkeypatch, capsys
) -> None:
    source = tmp_path / "episode.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello.\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "internal"

    def fail_preprocess(_args, *, work_dir):
        work_dir.mkdir(parents=True)
        (work_dir / "failure.txt").write_text("failed", encoding="utf-8")
        raise RuntimeError("optimize failed")

    monkeypatch.setattr(main, "_maybe_preprocess", fail_preprocess)
    monkeypatch.setattr(main, "_default_out", lambda _prefix: workspace)
    args = build_main_parser().parse_args(
        [
            "run",
            "--srt",
            str(source),
            "--model",
            "qwen3.7-plus",
            "--preprocess",
            "--optimize",
        ]
    )

    assert main.cmd_run(args) == 1
    assert (workspace / "_preprocess" / "failure.txt").is_file()
    output = capsys.readouterr().out
    assert "前处理失败" in output
    assert str(workspace) in output


def test_all_model_calling_layers_accept_api_mode() -> None:
    for fn in (
        batch_client.call_one_batch,
        summary.generate_episode_summary,
        orchestrator.run_once,
        repair.repair_run_dir,
        vc_optimize_adapter.optimize_document,
        vc_split_adapter.resplit_with_vc,
    ):
        assert "api_mode" in inspect.signature(fn).parameters, fn.__qualname__

    assert "api_mode" in PreprocessConfig.__dataclass_fields__


def test_run_once_propagates_api_mode_and_persists_it(
    sample_srt, tmp_path, monkeypatch
) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text(
        "Translate ${sourceLanguage} to ${targetLanguage}.",
        encoding="utf-8",
    )
    summary_modes: list[str] = []
    batch_modes: list[str] = []

    def fake_generate_summary(*args, **kwargs):
        summary_modes.append(kwargs["api_mode"])
        return "episode context", model_client.Usage(), "completed", None

    def fake_batch(**kwargs):
        batch_modes.append(kwargs["api_mode"])
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

    result = orchestrator.run_once(
        srt_path=sample_srt,
        model="test-alias",
        prompt_path=prompt,
        out_dir=tmp_path / "out",
        max_cues=2,
        batch_size=1,
        api_mode="Responses",
    )

    assert summary_modes == ["responses"]
    assert batch_modes == ["responses", "responses"]
    assert result.api_mode == "responses"
    assert result.meta_dict()["api_mode"] == "responses"


def _summary_response_ok(api_mode: str):
    if api_mode == "chat_completions":
        return SimpleNamespace(
            model="provider-model-id",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="本集摘要"),
                    finish_reason="stop",
                )
            ],
            usage={"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
        )
    return SimpleNamespace(
        model="provider-model-id",
        output_text="本集摘要",
        output=[],
        status="completed",
        incomplete_details=None,
        max_output_tokens=64,
        usage={
            "input_tokens": 8,
            "output_tokens": 4,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 12,
        },
    )


@pytest.mark.parametrize("api_mode", ["chat_completions", "responses"])
def test_summary_request_follows_source_language_and_glossary_in_both_api_modes(
    monkeypatch, glossary_path, api_mode
) -> None:
    chat_response = _summary_response_ok("chat_completions")
    responses_response = _summary_response_ok("responses")
    client, chat, responses = _fake_client(
        chat_response=chat_response,
        responses_response=responses_response,
    )
    _patch_model(monkeypatch, client, provider="ali")

    result_summary, _usage, status, err = summary.generate_episode_summary(
        "test-alias",
        [SimpleNamespace(id="0", text="Bonjour")],
        source_language="法语",
        glossary_path=glossary_path,
        max_output_tokens=64,
        api_mode=api_mode,
    )

    assert status == "completed"
    assert err is None
    assert result_summary == "本集摘要"

    if api_mode == "chat_completions":
        assert responses.calls == []
        instructions = chat.calls[0]["messages"][0]["content"]
        assert chat.calls[0]["messages"][0]["role"] == "system"
    else:
        assert chat.calls == []
        instructions = responses.calls[0]["instructions"]

    assert "一整集法语字幕" in instructions
    assert "一整集英文字幕" not in instructions
    assert "## 专有名词（必须遵守，不得另译）" in instructions
    assert "Daniel Larcher = 达尼埃尔·拉尔谢（市长）" in instructions
