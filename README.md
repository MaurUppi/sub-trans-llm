# Sub-trans-llm

一个多语言译简中字幕工具：读取 SRT，以 JSON 协议调用 OpenAI 兼容模型，校验原文回显与条目完整性，并输出“译文在上、原文在下”的双语 SRT。源语言默认英语、目标语言默认简体中文；在模型能力范围内，可通过 `--source-language` 和 `--target-language` 显式指定其他语言。

默认使用 Chat Completions API；可通过 `--APImode Responses` 复核 Responses API 路径。当前内置火山方舟与阿里云百炼的六个模型 alias，并支持外部 prompt、CSV/Markdown Glossary、剧集摘要、分批并发、失败重试、字幕前处理、已有运行修复，以及配置驱动的匿名 TQA v2 多模型评测。

## 主要能力

- Chat Completions / Responses 两种 API 模式，共用同一套翻译、校验与落盘流程。
- `temperature` 与 `top_p` 默认 OMIT，仅在显式指定时发送。
- 默认先生成当前输入范围的摘要，再以 50 cue 为一批翻译。
- 校验 JSON、键集合、`src` 原文回显、中文行长、行数、CPS 与时间长度。
- 失败批重试；必要时按更小 sub-batch 继续定位与补采。
- 可选 Stage A 前处理，以及最终双语 SRT 交付。
- CSV Glossary 原生支持 `source,target,note` 表头，也兼容既有 Markdown 表格。
- `bench --all --profile ...` 从单一 YAML 冻结实验参数，完成收集、匿名评分、聚合与可恢复报告。

## 仓库结构

```text
main.py                    # ping/selfcheck/repair/smoke/preprocess/run/bench
model_client.py            # Chat/Responses 适配、模型配置与调用
translate.py               # pipeline 公共 API 的兼容 re-export
pipeline/                  # 翻译、校验、repair、前处理与 TQA 实现
pipeline/prompts/          # 公开运行时 prompt
pipeline/tqa/              # TQA v2 Framework、Schema、默认 Profile 与流水线
scripts/                   # 手动 API 兼容性 smoke 工具
tests/                     # 自包含 pytest 回归测试
```

本地研究文档、字幕样例、术语表、agent 指令、issue tracker 与冻结实验编排器不属于公开发行包，已由根级 `.gitignore` 排除。使用者需要提供自己的 SRT；Glossary 为可选输入。

## 环境准备

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# 编辑 .env，填写 ARK/ALI 的 base URL、API key 和 MODEL_* ID
```

`.env` 中的 `DEFAULT_TEMPERATURE` / `DEFAULT_TOP_P` 已废弃，不影响调用。`DEFAULT_MAX_OUTPUT_TOKENS` 只作为直连 `model_client.call` 的可选上限来源。

## 快速开始

`run` 的稳定 CLI 契约只有 `--srt` 与 `--model` 必填；其余参数均有默认值并可显式覆盖。`bench` 不接受行内实验参数，字幕、模型、采样臂与运行控制统一从 `--profile` 指定的 YAML 读取。

```bash
# 离线解析与校验，不调用 API
python main.py selfcheck --srt /path/to/input.srt

# 指定模型的最小连通检查；会产生真实 API 调用，但不进入字幕流程
python main.py ping --models qwen3.7-plus

# Chat Completions 小规模烟测，默认前 8 个 cue
python main.py smoke \
  --APImode ChatCompletion \
  --srt /path/to/input.srt \
  --models qwen3.7-plus \
  --out out/smoke_qwen

# 单模型生产运行
python main.py run \
  --APImode ChatCompletion \
  --srt /path/to/input.srt \
  --model qwen3.7-plus \
  --glossary /path/to/glossary.csv \
  --output /path/to/input.zh.srt

