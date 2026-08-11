"""TQA aggregation and status propagation."""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
from statistics import mean
from typing import Any

from pipeline.tqa.evaluation import final_score


_PRIORITY = {"PASS": 0, "CONDITIONAL_PASS": 1, "FAIL": 2, "VETO": 3}


def _status(
    *, score: float, pass_score: float, failed_dimensions: int, hard_count: int,
    conditional: dict[str, int], veto: bool
) -> str:
    if veto:
        return "VETO"
    if (
        score < pass_score
        or failed_dimensions > int(conditional["max_failed_dimensions"])
        or hard_count > int(conditional["max_hard_failures_per_episode"])
    ):
        return "FAIL"
    if failed_dimensions or hard_count:
        return "CONDITIONAL_PASS"
    return "PASS"


def _severity(score: float, thresholds: dict[str, int]) -> str:
    if score <= int(thresholds["critical_upper"]):
        return "CRITICAL"
    if score <= int(thresholds["major_upper"]):
        return "MAJOR"
    if score <= int(thresholds["minor_upper"]):
        return "MINOR"
    return "PASS"


def build_report(
    *,
    profile: dict[str, Any],
    manifest: dict[str, Any],
    blind_map: dict[str, Any],
    records: list[dict[str, object]],
) -> dict[str, object]:
    grouped_records: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in records:
        grouped_records[str(record["anonymous_id"])].append(record)
    sample_info = blind_map["samples"]
    candidate_info = blind_map["candidates"]
    case_lookup = {case["case_id"]: case for case in manifest["cases"]}
    case_scores: dict[str, list[dict[str, object]]] = defaultdict(list)
    for anonymous_id, group in grouped_records.items():
        sample_id = str(group[0]["sample_id"])
        info = sample_info[sample_id]
        case_scores[info["candidate_id"]].append(
            {
                "sample_id": sample_id,
                "cue_id": info["cue_id"],
                "dimension": group[0]["dimension"],
                "score": final_score(
                    [int(item["score"]) for item in group],
                    profile["evaluator"]["final_score"],
                ),
                "hard_failures": sorted(
                    {
                        hard
                        for item in group
                        for hard in item["hard_failures"]
                    }
                ),
                "refusal_detected": any(item["refusal_detected"] for item in group),
            }
        )

    configurations: dict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
    rescue_configurations: dict[
        tuple[object, ...], list[dict[str, object]]
    ] = defaultdict(list)
    technical_failures: list[str] = []
    thresholds = profile["tqa"]["thresholds"]
    conditional = profile["tqa"]["conditional_pass"]
    hard_cfg = profile["tqa"]["hard_failures"]
    enabled_hard_failures = set(hard_cfg["enabled"])
    weights = profile["tqa"]["dimension_weights"]
    severity_thresholds = profile["tqa"]["severity_thresholds"]
    for candidate_id, candidate in candidate_info.items():
        case = case_lookup[candidate["case_id"]]
        if candidate["status"] == "technical_failure":
            technical_failures.append(case["case_id"])
            continue
        items = case_scores.get(candidate_id, [])
        by_dimension: dict[str, list[float]] = defaultdict(list)
        for item in items:
            by_dimension[str(item["dimension"])].append(float(item["score"]))
        dimensions = {
            dimension: round(mean(scores), 4)
            for dimension, scores in by_dimension.items()
        }
        dimensions_severity = {
            dimension: _severity(score, severity_thresholds)
            for dimension, score in dimensions.items()
        }
        active_weight = sum(float(weights[name]) for name in dimensions)
        episode_score = (
            sum(float(weights[name]) * score for name, score in dimensions.items())
            / active_weight
            if active_weight
            else 0.0
        )
        failed = sum(
            score < float(thresholds["dimension_floor"])
            for score in dimensions.values()
        )
        hard_count = len(
            {
                (item["sample_id"], hard_failure)
                for item in items
                for hard_failure in item["hard_failures"]
                if hard_failure in enabled_hard_failures
            }
        )
        veto = bool(thresholds["hard_failure_veto"]) and (
            hard_count > int(hard_cfg["max_hard_failures_per_episode"])
        )
        by_sample: dict[str, list[dict[str, object]]] = defaultdict(list)
        for item in items:
            by_sample[str(item["sample_id"])].append(item)
        sample_scores: list[dict[str, object]] = []
        strategy = profile["tqa"]["sample_aggregation"]
        for sample_id, sample_items in by_sample.items():
            values = [float(item["score"]) for item in sample_items]
            if strategy == "min":
                display_score = min(values)
            elif strategy == "harmonic":
                display_score = (
                    0.0 if any(value == 0 for value in values)
                    else len(values) / sum(1 / value for value in values)
                )
            else:
                sample_weight = sum(
                    float(weights[str(item["dimension"])]) for item in sample_items
                )
                display_score = sum(
                    float(weights[str(item["dimension"])]) * float(item["score"])
                    for item in sample_items
                ) / sample_weight
            sample_scores.append(
                {
                    "sample_id": sample_id,
                    "cue_id": sample_items[0]["cue_id"],
                    "display_score": round(display_score, 4),
                    "severity": _severity(display_score, severity_thresholds),
                    "report_only": True,
                    "dimension_scores": {
                        str(item["dimension"]): item["score"]
                        for item in sample_items
                    },
                }
            )
        sample_scores.sort(key=lambda item: (float(item["display_score"]), item["sample_id"]))
        episode = {
            "episode_id": case["episode_id"],
            "score": round(episode_score, 4),
            "status": _status(
                score=episode_score,
                pass_score=float(thresholds["episode_pass"]),
                failed_dimensions=failed,
                hard_count=hard_count,
                conditional=conditional,
                veto=veto,
            ),
            "dimension_scores": dimensions,
            "dimension_severity": dimensions_severity,
            "sample_scores": sample_scores,
            "sample_count": len({item["sample_id"] for item in items}),
            "hard_failure_count": hard_count,
            "refusal_count": len(
                {
                    item["sample_id"]
                    for item in items
                    if item["refusal_detected"]
                }
            ),
        }
        key = (
            case["model_alias"],
            case["temperature"]["sent"],
            case["temperature"]["value"],
            case["top_p"]["sent"],
            case["top_p"]["value"],
        )
        if candidate.get("lane") == "rescue":
            rescue_configurations[key].append(episode)
        else:
            configurations[key].append(episode)

    models: list[dict[str, object]] = []
    for key, episodes in configurations.items():
        total_samples = sum(int(item["sample_count"]) for item in episodes)
        score = (
            sum(float(item["score"]) * int(item["sample_count"]) for item in episodes)
            / total_samples
            if total_samples
            else 0.0
        )
        propagated = max(
            (str(item["status"]) for item in episodes),
            key=lambda value: _PRIORITY[value],
        )
        status = propagated
        if _PRIORITY[status] < _PRIORITY["FAIL"] and score < float(
            thresholds["model_pass"]
        ):
            status = "FAIL"
        models.append(
            {
                "model_alias": key[0],
                "sampling": {
                    "temperature": {"sent": key[1], "value": key[2]},
                    "top_p": {"sent": key[3], "value": key[4]},
                },
                "score": round(score, 4),
                "status": status,
                "sample_count": total_samples,
                "episodes": episodes,
            }
        )
    models.sort(key=lambda item: (-float(item["score"]), str(item["model_alias"])))
    rescued_quality: list[dict[str, object]] = []
    for key, episodes in rescue_configurations.items():
        total_samples = sum(int(item["sample_count"]) for item in episodes)
        score = (
            sum(float(item["score"]) * int(item["sample_count"]) for item in episodes)
            / total_samples
            if total_samples
            else 0.0
        )
        rescued_quality.append(
            {
                "model_alias": key[0],
                "sampling": {
                    "temperature": {"sent": key[1], "value": key[2]},
                    "top_p": {"sent": key[3], "value": key[4]},
                },
                "rescued_quality_score": round(score, 4),
                "sample_count": total_samples,
                "episodes": episodes,
            }
        )
    rescued_quality.sort(
        key=lambda item: (
            -float(item["rescued_quality_score"]),
            str(item["model_alias"]),
        )
    )
    return {
        "schema_version": 1,
        "aggregation_path": [
            "sample_dimension_raw_score",
            "episode_dimension_mean",
            "episode_weighted_score",
            "model_sample_count_weighted_score",
        ],
        "sample_aggregation": {
            "strategy": profile["tqa"]["sample_aggregation"],
            "report_only": True,
        },
        "status_priority": ["VETO", "FAIL", "CONDITIONAL_PASS", "PASS"],
        "models": models,
        "rescued_quality": rescued_quality,
        "technical_failures": technical_failures,
    }


