"""
字幕翻译模块：SRT → JSON input、外部文件拼 instructions、校验、双语 SRT。

约定见 docs/quality_control.md / docs/benchmark_plan.md。
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import model_client
from model_client import Usage

_ROOT = Path(__file__).resolve().parent
DEFAULT_PROMPT = _ROOT / "docs" / "translation_prompt.md"
DEFAULT_GLOSSARY = _ROOT / "docs" / "Un_Village_francais_Glossary.md"
DEFAULT_MAX_OUTPUT_TOKENS = 131072
DEFAULT_BATCH_SIZE = 50
DEFAULT_SUMMARY_MAX_OUTPUT_TOKENS = 2048

# 省略号
_ELLIPSIS_OK = "\u2026"  # …
_ELLIPSIS_BAD = "\u22ef"  # ⋯


@dataclass
class Cue:
    id: str
    seq: int
    start: str
    end: str
    text: str


@dataclass
class ValidateReport:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    parsed: Optional[dict[str, dict[str, str]]] = None
    stats: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "stats": self.stats,
            "parsed_keys": list(self.parsed.keys()) if self.parsed else [],
        }


@dataclass
class TranslateResult:
    model_alias: str
    model_id: str
    usage: Usage
    status: str
    incomplete_reason: Optional[str]
    validate: ValidateReport
    bilingual_srt: Optional[str]
    raw_text: str
    elapsed_sec: float
    input_map: dict[str, str] = field(default_factory=dict)
    instructions: str = ""
    cues: list[Cue] = field(default_factory=list)
    batch_count: int = 1
    batch_size: int = 0
    batch_jobs: int = 1
    batch_reports: list[dict[str, Any]] = field(default_factory=list)
    episode_summary: str = ""
    summary_usage: Optional[Usage] = None

    @property
    def ok(self) -> bool:
        return (
            self.status == "completed"
            and not self.incomplete_reason
            and self.validate.ok
            and bool(self.bilingual_srt)
        )

    def meta_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "model_alias": self.model_alias,
            "model_id": self.model_id,
            "status": self.status,
            "incomplete_reason": self.incomplete_reason,
            "elapsed_sec": round(self.elapsed_sec, 3),
            "ok": self.ok,
            "batch_count": self.batch_count,
            "batch_size": self.batch_size,
            "batch_jobs": self.batch_jobs,
            "batch_reports": self.batch_reports,
            "episode_summary_chars": len(self.episode_summary or ""),
            "usage": {
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
                "reasoning_tokens": self.usage.reasoning_tokens,
                "total_tokens": self.usage.total_tokens,
            },
            "validate": self.validate.to_dict(),
        }
        if self.summary_usage is not None:
            d["summary_usage"] = {
                "input_tokens": self.summary_usage.input_tokens,
                "output_tokens": self.summary_usage.output_tokens,
                "reasoning_tokens": self.summary_usage.reasoning_tokens,
                "total_tokens": self.summary_usage.total_tokens,
            }
        return d


# ---------------------------------------------------------------------------
# SRT I/O
# ---------------------------------------------------------------------------


def parse_srt(path: Path | str) -> list[Cue]:
    """解析 SRT；id 暂用序号字符串，切片后由 reindex_cues 重编号。"""
    path = Path(path)
    raw = path.read_bytes()
    while raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text.strip())
    cues: list[Cue] = []
    for block in blocks:
        lines = block.split("\n")
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if len(lines) < 2:
            continue
        seq_line = lines[0].strip()
        time_line = lines[1].strip()
        if not seq_line.isdigit() or "-->" not in time_line:
            continue
        m = re.match(
            r"(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})",
            time_line,
        )
        if not m:
            continue
        start = m.group(1).replace(".", ",")
        end = m.group(2).replace(".", ",")
        body = "\n".join(lines[2:]).strip("\n")
        seq = int(seq_line)
        cues.append(Cue(id=str(seq), seq=seq, start=start, end=end, text=body))
    return cues


def reindex_cues(cues: list[Cue]) -> list[Cue]:
    """切片后使用稳定 id '0'..'n-1'。"""
    out: list[Cue] = []
    for i, c in enumerate(cues):
        out.append(
            Cue(id=str(i), seq=c.seq, start=c.start, end=c.end, text=c.text)
        )
    return out


def slice_cues(
    cues: list[Cue],
    *,
    cue_offset: int = 0,
    max_cues: Optional[int] = None,
) -> list[Cue]:
    end = None if max_cues is None else cue_offset + max_cues
    sliced = cues[cue_offset:end]
    return reindex_cues(sliced)


def chunk_cues(cues: list[Cue], batch_size: int) -> list[list[Cue]]:
    """
    按批切分（借鉴 translate_subtitles 的 range 步进思路，不 import 该模块）。

    保留各 Cue 已有的全局 id（调用前应对全集 reindex 为 "0".."n-1"）。
    batch_size <= 0 表示单批整包。
    """
    if not cues:
        return []
    if batch_size is None or batch_size <= 0 or batch_size >= len(cues):
        return [cues]
    return [cues[i : i + batch_size] for i in range(0, len(cues), batch_size)]


def sum_usage(parts: list[Usage]) -> Usage:
    u = Usage()
    for p in parts:
        u.input_tokens += p.input_tokens
        u.output_tokens += p.output_tokens
        u.reasoning_tokens += p.reasoning_tokens
        u.total_tokens += p.total_tokens
    return u


def build_input_json(cues: list[Cue]) -> tuple[str, dict[str, str]]:
    mapping = {c.id: c.text for c in cues}
    # compact separators to save tokens slightly
    s = json.dumps(mapping, ensure_ascii=False, separators=(",", ":"))
    return s, mapping


# ---------------------------------------------------------------------------
# Instructions + Glossary
# ---------------------------------------------------------------------------


def compact_glossary(glossary_path: Path | str) -> str:
    """从 Markdown 表格提取 原名 → 中文 紧凑对照。"""
    path = Path(glossary_path)
    if not path.is_file():
        return ""
    lines_out: list[str] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        # skip separator |---|
        if re.match(r"^\|[\s\-:|]+\|$", line):
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) < 2:
            continue
        zh, src = parts[0], parts[1]
        # skip header
        if zh in ("中文译名", "中文") or "原名" in src or src in ("法文/英文原名",):
            continue
        if not zh or not src:
            continue
        # split multi aliases
        aliases = re.split(r"[/／]", src)
        for alias in aliases:
            alias = alias.strip()
            # drop parenthetical notes like (Helmut)
            alias_clean = re.sub(r"\s*\([^)]*\)\s*", " ", alias).strip()
            if not alias_clean or alias_clean in seen:
                continue
            # skip pure Chinese sources
            if re.fullmatch(r"[\u4e00-\u9fff·]+", alias_clean):
                continue
            seen.add(alias_clean)
            lines_out.append(f"{alias_clean} = {zh}")
            # also keep form with parenthetical content if different
            if alias != alias_clean and alias not in seen:
                seen.add(alias)
                lines_out.append(f"{alias} = {zh}")
    return "\n".join(lines_out)


def build_instructions(
    prompt_path: Path | str = DEFAULT_PROMPT,
    glossary_path: Optional[Path | str] = DEFAULT_GLOSSARY,
    source_language: str = "英语",
    target_language: str = "简体中文",
    episode_summary: Optional[str] = None,
) -> str:
    prompt = Path(prompt_path).read_text(encoding="utf-8")
    prompt = prompt.replace("${sourceLanguage}", source_language)
    prompt = prompt.replace("${targetLanguage}", target_language)
    parts = [prompt.rstrip()]
    if glossary_path:
        g = compact_glossary(glossary_path)
        if g.strip():
            parts.append("\n\n## 专有名词（必须遵守，不得另译）\n" + g)
    if episode_summary and episode_summary.strip():
        parts.append(
            "\n\n## 本集剧情摘要（翻译时请参考语境与人物状态，勿写入输出 JSON）\n"
            + episode_summary.strip()
        )
    return "\n".join(parts).strip() + "\n"


def build_summary_input(cues: list[Cue]) -> str:
    """通读用 input：仅 id + 原文，紧凑，无时间码。"""
    lines = [f"{c.id}\t{c.text.replace(chr(10), ' / ')}" for c in cues]
    return "\n".join(lines)


SUMMARY_INSTRUCTIONS = """你是影视字幕分析助手。下面是一整集英文字幕（每行：id<TAB>原文）。
请用简体中文输出本集「翻译用摘要」，控制在 400 字以内，包含：
1) 一句话梗概
2) 主要人物及其关系/立场（本集内）
3) 关键冲突与情绪走向
4) 翻译时需注意的称谓、潜台词、伏笔或专有名词线索

