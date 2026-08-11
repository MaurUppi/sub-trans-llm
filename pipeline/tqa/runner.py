"""Stage dispatcher and immutable plan builder for TQA bench."""

from __future__ import annotations

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from pipeline.srt_io import parse_srt
from pipeline.tqa.aggregate import build_report, write_report
from pipeline.tqa.evaluation import (
    Evaluator,
    build_anonymous_inputs,
    default_evaluator,
    evaluate_anonymous_inputs,
)
from pipeline.tqa.profile import ProfileError, ResolvedProfile, load_profile


_ASSET_ROOT = Path(__file__).resolve().parent


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _write_json_atomic(path: Path, value: object) -> None:
    _write_text_atomic(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _sampling_evidence(value: object) -> dict[str, object]:
    if value == "OMIT":
        return {"sent": False, "value": None}
    return {"sent": True, "value": float(value)}


def _framework_lock() -> dict[str, object]:
    document = _ASSET_ROOT / "framework_v2.md"
    schema = _ASSET_ROOT / "framework_v2.schema.yaml"
    meta = _ASSET_ROOT / "framework_v2.meta.yaml"
    profile_schema = _ASSET_ROOT / "profile_v2.schema.yaml"
    evaluator_schema = _ASSET_ROOT / "evaluator_response.schema.yaml"
    assessment_schema = _ASSET_ROOT / "assessment_record.schema.yaml"
    return {
        "version": "v2",
        "document": str(document),
        "document_sha256": _sha256(document),
        "machine_schema": str(schema),
        "machine_schema_sha256": _sha256(schema),
        "metadata": str(meta),
        "metadata_sha256": _sha256(meta),
        "profile_schema": str(profile_schema),
        "profile_schema_sha256": _sha256(profile_schema),
        "evaluator_response_schema": str(evaluator_schema),
        "evaluator_response_schema_sha256": _sha256(evaluator_schema),
        "assessment_record_schema": str(assessment_schema),
        "assessment_record_schema_sha256": _sha256(assessment_schema),
    }


def _case_slug(index: int, episode: str, model: str, arm: dict[str, object]) -> str:
    def label(value: object) -> str:
        return "OMIT" if value == "OMIT" else str(value)

    safe_model = model.replace("/", "_")
    return (
        f"{index:03d}__{episode}__{safe_model}__"
        f"temperature-{label(arm['temperature'])}__topP-{label(arm['top_p'])}"
    )


def _build_manifest(profile: ResolvedProfile, framework: dict[str, object]) -> dict[str, Any]:
    data = profile.data
    for episode in data["inputs"]["episodes"]:
        source_ids = {
            cue.seq for cue in parse_srt(Path(episode["source_srt"]))
        }
        sample_ids = {int(sample["cue_id"]) for sample in episode["samples"]}
        missing = sorted(
            cue_id for cue_id in sample_ids if cue_id not in source_ids
        )
        if missing:
            raise ProfileError(
                f"episode {episode['id']} samples reference missing cue ids: "
                + ", ".join(str(value) for value in missing)
            )
        if data["tqa"]["reference_mode"] == "single_reference":
            reference_ids = {
                cue.seq for cue in parse_srt(Path(episode["reference_srt"]))
            }
            missing_reference = sorted(
                cue_id for cue_id in sample_ids if cue_id not in reference_ids
            )
            if missing_reference:
                raise ProfileError(
                    f"episode {episode['id']} reference_srt is missing sample cue ids: "
                    + ", ".join(str(value) for value in missing_reference)
                )
    cases: list[dict[str, object]] = []
    for model in data["translation"]["models"]:
        for arm in data["sampling"]["arms"]:
            for episode in data["inputs"]["episodes"]:
                index = len(cases) + 1
                case_id = _case_slug(index, episode["id"], model, arm)
                source = Path(episode["source_srt"])
                cases.append(
                    {
                        "case_id": case_id,
                        "index": index,
                        "episode_id": episode["id"],
                        "model_alias": model,
                        "temperature": _sampling_evidence(arm["temperature"]),
                        "top_p": _sampling_evidence(arm["top_p"]),
                        "source": {
                            "path": str(source),
                            "sha256": _sha256(source),
                            "cue_count": len(parse_srt(source)),
                        },
                        "case_dir": str(profile.output_root / "cases" / case_id),
                        "status": "pending",
                    }
                )
    translation_files: dict[str, object] = {}
    for field in ("prompt", "glossary"):
        value = data["translation"][field]
        translation_files[field] = (
            None
            if value is None
            else {"path": value, "sha256": _sha256(Path(value))}
        )
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "profile_path": str(profile.source_path),
        "framework": framework,
        "project": data["project"],
        "inputs": data["inputs"],
        "translation": data["translation"],
        "sampling": data["sampling"],
        "execution": data["execution"],
        "tqa": data["tqa"],
        "evaluator": data["evaluator"],
        "translation_files": translation_files,
        "case_count": len(cases),
        "cases": cases,
    }


