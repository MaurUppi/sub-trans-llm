from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.preprocess.config import PreprocessConfig
from pipeline.preprocess.orchestrate_a import run_preprocess


_ROOT = Path(__file__).resolve().parents[2]
_SDH = _ROOT / "sample" / "SDH.srt"
_STAGE_A = _ROOT / "sample" / "StageA-test.srt"


@pytest.mark.skipif(not _SDH.is_file(), reason="local production sample is excluded")
def test_sdh_production_sample_removes_sdh_blocks(tmp_path: Path) -> None:
    result = run_preprocess(
        _SDH,
        PreprocessConfig(
            work_dir=tmp_path / "sdh",
            remove_sdh=True,
            resplit="off",
        ),
    )

    assert result.meta["counts"] == {
        "in": 1076,
        "sdh_removed": 169,
        "out": 907,
    }
    assert result.clean_srt_path is not None
    assert result.clean_srt_path.is_file()


@pytest.mark.skipif(
    not _STAGE_A.is_file(),
    reason="local production sample is excluded",
)
def test_stage_a_production_sample_auto_resplit_improves_report(
    tmp_path: Path,
) -> None:
    baseline = run_preprocess(
        _STAGE_A,
        PreprocessConfig(
            work_dir=tmp_path / "baseline",
            resplit="off",
        ),
    )
    processed = run_preprocess(
        _STAGE_A,
        PreprocessConfig(
            work_dir=tmp_path / "processed",
            resplit="auto",
        ),
    )

    assert processed.meta["steps"]["resplit"]["applied"] is True
    assert processed.meta["counts"] == {"in": 1510, "out": 1534}
    assert len(processed.report["validation"]["warnings"]) < len(
        baseline.report["validation"]["warnings"]
    )
    assert processed.clean_srt_path is not None
    assert processed.clean_srt_path.is_file()
