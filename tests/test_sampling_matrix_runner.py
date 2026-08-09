from __future__ import annotations

import json
import inspect
from pathlib import Path

import model_client
from pipeline import sampling_matrix
from pipeline.models import TranslateResult, ValidateReport


def test_sampling_matrix_runner_module_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (root / "pipeline" / "sampling_matrix.py").is_file()


def test_sampling_matrix_builder_is_available() -> None:
    assert callable(getattr(sampling_matrix, "build_sampling_arms", None))


def test_sampling_matrix_has_ten_single_axis_arms() -> None:
    arms = sampling_matrix.build_sampling_arms()

    assert len(arms) == 10
    assert [(arm.temperature, arm.top_p) for arm in arms] == [
        (None, None),
        (0.1, None),
        (0.3, None),
        (0.7, None),
        (1.0, None),
        (1.3, None),
        (1.5, None),
        (None, 0.7),
        (None, 0.8),
        (None, 1.0),
    ]
    assert all(
        arm.temperature is None or arm.top_p is None
        for arm in arms
    )


def test_case_builder_is_available() -> None:
    assert callable(getattr(sampling_matrix, "build_cases", None))


def test_default_case_builder_is_available() -> None:
    assert callable(getattr(sampling_matrix, "build_default_cases", None))


def test_default_cases_use_the_two_confirmed_models_and_subtitles() -> None:
    cases = sampling_matrix.build_default_cases()

    assert len(cases) == 40
    assert {case.model_alias for case in cases} == {
        "deepseek-v4-flash",
        "qwen3.7-plus",
    }
    assert {case.episode_id for case in cases} == {"S01E03", "S01E06"}
    assert {case.source_srt.name for case in cases} == {
        "A.French.Village.S01E03_eng.srt",
        "A.French.Village.S01E06_eng.srt",
    }
    assert all(case.source_srt.is_file() for case in cases)


def test_case_builder_expands_to_forty_unique_parameter_visible_outputs(tmp_path: Path) -> None:
    cases = sampling_matrix.build_cases(
        models=("deepseek-v4-flash", "qwen3.7-plus"),
        episodes=(
            ("S01E03", tmp_path / "e03.srt"),
            ("S01E06", tmp_path / "e06.srt"),
        ),
    )

    assert len(cases) == 40
    assert len({case.slug for case in cases}) == 40
    assert len({case.final_filename for case in cases}) == 40
    assert all("temperature-" in case.final_filename for case in cases)
    assert all("topP-" in case.final_filename for case in cases)
    assert cases[0].slug.endswith(
        "S01E03__deepseek-v4-flash__temperature-OMIT__topP-OMIT"
    )
    assert any("temperature-0.3__topP-OMIT" in case.slug for case in cases)
    assert any("temperature-OMIT__topP-0.8" in case.slug for case in cases)


def test_collection_preparer_is_available() -> None:
    assert callable(getattr(sampling_matrix, "prepare_collection", None))


def test_prepare_collection_writes_auditable_case_records(tmp_path: Path) -> None:
    e03 = tmp_path / "e03.srt"
    e06 = tmp_path / "e06.srt"
    e03.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nOne\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nTwo\n",
        encoding="utf-8",
    )
    e06.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nThree\n",
        encoding="utf-8",
    )
    cases = sampling_matrix.build_cases(
        models=("deepseek-v4-flash", "qwen3.7-plus"),
        episodes=(("S01E03", e03), ("S01E06", e06)),
    )
    output_root = tmp_path / "collection"

    matrix_path = sampling_matrix.prepare_collection(
        cases=cases,
        output_root=output_root,
    )

    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    assert matrix["case_count"] == 40
    assert matrix["batch_size"] == 50
    assert matrix["batch_jobs"] == 1
    assert matrix["full_subtitles"] is True
    first_case_path = output_root / "cases" / cases[0].slug / "case.json"
    first = json.loads(first_case_path.read_text(encoding="utf-8"))
    assert first["source"]["cue_count"] == 2
    assert len(first["source"]["sha256"]) == 64
    assert first["sampling"] == {
        "temperature": {"sent": False, "value": None},
        "top_p": {"sent": False, "value": None},
    }
    assert first["execution"]["expected_translation_batches"] == 1
    assert first["status"] == "pending"
    assert first["final_bilingual"].endswith(cases[0].final_filename)


def test_case_executor_is_available() -> None:
    assert callable(getattr(sampling_matrix, "execute_case", None))


def test_case_executor_accepts_frozen_summary_and_glossary() -> None:
    parameters = inspect.signature(sampling_matrix.execute_case).parameters
    assert "episode_summary_override" in parameters
    assert "glossary_path" in parameters


