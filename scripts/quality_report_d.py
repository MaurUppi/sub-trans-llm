#!/usr/bin/env python3
"""
Config D quality run + structured report (see docs/quality_ablation_plan.md § Report).

Defaults for this campaign:
  SRT=A.French.Village.S04E01.Le.Train_eng.srt
  model=qwen3.7-max
  glossary=on, summary=on, no preprocess
  batch-jobs=1
  out=out/quality_ablation_test
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRT = ROOT / "A.French.Village.S04E01.Le.Train_eng.srt"
GLOSSARY = ROOT / "docs" / "Un_Village_francais_Glossary.md"
PROMPT = ROOT / "docs" / "translation_prompt.md"

MARKERS = {
    "prompt": "# Role: 资深字幕翻译专家",
    "glossary": "## 专有名词（必须遵守，不得另译）",
    "summary": "## 本集剧情摘要（翻译时请参考语境与人物状态，勿写入输出 JSON）",
}

# Glossary surface forms to spot-check when present in English source
GLOSSARY_PROBES = [
    ("Marcel", "马赛尔"),
    ("Daniel", "达尼埃尔"),
    ("Gustave", "古斯塔夫"),
    ("Schwartz", "施瓦茨"),
    ("Larcher", "拉尔谢"),
    ("Kervern", "凯尔韦恩"),
    ("Villeneuve", "维勒纳夫"),
    ("Marie", "玛丽"),
]


def inspect_instructions(path: Path) -> dict:
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    return {
        "path": str(path),
        "chars": len(text),
        "has_prompt": MARKERS["prompt"] in text,
        "has_glossary": MARKERS["glossary"] in text,
        "has_summary": MARKERS["summary"] in text,
        "has_unreplaced_vars": "${sourceLanguage}" in text or "${targetLanguage}" in text,
        "sections": {
            "prompt": MARKERS["prompt"] in text,
            "glossary": MARKERS["glossary"] in text,
            "summary": MARKERS["summary"] in text,
        },
    }


def netflix_soft_stats(parsed: dict) -> dict:
    n = 0
    comma = 0
    bar = 0
    bad_ell = 0
    for v in parsed.values():
        tr = (v or {}).get("tr") or ""
        if not tr.strip():
            continue
        n += 1
        if "，" in tr or "。" in tr:
            comma += 1
        if "|" in tr or "｜" in tr:
            bar += 1
        if "..." in tr or "\u22ef" in tr:
            bad_ell += 1
    return {
        "n_tr": n,
        "with_chinese_comma_period": comma,
        "with_vertical_bar": bar,
        "with_bad_ellipsis": bad_ell,
    }


def glossary_hit_rate(parsed: dict, cues_en: dict[str, str]) -> dict:
    """For EN cues containing probe EN term, check if ZH form appears in tr."""
    details = []
    hit = 0
    total = 0
    for kid, en in cues_en.items():
        item = parsed.get(kid) or {}
        tr = item.get("tr") or ""
        for en_term, zh_term in GLOSSARY_PROBES:
            if re.search(re.escape(en_term), en, re.I):
                total += 1
                ok = zh_term in tr
                if ok:
                    hit += 1
                details.append(
                    {
                        "id": kid,
                        "en_term": en_term,
                        "expect_zh": zh_term,
                        "ok": ok,
                        "en": en[:80].replace("\n", " / "),
                        "tr": tr[:80].replace("\n", " / "),
                    }
                )
                break  # one probe per cue
    return {
        "matched_cues": total,
        "hits": hit,
        "rate": (hit / total) if total else None,
        "samples": details[:40],
        "misses": [d for d in details if not d["ok"]][:20],
    }


def build_report(run_dir: Path, model: str, srt_path: Path, cfg: dict) -> dict:
    model_dir = run_dir / model.replace("/", "_")
    if not (model_dir / "instructions.txt").is_file():
        cands = list(run_dir.rglob("instructions.txt"))
        model_dir = cands[0].parent if cands else model_dir

    inst = inspect_instructions(model_dir / "instructions.txt")
    val = {}
    vp = model_dir / "validate.json"
    if vp.is_file():
        val = json.loads(vp.read_text(encoding="utf-8"))
    meta = {}
    mp = model_dir / "meta.json"
    if mp.is_file():
        meta = json.loads(mp.read_text(encoding="utf-8"))
    parsed = {}
    pp = model_dir / "parsed.json"
    if pp.is_file():
        parsed = json.loads(pp.read_text(encoding="utf-8"))

    # English map from input.json if present
    cues_en: dict[str, str] = {}
    ip = model_dir / "input.json"
    if ip.is_file():
        cues_en = json.loads(ip.read_text(encoding="utf-8"))

    batch_dirs = sorted(model_dir.glob("batch_*"))
    batch_ok = 0
    for bd in batch_dirs:
        vj = bd / "validate.json"
        if vj.is_file() and json.loads(vj.read_text()).get("ok"):
            batch_ok += 1

    summary_path = model_dir / "episode_summary.txt"
    summary_text = summary_path.read_text(encoding="utf-8").strip() if summary_path.is_file() else ""

    deliver = list(run_dir.glob("*_zh.srt")) + list(run_dir.glob("deliver*.srt"))
    bi = model_dir / "bilingual.srt"

    report = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "campaign": {
            "name": "quality_ablation_test",
            "config_id": "D_full",
            "description": "prompt + glossary + episode summary; no preprocess",
        },
        "run": {
            "model": model,
            "srt": str(srt_path),
            "srt_stem": srt_path.stem,
            "out_dir": str(run_dir),
            "model_dir": str(model_dir),
            "batch_jobs": cfg.get("batch_jobs", 1),
            "batch_size": cfg.get("batch_size"),
            "max_cues": cfg.get("max_cues"),
            "cue_offset": cfg.get("cue_offset", 0),
            "preprocess": False,
            "glossary_path": str(GLOSSARY) if cfg.get("glossary") else None,
            "summary_enabled": cfg.get("summary", True),
            "temperature": cfg.get("temperature"),
            "top_p": cfg.get("top_p"),
            "sampling_note": cfg.get(
                "sampling_note",
                "None → .env DEFAULT_* (fallback 1.0); 'omit' → not sent to API",
            ),
        },
        "stage_A": {
            "enabled": False,
            "note": "config D quality run does not enable --preprocess",
        },
        "stage_B": {
            "instructions": inst,
            "instructions_expect": {
                "has_prompt": True,
                "has_glossary": True,
                "has_summary": True,
                "has_unreplaced_vars": False,
            },
            "instructions_ok": (
                inst["has_prompt"]
                and inst["has_glossary"]
                and inst["has_summary"]
                and not inst["has_unreplaced_vars"]
            ),
            "episode_summary": {
                "file_exists": summary_path.is_file(),
                "chars": len(summary_text),
                "preview": summary_text[:240].replace("\n", " "),
            },
            "meta": {
                "ok": meta.get("ok"),
                "status": meta.get("status"),
                "batch_count": meta.get("batch_count"),
                "batch_size": meta.get("batch_size"),
                "batch_jobs": meta.get("batch_jobs"),
                "episode_summary_chars": meta.get("episode_summary_chars"),
                "usage": meta.get("usage"),
                "elapsed_sec": meta.get("elapsed_sec"),
            },
            "validate": {
                "ok": val.get("ok"),
                "stats": val.get("stats"),
                "errors": (val.get("errors") or [])[:10],
                "warnings_count": len(val.get("warnings") or []),
                "warnings_sample": (val.get("warnings") or [])[:15],
            },
            "batches": {
                "dirs": len(batch_dirs),
                "ok": batch_ok,
            },
            "netflix_soft": netflix_soft_stats(parsed),
            "glossary_probes": glossary_hit_rate(parsed, cues_en),
        },
        "artifacts": {
            "instructions": str(model_dir / "instructions.txt"),
            "episode_summary": str(summary_path) if summary_path.is_file() else None,
            "parsed": str(pp) if pp.is_file() else None,
            "validate": str(vp) if vp.is_file() else None,
            "meta": str(mp) if mp.is_file() else None,
            "bilingual_work": str(bi) if bi.is_file() else None,
            "deliver_zh": [str(p) for p in deliver],
        },
        "verdict": {},
    }

    sb = report["stage_B"]
    verdict_ok = bool(
        sb["instructions_ok"]
        and sb["validate"].get("ok")
        and (sb["validate"].get("stats") or {}).get("n_out")
        == (sb["validate"].get("stats") or {}).get("n_in")
    )
    report["verdict"] = {
        "pipeline_stable": verdict_ok,
        "instructions_three_parts": sb["instructions_ok"],
        "coverage_complete": bool(sb["validate"].get("ok")),
        "notes": [],
    }
    if not sb["instructions"]["has_glossary"]:
        report["verdict"]["notes"].append("Glossary section missing — not config D")
    if not sb["instructions"]["has_summary"]:
        report["verdict"]["notes"].append("Summary section missing")
    g = sb["glossary_probes"]
    if g.get("rate") is not None and g["matched_cues"] >= 3 and g["rate"] < 0.5:
        report["verdict"]["notes"].append(
            f"Glossary surface hit-rate low: {g['rate']:.0%} ({g['hits']}/{g['matched_cues']})"
        )
    return report


def render_markdown(report: dict) -> str:
    r = report
    sb = r["stage_B"]
    ins = sb["instructions"]
    lines = [
        f"# Quality report — {r['campaign']['config_id']}",
        "",
        f"- generated_at: `{r['generated_at']}`",
        f"- model: `{r['run']['model']}`",
        f"- srt: `{r['run']['srt']}`",
        f"- out: `{r['run']['out_dir']}`",
        f"- batch_jobs: `{r['run']['batch_jobs']}` batch_size: `{r['run']['batch_size']}`",
        f"- temperature: `{r['run'].get('temperature')}` top_p: `{r['run'].get('top_p')}`",
        f"- sampling_note: {r['run'].get('sampling_note') or ''}",
        "",
        "## Verdict",
        "",
        f"- pipeline_stable: **{r['verdict']['pipeline_stable']}**",
        f"- instructions_three_parts: **{r['verdict']['instructions_three_parts']}**",
        f"- coverage_complete: **{r['verdict']['coverage_complete']}**",
    ]
    for n in r["verdict"].get("notes") or []:
        lines.append(f"- note: {n}")
    lines += [
        "",
        "## Stage B — instructions (三部分)",
        "",
        f"| part | present | expect |",
        f"|------|:-------:|:------:|",
        f"| prompt | {ins['has_prompt']} | True |",
        f"| glossary | {ins['has_glossary']} | True |",
        f"| summary | {ins['has_summary']} | True |",
        f"| unreplaced vars | {ins['has_unreplaced_vars']} | False |",
        f"",
        f"- instructions.chars: {ins['chars']}",
        f"- summary.chars: {sb['episode_summary']['chars']}",
        f"- summary.preview: {sb['episode_summary']['preview'][:200]}",
        "",
        "## Coverage / validate",
        "",
        f"- validate.ok: {sb['validate'].get('ok')}",
        f"- stats: `{json.dumps(sb['validate'].get('stats'), ensure_ascii=False)}`",
        f"- batches dirs/ok: {sb['batches']['dirs']}/{sb['batches']['ok']}",
        f"- usage: `{json.dumps(sb['meta'].get('usage'), ensure_ascii=False)}`",
        f"- elapsed_sec: {sb['meta'].get('elapsed_sec')}",
        "",
        "## Netflix soft (tr)",
        "",
        f"`{json.dumps(sb['netflix_soft'], ensure_ascii=False)}`",
        "",
        "## Glossary probes",
        "",
        f"- matched_cues: {sb['glossary_probes'].get('matched_cues')}",
        f"- hits: {sb['glossary_probes'].get('hits')}",
        f"- rate: {sb['glossary_probes'].get('rate')}",
        "",
    ]
    misses = sb["glossary_probes"].get("misses") or []
    if misses:
        lines.append("### Miss samples")
        for m in misses[:10]:
            lines.append(
                f"- id={m['id']} expect `{m['expect_zh']}` for `{m['en_term']}`: {m['tr']!r}"
            )
        lines.append("")
    lines += [
        "## Artifacts",
        "",
    ]
    for k, v in r["artifacts"].items():
        lines.append(f"- {k}: `{v}`")
    lines.append("")
    return "\n".join(lines)


def run_d(args: argparse.Namespace) -> Path:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    run_dir = out / "D_full"
    srt = Path(args.srt)
    cmd = [
        sys.executable,
        str(ROOT / "main.py"),
        "run",
        "--model",
        args.model,
        "--srt",
        str(srt),
        "--glossary",
        str(GLOSSARY),
        "--batch-jobs",
        str(args.batch_jobs),
        "--batch-size",
        str(args.batch_size),
        "--timeout",
        str(args.timeout),
        "--out",
        str(run_dir),
        "--output",
        str(out / f"{srt.stem}_zh.srt"),
    ]
    if args.max_cues is not None:
        cmd.extend(["--max-cues", str(args.max_cues)])
    if args.cue_offset:
        cmd.extend(["--cue-offset", str(args.cue_offset)])
    if args.max_output_tokens:
        cmd.extend(["--max-output-tokens", str(args.max_output_tokens)])
    if getattr(args, "no_temp", False):
        cmd.append("--no-temp")
    elif getattr(args, "temperature", None) is not None:
        cmd.extend(["--temperature", str(args.temperature)])
    if getattr(args, "no_top_p", False):
        cmd.append("--no-top-p")
    elif getattr(args, "top_p", None) is not None:
        cmd.extend(["--top-p", str(args.top_p)])
    print(">>>", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=str(ROOT), check=False)
    return run_dir


def _sampling_cfg(args: argparse.Namespace) -> dict:
    """Serialize sampling mode for the report (omit | float | None=env default)."""
    if getattr(args, "no_temp", False):
        temp: object = "omit"
    else:
        temp = getattr(args, "temperature", None)
    if getattr(args, "no_top_p", False):
        top_p: object = "omit"
    else:
        top_p = getattr(args, "top_p", None)
    return {
        "temperature": temp,
        "top_p": top_p,
        "sampling_note": (
            "None → .env DEFAULT_* then fallback 1.0; "
            "'omit' (--no-temp/--no-top-p or env omit) → field not sent to API"
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Config D quality run + report. Sampling: --temperature/--top-p "
        "or --no-temp/--no-top-p (omit from API)."
    )
    ap.add_argument("--srt", default=str(DEFAULT_SRT))
    ap.add_argument("--model", default="qwen3.7-max")
    ap.add_argument("--out", default=str(ROOT / "out" / "quality_ablation_test"))
    ap.add_argument("--batch-jobs", type=int, default=1)
    ap.add_argument("--batch-size", type=int, default=30)
    ap.add_argument("--max-cues", type=int, default=None)
    ap.add_argument("--cue-offset", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=1200)
    ap.add_argument("--max-output-tokens", type=int, default=None)
    tg = ap.add_mutually_exclusive_group()
    tg.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="sampling temperature (default: .env DEFAULT_TEMPERATURE)",
    )
    tg.add_argument(
        "--no-temp",
        action="store_true",
        help="omit temperature from API request (provider default)",
    )
    pg = ap.add_mutually_exclusive_group()
    pg.add_argument(
        "--top-p",
        type=float,
        default=None,
        dest="top_p",
        help="nucleus top_p (default: .env DEFAULT_TOP_P)",
    )
    pg.add_argument(
        "--no-top-p",
        action="store_true",
        dest="no_top_p",
        help="omit top_p from API request (provider default)",
    )
    ap.add_argument("--run", action="store_true", help="execute API run (config D)")
    ap.add_argument(
        "--report-only",
        action="store_true",
        help="only build report from existing out/D_full",
    )
    args = ap.parse_args()
    srt = Path(args.srt)
    out = Path(args.out)
    run_dir = out / "D_full"

    if args.run and not args.report_only:
        run_d(args)

    if not (run_dir / args.model.replace("/", "_")).exists() and not list(
        run_dir.rglob("validate.json")
    ):
        print(f"No run artifacts under {run_dir}; pass --run", file=sys.stderr)
        return 2

    cfg = {
        "batch_jobs": args.batch_jobs,
        "batch_size": args.batch_size,
        "max_cues": args.max_cues,
        "cue_offset": args.cue_offset,
        "glossary": True,
        "summary": True,
        **_sampling_cfg(args),
    }
    report = build_report(run_dir, args.model, srt, cfg)
    out.mkdir(parents=True, exist_ok=True)
    jp = out / "report_D_full.json"
    mp = out / "report_D_full.md"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(render_markdown(report), encoding="utf-8")
    print(f"Wrote {jp}")
    print(f"Wrote {mp}")
    print("verdict:", report["verdict"])
    return 0 if report["verdict"]["pipeline_stable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
