#!/usr/bin/env python3
"""
Ablation: which instruction parts actually land and how they affect translations.

Configs (Stage B only, no preprocess):
  A  prompt only
  B  prompt + glossary
  C  prompt + episode summary
  D  prompt + glossary + summary   ← historical full instructions

Usage:
  python scripts/ablation_instructions.py --model qwen3.7-max --slice open
  python scripts/ablation_instructions.py --model qwen3.7-max --slice names --run
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRT = ROOT / "sample" / "A.French.Village.S01E03_eng.srt"
GLOSSARY = ROOT / "docs" / "Un_Village_francais_Glossary.md"
PROMPT = ROOT / "docs" / "translation_prompt.md"

SLICES = {
    # opening: titles, names Schwartz, Meyer, Berthier
    "open": {"offset": 0, "max": 30, "note": "opening + sawmill names"},
    # political block + Marcel Larcher / Communists (was content-filter sensitive)
    "names": {"offset": 100, "max": 50, "note": "cops, Communists, Marcel Larcher"},
    "tiny": {"offset": 0, "max": 8, "note": "smoke size"},
}

CONFIGS = {
    "A_prompt": {"glossary": False, "summary": False},
    "B_gloss": {"glossary": True, "summary": False},
    "C_summary": {"glossary": False, "summary": True},
    "D_full": {"glossary": True, "summary": True},
}

MARKERS = {
    "prompt": "# Role: 资深字幕翻译专家",
    "glossary": "## 专有名词（必须遵守，不得另译）",
    "summary": "## 本集剧情摘要（翻译时请参考语境与人物状态，勿写入输出 JSON）",
}


def inspect_instructions(path: Path) -> dict:
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    return {
        "chars": len(text),
        "has_prompt": MARKERS["prompt"] in text,
        "has_glossary": MARKERS["glossary"] in text,
        "has_summary": MARKERS["summary"] in text,
        "path": str(path),
    }


def run_one(model: str, cfg_name: str, cfg: dict, slice_name: str, out_root: Path) -> Path:
    sl = SLICES[slice_name]
    run_dir = out_root / f"{slice_name}_{cfg_name}"
    cmd = [
        sys.executable,
        str(ROOT / "main.py"),
        "run",
        "--model",
        model,
        "--srt",
        str(SRT),
        "--cue-offset",
        str(sl["offset"]),
        "--max-cues",
        str(sl["max"]),
        "--batch-size",
        str(sl["max"]),
        "--batch-jobs",
        "1",
        "--timeout",
        "300",
        "--max-output-tokens",
        "8192",
        "--out",
        str(run_dir),
        "--output",
        str(run_dir / "deliver_zh.srt"),
    ]
    if cfg["glossary"]:
        cmd.extend(["--glossary", str(GLOSSARY)])
    if not cfg["summary"]:
        cmd.append("--no-summary")
    print("\n>>>", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=str(ROOT), check=False)
    return run_dir


def offline_expect() -> None:
    """Prove build_instructions composes three sections correctly."""
    sys.path.insert(0, str(ROOT))
    from pipeline.prompt import build_instructions

    cases = [
        ("A", None, None, (True, False, False)),
        ("B", GLOSSARY, None, (True, True, False)),
        ("C", None, "剧情摘要测试句", (True, False, True)),
        ("D", GLOSSARY, "剧情摘要测试句", (True, True, True)),
    ]
    print("=== offline build_instructions section markers ===")
    for name, g, s, exp in cases:
        text = build_instructions(prompt_path=PROMPT, glossary_path=g, episode_summary=s)
        got = (
            MARKERS["prompt"] in text,
            MARKERS["glossary"] in text,
            MARKERS["summary"] in text,
        )
        ok = got == exp
        print(f"  {name}: got={got} expect={exp} chars={len(text)} {'OK' if ok else 'FAIL'}")
        if not ok:
            raise SystemExit(1)


def summarize_run(run_dir: Path, model: str) -> dict:
    model_dir = run_dir / model.replace("/", "_")
    # also try nested
    if not model_dir.is_dir():
        cands = list(run_dir.glob("*/instructions.txt"))
        model_dir = cands[0].parent if cands else run_dir
    inst = inspect_instructions(model_dir / "instructions.txt")
    val_path = model_dir / "validate.json"
    parsed_path = model_dir / "parsed.json"
    meta_path = model_dir / "meta.json"
    val = json.loads(val_path.read_text()) if val_path.is_file() else {}
    parsed = json.loads(parsed_path.read_text()) if parsed_path.is_file() else {}
    meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
    # probe key name translations
    probes = {}
    for kid in ("0", "1", "4", "9", "18", "119", "123", "125"):
        if kid in parsed:
            probes[kid] = parsed[kid].get("tr", "")
    return {
        "run_dir": str(run_dir),
        "model_dir": str(model_dir),
        "instructions": inst,
        "validate_ok": val.get("ok"),
        "n_out": (val.get("stats") or {}).get("n_out"),
        "summary_chars": meta.get("episode_summary_chars"),
        "probes": probes,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.7-max")
    ap.add_argument("--slice", choices=list(SLICES), default="tiny")
    ap.add_argument("--run", action="store_true", help="call API for all 4 configs")
    ap.add_argument("--out", default=str(ROOT / "out" / "ablation_qwen37max"))
    ap.add_argument("--configs", default="A_prompt,B_gloss,C_summary,D_full")
    args = ap.parse_args()

    offline_expect()
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    if not args.run:
        print("offline checks OK. pass --run to execute API ablations.")
        return 0

    names = [x.strip() for x in args.configs.split(",") if x.strip()]
    reports = []
    for name in names:
        cfg = CONFIGS[name]
        run_dir = run_one(args.model, name, cfg, args.slice, out_root)
        rep = summarize_run(run_dir, args.model)
        rep["config"] = name
        rep["expect"] = cfg
        reports.append(rep)
        print(
            f"=== {name} instructions: prompt={rep['instructions']['has_prompt']} "
            f"gloss={rep['instructions']['has_glossary']} "
            f"sum={rep['instructions']['has_summary']} "
            f"chars={rep['instructions']['chars']} ok={rep['validate_ok']}"
        )
        for k, v in list(rep["probes"].items())[:6]:
            print(f"    [{k}] {v!r}")

    report_path = out_root / f"report_{args.slice}.json"
    report_path.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
