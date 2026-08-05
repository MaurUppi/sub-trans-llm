#!/usr/bin/env python3
"""json_object 负对照：prompt 明确要求散文，看 text.format 是否仍强制 JSON。

若输出是散文 → 参数被静默忽略；若被迫返回 JSON → 端上真的强制。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import model_client  # noqa: E402

# 故意与 json_object 冲突：要求纯散文、明令禁止 JSON
INSTRUCTIONS = "You are a helpful assistant. Answer in one plain English sentence. Do NOT use JSON, braces, or any markup."
PAYLOAD = "What is the capital of France?"


def probe(alias: str, fmt: bool) -> dict:
    cfg = model_client.resolve_model(alias)
    client = model_client._build_client(cfg, timeout=120.0)
    kwargs = {
        "model": cfg["model"],
        "input": PAYLOAD,
        "instructions": INSTRUCTIONS,
        "max_output_tokens": 256,
    }
    if cfg["thinking"] == "ark":
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    elif cfg["thinking"] == "ali":
        kwargs["reasoning"] = {"effort": "none"}
    if fmt:
        kwargs["text"] = {"format": {"type": "json_object"}}
    try:
        resp = client.responses.create(**kwargs)
    except Exception as e:  # noqa: BLE001
        return {"err": str(e).replace("\n", " ")[:120]}
    text = (model_client._extract_text(resp) or "").strip()
    try:
        json.loads(text)
        is_json = True
    except Exception:  # noqa: BLE001
        is_json = False
    return {"is_json": is_json, "text": text[:70]}


print(f"{'model':24s} {'无 format':10s} {'加 json_object':14s} 判定")
print("-" * 92)
for alias in model_client.list_models():
    base = probe(alias, False)
    fmt = probe(alias, True)
    if "err" in fmt:
        verdict = f"报错: {fmt['err'][:40]}"
    elif fmt["is_json"] and not base["is_json"]:
        verdict = "✅ 端上强制（覆盖了 prompt）"
    elif fmt["is_json"] and base["is_json"]:
        verdict = "? 无法判别（无 format 时也是 JSON）"
    else:
        verdict = "❌ 静默忽略（仍是散文）"
    b = "JSON" if base.get("is_json") else "散文"
    f = "JSON" if fmt.get("is_json") else "散文"
    print(f"{alias:24s} {b:10s} {f:14s} {verdict}")
    print(f"{'':24s} → {fmt.get('text', '')!r}")