# 复制并编辑统一 TQA Profile，再完成自动流水线
cp pipeline/tqa/profile.default.yaml /path/to/my-tqa-profile.yaml
python main.py bench --all --profile /path/to/my-tqa-profile.yaml
```

`run` 不接受 `--out`。省略 `--output` 时，最终文件默认写到输入 SRT 同目录，文件名为 `{stem}_zh.srt`；显式指定时严格写入该路径。内部运行证据仍写入自动生成的 `out/run_<model>_<timestamp>/`。

## 检查与恢复命令

| 子命令 | 检查范围 | 是否调用 API |
|---|---|---|
| `ping` | 以最少 token 检查所选模型、鉴权和指定 API 模式的连通性；不读取 SRT，不进入翻译 pipeline | 是 |
| `selfcheck` | 离线检查 SRT 解析、prompt 变量、可选 Glossary 注入、JSON 校验和双语 SRT 输出契约 | 否 |
| `smoke` | 对真实 SRT 执行端到端小规模翻译，默认从前 8 个 cue 开始，覆盖摘要、prompt、API、校验与落盘 | 是 |
| `repair` | 恢复已有 run 的失败批并重新合并；优先复用已落盘 JSON，仅在仍缺结果时调用 API | 视缺失结果而定 |

`ping` 只证明 API 连通，不证明字幕翻译链路正确；`selfcheck` 只证明离线契约；需要验证完整链路时使用 `smoke`。`repair` 默认读取 `meta.json` 并复用原运行的 API 模式及显式采样值，显式传入冲突的 `--APImode` 会拒绝执行，避免把同一次运行跨 API 模式混合修复。

## 常用参数

| 参数 | 当前行为 / 默认值 |
|---|---|
| `--APImode` / `--api-mode` | `ChatCompletion`；也接受 `Responses` |
| `--srt` | 读取字幕的命令必填，用户自己的源语言 SRT 路径 |
| `--model` | `run` 必填；必须选择一个已配置的模型 alias |
| `--source-language` | 英语；可显式指定模型支持的其他源语言 |
| `--target-language` | 简体中文；可显式指定模型支持的其他目标语言 |
| `--prompt` | `pipeline/prompts/translation.md` |
| `--glossary` | 默认不注入；可传 CSV 或 Markdown 路径 |
| `--batch-size` | 50；`≤0` 表示整包一批 |
| `--batch-jobs` | 1；大于 1 时同一模型多批并行 |
| `--jobs` | 1；`smoke` 等传统多模型命令的模型并发数；bench 使用 Profile 的 `execution.model_jobs` |
| `--temperature` | `[0,2)`；默认 OMIT |
| `--top-p` | `(0,1]`；默认 OMIT |
| `--max-output-tokens` | run/smoke/repair 默认 8192；bench 由 Profile 显式冻结，默认 Profile 为 8192 |
| `--timeout` | run/repair 默认 300 秒，smoke 默认 180 秒；bench 默认 Profile 为 300 秒 |
| `--max-retries` | 2 次额外重试 |
| `--retry-backoff` | 3 秒指数退避基数 |
| `--no-summary` | 跳过默认剧集摘要 |
| `--output` | 仅 `run`；默认与输入同目录 |
| `--out` | smoke/preprocess 等目录型产物；run 不接受，bench 使用 Profile 的 `output.root` |

同时显式指定 `temperature` 和 `top_p` 时，CLI 会警告但不阻断。`OMIT` 只表示请求体未发送该字段，不代表不同 Provider 使用相同的数值默认值。

## TQA v2 benchmark

`bench` 与生产 `run` 分工明确：`run` 交付一个模型的一份字幕；`bench` 比较 Profile 中明确定义的模型与采样参数臂，并执行 TQA v2 评测。公开接口只有两种形式：

```bash
# 完整执行；最终停在 awaiting_user_decision，不替代人工确认
python main.py bench --all --profile /path/to/profile.yaml

# 分阶段执行
python main.py bench plan     --profile /path/to/profile.yaml
python main.py bench collect  --profile /path/to/profile.yaml
python main.py bench evaluate --profile /path/to/profile.yaml
python main.py bench report   --profile /path/to/profile.yaml
python main.py bench status   --profile /path/to/profile.yaml
```

`plan` 将 Profile 内相对路径按 Profile 所在目录解析，并在 `output.root` 下冻结 `profile.source.yaml`、`profile.resolved.yaml`、`profile.lock.json` 与 `manifest.json`。同一输出目录若检测到不同 Profile 哈希会拒绝复用。`collect` 按 `model_jobs` 并行模型、按 `batch_jobs` 并行单模型分批；`generate_once` 摘要会按“模型 × 集”冻结并由该模型的后续参数臂复用。

Profile 中的 `source_srt` 始终作为整份字幕进入 collect；`inputs.episodes[].samples` 只指定随后进入 TQA evaluator 评分的定向条目，不会限制翻译范围。若要逐条评分整份字幕，需要将全部 cue 列入 `samples`，相应的评分调用量和费用也会显著增加。

Profile 中的文件路径建议统一使用 YAML 双引号，例如 `"./path/to/glossary.csv"`；`prompt: null` 与 `glossary: null` 表示不提供自定义文件，不能写成字符串 `"null"`。普通用户建议保持 `reference_mode: "no_reference"`。只有拥有可靠、经过人工审核的参考字幕时才使用 `single_reference`：`anchor` 表示把参考译文视为可信标准答案并严格比较，`hint` 表示参考译文只辅助理解，合理的不同译法不应被惩罚。

`sampling.arms[].temperature/top_p` 控制候选翻译模型，是被比较的实验变量；`evaluator.temperature` 只控制匿名裁判模型评分时的随机性，不会改变任何候选译文。Evaluator 的任务是针对每个“样例 × 维度”读取匿名源文、候选译文及上下文，输出 0–10 分、硬失败类别、理由与置信度；为提高多轮评分一致性，通常使用较低的 evaluator temperature。

匿名评分输入位于 `anonymized/`，不包含模型、Provider、参数臂、原始文件名/路径或 refusal/rescue 来源；解盲映射通过私有临时文件原子写入权限为 `0600` 的 `blind_map.json`。Evaluator 输出必须满足发布的 Schema，非法输出按预算重试，重复 `evaluator_run_id` 作为程序错误立即终止。Provider refusal 固定由系统记 0 并计入分母，技术故障单独统计且不进入质量分母。若 collect 后的候选记录提供 `rescued_translations`，它会进入独立匿名评分 lane，只生成 `rescued_quality_score`，绝不覆盖 refusal 主评分或进入模型总分；默认翻译 adapter 不自行制造 rescue。

聚合路径固定为“样例 × 维度原始分 → 集内维度平均分 → 维度加权集分 → 按有效样例数加权模型分”。`sample_aggregation` 仅用于报告展示；所有 `max_*` 是包含上界；状态优先级为 `VETO > FAIL > CONDITIONAL_PASS > PASS`。通用规范和机器契约位于 `pipeline/tqa/`，用户只需维护一份 YAML Profile。

## Glossary

推荐 UTF-8 CSV，表头固定为 `source,target,note`。`note` 可以留空；UTF-8 BOM 与带引号的逗号字段均受支持。

```csv
source,target,note
Daniel Larcher,达尼埃尔·拉尔谢,人物名
Villeneuve,维勒纳夫,地名
```

最终发送给模型的完整 prompt、Glossary 与摘要会写入运行目录的 `instructions.txt`。Chat Completions 将其作为首条 `system` message，Responses 使用 `instructions` 字段。

## 前处理与修复

```bash
# 只执行 Stage A
python main.py preprocess \
  --srt /path/to/input.srt \
  --fix-overlaps \
  --remove-sdh \
  --out out/preprocess

