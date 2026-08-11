"""Anonymous evaluator input, output validation, and resumable scoring."""

from __future__ import annotations

import hashlib
import json
import os
import random
from pathlib import Path
from statistics import mean, median
from typing import Any, Callable

import model_client
import yaml
from jsonschema import Draft202012Validator

from pipeline.srt_io import parse_srt
from pipeline.tqa.profile import ProfileError


Evaluator = Callable[[dict[str, object]], dict[str, object]]
_HARD_FAILURES = {
    "SEMANTIC_INVERSION",
    "IDENTITY_CONFUSION",
    "CRITICAL_OMISSION",
    "FABRICATION",
    "OFFENSIVE_MISTRANSLATION",
}
_FORBIDDEN = {
    "model_alias",
    "provider",
    "parameter_arm",
    "source_path",
    "case_path",
    "original_filename",
    "rescue_status",
    "refusal_detected",
}
_ASSESSMENT_SCHEMA = yaml.safe_load(
    (Path(__file__).resolve().parent / "assessment_record.schema.yaml").read_text(
        encoding="utf-8"
    )
)
Draft202012Validator.check_schema(_ASSESSMENT_SCHEMA)
_ASSESSMENT_VALIDATOR = Draft202012Validator(_ASSESSMENT_SCHEMA)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_private_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write(
                json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n"
            )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    temporary.replace(path)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    text = "".join(_canonical(row) + "\n" for row in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def build_anonymous_inputs(
    *, output: Path, profile: dict[str, Any], manifest: dict[str, Any]
) -> tuple[list[dict[str, object]], dict[str, object]]:
    input_path = output / "anonymized" / "eval_input.jsonl"
    map_path = output / "blind_map.json"
    if input_path.is_file() and map_path.is_file():
        rows = [json.loads(line) for line in input_path.read_text(encoding="utf-8").splitlines()]
        return rows, json.loads(map_path.read_text(encoding="utf-8"))

    episodes = {item["id"]: item for item in profile["inputs"]["episodes"]}
    rows: list[dict[str, object]] = []
    candidates: dict[str, dict[str, object]] = {}
    samples_map: dict[str, dict[str, object]] = {}
    for case in manifest["cases"]:
        candidate_path = output / "cases" / case["case_id"] / "candidate.json"
        if not candidate_path.is_file():
            continue
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        episode = episodes[case["episode_id"]]
        source_cues = {str(cue.seq): cue for cue in parse_srt(episode["source_srt"])}
        reference_cues: dict[str, Any] = {}
        if episode.get("reference_srt"):
            reference_cues = {
                str(cue.seq): cue for cue in parse_srt(episode["reference_srt"])
            }
        window = int(profile["evaluator"]["context_window"])
        ordered = list(source_cues.values())
        positions = {str(cue.seq): index for index, cue in enumerate(ordered)}
        lanes: list[tuple[str, str, dict[str, str]]] = [
            (
                "primary",
                str(candidate["status"]),
                candidate.get("translations") or {},
            )
        ]
        rescued = candidate.get("rescued_translations") or {}
        if candidate["status"] == "provider_refusal" and rescued:
            lanes.append(("rescue", "completed", rescued))
        for lane, status, translations in lanes:
            blind_id = f"candidate-{len(candidates) + 1:04d}"
            candidates[blind_id] = {
                "case_id": case["case_id"],
                "status": status,
                "lane": lane,
            }
            if status == "technical_failure":
                continue
            for sample in episode["samples"]:
                cue_key = str(sample["cue_id"])
                cue = source_cues.get(cue_key)
                if cue is None:
                    raise ProfileError(
                        f"sample cue_id {cue_key} is absent from episode {episode['id']}"
                    )
                position = positions[cue_key]
                before = [
                    item.text
                    for item in ordered[max(0, position - window) : position]
                ]
                after = [
                    item.text
                    for item in ordered[position + 1 : position + 1 + window]
                ]
                public_sample_id = f"sample-{len(samples_map) + 1:06d}"
                samples_map[public_sample_id] = {
                    "case_id": case["case_id"],
                    "candidate_id": blind_id,
                    "episode_id": case["episode_id"],
                    "cue_id": sample["cue_id"],
                    "lane": lane,
                }
                for dimension in sample["dimensions"]:
                    anonymous_id = f"assessment-{len(rows) + 1:06d}"
                    row = {
                        "anonymous_id": anonymous_id,
                        "candidate_id": blind_id,
                        "sample_id": public_sample_id,
                        "dimension": dimension,
                        "source_text": cue.text,
                        "target_text": translations.get(cue_key, ""),
                        "reference_text": (
                            reference_cues[cue_key].text
                            if cue_key in reference_cues
                            else None
                        ),
                        "context_before": before,
                        "context_after": after,
                        "dimension_instance": profile["tqa"]["dimension_instances"][dimension],
                        "test_note": sample["note"],
                        "scoring_anchors": {
                            "10": "excellent",
                            "8-9": "very good",
                            "6-7": "acceptable",
                            "4-5": "major defects",
                            "2-3": "critical defects",
                            "0-1": "failed or untranslated",
                        },
                    }
                    if set(row) & _FORBIDDEN:
                        raise AssertionError(
                            "anonymous evaluator payload leaked provenance"
                        )
                    rows.append(row)
    seed = int(profile["evaluator"]["random_seed"])
    random.Random(seed).shuffle(rows)
    blind_map: dict[str, object] = {
        "schema_version": 1,
        "candidates": candidates,
        "samples": samples_map,
    }
    _write_jsonl(input_path, rows)
    _write_private_json(map_path, blind_map)
    return rows, blind_map


def _validate_response(
    response: object, *, payload: dict[str, object], run_id: str
) -> dict[str, object]:
    if not isinstance(response, dict):
        raise ProfileError("evaluator response must be a JSON object")
    required = {
        "sample_id",
        "dimension",
        "score",
        "hard_failures",
        "rationale",
        "confidence",
        "evaluator_run_id",
    }
    missing = required - set(response)
    if missing:
        raise ProfileError(f"evaluator response missing: {', '.join(sorted(missing))}")
    extra = set(response) - required
    if extra:
        raise ProfileError(
            f"evaluator response has unexpected fields: {', '.join(sorted(extra))}"
        )
    if response["sample_id"] != payload["sample_id"]:
        raise ProfileError("evaluator sample_id mismatch")
    if response["dimension"] != payload["dimension"]:
        raise ProfileError("evaluator dimension mismatch")
    if response["evaluator_run_id"] != run_id:
        raise ProfileError("evaluator_run_id mismatch")
    score = response["score"]
    if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 10:
        raise ProfileError("evaluator score must be an integer from 0 to 10")
    hard = response["hard_failures"]
    if (
        not isinstance(hard, list)
        or len(hard) != len(set(hard))
        or set(hard) - _HARD_FAILURES
    ):
        raise ProfileError("evaluator hard_failures contains an unknown category")
    confidence = response["confidence"]
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= float(confidence) <= 1
    ):
        raise ProfileError("evaluator confidence must be between 0 and 1")
    if not isinstance(response["rationale"], str) or not response["rationale"].strip():
        raise ProfileError("evaluator rationale must be non-empty")
    return dict(response)


