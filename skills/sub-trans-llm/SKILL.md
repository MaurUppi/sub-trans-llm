---
name: sub-trans-llm
description: Operate and troubleshoot Sub-trans-llm, a multilingual-to-Simplified-Chinese SRT translation and TQA tool. Use when an agent needs to install or locate Sub-trans-llm; validate an SRT or model connection; translate, preprocess, smoke-test, or repair subtitles; choose Chat Completions or Responses mode; use prompts or glossaries; or run a TQA v2 benchmark through Claude Code, Codex, Grok Build, Hermes Agent, or another CLI agent.
---

# Sub-trans-llm

Operate the public Sub-trans-llm Python CLI from an existing checkout. Treat the checkout's CLI help and documentation as the current contract; this skill supplies routing, safety, and verification guidance rather than a second copy of the application.

## Locate or prepare the application

1. Look for a repository root containing `main.py`, `pipeline/`, `requirements.txt`, and `.env.example`. Prefer the current working directory or a path supplied by the user.
2. If no checkout exists and the user asked to install or run the tool, clone `https://github.com/MaurUppi/sub-trans-llm.git` into a user-approved location. Do not treat installing this skill as installing the Python application.
3. Prefer `<repo>/.venv/bin/python` when it exists. Otherwise create the virtual environment and install `requirements.txt` when installation is in scope.
4. Require the user to populate `.env` from `.env.example` for live API calls. Never read, print, log, or reproduce secret values. It is acceptable to inspect variable names in `.env.example` or rely on application errors that do not expose values.
5. Read `<repo>/README.md`. For TQA work also read `<repo>/TQA.md` and the selected YAML Profile. Before constructing a command, run `python main.py <subcommand> --help` from the repository root.

If a checkout is unavailable and cloning is not authorized, give the exact next command and stop; do not invent CLI behavior from this skill alone.

## Route the request

- Use `selfcheck` for offline SRT, prompt, Glossary, JSON, and output-contract checks.
- Use `ping` for the smallest live provider/model/API-mode connectivity check. Do not present it as proof of the translation pipeline.
- Use `smoke` for a small live end-to-end translation check that covers summary, prompt, API, validation, and output.
- Use `run` to produce one model's deliverable bilingual SRT. Only `--srt` and `--model` are required by the stable contract; add other flags only when the user requests them or the input requires them.
- Use `preprocess` to inspect Stage A output without translating. Use `run --preprocess` when Stage A should feed directly into translation.
- Use `repair` only for an existing failed run workspace. Reuse its recorded API mode and sampling metadata; do not silently mix modes.
- Use `bench` for TQA v2 multi-model or multi-arm evaluation. Do not use it as a substitute for a single production `run`.

Read [references/cli-workflows.md](references/cli-workflows.md) when preparing, executing, or troubleshooting ordinary CLI workflows. Read [references/tqa-workflow.md](references/tqa-workflow.md) only for `bench`, evaluator, Profile, or TQA questions.

## Execute safely

1. Resolve and report the input SRT, model alias, API mode, optional prompt/Glossary, and output destination before a production run. Never set the output path to the source SRT.
2. Preserve defaults unless the user has a reason to override them. Chat Completions is the default API mode; `temperature` and `top_p` are omitted unless explicitly supplied.
3. Distinguish offline work from provider-billed work. `ping`, `smoke`, `run`, LLM `--optimize`, API-backed `repair`, and TQA collection/evaluation can make paid calls. Execute them when the user explicitly requests the corresponding live operation; do not spend tokens merely to answer a documentation question.
4. For TQA, validate and freeze the Profile before expensive stages. Summarize the number of episodes, candidate model/parameter arms, samples, evaluator runs, and output root unless the user already approved that exact Profile and requested `--all`.
5. Do not modify source code, prompts, Glossaries, Profiles, or subtitles beyond the requested pipeline operation. Keep generated outputs outside tracked source paths unless the user explicitly chooses otherwise.

## Verify the outcome

- Check the process exit status and the exact output paths printed by the CLI.
- For `run`, confirm the final SRT exists and differs from the source path. The normal output is bilingual, with Simplified Chinese above the source line. If `--output` is omitted, expect `{source_stem}_zh.srt` beside the source.
- Treat warnings, partial batches, retained workspaces, refusals, and technical failures as evidence to report, not success to hide. Use `repair` only when its prerequisites exist.
- For `bench --all`, report the terminal machine state and generated report paths. `awaiting_user_decision` is an intentional successful handoff, not an automatic PASS decision.
- State separately what was checked offline, what reached a live provider, and what remains unverified.
