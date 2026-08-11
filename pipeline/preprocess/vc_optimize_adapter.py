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

_PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "optimize" / "subtitle.md"
OPTIMIZE_BATCH_SIZE = 100


def _parse_batch(raw_text: str, expected_keys: set[str]) -> dict[str, Any]:
    raw = (raw_text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("optimize output not object")
    actual_keys = set(data)
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys, key=int)
        unexpected = sorted(actual_keys - expected_keys)
        raise ValueError(
            f"optimize output key mismatch: missing={missing}, unexpected={unexpected}"
        )
    return data


def optimize_document(
    document: SRTDocument,
    *,
    model: Optional[str] = None,
    api_mode: str = model_client.DEFAULT_API_MODE,
) -> tuple[SRTDocument, dict[str, Any]]:
    if not model:
        raise ValueError("optimize requires --model")
    if not _PROMPT.is_file():
        raise FileNotFoundError(f"missing optimize prompt: {_PROMPT}")

    # Build globally numbered entries like VC, then call the model in bounded
    # batches. A whole episode can exceed the fixed output-token budget even
    # when the input fits in the model context window. Pure SDH cues bypass the
    # model so optimize cannot mutate or delete them; --remove-sdh owns removal.
    entries: dict[str, str] = {}
    bypassed_sdh_cues = 0
    for i, block in enumerate(document.blocks):
        if block.is_sdh_only_block():
            bypassed_sdh_cues += 1
            continue
        key = str(i + 1)
        entries[key] = block.text

    instructions = _PROMPT.read_text(encoding="utf-8")
    optimized: dict[str, Any] = {}
    statuses: list[str] = []
    items = list(entries.items())
    batch_count = (len(items) + OPTIMIZE_BATCH_SIZE - 1) // OPTIMIZE_BATCH_SIZE
    for batch_index, offset in enumerate(
        range(0, len(items), OPTIMIZE_BATCH_SIZE), start=1
    ):
        payload = dict(items[offset : offset + OPTIMIZE_BATCH_SIZE])
        log(
            f"🧠 optimize via {model} batch={batch_index}/{batch_count} "
            f"blocks={len(payload)}"
        )
        mr = model_client.call(
            model,
            json.dumps(payload, ensure_ascii=False),
            instructions=instructions,
            max_output_tokens=8192,
            temperature=0.2,
            timeout=180.0,
            api_mode=api_mode,
        )
        if mr.status != "completed":
            raise RuntimeError(
                f"optimize batch {batch_index}/{batch_count} failed: "
                f"status={mr.status}, reason={mr.incomplete_reason}"
            )
        data = _parse_batch(mr.text, set(payload))
        optimized.update(data)
        statuses.append(mr.status)

    new_blocks: list[SubtitleBlock] = []
    for i, block in enumerate(document.blocks):
        key = str(i + 1)
        text = optimized.get(key) or block.text
        if not isinstance(text, str) or not text.strip():
            text = block.text
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
    return out, {
        "batch_size": OPTIMIZE_BATCH_SIZE,
        "batch_count": batch_count,
        "bypassed_sdh_cues": bypassed_sdh_cues,
        "notes": [
            f"optimize model={model} api_mode={api_mode} "
            f"batches={batch_count} bypassed_sdh_cues={bypassed_sdh_cues} "
            f"statuses={','.join(statuses)}"
        ]
    }