def _verify_manifest_inputs(manifest: dict[str, Any]) -> None:
    checked_sources: set[str] = set()
    for case in manifest["cases"]:
        source = case["source"]
        path = Path(source["path"])
        if str(path) in checked_sources:
            continue
        checked_sources.add(str(path))
        if not path.is_file() or _sha256(path) != source["sha256"]:
            raise ProfileError(f"frozen source input drifted: {path}")
    for field, record in manifest["translation_files"].items():
        if record is None:
            continue
        path = Path(record["path"])
        if not path.is_file() or _sha256(path) != record["sha256"]:
            raise ProfileError(f"frozen translation {field} drifted: {path}")
    current_framework = _framework_lock()
    for field, expected in manifest["framework"].items():
        if field.endswith("_sha256") and current_framework.get(field) != expected:
            raise ProfileError(f"frozen framework asset drifted: {field}")


def plan(profile_path: Path) -> Path:
    profile = load_profile(profile_path)
    output = profile.output_root
    framework = _framework_lock()
    profile_hash = hashlib.sha256(profile.source_text.encode("utf-8")).hexdigest()
    lock_path = output / "profile.lock.json"
    if lock_path.is_file():
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        if lock.get("profile_sha256") != profile_hash:
            raise ProfileError(
                f"output root is frozen by a different profile: {output}"
            )
        if lock.get("framework") != framework:
            raise ProfileError(
                f"output root is frozen by a different framework build: {output}"
            )
        return output

    output.mkdir(parents=True, exist_ok=True)
    lock = {
        "schema_version": 1,
        "profile_path": str(profile.source_path),
        "profile_sha256": profile_hash,
        "framework": framework,
    }
    manifest = _build_manifest(profile, framework)
    progress = {
        "schema_version": 1,
        "state": "planned",
        "counts": {
            "cases_total": len(manifest["cases"]),
            "cases_completed": 0,
            "provider_refusals": 0,
            "technical_failures": 0,
            "assessment_inputs": 0,
            "assessment_records": 0,
            "hard_failures": 0,
        },
        "stages": {
            "plan": "completed",
            "collect": "pending",
            "evaluate": "pending",
            "report": "pending",
        },
    }
    _write_text_atomic(output / "profile.source.yaml", profile.source_text)
    _write_text_atomic(
        output / "profile.resolved.yaml",
        yaml.safe_dump(profile.data, allow_unicode=True, sort_keys=False),
    )
    _write_json_atomic(output / "manifest.json", manifest)
    _write_json_atomic(output / "progress.json", progress)
    _write_json_atomic(lock_path, lock)
    return output


