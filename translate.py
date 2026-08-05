"""
字幕翻译模块：SRT → JSON input、外部文件拼 instructions、校验、双语 SRT。

约定见 docs/quality_control.md / docs/benchmark_plan.md。
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import model_client
from model_client import Usage

from pipeline.config import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_GLOSSARY,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_PROMPT,
    DEFAULT_SUMMARY_MAX_OUTPUT_TOKENS,
    ELLIPSIS_BAD as _ELLIPSIS_BAD,
    ELLIPSIS_OK as _ELLIPSIS_OK,
    ROOT as _ROOT,
)
from pipeline.models import BatchOutcome, Cue, TranslateResult, ValidateReport
from pipeline.prompt import (
    SUMMARY_INSTRUCTIONS,
    build_instructions,
    build_summary_input,
    compact_glossary,
)
from pipeline.summary import generate_episode_summary
from pipeline.retry import is_retryable_exception as _is_retryable_exception
from pipeline.retry import should_retry_result as _should_retry_result
from pipeline.logging_util import log as _log
from pipeline.batch_client import call_one_batch as _call_one_batch
from pipeline.persist import write_outputs as _write_outputs
from pipeline.json_repair import repair_model_json, strip_code_fence as _strip_code_fence
from pipeline.validate import validate_response
from pipeline.srt_io import (
    build_bilingual_srt,
    build_input_json,
    chunk_cues,
    parse_srt,
    reindex_cues,
    slice_cues,
    sum_usage,
)

# re-export for callers
__all__ = [
    "Cue",
    "ValidateReport",
    "TranslateResult",
    "parse_srt",
    "run_once",
    "repair_run_dir",
    "self_check_offline",
]


# SRT I/O: pipeline.srt_io


# Prompt/summary: pipeline.prompt / pipeline.summary


# Validate: pipeline.validate / json_repair


# Bilingual SRT: pipeline.srt_io


# Retry/logging: pipeline.retry / logging_util


# Batch call: pipeline.batch_client


# ---------------------------------------------------------------------------
# run_once：分批（顺序或并行）+ 本地拼装
# ---------------------------------------------------------------------------


def run_once(
    srt_path: Path | str,
    model: str,
    *,
    source_language: str = "英语",
    target_language: str = "简体中文",
    prompt_path: Path | str = DEFAULT_PROMPT,
    glossary_path: Optional[Path | str] = DEFAULT_GLOSSARY,
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

    _log(f"📂 加载 SRT: {srt_path.name}")
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
            _log(f"   ⚠ 摘要失败，降级为无摘要分批: {sum_err}")
            episode_summary = episode_summary or ""
        elif not episode_summary.strip():
            summary_notes.append("episode_summary empty; continue without")
            _log("   ⚠ 摘要为空，降级为无摘要分批")
    else:
        _log("   （跳过通读摘要 use_episode_summary=False）")

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

    _log(
        f"🌐 分批翻译 model={model} cues={len(cues)}/{len(all_cues)} "
        f"batches={n_batches}×{batch_size if batch_size > 0 else 'all'} "
        f"batch_jobs={jobs} max_out={max_output_tokens} timeout={timeout}s "
        f"retries={max_retries} summary={'yes' if episode_summary else 'no'}"
    )
    _log(
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
        return _call_one_batch(
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
        _log(f"   ⚡ {label} 并行 {len(indices)} 批（workers={min(jobs, len(indices))}）…")
        with ThreadPoolExecutor(max_workers=min(jobs, len(indices))) as ex:
            futs = {ex.submit(_run_idx, i): i for i in indices}
            for fut in as_completed(futs):
                i = futs[fut]
                try:
                    out_map[i] = fut.result()
                except Exception as e:  # noqa: BLE001
                    _log(f"   ✗ batch {i:02d} worker 崩溃: {e}")
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
        _log(
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
    if overall_ok:
        tr_map = {k: v["tr"] for k, v in merged_parsed.items()}
        # 原文始终用本地 Cue.text，避免模型 src 改写（方案 B 评估见 docs）
        bilingual = build_bilingual_srt(cues, tr_map)

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
    )
    if out_path:
        _write_outputs(out_path, result, full_input_json)

    if result.ok:
        _log(
            f"✅ 完成 model={alias} cues={len(cues)} batches={n_ok}/{n_batches} "
            f"tokens={usage.total_tokens} "
            f"(summary={summary_usage.total_tokens if summary_usage else 0}) "
            f"sec={elapsed:.1f} → {out_path or '(no out_dir)'}"
        )
    else:
        _log(
            f"❌ 未通过 model={alias} batches_ok={n_ok}/{n_batches} "
            f"merged={len(merged_parsed)}/{len(cues)} "
            f"errors={all_errors[:3]} sec={elapsed:.1f}"
        )
    return result


# write_outputs: pipeline.persist


def repair_run_dir(
    run_dir: Path | str,
    srt_path: Path | str,
    model: str,
    *,
    batch_indices: Optional[list[int]] = None,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    timeout: float = 300.0,
    max_retries: int = 2,
    retry_backoff_sec: float = 3.0,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    sub_batch_size: int = 10,
) -> TranslateResult:
    """
    对已有 run 目录只重跑失败批（或指定 batch_indices），合并进 parsed.json 并尝试生成 bilingual.srt。

    需要目录内已有: input.json, instructions.txt；建议有 meta.json / parsed.json。
    sub_batch_size: 整批 API 失败时，拆成更小块重试（绕过审核/截断），默认 10。
    """
    run_dir = Path(run_dir)
    srt_path = Path(srt_path)
    if not (run_dir / "input.json").is_file():
        raise FileNotFoundError(f"missing input.json in {run_dir}")
    if not (run_dir / "instructions.txt").is_file():
        raise FileNotFoundError(f"missing instructions.txt in {run_dir}")

    full_input_map: dict[str, str] = json.loads(
        (run_dir / "input.json").read_text(encoding="utf-8")
    )
    instructions = (run_dir / "instructions.txt").read_text(encoding="utf-8")

    # 从 SRT 重建 cues（全局 reindex 后按 input 键过滤）
    all_cues = reindex_cues(parse_srt(srt_path))
    # 若 run 是 max_cues 切片，input 键为 0..n-1
    cues = [c for c in all_cues if c.id in full_input_map]
    if len(cues) != len(full_input_map):
        # 仅用 input 文本 + 时间码尽量匹配
        by_id = {c.id: c for c in all_cues}
        cues = []
        for kid, text in sorted(
            full_input_map.items(), key=lambda x: int(x[0]) if x[0].isdigit() else x[0]
        ):
            if kid in by_id:
                cues.append(by_id[kid])
            else:
                cues.append(
                    Cue(id=kid, seq=int(kid) if kid.isdigit() else 0, start="00:00:00,000", end="00:00:00,000", text=text)
                )

    meta_path = run_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    batch_size = int(meta.get("batch_size") or DEFAULT_BATCH_SIZE)
    batches = chunk_cues(cues, batch_size)
    n_batches = len(batches)

    existing: dict[str, dict[str, str]] = {}
    parsed_path = run_dir / "parsed.json"
    if parsed_path.is_file():
        existing = json.loads(parsed_path.read_text(encoding="utf-8"))

    # 判定失败批
    if batch_indices is None:
        failed: list[int] = []
        reports = meta.get("batch_reports") or []
        if reports:
            for br in reports:
                if not br.get("ok"):
                    failed.append(int(br["batch_index"]))
        else:
            missing = set(full_input_map) - set(existing)
            for mid in missing:
                try:
                    failed.append(int(mid) // batch_size)
                except ValueError:
                    pass
            failed = sorted(set(failed))
        batch_indices = failed

    batch_indices = sorted(set(int(i) for i in batch_indices))
    _log(f"🔧 repair_run_dir {run_dir.name} re-run batches={batch_indices}")

    # 先对已有 raw 尝试 JSON 加固解析（免 API）
    recovered_offline: list[int] = []
    for i in list(batch_indices):
        raw_p = run_dir / f"batch_{i:02d}" / "raw_output.txt"
        if not raw_p.is_file():
            continue
        raw = raw_p.read_text(encoding="utf-8")
        input_map = {c.id: c.text for c in batches[i]} if i < len(batches) else {}
        if not input_map and (run_dir / f"batch_{i:02d}" / "input.json").is_file():
            input_map = json.loads(
                (run_dir / f"batch_{i:02d}" / "input.json").read_text(encoding="utf-8")
            )
        vr = validate_response(raw, input_map)
        if vr.ok and vr.parsed:
            _log(f"   ✓ batch {i:02d} 离线 JSON 加固恢复 {len(vr.parsed)} keys")
            existing.update(vr.parsed)
            recovered_offline.append(i)
            (run_dir / f"batch_{i:02d}" / "parsed.json").write_text(
                json.dumps(vr.parsed, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (run_dir / f"batch_{i:02d}" / "validate.json").write_text(
                json.dumps(vr.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )

    need_api = [i for i in batch_indices if i not in recovered_offline]
    usages = [Usage()]
    t0 = time.perf_counter()
    for i in need_api:
        if i < 0 or i >= n_batches:
            _log(f"   skip invalid batch index {i}")
            continue
        bout = run_dir / f"batch_{i:02d}"
        oc = _call_one_batch(
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
        usages.append(oc.usage)
        if oc.validate.ok and oc.validate.parsed:
            existing.update(oc.validate.parsed)
            _log(f"   ✓ batch {i:02d} API 重跑成功")
            continue

        _log(
            f"   ✗ batch {i:02d} 整批仍失败，拆小块重试 "
            f"(sub_batch_size={sub_batch_size})…"
        )
        # 审核/截断：拆小块（保持全局 id）；仍失败再减半
        sizes_to_try = []
        sb = sub_batch_size if sub_batch_size > 0 else 10
        while sb >= 1:
            sizes_to_try.append(sb)
            if sb == 1:
                break
            sb = max(1, sb // 2)
            if sb in sizes_to_try:
                break

        ids = {c.id for c in batches[i]}
        remaining = [c for c in batches[i] if c.id not in existing]
        for sb_size in sizes_to_try:
            if not remaining:
                break
            _log(f"      · 尝试 sub_batch_size={sb_size} remaining={len(remaining)}")
            sub_chunks = chunk_cues(remaining, sb_size)
            still: list[Cue] = []
            for si, sub in enumerate(sub_chunks):
                sub_out = bout / f"sub{sb_size}_{si:02d}"
                soc = _call_one_batch(
                    model=model,
                    batch_index=i,
                    batch_cues=sub,
                    instructions=instructions,
                    max_output_tokens=max_output_tokens,
                    timeout=timeout,
                    temperature=temperature,
                    top_p=top_p,
                    max_retries=max(1, max_retries),
                    retry_backoff_sec=retry_backoff_sec,
                    batch_out=sub_out,
                )
                usages.append(soc.usage)
                if soc.validate.ok and soc.validate.parsed:
                    existing.update(soc.validate.parsed)
                    _log(
                        f"      ✓ batch {i:02d} sub{sb_size}/{si:02d} "
                        f"ids={sub[0].id}..{sub[-1].id} n={len(sub)}"
                    )
                else:
                    still.extend(sub)
                    _log(
                        f"      ✗ batch {i:02d} sub{sb_size}/{si:02d} "
                        f"ids={sub[0].id}..{sub[-1].id} "
                        f"err={soc.validate.errors[:1]}"
                    )
            remaining = [c for c in still if c.id not in existing]

        if ids.issubset(set(existing.keys())):
            part = {k: existing[k] for k in ids}
            (bout / "parsed.json").write_text(
                json.dumps(part, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            _log(f"   ✓ batch {i:02d} 经 sub-batch 凑齐 {len(part)} keys")
        else:
            miss = sorted(
                ids - set(existing.keys()),
                key=lambda x: int(x) if x.isdigit() else x,
            )
            _log(f"   ✗ batch {i:02d} sub-batch 后仍缺 {len(miss)}: {miss[:10]}")

    elapsed = time.perf_counter() - t0
    missing = sorted(
        set(full_input_map) - set(existing),
        key=lambda x: int(x) if x.isdigit() else x,
    )
    overall_ok = len(missing) == 0 and len(existing) == len(full_input_map)
    errors = []
    if missing:
        errors.append(
            "still missing keys: "
            + ", ".join(missing[:40])
            + (f" ...(+{len(missing)-40})" if len(missing) > 40 else "")
        )

    bilingual = None
    if overall_ok:
        tr_map = {k: v["tr"] for k, v in existing.items()}
        bilingual = build_bilingual_srt(cues, tr_map)

    # 更新 batch_reports ok flags where possible
    reports = meta.get("batch_reports") or []
    for br in reports:
        bi = int(br["batch_index"])
        if bi in recovered_offline or bi in need_api:
            # recompute ok from keys coverage
            ids = {c.id for c in batches[bi]} if bi < len(batches) else set()
            br["ok"] = ids.issubset(set(existing.keys()))
            if br["ok"]:
                br["errors"] = []
                br["n_tr_ok"] = len(ids)
                br["status"] = "completed"

    vr = ValidateReport(
        ok=overall_ok,
        errors=errors,
        warnings=[f"offline recovered batches: {recovered_offline}"]
        if recovered_offline
        else [],
        parsed=existing if existing else None,
        stats={
            "n_in": len(full_input_map),
            "n_out": len(existing),
            "n_tr_ok": sum(1 for v in existing.values() if (v.get("tr") or "").strip()),
            "n_batches": n_batches,
            "n_batches_ok": sum(1 for br in reports if br.get("ok"))
            if reports
            else (n_batches if overall_ok else 0),
        },
    )
    usage = sum_usage(usages)
    result = TranslateResult(
        model_alias=model,
        model_id=str(meta.get("model_id") or ""),
        usage=usage,
        status="completed" if overall_ok else f"error: missing {len(missing)} keys",
        incomplete_reason=None,
        validate=vr,
        bilingual_srt=bilingual,
        raw_text="",
        elapsed_sec=elapsed,
        input_map=full_input_map,
        instructions=instructions,
        cues=cues,
        batch_count=n_batches,
        batch_size=batch_size,
        batch_jobs=int(meta.get("batch_jobs") or 1),
        batch_reports=reports,
        episode_summary=(
            (run_dir / "episode_summary.txt").read_text(encoding="utf-8")
            if (run_dir / "episode_summary.txt").is_file()
            else ""
        ),
    )
    # preserve previous total usage if present
    if meta.get("usage") and overall_ok:
        prev_u = meta["usage"]
        result.usage = Usage(
            input_tokens=int(prev_u.get("input_tokens") or 0)
            + usage.input_tokens,
            output_tokens=int(prev_u.get("output_tokens") or 0)
            + usage.output_tokens,
            reasoning_tokens=int(prev_u.get("reasoning_tokens") or 0)
            + usage.reasoning_tokens,
            total_tokens=int(prev_u.get("total_tokens") or 0) + usage.total_tokens,
        )

    _write_outputs(run_dir, result, json.dumps(full_input_map, ensure_ascii=False, separators=(",", ":")))
    if overall_ok:
        _log(f"✅ repair 完成 → bilingual.srt keys={len(existing)}")
    else:
        _log(f"❌ repair 仍缺 {len(missing)} keys: {missing[:20]}")
    return result


def self_check_offline(srt_path: Path | str) -> None:
    """无 API 的快速自检。"""
    cues = parse_srt(srt_path)
    assert len(cues) > 0, "no cues"
    sliced = slice_cues(cues, max_cues=8)
    assert len(sliced) == 8
    assert sliced[0].id == "0"
    js, mp = build_input_json(sliced)
    assert json.loads(js) == mp
    inst = build_instructions()
    assert "英语" in inst or "${sourceLanguage}" not in inst
    assert "简体中文" in inst or "${targetLanguage}" not in inst
    assert " = " in inst  # glossary lines

    # good fixture
    good = {
        k: {"src": v, "tr": "测试译文"}
        for k, v in mp.items()
    }
    vr = validate_response(json.dumps(good, ensure_ascii=False), mp)
    assert vr.ok, vr.errors

    # fence
    fenced = "```json\n" + json.dumps(good, ensure_ascii=False) + "\n```"
    assert validate_response(fenced, mp).ok

    # missing key
    bad = {k: good[k] for k in list(good)[:-1]}
    vr2 = validate_response(json.dumps(bad), mp)
    assert not vr2.ok

    tr_map = {k: "中文一行" for k in mp}
    srt = build_bilingual_srt(sliced, tr_map)
    assert "中文一行" in srt
    assert sliced[0].text.split("\n")[0] in srt

    # chunking: 747 / 50 → 15 batches (14*50 + 47)
    full = reindex_cues(cues)
    chunks = chunk_cues(full, 50)
    assert len(chunks) == (len(full) + 49) // 50
    assert sum(len(c) for c in chunks) == len(full)
    assert chunks[0][0].id == "0"
    assert chunks[1][0].id == "50"
    assert chunk_cues(full, 0) == [full]
    assert sum_usage([Usage(1, 2, 0, 3), Usage(4, 5, 1, 10)]).total_tokens == 13

    print(
        f"offline self-check OK: total_cues={len(cues)} sample={len(sliced)} "
        f"batches_50={len(chunks)}"
    )
