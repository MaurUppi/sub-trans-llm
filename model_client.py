"""
统一模型调用模块（OpenAI SDK + Chat Completions / Responses API）

配置全部来自项目根目录 `.env`（见 `.env.example`），脚本内不硬编码密钥。

支持 6 个模型（alias → .env 中的 MODEL_*）：
  Ark:    deepseek-v4-flash / deepseek-v4-pro / doubao-seed-2-1-turbo
  Aliyun: qwen3.7-plus / qwen3.7-max / qwen3.8-max

约定：
  - 强制关闭思考
  - temperature / top_p：**默认不传**；只有显式给值才写进请求体
  - 默认 Chat Completions API；可显式切换 Responses API
  - max_output_tokens 可选

用法::

    from model_client import call, list_models, OMIT

    r = call("deepseek-v4-flash", "Reply with exactly: OK", max_output_tokens=16)
    # 默认就不传采样参数（服务端默认）；要调就显式给值：
    # r = call(..., temperature=0.2, top_p=0.8)
    print(r.text, r.usage)
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from openai import OpenAI

# ---------------------------------------------------------------------------
# Sampling: explicit value = send it | None / OMIT = never send (provider default)
# ---------------------------------------------------------------------------


class _OmitType:
    """Sentinel: do not include this sampling field in the API request."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "OMIT"


OMIT = _OmitType()
SamplingArg = Union[float, None, _OmitType]

API_MODE_CHAT_COMPLETIONS = "chat_completions"
API_MODE_RESPONSES = "responses"
DEFAULT_API_MODE = API_MODE_CHAT_COMPLETIONS


def normalize_api_mode(value: str) -> str:
    """Normalize user-facing API mode spellings to stable metadata values."""
    key = "".join(ch for ch in str(value).strip().lower() if ch.isalnum())
    if key in {"chat", "chatcompletion", "chatcompletions", "chatcompletionapi"}:
        return API_MODE_CHAT_COMPLETIONS
    if key in {"response", "responses", "responseapi", "responsesapi"}:
        return API_MODE_RESPONSES
    raise ValueError(
        f"未知 API 模式 {value!r}；可用 ChatCompletion 或 Responses"
    )

# ---------------------------------------------------------------------------
# 加载 .env（不依赖系统全局环境，优先项目根目录）
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parent
_ENV_PATH = _ROOT / ".env"


def _load_dotenv() -> None:
    """轻量加载 .env；若已安装 python-dotenv 则用之，否则手写解析。"""
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(_ENV_PATH, override=False)
        return
    except ImportError:
        pass

    if not _ENV_PATH.is_file():
        return
    for raw in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        # 不覆盖已有环境变量
        os.environ.setdefault(key, val)


_load_dotenv()


def _require_env(name: str) -> str:
    val = (os.environ.get(name) or "").strip()
    if not val:
        raise RuntimeError(
            f"缺少环境变量 {name}。请在 {_ENV_PATH} 中配置"
            f"（可参考 .env.example），或 export {name}=..."
        )
    return val


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    val = os.environ.get(name)
    if val is None or str(val).strip() == "":
        return default
    return str(val).strip()


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if raw is None:
        return default
    return float(raw)


def _env_float_optional(name: str) -> Optional[float]:
    """Read float from env; treat omit/none/empty as None (do not send to API)."""
    raw = _env(name)
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in ("", "omit", "none", "api", "default"):
        return None
    return float(raw)


def _env_int_optional(name: str) -> Optional[int]:
    raw = _env(name)
    if raw is None:
        return None
    return int(raw)


def _resolve_sampling_param(
    explicit: SamplingArg,
    env_name: str,  # noqa: ARG001 — 保留形参以免调用点大改；已不再读取 env
) -> Optional[float]:
    """
    Resolve temperature / top_p to a value sent on the wire, or None to omit.

    **默认不发送**：只有显式传入数值才会写进请求体，其余一律 None（走服务端默认）。

    - explicit float → that value（``0`` 也是合法值，会照发）
    - ``None`` / ``OMIT`` → None，字段不进请求体

    历史行为是「未传则读 .env ``DEFAULT_TEMPERATURE``/``DEFAULT_TOP_P``，缺省 1.0」。
    已废弃：默认值散落在 .env 里会让六模型对比实验的采样条件变得不可见且易漂移，
    要调采样就在命令行显式写出来。
    """
    if explicit is OMIT or explicit is None:
        return None
    return float(explicit)