def default_evaluator(profile: dict[str, Any]) -> Evaluator:
    evaluator = profile["evaluator"]

    def call(payload: dict[str, object]) -> dict[str, object]:
        instructions = (
            "You are an anonymous subtitle translation quality evaluator. "
            "Return only one JSON object with exactly these fields: sample_id, "
            "dimension, score (integer 0..10), hard_failures (array), rationale, "
            "confidence (0..1), evaluator_run_id. Echo identifiers exactly."
        )
        result = model_client.call(
            evaluator["model"],
            _canonical(payload),
            instructions=instructions,
            max_output_tokens=int(evaluator.get("max_output_tokens", 2048)),
            timeout=float(evaluator.get("timeout", 300)),
            temperature=float(evaluator["temperature"]),
            top_p=evaluator.get("top_p"),
            api_mode=evaluator.get("api_mode", profile["translation"]["api_mode"]),
        )
        text = result.text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1])
            if text.lstrip().startswith("json"):
                text = text.lstrip()[4:].lstrip()
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ProfileError("evaluator returned non-object JSON")
        return value

    return call


def evaluate_anonymous_inputs(
    *,
    output: Path,
    profile: dict[str, Any],
    inputs: list[dict[str, object]],
    blind_map: dict[str, object],
    evaluate_fn: Evaluator,
) -> list[dict[str, object]]:
    record_path = output / "assessments" / "records.jsonl"
    records: list[dict[str, object]] = []
    if record_path.is_file():
        records = [
            json.loads(line)
            for line in record_path.read_text(encoding="utf-8").splitlines()
        ]
    completed = {
        (str(row["anonymous_id"]), int(row["run_index"])) for row in records
    }
    seen_run_ids = {str(row["evaluator_run_id"]) for row in records}
    candidate_meta = blind_map["candidates"]
    runs = int(profile["evaluator"]["runs"])
    max_retries = int(profile["evaluator"]["max_retries"])
    orders: list[dict[str, object]] = []
    for run_index in range(runs):
        order = list(inputs)
        seed = int(profile["evaluator"]["random_seed"]) + run_index
        random.Random(seed).shuffle(order)
        orders.append(
            {
                "run_index": run_index,
                "seed": seed,
                "anonymous_ids": [row["anonymous_id"] for row in order],
            }
        )
        for item in order:
            key = (str(item["anonymous_id"]), run_index)
            if key in completed:
                continue
            run_id = "eval-" + hashlib.sha256(
                f"{item['anonymous_id']}:{run_index}".encode("utf-8")
            ).hexdigest()[:20]
            if run_id in seen_run_ids:
                raise RuntimeError(f"duplicate evaluator_run_id: {run_id}")
            candidate = candidate_meta[item["candidate_id"]]
            refusal = candidate["status"] == "provider_refusal"
            retry_index = 0
            if refusal:
                refusal_cfg = profile["tqa"]["refusal_handling"]
                hard_failures = (
                    [refusal_cfg["hard_failure_type"]]
                    if refusal_cfg["mark_as_hard_failure"]
                    else []
                )
                validated = {
                    "sample_id": item["sample_id"],
                    "dimension": item["dimension"],
                    "score": int(refusal_cfg["default_score"]),
                    "hard_failures": hard_failures,
                    "rationale": "Provider refusal; system-assigned score.",
                    "confidence": 1.0,
                    "evaluator_run_id": run_id,
                }
                scored_by = "system"
                raw = validated
            else:
                scored_by = "evaluator"
                request = dict(item)
                request["evaluator_run_id"] = run_id
                last_error: Exception | None = None
                for retry_index in range(max_retries + 1):
                    try:
                        raw = evaluate_fn(request)
                    except Exception as exc:  # noqa: BLE001
                        last_error = exc
                        continue
                    if (
                        isinstance(raw, dict)
                        and str(raw.get("evaluator_run_id")) in seen_run_ids
                    ):
                        raise RuntimeError(
                            "duplicate evaluator_run_id returned by evaluator: "
                            f"{raw.get('evaluator_run_id')}"
                        )
                    try:
                        validated = _validate_response(raw, payload=item, run_id=run_id)
                        break
                    except (ValueError, TypeError, json.JSONDecodeError) as exc:
                        last_error = exc
                else:
                    raise ProfileError(
                        f"EVALUATION_FAILURE {item['anonymous_id']}: {last_error}"
                    )
            raw_text = _canonical(raw)
            raw_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
            raw_path = output / "assessments" / "raw" / f"{run_id}.json"
            _write_json(raw_path, raw)
            record = {
                **validated,
                "anonymous_id": item["anonymous_id"],
                "candidate_id": item["candidate_id"],
                "scored_by": scored_by,
                "refusal_detected": refusal,
                "raw_response_hash": raw_hash,
                "raw_response_path": str(raw_path),
                "valid": True,
                "retry_index": retry_index,
                "run_index": run_index,
            }
            _ASSESSMENT_VALIDATOR.validate(record)
            records.append(record)
            completed.add(key)
            seen_run_ids.add(run_id)
            _write_jsonl(record_path, records)

    evaluator_cfg = profile["evaluator"]
    if evaluator_cfg["divergence_action"] == "re_evaluate":
        scores_by_input: dict[str, list[int]] = {}
        for item in inputs:
            anonymous_id = str(item["anonymous_id"])
            scores_by_input[anonymous_id] = [
                int(record["score"])
                for record in records
                if record["anonymous_id"] == anonymous_id
                and int(record["run_index"]) < runs
            ]
        divergent = [
            item
            for item in inputs
            if len(scores_by_input[str(item["anonymous_id"])]) >= 2
            and max(scores_by_input[str(item["anonymous_id"])])
            - min(scores_by_input[str(item["anonymous_id"])])
            >= float(evaluator_cfg["divergence_threshold"])
        ]
        extra_runs = int(evaluator_cfg["re_evaluate_extra_runs"])
        for extra_index in range(extra_runs):
            run_index = runs + extra_index
            order = list(divergent)
            seed = int(evaluator_cfg["random_seed"]) + run_index
            random.Random(seed).shuffle(order)
            orders.append(
                {
                    "run_index": run_index,
                    "seed": seed,
                    "reason": "divergence_re_evaluation",
                    "anonymous_ids": [row["anonymous_id"] for row in order],
                }
            )
            for item in order:
                key = (str(item["anonymous_id"]), run_index)
                if key in completed:
                    continue
                run_id = "eval-" + hashlib.sha256(
                    f"{item['anonymous_id']}:{run_index}".encode("utf-8")
                ).hexdigest()[:20]
                if run_id in seen_run_ids:
                    raise RuntimeError(f"duplicate evaluator_run_id: {run_id}")
                request = dict(item)
                request["evaluator_run_id"] = run_id
                last_error: Exception | None = None
                for retry_index in range(max_retries + 1):
                    try:
                        raw = evaluate_fn(request)
                    except Exception as exc:  # noqa: BLE001
                        last_error = exc
                        continue
                    if (
                        isinstance(raw, dict)
                        and str(raw.get("evaluator_run_id")) in seen_run_ids
                    ):
                        raise RuntimeError(
                            "duplicate evaluator_run_id returned by evaluator: "
                            f"{raw.get('evaluator_run_id')}"
                        )
                    try:
                        validated = _validate_response(
                            raw, payload=item, run_id=run_id
                        )
                        break
                    except (ValueError, TypeError, json.JSONDecodeError) as exc:
                        last_error = exc
                else:
                    raise ProfileError(
                        f"EVALUATION_FAILURE {item['anonymous_id']}: {last_error}"
                    )
                raw_text = _canonical(raw)
                raw_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
                raw_path = output / "assessments" / "raw" / f"{run_id}.json"
                _write_json(raw_path, raw)
                record = {
                    **validated,
                    "anonymous_id": item["anonymous_id"],
                    "candidate_id": item["candidate_id"],
                    "scored_by": "evaluator",
                    "refusal_detected": False,
                    "raw_response_hash": raw_hash,
                    "raw_response_path": str(raw_path),
                    "valid": True,
                    "retry_index": retry_index,
                    "run_index": run_index,
                }
                _ASSESSMENT_VALIDATOR.validate(record)
                records.append(record)
                completed.add(key)
                seen_run_ids.add(run_id)
                _write_jsonl(record_path, records)
    _write_json(output / "assessments" / "round_order.json", orders)
    return records


def final_score(values: list[int], method: str) -> float:
    if method == "median":
        return float(median(values))
    if method == "mean":
        return float(mean(values))
    if method == "trimmed_mean":
        trimmed = sorted(values)[1:-1] if len(values) >= 3 else values
        return float(mean(trimmed))
    raise ProfileError(f"unknown evaluator.final_score: {method}")
