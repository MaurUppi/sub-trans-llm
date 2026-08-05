"""Semantic resplit adapter inspired by VideoCaptioner Split.

Only the prompt is borrowed (pipeline/prompts/split/sentence.md); all model
access goes through model_client (Responses API). This adapter provides a
pragmatic integration: optional LLM <br> boundaries on flat text + proportional
timing (when no word-level timestamps), or word-JSON alignment when provided.
"""
from __future__ import annotations

import json
import re
from datetime import timedelta  # noqa: F401 — used in TimeCode
from pathlib import Path
from typing import Any, Optional

import model_client
from pipeline.logging_util import log
from pipeline.rules.sub_processor import (
    ProcessingConfig,
    SRTDocument,
    SRTProcessor,
    SubtitleBlock,
    TimeCode,
)

_PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "split" / "sentence.md"


def resplit_with_vc(
    document: SRTDocument,
    *,
    words_path: Path,
    model: Optional[str] = None,
) -> tuple[SRTDocument, dict[str, Any]]:
    notes: list[str] = []
    words = json.loads(Path(words_path).read_text(encoding="utf-8"))
    # Expected: list of {text, start, end} in seconds or ms
    if not isinstance(words, list) or not words:
        raise ValueError("words JSON must be a non-empty list")

    # Flatten all words to a string and ask LLM for <br> (if model given)
    flat = " ".join(str(w.get("text", "")).strip() for w in words if w.get("text"))
    if model and _PROMPT.is_file():
        inst = _PROMPT.read_text(encoding="utf-8")
        log(f"🧠 vc_split LLM boundary model={model}")
        mr = model_client.call(
            model,
            flat,
            instructions=inst,
            max_output_tokens=8192,
            temperature=0.2,
            timeout=180.0,
        )
        text = (mr.text or "").strip()
        if "<br>" in text:
            sentences = [s.strip() for s in text.split("<br>") if s.strip()]
        else:
            sentences = [flat]
            notes.append("LLM returned no <br>; single segment")
        notes.append(f"split_llm status={mr.status}")
    else:
        # fall back: pack words into ~18-word chunks
        sentences = []
        buf: list[str] = []
        for w in words:
            t = str(w.get("text", "")).strip()
            if not t:
                continue
            buf.append(t)
            if len(buf) >= 18:
                sentences.append(" ".join(buf))
                buf = []
        if buf:
            sentences.append(" ".join(buf))
        notes.append("split without LLM: fixed word packing")

    # Align each sentence greedily to consecutive words by text match
    blocks: list[SubtitleBlock] = []
    wi = 0
    for sent in sentences:
        need = re.sub(r"\s+", " ", sent).strip().lower()
        if not need:
            continue
        start_i = wi
        acc = ""
        end_i = wi
        while end_i < len(words) and need not in acc and len(acc) < len(need) + 40:
            acc = (acc + " " + str(words[end_i].get("text", "")).strip()).strip().lower()
            acc = re.sub(r"\s+", " ", acc)
            end_i += 1
            if need in acc or acc in need:
                break
        if end_i <= start_i:
            end_i = min(start_i + 1, len(words))
        chunk = words[start_i:end_i] or words[start_i : start_i + 1]
        wi = end_i
        t0 = _to_seconds(chunk[0].get("start", 0))
        t1 = _to_seconds(chunk[-1].get("end", t0 + 0.5))
        if t1 <= t0:
            t1 = t0 + 0.3
        blocks.append(
            SubtitleBlock(
                index=len(blocks) + 1,
                time_code=TimeCode(
                    start=timedelta(seconds=t0),
                    end=timedelta(seconds=t1),
                ),
                lines=[sent],
                is_split=True,
            )
        )
    if not blocks:
        # ultimate fallback rules
        notes.append("word align produced empty; rules resplit")
        pcfg = ProcessingConfig(remove_sdh=False, remove_disfluency=False, no_punct_fix=True)
        processor = SRTProcessor(pcfg)
        return processor._split_overlong_blocks(processor._process_document(document)), {
            "notes": notes
        }

    out = SRTDocument(
        blocks=blocks,
        source_file=document.source_file,
        detected_language=document.detected_language,
        encoding=document.encoding,
    )
    return out, {"notes": notes}


def _to_seconds(v: Any) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    # heuristic: large numbers are ms
    if x > 10000:
        return x / 1000.0
    return x
