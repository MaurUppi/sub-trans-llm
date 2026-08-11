from __future__ import annotations

import json
import re
from typing import Optional

from pipeline.config import ELLIPSIS_BAD, STRICT_SRC_DEFAULT
from pipeline.json_repair import repair_model_json, strip_code_fence
from pipeline.models import ValidateReport
from pipeline.src_align import build_src_index, classify_src


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def validate_response(
    raw: str,
    input_map: dict[str, str],
    *,
    strict_src: bool = STRICT_SRC_DEFAULT,
) -> ValidateReport:
    """校验模型回包是否满足输出契约。

    strict_src=True 时，`src` 回显错位（本条回显了另一条的原文）或与本条内容
    完全不符，判为 **error** —— 这类事故不会破坏 JSON、键集也齐全，只能靠回显
    比对发现，而成片原文取自本地 Cue，所以一旦错位就是整段译文对错行。
    判为 error 后会自动走既有的重试 / 拆批链路（见 `pipeline/retry.py`）。
    """
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
        try:
            json.loads(strip_code_fence(raw))
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

    src_index = build_src_index(input_map)
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
        if src is not None and not isinstance(src, str):
            warnings.append(f"id {kid}: src not string")
            src = str(src)

        verdict = classify_src(kid, src, input_map, src_index)
        if verdict.kind == "missing":
            warnings.append(f"id {kid}: missing src")
            src = ""
        elif verdict.kind == "drift":
            warnings.append(f"id {kid}: src drift (ratio={verdict.ratio:.2f})")
        elif verdict.kind == "misaligned":
            msg = f"id {kid}: src misaligned — echoes id {verdict.other_id}"
            (errors if strict_src else warnings).append(msg)
        elif verdict.kind == "mismatch":
            msg = f"id {kid}: src mismatch (ratio={verdict.ratio:.2f})"
            (errors if strict_src else warnings).append(msg)

        if "，" in tr or "。" in tr:
            warnings.append(f"id {kid}: contains '，' or '。'")
        if "|" in tr or "｜" in tr:
            warnings.append(f"id {kid}: contains vertical bar")
        if ELLIPSIS_BAD in tr or "..." in tr:
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
