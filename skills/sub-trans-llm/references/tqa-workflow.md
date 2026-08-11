# TQA v2 workflow

Use this reference only for Sub-trans-llm benchmark and quality-evaluation work. Read the checkout's current `TQA.md`, `pipeline/tqa/profile.default.yaml`, and `python main.py bench --help` before execution.

## Boundary

`run` produces one model's deliverable subtitle. `bench` compares the candidate models and sampling arms frozen in one YAML Profile, then performs anonymous TQA v2 evaluation. Do not translate an ordinary single deliverable through `bench`.

The Profile is the only experiment configuration input. Do not add inline model, temperature, `top_p`, evaluator, or input overrides to `bench` commands.

## Prepare the Profile

Copy the repository template instead of editing it in place:

```bash
cp pipeline/tqa/profile.default.yaml "/path/to/experiment.profile.yaml"
```

Before live execution, validate at least:

- each episode ID and full `source_srt` path;
- optional per-episode `reference_srt`;
- candidate models and every `sampling.arms` entry;
- translation prompt, Glossary, languages, API mode, and batching limits;
- evaluator model, runs, sampling, retry limits, and context window;
- sample cue IDs, requested dimensions, and expected evaluation-call count;
- output root and whether it already contains a different frozen Profile.

`inputs.episodes[].samples` selects cues for evaluator scoring; it does not limit collection. Collection translates each complete `source_srt`.

Keep `reference_mode: "no_reference"` unless the user has a reliable human-reviewed reference. With `single_reference`, `reference_role` is required and every episode needs its own `reference_srt`. Use `anchor` only when the reference is a trusted answer to compare strictly; use `hint` when it should only aid interpretation.

Candidate `sampling.arms[].temperature/top_p` control translations under comparison. `evaluator.temperature` controls the anonymous judge and does not alter candidate translations.

## Execute

Prefer the automatic pipeline after the exact Profile is approved:

```bash
./.venv/bin/python main.py bench --all \
  --profile "/path/to/experiment.profile.yaml"
```

Use stages for inspection or recovery:

```bash
./.venv/bin/python main.py bench plan     --profile "/path/to/experiment.profile.yaml"
./.venv/bin/python main.py bench collect  --profile "/path/to/experiment.profile.yaml"
./.venv/bin/python main.py bench evaluate --profile "/path/to/experiment.profile.yaml"
./.venv/bin/python main.py bench report   --profile "/path/to/experiment.profile.yaml"
./.venv/bin/python main.py bench status   --profile "/path/to/experiment.profile.yaml"
```

Run `plan` before paid stages when the Profile is new or changed. Do not reuse an output root when its frozen Profile hash conflicts. Treat collection and evaluation as paid live operations unless the caller explicitly supplies test doubles.

## Interpret results

- Anonymous inputs must not reveal model, provider, sampling arm, original paths, or refusal/rescue provenance.
- Provider refusals remain zero in the primary lane. Rescue quality is reported separately and does not replace the refusal score.
- Technical failures are reported separately and do not enter the quality denominator.
- Aggregation is sample/dimension scores to episode dimension means, weighted episode scores, and sample-count-weighted model scores.
- Status priority is `VETO > FAIL > CONDITIONAL_PASS > PASS`.
- A completed `bench --all` intentionally ends at `awaiting_user_decision`. Present its report and evidence for human review; do not turn that state into an automatic release decision.

Report the frozen Profile and manifest paths, completion/refusal/technical-failure counts, primary and rescue metrics when present, final report paths, and any stages that did not run.