def _default_translate(**request: object) -> dict[str, object]:
    from pipeline.orchestrator import run_once

    profile = request["profile"]
    temperature = request["temperature"]
    top_p = request["top_p"]
    summary = profile["translation"]["summary"]
    summary_override = request.get("episode_summary_override")
    result = run_once(
        request["source_srt"],
        str(request["model"]),
        source_language=profile["project"]["source_language"],
        target_language=profile["project"]["target_language"],
        prompt_path=profile["translation"]["prompt"],
        glossary_path=profile["translation"]["glossary"],
        max_output_tokens=int(profile["execution"]["max_output_tokens"]),
        out_dir=request["out_dir"],
        timeout=float(profile["execution"]["timeout"]),
        temperature=temperature,
        top_p=top_p,
        max_retries=int(profile["execution"]["max_retries"]),
        retry_backoff_sec=float(profile["execution"]["retry_backoff"]),
        batch_size=int(profile["execution"]["batch_size"]),
        batch_jobs=int(profile["execution"]["batch_jobs"]),
        use_episode_summary=summary["mode"] == "generate_once",
        summary_max_output_tokens=int(summary.get("max_output_tokens", 4096)),
        summary_timeout=float(summary.get("timeout", 180)),
        episode_summary_override=(
            str(summary_override) if summary_override is not None else None
        ),
        api_mode=profile["translation"]["api_mode"],
    )
    translations: dict[str, str] = {}
    parsed = result.validate.parsed or {}
    for index, cue in enumerate(result.cues):
        value = parsed.get(str(index)) or parsed.get(cue.id) or {}
        if isinstance(value, dict):
            translations[str(cue.seq)] = str(value.get("tr") or "")
    status = "completed" if result.ok else "technical_failure"
    failure_text = " ".join(
        [result.status, result.incomplete_reason or "", *result.validate.errors]
    ).lower()
    refusal_markers = (
        "refusal",
        "safety",
        "content policy",
        "content_filter",
        "content filter",
        "blocked",
    )
    technical_markers = (
        "connection",
        "timeout",
        "timed out",
        "rate limit",
        "ratelimit",
        "network",
    )
    empty_provider_output = (
        not result.raw_text.strip()
        and "empty" in failure_text
        and not any(token in failure_text for token in technical_markers)
    )
    if any(token in failure_text for token in refusal_markers) or empty_provider_output:
        status = "provider_refusal"
    return {
        "status": status,
        "translations": translations,
        "usage": result.meta_dict()["usage"],
        "meta": result.meta_dict(),
        "episode_summary": result.episode_summary,
    }


def _update_progress(
    output: Path,
    *,
    state: str,
    stage: str,
    counts: dict[str, int] | None = None,
) -> None:
    path = output / "progress.json"
    progress = json.loads(path.read_text(encoding="utf-8"))
    progress["state"] = state
    progress["stages"][stage] = "completed"
    if counts:
        progress.setdefault("counts", {}).update(counts)
    _write_json_atomic(path, progress)


def collect(
    *,
    profile: ResolvedProfile,
    manifest: dict[str, Any],
    translate_fn: Any,
) -> None:
    def collect_case(case: dict[str, Any]) -> None:
        case_dir = profile.output_root / "cases" / case["case_id"]
        candidate_path = case_dir / "candidate.json"
        summary_mode = profile.data["translation"]["summary"]["mode"]
        summary_name = (
            f"{case['episode_id']}__{case['model_alias'].replace('/', '_')}.txt"
        )
        summary_path = profile.output_root / "summaries" / summary_name
        if candidate_path.is_file():
            if summary_mode == "generate_once" and not summary_path.is_file():
                existing = json.loads(candidate_path.read_text(encoding="utf-8"))
                summary_text = str(existing.get("episode_summary") or "").strip()
                if summary_text:
                    _write_text_atomic(summary_path, summary_text + "\n")
            return
        case_dir.mkdir(parents=True, exist_ok=True)
        temperature = (
            case["temperature"]["value"] if case["temperature"]["sent"] else None
        )
        top_p = case["top_p"]["value"] if case["top_p"]["sent"] else None
        summary_override = None
        if summary_mode == "generate_once" and summary_path.is_file():
            summary_override = summary_path.read_text(encoding="utf-8").strip()
        try:
            candidate = translate_fn(
                source_srt=Path(case["source"]["path"]),
                model=case["model_alias"],
                temperature=temperature,
                top_p=top_p,
                out_dir=case_dir / "run",
                profile=profile.data,
                episode_summary_override=summary_override,
            )
            if not isinstance(candidate, dict):
                raise TypeError("translation adapter must return a mapping")
            if candidate.get("status") not in {
                "completed",
                "provider_refusal",
                "technical_failure",
            }:
                raise ValueError("translation adapter returned an invalid status")
            generated_summary = str(candidate.get("episode_summary") or "").strip()
            if (
                summary_mode == "generate_once"
                and generated_summary
                and not summary_path.is_file()
            ):
                _write_text_atomic(summary_path, generated_summary + "\n")
        except Exception as exc:  # noqa: BLE001
            candidate = {
                "status": "technical_failure",
                "translations": {},
                "error": f"{type(exc).__name__}: {exc}",
            }
        _write_json_atomic(candidate_path, candidate)

    by_model: dict[str, list[dict[str, Any]]] = {}
    for case in manifest["cases"]:
        by_model.setdefault(case["model_alias"], []).append(case)

    def collect_model(cases: list[dict[str, Any]]) -> None:
        for case in cases:
            collect_case(case)

    jobs = min(int(profile.data["execution"]["model_jobs"]), len(by_model))
    if jobs <= 1:
        for cases in by_model.values():
            collect_model(cases)
    else:
        with ThreadPoolExecutor(max_workers=jobs) as executor:
            futures = [executor.submit(collect_model, cases) for cases in by_model.values()]
            for future in futures:
                future.result()
    statuses = [
        json.loads(
            (
                profile.output_root
                / "cases"
                / case["case_id"]
                / "candidate.json"
            ).read_text(encoding="utf-8")
        )["status"]
        for case in manifest["cases"]
    ]
    _update_progress(
        profile.output_root,
        state="collected",
        stage="collect",
        counts={
            "cases_completed": statuses.count("completed"),
            "provider_refusals": statuses.count("provider_refusal"),
            "technical_failures": statuses.count("technical_failure"),
        },
    )


