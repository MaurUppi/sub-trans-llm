#!/usr/bin/env python3
"""思考关闭烟测：验证 6 个模型（Ark 3 + 阿里云 3）思考模式确实关闭。

判据（三项全过才算 PASS）：
  1. status == "completed"（未因预算耗尽而 incomplete）
  2. usage.output_tokens_details.reasoning_tokens == 0
  3. Responses 输出里没有携带内容的 ``reasoning`` item

用一道会诱发思考的题目做输入；若开关失效，模型通常会产生 reasoning tokens。

用法::

    python3 scripts/smoke_thinking.py              # 只测正路径（默认关思考）
    python3 scripts/smoke_thinking.py --control    # 额外跑对照组（不传关闭参数）
    python3 scripts/smoke_thinking.py -m qwen3.7-max
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import model_client  # noqa: E402

# 足够诱发思考，又不会产生长输出
PROBE = (
    "A train leaves at 14:37 and arrives at 16:05 after one 8-minute stop. "
    "How many minutes was it moving? Reply with only the number."
)
MAX_OUT = 512


def _reasoning_items(resp: Any) -> list[str]:
    """返回 raw output 中 reasoning item 的非空摘要文本。"""
    found: list[str] = []
    for item in getattr(resp, "output", None) or []:
        if getattr(item, "type", None) != "reasoning":
            continue
        for c in getattr(item, "summary", None) or []:
            text = getattr(c, "text", "") or ""
            if text.strip():
                found.append(text.strip())
        for c in getattr(item, "content", None) or []:
            text = getattr(c, "text", "") or ""
            if text.strip():
                found.append(text.strip())
    return found


def _call_control(alias: str) -> Any:
    """对照组：绕过 model_client.call，不带任何关闭参数直接发请求。"""
    cfg = model_client.resolve_model(alias)
    client = model_client._build_client(cfg, timeout=180.0)
    return client.responses.create(
        model=cfg["model"], input=PROBE, max_output_tokens=MAX_OUT
    )


def check(alias: str, *, disable_thinking: bool = True) -> dict[str, Any]:
    """正路径走真实的 model_client.call，确保测的是线上代码而非复刻逻辑。"""
    row: dict[str, Any] = {"alias": alias, "control": not disable_thinking}
    try:
        if disable_thinking:
            resp = model_client.call(
                alias, PROBE, max_output_tokens=MAX_OUT, timeout=180.0
            ).raw
        else:
            resp = _call_control(alias)
    except Exception as e:  # noqa: BLE001 — 烟测需报告而非中断
        row.update(ok=False, status=f"error: {type(e).__name__}: {e}")
        return row

    usage = model_client.Usage.from_response(getattr(resp, "usage", None))
    status = getattr(resp, "status", "") or ""
    incomplete = getattr(resp, "incomplete_details", None)
    reasoning_texts = _reasoning_items(resp)

    row.update(
        status=status,
        reasoning_tokens=usage.reasoning_tokens,
        output_tokens=usage.output_tokens,
        input_tokens=usage.input_tokens,
        reasoning_items=len(reasoning_texts),
        text=(model_client._extract_text(resp) or "").strip()[:60],
        incomplete=getattr(incomplete, "reason", None) if incomplete else None,
        ok=(
            status == "completed"
            and usage.reasoning_tokens == 0
            and not reasoning_texts
        ),
    )
    return row


def _print(row: dict[str, Any]) -> None:
    tag = "CTRL" if row["control"] else ("PASS" if row["ok"] else "FAIL")
    if "reasoning_tokens" not in row:
        print(f"[{tag}] {row['alias']:24s} {row['status']}")
        return
    print(
        f"[{tag}] {row['alias']:24s} status={row['status']:10s} "
        f"reasoning_tokens={row['reasoning_tokens']:<6d} "
        f"reasoning_items={row['reasoning_items']} "
        f"out={row['output_tokens']:<5d} text={row['text']!r}"
        + (f" incomplete={row['incomplete']}" if row.get("incomplete") else "")
    )


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-m", "--model", action="append", help="只测指定 alias（可重复）")
    ap.add_argument(
        "--control",
        action="store_true",
        help="额外跑对照组（不传关闭参数），用于证明关闭参数确实起作用",
    )
    args = ap.parse_args(argv)

    aliases = args.model or model_client.list_models()
    print(f"思考关闭烟测 · {len(aliases)} 个模型 · max_output_tokens={MAX_OUT}\n")

    rows = [check(a) for a in aliases]
    for r in rows:
        _print(r)

    if args.control:
        print("\n对照组（不传关闭参数，仅供比对，不计入通过率）：")
        for a in aliases:
            _print(check(a, disable_thinking=False))

    passed = sum(1 for r in rows if r["ok"])
    print(f"\n通过 {passed}/{len(rows)}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