# 阿里云 Responses API：max_output_tokens 下限为 16（实测 5 会 400）
ALI_MIN_MAX_OUTPUT_TOKENS = 16

# alias -> 静态元数据（不含密钥与 model id）
_MODEL_META: dict[str, dict[str, Any]] = {
    "deepseek-v4-flash": {
        "provider": "ark",
        "model_env": "MODEL_DEEPSEEK_V4_FLASH",
        "thinking": "ark",
    },
    "deepseek-v4-pro": {
        "provider": "ark",
        "model_env": "MODEL_DEEPSEEK_V4_PRO",
        "thinking": "ark",
    },
    "doubao-seed-2-1-turbo": {
        "provider": "ark",
        "model_env": "MODEL_DOUBAO_SEED_2_1_TURBO",
        "thinking": "ark",
    },
    "qwen3.7-plus": {
        "provider": "ali",
        "model_env": "MODEL_QWEN37_PLUS",
        "thinking": "ali",
    },
    "qwen3.7-max": {
        "provider": "ali",
        "model_env": "MODEL_QWEN37_MAX",
        "thinking": "ali",
    },
    "qwen3.8-max": {
        "provider": "ali",
        "model_env": "MODEL_QWEN38_MAX",
        "thinking": "ali",
    },
}


def _resolved_models() -> dict[str, dict[str, Any]]:
    """按当前 .env 解析完整模型表（每次调用读取，便于改 .env 后热生效）。"""
    ark_base = _require_env("ARK_BASE_URL").rstrip("/")
    ali_base = _require_env("ALI_BASE_URL").rstrip("/")
    # 若误写成 .../responses，剥掉末段
    for label, base in (("ARK_BASE_URL", ark_base), ("ALI_BASE_URL", ali_base)):
        if base.endswith("/responses"):
            fixed = base[: -len("/responses")]
            if label == "ARK_BASE_URL":
                ark_base = fixed
            else:
                ali_base = fixed

    models: dict[str, dict[str, Any]] = {}
    for alias, meta in _MODEL_META.items():
        provider = meta["provider"]
        cfg = {
            "provider": provider,
            "model": _require_env(meta["model_env"]),
            "base_url": ark_base if provider == "ark" else ali_base,
            "api_key_env": "ARK_API_KEY" if provider == "ark" else "ALI_API_KEY",
            "thinking": meta["thinking"],
            "alias": alias,
        }
        models[alias] = cfg
        # 也允许用完整 model id 调用
        models.setdefault(cfg["model"], cfg)
    return models


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_response(cls, usage: Any) -> "Usage":
        if usage is None:
            return cls()
        if hasattr(usage, "model_dump"):
            data = usage.model_dump()
        elif isinstance(usage, dict):
            data = dict(usage)
        else:
            data = vars(usage)
        details = (
            data.get("output_tokens_details")
            or data.get("completion_tokens_details")
            or {}
        )
        return cls(
            input_tokens=int(
                data.get("input_tokens") or data.get("prompt_tokens") or 0
            ),
            output_tokens=int(
                data.get("output_tokens") or data.get("completion_tokens") or 0
            ),
            reasoning_tokens=int(details.get("reasoning_tokens") or 0),
            total_tokens=int(data.get("total_tokens") or 0),
            raw=data,
        )


@dataclass
class ModelResult:
    """一次 Chat Completions 或 Responses 调用的统一结果。"""

    text: str
    model: str
    alias: str
    status: str
    usage: Usage
    max_output_tokens: Optional[int]
    api_mode: str = DEFAULT_API_MODE
    incomplete_reason: Optional[str] = None
    raw: Any = None

    @property
    def ok(self) -> bool:
        return self.status == "completed" and bool(self.text)


def list_models() -> list[str]:
    """返回可用的短 alias 列表。"""
    return list(_MODEL_META.keys())


def resolve_model(name: str) -> dict[str, Any]:
    models = _resolved_models()
    if name not in models:
        known = ", ".join(list_models())
        raise KeyError(f"未知模型 {name!r}。可用: {known}")
    return models[name]


