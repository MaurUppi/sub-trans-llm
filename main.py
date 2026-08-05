#!/usr/bin/env python3
"""
六模型字幕翻译 Benchmark 调度入口。

  python main.py ping
  python main.py smoke --srt ... --max-cues 8 --models all
  python main.py run   --srt ... --model deepseek-v4-flash
  python main.py bench --srt ... --jobs 1
  python main.py selfcheck --srt ...
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

import model_client
import translate

_ROOT = Path(__file__).resolve().parent
DEFAULT_SRT = _ROOT / "A.French.Village.S01E03.Passer.la.ligne_eng.srt"


def _parse_models(spec: str) -> list[str]:
    spec = (spec or "all").strip()
    if spec.lower() in ("all", "*"):
        return model_client.list_models()
    return [m.strip() for m in spec.split(",") if m.strip()]


def _default_out(prefix: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return _ROOT / "out" / f"{prefix}_{ts}"


def _write_summary(out_dir: Path, results: list[translate.TranslateResult]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for r in results:
        rows.append(
            {
                "model_alias": r.model_alias,
                "model_id": r.model_id,
                "ok": r.ok,
                "status": r.status,
                "incomplete_reason": r.incomplete_reason,
                "errors": r.validate.errors,
                "warnings": r.validate.warnings,
                "n_errors": len(r.validate.errors),
                "n_warnings": len(r.validate.warnings),
                "input_tokens": r.usage.input_tokens,
                "output_tokens": r.usage.output_tokens,
                "reasoning_tokens": r.usage.reasoning_tokens,
                "total_tokens": r.usage.total_tokens,
                "elapsed_sec": round(r.elapsed_sec, 3),
            }
        )
    (out_dir / "summary.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "| model | ok | errors | warnings | in | out | reason | sec |",
        "|---|:---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['model_alias']} | {'OK' if row['ok'] else 'FAIL'} | "
            f"{row['n_errors']} | {row['n_warnings']} | "
            f"{row['input_tokens']} | {row['output_tokens']} | "
            f"{row['reasoning_tokens']} | {row['elapsed_sec']} |"
        )
    fail_detail = []
    for row in rows:
        if not row["ok"]:
            fail_detail.append(
                f"### {row['model_alias']}\n"
                f"- status: `{row['status']}`\n"
                f"- errors: {row['errors']}\n"
            )
    md = "# Benchmark summary\n\n" + "\n".join(lines) + "\n"
    if fail_detail:
        md += "\n## Failures\n\n" + "\n".join(fail_detail)
    (out_dir / "summary.md").write_text(md, encoding="utf-8")
    print(md)


def cmd_ping(_: argparse.Namespace) -> int:
    rows = model_client.smoke_test()
    ok_n = sum(1 for r in rows if r.ok)
    for r in rows:
        mark = "OK" if r.ok else "FAIL"
        print(
            f"[{mark}] {r.alias:24s} {r.status} text={r.text!r} "
            f"tokens={r.usage.total_tokens}"
        )
    print(f"\n通过 {ok_n}/{len(rows)}")
    return 0 if ok_n == len(rows) else 1


def cmd_selfcheck(args: argparse.Namespace) -> int:
    srt = Path(args.srt or DEFAULT_SRT)
    translate.self_check_offline(srt)
    return 0


def cmd_repair(args: argparse.Namespace) -> int:
    """对已有 run 目录重跑失败批并合并。"""
    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        # allow parent that contains model subdir
        if (run_dir / args.model).is_dir():
            run_dir = run_dir / args.model
        else:
            print(f"run dir not found: {run_dir}", file=sys.stderr)
            return 2
    indices = None
    if args.batches:
        indices = [int(x) for x in args.batches.split(",") if x.strip() != ""]
    r = translate.repair_run_dir(
        run_dir=run_dir,
        srt_path=Path(args.srt),
        model=args.model,
        batch_indices=indices,
        max_output_tokens=args.max_output_tokens or 8192,
        timeout=args.timeout or 300.0,
        max_retries=args.max_retries,
        retry_backoff_sec=args.retry_backoff,
        sub_batch_size=getattr(args, "sub_batch_size", 10),
        temperature=_sampling_from_args(args)[0],  # type: ignore[arg-type]
        top_p=_sampling_from_args(args)[1],  # type: ignore[arg-type]
    )
    print(
        f"{'OK' if r.ok else 'FAIL'} repair {run_dir} "
        f"merged={r.validate.stats.get('n_out')}/{r.validate.stats.get('n_in')} "
        f"err={r.validate.errors[:2]}"
    )
    return 0 if r.ok else 1


def _maybe_preprocess(args: argparse.Namespace) -> Path:
    """If --preprocess, run Stage A and return clean SRT path; else original."""
    srt = Path(args.srt)
    if not getattr(args, "preprocess", False):
        return srt
    from pipeline.preprocess.config import PreprocessConfig
    from pipeline.preprocess.orchestrate_a import run_preprocess

    fix = "auto"
    if getattr(args, "fix_overlaps", False):
        fix = "on"
    if getattr(args, "no_fix_overlaps", False):
        fix = "off"
    resplit = "auto"
    if getattr(args, "resplit", False):
        resplit = "on"
    if getattr(args, "no_resplit", False):
        resplit = "off"
    work = Path(args.out) / "_preprocess" if args.out else None
    cfg = PreprocessConfig(
        fix_overlaps=fix,  # type: ignore[arg-type]
        remove_sdh=bool(getattr(args, "remove_sdh", False)),
        remove_disfluency=bool(getattr(args, "remove_disfluency", False)),
        optimize=bool(getattr(args, "optimize", False)),
        resplit=resplit,  # type: ignore[arg-type]
        words_path=Path(args.words) if getattr(args, "words", None) else None,
        model=getattr(args, "model", None) or getattr(args, "models", None),
        work_dir=work,
    )
    # single-model string if models is list-like string
    if isinstance(cfg.model, str) and "," in cfg.model:
        cfg.model = cfg.model.split(",")[0].strip()
    pr = run_preprocess(srt, cfg)
    assert pr.clean_srt_path is not None
    print(f"preprocess: clean → {pr.clean_srt_path}")
    return pr.clean_srt_path


def _sampling_from_args(args: argparse.Namespace) -> tuple[object, object]:
    """Map CLI --temperature/--no-temp (and top_p) to model_client args.

    ``None`` → resolve from .env / fallback 1.0.
    ``OMIT`` → never send the field (provider default).
    """
    temp: object = model_client.OMIT if getattr(args, "no_temp", False) else getattr(
        args, "temperature", None
    )
    top_p: object = model_client.OMIT if getattr(args, "no_top_p", False) else getattr(
        args, "top_p", None
    )
    return temp, top_p


def _fmt_sampling(args: argparse.Namespace) -> str:
    t, p = _sampling_from_args(args)
    def one(v: object) -> str:
        if v is model_client.OMIT:
            return "OMIT"
        return str(v)
    return f"temp={one(t)} top_p={one(p)}"


def _run_one(
    model: str,
    args: argparse.Namespace,
    out_dir: Path,
) -> translate.TranslateResult:
    model_out = out_dir / model.replace("/", "_")
    srt_path = Path(getattr(args, "_resolved_srt", None) or args.srt)
    temperature, top_p = _sampling_from_args(args)
    return translate.run_once(
        srt_path=srt_path,
        model=model,
        source_language=args.source_language,
        target_language=args.target_language,
        prompt_path=Path(args.prompt),
        glossary_path=Path(args.glossary) if args.glossary else None,
        max_cues=args.max_cues,
        cue_offset=args.cue_offset,
        max_output_tokens=args.max_output_tokens,
        out_dir=model_out,
        timeout=args.timeout,
        max_retries=getattr(args, "max_retries", 2),
        retry_backoff_sec=getattr(args, "retry_backoff", 3.0),
        batch_size=getattr(args, "batch_size", 50),
        batch_jobs=getattr(args, "batch_jobs", 1),
        use_episode_summary=not getattr(args, "no_summary", False),
        temperature=temperature,  # type: ignore[arg-type]
        top_p=top_p,  # type: ignore[arg-type]
    )


def _deliver_zh(args: argparse.Namespace, results: list[translate.TranslateResult]) -> None:
    """Write {stem}_zh.srt next to source (or --output) for successful runs."""
    from pipeline.preprocess.deliver import write_zh_srt

    source = Path(getattr(args, "srt", "") or "")
    # prefer original user srt for naming, not clean path
    name_src = Path(getattr(args, "_original_srt", None) or source)
    out_override = getattr(args, "output", None)
    for r in results:
        if not r.ok:
            continue
        # multi-model: suffix with model alias
        if out_override and len(results) == 1:
            path = write_zh_srt(r, name_src, output=out_override)
        elif len(results) == 1:
            path = write_zh_srt(r, name_src)
        else:
            stem = name_src.stem
            path = name_src.with_name(f"{stem}_{r.model_alias}_zh.srt")
            path = write_zh_srt(r, name_src, output=path)
        if path:
            print(f"交付: {path}")


def cmd_smoke(args: argparse.Namespace) -> int:
    models = _parse_models(args.models)
    out_dir = Path(args.out) if args.out else _default_out("smoke")
    if args.max_cues is None:
        args.max_cues = 8
    if args.max_output_tokens is None:
        args.max_output_tokens = 8192
    if args.timeout is None:
        args.timeout = 180.0
    print(
        f"smoke: models={models} max_cues={args.max_cues} "
        f"batch_size={args.batch_size} batch_jobs={args.batch_jobs} "
        f"max_out={args.max_output_tokens} out={out_dir}"
    )
    return _dispatch(models, args, out_dir)


def cmd_preprocess(args: argparse.Namespace) -> int:
    """Stage A only."""
    from pipeline.preprocess.config import PreprocessConfig
    from pipeline.preprocess.orchestrate_a import run_preprocess

    fix = "auto"
    if getattr(args, "fix_overlaps", False):
        fix = "on"
    if getattr(args, "no_fix_overlaps", False):
        fix = "off"
    resplit = "auto"
    if getattr(args, "resplit", False):
        resplit = "on"
    if getattr(args, "no_resplit", False):
        resplit = "off"
    work = Path(args.out) if args.out else None
    cfg = PreprocessConfig(
        fix_overlaps=fix,  # type: ignore[arg-type]
        remove_sdh=bool(args.remove_sdh),
        remove_disfluency=bool(args.remove_disfluency),
        optimize=bool(args.optimize),
        resplit=resplit,  # type: ignore[arg-type]
        words_path=Path(args.words) if args.words else None,
        model=args.model,
        work_dir=work,
    )
    pr = run_preprocess(args.srt, cfg)
    print(
        f"OK preprocess {pr.meta['counts'].get('in')}→{pr.meta['counts'].get('out')} "
        f"clean={pr.clean_srt_path}"
    )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    if not args.model:
        print("run requires --model", file=sys.stderr)
        return 2
    models = [args.model]
    out_dir = Path(args.out) if args.out else _default_out(f"run_{args.model}")
    if args.max_output_tokens is None:
        args.max_output_tokens = 131072
    if args.timeout is None:
        # 分批后单批更快；总墙钟仍可能较长，默认 1200
        args.timeout = 1200.0
    # 全量默认 50/批；可用 --batch-jobs 并行送批
    if getattr(args, "batch_size", None) is None:
        args.batch_size = 50
    args._original_srt = Path(args.srt)
    args._resolved_srt = _maybe_preprocess(args)
    args.srt = str(args._resolved_srt)
    print(
        f"run: model={args.model} batch_size={args.batch_size} "
        f"batch_jobs={args.batch_jobs} "
        f"{_fmt_sampling(args)} "
        f"preprocess={bool(getattr(args, 'preprocess', False))} out={out_dir}"
    )
    code = _dispatch(models, args, out_dir)
    # re-load results for deliver? _dispatch doesn't return results.
    # Deliver from model out dir bilingual if present.
    from pipeline.preprocess.deliver import default_zh_path, write_zh_srt
    from model_client import Usage
    from pipeline.models import TranslateResult, ValidateReport

    model_out = out_dir / args.model.replace("/", "_")
    bi = model_out / "bilingual.srt"
    if bi.is_file():
        r = TranslateResult(
            model_alias=args.model,
            model_id="",
            usage=Usage(),
            status="completed",
            incomplete_reason=None,
            validate=ValidateReport(ok=True),
            bilingual_srt=bi.read_text(encoding="utf-8"),
            raw_text="",
            elapsed_sec=0.0,
        )
        path = write_zh_srt(
            r,
            getattr(args, "_original_srt", Path(args.srt)),
            output=getattr(args, "output", None),
        )
        if path:
            print(f"交付: {path}")
    return code


def cmd_bench(args: argparse.Namespace) -> int:
    models = _parse_models(args.models)
    out_dir = Path(args.out) if args.out else _default_out("bench")
    if args.max_output_tokens is None:
        args.max_output_tokens = 131072
    if args.timeout is None:
        args.timeout = 1200.0
    if getattr(args, "batch_size", None) is None:
        args.batch_size = 50
    print(
        f"bench: models={models} model_jobs={args.jobs} "
        f"batch_size={args.batch_size} batch_jobs={args.batch_jobs} "
        f"max_cues={args.max_cues} out={out_dir}"
    )
    return _dispatch(models, args, out_dir)


def _dispatch(
    models: list[str],
    args: argparse.Namespace,
    out_dir: Path,
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[translate.TranslateResult] = []
    jobs = max(1, int(args.jobs or 1))

    if jobs == 1:
        for m in models:
            print(f"\n=== {m} ===")
            r = _run_one(m, args, out_dir)
            results.append(r)
            print(
                f"{'OK' if r.ok else 'FAIL'} status={r.status} "
                f"err={r.validate.errors[:3]} "
                f"warn={len(r.validate.warnings)} "
                f"tokens={r.usage.total_tokens} sec={r.elapsed_sec:.1f}"
            )
    else:
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            futs = {ex.submit(_run_one, m, args, out_dir): m for m in models}
            for fut in as_completed(futs):
                m = futs[fut]
                try:
                    r = fut.result()
                except Exception as e:  # noqa: BLE001
                    print(f"FAIL {m}: {e}")
                    r = translate.TranslateResult(
                        model_alias=m,
                        model_id="",
                        usage=model_client.Usage(),
                        status=f"error: {e}",
                        incomplete_reason=None,
                        validate=translate.ValidateReport(
                            ok=False, errors=[str(e)]
                        ),
                        bilingual_srt=None,
                        raw_text="",
                        elapsed_sec=0.0,
                    )
                results.append(r)
                print(
                    f"{'OK' if r.ok else 'FAIL'} {r.model_alias} "
                    f"tokens={r.usage.total_tokens} sec={r.elapsed_sec:.1f}"
                )

    # stable order by model list
    order = {m: i for i, m in enumerate(models)}
    results.sort(key=lambda r: order.get(r.model_alias, 999))
    _write_summary(out_dir, results)
    ok_n = sum(1 for r in results if r.ok)
    print(f"\n结果: {ok_n}/{len(results)} OK → {out_dir}")
    return 0 if ok_n == len(results) else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="六模型字幕翻译 benchmark")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp: argparse.ArgumentParser, *, need_srt: bool = True) -> None:
        if need_srt:
            sp.add_argument(
                "--srt",
                default=str(DEFAULT_SRT),
                help="英文字幕 SRT 路径",
            )
        sp.add_argument(
            "--source-language",
            default="英语",
            help="替换 ${sourceLanguage}",
        )
        sp.add_argument(
            "--target-language",
            default="简体中文",
            help="替换 ${targetLanguage}",
        )
        sp.add_argument(
            "--prompt",
            default=str(_ROOT / "docs" / "translation_prompt.md"),
        )
        sp.add_argument(
            "--glossary",
            default=None,
            help="可选术语表路径；默认不注入 Glossary",
        )
        sp.add_argument("--max-cues", type=int, default=None)
        sp.add_argument("--cue-offset", type=int, default=0)
        sp.add_argument("--max-output-tokens", type=int, default=None)
        sp.add_argument("--timeout", type=float, default=None)
        sp.add_argument("--out", default=None, help="输出目录")
        sp.add_argument(
            "--jobs",
            type=int,
            default=1,
            help="并发模型数（默认 1）",
        )
        sp.add_argument(
            "--max-retries",
            type=int,
            default=2,
            help="失败后额外重试次数（总尝试=1+max_retries）",
        )
        sp.add_argument(
            "--retry-backoff",
            type=float,
            default=3.0,
            help="重试基础退避秒数（指数：3,6,12…）",
        )
        sp.add_argument(
            "--batch-size",
            type=int,
            default=50,
            help="每批字幕条数（默认 50；<=0 表示单批整包）",
        )
        sp.add_argument(
            "--batch-jobs",
            type=int,
            default=1,
            help="批并行度：1=顺序送批；>1 多批并行请求后本地拼装",
        )
        sp.add_argument(
            "--no-summary",
            action="store_true",
            help="跳过通读摘要（默认开启：全量字幕→摘要→注入各批 instructions）",
        )
        tg = sp.add_mutually_exclusive_group()
        tg.add_argument(
            "--temperature",
            type=float,
            default=None,
            help="采样温度（默认读 .env DEFAULT_TEMPERATURE；缺省 1.0）",
        )
        tg.add_argument(
            "--no-temp",
            action="store_true",
            help="不向 API 传 temperature（服务端默认；与 --temperature 互斥）",
        )
        pg = sp.add_mutually_exclusive_group()
        pg.add_argument(
            "--top-p",
            type=float,
            default=None,
            dest="top_p",
            help="nucleus top_p（默认读 .env DEFAULT_TOP_P；缺省 1.0）",
        )
        pg.add_argument(
            "--no-top-p",
            action="store_true",
            dest="no_top_p",
            help="不向 API 传 top_p（服务端默认；与 --top-p 互斥）",
        )

    sp = sub.add_parser("ping", help="最少 token 连通六模型")
    sp.set_defaults(func=cmd_ping)

    sp = sub.add_parser("selfcheck", help="离线自检 parse/validate/srt")
    add_common(sp)
    sp.set_defaults(func=cmd_selfcheck)

    sp = sub.add_parser(
        "repair",
        help="重跑已有 run 目录中的失败批并合并（可先离线 JSON 加固）",
    )
    add_common(sp)
    sp.add_argument(
        "--run-dir",
        required=True,
        help="模型输出目录（含 input.json/batch_XX），或含模型子目录的父目录",
    )
    sp.add_argument(
        "--model",
        required=True,
        help="模型 alias（用于 API 重跑；离线恢复时也需提供）",
    )
    sp.add_argument(
        "--batches",
        default=None,
        help="逗号分隔批号，如 2,4；默认自动从 meta 失败批/缺键推断",
    )
    sp.add_argument(
        "--sub-batch-size",
        type=int,
        default=10,
        help="整批失败时拆成更小块重试（默认 10，利于绕过内容审核）",
    )
    sp.set_defaults(func=cmd_repair)

    sp = sub.add_parser("smoke", help="小规模烟测（默认前 8 条）")
    add_common(sp)
    sp.add_argument(
        "--models",
        default="all",
        help="逗号分隔 alias，或 all",
    )
    sp.set_defaults(func=cmd_smoke)

    def add_preprocess_flags(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--fix-overlaps",
            action="store_true",
            help="强制启用时间轴去重叠（默认 auto：检测到重叠才修）",
        )
        sp.add_argument(
            "--no-fix-overlaps",
            action="store_true",
            help="禁用时间轴去重叠",
        )
        sp.add_argument("--remove-sdh", action="store_true", help="移除 SDH 标记/纯 SDH 块")
        sp.add_argument(
            "--remove-disfluency",
            action="store_true",
            help="移除口癖/重复（改原文）",
        )
        sp.add_argument("--optimize", action="store_true", help="LLM 源文优化（条数不变）")
        sp.add_argument("--resplit", action="store_true", help="强制重切过长字幕")
        sp.add_argument("--no-resplit", action="store_true", help="禁止重切")
        sp.add_argument(
            "--words",
            default=None,
            help="词级时间戳 JSON（启用 VideoCaptioner Split 时）",
        )

    sp = sub.add_parser("preprocess", help="Stage A：字幕前处理（不翻译）")
    add_common(sp)
    add_preprocess_flags(sp)
    sp.add_argument(
        "--model",
        default=None,
        help="optimize/resplit-LLM 时使用的模型 alias",
    )
    sp.set_defaults(func=cmd_preprocess)

    sp = sub.add_parser("run", help="单模型运行（默认可全量）")
    add_common(sp)
    add_preprocess_flags(sp)
    sp.add_argument("--model", required=True)
    sp.add_argument(
        "--preprocess",
        action="store_true",
        help="联动：先 Stage A 前处理再翻译",
    )
    sp.add_argument(
        "--output",
        default=None,
        help="最终双语 SRT 路径（默认：与源同目录 {stem}_zh.srt）",
    )
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("bench", help="多模型 benchmark")
    add_common(sp)
    sp.add_argument("--models", default="all")
    sp.set_defaults(func=cmd_bench)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # empty glossary string → None
    if hasattr(args, "glossary") and args.glossary == "":
        args.glossary = None
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
