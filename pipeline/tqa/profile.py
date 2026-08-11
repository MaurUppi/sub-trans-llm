"""Load, validate, and resolve the single-file TQA bench profile."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

import model_client
from pipeline.config import DEFAULT_PROMPT


class ProfileError(ValueError):
    """The bench profile violates the published machine contract."""


@dataclass(frozen=True)
class ResolvedProfile:
    source_path: Path
    source_text: str
    data: dict[str, Any]
    output_root: Path


_TOP_LEVEL = {
    "profile_version",
    "framework",
    "project",
    "inputs",
    "translation",
    "sampling",
    "execution",
    "tqa",
    "evaluator",
    "output",
}
_HARD_FAILURES = {
    "SEMANTIC_INVERSION",
    "IDENTITY_CONFUSION",
    "CRITICAL_OMISSION",
    "FABRICATION",
    "OFFENSIVE_MISTRANSLATION",
}


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProfileError(f"{field} must be a mapping")
    return value


def _required(mapping: dict[str, Any], field: str, parent: str) -> Any:
    if field not in mapping:
        raise ProfileError(f"missing required field: {parent}.{field}")
    return mapping[field]


def _resolve_optional_path(base: Path, value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{field} must be a non-empty path or null")
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return str(path.resolve())


def _validate_sampling_value(value: object, field: str) -> None:
    if value == "OMIT":
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileError(f"{field} must be a number or OMIT")
    numeric = float(value)
    if field.endswith("temperature") and not 0 <= numeric < 2:
        raise ProfileError(f"{field} must satisfy 0 <= temperature < 2")
    if field.endswith("top_p") and not 0 < numeric <= 1:
        raise ProfileError(f"{field} must satisfy 0 < top_p <= 1")


def _validate_int(
    mapping: dict[str, Any], field: str, parent: str, *, minimum: int = 1
) -> None:
    value = _required(mapping, field, parent)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ProfileError(f"{parent}.{field} must be an integer >= {minimum}")


def _validate_profile(data: dict[str, Any]) -> None:
    unknown = set(data) - _TOP_LEVEL
    if unknown:
        raise ProfileError(f"unknown top-level fields: {', '.join(sorted(unknown))}")
    missing = _TOP_LEVEL - set(data)
    if missing:
        raise ProfileError(f"missing top-level fields: {', '.join(sorted(missing))}")
    if str(data["profile_version"]) != "1.0":
        raise ProfileError("profile_version must be '1.0'")

    framework = _mapping(data["framework"], "framework")
    if _required(framework, "version", "framework") != "v2":
        raise ProfileError("framework.version must be 'v2'")

    project = _mapping(data["project"], "project")
    for field in ("name", "source_language", "target_language"):
        value = _required(project, field, "project")
        if not isinstance(value, str) or not value.strip():
            raise ProfileError(f"project.{field} must be a non-empty string")

    inputs = _mapping(data["inputs"], "inputs")
    episodes = _required(inputs, "episodes", "inputs")
    if not isinstance(episodes, list) or not episodes:
        raise ProfileError("inputs.episodes must be a non-empty list")
    episode_ids: set[str] = set()
    active_dimensions: set[str] = set()
    for index, item in enumerate(episodes):
        episode = _mapping(item, f"inputs.episodes[{index}]")
        episode_id = _required(episode, "id", f"inputs.episodes[{index}]")
        if not isinstance(episode_id, str) or not episode_id.strip():
            raise ProfileError(f"inputs.episodes[{index}].id must be non-empty")
        if episode_id in episode_ids:
            raise ProfileError(f"duplicate episode id: {episode_id}")
        episode_ids.add(episode_id)
        _required(episode, "source_srt", f"inputs.episodes[{index}]")
        samples = _required(episode, "samples", f"inputs.episodes[{index}]")
        if not isinstance(samples, list) or not samples:
            raise ProfileError(f"inputs.episodes[{index}].samples must be non-empty")
        cue_ids: set[int] = set()
        for sample_index, sample_value in enumerate(samples):
            sample = _mapping(
                sample_value,
                f"inputs.episodes[{index}].samples[{sample_index}]",
            )
            cue_id = _required(
                sample,
                "cue_id",
                f"inputs.episodes[{index}].samples[{sample_index}]",
            )
            if isinstance(cue_id, bool) or not isinstance(cue_id, int) or cue_id < 1:
                raise ProfileError("sample cue_id must be an integer >= 1")
            if cue_id in cue_ids:
                raise ProfileError(f"duplicate cue_id {cue_id} in episode {episode_id}")
            cue_ids.add(cue_id)
            dimensions = _required(sample, "dimensions", "sample")
            if not isinstance(dimensions, list) or not dimensions:
                raise ProfileError("sample dimensions must be a non-empty list")
            if not all(isinstance(item, str) and item for item in dimensions):
                raise ProfileError("sample dimensions must contain non-empty strings")
            active_dimensions.update(dimensions)
            note = _required(sample, "note", "sample")
            if not isinstance(note, str) or not note.strip():
                raise ProfileError("sample note must be a non-empty string")

    translation = _mapping(data["translation"], "translation")
    models = _required(translation, "models", "translation")
    if not isinstance(models, list) or not models or not all(
        isinstance(model, str) and model.strip() for model in models
    ):
        raise ProfileError("translation.models must be a non-empty string list")
    if len(models) != len(set(models)):
        raise ProfileError("translation.models must not contain duplicates")
    translation["api_mode"] = model_client.normalize_api_mode(
        _required(translation, "api_mode", "translation")
    )
    for field in ("prompt", "glossary", "summary"):
        _required(translation, field, "translation")
    summary = _mapping(translation["summary"], "translation.summary")
    if _required(summary, "mode", "translation.summary") not in {
        "generate_once",
        "none",
    }:
        raise ProfileError("translation.summary.mode must be generate_once or none")
    if summary["mode"] == "generate_once":
        _validate_int(summary, "max_output_tokens", "translation.summary")
        timeout = _required(summary, "timeout", "translation.summary")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ProfileError("translation.summary.timeout must be > 0")

    sampling = _mapping(data["sampling"], "sampling")
    arms = _required(sampling, "arms", "sampling")
    if not isinstance(arms, list) or not arms:
        raise ProfileError("sampling.arms must be a non-empty list")
    seen_arms: set[tuple[object, object]] = set()
    for index, arm_value in enumerate(arms):
        arm = _mapping(arm_value, f"sampling.arms[{index}]")
        temperature = _required(arm, "temperature", f"sampling.arms[{index}]")
        top_p = _required(arm, "top_p", f"sampling.arms[{index}]")
        _validate_sampling_value(temperature, f"sampling.arms[{index}].temperature")
        _validate_sampling_value(top_p, f"sampling.arms[{index}].top_p")
        key = (temperature, top_p)
        if key in seen_arms:
            raise ProfileError(f"duplicate sampling arm at index {index}")
        seen_arms.add(key)

    execution = _mapping(data["execution"], "execution")
    for field in (
        "batch_size",
        "batch_jobs",
        "model_jobs",
        "max_output_tokens",
        "max_retries",
    ):
        _validate_int(
            execution,
            field,
            "execution",
            minimum=0 if field == "max_retries" else 1,
        )
    for field in ("timeout", "retry_backoff"):
        value = _required(execution, field, "execution")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ProfileError(f"execution.{field} must be > 0")

    tqa = _mapping(data["tqa"], "tqa")
    reference_mode = _required(tqa, "reference_mode", "tqa")
    if reference_mode not in {"no_reference", "single_reference"}:
        raise ProfileError("tqa.reference_mode must be no_reference or single_reference")
    if reference_mode == "single_reference":
        if tqa.get("reference_role") not in {"anchor", "hint"}:
            raise ProfileError(
                "single_reference requires tqa.reference_role anchor or hint"
            )
        missing_reference = [
            episode["id"] for episode in episodes if not episode.get("reference_srt")
        ]
        if missing_reference:
            raise ProfileError(
                "single_reference requires reference_srt for episodes: "
                + ", ".join(missing_reference)
            )
    instances = _mapping(
        _required(tqa, "dimension_instances", "tqa"),
        "tqa.dimension_instances",
    )
    weights = _mapping(
        _required(tqa, "dimension_weights", "tqa"),
        "tqa.dimension_weights",
    )
    if active_dimensions - set(instances):
        raise ProfileError(
            "missing dimension instances: "
            + ", ".join(sorted(active_dimensions - set(instances)))
        )
    if active_dimensions - set(weights):
        raise ProfileError(
            "missing dimension weights: "
            + ", ".join(sorted(active_dimensions - set(weights)))
        )
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value < 0
        for value in weights.values()
    ):
        raise ProfileError("all dimension weights must be non-negative numbers")
    if abs(sum(float(value) for value in weights.values()) - 1.0) > 1e-9:
        raise ProfileError("tqa.dimension_weights must sum to 1.0")
    if _required(tqa, "sample_aggregation", "tqa") not in {
        "weighted_avg",
        "min",
        "harmonic",
    }:
        raise ProfileError("unsupported tqa.sample_aggregation")
    severity = _mapping(
        _required(tqa, "severity_thresholds", "tqa"),
        "tqa.severity_thresholds",
    )
    bounds = [
        _required(severity, field, "tqa.severity_thresholds")
        for field in ("critical_upper", "major_upper", "minor_upper")
    ]
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in bounds):
        raise ProfileError("severity thresholds must be integers")
    if not (0 <= bounds[0] < bounds[1] < bounds[2] < 10):
        raise ProfileError("severity thresholds must be ordered within 0..9")
    hard = _mapping(_required(tqa, "hard_failures", "tqa"), "tqa.hard_failures")
    enabled = _required(hard, "enabled", "tqa.hard_failures")
    if not isinstance(enabled, list) or set(enabled) - _HARD_FAILURES:
        raise ProfileError("tqa.hard_failures.enabled contains an unknown category")
    _validate_int(
        hard,
        "max_hard_failures_per_episode",
        "tqa.hard_failures",
        minimum=0,
    )
    for field in ("thresholds", "conditional_pass", "refusal_handling"):
        _mapping(_required(tqa, field, "tqa"), f"tqa.{field}")
    thresholds = tqa["thresholds"]
    for field in ("dimension_floor", "episode_pass", "model_pass"):
        value = _required(thresholds, field, "tqa.thresholds")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 <= float(value) <= 10
        ):
            raise ProfileError(f"tqa.thresholds.{field} must be between 0 and 10")
    if not isinstance(
        _required(thresholds, "hard_failure_veto", "tqa.thresholds"), bool
    ):
        raise ProfileError("tqa.thresholds.hard_failure_veto must be boolean")
    conditional = tqa["conditional_pass"]
    for field in ("max_failed_dimensions", "max_hard_failures_per_episode"):
        value = _required(conditional, field, "tqa.conditional_pass")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ProfileError(f"tqa.conditional_pass.{field} must be >= 0")
    refusal = tqa["refusal_handling"]
    if _required(refusal, "mode", "tqa.refusal_handling") != "count_and_mark":
        raise ProfileError("refusal mode must be count_and_mark")
    refusal_type = _required(refusal, "hard_failure_type", "tqa.refusal_handling")
    if refusal_type not in _HARD_FAILURES:
        raise ProfileError("unknown refusal hard_failure_type")
    refusal_score = _required(refusal, "default_score", "tqa.refusal_handling")
    if refusal_score != 0 or isinstance(refusal_score, bool):
        raise ProfileError("tqa.refusal_handling.default_score must be 0")
    if not isinstance(
        _required(
            refusal,
            "mark_as_hard_failure",
            "tqa.refusal_handling",
        ),
        bool,
    ):
        raise ProfileError(
            "tqa.refusal_handling.mark_as_hard_failure must be boolean"
        )

    evaluator = _mapping(data["evaluator"], "evaluator")
    for field in ("model", "final_score", "divergence_action"):
        value = _required(evaluator, field, "evaluator")
        if not isinstance(value, str) or not value:
            raise ProfileError(f"evaluator.{field} must be a non-empty string")
    if evaluator["final_score"] not in {"median", "mean", "trimmed_mean"}:
        raise ProfileError("unsupported evaluator.final_score")
    if evaluator["divergence_action"] not in {"re_evaluate", "flag_only"}:
        raise ProfileError("unsupported evaluator.divergence_action")
    for field in ("runs", "context_window", "max_retries"):
        value = _required(evaluator, field, "evaluator")
        minimum = 0 if field in {"context_window", "max_retries"} else 1
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ProfileError(f"evaluator.{field} must be >= {minimum}")
    extra = _required(evaluator, "re_evaluate_extra_runs", "evaluator")
    if isinstance(extra, bool) or not isinstance(extra, int) or extra < 0:
        raise ProfileError("evaluator.re_evaluate_extra_runs must be >= 0")
    _validate_sampling_value(
        _required(evaluator, "temperature", "evaluator"),
        "evaluator.temperature",
    )
    if "top_p" in evaluator:
        _validate_sampling_value(evaluator["top_p"], "evaluator.top_p")
    divergence = _required(evaluator, "divergence_threshold", "evaluator")
    if (
        isinstance(divergence, bool)
        or not isinstance(divergence, (int, float))
        or not 0 <= float(divergence) <= 10
    ):
        raise ProfileError("evaluator.divergence_threshold must be between 0 and 10")
    seed = _required(evaluator, "random_seed", "evaluator")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ProfileError("evaluator.random_seed must be an integer")

    output = _mapping(data["output"], "output")
    _required(output, "root", "output")


def load_profile(path: Path) -> ResolvedProfile:
    source_path = Path(path).expanduser().resolve()
    if not source_path.is_file():
        raise ProfileError(f"profile does not exist: {source_path}")
    source_text = source_path.read_text(encoding="utf-8")
    try:
        loaded = yaml.safe_load(source_text)
    except yaml.YAMLError as exc:
        raise ProfileError(f"invalid YAML: {exc}") from exc
    data = _mapping(loaded, "profile")
    schema_path = Path(__file__).resolve().parent / "profile_v2.schema.yaml"
    schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    schema_errors = sorted(
        Draft202012Validator(schema).iter_errors(data),
        key=lambda error: tuple(str(item) for item in error.absolute_path),
    )
    if schema_errors:
        error = schema_errors[0]
        location = ".".join(str(item) for item in error.absolute_path) or "profile"
        raise ProfileError(f"schema validation failed at {location}: {error.message}")
    _validate_profile(data)

    resolved = deepcopy(data)
    base = source_path.parent
    for index, episode in enumerate(resolved["inputs"]["episodes"]):
        episode["source_srt"] = _resolve_optional_path(
            base, episode["source_srt"], f"inputs.episodes[{index}].source_srt"
        )
        source_srt = Path(episode["source_srt"])
        if not source_srt.is_file():
            raise ProfileError(f"source SRT does not exist: {source_srt}")
        if "reference_srt" in episode:
            episode["reference_srt"] = _resolve_optional_path(
                base,
                episode["reference_srt"],
                f"inputs.episodes[{index}].reference_srt",
            )
            if episode["reference_srt"] and not Path(episode["reference_srt"]).is_file():
                raise ProfileError(
                    f"reference SRT does not exist: {episode['reference_srt']}"
                )
    translation = resolved["translation"]
    translation["prompt"] = _resolve_optional_path(
        base, translation["prompt"], "translation.prompt"
    )
    if translation["prompt"] is None:
        translation["prompt"] = str(DEFAULT_PROMPT.resolve())
    translation["glossary"] = _resolve_optional_path(
        base, translation["glossary"], "translation.glossary"
    )
    for field in ("prompt", "glossary"):
        value = translation[field]
        if value is not None and not Path(value).is_file():
            raise ProfileError(f"{field} does not exist: {value}")
    output_root = Path(str(resolved["output"]["root"]))
    if not output_root.is_absolute():
        output_root = base / output_root
    output_root = output_root.resolve()
    resolved["output"]["root"] = str(output_root)
    return ResolvedProfile(source_path, source_text, resolved, output_root)
