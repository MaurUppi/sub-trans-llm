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


def _run_one(
    model: str,
    args: argparse.Namespace,
    out_dir: Path,
) -> translate.TranslateResult:
    model_out = out_dir / model.replace("/", "_")
    return translate.run_once(
        srt_path=Path(args.srt),
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
    )


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
        f"max_out={args.max_output_tokens} out={out_dir}"
    )
    return _dispatch(models, args, out_dir)


def cmd_run(args: argparse.Namespace) -> int:
    if not args.model:
        print("run requires --model", file=sys.stderr)
        return 2
    models = [args.model]
    out_dir = Path(args.out) if args.out else _default_out(f"run_{args.model}")
    if args.max_output_tokens is None:
        args.max_output_tokens = 131072
    if args.timeout is None:
        args.timeout = 1200.0  # 全量预估 10–20min，留足余量
    return _dispatch(models, args, out_dir)


def cmd_bench(args: argparse.Namespace) -> int:
    models = _parse_models(args.models)
    out_dir = Path(args.out) if args.out else _default_out("bench")
    if args.max_output_tokens is None:
        args.max_output_tokens = 131072
    if args.timeout is None:
        args.timeout = 1200.0
    print(
        f"bench: models={models} jobs={args.jobs} "
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
            default=str(_ROOT / "docs" / "Un_Village_francais_Glossary.md"),
            help="设为空字符串可关闭 glossary",
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

    sp = sub.add_parser("ping", help="最少 token 连通六模型")
    sp.set_defaults(func=cmd_ping)

    sp = sub.add_parser("selfcheck", help="离线自检 parse/validate/srt")
    add_common(sp)
    sp.set_defaults(func=cmd_selfcheck)

    sp = sub.add_parser("smoke", help="小规模烟测（默认前 8 条）")
    add_common(sp)
    sp.add_argument(
        "--models",
        default="all",
        help="逗号分隔 alias，或 all",
    )
    sp.set_defaults(func=cmd_smoke)

    sp = sub.add_parser("run", help="单模型运行（默认可全量）")
    add_common(sp)
    sp.add_argument("--model", required=True)
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