def test_execute_case_is_serial_recorded_and_resumable(tmp_path: Path) -> None:
    source = tmp_path / "e03.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nOne\n",
        encoding="utf-8",
    )
    case = sampling_matrix.build_cases(
        models=("deepseek-v4-flash",),
        episodes=(("S01E03", source),),
    )[0]
    output_root = tmp_path / "collection"
    sampling_matrix.prepare_collection(cases=(case,), output_root=output_root)
    glossary = tmp_path / "glossary.md"
    glossary.write_text("| 中文 | 原名 |\n|---|---|\n| 一 | One |\n", encoding="utf-8")
    calls: list[dict[str, object]] = []

    def fake_translate(**kwargs: object) -> TranslateResult:
        calls.append(kwargs)
        return TranslateResult(
            model_alias="deepseek-v4-flash",
            model_id="deepseek-v4-flash-test",
            usage=model_client.Usage(total_tokens=12),
            status="completed",
            incomplete_reason=None,
            validate=ValidateReport(
                ok=True,
                parsed={"0": {"src": "One", "tr": "一"}},
            ),
            bilingual_srt=(
                "1\n00:00:00,000 --> 00:00:01,000\n一\nOne\n"
            ),
            raw_text='{"0":{"src":"One","tr":"一"}}',
            elapsed_sec=1.5,
            sampling={
                "temperature": {"sent": False, "value": None},
                "top_p": {"sent": False, "value": None},
            },
        )

    first = sampling_matrix.execute_case(
        case=case,
        output_root=output_root,
        translate_fn=fake_translate,
        episode_summary_override="frozen context",
        glossary_path=glossary,
    )
    second = sampling_matrix.execute_case(
        case=case,
        output_root=output_root,
        translate_fn=fake_translate,
        episode_summary_override="frozen context",
        glossary_path=glossary,
    )

    assert first["status"] == "completed"
    assert second["status"] == "completed"
    assert second["resume_action"] == "skipped_completed"
    assert len(calls) == 1
    assert calls[0]["batch_size"] == 50
    assert calls[0]["batch_jobs"] == 1
    assert calls[0]["max_cues"] is None
    assert calls[0]["temperature"] is model_client.OMIT
    assert calls[0]["top_p"] is model_client.OMIT
    assert calls[0]["episode_summary_override"] == "frozen context"
    assert calls[0]["glossary_path"] == glossary
    final_path = output_root / "bilingual" / case.final_filename
    assert final_path.read_text(encoding="utf-8").startswith("1\n")
    record = json.loads(
        (output_root / "cases" / case.slug / "case.json").read_text(
            encoding="utf-8"
        )
    )
    assert record["status"] == "completed"
    assert record["attempt_count"] == 1
    assert len(record["final_bilingual_sha256"]) == 64


def test_collection_runner_is_available() -> None:
    assert callable(getattr(sampling_matrix, "run_collection", None))


def test_failed_case_repair_preserves_sampling_and_publishes_final_output(
    tmp_path: Path,
) -> None:
    source = tmp_path / "e03.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nOne\n",
        encoding="utf-8",
    )
    case = sampling_matrix.build_cases(
        models=("qwen3.7-plus",),
        episodes=(("S01E03", source),),
    )[2]
    output_root = tmp_path / "collection"
    sampling_matrix.prepare_collection(cases=(case,), output_root=output_root)
    case_dir = output_root / "cases" / case.slug
    attempt_dir = case_dir / "attempt-001"
    attempt_dir.mkdir()
    case_path = case_dir / "case.json"
    record = json.loads(case_path.read_text(encoding="utf-8"))
    record.update(
        {
            "status": "failed",
            "attempt_count": 1,
            "active_attempt": str(attempt_dir.resolve()),
            "last_error": "DataInspectionFailed",
        }
    )
    case_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    calls: list[dict[str, object]] = []

    def fake_repair(run_dir: Path, srt_path: Path, model: str, **kwargs: object):
        calls.append(
            {
                "run_dir": run_dir,
                "srt_path": srt_path,
                "model": model,
                **kwargs,
            }
        )
        return TranslateResult(
            model_alias=model,
            model_id="qwen-test",
            usage=model_client.Usage(total_tokens=8),
            status="completed",
            incomplete_reason=None,
            validate=ValidateReport(
                ok=True,
                parsed={"0": {"src": "One", "tr": "一"}},
            ),
            bilingual_srt=(
                "1\n00:00:00,000 --> 00:00:01,000\n一\nOne\n"
            ),
            raw_text="",
            elapsed_sec=0.2,
            sampling={
                "temperature": {"sent": True, "value": 0.3},
                "top_p": {"sent": False, "value": None},
            },
        )

    progress = sampling_matrix.repair_failed_cases(
        cases=(case,),
        output_root=output_root,
        repair_fn=fake_repair,
    )

    assert progress["completed"] == 1
    assert progress["failed"] == 0
    assert len(calls) == 1
    assert calls[0]["temperature"] == 0.3
    assert calls[0]["top_p"] is model_client.OMIT
    assert calls[0]["sub_batch_size"] == 10
    final_path = output_root / "bilingual" / case.final_filename
    assert final_path.read_text(encoding="utf-8").startswith("1\n")
    repaired = json.loads(case_path.read_text(encoding="utf-8"))
    assert repaired["status"] == "completed"
    assert repaired["repair"]["source_attempt"].endswith("attempt-001")
    assert repaired["result"]["sampling"] == record["sampling"]


