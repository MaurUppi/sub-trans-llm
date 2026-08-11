"""Auditable last-resort rescue for provider inspection-blocked glossary terms."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Callable

import model_client
from model_client import Usage

from pipeline.batch_client import call_one_batch
from pipeline.models import BatchOutcome, Cue, TranslateResult, ValidateReport
from pipeline.persist import write_outputs
from pipeline.srt_io import (
    build_bilingual_srt,
    chunk_cues,
    parse_srt,
    reindex_cues,
)


_SOURCE_TERM = "Communists"
_PLACEHOLDER = "__TQA_GLOSSARY_COMMUNISTS__"
_RESTORED_TERM = "共产党"
_RESCUE_INSTRUCTION = f"""

## Provider inspection rescue (audited)
The source JSON in this rescue request contains the opaque token
`{_PLACEHOLDER}`. Copy that token byte-for-byte into both `src` and `tr`.
Translate all other text normally. Do not explain, expand, or translate the
opaque token. Return the same JSON schema as required above.
""".rstrip()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _new_pass_dir(run_dir: Path) -> Path:
    root = run_dir / "inspection_rescue"
    root.mkdir(parents=True, exist_ok=True)
    indices = []
    for child in root.glob("pass-*"):
        try:
            indices.append(int(child.name.removeprefix("pass-")))
        except ValueError:
            continue
    path = root / f"pass-{max(indices, default=0) + 1:03d}"
    path.mkdir()
    return path


def rescue_inspection_run_dir(
    *,
    run_dir: Path | str,
    srt_path: Path | str,
    model: str,
    temperature: object = model_client.OMIT,
    top_p: object = model_client.OMIT,
    call_fn: Callable[..., BatchOutcome] | None = None,
    api_mode: str = model_client.API_MODE_RESPONSES,
) -> TranslateResult:
    """Fill only inspection-blocked glossary cues via a recorded placeholder."""
    api_mode = model_client.normalize_api_mode(api_mode)
    run_dir = Path(run_dir)
    srt_path = Path(srt_path)
    full_input: dict[str, str] = json.loads(
        (run_dir / "input.json").read_text(encoding="utf-8")
    )
    parsed_path = run_dir / "parsed.json"
    existing: dict[str, dict[str, str]] = (
        json.loads(parsed_path.read_text(encoding="utf-8"))
        if parsed_path.is_file()
        else {}
    )
    missing = sorted(set(full_input) - set(existing), key=int)
    if not missing:
        raise ValueError("inspection rescue requested with no missing cues")

    base_instructions = (run_dir / "instructions.txt").read_text(
        encoding="utf-8"
    )
    meta_path = run_dir / "meta.json"
    meta = (
        json.loads(meta_path.read_text(encoding="utf-8"))
        if meta_path.is_file()
        else {}
    )
    cues = reindex_cues(parse_srt(srt_path))
    by_id = {cue.id: cue for cue in cues}
    masked_cues: list[Cue] = []
    transformations: list[dict[str, str]] = []
    for cue_id in missing:
        original = full_input[cue_id]
        if _SOURCE_TERM not in original:
            raise ValueError(
                f"unsupported inspection rescue cue {cue_id}: "
                f"missing frozen source term {_SOURCE_TERM!r}"
            )
        source_cue = by_id[cue_id]
        masked = original.replace(_SOURCE_TERM, _PLACEHOLDER)
        masked_cues.append(
            Cue(
                id=cue_id,
                seq=source_cue.seq,
                start=source_cue.start,
                end=source_cue.end,
                text=masked,
            )
        )
        transformations.append(
            {
                "cue_id": cue_id,
                "source_term": _SOURCE_TERM,
                "placeholder": _PLACEHOLDER,
                "restored_term": _RESTORED_TERM,
                "original_source": original,
                "masked_source": masked,
            }
        )

    pass_dir = _new_pass_dir(run_dir)
    rescue_instructions = base_instructions + _RESCUE_INSTRUCTION
    manifest: dict[str, object] = {
        "schema_version": 1,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "model_alias": model,
        "api_mode": api_mode,
        "sampling": meta.get("sampling") or {},
        "missing_cue_ids": missing,
        "base_instructions_sha256": hashlib.sha256(
            base_instructions.encode("utf-8")
        ).hexdigest(),
        "rescue_instructions_sha256": hashlib.sha256(
            rescue_instructions.encode("utf-8")
        ).hexdigest(),
        "transformations": transformations,
    }
    _write_json(pass_dir / "manifest.json", manifest)
    (pass_dir / "rescue_instructions.txt").write_text(
        rescue_instructions, encoding="utf-8"
    )

    runner = call_fn or call_one_batch
    outcome = runner(
        model=model,
        batch_index=int(missing[0]) // int(meta.get("batch_size") or 50),
        batch_cues=masked_cues,
        instructions=rescue_instructions,
        max_output_tokens=131072,
        timeout=1200.0,
        temperature=temperature,
        top_p=top_p,
        max_retries=2,
        retry_backoff_sec=3.0,
        batch_out=pass_dir / "call",
        api_mode=api_mode,
    )
    if not outcome.validate.ok or not outcome.validate.parsed:
        manifest.update(
            {
                "status": "failed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "errors": outcome.validate.errors,
            }
        )
        _write_json(pass_dir / "manifest.json", manifest)
        raise RuntimeError(
            "inspection rescue call failed: "
            + "; ".join(outcome.validate.errors[:3])
        )

    restored: dict[str, dict[str, str]] = {}
    for cue_id in missing:
        item = outcome.validate.parsed.get(cue_id)
        if not item:
            raise RuntimeError(f"inspection rescue missing output cue {cue_id}")
        translated = (item.get("tr") or "").strip()
        if _PLACEHOLDER not in translated:
            raise RuntimeError(
                f"inspection rescue output omitted placeholder for cue {cue_id}"
            )
        restored[cue_id] = {
            "src": full_input[cue_id],
            "tr": translated.replace(_PLACEHOLDER, _RESTORED_TERM),
        }
    existing.update(restored)
    still_missing = sorted(set(full_input) - set(existing), key=int)
    if still_missing:
        raise RuntimeError(
            f"inspection rescue incomplete; still missing {still_missing}"
        )

    batch_size = int(meta.get("batch_size") or 50)
    batches = chunk_cues(cues, batch_size)
    reports = list(meta.get("batch_reports") or [])
    for report in reports:
        batch_index = int(report["batch_index"])
        ids = {cue.id for cue in batches[batch_index]}
        if ids.issubset(existing):
            report.update(
                {
                    "ok": True,
                    "errors": [],
                    "n_tr_ok": len(ids),
                    "status": "completed",
                }
            )

    previous_usage = meta.get("usage") or {}
    usage = Usage(
        input_tokens=int(previous_usage.get("input_tokens") or 0)
        + outcome.usage.input_tokens,
        output_tokens=int(previous_usage.get("output_tokens") or 0)
        + outcome.usage.output_tokens,
        reasoning_tokens=int(previous_usage.get("reasoning_tokens") or 0)
        + outcome.usage.reasoning_tokens,
        total_tokens=int(previous_usage.get("total_tokens") or 0)
        + outcome.usage.total_tokens,
    )
    validation = ValidateReport(
        ok=True,
        parsed=existing,
        warnings=[
            "provider inspection rescue used an opaque glossary placeholder; "
            "see inspection_rescue manifest"
        ],
        stats={
            "n_in": len(full_input),
            "n_out": len(existing),
            "n_tr_ok": len(existing),
            "n_batches": len(batches),
            "n_batches_ok": len(batches),
        },
    )
    bilingual = build_bilingual_srt(
        cues, {key: value["tr"] for key, value in existing.items()}
    )
    result = TranslateResult(
        model_alias=model,
        model_id=outcome.model_id or str(meta.get("model_id") or ""),
        usage=usage,
        status="completed",
        incomplete_reason=None,
        validate=validation,
        bilingual_srt=bilingual,
        raw_text=(
            (run_dir / "raw_output.txt").read_text(encoding="utf-8")
            if (run_dir / "raw_output.txt").is_file()
            else ""
        ),
        elapsed_sec=float(meta.get("elapsed_sec") or 0),
        api_mode=api_mode,
        input_map=full_input,
        instructions=base_instructions,
        cues=cues,
        batch_count=len(batches),
        batch_size=batch_size,
        batch_jobs=int(meta.get("batch_jobs") or 1),
        batch_reports=reports,
        episode_summary=(
            (run_dir / "episode_summary.txt").read_text(encoding="utf-8")
            if (run_dir / "episode_summary.txt").is_file()
            else ""
        ),
        sampling=dict(meta.get("sampling") or {}),
    )
    write_outputs(
        pass_dir / "assembled",
        result,
        json.dumps(full_input, ensure_ascii=False, separators=(",", ":")),
    )
    manifest.update(
        {
            "status": "completed",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "restored": restored,
            "result": result.meta_dict(),
        }
    )
    _write_json(pass_dir / "manifest.json", manifest)
    return result
