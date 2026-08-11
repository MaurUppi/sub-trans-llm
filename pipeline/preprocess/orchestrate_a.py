from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from pipeline.logging_util import log
from pipeline.preprocess.bridge import document_to_cues, parse_to_document, write_srt
from pipeline.preprocess.config import PreprocessConfig
from pipeline.preprocess.detect import (
    detect_overlaps,
    needs_resplit_rules,
    should_fix_overlaps,
)
from pipeline.preprocess.types import PreprocessResult
from pipeline.rules.sub_processor import ProcessingConfig, SRTProcessor


def run_preprocess(
    srt_path: Path | str,
    config: Optional[PreprocessConfig] = None,
) -> PreprocessResult:
    """
    Stage A: clean / resplit / retiming only. Does not translate.
    """
    config = config or PreprocessConfig()
    source = Path(srt_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"SRT not found: {source}")

    work_dir = config.work_dir
    if work_dir is None:
        work_dir = source.parent / f".preprocess_{source.stem}"
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    document = parse_to_document(source)
    n_in = document.total_blocks
    meta: dict[str, Any] = {
        "source_srt": str(source),
        "steps": {},
        "counts": {"in": n_in},
        "backends": {},
        "notes": [],
    }
    report: dict[str, Any] = {}

    # A1 fix-overlaps (auto/on/off)
    ostats = detect_overlaps(document, min_overlap_ms=config.overlap_min_ms)
    report["overlaps"] = {
        "pair_count": ostats.pair_count,
        "overlap_count": ostats.overlap_count,
        "max_overlap_ms": ostats.max_overlap_ms,
        "ratio": ostats.overlap_ratio,
    }
    apply_fix = should_fix_overlaps(ostats, mode=config.fix_overlaps)
    meta["steps"]["fix_overlaps"] = {
        "mode": config.fix_overlaps,
        "detected": ostats.overlap_count,
        "applied": apply_fix,
    }
    if apply_fix:
        log(f"🔧 fix-overlaps: detected={ostats.overlap_count}, applying…")
        document = document.fix_rolling_window_overlaps()
        meta["backends"]["fix_overlaps"] = "pipeline.rules.sub_processor"
    else:
        log(f"🔧 fix-overlaps: skip (mode={config.fix_overlaps}, detected={ostats.overlap_count})")

    # A2 SDH
    meta["steps"]["remove_sdh"] = config.remove_sdh
    if config.remove_sdh:
        before = document.total_blocks
        document = document.remove_sdh_blocks_and_clean_content()
        meta["counts"]["sdh_removed"] = before - document.total_blocks
        meta["backends"]["remove_sdh"] = "pipeline.rules.sub_processor"
        log(f"🔧 remove-sdh: {before} → {document.total_blocks}")

    # A3 disfluency
    meta["steps"]["remove_disfluency"] = config.remove_disfluency
    if config.remove_disfluency:
        from pipeline.rules.sub_processor import DisfluencyRemover

        before = document.total_blocks
        document = DisfluencyRemover().process_document(document)
        meta["counts"]["disfluency_dropped_blocks"] = before - document.total_blocks
        meta["backends"]["remove_disfluency"] = "pipeline.rules.DisfluencyRemover"
        log(f"🔧 remove-disfluency: {before} → {document.total_blocks}")

    # A4 optimize (VC) — optional, may be no-op if not available
    meta["steps"]["optimize"] = config.optimize
    if config.optimize:
        try:
            from pipeline.preprocess.vc_optimize_adapter import optimize_document

            document, opt_meta = optimize_document(
                document,
                model=config.model,
                api_mode=config.api_mode,
            )
            meta["backends"]["optimize"] = "pipeline.preprocess.vc_optimize_adapter"
            meta["notes"].extend(opt_meta.get("notes") or [])
        except Exception as e:  # noqa: BLE001
            meta["notes"].append(f"optimize skipped: {type(e).__name__}: {e}")
            log(f"⚠ optimize skipped: {e}")

    # A5 resplit
    need_split = needs_resplit_rules(
        document,
        char_limit=config.english_char_limit,
        max_lines=config.max_lines,
    )
    has_words = bool(config.words_path and Path(config.words_path).is_file())
    apply_resplit = False
    if config.resplit == "on":
        apply_resplit = True
    elif config.resplit == "off":
        apply_resplit = False
    else:
        apply_resplit = need_split or has_words

    meta["steps"]["resplit"] = {
        "mode": config.resplit,
        "need_split_heuristic": need_split,
        "has_words": has_words,
        "applied": apply_resplit,
    }

    if apply_resplit:
        if has_words:
            try:
                from pipeline.preprocess.vc_split_adapter import resplit_with_vc

                document, sp_meta = resplit_with_vc(
                    document,
                    words_path=Path(config.words_path),
                    model=config.model,
                    api_mode=config.api_mode,
                )
                meta["backends"]["resplit"] = "pipeline.preprocess.vc_split_adapter"
                meta["notes"].extend(sp_meta.get("notes") or [])
            except Exception as e:  # noqa: BLE001
                meta["notes"].append(f"vc_split failed, fallback rules: {e}")
                log(f"⚠ vc_split failed, rules fallback: {e}")
                document = _rules_resplit(document, config)
                meta["backends"]["resplit"] = "pipeline.rules.timeline_split"
        else:
            document = _rules_resplit(document, config)
            meta["backends"]["resplit"] = "pipeline.rules.timeline_split"
        log(f"🔧 resplit applied → blocks={document.total_blocks}")

    # A6 report
    proc = SRTProcessor(ProcessingConfig(no_punct_fix=True, remove_sdh=False, remove_disfluency=False))
    try:
        report["validation"] = proc.validate_document(document)
    except Exception as e:  # noqa: BLE001
        report["validation_error"] = str(e)

    cues = document_to_cues(document)
    meta["counts"]["out"] = len(cues)

    clean_path = work_dir / f"{source.stem}.clean.srt"
    write_srt(clean_path, cues)
    meta["clean_srt"] = str(clean_path)

    (work_dir / "preprocess_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (work_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # copy original for audit
    raw_copy = work_dir / "input.srt"
    if not raw_copy.exists():
        raw_copy.write_bytes(source.read_bytes())

    log(f"✅ Stage A done: {n_in} → {len(cues)} cues → {clean_path}")
    return PreprocessResult(
        cues=cues,
        clean_srt_path=clean_path,
        source_srt_path=source,
        meta=meta,
        report=report,
    )


def _rules_resplit(document, config: PreprocessConfig):
    """Use SRTProcessor timeline split path without SDH/disfluency re-run."""
    pcfg = ProcessingConfig(
        remove_sdh=False,
        remove_disfluency=False,
        no_punct_fix=True,
        no_speed_check=False,
    )
    processor = SRTProcessor(pcfg)
    # process_document line-breaking then split overlong
    processed = processor._process_document(document)
    return processor._split_overlong_blocks(processed)
