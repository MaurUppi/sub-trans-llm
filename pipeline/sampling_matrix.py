"""Serial, resumable sampling-matrix collection."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import threading
from typing import Callable

import model_client
from pipeline.models import TranslateResult
from pipeline.inspection_rescue import rescue_inspection_run_dir
from pipeline.orchestrator import _sampling_evidence, run_once
from pipeline.repair import repair_run_dir
from pipeline.srt_io import parse_srt, slice_cues
from pipeline.summary import generate_episode_summary


_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_PROMPT = _ROOT / "docs" / "translation_prompt.md"
_DEFAULT_GLOSSARY = _ROOT / "docs" / "Un_Village_francais_Glossary.md"


@dataclass(frozen=True)
class SamplingArm:
    temperature: float | None
    top_p: float | None

    @staticmethod
    def _label(value: float | None) -> str:
        return "OMIT" if value is None else str(value)

    @property
    def label(self) -> str:
        return (
            f"temperature-{self._label(self.temperature)}"
            f"__topP-{self._label(self.top_p)}"
        )


@dataclass(frozen=True)
class CollectionCase:
    index: int
    episode_id: str
    source_srt: Path
    model_alias: str
    arm: SamplingArm

    @property
    def slug(self) -> str:
        return (
            f"{self.index:03d}__{self.episode_id}__{self.model_alias}__"
            f"{self.arm.label}"
        )

    @property
    def final_filename(self) -> str:
        return f"{self.slug}__bilingual.srt"


def build_sampling_arms() -> tuple[SamplingArm, ...]:
    return (
        SamplingArm(None, None),
        SamplingArm(0.1, None),
        SamplingArm(0.3, None),
        SamplingArm(0.7, None),
        SamplingArm(1.0, None),
        SamplingArm(1.3, None),
        SamplingArm(1.5, None),
        SamplingArm(None, 0.7),
        SamplingArm(None, 0.8),
        SamplingArm(None, 1.0),
    )


def build_cases(
    *,
    models: tuple[str, ...],
    episodes: tuple[tuple[str, Path], ...],
) -> tuple[CollectionCase, ...]:
    cases: list[CollectionCase] = []
    for model in models:
        for arm in build_sampling_arms():
            for episode_id, source_srt in episodes:
                cases.append(
                    CollectionCase(
                        index=len(cases) + 1,
                        episode_id=episode_id,
                        source_srt=Path(source_srt),
                        model_alias=model,
                        arm=arm,
                    )
                )
    return tuple(cases)


def build_default_cases() -> tuple[CollectionCase, ...]:
    return build_cases(
        models=("deepseek-v4-flash", "qwen3.7-plus"),
        episodes=(
            (
                "S01E03",
                _ROOT / "sample" / "A.French.Village.S01E03_eng.srt",
            ),
            (
                "S01E06",
                _ROOT / "sample" / "A.French.Village.S01E06_eng.srt",
            ),
        ),
    )


def prepare_collection(
    *, cases: tuple[CollectionCase, ...], output_root: Path
) -> Path:
    output_root = Path(output_root)
    cases_root = output_root / "cases"
    bilingual_root = output_root / "bilingual"
    cases_root.mkdir(parents=True, exist_ok=True)
    bilingual_root.mkdir(parents=True, exist_ok=True)

    matrix_cases: list[dict[str, object]] = []
    for case in cases:
        source_bytes = case.source_srt.read_bytes()
        cue_count = len(parse_srt(case.source_srt))
        final_bilingual = bilingual_root / case.final_filename
        case_record: dict[str, object] = {
            "case_id": case.slug,
            "index": case.index,
            "episode_id": case.episode_id,
            "model_alias": case.model_alias,
            "source": {
                "path": str(case.source_srt.resolve()),
                "sha256": hashlib.sha256(source_bytes).hexdigest(),
                "cue_count": cue_count,
            },
            "sampling": _sampling_evidence(
                case.arm.temperature,
                case.arm.top_p,
            ),
            "execution": {
                "full_subtitles": True,
                "batch_size": 50,
                "batch_jobs": 1,
                "expected_translation_batches": math.ceil(cue_count / 50),
                "episode_summary": True,
            },
            "final_bilingual": str(final_bilingual.resolve()),
            "status": "pending",
        }
        case_dir = cases_root / case.slug
        case_dir.mkdir(parents=True, exist_ok=True)
        case_path = case_dir / "case.json"
        if not case_path.exists():
            case_path.write_text(
                json.dumps(case_record, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        matrix_cases.append(
            {
                "case_id": case.slug,
                "case_record": str(case_path.resolve()),
                "final_bilingual": str(final_bilingual.resolve()),
            }
        )

    matrix = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "case_count": len(cases),
        "full_subtitles": True,
        "batch_size": 50,
        "batch_jobs": 1,
        "cases": matrix_cases,
    }
    matrix_path = output_root / "matrix.json"
    matrix_path.write_text(
        json.dumps(matrix, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return matrix_path


def execute_case(
    *,
    case: CollectionCase,
    output_root: Path,
    translate_fn: Callable[..., TranslateResult] | None = None,
    episode_summary_override: str | None = None,
    glossary_path: Path | None = None,
) -> dict[str, object]:
    output_root = Path(output_root)
    case_dir = output_root / "cases" / case.slug
    case_path = case_dir / "case.json"
    if not case_path.is_file():
        raise FileNotFoundError(
            f"missing case record; run prepare_collection first: {case_path}"
        )
    record = json.loads(case_path.read_text(encoding="utf-8"))
    final_path = output_root / "bilingual" / case.final_filename
    if record.get("status") == "completed" and final_path.is_file():
        skipped = dict(record)
        skipped["resume_action"] = "skipped_completed"
        return skipped

    attempt_count = int(record.get("attempt_count") or 0) + 1
    attempt_dir = case_dir / f"attempt-{attempt_count:03d}"
    attempt_dir.mkdir(parents=True, exist_ok=False)
    started_at = datetime.now(timezone.utc).isoformat()
    record.update(
        {
            "status": "running",
            "attempt_count": attempt_count,
            "started_at": started_at,
            "active_attempt": str(attempt_dir.resolve()),
        }
    )
    _write_json(case_path, record)

    temperature: object = (
        model_client.OMIT
        if case.arm.temperature is None
        else case.arm.temperature
    )
    top_p: object = (
        model_client.OMIT if case.arm.top_p is None else case.arm.top_p
    )
    resolved_glossary = Path(glossary_path or _DEFAULT_GLOSSARY)
    summary_text = (episode_summary_override or "").strip()
    context_record = {
        "prompt": {
            "path": str(_DEFAULT_PROMPT.resolve()),
            "sha256": hashlib.sha256(_DEFAULT_PROMPT.read_bytes()).hexdigest(),
        },
        "glossary": {
            "path": str(resolved_glossary.resolve()),
            "sha256": hashlib.sha256(resolved_glossary.read_bytes()).hexdigest(),
        },
        "episode_summary": {
            "mode": "frozen_override" if episode_summary_override is not None else "generated",
            "chars": len(summary_text),
            "sha256": hashlib.sha256(summary_text.encode("utf-8")).hexdigest()
            if summary_text
            else None,
        },
    }
    record["context"] = context_record
    _write_json(case_path, record)
    attempt_record: dict[str, object] = {
        "case_id": case.slug,
        "attempt": attempt_count,
        "started_at": started_at,
        "status": "running",
        "sampling": _sampling_evidence(temperature, top_p),
        "execution": record["execution"],
        "context": context_record,
    }
    attempt_path = attempt_dir / "runner_attempt.json"
    _write_json(attempt_path, attempt_record)

    runner = translate_fn or run_once
    try:
        result = runner(
            srt_path=case.source_srt,
            model=case.model_alias,
            source_language="英语",
            target_language="简体中文",
            prompt_path=_DEFAULT_PROMPT,
            glossary_path=resolved_glossary,
            max_cues=None,
            cue_offset=0,
            max_output_tokens=131072,
            out_dir=attempt_dir,
            timeout=1200.0,
            temperature=temperature,
            top_p=top_p,
            max_retries=2,
            retry_backoff_sec=3.0,
            batch_size=50,
            batch_jobs=1,
            use_episode_summary=True,
            episode_summary_override=episode_summary_override,
        )
    except Exception as exc:  # noqa: BLE001
        finished_at = datetime.now(timezone.utc).isoformat()
        error = f"{type(exc).__name__}: {exc}"
        attempt_record.update(
            {
                "status": "failed",
                "finished_at": finished_at,
                "error": error,
            }
        )
        _write_json(attempt_path, attempt_record)
        record.update(
            {
                "status": "failed",
                "finished_at": finished_at,
                "last_error": error,
            }
        )
        _write_json(case_path, record)
        return record

    finished_at = datetime.now(timezone.utc).isoformat()
    attempt_record.update(
        {
            "status": "completed" if result.ok else "failed",
            "finished_at": finished_at,
            "result": result.meta_dict(),
        }
    )
    _write_json(attempt_path, attempt_record)
    if result.ok and result.bilingual_srt:
        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_text(result.bilingual_srt, encoding="utf-8")
        final_sha256 = hashlib.sha256(final_path.read_bytes()).hexdigest()
        record.update(
            {
                "status": "completed",
                "finished_at": finished_at,
                "result": result.meta_dict(),
                "final_bilingual_sha256": final_sha256,
            }
        )
    else:
        record.update(
            {
                "status": "failed",
                "finished_at": finished_at,
                "result": result.meta_dict(),
                "last_error": "; ".join(result.validate.errors[:3])
                or result.status,
            }
        )
    _write_json(case_path, record)
    return record


def _write_json(path: Path, value: object) -> None:
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)


def run_collection(
    *,
    cases: tuple[CollectionCase, ...],
    output_root: Path,
    limit: int | None = None,
    translate_fn: Callable[..., TranslateResult] | None = None,
    episode_summaries: dict[str, str] | None = None,
    glossary_path: Path | None = None,
) -> dict[str, object]:
    output_root = Path(output_root)
    selected = cases if limit is None else cases[: max(0, limit)]
    by_model: dict[str, list[CollectionCase]] = {}
    for case in selected:
        by_model.setdefault(case.model_alias, []).append(case)

    progress_lock = threading.Lock()

    def update_progress() -> dict[str, object]:
        with progress_lock:
            progress = _collection_progress(cases, output_root)
            _write_json(output_root / "progress.json", progress)
            return progress

    def run_model_stream(model_cases: list[CollectionCase]) -> None:
        for item in model_cases:
            summary_override = None
            if episode_summaries is not None:
                if item.episode_id not in episode_summaries:
                    raise KeyError(
                        f"missing frozen summary for {item.episode_id}"
                    )
                summary_override = episode_summaries[item.episode_id]
            execute_case(
                case=item,
                output_root=output_root,
                translate_fn=translate_fn,
                episode_summary_override=summary_override,
                glossary_path=glossary_path,
            )
            update_progress()

    update_progress()
    if by_model:
        with ThreadPoolExecutor(max_workers=len(by_model)) as executor:
            futures = [
                executor.submit(run_model_stream, model_cases)
                for model_cases in by_model.values()
            ]
            for future in as_completed(futures):
                future.result()
    return update_progress()


def _collection_progress(
    cases: tuple[CollectionCase, ...], output_root: Path
) -> dict[str, object]:
    counts = {
        "completed": 0,
        "provider_refusal": 0,
        "failed": 0,
        "running": 0,
        "pending": 0,
    }
    failed_case_ids: list[str] = []
    provider_refusal_case_ids: list[str] = []
    inspection_rescue_completed = 0
    inspection_rescue_failed = 0
    for case in cases:
        case_path = output_root / "cases" / case.slug / "case.json"
        status = "pending"
        record: dict[str, object] = {}
        if case_path.is_file():
            record = json.loads(case_path.read_text(encoding="utf-8"))
            status = str(record.get("status", "pending"))
        if status not in counts:
            status = "pending"
        counts[status] += 1
        if status == "failed":
            failed_case_ids.append(case.slug)
        if status == "provider_refusal":
            provider_refusal_case_ids.append(case.slug)
        rescue = record.get("inspection_rescue")
        if isinstance(rescue, dict):
            if rescue.get("status") == "completed":
                inspection_rescue_completed += 1
            elif rescue.get("status") == "failed":
                inspection_rescue_failed += 1
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(cases),
        **counts,
        "inspection_rescue_completed": inspection_rescue_completed,
        "inspection_rescue_failed": inspection_rescue_failed,
        "failed_case_ids": failed_case_ids,
        "provider_refusal_case_ids": provider_refusal_case_ids,
    }


def repair_failed_cases(
    *,
    cases: tuple[CollectionCase, ...],
    output_root: Path,
    repair_fn: Callable[..., TranslateResult] | None = None,
) -> dict[str, object]:
    """Repair only failed cases in place, preserving their original attempts."""
    output_root = Path(output_root)
    repairer = repair_fn or repair_run_dir
    for case in cases:
        case_dir = output_root / "cases" / case.slug
        case_path = case_dir / "case.json"
        if not case_path.is_file():
            continue
        record = json.loads(case_path.read_text(encoding="utf-8"))
        if record.get("status") != "failed":
            continue

        active_attempt = record.get("active_attempt")
        if not active_attempt:
            record["last_error"] = "failed case has no active_attempt"
            _write_json(case_path, record)
            continue
        attempt_dir = Path(str(active_attempt))
        started_at = datetime.now(timezone.utc).isoformat()
        repair_record: dict[str, object] = {
            "status": "running",
            "started_at": started_at,
            "source_attempt": str(attempt_dir.resolve()),
            "sub_batch_size": 10,
            "sampling": record.get("sampling"),
        }
        record["repair"] = repair_record
        _write_json(case_path, record)
        _write_json(attempt_dir / "repair.json", repair_record)

        temperature: object = (
            model_client.OMIT
            if case.arm.temperature is None
            else case.arm.temperature
        )
        top_p: object = (
            model_client.OMIT if case.arm.top_p is None else case.arm.top_p
        )
        try:
            result = repairer(
                attempt_dir,
                case.source_srt,
                case.model_alias,
                max_output_tokens=131072,
                timeout=1200.0,
                max_retries=2,
                retry_backoff_sec=3.0,
                temperature=temperature,
                top_p=top_p,
                sub_batch_size=10,
            )
        except Exception as exc:  # noqa: BLE001
            finished_at = datetime.now(timezone.utc).isoformat()
            error = f"{type(exc).__name__}: {exc}"
            repair_record.update(
                {"status": "failed", "finished_at": finished_at, "error": error}
            )
            record.update(
                {
                    "status": "failed",
                    "finished_at": finished_at,
                    "last_error": error,
                    "repair": repair_record,
                }
            )
            _write_json(attempt_dir / "repair.json", repair_record)
            _write_json(case_path, record)
            continue

        finished_at = datetime.now(timezone.utc).isoformat()
        result.sampling = dict(record.get("sampling") or {})
        result_meta = result.meta_dict()
        if result.ok and result.bilingual_srt:
            final_path = output_root / "bilingual" / case.final_filename
            final_path.parent.mkdir(parents=True, exist_ok=True)
            final_path.write_text(result.bilingual_srt, encoding="utf-8")
            repair_record.update(
                {
                    "status": "completed",
                    "finished_at": finished_at,
                    "result": result_meta,
                }
            )
            record.update(
                {
                    "status": "completed",
                    "finished_at": finished_at,
                    "result": result_meta,
                    "final_bilingual_sha256": hashlib.sha256(
                        final_path.read_bytes()
                    ).hexdigest(),
                    "repair": repair_record,
                }
            )
            record.pop("last_error", None)
        else:
            error = "; ".join(result.validate.errors[:3]) or result.status
            repair_record.update(
                {
                    "status": "failed",
                    "finished_at": finished_at,
                    "error": error,
                    "result": result_meta,
                }
            )
            record.update(
                {
                    "status": "failed",
                    "finished_at": finished_at,
                    "last_error": error,
                    "result": result_meta,
                    "repair": repair_record,
                }
            )
        _write_json(attempt_dir / "repair.json", repair_record)
        _write_json(case_path, record)
        _write_json(
            output_root / "progress.json",
            _collection_progress(cases, output_root),
        )

    progress = _collection_progress(cases, output_root)
    _write_json(output_root / "progress.json", progress)
    return progress


def rescue_inspection_blocked_cases(
    *,
    cases: tuple[CollectionCase, ...],
    output_root: Path,
    rescue_fn: Callable[..., TranslateResult] | None = None,
) -> dict[str, object]:
    """Apply the audited glossary-placeholder rescue to remaining failures."""
    output_root = Path(output_root)
    rescuer = rescue_fn or rescue_inspection_run_dir
    for case in cases:
        case_path = output_root / "cases" / case.slug / "case.json"
        if not case_path.is_file():
            continue
        record = json.loads(case_path.read_text(encoding="utf-8"))
        if record.get("status") not in {"failed", "provider_refusal"}:
            continue
        existing_rescue = record.get("inspection_rescue")
        if (
            isinstance(existing_rescue, dict)
            and existing_rescue.get("status") == "completed"
        ):
            continue
        attempt_dir = Path(str(record.get("active_attempt") or ""))
        if not attempt_dir.is_dir():
            record["last_error"] = "failed case has no active attempt directory"
            record["status"] = "provider_refusal"
            _write_json(case_path, record)
            continue

        full_input = json.loads(
            (attempt_dir / "input.json").read_text(encoding="utf-8")
        )
        parsed = json.loads(
            (attempt_dir / "parsed.json").read_text(encoding="utf-8")
        )
        missing_cue_ids = sorted(set(full_input) - set(parsed), key=int)
        record["primary_outcome"] = {
            "status": "provider_refusal",
            "provider_code": "DataInspectionFailed",
            "missing_cue_ids": missing_cue_ids,
            "completed_cues": len(parsed),
            "expected_cues": len(full_input),
        }

        previous_repair = record.get("repair")
        if (
            isinstance(previous_repair, dict)
            and previous_repair.get("status") == "running"
        ):
            previous_repair["status"] = "interrupted"
            previous_repair["note"] = (
                "superseded by audited provider-inspection rescue"
            )

        started_at = datetime.now(timezone.utc).isoformat()
        rescue_record: dict[str, object] = {
            "status": "running",
            "started_at": started_at,
            "source_attempt": str(attempt_dir.resolve()),
            "strategy": "opaque_glossary_placeholder",
            "sampling": record.get("sampling"),
            "missing_cue_ids": missing_cue_ids,
        }
        record["inspection_rescue"] = rescue_record
        _write_json(case_path, record)

        temperature: object = (
            model_client.OMIT
            if case.arm.temperature is None
            else case.arm.temperature
        )
        top_p: object = (
            model_client.OMIT if case.arm.top_p is None else case.arm.top_p
        )
        try:
            result = rescuer(
                run_dir=attempt_dir,
                srt_path=case.source_srt,
                model=case.model_alias,
                temperature=temperature,
                top_p=top_p,
            )
        except Exception as exc:  # noqa: BLE001
            finished_at = datetime.now(timezone.utc).isoformat()
            error = f"{type(exc).__name__}: {exc}"
            rescue_record.update(
                {"status": "failed", "finished_at": finished_at, "error": error}
            )
            record.update(
                {
                    "status": "provider_refusal",
                    "last_error": error,
                    "inspection_rescue": rescue_record,
                }
            )
            _write_json(case_path, record)
            continue

        finished_at = datetime.now(timezone.utc).isoformat()
        result.sampling = dict(record.get("sampling") or {})
        result_meta = result.meta_dict()
        if result.ok and result.bilingual_srt:
            rescue_path = (
                output_root
                / "bilingual"
                / f"{case.slug}__inspection-rescue.srt"
            )
            rescue_path.parent.mkdir(parents=True, exist_ok=True)
            rescue_path.write_text(result.bilingual_srt, encoding="utf-8")
            rescue_sha256 = hashlib.sha256(rescue_path.read_bytes()).hexdigest()
            rescue_record.update(
                {
                    "status": "completed",
                    "finished_at": finished_at,
                    "output": str(rescue_path.resolve()),
                    "output_sha256": rescue_sha256,
                    "result": result_meta,
                }
            )
            record.update(
                {
                    "status": "provider_refusal",
                    "inspection_rescue": rescue_record,
                }
            )
        else:
            error = "; ".join(result.validate.errors[:3]) or result.status
            rescue_record.update(
                {"status": "failed", "finished_at": finished_at, "error": error}
            )
            record.update(
                {
                    "status": "provider_refusal",
                    "last_error": error,
                    "inspection_rescue": rescue_record,
                }
            )
        _write_json(case_path, record)
        _write_json(
            output_root / "progress.json",
            _collection_progress(cases, output_root),
        )

    progress = _collection_progress(cases, output_root)
    _write_json(output_root / "progress.json", progress)
    return progress


def generate_frozen_summaries(
    *,
    episodes: tuple[tuple[str, Path], ...],
    output_root: Path,
    model_alias: str,
    generate_fn: Callable[..., tuple] | None = None,
) -> dict[str, str]:
    output_root = Path(output_root)
    summaries: dict[str, str] = {}
    generator = generate_fn or generate_episode_summary
    for episode_id, source_srt in episodes:
        source_srt = Path(source_srt)
        source_sha256 = hashlib.sha256(source_srt.read_bytes()).hexdigest()
        context_dir = output_root / "context" / episode_id
        summary_path = context_dir / "episode_summary.txt"
        meta_path = context_dir / "fixed_summary.json"
        if summary_path.is_file() and meta_path.is_file():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("source_sha256") != source_sha256:
                raise ValueError(
                    f"frozen summary source changed for {episode_id}"
                )
            summaries[episode_id] = summary_path.read_text(
                encoding="utf-8"
            ).strip()
            continue

        context_dir.mkdir(parents=True, exist_ok=True)
        cues = slice_cues(parse_srt(source_srt), cue_offset=0, max_cues=None)
        summary, usage, status, error = generator(
            model_alias,
            cues,
            max_output_tokens=2048,
            timeout=180.0,
            temperature=model_client.OMIT,
            top_p=model_client.OMIT,
            max_retries=2,
            retry_backoff_sec=3.0,
            out_dir=context_dir,
        )
        summary = (summary or "").strip()
        if error or status != "completed" or not summary:
            raise RuntimeError(
                f"fixed summary failed for {episode_id}: "
                f"status={status} error={error}"
            )
        summary_path.write_text(summary + "\n", encoding="utf-8")
        meta = {
            "episode_id": episode_id,
            "source_path": str(source_srt.resolve()),
            "source_sha256": source_sha256,
            "cue_count": len(cues),
            "generator_model_alias": model_alias,
            "sampling": _sampling_evidence(
                model_client.OMIT,
                model_client.OMIT,
            ),
            "summary_sha256": hashlib.sha256(
                summary.encode("utf-8")
            ).hexdigest(),
            "summary_chars": len(summary),
            "status": status,
            "usage": {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "reasoning_tokens": usage.reasoning_tokens,
                "total_tokens": usage.total_tokens,
            },
        }
        _write_json(meta_path, meta)
        summaries[episode_id] = summary
    return summaries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect the frozen 40-output subtitle sampling matrix."
    )
    parser.add_argument(
        "command",
        choices=(
            "plan",
            "summaries",
            "run",
            "repair",
            "inspection-rescue",
            "status",
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_ROOT / "out" / "opus46-low-parity-full-matrix-20260809",
    )
    parser.add_argument(
        "--summary-model",
        default="deepseek-v4-flash",
    )
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    output_root = Path(args.out)
    cases = build_default_cases()
    episodes = tuple(
        dict.fromkeys(
            (case.episode_id, case.source_srt)
            for case in cases
        )
    )

    if args.command == "status":
        progress_path = output_root / "progress.json"
        if not progress_path.is_file():
            print(f"no progress yet: {progress_path}")
            return 1
        print(progress_path.read_text(encoding="utf-8"), end="")
        return 0

    matrix_path = prepare_collection(cases=cases, output_root=output_root)
    translation_batches = sum(
        math.ceil(len(parse_srt(case.source_srt)) / 50)
        for case in cases
    )
    print(
        f"matrix: cases={len(cases)} translation_batches={translation_batches} "
        f"summary_calls=2 out={output_root}"
    )
    if args.command == "plan":
        print(f"manifest: {matrix_path}")
        return 0

    if args.command == "repair":
        progress = repair_failed_cases(
            cases=cases,
            output_root=output_root,
        )
        print(json.dumps(progress, ensure_ascii=False, indent=2))
        return 0 if progress["failed"] == 0 else 1

    if args.command == "inspection-rescue":
        progress = rescue_inspection_blocked_cases(
            cases=cases,
            output_root=output_root,
        )
        print(json.dumps(progress, ensure_ascii=False, indent=2))
        return 0 if (
            progress["inspection_rescue_failed"] == 0
            and progress["inspection_rescue_completed"]
            == progress["provider_refusal"]
        ) else 1

    summaries = generate_frozen_summaries(
        episodes=episodes,
        output_root=output_root,
        model_alias=args.summary_model,
    )
    print(
        "frozen summaries: "
        + ", ".join(f"{key}={len(value)} chars" for key, value in summaries.items())
    )
    if args.command == "summaries":
        return 0

    progress = run_collection(
        cases=cases,
        output_root=output_root,
        limit=args.limit,
        episode_summaries=summaries,
        glossary_path=_DEFAULT_GLOSSARY,
    )
    print(json.dumps(progress, ensure_ascii=False, indent=2))
    return 0 if progress["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
