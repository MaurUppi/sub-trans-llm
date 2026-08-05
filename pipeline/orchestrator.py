from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

import model_client
from model_client import Usage

from pipeline.batch_client import call_one_batch
from pipeline.config import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_GLOSSARY,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_PROMPT,
    DEFAULT_SUMMARY_MAX_OUTPUT_TOKENS,
)
from pipeline.logging_util import log
from pipeline.models import BatchOutcome, Cue, TranslateResult, ValidateReport
from pipeline.persist import write_outputs
from pipeline.prompt import build_instructions
from pipeline.srt_io import (
    build_bilingual_srt,
    build_input_json,
    chunk_cues,
    parse_srt,
    slice_cues,
    sum_usage,
)
from pipeline.subtitle_check import check_subtitle_quality
from pipeline.summary import generate_episode_summary


def run_once(
    srt_path: Path | str,
    model: str,
    *,
    source_language: str = "英语",
    target_language: str = "简体中文",
    prompt_path: Path | str = DEFAULT_PROMPT,
    glossary_path: Optional[Path | str] = None,
    max_cues: Optional[int] = None,
    cue_offset: int = 0,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    out_dir: Optional[Path | str] = None,
    timeout: float = 1200.0,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    max_retries: int = 2,
    retry_backoff_sec: float = 3.0,
    batch_size: int = DEFAULT_BATCH_SIZE,
    batch_jobs: int = 1,
    use_episode_summary: bool = True,
    summary_max_output_tokens: int = DEFAULT_SUMMARY_MAX_OUTPUT_TOKENS,
    summary_timeout: float = 180.0,
) -> TranslateResult:
    """
    整集（或切片）翻译：可选通读摘要 + 按 batch_size 分批送模型，本地合并。

    - batch_size: 每批条数，默认 50；<=0 表示单批整包
    - batch_jobs: 批并行度，1=顺序；>1 多批并行请求后拼装
    - use_episode_summary: 先全量通读生成摘要，再注入各批 instructions
    - 双语 SRT：译文用模型 tr，原文用本地 Cue.text（按全局 id 对齐）
    """
    srt_path = Path(srt_path)
    out_path = Path(out_dir) if out_dir else None

    log(f"📂 加载 SRT: {srt_path.name}")
    all_cues = parse_srt(srt_path)
    cues = slice_cues(all_cues, cue_offset=cue_offset, max_cues=max_cues)
    if not cues:
        raise ValueError(f"no cues parsed from {srt_path}")

    full_input_json, full_input_map = build_input_json(cues)

    t0 = time.perf_counter()
    episode_summary = ""
    summary_usage: Optional[Usage] = None
    summary_notes: list[str] = []

    if use_episode_summary:
        summary_dir = out_path  # 落盘到模型输出根目录
        episode_summary, summary_usage, _sum_status, sum_err = generate_episode_summary(
            model,
            cues,
            max_output_tokens=summary_max_output_tokens,
            timeout=summary_timeout,
            temperature=temperature,
            top_p=top_p,
            max_retries=max_retries,
            retry_backoff_sec=retry_backoff_sec,
            out_dir=summary_dir,
        )
        if sum_err:
            summary_notes.append(f"episode_summary degraded: {sum_err}")
            log(f"   ⚠ 摘要失败，降级为无摘要分批: {sum_err}")
            episode_summary = episode_summary or ""
        elif not episode_summary.strip():
            summary_notes.append("episode_summary empty; continue without")
            log("   ⚠ 摘要为空，降级为无摘要分批")
    else:
        log("   （跳过通读摘要 use_episode_summary=False）")

    instructions = build_instructions(
        prompt_path=prompt_path,
        glossary_path=glossary_path,
        source_language=source_language,
        target_language=target_language,
        episode_summary=episode_summary or None,
    )

    batches = chunk_cues(cues, batch_size)
    n_batches = len(batches)
    jobs = max(1, int(batch_jobs or 1))

    log(
        f"🌐 分批翻译 model={model} cues={len(cues)}/{len(all_cues)} "
        f"batches={n_batches}×{batch_size if batch_size > 0 else 'all'} "
        f"batch_jobs={jobs} max_out={max_output_tokens} timeout={timeout}s "
        f"retries={max_retries} summary={'yes' if episode_summary else 'no'}"
    )
    log(
        f"   full_input ≈ {len(full_input_json)} chars, "
        f"instructions ≈ {len(instructions)} chars "
        f"(summary_chars={len(episode_summary)})"
    )

    if out_path:
        out_path.mkdir(parents=True, exist_ok=True)
        (out_path / "input.json").write_text(full_input_json, encoding="utf-8")
        (out_path / "instructions.txt").write_text(instructions, encoding="utf-8")
        (out_path / "batches_plan.json").write_text(
            json.dumps(
                {
                    "batch_size": batch_size,
                    "batch_jobs": jobs,
                    "n_batches": n_batches,
                    "use_episode_summary": use_episode_summary,
                    "episode_summary_chars": len(episode_summary),
                    "batches": [
                        {
                            "index": i,
                            "n": len(b),
                            "id_from": b[0].id,
                            "id_to": b[-1].id,
                        }
                        for i, b in enumerate(batches)
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    outcomes: list[BatchOutcome] = []

    def _run_idx(i: int) -> BatchOutcome:
        bout = (out_path / f"batch_{i:02d}") if out_path else None
        return call_one_batch(
            model=model,
            batch_index=i,
            batch_cues=batches[i],
            instructions=instructions,
            max_output_tokens=max_output_tokens,
            timeout=timeout,
            temperature=temperature,
            top_p=top_p,
            max_retries=max_retries,
            retry_backoff_sec=retry_backoff_sec,
            batch_out=bout,
        )

    def _run_many(indices: list[int], *, parallel: bool, label: str) -> dict[int, BatchOutcome]:
        out_map: dict[int, BatchOutcome] = {}
        if not indices:
            return out_map
        if not parallel or len(indices) == 1:
            for i in indices:
                out_map[i] = _run_idx(i)
            return out_map
        log(f"   ⚡ {label} 并行 {len(indices)} 批（workers={min(jobs, len(indices))}）…")
        with ThreadPoolExecutor(max_workers=min(jobs, len(indices))) as ex:
            futs = {ex.submit(_run_idx, i): i for i in indices}
            for fut in as_completed(futs):
                i = futs[fut]
                try:
                    out_map[i] = fut.result()
                except Exception as e:  # noqa: BLE001
                    log(f"   ✗ batch {i:02d} worker 崩溃: {e}")
                    out_map[i] = BatchOutcome(
                        batch_index=i,
                        cues=batches[i],
                        input_map={c.id: c.text for c in batches[i]},
                        raw_text="",
                        status=f"error: {type(e).__name__}: {e}",
                        incomplete_reason=None,
                        usage=Usage(),
                        model_id="",
                        alias=model,
                        validate=ValidateReport(ok=False, errors=[str(e)]),
                    )
        return out_map

    # 第一波：顺序或并行
    first = _run_many(
        list(range(n_batches)),
        parallel=(jobs > 1 and n_batches > 1),
        label="首轮",
    )
    outcomes_map = dict(first)

    # 失败批重跑（并行首轮后必做；顺序首轮也做一次，提高稳健性）
    failed_idx = sorted(i for i, oc in outcomes_map.items() if not oc.validate.ok)
    if failed_idx:
        log(
            f"🔄 失败批重跑（顺序）: {failed_idx} "
            f"（共 {len(failed_idx)}/{n_batches}）"
        )
        # 失败批默认顺序，降低限流/审核叠加；仍走各自 max_retries
        retry_map = _run_many(failed_idx, parallel=False, label="失败重跑")
        for i, oc in retry_map.items():
            # 累加 usage：保留两轮 usage 之和
            prev = outcomes_map[i]
            oc.usage = sum_usage([prev.usage, oc.usage])
            if oc.validate.ok:
                oc.validate.warnings.append(
                    f"batch {i:02d}: recovered on failure re-run"
                )
            else:
                oc.validate.warnings.append(
                    f"batch {i:02d}: still failing after re-run"
                )
            outcomes_map[i] = oc

    outcomes = [outcomes_map[i] for i in range(n_batches)]
    elapsed = time.perf_counter() - t0

    # 合并
    merged_parsed: dict[str, dict[str, str]] = {}
    all_errors: list[str] = []
    all_warnings: list[str] = []
    batch_reports: list[dict[str, Any]] = []
    usages: list[Usage] = []
    raw_parts: list[str] = []
    model_id = ""
    alias = model
    any_incomplete: Optional[str] = None

    for oc in outcomes:
        usages.append(oc.usage)
        if oc.model_id:
            model_id = oc.model_id
        if oc.alias:
            alias = oc.alias
        if oc.incomplete_reason and not any_incomplete:
            any_incomplete = oc.incomplete_reason
        if oc.raw_text:
            raw_parts.append(f"--- batch {oc.batch_index:02d} ---\n{oc.raw_text}")
        batch_reports.append(
            {
                "batch_index": oc.batch_index,
                "ok": oc.validate.ok,
                "status": oc.status,
                "n_in": oc.validate.stats.get("n_in", len(oc.input_map)),
                "n_tr_ok": oc.validate.stats.get("n_tr_ok", 0),
                "errors": oc.validate.errors,
                "warnings": oc.validate.warnings,
                "usage": {
                    "input_tokens": oc.usage.input_tokens,
                    "output_tokens": oc.usage.output_tokens,
                    "total_tokens": oc.usage.total_tokens,
                    "reasoning_tokens": oc.usage.reasoning_tokens,
                },
            }
        )
        if not oc.validate.ok:
            all_errors.append(
                f"batch {oc.batch_index:02d}: "
                + ("; ".join(oc.validate.errors[:3]) or oc.status)
            )
        all_warnings.extend(
            f"batch {oc.batch_index:02d}: {w}" for w in oc.validate.warnings
        )
        if oc.validate.parsed:
            for k, v in oc.validate.parsed.items():
                if k in merged_parsed:
                    all_warnings.append(f"duplicate key after merge: {k}")
                merged_parsed[k] = v

    # 全集键完整性
    missing = sorted(
        set(full_input_map) - set(merged_parsed),
        key=lambda x: int(x) if x.isdigit() else x,
    )
    if missing:
        all_errors.append(
            "missing keys after merge: "
            + ", ".join(missing[:30])
            + (f" ...(+{len(missing)-30})" if len(missing) > 30 else "")
        )

    n_ok = sum(1 for oc in outcomes if oc.validate.ok)
    overall_ok = n_ok == n_batches and not missing and len(merged_parsed) == len(cues)
    status = (
        "completed"
        if overall_ok
        else f"error: {n_ok}/{n_batches} batches ok, merged={len(merged_parsed)}/{len(cues)}"
    )

    vr = ValidateReport(
        ok=overall_ok,
        errors=all_errors,
        warnings=all_warnings,
        parsed=merged_parsed if merged_parsed else None,
        stats={
            "n_in": len(full_input_map),
            "n_out": len(merged_parsed),
            "n_tr_ok": sum(
                1
                for k, v in merged_parsed.items()
                if (v.get("tr") or "").strip()
            ),
            "n_batches": n_batches,
            "n_batches_ok": n_ok,
        },
    )

    bilingual: Optional[str] = None
    subtitle_quality: Optional[dict] = None
    if overall_ok:
        tr_map = {k: v["tr"] for k, v in merged_parsed.items()}
        # 原文始终用本地 Cue.text，避免模型 src 改写（方案 B 评估见 docs）
        bilingual = build_bilingual_srt(cues, tr_map)
        # 译文侧字幕约束：只度量不阻断，供六模型横向比较
        sq = check_subtitle_quality(cues, tr_map)
        subtitle_quality = sq.to_dict()
        if sq.n_cps_over or sq.n_line_over or sq.n_lines_over:
            vr.warnings.append(
                f"subtitle quality: cps_over={sq.n_cps_over} "
                f"line_over={sq.n_line_over} lines_over={sq.n_lines_over} "
                f"(max_cps={sq.max_cps}, p95={sq.p95_cps})"
            )

    raw_text = "\n\n".join(raw_parts)
    usage = sum_usage(usages)
    if summary_usage is not None:
        usage = sum_usage([summary_usage, usage])

    for note in summary_notes:
        vr.warnings.append(note)

    result = TranslateResult(
        model_alias=alias,
        model_id=model_id,
        usage=usage,
        status=status,
        incomplete_reason=any_incomplete,
        validate=vr,
        bilingual_srt=bilingual,
        raw_text=raw_text,
        elapsed_sec=elapsed,
        input_map=full_input_map,
        instructions=instructions,
        cues=cues,
        batch_count=n_batches,
        batch_size=batch_size if batch_size > 0 else len(cues),
        batch_jobs=jobs,
        batch_reports=batch_reports,
        episode_summary=episode_summary,
        summary_usage=summary_usage,
        subtitle_quality=subtitle_quality,
    )
    if out_path:
        write_outputs(out_path, result, full_input_json)

    if result.ok:
        log(
            f"✅ 完成 model={alias} cues={len(cues)} batches={n_ok}/{n_batches} "
            f"tokens={usage.total_tokens} "
            f"(summary={summary_usage.total_tokens if summary_usage else 0}) "
            f"sec={elapsed:.1f} → {out_path or '(no out_dir)'}"
        )
    else:
        log(
            f"❌ 未通过 model={alias} batches_ok={n_ok}/{n_batches} "
            f"merged={len(merged_parsed)}/{len(cues)} "
            f"errors={all_errors[:3]} sec={elapsed:.1f}"
        )
    return result


# write_outputs: pipeline.persist