def write_report(output: Path, report: dict[str, object]) -> None:
    json_path = output / "report.json"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# TQA bench report",
        "",
        "| model | temperature | top_p | score | status | samples |",
        "|---|---:|---:|---:|---|---:|",
    ]
    for item in report["models"]:
        sampling = item["sampling"]
        temperature = (
            sampling["temperature"]["value"]
            if sampling["temperature"]["sent"]
            else "OMIT"
        )
        top_p = sampling["top_p"]["value"] if sampling["top_p"]["sent"] else "OMIT"
        lines.append(
            f"| {item['model_alias']} | {temperature} | {top_p} | "
            f"{item['score']} | {item['status']} | {item['sample_count']} |"
        )
    if report["rescued_quality"]:
        lines.extend(
            [
                "",
                "## Rescued quality (report only)",
                "",
                "| model | temperature | top_p | rescued score | samples |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for item in report["rescued_quality"]:
            sampling = item["sampling"]
            temperature = (
                sampling["temperature"]["value"]
                if sampling["temperature"]["sent"]
                else "OMIT"
            )
            top_p = (
                sampling["top_p"]["value"]
                if sampling["top_p"]["sent"]
                else "OMIT"
            )
            lines.append(
                f"| {item['model_alias']} | {temperature} | {top_p} | "
                f"{item['rescued_quality_score']} | {item['sample_count']} |"
            )
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