def _extract_text(resp: Any) -> str:
    text = getattr(resp, "output_text", None)
    if text:
        return text
    parts: list[str] = []
    for item in getattr(resp, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        for c in getattr(item, "content", None) or []:
            if getattr(c, "type", None) == "output_text":
                parts.append(getattr(c, "text", "") or "")
    return "".join(parts)


def _extract_chat_text(resp: Any) -> str:
    choices = getattr(resp, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for item in content or []:
        if isinstance(item, dict):
            text = item.get("text")
        else:
            text = getattr(item, "text", None)
        if text:
            parts.append(str(text))
    return "".join(parts)


def _chat_messages(
    input: str | list[dict[str, Any]], instructions: Optional[str]
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if instructions is not None:
        messages.append({"role": "system", "content": instructions})
    if isinstance(input, str):
        messages.append({"role": "user", "content": input})
    else:
        messages.extend(dict(item) for item in input)
    return messages


def _build_client(cfg: dict[str, Any], timeout: float) -> OpenAI:
    api_key = _require_env(cfg["api_key_env"])
    return OpenAI(api_key=api_key, base_url=cfg["base_url"], timeout=timeout)


def call(
    model: str,
    input: str | list[dict[str, Any]],
    *,
    instructions: Optional[str] = None,
    temperature: SamplingArg = None,
    top_p: SamplingArg = None,
    max_output_tokens: Optional[int] = None,
    timeout: float = 120.0,
    extra: Optional[dict[str, Any]] = None,
    api_mode: str = DEFAULT_API_MODE,
) -> ModelResult:
    """
    调用指定模型；默认 Chat Completions，可切换 Responses。

    Parameters
    ----------
    model :
        短 alias（如 ``deepseek-v4-flash``）或完整 model id。
    input :
        字符串，或 OpenAI Responses 消息数组。
    instructions :
        可选系统指令（Responses 的 instructions 字段）。
    temperature / top_p :
        采样参数。只有显式传入数值才写入请求；``None`` 或 ``OMIT``
        均省略字段并使用服务端默认值。旧的 ``DEFAULT_TEMPERATURE`` /
        ``DEFAULT_TOP_P`` 环境变量不再参与解析。
    max_output_tokens :
        输出上限（含 reasoning tokens）。Chat 映射为 ``max_completion_tokens``，
        Responses 使用 ``max_output_tokens``。
        ``None`` 时先读 ``DEFAULT_MAX_OUTPUT_TOKENS``，仍为空则不传（服务端默认）。
        阿里云若传入，最小值为 16。
    """
    api_mode = normalize_api_mode(api_mode)
    cfg = resolve_model(model)
    client = _build_client(cfg, timeout=timeout)

    temperature = _resolve_sampling_param(temperature, "DEFAULT_TEMPERATURE")
    top_p = _resolve_sampling_param(top_p, "DEFAULT_TOP_P")
    if max_output_tokens is None:
        max_output_tokens = _env_int_optional("DEFAULT_MAX_OUTPUT_TOKENS")

    kwargs: dict[str, Any] = {"model": cfg["model"]}
    if api_mode == API_MODE_CHAT_COMPLETIONS:
        kwargs["messages"] = _chat_messages(input, instructions)
    else:
        kwargs["input"] = input
    # Only send sampling params when resolved; None = provider/model default
    if temperature is not None:
        kwargs["temperature"] = temperature
    if top_p is not None:
        kwargs["top_p"] = top_p
    if instructions is not None and api_mode == API_MODE_RESPONSES:
        kwargs["instructions"] = instructions

    if max_output_tokens is not None:
        mot = int(max_output_tokens)
        if (
            api_mode == API_MODE_RESPONSES
            and cfg["provider"] == "ali"
            and mot < ALI_MIN_MAX_OUTPUT_TOKENS
        ):
            raise ValueError(
                f"阿里云 max_output_tokens 最小为 {ALI_MIN_MAX_OUTPUT_TOKENS}，"
                f"收到 {mot}"
            )
        kwargs[
            "max_completion_tokens"
            if api_mode == API_MODE_CHAT_COMPLETIONS
            else "max_output_tokens"
        ] = mot

    extra_body: dict[str, Any] = dict(extra or {})
    if cfg["thinking"] == "ark":
        extra_body.setdefault("thinking", {"type": "disabled"})
    elif cfg["thinking"] == "ali":
        if api_mode == API_MODE_CHAT_COMPLETIONS:
            extra_body.setdefault("enable_thinking", False)
        else:
            kwargs["reasoning"] = {"effort": "none"}

    if extra_body:
        kwargs["extra_body"] = extra_body

    if api_mode == API_MODE_CHAT_COMPLETIONS:
        resp = client.chat.completions.create(**kwargs)
        choices = getattr(resp, "choices", None) or []
        finish_reason = getattr(choices[0], "finish_reason", None) if choices else None
        incomplete_reason = finish_reason if finish_reason not in (None, "stop") else None
        status = "completed" if incomplete_reason is None else "incomplete"
        text = _extract_chat_text(resp)
        returned_max_tokens = max_output_tokens
    else:
        resp = client.responses.create(**kwargs)
        incomplete = getattr(resp, "incomplete_details", None)
        incomplete_reason = None
        if incomplete is not None:
            incomplete_reason = getattr(incomplete, "reason", None) or str(incomplete)
        status = getattr(resp, "status", "") or ""
        text = _extract_text(resp)
        returned_max_tokens = getattr(resp, "max_output_tokens", None)

    return ModelResult(
        text=text,
        model=getattr(resp, "model", None) or cfg["model"],
        alias=cfg.get("alias", model),
        status=status,
        usage=Usage.from_response(getattr(resp, "usage", None)),
        max_output_tokens=returned_max_tokens,
        api_mode=api_mode,
        incomplete_reason=incomplete_reason,
        raw=resp,
    )


def smoke_test(
    models: Optional[list[str]] = None,
    *,
    max_output_tokens: int = 16,
    prompt: str = "Reply with exactly: OK",
    api_mode: str = DEFAULT_API_MODE,
) -> list[ModelResult]:
    """用最少 token 验证模型可连通。"""
    results: list[ModelResult] = []
    for name in models or list_models():
        try:
            r = call(
                name,
                prompt,
                max_output_tokens=max_output_tokens,
                api_mode=api_mode,
            )
            results.append(r)
        except Exception as e:  # noqa: BLE001
            try:
                mid = resolve_model(name)["model"]
            except Exception:
                mid = name
            results.append(
                ModelResult(
                    text="",
                    model=mid,
                    alias=name,
                    status=f"error: {type(e).__name__}: {e}",
                    usage=Usage(),
                    max_output_tokens=max_output_tokens,
                    api_mode=normalize_api_mode(api_mode),
                )
            )
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="六模型 API 连通性烟测")
    parser.add_argument(
        "--APImode",
        "--api-mode",
        dest="api_mode",
        type=normalize_api_mode,
        default=DEFAULT_API_MODE,
        metavar="MODE",
        help="API 模式：ChatCompletion（默认）或 Responses",
    )
    parser.add_argument("--models", default="all", help="逗号分隔 alias，或 all")
    parser.add_argument("--max-output-tokens", type=int, default=16)
    parser.add_argument("--prompt", default="Reply with exactly: OK")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    models = None
    if args.models.strip().lower() not in {"all", "*"}:
        models = [name.strip() for name in args.models.split(",") if name.strip()]

    print(f"配置文件: {_ENV_PATH} (exists={_ENV_PATH.is_file()})")
    print(f"模型调用烟测 api_mode={args.api_mode}（最少 token）\n")
    rows = smoke_test(
        models,
        max_output_tokens=args.max_output_tokens,
        prompt=args.prompt,
        api_mode=args.api_mode,
    )
    ok_n = 0
    for r in rows:
        mark = "OK" if r.ok else "FAIL"
        if r.ok:
            ok_n += 1
        print(
            f"[{mark}] {r.alias:24s} model={r.model}\n"
            f"       status={r.status} text={r.text!r}\n"
            f"       tokens in/out/reason/total="
            f"{r.usage.input_tokens}/{r.usage.output_tokens}/"
            f"{r.usage.reasoning_tokens}/{r.usage.total_tokens}  "
            f"max_output_tokens={r.max_output_tokens}"
        )
        if r.incomplete_reason:
            print(f"       incomplete_reason={r.incomplete_reason}")
    print(f"\n通过 {ok_n}/{len(rows)}")
    return 0 if ok_n == len(rows) else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