要求：只输出摘要正文，不要 JSON，不要条目译文，不要 Markdown 标题堆砌。"""


def generate_episode_summary(
    model: str,
    cues: list[Cue],
    *,
    max_output_tokens: int = DEFAULT_SUMMARY_MAX_OUTPUT_TOKENS,
    timeout: float = 180.0,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    max_retries: int = 2,
    retry_backoff_sec: float = 3.0,
    out_dir: Optional[Path] = None,
) -> tuple[str, Usage, str, Optional[str]]:
    """
    全量字幕通读 → 摘要。

    Returns
    -------
    summary, usage, status, error_message
    失败时 summary 可能为空，error_message 非空；调用方可降级为无摘要分批。
    """
    summary_input = build_summary_input(cues)
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "episode_summary_input.txt").write_text(
            summary_input, encoding="utf-8"
        )

    attempts = 1 + max(0, max_retries)
    last_err: Optional[str] = None
    usage = Usage()
    raw = ""
    status = "error"

    for attempt in range(1, attempts + 1):
        try:
            _log(f"📖 通读摘要 attempt {attempt}/{attempts} cues={len(cues)} ...")
            mr = model_client.call(
                model,
                summary_input,
                instructions=SUMMARY_INSTRUCTIONS,
                temperature=temperature,
                top_p=top_p,
                max_output_tokens=max_output_tokens,
                timeout=timeout,
            )
            raw = (mr.text or "").strip()
            status = mr.status
            usage = mr.usage
            if out_dir:
                (out_dir / "episode_summary.raw.txt").write_text(
                    raw, encoding="utf-8"
                )

            if status == "completed" and raw and not mr.incomplete_reason:
                _log(
                    f"   ✓ 摘要完成 chars={len(raw)} "
                    f"tokens={usage.input_tokens}/{usage.output_tokens}/{usage.total_tokens}"
                )
                if out_dir:
                    (out_dir / "episode_summary.txt").write_text(
                        raw + "\n", encoding="utf-8"
                    )
                    (out_dir / "episode_summary.meta.json").write_text(
                        json.dumps(
                            {
                                "ok": True,
                                "status": status,
                                "chars": len(raw),
                                "usage": {
                                    "input_tokens": usage.input_tokens,
                                    "output_tokens": usage.output_tokens,
                                    "reasoning_tokens": usage.reasoning_tokens,
                                    "total_tokens": usage.total_tokens,
                                },
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                return raw, usage, status, None

            last_err = (
                f"status={status} incomplete={mr.incomplete_reason} "
                f"empty={not bool(raw)}"
            )
            _log(f"   ⚠ 摘要不理想: {last_err}")
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            _log(f"   ✗ 摘要异常: {last_err}")
            if attempt >= attempts or not _is_retryable_exception(e):
                break
            sleep_s = retry_backoff_sec * (2 ** (attempt - 1))
            time.sleep(sleep_s)
            continue
        if attempt < attempts:
            sleep_s = retry_backoff_sec * (2 ** (attempt - 1))
            time.sleep(sleep_s)

    if out_dir:
        (out_dir / "episode_summary.meta.json").write_text(
            json.dumps(
                {
                    "ok": False,
                    "status": status,
                    "error": last_err,
                    "usage": {
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                        "reasoning_tokens": usage.reasoning_tokens,
                        "total_tokens": usage.total_tokens,
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if raw:
            (out_dir / "episode_summary.txt").write_text(raw + "\n", encoding="utf-8")
    return raw, usage, status, last_err or "summary failed"


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


def _strip_code_fence(text: str) -> str:
    s = text.strip()
    # ```json ... ``` or ``` ... ```
    m = re.match(r"^```(?:json|JSON)?\s*\n([\s\S]*?)\n```\s*$", s)
    if m:
        return m.group(1).strip()
    # leading fence without clean end
    if s.startswith("```"):
        s = re.sub(r"^```(?:json|JSON)?\s*\n?", "", s)
        s = re.sub(r"\n```\s*$", "", s)
    return s.strip()


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def repair_model_json(text: str) -> tuple[Optional[Any], list[str]]:
    """
    尝试解析模型 JSON；对常见畸形做加固修复。

    Returns (data_or_None, repair_notes)
    """
    notes: list[str] = []
    if not text or not str(text).strip():
        return None, ["empty"]

    s = _strip_code_fence(text)

    def _try(s0: str) -> Optional[Any]:
        try:
            return json.loads(s0)
        except json.JSONDecodeError:
            return None

    data = _try(s)
    if data is not None:
        return data, notes

    # 1) 数字键缺左引号： ,201": 或 {201":  → ,"201": / {"201":
    s1 = re.sub(r"([,{])\s*(\d+)\s*\":", r'\1"\2":', s)
    if s1 != s:
        notes.append("fixed unquoted numeric keys")
        data = _try(s1)
        if data is not None:
            return data, notes
        s = s1

    # 2) 根对象缺收尾花括号（常见截断）
    open_b = s.count("{") - s.count("}")
    if open_b > 0:
        s2 = s + ("}" * open_b)
        notes.append(f"appended {open_b} closing brace(s)")
        data = _try(s2)
        if data is not None:
            return data, notes
        s = s2

    # 3) 截断在字符串中间：回退到最后一个完整 "}," 或 "}
    #    并闭合根对象
    for pat in (r"\}\s*,\s*$", r"\}\s*$"):
        m = None
        # find last complete entry end
        idx = s.rfind('"},')
        if idx < 0:
            idx = s.rfind('"}')
        if idx > 0:
            s3 = s[: idx + 2]  # include "}
            # ensure root closed
            ob = s3.count("{") - s3.count("}")
            if ob > 0:
                s3 = s3 + ("}" * ob)
            # if ends with }, remove trailing comma before close
            s3 = re.sub(r",\s*}+\s*$", lambda m: "}" * (m.group(0).count("}")), s3)
            # simpler: strip trailing commas before }
            s3 = re.sub(r",(\s*})", r"\1", s3)
            data = _try(s3)
            if data is not None:
                notes.append("truncated to last complete object entry")
                return data, notes

    # 4) 再试：去掉尾部不完整 key 片段
    idx = s.rfind(",\"")
    if idx > 0:
        s4 = s[:idx] + "}"
        ob = s4.count("{") - s4.count("}")
        if ob > 0:
            s4 += "}" * ob
        s4 = re.sub(r",(\s*})", r"\1", s4)
        data = _try(s4)
        if data is not None:
            notes.append("dropped trailing incomplete entry")
            return data, notes

    return None, notes + ["unrecoverable json"]


def validate_response(raw: str, input_map: dict[str, str]) -> ValidateReport:
    errors: list[str] = []
    warnings: list[str] = []
    parsed: Optional[dict[str, dict[str, str]]] = None
    stats = {
        "n_in": len(input_map),
        "n_out": 0,
        "n_tr_ok": 0,
    }

    if not raw or not raw.strip():
        return ValidateReport(
            ok=False, errors=["empty response"], stats=stats
        )

    data, repair_notes = repair_model_json(raw)
    for n in repair_notes:
        if n not in ("empty", "unrecoverable json"):
            warnings.append(f"json repair: {n}")
    if data is None:
        # keep legacy error shape for logs
        try:
            json.loads(_strip_code_fence(raw))
        except json.JSONDecodeError as e:
            return ValidateReport(
                ok=False,
                errors=[f"json.loads failed: {e}"]
                + ([f"repair notes: {repair_notes}"] if repair_notes else []),
                stats=stats,
            )
        return ValidateReport(
            ok=False, errors=["json parse failed"], stats=stats
        )

    if not isinstance(data, dict):
        return ValidateReport(
            ok=False, errors=["top-level JSON must be object"], stats=stats
        )

    # normalize keys to str
    data_s = {str(k): v for k, v in data.items()}
    stats["n_out"] = len(data_s)

    in_keys = set(input_map.keys())
    out_keys = set(data_s.keys())
    missing = sorted(in_keys - out_keys, key=lambda x: int(x) if x.isdigit() else x)
    extra = sorted(out_keys - in_keys, key=lambda x: int(x) if x.isdigit() else x)
    if missing:
        errors.append("missing keys: " + ", ".join(missing[:20]) + (
            f" ...(+{len(missing)-20})" if len(missing) > 20 else ""
        ))
    if extra:
        errors.append("extra keys: " + ", ".join(extra[:20]) + (
            f" ...(+{len(extra)-20})" if len(extra) > 20 else ""
        ))

    result_map: dict[str, dict[str, str]] = {}
    for kid, expected_src in input_map.items():
        if kid not in data_s:
            continue
        val = data_s[kid]
        if not isinstance(val, dict):
            errors.append(f"id {kid}: value must be object with src/tr")
            continue
        src = val.get("src")
        tr = val.get("tr")
        if tr is None or (isinstance(tr, str) and not tr.strip()):
            errors.append(f"id {kid}: missing or empty tr")
            continue
        if not isinstance(tr, str):
            errors.append(f"id {kid}: tr must be string")
            continue
        if src is None:
            warnings.append(f"id {kid}: missing src")
            src = ""
        elif not isinstance(src, str):
            warnings.append(f"id {kid}: src not string")
            src = str(src)
        else:
            if _norm_ws(src) != _norm_ws(expected_src):
                warnings.append(f"id {kid}: src mismatch")

        # soft Netflix checks on tr
        if "，" in tr or "。" in tr:
            warnings.append(f"id {kid}: contains '，' or '。'")
        if "|" in tr or "｜" in tr:
            warnings.append(f"id {kid}: contains vertical bar")
        if _ELLIPSIS_BAD in tr or "..." in tr:
            warnings.append(f"id {kid}: bad ellipsis (use U+2026 …)")
        lat = len(re.findall(r"[A-Za-z]", tr))
        cjk = len(re.findall(r"[\u4e00-\u9fff]", tr))
        if lat > 8 and lat > cjk:
            warnings.append(f"id {kid}: possible English residue in tr")

        result_map[kid] = {"src": src, "tr": tr}
        stats["n_tr_ok"] += 1

    parsed = result_map if result_map else None
    ok = len(errors) == 0 and stats["n_tr_ok"] == stats["n_in"]
    return ValidateReport(
        ok=ok, errors=errors, warnings=warnings, parsed=parsed, stats=stats
    )


# ---------------------------------------------------------------------------
# Bilingual SRT
# ---------------------------------------------------------------------------


def build_bilingual_srt(
    cues: list[Cue],
    translations: dict[str, str],
) -> str:
    """译文在上、原文在下；原文用本地 Cue.text。"""
    blocks: list[str] = []
    for i, c in enumerate(cues, start=1):
        tr = translations.get(c.id, "").strip()
        src = c.text
        # ensure tr uses newlines as in model; src as original
        body = f"{tr}\n{src}" if src else tr
        blocks.append(f"{i}\n{c.start} --> {c.end}\n{body}")
    return "\n\n".join(blocks) + "\n"


# ---------------------------------------------------------------------------
# Retry helpers / logging
# ---------------------------------------------------------------------------


def _log(msg: str) -> None:
    """进度日志（借鉴 docs/translate_subtitles.py 的阶段打印风格）。"""
    print(msg, flush=True)


def _is_retryable_exception(exc: BaseException) -> bool:
    """网络/限流/5xx 等可重试异常。"""
    name = type(exc).__name__
    text = str(exc).lower()
    retry_names = (
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
        "APIStatusError",
        "TimeoutError",
        "RemoteProtocolError",
        "ConnectError",
        "ReadTimeout",
    )
    if name in retry_names:
        return True
    # OpenAI SDK often embeds status in message
    for needle in ("429", "500", "502", "503", "504", "timeout", "rate limit", "overloaded"):
        if needle in text:
            return True
    # Status code attribute
    status = getattr(exc, "status_code", None)
    if status in (408, 409, 429, 500, 502, 503, 504):
        return True
    return False


def _should_retry_result(
    status: str,
    incomplete: Optional[str],
    raw_text: str,
    input_map: dict[str, str],
) -> tuple[bool, str]:
    """根据 API 结果决定是否重试，并返回原因。"""
    if status != "completed":
        return True, f"api status={status}"
    if incomplete:
        reason = str(incomplete)
        hint = ""
        if "length" in reason.lower():
            hint = " (可能触顶 max_output_tokens 或输出被截断)"
        return True, f"incomplete: {reason}{hint}"
    vr = validate_response(raw_text, input_map)
    if not vr.ok:
        # JSON 解析失败或键不全 → 重试一次有时能好
        joined = " ".join(vr.errors).lower()
        if "json.loads failed" in joined or "empty response" in joined or "missing keys" in joined:
            return True, "validate hard fail: " + "; ".join(vr.errors[:3])
        # 其它结构错误也重试一次
        return True, "validate hard fail: " + "; ".join(vr.errors[:3])
    return False, ""


# ---------------------------------------------------------------------------
# 单批 API 调用（带重试）
# ---------------------------------------------------------------------------


@dataclass
class _BatchOutcome:
    batch_index: int
    cues: list[Cue]
    input_map: dict[str, str]
    raw_text: str
    status: str
    incomplete_reason: Optional[str]
    usage: Usage
    model_id: str
    alias: str
    validate: ValidateReport
    attempt_notes: list[str] = field(default_factory=list)


def _call_one_batch(
    *,
    model: str,
    batch_index: int,
    batch_cues: list[Cue],
    instructions: str,
    max_output_tokens: int,
    timeout: float,
    temperature: Optional[float],
    top_p: Optional[float],
    max_retries: int,
    retry_backoff_sec: float,
    batch_out: Optional[Path],
) -> _BatchOutcome:
    """对一批 cue 调用模型（JSON 键使用全局 id）。"""
    input_json, input_map = build_input_json(batch_cues)
    if batch_out:
        batch_out.mkdir(parents=True, exist_ok=True)
        (batch_out / "input.json").write_text(input_json, encoding="utf-8")

    attempts = 1 + max(0, max_retries)
    last_exc: Optional[BaseException] = None
    raw_text = ""
    status = "error"
    incomplete: Optional[str] = None
    usage = Usage()
    model_id = ""
    alias = model
    attempt_notes: list[str] = []

    for attempt in range(1, attempts + 1):
        try:
            _log(
                f"   → batch {batch_index:02d} API attempt {attempt}/{attempts} "
                f"(cues={len(batch_cues)} ids={batch_cues[0].id}..{batch_cues[-1].id})"
            )
            mr = model_client.call(
                model,
                input_json,
                instructions=instructions,
                temperature=temperature,
                top_p=top_p,
                max_output_tokens=max_output_tokens,
                timeout=timeout,
            )
            raw_text = mr.text or ""
            status = mr.status
            incomplete = mr.incomplete_reason
            usage = mr.usage
            model_id = mr.model
            alias = mr.alias
            last_exc = None

            if batch_out:
                (batch_out / "raw_output.txt").write_text(raw_text, encoding="utf-8")
                if attempt > 1:
                    (batch_out / f"raw_output.attempt{attempt}.txt").write_text(
                        raw_text, encoding="utf-8"
                    )

            retry, why = _should_retry_result(status, incomplete, raw_text, input_map)
            if not retry:
                _log(
                    f"   ✓ batch {batch_index:02d} ok "
                    f"tokens={usage.input_tokens}/{usage.output_tokens}/{usage.total_tokens}"
                )
                break

            attempt_notes.append(f"batch{batch_index} attempt{attempt}: retry — {why}")
            _log(f"   ⚠ batch {batch_index:02d} attempt {attempt} 需重试: {why}")
            if attempt >= attempts:
                break
            sleep_s = retry_backoff_sec * (2 ** (attempt - 1))
            _log(f"   … 退避 {sleep_s:.1f}s")
            time.sleep(sleep_s)

        except Exception as e:  # noqa: BLE001
            last_exc = e
            attempt_notes.append(
                f"batch{batch_index} attempt{attempt}: {type(e).__name__}: {e}"
            )
            _log(
                f"   ✗ batch {batch_index:02d} attempt {attempt} "
                f"异常: {type(e).__name__}: {e}"
            )
            if batch_out:
                (batch_out / "last_exception.txt").write_text(
                    f"{type(e).__name__}: {e}\n", encoding="utf-8"
                )
            if attempt >= attempts or not _is_retryable_exception(e):
                status = f"error: {type(e).__name__}: {e}"
                break
            sleep_s = retry_backoff_sec * (2 ** (attempt - 1))
            _log(f"   … 可重试异常，退避 {sleep_s:.1f}s")
            time.sleep(sleep_s)

    if last_exc is not None and not raw_text:
        vr = ValidateReport(
            ok=False,
            errors=[f"api error: {type(last_exc).__name__}: {last_exc}"]
            + attempt_notes,
            stats={"n_in": len(input_map), "n_out": 0, "n_tr_ok": 0},
        )
    else:
        vr = validate_response(raw_text, input_map)
        if status != "completed":
            vr.errors.append(f"api status={status}")
            vr.ok = False
        if incomplete:
            msg = f"incomplete: {incomplete}"
            if "length" in str(incomplete).lower():
                msg += (
                    f" — 可能截断；max_output_tokens={max_output_tokens}"
                )
            vr.errors.append(msg)
            vr.ok = False
        if any("json.loads failed" in e for e in vr.errors):
            vr.errors.append(
                "JSON 解析失败：可能输出被截断；见 raw_output.txt"
            )
        for note in attempt_notes:
            vr.warnings.append(note)

    if batch_out:
        (batch_out / "validate.json").write_text(
            json.dumps(vr.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if vr.parsed:
            (batch_out / "parsed.json").write_text(
                json.dumps(vr.parsed, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    return _BatchOutcome(
        batch_index=batch_index,
        cues=batch_cues,
        input_map=input_map,
        raw_text=raw_text,
        status=status,
        incomplete_reason=incomplete,
        usage=usage,
        model_id=model_id,
        alias=alias,
        validate=vr,
        attempt_notes=attempt_notes,
    )


# ---------------------------------------------------------------------------
# run_once：分批（顺序或并行）+ 本地拼装
# ---------------------------------------------------------------------------


def run_once(
    srt_path: Path | str,
    model: str,
    *,
    source_language: str = "英语",
    target_language: str = "简体中文",
    prompt_path: Path | str = DEFAULT_PROMPT,
    glossary_path: Optional[Path | str] = DEFAULT_GLOSSARY,
    max_cues: Optional[int] = None,
    cue_offset: int = 0,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    out_dir: Optional[Path | str] = None,
    timeout: float = 1200.0,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    max_retries: int = 2,
    retry_backoff_sec: float = 3.0,
    batch_size: int = DEFAULT_BATCH_SIZE,
    batch_jobs: int = 1,
    use_episode_summary: bool = True,
    summary_max_output_tokens: int = DEFAULT_SUMMARY_MAX_OUTPUT_TOKENS,
    summary_timeout: float = 180.0,
) -> TranslateResult:
    """
    整集（或切片）翻译：可选通读摘要 + 按 batch_size 分批送模型，本地合并。

    - batch_size: 每批条数，默认 50；<=0 表示单批整包
    - batch_jobs: 批并行度，1=顺序；>1 多批并行请求后拼装
    - use_episode_summary: 先全量通读生成摘要，再注入各批 instructions
    - 双语 SRT：译文用模型 tr，原文用本地 Cue.text（按全局 id 对齐）
    """
    srt_path = Path(srt_path)
    out_path = Path(out_dir) if out_dir else None

    _log(f"📂 加载 SRT: {srt_path.name}")
    all_cues = parse_srt(srt_path)
    cues = slice_cues(all_cues, cue_offset=cue_offset, max_cues=max_cues)
    if not cues:
        raise ValueError(f"no cues parsed from {srt_path}")

    full_input_json, full_input_map = build_input_json(cues)

    t0 = time.perf_counter()
    episode_summary = ""
    summary_usage: Optional[Usage] = None
    summary_notes: list[str] = []

    if use_episode_summary:
        summary_dir = out_path  # 落盘到模型输出根目录
        episode_summary, summary_usage, _sum_status, sum_err = generate_episode_summary(
            model,
            cues,
            max_output_tokens=summary_max_output_tokens,
            timeout=summary_timeout,
            temperature=temperature,
            top_p=top_p,
            max_retries=max_retries,
            retry_backoff_sec=retry_backoff_sec,
            out_dir=summary_dir,
        )
        if sum_err:
            summary_notes.append(f"episode_summary degraded: {sum_err}")
            _log(f"   ⚠ 摘要失败，降级为无摘要分批: {sum_err}")
            episode_summary = episode_summary or ""
        elif not episode_summary.strip():
            summary_notes.append("episode_summary empty; continue without")
            _log("   ⚠ 摘要为空，降级为无摘要分批")
    else:
        _log("   （跳过通读摘要 use_episode_summary=False）")

    instructions = build_instructions(
        prompt_path=prompt_path,
        glossary_path=glossary_path,
        source_language=source_language,
        target_language=target_language,
        episode_summary=episode_summary or None,
    )

    batches = chunk_cues(cues, batch_size)
    n_batches = len(batches)
    jobs = max(1, int(batch_jobs or 1))

    _log(
        f"🌐 分批翻译 model={model} cues={len(cues)}/{len(all_cues)} "
        f"batches={n_batches}×{batch_size if batch_size > 0 else 'all'} "
        f"batch_jobs={jobs} max_out={max_output_tokens} timeout={timeout}s "
        f"retries={max_retries} summary={'yes' if episode_summary else 'no'}"
    )
    _log(
        f"   full_input ≈ {len(full_input_json)} chars, "
        f"instructions ≈ {len(instructions)} chars "
        f"(summary_chars={len(episode_summary)})"
    )

    if out_path:
        out_path.mkdir(parents=True, exist_ok=True)
        (out_path / "input.json").write_text(full_input_json, encoding="utf-8")
        (out_path / "instructions.txt").write_text(instructions, encoding="utf-8")
        (out_path / "batches_plan.json").write_text(
            json.dumps(
                {
                    "batch_size": batch_size,
                    "batch_jobs": jobs,
                    "n_batches": n_batches,
                    "use_episode_summary": use_episode_summary,
                    "episode_summary_chars": len(episode_summary),
                    "batches": [
                        {
                            "index": i,
                            "n": len(b),
                            "id_from": b[0].id,
                            "id_to": b[-1].id,
                        }
                        for i, b in enumerate(batches)
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    outcomes: list[_BatchOutcome] = []

    def _run_idx(i: int) -> _BatchOutcome:
        bout = (out_path / f"batch_{i:02d}") if out_path else None
        return _call_one_batch(
            model=model,
            batch_index=i,
            batch_cues=batches[i],
            instructions=instructions,
            max_output_tokens=max_output_tokens,
            timeout=timeout,
            temperature=temperature,
            top_p=top_p,
            max_retries=max_retries,
            retry_backoff_sec=retry_backoff_sec,
            batch_out=bout,
        )

    def _run_many(indices: list[int], *, parallel: bool, label: str) -> dict[int, _BatchOutcome]:
        out_map: dict[int, _BatchOutcome] = {}
        if not indices:
            return out_map
        if not parallel or len(indices) == 1:
            for i in indices:
                out_map[i] = _run_idx(i)
            return out_map
        _log(f"   ⚡ {label} 并行 {len(indices)} 批（workers={min(jobs, len(indices))}）…")
        with ThreadPoolExecutor(max_workers=min(jobs, len(indices))) as ex:
            futs = {ex.submit(_run_idx, i): i for i in indices}
            for fut in as_completed(futs):
                i = futs[fut]
                try:
                    out_map[i] = fut.result()
                except Exception as e:  # noqa: BLE001
                    _log(f"   ✗ batch {i:02d} worker 崩溃: {e}")
                    out_map[i] = _BatchOutcome(
                        batch_index=i,
                        cues=batches[i],
                        input_map={c.id: c.text for c in batches[i]},
                        raw_text="",
                        status=f"error: {type(e).__name__}: {e}",
                        incomplete_reason=None,
                        usage=Usage(),
                        model_id="",
                        alias=model,
                        validate=ValidateReport(ok=False, errors=[str(e)]),
                    )
        return out_map

    # 第一波：顺序或并行
    first = _run_many(
        list(range(n_batches)),
        parallel=(jobs > 1 and n_batches > 1),
        label="首轮",
    )
    outcomes_map = dict(first)

    # 失败批重跑（并行首轮后必做；顺序首轮也做一次，提高稳健性）
    failed_idx = sorted(i for i, oc in outcomes_map.items() if not oc.validate.ok)
    if failed_idx:
        _log(
            f"🔄 失败批重跑（顺序）: {failed_idx} "
            f"（共 {len(failed_idx)}/{n_batches}）"
        )
        # 失败批默认顺序，降低限流/审核叠加；仍走各自 max_retries
        retry_map = _run_many(failed_idx, parallel=False, label="失败重跑")
        for i, oc in retry_map.items():
            # 累加 usage：保留两轮 usage 之和
            prev = outcomes_map[i]
            oc.usage = sum_usage([prev.usage, oc.usage])
            if oc.validate.ok:
                oc.validate.warnings.append(
                    f"batch {i:02d}: recovered on failure re-run"
                )
            else:
                oc.validate.warnings.append(
                    f"batch {i:02d}: still failing after re-run"
                )
            outcomes_map[i] = oc

    outcomes = [outcomes_map[i] for i in range(n_batches)]
    elapsed = time.perf_counter() - t0

    # 合并
    merged_parsed: dict[str, dict[str, str]] = {}
    all_errors: list[str] = []
    all_warnings: list[str] = []
    batch_reports: list[dict[str, Any]] = []
    usages: list[Usage] = []
    raw_parts: list[str] = []
    model_id = ""
    alias = model
    any_incomplete: Optional[str] = None

    for oc in outcomes:
        usages.append(oc.usage)
        if oc.model_id:
            model_id = oc.model_id
        if oc.alias:
            alias = oc.alias
        if oc.incomplete_reason and not any_incomplete:
            any_incomplete = oc.incomplete_reason
        if oc.raw_text:
            raw_parts.append(f"--- batch {oc.batch_index:02d} ---\n{oc.raw_text}")
        batch_reports.append(
            {
                "batch_index": oc.batch_index,
                "ok": oc.validate.ok,
                "status": oc.status,
                "n_in": oc.validate.stats.get("n_in", len(oc.input_map)),
                "n_tr_ok": oc.validate.stats.get("n_tr_ok", 0),
                "errors": oc.validate.errors,
                "warnings": oc.validate.warnings,
                "usage": {
                    "input_tokens": oc.usage.input_tokens,
                    "output_tokens": oc.usage.output_tokens,
                    "total_tokens": oc.usage.total_tokens,
                    "reasoning_tokens": oc.usage.reasoning_tokens,
                },
            }
        )
        if not oc.validate.ok:
            all_errors.append(
                f"batch {oc.batch_index:02d}: "
                + ("; ".join(oc.validate.errors[:3]) or oc.status)
            )
        all_warnings.extend(
            f"batch {oc.batch_index:02d}: {w}" for w in oc.validate.warnings
        )
        if oc.validate.parsed:
            for k, v in oc.validate.parsed.items():
                if k in merged_parsed:
                    all_warnings.append(f"duplicate key after merge: {k}")
                merged_parsed[k] = v

    # 全集键完整性
    missing = sorted(
        set(full_input_map) - set(merged_parsed),
        key=lambda x: int(x) if x.isdigit() else x,
    )
    if missing:
        all_errors.append(
            "missing keys after merge: "
            + ", ".join(missing[:30])
            + (f" ...(+{len(missing)-30})" if len(missing) > 30 else "")
        )

    n_ok = sum(1 for oc in outcomes if oc.validate.ok)
    overall_ok = n_ok == n_batches and not missing and len(merged_parsed) == len(cues)
    status = (
        "completed"
        if overall_ok
        else f"error: {n_ok}/{n_batches} batches ok, merged={len(merged_parsed)}/{len(cues)}"
    )

    vr = ValidateReport(
        ok=overall_ok,
        errors=all_errors,
        warnings=all_warnings,
        parsed=merged_parsed if merged_parsed else None,
        stats={
            "n_in": len(full_input_map),
            "n_out": len(merged_parsed),
            "n_tr_ok": sum(
                1
                for k, v in merged_parsed.items()
                if (v.get("tr") or "").strip()
            ),
            "n_batches": n_batches,
            "n_batches_ok": n_ok,
        },
    )

    bilingual: Optional[str] = None
    if overall_ok:
        tr_map = {k: v["tr"] for k, v in merged_parsed.items()}
        # 原文始终用本地 Cue.text，避免模型 src 改写（方案 B 评估见 docs）
        bilingual = build_bilingual_srt(cues, tr_map)

    raw_text = "\n\n".join(raw_parts)
    usage = sum_usage(usages)
    if summary_usage is not None:
        usage = sum_usage([summary_usage, usage])

    for note in summary_notes:
        vr.warnings.append(note)

    result = TranslateResult(
        model_alias=alias,
        model_id=model_id,
        usage=usage,
        status=status,
        incomplete_reason=any_incomplete,
        validate=vr,
        bilingual_srt=bilingual,
        raw_text=raw_text,
        elapsed_sec=elapsed,
        input_map=full_input_map,
        instructions=instructions,
        cues=cues,
        batch_count=n_batches,
        batch_size=batch_size if batch_size > 0 else len(cues),
        batch_jobs=jobs,
        batch_reports=batch_reports,
        episode_summary=episode_summary,
        summary_usage=summary_usage,
    )
    if out_path:
        _write_outputs(out_path, result, full_input_json)

    if result.ok:
        _log(
            f"✅ 完成 model={alias} cues={len(cues)} batches={n_ok}/{n_batches} "
            f"tokens={usage.total_tokens} "
            f"(summary={summary_usage.total_tokens if summary_usage else 0}) "
            f"sec={elapsed:.1f} → {out_path or '(no out_dir)'}"
        )
    else:
        _log(
            f"❌ 未通过 model={alias} batches_ok={n_ok}/{n_batches} "
            f"merged={len(merged_parsed)}/{len(cues)} "
            f"errors={all_errors[:3]} sec={elapsed:.1f}"
        )
    return result


def _write_outputs(out_dir: Path, result: TranslateResult, input_json: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "input.json").write_text(input_json, encoding="utf-8")
    (out_dir / "instructions.txt").write_text(result.instructions, encoding="utf-8")
    (out_dir / "raw_output.txt").write_text(result.raw_text or "", encoding="utf-8")
    (out_dir / "validate.json").write_text(
        json.dumps(result.validate.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "meta.json").write_text(
        json.dumps(result.meta_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if result.validate.parsed:
        (out_dir / "parsed.json").write_text(
            json.dumps(result.validate.parsed, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if result.bilingual_srt:
        (out_dir / "bilingual.srt").write_text(
            result.bilingual_srt, encoding="utf-8"
        )
        # 成功则移除 partial 提示
        partial = out_dir / "bilingual.PARTIAL.txt"
        if partial.exists():
            partial.unlink()
    else:
        # 失败时写 partial 占位说明
        (out_dir / "bilingual.PARTIAL.txt").write_text(
            "bilingual.srt not written: validation or API failed.\n"
            "See validate.json / raw_output.txt / meta.json\n",
            encoding="utf-8",
        )


def repair_run_dir(
    run_dir: Path | str,
    srt_path: Path | str,
    model: str,
    *,
    batch_indices: Optional[list[int]] = None,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    timeout: float = 300.0,
    max_retries: int = 2,
    retry_backoff_sec: float = 3.0,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
) -> TranslateResult:
    """
    对已有 run 目录只重跑失败批（或指定 batch_indices），合并进 parsed.json 并尝试生成 bilingual.srt。

    需要目录内已有: input.json, instructions.txt；建议有 meta.json / parsed.json。
    """
    run_dir = Path(run_dir)
    srt_path = Path(srt_path)
    if not (run_dir / "input.json").is_file():
        raise FileNotFoundError(f"missing input.json in {run_dir}")
    if not (run_dir / "instructions.txt").is_file():
        raise FileNotFoundError(f"missing instructions.txt in {run_dir}")

    full_input_map: dict[str, str] = json.loads(
        (run_dir / "input.json").read_text(encoding="utf-8")
    )
    instructions = (run_dir / "instructions.txt").read_text(encoding="utf-8")

    # 从 SRT 重建 cues（全局 reindex 后按 input 键过滤）
    all_cues = reindex_cues(parse_srt(srt_path))
    # 若 run 是 max_cues 切片，input 键为 0..n-1
    cues = [c for c in all_cues if c.id in full_input_map]
    if len(cues) != len(full_input_map):
        # 仅用 input 文本 + 时间码尽量匹配
        by_id = {c.id: c for c in all_cues}
        cues = []
        for kid, text in sorted(
            full_input_map.items(), key=lambda x: int(x[0]) if x[0].isdigit() else x[0]
        ):
            if kid in by_id:
                cues.append(by_id[kid])
            else:
                cues.append(
                    Cue(id=kid, seq=int(kid) if kid.isdigit() else 0, start="00:00:00,000", end="00:00:00,000", text=text)
                )

    meta_path = run_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    batch_size = int(meta.get("batch_size") or DEFAULT_BATCH_SIZE)
    batches = chunk_cues(cues, batch_size)
    n_batches = len(batches)

    existing: dict[str, dict[str, str]] = {}
    parsed_path = run_dir / "parsed.json"
    if parsed_path.is_file():
        existing = json.loads(parsed_path.read_text(encoding="utf-8"))

    # 判定失败批
    if batch_indices is None:
        failed: list[int] = []
        reports = meta.get("batch_reports") or []
        if reports:
            for br in reports:
                if not br.get("ok"):
                    failed.append(int(br["batch_index"]))
        else:
            missing = set(full_input_map) - set(existing)
            for mid in missing:
                try:
                    failed.append(int(mid) // batch_size)
                except ValueError:
                    pass
            failed = sorted(set(failed))
        batch_indices = failed

    batch_indices = sorted(set(int(i) for i in batch_indices))
    _log(f"🔧 repair_run_dir {run_dir.name} re-run batches={batch_indices}")

    # 先对已有 raw 尝试 JSON 加固解析（免 API）
    recovered_offline: list[int] = []
    for i in list(batch_indices):
        raw_p = run_dir / f"batch_{i:02d}" / "raw_output.txt"
        if not raw_p.is_file():
            continue
        raw = raw_p.read_text(encoding="utf-8")
        input_map = {c.id: c.text for c in batches[i]} if i < len(batches) else {}
        if not input_map and (run_dir / f"batch_{i:02d}" / "input.json").is_file():
            input_map = json.loads(
                (run_dir / f"batch_{i:02d}" / "input.json").read_text(encoding="utf-8")
            )
        vr = validate_response(raw, input_map)
        if vr.ok and vr.parsed:
            _log(f"   ✓ batch {i:02d} 离线 JSON 加固恢复 {len(vr.parsed)} keys")
            existing.update(vr.parsed)
            recovered_offline.append(i)
            (run_dir / f"batch_{i:02d}" / "parsed.json").write_text(
                json.dumps(vr.parsed, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (run_dir / f"batch_{i:02d}" / "validate.json").write_text(
                json.dumps(vr.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )

    need_api = [i for i in batch_indices if i not in recovered_offline]
    usages = [Usage()]
    t0 = time.perf_counter()
    for i in need_api:
        if i < 0 or i >= n_batches:
            _log(f"   skip invalid batch index {i}")
            continue
        bout = run_dir / f"batch_{i:02d}"
        oc = _call_one_batch(
            model=model,
            batch_index=i,
            batch_cues=batches[i],
            instructions=instructions,
            max_output_tokens=max_output_tokens,
            timeout=timeout,
            temperature=temperature,
            top_p=top_p,
            max_retries=max_retries,
            retry_backoff_sec=retry_backoff_sec,
            batch_out=bout,
        )
        usages.append(oc.usage)
        if oc.validate.ok and oc.validate.parsed:
            existing.update(oc.validate.parsed)
            _log(f"   ✓ batch {i:02d} API 重跑成功")
        else:
            _log(f"   ✗ batch {i:02d} API 重跑仍失败: {oc.validate.errors[:2]}")

    elapsed = time.perf_counter() - t0
    missing = sorted(
        set(full_input_map) - set(existing),
        key=lambda x: int(x) if x.isdigit() else x,
    )
    overall_ok = len(missing) == 0 and len(existing) == len(full_input_map)
    errors = []
    if missing:
        errors.append(
            "still missing keys: "
            + ", ".join(missing[:40])
            + (f" ...(+{len(missing)-40})" if len(missing) > 40 else "")
        )

    bilingual = None
    if overall_ok:
        tr_map = {k: v["tr"] for k, v in existing.items()}
        bilingual = build_bilingual_srt(cues, tr_map)

    # 更新 batch_reports ok flags where possible
    reports = meta.get("batch_reports") or []
    for br in reports:
        bi = int(br["batch_index"])
        if bi in recovered_offline or bi in need_api:
            # recompute ok from keys coverage
            ids = {c.id for c in batches[bi]} if bi < len(batches) else set()
            br["ok"] = ids.issubset(set(existing.keys()))
            if br["ok"]:
                br["errors"] = []
                br["n_tr_ok"] = len(ids)
                br["status"] = "completed"

    vr = ValidateReport(
        ok=overall_ok,
        errors=errors,
        warnings=[f"offline recovered batches: {recovered_offline}"]
        if recovered_offline
        else [],
        parsed=existing if existing else None,
        stats={
            "n_in": len(full_input_map),
            "n_out": len(existing),
            "n_tr_ok": sum(1 for v in existing.values() if (v.get("tr") or "").strip()),
            "n_batches": n_batches,
            "n_batches_ok": sum(1 for br in reports if br.get("ok"))
            if reports
            else (n_batches if overall_ok else 0),
        },
    )
    usage = sum_usage(usages)
    result = TranslateResult(
        model_alias=model,
        model_id=str(meta.get("model_id") or ""),
        usage=usage,
        status="completed" if overall_ok else f"error: missing {len(missing)} keys",
        incomplete_reason=None,
        validate=vr,
        bilingual_srt=bilingual,
        raw_text="",
        elapsed_sec=elapsed,
        input_map=full_input_map,
        instructions=instructions,
        cues=cues,
        batch_count=n_batches,
        batch_size=batch_size,
        batch_jobs=int(meta.get("batch_jobs") or 1),
        batch_reports=reports,
        episode_summary=(
            (run_dir / "episode_summary.txt").read_text(encoding="utf-8")
            if (run_dir / "episode_summary.txt").is_file()
            else ""
        ),
    )
    # preserve previous total usage if present
    if meta.get("usage") and overall_ok:
        prev_u = meta["usage"]
        result.usage = Usage(
            input_tokens=int(prev_u.get("input_tokens") or 0)
            + usage.input_tokens,
            output_tokens=int(prev_u.get("output_tokens") or 0)
            + usage.output_tokens,
            reasoning_tokens=int(prev_u.get("reasoning_tokens") or 0)
            + usage.reasoning_tokens,
            total_tokens=int(prev_u.get("total_tokens") or 0) + usage.total_tokens,
        )

    _write_outputs(run_dir, result, json.dumps(full_input_map, ensure_ascii=False, separators=(",", ":")))
    if overall_ok:
        _log(f"✅ repair 完成 → bilingual.srt keys={len(existing)}")
    else:
        _log(f"❌ repair 仍缺 {len(missing)} keys: {missing[:20]}")
    return result


def self_check_offline(srt_path: Path | str) -> None:
    """无 API 的快速自检。"""
    cues = parse_srt(srt_path)
    assert len(cues) > 0, "no cues"
    sliced = slice_cues(cues, max_cues=8)
    assert len(sliced) == 8
    assert sliced[0].id == "0"
    js, mp = build_input_json(sliced)
    assert json.loads(js) == mp
    inst = build_instructions()
    assert "英语" in inst or "${sourceLanguage}" not in inst
    assert "简体中文" in inst or "${targetLanguage}" not in inst
    assert " = " in inst  # glossary lines

    # good fixture
    good = {
        k: {"src": v, "tr": "测试译文"}
        for k, v in mp.items()
    }
    vr = validate_response(json.dumps(good, ensure_ascii=False), mp)
    assert vr.ok, vr.errors

    # fence
    fenced = "```json\n" + json.dumps(good, ensure_ascii=False) + "\n```"
    assert validate_response(fenced, mp).ok

    # missing key
    bad = {k: good[k] for k in list(good)[:-1]}
    vr2 = validate_response(json.dumps(bad), mp)
    assert not vr2.ok

    tr_map = {k: "中文一行" for k in mp}
    srt = build_bilingual_srt(sliced, tr_map)
    assert "中文一行" in srt
    assert sliced[0].text.split("\n")[0] in srt

    # chunking: 747 / 50 → 15 batches (14*50 + 47)
    full = reindex_cues(cues)
    chunks = chunk_cues(full, 50)
    assert len(chunks) == (len(full) + 49) // 50
    assert sum(len(c) for c in chunks) == len(full)
    assert chunks[0][0].id == "0"
    assert chunks[1][0].id == "50"
    assert chunk_cues(full, 0) == [full]
    assert sum_usage([Usage(1, 2, 0, 3), Usage(4, 5, 1, 10)]).total_tokens == 13

    print(
        f"offline self-check OK: total_cues={len(cues)} sample={len(sliced)} "
        f"batches_50={len(chunks)}"
    )
