from __future__ import annotations

import argparse
import inspect

import model_client
from main import DEFAULT_SRT, _sampling_from_args, warn_if_both_sampling
from pipeline import orchestrator
from pipeline.models import BatchOutcome, ValidateReport


def test_default_is_omit_and_ignores_env(monkeypatch):
    """默认不发 temperature/top_p，且不再受 .env 影响。"""
    monkeypatch.setenv("DEFAULT_TEMPERATURE", "0.3")
    monkeypatch.setenv("DEFAULT_TOP_P", "0.7")
    assert model_client._resolve_sampling_param(None, "DEFAULT_TEMPERATURE") is None
    assert model_client._resolve_sampling_param(None, "DEFAULT_TOP_P") is None


def test_cli_default_srt_is_current_e03_sample() -> None:
    assert DEFAULT_SRT.name == "A.French.Village.S01E03_eng.srt"
    assert DEFAULT_SRT.parent.name == "sample"
    assert DEFAULT_SRT.is_file()


def test_explicit_value_is_sent(monkeypatch):
    monkeypatch.setenv("DEFAULT_TEMPERATURE", "0.3")
    assert model_client._resolve_sampling_param(0.9, "DEFAULT_TEMPERATURE") == 0.9
    assert model_client._resolve_sampling_param(0, "DEFAULT_TEMPERATURE") == 0.0


def test_omit_sentinel_still_means_omit():
    assert model_client._resolve_sampling_param(model_client.OMIT, "X") is None


def test_cli_default_omits_both():
    args = argparse.Namespace(temperature=None, top_p=None)
    t, p = _sampling_from_args(args)
    assert t is model_client.OMIT
    assert p is model_client.OMIT


def test_cli_explicit_values_pass_through():
    args = argparse.Namespace(temperature=0.2, top_p=0.8)
    t, p = _sampling_from_args(args)
    assert t == 0.2
    assert p == 0.8


def test_cli_zero_is_not_treated_as_absent():
    """0.0 是合法采样值，不能被当成「没给」。"""
    args = argparse.Namespace(temperature=0.0, top_p=0.0)
    t, p = _sampling_from_args(args)
    assert t == 0.0
    assert p == 0.0


# ---- 官方建议：temperature 与 top_p 只设其一（阿里云/OpenAI 一致口径）----
# 见 docs/阿里云-OpenAI兼容-Responses创建响应.md L85-86 参数表。
# 采用「警告不阻断」：官方 SCENARIO_CONFIGS 自身同时给两个值，硬互斥会
# 让官方翻译配置(0.3/0.8)无法复现，也会砍掉消融实验的一个自由度。


def test_warn_when_both_sampling_params_given(capsys):
    args = argparse.Namespace(temperature=0.3, top_p=0.8)
    warn_if_both_sampling(args)
    err = capsys.readouterr().err
    assert "temperature" in err and "top_p" in err


def test_no_warn_when_only_one_given(capsys):
    warn_if_both_sampling(argparse.Namespace(temperature=0.3, top_p=None))
    warn_if_both_sampling(argparse.Namespace(temperature=None, top_p=0.8))
    assert capsys.readouterr().err == ""


def test_no_warn_when_neither_given(capsys):
    warn_if_both_sampling(argparse.Namespace(temperature=None, top_p=None))
    assert capsys.readouterr().err == ""


def test_warn_counts_zero_as_given(capsys):
    """0 是合法值，不能当成'没给'。"""
    warn_if_both_sampling(argparse.Namespace(temperature=0.0, top_p=0.9))
    assert capsys.readouterr().err != ""


def test_pipeline_sampling_evidence_builder_is_available():
    assert callable(getattr(orchestrator, "_sampling_evidence", None))


def test_pipeline_sampling_evidence_preserves_sent_and_omitted_intent():
    assert orchestrator._sampling_evidence(model_client.OMIT, 0.8) == {
        "temperature": {"sent": False, "value": None},
        "top_p": {"sent": True, "value": 0.8},
    }
    assert orchestrator._sampling_evidence(0.0, model_client.OMIT) == {
        "temperature": {"sent": True, "value": 0.0},
        "top_p": {"sent": False, "value": None},
    }


def test_run_once_accepts_a_frozen_episode_summary():
    assert "episode_summary_override" in inspect.signature(
        orchestrator.run_once
    ).parameters


def test_frozen_episode_summary_is_reused_without_model_call(
    sample_srt, tmp_path, monkeypatch
):
    prompt = tmp_path / "prompt.md"
    prompt.write_text(
        "Translate ${sourceLanguage} to ${targetLanguage}.",
        encoding="utf-8",
    )
    summary_calls = 0

    def fake_generate_summary(*args, **kwargs):
        nonlocal summary_calls
        summary_calls += 1
        return "generated summary", model_client.Usage(), "completed", None

    def fake_batch(**kwargs):
        cues = kwargs["batch_cues"]
        parsed = {
            cue.id: {"src": cue.text, "tr": f"译{cue.id}"}
            for cue in cues
        }
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

    monkeypatch.setattr(
        orchestrator,
        "generate_episode_summary",
        fake_generate_summary,
    )
    monkeypatch.setattr(orchestrator, "call_one_batch", fake_batch)

    result = orchestrator.run_once(
        srt_path=sample_srt,
        model="test-alias",
        prompt_path=prompt,
        out_dir=tmp_path / "out",
        batch_size=50,
        batch_jobs=1,
        episode_summary_override="frozen context",
    )

    assert summary_calls == 0
    assert result.episode_summary == "frozen context"
    assert "frozen context" in result.instructions
    assert result.summary_usage is None
    assert (tmp_path / "out" / "episode_summary.txt").read_text(
        encoding="utf-8"
    ).strip() == "frozen context"