# 前处理后翻译
python main.py run \
  --srt /path/to/input.srt \
  --model deepseek-v4-flash \
  --preprocess \
  --remove-sdh

# 修复已有运行中的失败批
python main.py repair \
  --run-dir out/run_xxx/qwen3.7-plus \
  --model qwen3.7-plus \
  --srt /path/to/input.srt \
  --batches 2,3 \
  --sub-batch-size 10
```

repair 会从 `meta.json` 自动沿用原运行的 API 模式及 `temperature` / `top_p` 发送记录；如需显式确认或覆盖采样值，可传入相应参数。`--APImode` 只能省略或传入与原运行相同的模式。批目录、合并后的 `parsed.json`、更新后的 `meta.json` 与最终 SRT 共同构成运行证据。

## Python API

```python
from pipeline import run_once, self_check_offline

self_check_offline("/path/to/input.srt")

result = run_once(
    srt_path="/path/to/input.srt",
    model="qwen3.7-plus",
    glossary_path="/path/to/glossary.csv",
    batch_size=50,
    batch_jobs=1,
    max_output_tokens=8192,
    timeout=300,
    api_mode="ChatCompletion",
    out_dir="out/demo/qwen3.7-plus",
)
print(result.ok, result.validate.stats, result.sampling)
```

`run_once` 的库级默认上限与超时仍为 `131072` 和 `1200` 秒；上例显式使用 CLI `run` 的生产默认值。

## 模型 alias

| Alias | Provider |
|---|---|
| `deepseek-v4-flash` | 火山方舟 Ark |
| `deepseek-v4-pro` | 火山方舟 Ark |
| `doubao-seed-2-1-turbo` | 火山方舟 Ark |
| `qwen3.7-plus` | 阿里云百炼 |
| `qwen3.7-max` | 阿里云百炼 |
| `qwen3.8-max` | 阿里云百炼 |

具体 model ID 从 `.env` 的 `MODEL_*` 变量读取。

## 运行产物

单模型运行目录通常包含：

```text
episode_summary.txt
instructions.txt
input.json
batches_plan.json
batch_00/
  input.json
  raw_output.txt
  parsed.json
  validate.json
parsed.json
meta.json
validate.json
bilingual.srt
```

最终双语格式：

```text
1
00:00:00,500 --> 00:00:03,340
翻译文本
SOURCE TEXT
```

## 开发检查

```bash
python main.py selfcheck --srt /path/to/input.srt
# 无 API、真实文件 I/O 的 bench 端到端 smoketest
PYTHONPATH=. pytest -q tests/test_tqa_bench.py::test_bench_all_offline_smoke_runs_end_to_end_and_resumes
PYTHONPATH=. pytest -q
git diff --check
```

`.env`、`out/`、虚拟环境、缓存与日志不会进入版本控制。

本项目源代码采用 [MIT License](LICENSE)。用户自行提供的字幕、Glossary、模型生成内容及第三方模型/API 服务不属于本许可证的授权对象，分别受其权利人与服务条款约束。