def test_inspection_rescue_updates_failed_case_record(tmp_path: Path) -> None:
    source = tmp_path / "e03.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nCommunists\n",
        encoding="utf-8",
    )
    case = sampling_matrix.build_cases(
        models=("qwen3.7-plus",),
        episodes=(("S01E03", source),),
    )[1]
    output_root = tmp_path / "collection"
    sampling_matrix.prepare_collection(cases=(case,), output_root=output_root)
    case_dir = output_root / "cases" / case.slug
    attempt_dir = case_dir / "attempt-001"
    attempt_dir.mkdir()
    (attempt_dir / "input.json").write_text(
        json.dumps({"0": "Communists"}), encoding="utf-8"
    )
    (attempt_dir / "parsed.json").write_text("{}", encoding="utf-8")
    case_path = case_dir / "case.json"
    record = json.loads(case_path.read_text(encoding="utf-8"))
    record.update(
        {
            "status": "failed",
            "active_attempt": str(attempt_dir.resolve()),
        }
    )
    case_path.write_text(json.dumps(record), encoding="utf-8")
    calls: list[dict[str, object]] = []

    def fake_rescue(**kwargs: object) -> TranslateResult:
        calls.append(kwargs)
        return TranslateResult(
            model_alias="qwen3.7-plus",
            model_id="qwen-test",
            usage=model_client.Usage(total_tokens=4),
            status="completed",
            incomplete_reason=None,
            validate=ValidateReport(
                ok=True,
                parsed={"0": {"src": "Communists", "tr": "共产党"}},
            ),
            bilingual_srt=(
                "1\n00:00:00,000 --> 00:00:01,000\n共产党\nCommunists\n"
            ),
            raw_text="",
            elapsed_sec=0.1,
        )

    progress = sampling_matrix.rescue_inspection_blocked_cases(
        cases=(case,),
        output_root=output_root,
        rescue_fn=fake_rescue,
    )

    assert progress["completed"] == 0
    assert progress["provider_refusal"] == 1
    assert progress["inspection_rescue_completed"] == 1
    assert calls[0]["temperature"] == 0.1
    assert calls[0]["top_p"] is model_client.OMIT
    updated = json.loads(case_path.read_text(encoding="utf-8"))
    assert updated["status"] == "provider_refusal"
    assert updated["inspection_rescue"]["status"] == "completed"
    assert updated["inspection_rescue"]["result"]["sampling"] == record["sampling"]
    rescue_output = Path(updated["inspection_rescue"]["output"])
    assert rescue_output.name.endswith("__inspection-rescue.srt")
    assert rescue_output.parent == output_root / "bilingual"
    assert rescue_output.is_file()
    assert not (output_root / "bilingual" / case.final_filename).exists()

    updated["inspection_rescue"]["status"] = "failed"
    case_path.write_text(json.dumps(updated), encoding="utf-8")
    ordinary_repair_calls: list[bool] = []

    def forbidden_ordinary_repair(*args: object, **kwargs: object):
        ordinary_repair_calls.append(True)
        raise AssertionError("provider refusal must not re-enter ordinary repair")

    sampling_matrix.repair_failed_cases(
        cases=(case,),
        output_root=output_root,
        repair_fn=forbidden_ordinary_repair,
    )
    calls.clear()
    retried = sampling_matrix.rescue_inspection_blocked_cases(
        cases=(case,),
        output_root=output_root,
        rescue_fn=fake_rescue,
    )

    assert ordinary_repair_calls == []
    assert len(calls) == 1
    assert retried["inspection_rescue_completed"] == 1


def test_collection_runner_accepts_frozen_summary_map_and_glossary() -> None:
    parameters = inspect.signature(sampling_matrix.run_collection).parameters
    assert "episode_summaries" in parameters
    assert "glossary_path" in parameters


