"""
字幕翻译模块：SRT → JSON input、外部文件拼 instructions、校验、双语 SRT。

约定见 docs/quality_control.md / docs/benchmark_plan.md。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

import model_client
from model_client import Usage

_ROOT = Path(__file__).resolve().parent
DEFAULT_PROMPT = _ROOT / "docs" / "translation_prompt.md"
DEFAULT_GLOSSARY = _ROOT / "docs" / "Un_Village_francais_Glossary.md"
DEFAULT_MAX_OUTPUT_TOKENS = 131072

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

    @property
    def ok(self) -> bool:
        return (
            self.status == "completed"
            and not self.incomplete_reason
            and self.validate.ok
            and bool(self.bilingual_srt)
        )

    def meta_dict(self) -> dict[str, Any]:
        return {
            "model_alias": self.model_alias,
            "model_id": self.model_id,
            "status": self.status,
            "incomplete_reason": self.incomplete_reason,
            "elapsed_sec": round(self.elapsed_sec, 3),
            "ok": self.ok,
            "usage": {
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
                "reasoning_tokens": self.usage.reasoning_tokens,
                "total_tokens": self.usage.total_tokens,
            },
            "validate": self.validate.to_dict(),
        }


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
) -> str:
    prompt = Path(prompt_path).read_text(encoding="utf-8")
    prompt = prompt.replace("${sourceLanguage}", source_language)
    prompt = prompt.replace("${targetLanguage}", target_language)
    parts = [prompt.rstrip()]
    if glossary_path:
        g = compact_glossary(glossary_path)
        if g.strip():
            parts.append("\n\n## 专有名词（必须遵守，不得另译）\n" + g)
    return "\n".join(parts).strip() + "\n"


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

    cleaned = _strip_code_fence(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        return ValidateReport(
            ok=False,
            errors=[f"json.loads failed: {e}"],
            stats=stats,
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
# run_once
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
) -> TranslateResult:
    """
    单次翻译（含重试）。

    max_retries: 失败后再试的次数（总尝试 = 1 + max_retries）。
    """
    srt_path = Path(srt_path)
    out_path = Path(out_dir) if out_dir else None

    _log(f"📂 加载 SRT: {srt_path.name}")
    all_cues = parse_srt(srt_path)
    cues = slice_cues(all_cues, cue_offset=cue_offset, max_cues=max_cues)
    if not cues:
        raise ValueError(f"no cues parsed from {srt_path}")

    input_json, input_map = build_input_json(cues)
    instructions = build_instructions(
        prompt_path=prompt_path,
        glossary_path=glossary_path,
        source_language=source_language,
        target_language=target_language,
    )

    _log(
        f"🌐 翻译开始 model={model} cues={len(cues)}/{len(all_cues)} "
        f"(offset={cue_offset}) max_out={max_output_tokens} timeout={timeout}s "
        f"retries={max_retries}"
    )
    _log(f"   input_json ≈ {len(input_json)} chars, instructions ≈ {len(instructions)} chars")

    # 先落盘 input/instructions，全量失败时也有现场
    if out_path:
        out_path.mkdir(parents=True, exist_ok=True)
        (out_path / "input.json").write_text(input_json, encoding="utf-8")
        (out_path / "instructions.txt").write_text(instructions, encoding="utf-8")

    t0 = time.perf_counter()
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
            _log(f"   → API 调用 attempt {attempt}/{attempts} ...")
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

            # 每次都写 raw，便于截断分析
            if out_path:
                (out_path / "raw_output.txt").write_text(raw_text, encoding="utf-8")
                if attempt > 1:
                    (out_path / f"raw_output.attempt{attempt}.txt").write_text(
                        raw_text, encoding="utf-8"
                    )

            retry, why = _should_retry_result(status, incomplete, raw_text, input_map)
            if not retry:
                _log(
                    f"   ✓ attempt {attempt} 成功 status={status} "
                    f"tokens in/out/total="
                    f"{usage.input_tokens}/{usage.output_tokens}/{usage.total_tokens}"
                )
                break

            attempt_notes.append(f"attempt {attempt}: will retry — {why}")
            _log(f"   ⚠ attempt {attempt} 需重试: {why}")
            if incomplete and "length" in str(incomplete).lower():
                _log(
                    "   提示: incomplete/length 时提高 max_output_tokens 通常比盲重试更有效"
                )
            if attempt >= attempts:
                break
            sleep_s = retry_backoff_sec * (2 ** (attempt - 1))
            _log(f"   … 退避 {sleep_s:.1f}s 后重试")
            time.sleep(sleep_s)

        except Exception as e:  # noqa: BLE001
            last_exc = e
            attempt_notes.append(
                f"attempt {attempt}: exception {type(e).__name__}: {e}"
            )
            _log(f"   ✗ attempt {attempt} 异常: {type(e).__name__}: {e}")
            if out_path:
                (out_path / "last_exception.txt").write_text(
                    f"{type(e).__name__}: {e}\n", encoding="utf-8"
                )
            if attempt >= attempts or not _is_retryable_exception(e):
                status = f"error: {type(e).__name__}: {e}"
                break
            sleep_s = retry_backoff_sec * (2 ** (attempt - 1))
            _log(f"   … 可重试异常，退避 {sleep_s:.1f}s")
            time.sleep(sleep_s)

    elapsed = time.perf_counter() - t0

    if last_exc is not None and not raw_text:
        vr = ValidateReport(
            ok=False,
            errors=[f"api error: {type(last_exc).__name__}: {last_exc}"]
            + attempt_notes,
        )
        result = TranslateResult(
            model_alias=model,
            model_id=model_id,
            usage=usage,
            status=status if status.startswith("error") else f"error: {last_exc}",
            incomplete_reason=None,
            validate=vr,
            bilingual_srt=None,
            raw_text=raw_text,
            elapsed_sec=elapsed,
            input_map=input_map,
            instructions=instructions,
            cues=cues,
        )
        if out_path:
            _write_outputs(out_path, result, input_json)
        _log(f"❌ 翻译失败 model={model} sec={elapsed:.1f}")
        return result

    vr = validate_response(raw_text, input_map)
    if status != "completed":
        vr.errors.append(f"api status={status}")
        vr.ok = False
    if incomplete:
        msg = f"incomplete: {incomplete}"
        if "length" in str(incomplete).lower():
            msg += (
                f" — 输出可能被截断；当前 max_output_tokens={max_output_tokens}，"
                "可提高上限或减小任务量后重试"
            )
        vr.errors.append(msg)
        vr.ok = False
    if any("json.loads failed" in e for e in vr.errors):
        vr.errors.append(
            "JSON 解析失败：可能输出被截断、混入非 JSON 文本；已保存 raw_output.txt"
        )
    for note in attempt_notes:
        vr.warnings.append(note)

    bilingual: Optional[str] = None
    if vr.ok and vr.parsed:
        tr_map = {k: v["tr"] for k, v in vr.parsed.items()}
        bilingual = build_bilingual_srt(cues, tr_map)

    result = TranslateResult(
        model_alias=alias,
        model_id=model_id,
        usage=usage,
        status=status,
        incomplete_reason=incomplete,
        validate=vr,
        bilingual_srt=bilingual,
        raw_text=raw_text,
        elapsed_sec=elapsed,
        input_map=input_map,
        instructions=instructions,
        cues=cues,
    )
    if out_path:
        _write_outputs(out_path, result, input_json)

    if result.ok:
        _log(
            f"✅ 完成 model={alias} cues={len(cues)} "
            f"tokens={usage.total_tokens} sec={elapsed:.1f} "
            f"→ {out_path or '(no out_dir)'}"
        )
    else:
        _log(
            f"❌ 校验/API 未通过 model={alias} errors={vr.errors[:3]} "
            f"sec={elapsed:.1f} raw_len={len(raw_text)}"
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
    else:
        # 失败时写 partial 占位说明
        (out_dir / "bilingual.PARTIAL.txt").write_text(
            "bilingual.srt not written: validation or API failed.\n"
            "See validate.json / raw_output.txt / meta.json\n",
            encoding="utf-8",
        )


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
    print(f"offline self-check OK: total_cues={len(cues)} sample={len(sliced)}")
