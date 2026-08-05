from __future__ import annotations

import json
import re
from typing import Any, Optional


def strip_code_fence(text: str) -> str:
    s = text.strip()
    m = re.match(r"^```(?:json|JSON)?\s*\n([\s\S]*?)\n```\s*$", s)
    if m:
        return m.group(1).strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json|JSON)?\s*\n?", "", s)
        s = re.sub(r"\n```\s*$", "", s)
    return s.strip()


def repair_model_json(text: str) -> tuple[Optional[Any], list[str]]:
    """
    尝试解析模型 JSON；对常见畸形做加固修复。

    Returns (data_or_None, repair_notes)
    """
    notes: list[str] = []
    if not text or not str(text).strip():
        return None, ["empty"]

    s = strip_code_fence(text)

    def _try(s0: str) -> Optional[Any]:
        try:
            return json.loads(s0)
        except json.JSONDecodeError:
            return None

    data = _try(s)
    if data is not None:
        return data, notes

    s1 = re.sub(r"([,{])\s*(\d+)\s*\":", r'\1"\2":', s)
    if s1 != s:
        notes.append("fixed unquoted numeric keys")
        data = _try(s1)
        if data is not None:
            return data, notes
        s = s1

    open_b = s.count("{") - s.count("}")
    if open_b > 0:
        s2 = s + ("}" * open_b)
        notes.append(f"appended {open_b} closing brace(s)")
        data = _try(s2)
        if data is not None:
            return data, notes
        s = s2

    idx = s.rfind('"},')
    if idx < 0:
        idx = s.rfind('"}')
    if idx > 0:
        s3 = s[: idx + 2]
        ob = s3.count("{") - s3.count("}")
        if ob > 0:
            s3 = s3 + ("}" * ob)
        s3 = re.sub(r",(\s*})", r"\1", s3)
        data = _try(s3)
        if data is not None:
            notes.append("truncated to last complete object entry")
            return data, notes

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
