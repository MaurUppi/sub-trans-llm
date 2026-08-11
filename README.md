# Sub-trans-llm

一个多语言译简中字幕工具：读取 SRT，以 JSON 协议调用 OpenAI 兼容模型，校验原文回显与条目完整性，并输出“译文在上、原文在下”的双语 SRT。源语言默认英语、目标语言默认简体中文；在模型能力范围内，可通过 `--source-language` 和 `--target-language` 显式指定其他语言。

默认使用 Chat Completions API；可通过 `--APImode Responses` 复核 Responses API 路径。当前内置火山方舟与阿里云百炼的六个模型 alias，并支持外部 prompt、CSV/Markdown Glossary、剧集摘要、分批并发、失败重试、字幕前处理和已有运行修复。

## 主要能力

- Chat Completions / Responses 两种 API 模式，共用同一套翻译、校验与落盘流程。
- `temperature` 与 `top_p` 默认 OMIT，仅在显式指定时发送。
- 默认先生成当前输入范围的摘要，再以 50 cue 为一批翻译。
- 校验 JSON、键集合、`src` 原文回显、中文行长、行数、CPS 与时间长度。
- 失败批重试；必要时按更小 sub-batch 继续定位与补采。
- 可选 Stage A 前处理，以及最终双语 SRT 交付。
- CSV Glossary 原生支持 `source,target,note` 表头，也兼容既有 Markdown 表格。

## 仓库结构

```text
main.py                    # ping/selfcheck/repair/smoke/preprocess/run/bench
model_client.py            # Chat/Responses 适配、模型配置与调用
translate.py               # pipeline 公共 API 的兼容 re-export
pipeline/                  # 翻译、校验、repair 与前处理实现
pipeline/prompts/          # 公开运行时 prompt
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

所有读取字幕的命令都要求显式传入 `--srt`。

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

# 多模型 benchmark
python main.py bench \
  --srt /path/to/input.srt \
  --models all \
  --jobs 2 \
  --out out/bench
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
| `--jobs` | 1；多模型命令的模型并发数 |
| `--temperature` | `[0,2)`；默认 OMIT |
| `--top-p` | `(0,1]`；默认 OMIT |
| `--max-output-tokens` | run/smoke/repair 8192，bench 131072；可显式覆盖 |
| `--timeout` | run/repair 300 秒，smoke 180 秒，bench 1200 秒 |
| `--max-retries` | 2 次额外重试 |
| `--retry-backoff` | 3 秒指数退避基数 |
| `--no-summary` | 跳过默认剧集摘要 |
| `--output` | 仅 `run`；默认与输入同目录 |
| `--out` | smoke/bench/preprocess 等目录型产物 |

同时显式指定 `temperature` 和 `top_p` 时，CLI 会警告但不阻断。`OMIT` 只表示请求体未发送该字段，不代表不同 Provider 使用相同的数值默认值。

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
PYTHONPATH=. pytest -q
git diff --check
```

`.env`、`out/`、虚拟环境、缓存与日志不会进入版本控制。

本项目源代码采用 [MIT License](LICENSE)。用户自行提供的字幕、Glossary、模型生成内容及第三方模型/API 服务不属于本许可证的授权对象，分别受其权利人与服务条款约束。
