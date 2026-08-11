from __future__ import annotations

import json
import hashlib
from pathlib import Path
import threading
from types import SimpleNamespace

import main
import pytest
import yaml
from pipeline.tqa import runner
from pipeline.tqa import evaluation as tqa_evaluation


def _write_minimal_profile(tmp_path: Path) -> Path:
    source = tmp_path / "episode.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello.\n\n",
        encoding="utf-8",
    )
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        """\
profile_version: "1.0"
framework:
  version: "v2"
project:
  name: "Smoke"
  source_language: "English"
  target_language: "Simplified Chinese"
inputs:
  episodes:
    - id: "E01"
      source_srt: "episode.srt"
      samples:
        - cue_id: 1
          dimensions: ["上下文依赖"]
          note: "Greeting in context"
translation:
  models: ["candidate-model"]
  api_mode: "ChatCompletion"
  prompt: null
  glossary: null
  summary:
    mode: generate_once
    max_output_tokens: 4096
    timeout: 180
sampling:
  arms:
    - temperature: OMIT
      top_p: OMIT
execution:
  batch_size: 50
  batch_jobs: 1
  model_jobs: 1
  max_output_tokens: 8192
  timeout: 300
  max_retries: 2
  retry_backoff: 3
tqa:
  reference_mode: no_reference
  dimension_instances:
    上下文依赖:
      description: "Resolve meaning using neighboring subtitles."
  dimension_weights:
    上下文依赖: 1.0
  sample_aggregation: weighted_avg
  severity_thresholds:
    critical_upper: 3
    major_upper: 5
    minor_upper: 7
  hard_failures:
    enabled: [CRITICAL_OMISSION]
    max_hard_failures_per_episode: 1
  thresholds:
    dimension_floor: 4.0
    episode_pass: 6.5
    model_pass: 6.5
    hard_failure_veto: true
  conditional_pass:
    max_failed_dimensions: 1
    max_hard_failures_per_episode: 1
  refusal_handling:
    mode: count_and_mark
    default_score: 0
    mark_as_hard_failure: true
    hard_failure_type: CRITICAL_OMISSION
evaluator:
  model: "evaluator-model"
  runs: 1
  temperature: 0.3
  context_window: 1
  divergence_threshold: 3
  divergence_action: flag_only
  re_evaluate_extra_runs: 0
  final_score: median
  random_seed: 20260811
  max_retries: 2
output:
  root: "bench-out"
""",
        encoding="utf-8",
    )
    return profile


