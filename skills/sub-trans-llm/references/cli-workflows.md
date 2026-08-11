# CLI workflows

Use this reference after locating the Sub-trans-llm repository. Always reconcile examples with the current `README.md` and `python main.py <subcommand> --help`.

## Runtime preparation

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

The user must edit `.env`. Never inspect or echo its secret values. Model aliases and provider variable names may be read from `.env.example` and the public documentation.

Prefer invoking the selected interpreter consistently:

```bash
./.venv/bin/python main.py --help
```

## Choose the smallest valid check

| Goal | Command | Live API |
|---|---|---|
| Validate local SRT and contracts | `selfcheck` | No |
| Check model/auth/API-mode connectivity | `ping` | Yes |
| Check a small end-to-end translation | `smoke` | Yes |
| Produce one final bilingual SRT | `run` | Yes |
| Inspect Stage A only | `preprocess` | Only with `--optimize` |
| Recover a failed run workspace | `repair` | Maybe |

Examples:

```bash
./.venv/bin/python main.py selfcheck --srt "/path/to/input.srt"

./.venv/bin/python main.py ping \
  --models qwen3.7-plus \
  --APImode ChatCompletion

./.venv/bin/python main.py smoke \
  --srt "/path/to/input.srt" \
  --models qwen3.7-plus \
  --APImode ChatCompletion \
  --out "out/smoke-qwen"

./.venv/bin/python main.py run \
  --srt "/path/to/input.srt" \
  --model qwen3.7-plus
```

For `run`, keep the stable minimum of `--srt` and `--model` unless a real requirement calls for another flag. Omitted `--output` means `{source_stem}_zh.srt` next to the input. A supplied `--output` is the final SRT path, not a process directory. `run` does not accept `--out`.

Add inputs only when needed:

```bash
./.venv/bin/python main.py run \
  --srt "/path/to/input.srt" \
  --model qwen3.7-plus \
  --source-language "French" \
  --target-language "Simplified Chinese" \
  --glossary "/path/to/glossary.csv" \
  --prompt "/path/to/prompt.md" \
  --output "/path/to/output.zh.srt"
```

CSV Glossaries use `source,target,note`; Markdown tables are also accepted. The default API mode is `ChatCompletion`. Use `--APImode Responses` only when explicitly requested or when comparing that path. Do not force `temperature` or `top_p`; omission preserves the provider's effective default and the application's recorded OMIT semantics.

## Stage A

Use `preprocess` when the user wants an inspectable cleaned SRT without translation:

```bash
./.venv/bin/python main.py preprocess \
  --srt "/path/to/input.srt" \
  --remove-sdh \
  --out "out/preprocess"
```

Use `run --preprocess` when preprocessing should continue directly into translation:

```bash
./.venv/bin/python main.py run \
  --srt "/path/to/input.srt" \
  --model qwen3.7-plus \
  --preprocess \
  --remove-sdh
```

Stage A options must accompany `--preprocess` on `run`. `--optimize` makes live model calls and requires `--model`; failures are fatal rather than silently skipped. Overlap fixing and resplitting are heuristic source-subtitle operations, so report their generated `report.json` instead of claiming all subtitle warnings were eliminated.

## Repair

Use the failed workspace path printed by `run`:

```bash
./.venv/bin/python main.py repair \
  --run-dir "/path/to/out/run_xxx/model" \
  --model qwen3.7-plus \
  --srt "/path/to/input.srt" \
  --batches 2,3
```

Inspect `repair --help` and the workspace metadata first. Preserve its API mode and sampling record. Report whether repair reused local JSON or made new provider calls.

## Completion evidence

For any operation, capture:

- exact command with secrets omitted;
- exit status;
- output or retained failure-workspace path;
- whether a live API was reached;
- validation warnings or failed batches;
- remaining unverified boundaries.