def test_run_collection_tracks_progress_and_honors_limit(tmp_path: Path) -> None:
    source = tmp_path / "e03.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nOne\n",
        encoding="utf-8",
    )
    cases = sampling_matrix.build_cases(
        models=("deepseek-v4-flash",),
        episodes=(("S01E03", source),),
    )
    output_root = tmp_path / "collection"
    sampling_matrix.prepare_collection(cases=cases, output_root=output_root)
    glossary = tmp_path / "glossary.md"
    glossary.write_text("| 中文 | 原名 |\n|---|---|\n| 一 | One |\n", encoding="utf-8")
    calls = 0
    contexts: list[tuple[object, object]] = []

    def fake_translate(**kwargs: object) -> TranslateResult:
        nonlocal calls
        calls += 1
        contexts.append(
            (kwargs["episode_summary_override"], kwargs["glossary_path"])
        )
        return TranslateResult(
            model_alias="deepseek-v4-flash",
            model_id="test-model",
            usage=model_client.Usage(total_tokens=1),
            status="completed",
            incomplete_reason=None,
            validate=ValidateReport(
                ok=True,
                parsed={"0": {"src": "One", "tr": "一"}},
            ),
            bilingual_srt=(
                "1\n00:00:00,000 --> 00:00:01,000\n一\nOne\n"
            ),
            raw_text="{}",
            elapsed_sec=0.1,
        )

    progress = sampling_matrix.run_collection(
        cases=cases,
        output_root=output_root,
        limit=2,
        translate_fn=fake_translate,
        episode_summaries={"S01E03": "frozen E03"},
        glossary_path=glossary,
    )

    assert calls == 2
    assert contexts == [("frozen E03", glossary), ("frozen E03", glossary)]
    assert progress["total"] == 10
    assert progress["completed"] == 2
    assert progress["failed"] == 0
    assert progress["pending"] == 8
    saved = json.loads(
        (output_root / "progress.json").read_text(encoding="utf-8")
    )
    assert saved["completed"] == 2


def test_frozen_summary_generator_is_available() -> None:
    assert callable(
        getattr(sampling_matrix, "generate_frozen_summaries", None)
    )


def test_frozen_summaries_are_generated_once_per_episode_and_reused(
    tmp_path: Path,
) -> None:
    e03 = tmp_path / "e03.srt"
    e06 = tmp_path / "e06.srt"
    e03.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nOne\n",
        encoding="utf-8",
    )
    e06.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nTwo\n",
        encoding="utf-8",
    )
    calls: list[dict[str, object]] = []

    def fake_summary(model: str, cues: list, **kwargs: object):
        calls.append({"model": model, "cues": cues, **kwargs})
        episode_id = Path(kwargs["out_dir"]).name
        return (
            f"frozen {episode_id}",
            model_client.Usage(total_tokens=5),
            "completed",
            None,
        )

    first = sampling_matrix.generate_frozen_summaries(
        episodes=(("S01E03", e03), ("S01E06", e06)),
        output_root=tmp_path / "collection",
        model_alias="deepseek-v4-flash",
        generate_fn=fake_summary,
    )
    second = sampling_matrix.generate_frozen_summaries(
        episodes=(("S01E03", e03), ("S01E06", e06)),
        output_root=tmp_path / "collection",
        model_alias="deepseek-v4-flash",
        generate_fn=fake_summary,
    )

    assert first == second == {
        "S01E03": "frozen S01E03",
        "S01E06": "frozen S01E06",
    }
    assert len(calls) == 2
    assert all(call["temperature"] is model_client.OMIT for call in calls)
    assert all(call["top_p"] is model_client.OMIT for call in calls)
    meta = json.loads(
        (
            tmp_path
            / "collection"
            / "context"
            / "S01E03"
            / "fixed_summary.json"
        ).read_text(encoding="utf-8")
    )
    assert meta["generator_model_alias"] == "deepseek-v4-flash"
    assert meta["sampling"]["temperature"]["sent"] is False
    assert len(meta["summary_sha256"]) == 64


def test_sampling_matrix_cli_is_available() -> None:
    assert callable(getattr(sampling_matrix, "main", None))


def test_plan_cli_writes_the_forty_case_manifest(tmp_path: Path, capsys) -> None:
    output_root = tmp_path / "collection"

    code = sampling_matrix.main(["plan", "--out", str(output_root)])

    assert code == 0
    assert json.loads(
        (output_root / "matrix.json").read_text(encoding="utf-8")
    )["case_count"] == 40
    text = capsys.readouterr().out
    assert "40" in text
    assert "560" in text
