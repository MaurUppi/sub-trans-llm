#!/usr/bin/env python3
"""探测 6 个模型对结构化输出的真实支持情况（文档口径不可靠，以实测为准）。

对每个模型探测 4 种组合：

  1. responses + text.format = {"type": "json_object"}
  2. responses + text.format = {"type": "json_schema", ...}
  3. chat      + response_format = {"type": "json_object"}
  4. chat      + response_format = {"type": "json_schema", ...}

schema 用**本项目真实的字幕回显契约**（动态键 = cue id，值为 {src, tr}），
按批次动态生成——这是能否落地的关键：strict 模式要求 properties 全枚举 +
additionalProperties=false，而 cue id 每批不同，所以 schema 必须每次现生成。

用法::

    python3 scripts/probe_json_schema.py
    python3 scripts/probe_json_schema.py -m qwen3.8-max --verbose
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import model_client  # noqa: E402

CUES = {
    "12": "Marcel ?",
    "13": "The train leaves at dawn.",
    "14": "We can't stay here.",
}
INSTRUCTIONS = (
    "You translate subtitles to Simplified Chinese. Return a JSON object whose keys "
    "are exactly the input keys; each value is an object with 'src' (original) and "
    "'tr' (translation). Return JSON only."
)


# 探针字段：instructions 里**从不提及**。若 schema 真被强制执行，输出必须含它；
# 若输出没有它，说明网关只是接受了参数而并未约束解码。
CONTROL_FIELD = "zzz_schema_probe"


def build_schema(cue_ids: list[str], *, control: bool = False) -> dict[str, Any]:
    """按本批 cue id 动态生成 strict schema（键集合即约束）。

    ``control=True`` 时额外要求一个 prompt 未提及的字段，用于判别
    「真的按 schema 约束解码」还是「参数被静默忽略」。
    """
    props: dict[str, Any] = {"src": {"type": "string"}, "tr": {"type": "string"}}
    required = ["src", "tr"]
    if control:
        props[CONTROL_FIELD] = {"type": "string"}
        required.append(CONTROL_FIELD)
    return {
        "type": "object",
        "properties": {
            cid: {
                "type": "object",
                "properties": props,
                "required": required,
                "additionalProperties": False,
            }
            for cid in cue_ids
        },
        "required": cue_ids,
        "additionalProperties": False,
    }


def _payload() -> str:
    return json.dumps(CUES, ensure_ascii=False, separators=(",", ":"))


def _thinking_kwargs(cfg: dict[str, Any], *, chat: bool) -> dict[str, Any]:
    """沿用项目既有的关思考写法。"""
    if cfg["thinking"] == "ark":
        return {"extra_body": {"thinking": {"type": "disabled"}}}
    if cfg["thinking"] == "ali":
        # chat 端没有 reasoning 顶层参数，退回 enable_thinking
        return (
            {"extra_body": {"enable_thinking": False}}
            if chat
            else {"reasoning": {"effort": "none"}}
        )
    return {}


def probe(alias: str, mode: str, *, control: bool = False) -> dict[str, Any]:
    """mode ∈ {responses_object, responses_schema, chat_object, chat_schema}"""
    cfg = model_client.resolve_model(alias)
    client = model_client._build_client(cfg, timeout=180.0)
    schema = build_schema(list(CUES.keys()), control=control)
    row: dict[str, Any] = {"alias": alias, "mode": mode, "control": control}

    try:
        if mode.startswith("responses"):
            kwargs: dict[str, Any] = {
                "model": cfg["model"],
                "input": _payload(),
                "instructions": INSTRUCTIONS,
                "max_output_tokens": 1024,
                **_thinking_kwargs(cfg, chat=False),
            }
            if mode == "responses_object":
                kwargs["text"] = {"format": {"type": "json_object"}}
            else:
                kwargs["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": "subtitle_batch",
                        "schema": schema,
                        "strict": True,
                    }
                }
            resp = client.responses.create(**kwargs)
            text = model_client._extract_text(resp)
            row["status"] = getattr(resp, "status", "") or ""
        else:
            kwargs = {
                "model": cfg["model"],
                "messages": [
                    {"role": "system", "content": INSTRUCTIONS},
                    {"role": "user", "content": _payload()},
                ],
                "max_tokens": 1024,
                **_thinking_kwargs(cfg, chat=True),
            }
            if mode == "chat_object":
                kwargs["response_format"] = {"type": "json_object"}
            else:
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "subtitle_batch",
                        "schema": schema,
                        "strict": True,
                    },
                }
            resp = client.chat.completions.create(**kwargs)
            text = resp.choices[0].message.content or ""
            row["status"] = resp.choices[0].finish_reason or ""
    except Exception as e:  # noqa: BLE001 — 探测需记录失败原因
        msg = str(e).replace("\n", " ")
        row.update(accepted=False, conforms=False, err=msg[:200])
        return row

    row["accepted"] = True
    row["text"] = (text or "").strip()[:120]

    # 契约校验：纯 JSON（无 fence）+ 键集完全一致 + 每值含 src/tr
    conforms = False
    detail = ""
    raw = (text or "").strip()
    if raw.startswith("```"):
        detail = "code fence"
    else:
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                detail = "not an object"
            elif set(data.keys()) != set(CUES.keys()):
                detail = f"key mismatch: {sorted(data.keys())}"
            elif not all(
                isinstance(v, dict) and {"src", "tr"} <= set(v.keys())
                for v in data.values()
            ):
                detail = "value missing src/tr"
            else:
                conforms = True
        except json.JSONDecodeError as e:
            detail = f"json err: {e}"
    row["conforms"] = conforms
    row["detail"] = detail
    if control:
        # schema 是否真被强制：输出里必须出现 prompt 从未提及的探针字段
        row["enforced"] = CONTROL_FIELD in (text or "")
        if not row["enforced"]:
            row["detail"] = f"schema 未强制（无 {CONTROL_FIELD}）"
    return row


MODES = ["responses_object", "responses_schema", "chat_object", "chat_schema"]


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-m", "--model", action="append", help="只测指定 alias（可重复）")
    ap.add_argument("--mode", action="append", choices=MODES, help="只测指定模式")
    ap.add_argument("--verbose", action="store_true", help="打印返回文本")
    ap.add_argument(
        "--control",
        action="store_true",
        help="负对照：schema 里加一个 prompt 未提及的必填字段，判别 schema 是否真被强制",
    )
    args = ap.parse_args(argv)

    aliases = args.model or model_client.list_models()
    modes = args.mode or MODES
    if args.control:
        modes = [m for m in modes if m.endswith("_schema")]

    print(f"结构化输出探测 · {len(aliases)} 模型 × {len(modes)} 模式")
    if args.control:
        print(f"负对照模式：schema 强制要求 prompt 未提及的字段 {CONTROL_FIELD!r}")
    print()
    hdr = "强制" if args.control else "合约"
    print(f"{'model':24s} {'mode':18s} {'接受':4s} {hdr:4s} 说明")
    print("-" * 100)

    rows: list[dict[str, Any]] = []
    for alias in aliases:
        for mode in modes:
            r = probe(alias, mode, control=args.control)
            rows.append(r)
            acc = "✓" if r.get("accepted") else "✗"
            key = "enforced" if args.control else "conforms"
            con = "✓" if r.get(key) else "✗"
            note = r.get("err") or r.get("detail") or ""
            print(f"{alias:24s} {mode:18s} {acc:4s} {con:4s} {note[:60]}")
            if args.verbose and r.get("text"):
                print(f"{'':24s} {'':18s} → {r['text']!r}")
        print()

    key = "enforced" if args.control else "conforms"
    label = "强制" if args.control else "合约"
    print(f"\n汇总（接受 = 未报错；{label} = " + (
        "输出含 schema 独有字段，证明按 schema 约束解码）"
        if args.control
        else "返回纯 JSON 且键集/字段完全符合）"
    ))
    for mode in modes:
        sub = [r for r in rows if r["mode"] == mode]
        print(
            f"  {mode:18s} 接受 {sum(1 for r in sub if r.get('accepted'))}/{len(sub)}"
            f"   {label} {sum(1 for r in sub if r.get(key))}/{len(sub)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
