"""Source-text optimize adapted from VideoCaptioner optimize design.

Uses local prompt + model_client; preserves block count (1:1).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

import model_client
from pipeline.logging_util import log
from pipeline.rules.sub_processor import SRTDocument, SubtitleBlock

_PROMPT = Path(__file__).resolve().parents[1] / "vc_optimize" / "prompts" / "subtitle.md"


def optimize_document(
    document: SRTDocument, *, model: Optional[str] = None
) -> tuple[SRTDocument, dict[str, Any]]:
    if not model:
        raise ValueError("optimize requires --model")
    if not _PROMPT.is_file():
        raise FileNotFoundError(f"missing optimize prompt: {_PROMPT}")

    # Build numbered dict like VC
    payload = {}
    order: list[int] = []
    for i, block in enumerate(document.blocks):
        key = str(i + 1)
        payload[key] = block.text
        order.append(i)

    instructions = _PROMPT.read_text(encoding="utf-8")
    user = json.dumps(payload, ensure_ascii=False)
    log(f"🧠 optimize via {model} blocks={len(payload)}")
    mr = model_client.call(
        model,
        user,
        instructions=instructions,
        max_output_tokens=8192,
        temperature=0.2,
        timeout=180.0,
    )
    raw = (mr.text or "").strip()
    # strip fence
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("optimize output not object")

    new_blocks: list[SubtitleBlock] = []
    for i, block in enumerate(document.blocks):
        key = str(i + 1)
        text = data.get(key) or data.get(str(i)) or block.text
        if not isinstance(text, str) or not text.strip():
            text = block.text
        lines = [ln for ln in text.split("\n") if ln.strip() or True]
        # keep as single/multi lines from model
        lines = text.split("\n") if "\n" in text else [text]
        new_blocks.append(
            SubtitleBlock(
                index=block.index,
                time_code=block.time_code,
                lines=lines,
                language=block.language,
                is_sdh=block.is_sdh,
                is_split=block.is_split,
            )
        )
    for i, b in enumerate(new_blocks):
        b.index = i + 1
    out = SRTDocument(
        blocks=new_blocks,
        source_file=document.source_file,
        detected_language=document.detected_language,
        encoding=document.encoding,
    )
    return out, {"notes": [f"optimize model={model} status={mr.status}"]}