def test_bench_plan_validates_and_freezes_one_profile(tmp_path: Path) -> None:
    profile = _write_minimal_profile(tmp_path)

    assert main.main(["bench", "plan", "--profile", str(profile)]) == 0

    output = tmp_path / "bench-out"
    assert (output / "profile.source.yaml").read_text(encoding="utf-8") == (
        profile.read_text(encoding="utf-8")
    )
    resolved = (output / "profile.resolved.yaml").read_text(encoding="utf-8")
    assert str((tmp_path / "episode.srt").resolve()) in resolved
    lock = json.loads((output / "profile.lock.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    progress = json.loads((output / "progress.json").read_text(encoding="utf-8"))
    assert lock["profile_sha256"]
    assert lock["framework"]["document_sha256"]
    assert lock["framework"]["evaluator_response_schema_sha256"]
    assert lock["framework"]["assessment_record_schema_sha256"]
    assert manifest["case_count"] == 1
    assert manifest["translation"]["api_mode"] == "chat_completions"
    assert manifest["translation"]["summary"]["mode"] == "generate_once"
    assert manifest["execution"]["batch_size"] == 50
    assert manifest["evaluator"]["random_seed"] == 20260811
    assert manifest["cases"][0]["temperature"]["sent"] is False
    assert progress["state"] == "planned"


def test_bench_all_offline_smoke_runs_end_to_end_and_resumes(tmp_path: Path) -> None:
    profile = _write_minimal_profile(tmp_path)
    translated: list[dict[str, object]] = []
    evaluated: list[dict[str, object]] = []

    def fake_translate(**request: object) -> dict[str, object]:
        translated.append(request)
        return {
            "status": "completed",
            "translations": {"1": "你好。"},
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }

    def fake_evaluate(payload: dict[str, object]) -> dict[str, object]:
        evaluated.append(payload)
        return {
            "sample_id": payload["sample_id"],
            "dimension": payload["dimension"],
            "score": 8,
            "hard_failures": [],
            "rationale": "Meaning and register are preserved.",
            "confidence": 0.9,
            "evaluator_run_id": payload["evaluator_run_id"],
        }

    assert (
        runner.run_pipeline(
            profile_path=profile,
            action="all",
            translate_fn=fake_translate,
            evaluate_fn=fake_evaluate,
        )
        == 0
    )

    output = tmp_path / "bench-out"
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    progress = json.loads((output / "progress.json").read_text(encoding="utf-8"))
    blind_map = json.loads((output / "blind_map.json").read_text(encoding="utf-8"))
    anonymous = [
        json.loads(line)
        for line in (output / "anonymized" / "eval_input.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(translated) == 1
    assert translated[0]["temperature"] is None
    assert translated[0]["top_p"] is None
    assert len(evaluated) == 1
    assert set(evaluated[0]).isdisjoint(
        {"model_alias", "provider", "parameter_arm", "source_path", "rescue_status"}
    )
    assert "candidate-model" not in json.dumps(anonymous, ensure_ascii=False)
    assert blind_map["candidates"]
    assert report["models"][0]["score"] == 8.0
    assert report["models"][0]["status"] == "PASS"
    assert progress["state"] == "awaiting_user_decision"
    assert progress["counts"] == {
        "cases_total": 1,
        "cases_completed": 1,
        "provider_refusals": 0,
        "technical_failures": 0,
        "assessment_inputs": 1,
        "assessment_records": 1,
        "hard_failures": 0,
    }

    # A second --all is an idempotent resume: completed calls are not repeated.
    assert (
        runner.run_pipeline(
            profile_path=profile,
            action="all",
            translate_fn=fake_translate,
            evaluate_fn=fake_evaluate,
        )
        == 0
    )
    assert len(translated) == 1
    assert len(evaluated) == 1


def test_bench_refusal_is_system_scored_and_vetoes_when_max_is_zero(
    tmp_path: Path,
) -> None:
    profile = _write_minimal_profile(tmp_path)
    profile.write_text(
        profile.read_text(encoding="utf-8").replace(
            "max_hard_failures_per_episode: 1",
            "max_hard_failures_per_episode: 0",
        ),
        encoding="utf-8",
    )

    def refusal(**_request: object) -> dict[str, object]:
        return {"status": "provider_refusal", "translations": {}}

    def must_not_evaluate(_payload: dict[str, object]) -> dict[str, object]:
        raise AssertionError("provider refusal must not be sent to evaluator")

    assert (
        runner.run_pipeline(
            profile_path=profile,
            action="all",
            translate_fn=refusal,
            evaluate_fn=must_not_evaluate,
        )
        == 0
    )
    output = tmp_path / "bench-out"
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    records = [
        json.loads(line)
        for line in (output / "assessments" / "records.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert records[0]["scored_by"] == "system"
    assert records[0]["score"] == 0
    assert records[0]["refusal_detected"] is True
    assert report["models"][0]["status"] == "VETO"


def test_bench_divergence_re_evaluation_is_included_in_final_median(
    tmp_path: Path,
) -> None:
    profile = _write_minimal_profile(tmp_path)
    text = profile.read_text(encoding="utf-8")
    text = text.replace("runs: 1", "runs: 2")
    text = text.replace("divergence_action: flag_only", "divergence_action: re_evaluate")
    text = text.replace("re_evaluate_extra_runs: 0", "re_evaluate_extra_runs: 1")
    profile.write_text(text, encoding="utf-8")
    scores = iter((2, 8, 9))
    calls = 0

    def fake_translate(**_request: object) -> dict[str, object]:
        return {"status": "completed", "translations": {"1": "你好。"}}

    def divergent(payload: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {
            "sample_id": payload["sample_id"],
            "dimension": payload["dimension"],
            "score": next(scores),
            "hard_failures": [],
            "rationale": "Independent scoring round.",
            "confidence": 0.8,
            "evaluator_run_id": payload["evaluator_run_id"],
        }

    assert (
        runner.run_pipeline(
            profile_path=profile,
            action="all",
            translate_fn=fake_translate,
            evaluate_fn=divergent,
        )
        == 0
    )
    report = json.loads(
        (tmp_path / "bench-out" / "report.json").read_text(encoding="utf-8")
    )
    assert calls == 3
    assert report["models"][0]["score"] == 8.0


def test_duplicate_evaluator_run_id_aborts_without_retry(tmp_path: Path) -> None:
    profile = _write_minimal_profile(tmp_path)
    profile.write_text(
        profile.read_text(encoding="utf-8").replace("runs: 1", "runs: 2"),
        encoding="utf-8",
    )
    first_run_id: str | None = None
    calls = 0

    def fake_translate(**_request: object) -> dict[str, object]:
        return {"status": "completed", "translations": {"1": "你好。"}}

    def duplicate(payload: dict[str, object]) -> dict[str, object]:
        nonlocal first_run_id, calls
        calls += 1
        if first_run_id is None:
            first_run_id = str(payload["evaluator_run_id"])
        return {
            "sample_id": payload["sample_id"],
            "dimension": payload["dimension"],
            "score": 8,
            "hard_failures": [],
            "rationale": "Scored.",
            "confidence": 0.9,
            "evaluator_run_id": first_run_id,
        }

    assert (
        runner.run_pipeline(
            profile_path=profile,
            action="all",
            translate_fn=fake_translate,
            evaluate_fn=duplicate,
        )
        == 2
    )
    assert calls == 2


def test_framework_metadata_hashes_are_not_self_referential_placeholders() -> None:
    root = Path(__file__).resolve().parents[1] / "pipeline" / "tqa"
    metadata = yaml.safe_load(
        (root / "framework_v2.meta.yaml").read_text(encoding="utf-8")
    )

    assert metadata["document_sha256"] == hashlib.sha256(
        (root / metadata["document"]).read_bytes()
    ).hexdigest()
    assert metadata["machine_schema_sha256"] == hashlib.sha256(
        (root / metadata["machine_schema"]).read_bytes()
    ).hexdigest()


def test_multi_dimension_sample_counts_once_at_episode_and_model_level(
    tmp_path: Path,
) -> None:
    profile = _write_minimal_profile(tmp_path)
    text = profile.read_text(encoding="utf-8")
    text = text.replace(
        'dimensions: ["上下文依赖"]',
        'dimensions: ["上下文依赖", "习语/口语"]',
    )
    text = text.replace(
        '    上下文依赖:\n      description: "Resolve meaning using neighboring subtitles."',
        '    上下文依赖:\n      description: "Resolve meaning using neighboring subtitles."\n'
        '    习语/口语:\n      description: "Natural spoken language."',
    )
    text = text.replace(
        "    上下文依赖: 1.0",
        "    上下文依赖: 0.5\n    习语/口语: 0.5",
    )
    profile.write_text(text, encoding="utf-8")

    def fake_translate(**_request: object) -> dict[str, object]:
        return {"status": "completed", "translations": {"1": "你好。"}}

    def fake_evaluate(payload: dict[str, object]) -> dict[str, object]:
        return {
            "sample_id": payload["sample_id"],
            "dimension": payload["dimension"],
            "score": 8,
            "hard_failures": ["CRITICAL_OMISSION"],
            "rationale": "Acceptable.",
            "confidence": 0.9,
            "evaluator_run_id": payload["evaluator_run_id"],
        }

    assert runner.run_pipeline(
        profile_path=profile,
        action="all",
        translate_fn=fake_translate,
        evaluate_fn=fake_evaluate,
    ) == 0
    report = json.loads(
        (tmp_path / "bench-out" / "report.json").read_text(encoding="utf-8")
    )
    assert report["models"][0]["sample_count"] == 1
    assert report["models"][0]["episodes"][0]["sample_count"] == 1
    assert report["models"][0]["episodes"][0]["hard_failure_count"] == 1


def test_default_profile_freezes_the_ten_explicit_sampling_arms() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "pipeline"
        / "tqa"
        / "profile.default.yaml"
    )
    text = path.read_text(encoding="utf-8")
    profile = yaml.safe_load(text)

    assert len(profile["sampling"]["arms"]) == 10
    assert profile["sampling"]["arms"][0] == {
        "temperature": "OMIT",
        "top_p": "OMIT",
    }
    assert profile["execution"] == {
        "batch_size": 50,
        "batch_jobs": 1,
        "model_jobs": 1,
        "max_output_tokens": 8192,
        "timeout": 300,
        "max_retries": 2,
        "retry_backoff": 3,
    }
    assert text.index("一、必须根据实际情况修订的项目") < text.index("sampling:")
    assert "collect 始终翻译 source_srt 的整份字幕" in text
    assert "samples 只指定整份候选译文中哪些 cue 进入 evaluator" in text
    assert "evaluator 是“评分模型/裁判”，不负责生成候选字幕" in text
    assert "与 evaluator.temperature 完全独立" in text
    assert '实际路径建议使用双引号，例如 "./path/to/file.md"' in text
    assert 'null 表示不提供自定义文件，不能写成 "null"' in text
    assert "普通用户建议保持 no_reference" in text
    assert "anchor：参考译文作为可信标准答案" in text
    assert "hint：参考译文仅辅助理解" in text
    assert text.index("tqa:") > text.index("execution:")
    tqa_section = text[text.index("tqa:") :]
    assert "【必须修改】" not in tqa_section


def test_collect_model_jobs_parallelizes_models_but_keeps_case_artifacts_isolated(
    tmp_path: Path,
) -> None:
    profile = _write_minimal_profile(tmp_path)
    text = profile.read_text(encoding="utf-8")
    text = text.replace(
        'models: ["candidate-model"]',
        'models: ["candidate-a", "candidate-b"]',
    ).replace("model_jobs: 1", "model_jobs: 2")
    profile.write_text(text, encoding="utf-8")
    rendezvous = threading.Barrier(2)

    def concurrent_translate(**_request: object) -> dict[str, object]:
        rendezvous.wait(timeout=2)
        return {"status": "completed", "translations": {"1": "你好。"}}

    assert runner.run_pipeline(
        profile_path=profile,
        action="collect",
        translate_fn=concurrent_translate,
    ) == 0
    candidates = sorted((tmp_path / "bench-out" / "cases").glob("*/candidate.json"))
    assert len(candidates) == 2
    assert all(
        json.loads(path.read_text(encoding="utf-8"))["status"] == "completed"
        for path in candidates
    )


def test_collect_freezes_one_summary_per_model_and_episode_across_arms(
    tmp_path: Path,
) -> None:
    profile = _write_minimal_profile(tmp_path)
    text = profile.read_text(encoding="utf-8").replace(
        "    - temperature: OMIT\n      top_p: OMIT",
        "    - temperature: OMIT\n      top_p: OMIT\n"
        "    - temperature: 0.3\n      top_p: OMIT",
    )
    profile.write_text(text, encoding="utf-8")
    overrides: list[object] = []

    def fake_translate(**request: object) -> dict[str, object]:
        overrides.append(request["episode_summary_override"])
        return {
            "status": "completed",
            "translations": {"1": "你好。"},
            "episode_summary": "Frozen story context.",
        }

    assert runner.run_pipeline(
        profile_path=profile,
        action="collect",
        translate_fn=fake_translate,
    ) == 0
    assert overrides == [None, "Frozen story context."]


def test_bench_status_is_read_only_and_requires_an_existing_plan(
    tmp_path: Path,
) -> None:
    profile = _write_minimal_profile(tmp_path)

    assert main.main(["bench", "status", "--profile", str(profile)]) == 2
    assert not (tmp_path / "bench-out").exists()


def test_individual_bench_stages_enforce_checkpoint_order(tmp_path: Path) -> None:
    profile = _write_minimal_profile(tmp_path)

    assert main.main(["bench", "plan", "--profile", str(profile)]) == 0
    assert main.main(["bench", "evaluate", "--profile", str(profile)]) == 2
    assert main.main(["bench", "report", "--profile", str(profile)]) == 2
    progress = json.loads(
        (tmp_path / "bench-out" / "progress.json").read_text(encoding="utf-8")
    )
    assert progress["stages"]["evaluate"] == "pending"
    assert progress["stages"]["report"] == "pending"


def test_evaluator_transport_failure_retries_within_profile_budget(
    tmp_path: Path,
) -> None:
    profile = _write_minimal_profile(tmp_path)
    calls = 0

    def fake_translate(**_request: object) -> dict[str, object]:
        return {"status": "completed", "translations": {"1": "你好。"}}

    def flaky(payload: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("temporary evaluator outage")
        return {
            "sample_id": payload["sample_id"],
            "dimension": payload["dimension"],
            "score": 8,
            "hard_failures": [],
            "rationale": "Recovered evaluator call.",
            "confidence": 0.9,
            "evaluator_run_id": payload["evaluator_run_id"],
        }

    assert runner.run_pipeline(
        profile_path=profile,
        action="all",
        translate_fn=fake_translate,
        evaluate_fn=flaky,
    ) == 0
    records = [
        json.loads(line)
        for line in (tmp_path / "bench-out" / "assessments" / "records.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert calls == 2
    assert records[0]["retry_index"] == 1


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("temperature: OMIT", "temperature: 2.0"),
        ("top_p: OMIT", "top_p: 0"),
    ],
)
def test_profile_rejects_sampling_values_outside_provider_contract(
    tmp_path: Path, old: str, new: str
) -> None:
    profile = _write_minimal_profile(tmp_path)
    profile.write_text(
        profile.read_text(encoding="utf-8").replace(old, new, 1),
        encoding="utf-8",
    )

    assert main.main(["bench", "plan", "--profile", str(profile)]) == 2
    assert not (tmp_path / "bench-out" / "profile.lock.json").exists()


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("final_score: median", "final_score: unsupported"),
        ("dimension_floor: 4.0", "dimension_floor: 11"),
        ("divergence_action: flag_only", "divergence_action: drop"),
    ],
)
def test_profile_rejects_invalid_scoring_contract_before_freeze(
    tmp_path: Path, old: str, new: str
) -> None:
    profile = _write_minimal_profile(tmp_path)
    profile.write_text(
        profile.read_text(encoding="utf-8").replace(old, new, 1),
        encoding="utf-8",
    )

    assert main.main(["bench", "plan", "--profile", str(profile)]) == 2


def test_single_reference_mode_requires_role_and_reference_for_every_episode(
    tmp_path: Path,
) -> None:
    profile = _write_minimal_profile(tmp_path)
    profile.write_text(
        profile.read_text(encoding="utf-8").replace(
            "reference_mode: no_reference",
            "reference_mode: single_reference",
        ),
        encoding="utf-8",
    )

    assert main.main(["bench", "plan", "--profile", str(profile)]) == 2


def test_single_reference_requires_each_sample_cue_in_reference_srt(
    tmp_path: Path,
) -> None:
    profile = _write_minimal_profile(tmp_path)
    (tmp_path / "reference.srt").write_text(
        "2\n00:00:00,000 --> 00:00:01,000\n错误序号。\n\n",
        encoding="utf-8",
    )
    data = yaml.safe_load(profile.read_text(encoding="utf-8"))
    data["inputs"]["episodes"][0]["reference_srt"] = "reference.srt"
    data["tqa"]["reference_mode"] = "single_reference"
    data["tqa"]["reference_role"] = "anchor"
    profile.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    assert main.main(["bench", "plan", "--profile", str(profile)]) == 2
    assert not (tmp_path / "bench-out" / "profile.lock.json").exists()


def test_no_reference_mode_never_sends_a_configured_reference(
    tmp_path: Path,
) -> None:
    profile = _write_minimal_profile(tmp_path)
    (tmp_path / "reference.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n不应发送。\n\n",
        encoding="utf-8",
    )
    data = yaml.safe_load(profile.read_text(encoding="utf-8"))
    data["inputs"]["episodes"][0]["reference_srt"] = "reference.srt"
    profile.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    evaluated: list[dict[str, object]] = []

    def fake_translate(**_request: object) -> dict[str, object]:
        return {"status": "completed", "translations": {"1": "候选。"}}

    def fake_evaluate(payload: dict[str, object]) -> dict[str, object]:
        evaluated.append(payload)
        return {
            "sample_id": payload["sample_id"],
            "dimension": payload["dimension"],
            "score": 8,
            "hard_failures": [],
            "rationale": "No reference used.",
            "confidence": 0.9,
            "evaluator_run_id": payload["evaluator_run_id"],
        }

    assert runner.run_pipeline(
        profile_path=profile,
        action="all",
        translate_fn=fake_translate,
        evaluate_fn=fake_evaluate,
    ) == 0
    assert len(evaluated) == 1
    assert evaluated[0]["reference_text"] is None
    assert evaluated[0]["reference_role"] is None
    assert evaluated[0]["reference_instruction"] is None


@pytest.mark.parametrize(
    ("role", "instruction_fragment"),
    [
        ("anchor", "authoritative scoring anchor"),
        ("hint", "only as a comprehension aid"),
    ],
)
def test_single_reference_role_and_per_episode_reference_reach_evaluator(
    tmp_path: Path,
    role: str,
    instruction_fragment: str,
) -> None:
    profile = _write_minimal_profile(tmp_path)
    (tmp_path / "episode-2.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello again.\n\n",
        encoding="utf-8",
    )
    (tmp_path / "reference-1.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n参考一。\n\n",
        encoding="utf-8",
    )
    (tmp_path / "reference-2.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n参考二。\n\n",
        encoding="utf-8",
    )
    data = yaml.safe_load(profile.read_text(encoding="utf-8"))
    first_episode = data["inputs"]["episodes"][0]
    first_episode["reference_srt"] = "reference-1.srt"
    second_episode = dict(first_episode)
    second_episode["id"] = "E02"
    second_episode["source_srt"] = "episode-2.srt"
    second_episode["reference_srt"] = "reference-2.srt"
    data["inputs"]["episodes"].append(second_episode)
    data["tqa"]["reference_mode"] = "single_reference"
    data["tqa"]["reference_role"] = role
    profile.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    evaluated: list[dict[str, object]] = []

    def fake_translate(**_request: object) -> dict[str, object]:
        return {"status": "completed", "translations": {"1": "候选。"}}

    def fake_evaluate(payload: dict[str, object]) -> dict[str, object]:
        evaluated.append(payload)
        return {
            "sample_id": payload["sample_id"],
            "dimension": payload["dimension"],
            "score": 8,
            "hard_failures": [],
            "rationale": "Reference contract applied.",
            "confidence": 0.9,
            "evaluator_run_id": payload["evaluator_run_id"],
        }

    assert runner.run_pipeline(
        profile_path=profile,
        action="all",
        translate_fn=fake_translate,
        evaluate_fn=fake_evaluate,
    ) == 0
    assert len(evaluated) == 2
    assert {item["reference_text"] for item in evaluated} == {"参考一。", "参考二。"}
    assert {item["reference_role"] for item in evaluated} == {role}
    assert all(
        instruction_fragment in str(item["reference_instruction"])
        for item in evaluated
    )


def test_default_evaluator_is_told_to_follow_reference_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_call(_model: str, _input_text: str, **kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(
            text=(
                '{"sample_id":"sample-1","dimension":"上下文依赖",'
                '"score":8,"hard_failures":[],"rationale":"ok",'
                '"confidence":0.9,"evaluator_run_id":"eval-1"}'
            )
        )

    monkeypatch.setattr(tqa_evaluation.model_client, "call", fake_call)
    evaluate = tqa_evaluation.default_evaluator(
        {
            "translation": {"api_mode": "chat_completions"},
            "evaluator": {
                "model": "judge",
                "temperature": 0.3,
                "max_output_tokens": 2048,
                "timeout": 300,
            },
        }
    )
    evaluate(
        {
            "sample_id": "sample-1",
            "dimension": "上下文依赖",
            "evaluator_run_id": "eval-1",
            "reference_instruction": "Use only as a hint.",
        }
    )

    assert "Follow reference_instruction" in str(captured["instructions"])


def test_public_tqa_guide_matches_current_reference_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    guide = (root / "TQA.md").read_text(encoding="utf-8")

    assert "[TQA.md](TQA.md)" in readme
    assert "一集对应一个 `reference_srt`" in guide
    assert "`single_reference` 时必填" in guide
    assert "当前不支持 `multi_reference`" in guide
    assert "`anchor`" in guide and "`hint`" in guide


def test_collect_rejects_source_drift_after_manifest_freeze(tmp_path: Path) -> None:
    profile = _write_minimal_profile(tmp_path)
    assert main.main(["bench", "plan", "--profile", str(profile)]) == 0
    (tmp_path / "episode.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nChanged.\n\n",
        encoding="utf-8",
    )
    called = False

    def must_not_translate(**_request: object) -> dict[str, object]:
        nonlocal called
        called = True
        return {"status": "completed", "translations": {"1": "变化。"}}

    assert runner.run_pipeline(
        profile_path=profile,
        action="collect",
        translate_fn=must_not_translate,
    ) == 2
    assert called is False


def test_plan_rejects_framework_drift_in_an_existing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _write_minimal_profile(tmp_path)
    assert main.main(["bench", "plan", "--profile", str(profile)]) == 0
    changed = dict(runner._framework_lock())
    changed["document_sha256"] = "0" * 64
    monkeypatch.setattr(runner, "_framework_lock", lambda: changed)

    assert main.main(["bench", "plan", "--profile", str(profile)]) == 2


def test_evaluator_extra_fields_are_invalid_and_retried(tmp_path: Path) -> None:
    profile = _write_minimal_profile(tmp_path)
    calls = 0

    def fake_translate(**_request: object) -> dict[str, object]:
        return {"status": "completed", "translations": {"1": "你好。"}}

    def evaluator(payload: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        response: dict[str, object] = {
            "sample_id": payload["sample_id"],
            "dimension": payload["dimension"],
            "score": 8,
            "hard_failures": [],
            "rationale": "Valid fields.",
            "confidence": 0.9,
            "evaluator_run_id": payload["evaluator_run_id"],
        }
        if calls == 1:
            response["model_alias"] = "leaked-extra-field"
        return response

    assert runner.run_pipeline(
        profile_path=profile,
        action="all",
        translate_fn=fake_translate,
        evaluate_fn=evaluator,
    ) == 0
    assert calls == 2


def test_disabled_hard_failure_category_is_reported_but_does_not_trigger_gate(
    tmp_path: Path,
) -> None:
    profile = _write_minimal_profile(tmp_path)

    def fake_translate(**_request: object) -> dict[str, object]:
        return {"status": "completed", "translations": {"1": "你好。"}}

    def evaluator(payload: dict[str, object]) -> dict[str, object]:
        return {
            "sample_id": payload["sample_id"],
            "dimension": payload["dimension"],
            "score": 8,
            "hard_failures": ["OFFENSIVE_MISTRANSLATION"],
            "rationale": "Category is not enabled by this project profile.",
            "confidence": 0.9,
            "evaluator_run_id": payload["evaluator_run_id"],
        }

    assert runner.run_pipeline(
        profile_path=profile,
        action="all",
        translate_fn=fake_translate,
        evaluate_fn=evaluator,
    ) == 0
    report = json.loads(
        (tmp_path / "bench-out" / "report.json").read_text(encoding="utf-8")
    )
    episode = report["models"][0]["episodes"][0]
    assert episode["hard_failure_count"] == 0
    assert episode["status"] == "PASS"


def test_plan_rejects_sample_cue_missing_from_source_srt(tmp_path: Path) -> None:
    profile = _write_minimal_profile(tmp_path)
    profile.write_text(
        profile.read_text(encoding="utf-8").replace("cue_id: 1", "cue_id: 99"),
        encoding="utf-8",
    )

    assert main.main(["bench", "plan", "--profile", str(profile)]) == 2


def test_refusal_primary_score_is_frozen_to_zero(tmp_path: Path) -> None:
    profile = _write_minimal_profile(tmp_path)
    profile.write_text(
        profile.read_text(encoding="utf-8").replace(
            "default_score: 0",
            "default_score: 1",
        ),
        encoding="utf-8",
    )

    assert main.main(["bench", "plan", "--profile", str(profile)]) == 2


def test_evaluator_duplicate_hard_failures_are_invalid_and_retried(
    tmp_path: Path,
) -> None:
    profile = _write_minimal_profile(tmp_path)
    calls = 0

    def fake_translate(**_request: object) -> dict[str, object]:
        return {"status": "completed", "translations": {"1": "你好。"}}

    def evaluator(payload: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        hard_failures = (
            ["CRITICAL_OMISSION", "CRITICAL_OMISSION"] if calls == 1 else []
        )
        return {
            "sample_id": payload["sample_id"],
            "dimension": payload["dimension"],
            "score": 8,
            "hard_failures": hard_failures,
            "rationale": "Schema uniqueness check.",
            "confidence": 0.9,
            "evaluator_run_id": payload["evaluator_run_id"],
        }

    assert runner.run_pipeline(
        profile_path=profile,
        action="all",
        translate_fn=fake_translate,
        evaluate_fn=evaluator,
    ) == 0
    assert calls == 2


def test_translation_empty_provider_output_is_classified_as_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from model_client import Usage
    from pipeline.models import TranslateResult, ValidateReport
    from pipeline.tqa.profile import load_profile

    profile = load_profile(_write_minimal_profile(tmp_path)).data
    result = TranslateResult(
        model_alias="candidate-model",
        model_id="candidate-id",
        usage=Usage(),
        status="completed",
        incomplete_reason=None,
        validate=ValidateReport(ok=False, errors=["empty output"]),
        bilingual_srt=None,
        raw_text="",
        elapsed_sec=0.1,
    )
    monkeypatch.setattr("pipeline.orchestrator.run_once", lambda *_a, **_kw: result)

    candidate = runner._default_translate(
        source_srt=tmp_path / "episode.srt",
        model="candidate-model",
        temperature=None,
        top_p=None,
        out_dir=tmp_path / "run",
        profile=profile,
        episode_summary_override=None,
    )
    assert candidate["status"] == "provider_refusal"


def test_rescued_translation_is_scored_in_separate_lane_not_primary_score(
    tmp_path: Path,
) -> None:
    profile = _write_minimal_profile(tmp_path)
    profile.write_text(
        profile.read_text(encoding="utf-8").replace(
            "max_hard_failures_per_episode: 1",
            "max_hard_failures_per_episode: 0",
        ),
        encoding="utf-8",
    )
    evaluator_calls = 0

    def rescued(**_request: object) -> dict[str, object]:
        return {
            "status": "provider_refusal",
            "translations": {},
            "rescued_translations": {"1": "越界"},
        }

    def evaluator(payload: dict[str, object]) -> dict[str, object]:
        nonlocal evaluator_calls
        evaluator_calls += 1
        return {
            "sample_id": payload["sample_id"],
            "dimension": payload["dimension"],
            "score": 8,
            "hard_failures": [],
            "rationale": "Rescued text is deliverable.",
            "confidence": 0.9,
            "evaluator_run_id": payload["evaluator_run_id"],
        }

    assert runner.run_pipeline(
        profile_path=profile,
        action="all",
        translate_fn=rescued,
        evaluate_fn=evaluator,
    ) == 0
    report = json.loads(
        (tmp_path / "bench-out" / "report.json").read_text(encoding="utf-8")
    )
    assert evaluator_calls == 1
    assert report["models"][0]["score"] == 0.0
    assert report["models"][0]["status"] == "VETO"
    assert report["rescued_quality"][0]["rescued_quality_score"] == 8.0
    report_markdown = (tmp_path / "bench-out" / "report.md").read_text(
        encoding="utf-8"
    )
    assert "Rescued quality" in report_markdown
    assert "8.0" in report_markdown


def test_sample_aggregation_is_report_only_and_does_not_change_episode_score(
    tmp_path: Path,
) -> None:
    profile = _write_minimal_profile(tmp_path)
    text = profile.read_text(encoding="utf-8")
    text = text.replace(
        'dimensions: ["上下文依赖"]',
        'dimensions: ["上下文依赖", "习语/口语"]',
    ).replace(
        '    上下文依赖:\n      description: "Resolve meaning using neighboring subtitles."',
        '    上下文依赖:\n      description: "Resolve meaning using neighboring subtitles."\n'
        '    习语/口语:\n      description: "Natural spoken language."',
    ).replace(
        "    上下文依赖: 1.0",
        "    上下文依赖: 0.5\n    习语/口语: 0.5",
    ).replace("sample_aggregation: weighted_avg", "sample_aggregation: min")
    profile.write_text(text, encoding="utf-8")

    def fake_translate(**_request: object) -> dict[str, object]:
        return {"status": "completed", "translations": {"1": "你好。"}}

    def evaluator(payload: dict[str, object]) -> dict[str, object]:
        score = 8 if payload["dimension"] == "上下文依赖" else 4
        return {
            "sample_id": payload["sample_id"],
            "dimension": payload["dimension"],
            "score": score,
            "hard_failures": [],
            "rationale": "Dimension-specific score.",
            "confidence": 0.9,
            "evaluator_run_id": payload["evaluator_run_id"],
        }

    assert runner.run_pipeline(
        profile_path=profile,
        action="all",
        translate_fn=fake_translate,
        evaluate_fn=evaluator,
    ) == 0
    report = json.loads(
        (tmp_path / "bench-out" / "report.json").read_text(encoding="utf-8")
    )
    episode = report["models"][0]["episodes"][0]
    assert episode["score"] == 6.0
    assert episode["sample_scores"][0]["display_score"] == 4.0
    assert episode["sample_scores"][0]["report_only"] is True
