from __future__ import annotations

from pathlib import Path

from scripts import ablation_instructions, quality_report_d


ROOT = Path(__file__).resolve().parents[1]
CURRENT_E03 = ROOT / "sample" / "A.French.Village.S01E03_eng.srt"


def test_auxiliary_scripts_use_the_current_sample_tree() -> None:
    assert ablation_instructions.SRT == CURRENT_E03
    assert quality_report_d.DEFAULT_SRT == CURRENT_E03
    assert ablation_instructions.SRT.is_file()
    assert quality_report_d.DEFAULT_SRT.is_file()


def test_quality_report_describes_current_sampling_omit_semantics(
    tmp_path: Path,
) -> None:
    report = quality_report_d.build_report(
        run_dir=tmp_path / "run",
        model="test-model",
        srt_path=CURRENT_E03,
        cfg={},
    )

    assert report["run"]["sampling_note"] == (
        "None / OMIT -> omitted from the API request; explicit number -> sent"
    )