def run_pipeline(
    *,
    profile_path: Path,
    action: str,
    translate_fn: Any | None = None,
    evaluate_fn: Evaluator | None = None,
) -> int:
    try:
        if action == "status":
            status_profile = load_profile(profile_path)
            progress_path = status_profile.output_root / "progress.json"
            if not progress_path.is_file():
                raise ProfileError(
                    "bench status requires an existing plan: "
                    f"{status_profile.output_root}"
                )
            print(progress_path.read_text(encoding="utf-8"), end="")
            return 0
        output = plan(profile_path)
        profile = load_profile(profile_path)
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        progress = json.loads((output / "progress.json").read_text(encoding="utf-8"))
        if action == "plan":
            return 0
        _verify_manifest_inputs(manifest)
        if action == "evaluate" and progress["stages"]["collect"] != "completed":
            raise ProfileError("bench evaluate requires a completed collect stage")
        if action == "report" and progress["stages"]["evaluate"] != "completed":
            raise ProfileError("bench report requires a completed evaluate stage")
        if action in {"all", "collect"}:
            collect(
                profile=profile,
                manifest=manifest,
                translate_fn=translate_fn or _default_translate,
            )
            if action == "collect":
                return 0
        inputs, blind_map = build_anonymous_inputs(
            output=output, profile=profile.data, manifest=manifest
        )
        if action in {"all", "evaluate"}:
            records = evaluate_anonymous_inputs(
                output=output,
                profile=profile.data,
                inputs=inputs,
                blind_map=blind_map,
                evaluate_fn=evaluate_fn or default_evaluator(profile.data),
            )
            enabled_hard = set(profile.data["tqa"]["hard_failures"]["enabled"])
            _update_progress(
                output,
                state="evaluated",
                stage="evaluate",
                counts={
                    "assessment_inputs": len(inputs),
                    "assessment_records": len(records),
                    "hard_failures": len(
                        {
                            (record["anonymous_id"], hard_failure)
                            for record in records
                            for hard_failure in record["hard_failures"]
                            if hard_failure in enabled_hard
                        }
                    ),
                },
            )
            if action == "evaluate":
                return 0
        else:
            record_path = output / "assessments" / "records.jsonl"
            records = [
                json.loads(line)
                for line in record_path.read_text(encoding="utf-8").splitlines()
            ]
        if action in {"all", "report"}:
            report = build_report(
                profile=profile.data,
                manifest=manifest,
                blind_map=blind_map,
                records=records,
            )
            write_report(output, report)
            _update_progress(
                output, state="awaiting_user_decision", stage="report"
            )
            return 0
        raise ProfileError(f"unknown bench action: {action}")
    except (OSError, ProfileError, RuntimeError) as exc:
        print(f"bench {action}: {exc}")
        return 2


def run_bench(*, profile_path: Path, action: str) -> int:
    code = run_pipeline(profile_path=profile_path, action=action)
    if code == 0:
        profile = load_profile(profile_path)
        print(f"bench {action}: {profile.output_root}")
    return code
