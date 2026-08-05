from __future__ import annotations

import json
import re
from typing import Optional

from pipeline.config import ELLIPSIS_BAD
from pipeline.json_repair import repair_model_json, strip_code_fence
from pipeline.models import ValidateReport


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
