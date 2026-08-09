from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from model_client import Usage

from pipeline.batch_client import call_one_batch
from pipeline.config import DEFAULT_BATCH_SIZE, DEFAULT_MAX_OUTPUT_TOKENS
from pipeline.logging_util import log
from pipeline.models import Cue, TranslateResult, ValidateReport
from pipeline.persist import write_outputs
from pipeline.srt_io import (
    build_bilingual_srt,
    chunk_cues,
    parse_srt,
    reindex_cues,
    sum_usage,
)
from pipeline.validate import validate_response


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
    log(f"🔧 repair_run_dir {run_dir.name} re-run batches={batch_indices}")

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
            log(f"   ✓ batch {i:02d} 离线 JSON 加固恢复 {len(vr.parsed)} keys")
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
            log(f"   skip invalid batch index {i}")
            continue
        bout = run_dir / f"batch_{i:02d}"
        oc = call_one_batch(
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
            log(f"   ✓ batch {i:02d} API 重跑成功")
            continue

        log(
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
            log(f"      · 尝试 sub_batch_size={sb_size} remaining={len(remaining)}")
            sub_chunks = chunk_cues(remaining, sb_size)
            still: list[Cue] = []
            for si, sub in enumerate(sub_chunks):
                sub_out = bout / f"sub{sb_size}_{si:02d}"
                soc = call_one_batch(
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
                    log(
                        f"      ✓ batch {i:02d} sub{sb_size}/{si:02d} "
                        f"ids={sub[0].id}..{sub[-1].id} n={len(sub)}"
                    )
                else:
                    still.extend(sub)
                    log(
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
            log(f"   ✓ batch {i:02d} 经 sub-batch 凑齐 {len(part)} keys")
        else:
            miss = sorted(
                ids - set(existing.keys()),
                key=lambda x: int(x) if x.isdigit() else x,
            )
            log(f"   ✗ batch {i:02d} sub-batch 后仍缺 {len(miss)}: {miss[:10]}")

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
        sampling=dict(meta.get("sampling") or {}),
    )
    # Preserve cumulative usage on both successful and incomplete repair passes.
    if meta.get("usage"):
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

    write_outputs(run_dir, result, json.dumps(full_input_map, ensure_ascii=False, separators=(",", ":")))
    if overall_ok:
        log(f"✅ repair 完成 → bilingual.srt keys={len(existing)}")
    else:
        log(f"❌ repair 仍缺 {len(missing)} keys: {missing[:20]}")
    return result
