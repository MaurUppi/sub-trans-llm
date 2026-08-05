from __future__ import annotations

import argparse

import model_client
from main import _sampling_from_args, warn_if_both_sampling


def test_default_is_omit_and_ignores_env(monkeypatch):
    """默认不发 temperature/top_p，且不再受 .env 影响。"""
    monkeypatch.setenv("DEFAULT_TEMPERATURE", "0.3")
    monkeypatch.setenv("DEFAULT_TOP_P", "0.7")
    assert model_client._resolve_sampling_param(None, "DEFAULT_TEMPERATURE") is None
    assert model_client._resolve_sampling_param(None, "DEFAULT_TOP_P") is None


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
