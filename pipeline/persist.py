from __future__ import annotations

import json
from pathlib import Path

from pipeline.models import TranslateResult


def write_outputs(out_dir: Path, result: TranslateResult, input_json: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "input.json").write_text(input_json, encoding="utf-8")
    (out_dir / "instructions.txt").write_text(result.instructions, encoding="utf-8")
    (out_dir / "raw_output.txt").write_text(result.raw_text or "", encoding="utf-8")
    (out_dir / "validate.json").write_text(
        json.dumps(result.validate.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "meta.json").write_text(
        json.dumps(result.meta_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if result.validate.parsed:
        (out_dir / "parsed.json").write_text(
            json.dumps(result.validate.parsed, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if result.bilingual_srt:
        (out_dir / "bilingual.srt").write_text(result.bilingual_srt, encoding="utf-8")
        partial = out_dir / "bilingual.PARTIAL.txt"
        if partial.exists():
            partial.unlink()
    else:
        (out_dir / "bilingual.PARTIAL.txt").write_text(
            "bilingual.srt not written: validation or API failed.\n"
            "See validate.json / raw_output.txt / meta.json\n",
            encoding="utf-8",
        )
