# translation-test

英→简中字幕翻译与参数采集工具：读取整集或切片英文 SRT，以 JSON 协议调用模型，完成回显对齐、结果校验和失败重试，最后生成“译文在上、原文在下”的双语 SRT。

仓库目前有三层用途：

- 通用六模型翻译 CLI：单模型运行、六模型 benchmark、前处理和失败批修复。
- Opus 4.6-Low 参数对齐采集器：仅针对方舟 `deepseek-v4-flash` 与阿里云 `qwen3.7-plus` 的冻结 40-case 矩阵。
- TQA v1 评估材料：两集重点检查字幕及对应辅助说明；它们用于后续统一评价，不作为候选模型输入。

默认英文字幕是 [`sample/A.French.Village.S01E03_eng.srt`](sample/A.French.Village.S01E03_eng.srt)，共 747 个 cue。

## 主要能力

- 六个模型 alias 统一走 OpenAI SDK；默认 Chat Completions，可用 `--APImode Responses` 切换旧路径。
- Ark 两种模式都使用 `thinking={"type":"disabled"}`；阿里云 Chat 使用 `enable_thinking=False`，Responses 使用 `reasoning={"effort":"none"}`，均显式关闭思考。
- `temperature` / `top_p` 默认均不写入请求体；只有 CLI 或 Python 调用显式给值时才发送。
- 可先通读当前输入范围生成剧集摘要，再按默认 50 cue 分批翻译。
- JSON 加固、键集合校验、`src` 回显对齐和字幕质量度量。
- 失败批顺序重跑；仍失败时可按 10→5→2→1 cue 逐级定位和补采。
- 可选 Stage A 英文字幕前处理，以及最终 `{stem}_zh.srt` 交付。
- 冻结矩阵支持固定摘要复用、参数 wire evidence、断点续跑和 Provider refusal 留痕。

## 仓库结构

```text
main.py                    # 通用 CLI：ping/selfcheck/repair/smoke/preprocess/run/bench
model_client.py            # 六模型 Chat/Responses 适配、.env、采样字段 OMIT 语义
translate.py               # pipeline 公共 API 的兼容 re-export
pipeline/                  # 字幕翻译、校验、repair、前处理与矩阵采集实现
sample/                    # 两集全量源字幕、Low 中文参考与 TQA 重点检查材料
tests/                     # pytest 表征与回归测试
docs/                      # prompt、Glossary、API/质量/采样证据
.scratch/                  # 本地 Markdown issue tracker 与路线图
out/                       # 运行产物；已 gitignore
```

### 核心模块

| 模块 | 职责 |
|---|---|
| `pipeline.srt_io` | SRT 解析、切片、重编号、分批、双语 SRT 组装 |
| `pipeline.prompt` / `summary` | instructions、Glossary 与剧集摘要 |
| `pipeline.validate` / `src_align` | JSON 契约、键集合和原文回显对齐 |
| `pipeline.subtitle_check` | 中文 CPS、行长、行数和时长度量；默认不阻断 |
| `pipeline.batch_client` / `retry` | 单批模型调用、重试与批级证据落盘 |
| `pipeline.orchestrator` | `run_once`：摘要→分批→失败重跑→合并 |
| `pipeline.repair` | 已有 run 的离线恢复、失败批重跑和 sub-batch 补采 |
| `pipeline.preprocess` | Stage A 英文字幕清理、重切和交付适配 |
| `pipeline.sampling_matrix` | 冻结 40-case 的外层编排器；复用既有摘要、`run_once`、repair 与落盘能力 |
| `pipeline.inspection_rescue` | 获明确授权后，对已记录 Provider refusal 执行单术语专用占位补采 |

## 样例与评估材料

| 文件 | cue | 用途 |
|---|---:|---|
| `sample/A.French.Village.S01E03_eng.srt` | 747 | S01E03 全量英文模型输入 |
| `sample/A.French.Village.S01E03_chs.srt` | 747 | S01E03 Low 中文参考 |
| `sample/A.French.Village.S01E06_eng.srt` | 647 | S01E06 全量英文模型输入 |
| `sample/A.French.Village.S01E06_chs.srt` | 647 | S01E06 Low 中文参考 |
| `sample/A_French_Village_S01E03_翻译测试样例.srt` | 79 | 模型译文的重点检查清单 |
| `sample/A_French_Village_S01E06_翻译测试样例.srt` | 72 | 模型译文的重点检查清单 |
| `sample/*_翻译测试样例_说明.md` | — | 对应 cue 的 TQA 维度与说明，仅作评价辅助 |
| `sample/字幕翻译质量评估框架_TQA_v1.md` | — | 九维 TQA 评估框架 |

重点检查 SRT 只保留 Low 中文与英文正文，并使用与全量英文字幕一致的原 cue 编号和时间码。说明文件不是模型输入、独立测试项或正文真值来源。

## 环境准备

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# 编辑 .env，填写 ARK/ALI 的 base URL、API key 和 MODEL_* ID
```

`.env` 中的 `DEFAULT_TEMPERATURE` / `DEFAULT_TOP_P` 已废弃；即使存在也不会影响调用。`DEFAULT_MAX_OUTPUT_TOKENS` 仍可作为直连 `model_client.call` 的可选上限来源。

## 通用 CLI

```bash
# 六模型最小连通检查；默认 Chat Completions，会产生真实 API 调用
python main.py ping

# 保留的 Responses 路径回归
python main.py ping --APImode Responses

# 使用当前默认 E03 样例做离线解析/校验自检
python main.py selfcheck

# 单元测试
PYTHONPATH=. pytest -q

# 默认取前 8 个 cue 的烟测
python main.py smoke \
  --APImode ChatCompletion \
  --models deepseek-v4-flash \
  --out out/smoke_flash

# 单模型全量；默认 50 cue/批、batch_jobs=1、max_output_tokens=8192、timeout=300
python main.py run \
  --APImode ChatCompletion \
  --srt sample/A.French.Village.S01E03_eng.srt \
  --model deepseek-v4-flash \
  --glossary docs/Un_Village_francais_Glossary.csv \
  --batch-size 50 \
  --batch-jobs 1 \
  --output out/run_flash_full_bilingual.srt

# 多模型 benchmark；jobs 是模型并发数，batch-jobs 是单模型批并发数
python main.py bench \
  --srt sample/A.French.Village.S01E03_eng.srt \
  --models all \
  --jobs 2 \
  --batch-size 50 \
  --batch-jobs 1 \
  --out out/bench
```

### 前处理

```bash
# 只执行 Stage A
python main.py preprocess \
  --srt sample/A.French.Village.S01E03_eng.srt \
  --fix-overlaps \
  --remove-sdh \
  --out out/preprocess_e03

# 前处理后接单模型翻译
python main.py run \
  --srt sample/A.French.Village.S01E03_eng.srt \
  --model deepseek-v4-flash \
  --preprocess \
  --remove-sdh \
  --output out/run_preprocessed_bilingual.srt
```

### 常用参数

| 参数 | 当前行为 / 默认值 |
|---|---|
| `--APImode` / `--api-mode` | `ChatCompletion`（默认）；也接受 `Responses`，内部记录为 `chat_completions` / `responses` |
| `--srt` | `sample/A.French.Village.S01E03_eng.srt` |
| `--model` / `--models` | 单个 alias、逗号列表或 `all` |
| `--glossary` | `smoke` / `run` / `bench` 默认不注入，必须显式给路径；推荐 UTF-8 CSV，表头固定为 `source,target,note`，兼容原 Markdown 表格；`repair` 复用原 run 的 `instructions.txt` |
| `--batch-size` | 50；`≤0` 表示整包一批 |
| `--batch-jobs` | 1；大于 1 时同一模型多批并行 |
| `--jobs` | 1；多模型命令的模型并发数 |
| `--temperature` | `[0,2)`；默认 OMIT，不向 API 发送 |
| `--top-p` | `(0,1]`；默认 OMIT，不向 API 发送 |
| `--max-output-tokens` | run/smoke/repair 8192，bench 131072；50-cue 现有生产证据的单批峰值为 3549，仍可显式调高 |
| `--timeout` | run/repair 300 秒，smoke 180 秒，bench 1200 秒；可显式覆盖 |
| `--no-summary` | `smoke` / `run` / `bench` 默认生成摘要；传入后跳过 |
| `--max-retries` / `--retry-backoff` | 2 次额外重试 / 3 秒指数退避 |
| `--out` | 仅用于 `smoke` / `bench` / `preprocess` 等目录型产物；`run` 已废弃该参数 |
| `--output` | `run` 的最终双语 SRT 文件路径；默认写到 `--srt` 同目录的 `{stem}_zh.srt`，显式指定则严格写到该路径；内部证据目录自动生成 |

同时显式指定 `temperature` 和 `top_p` 时 CLI 会警告但不阻断。做可归因实验时建议一次只改变一个；`OMIT` 只表示字段未发送，不能写成跨 Provider 的共同数值。

### Glossary CSV

通用翻译命令推荐显式传入 `docs/Un_Village_francais_Glossary.csv`。CSV 使用
`source,target,note` 表头；`note` 可以留空，存在时会随 `source = target` 映射一起注入
instructions。UTF-8 BOM 和带引号的逗号字段均受支持。最终发送给模型的完整内容会写入运行目录的
`instructions.txt`；Chat Completions 将它作为第一条 `system` message，Responses 则使用独立的
`instructions` 字段。

```csv
source,target,note
Daniel Larcher,达尼埃尔·拉尔谢,维勒纳夫市长/大夫
Villeneuve,维勒纳夫,故事主要发生地
```

冻结 40-case 矩阵仍固定使用 Markdown 版 Glossary，以保持既有实验哈希与续跑契约不变。

## 修复已有 run

```bash
python main.py repair \
  --APImode Responses \
  --run-dir out/run_xxx/qwen3.7-plus \
  --model qwen3.7-plus \
  --srt sample/A.French.Village.S01E03_eng.srt \
  --batches 2,3 \
  --sub-batch-size 10 \
  --temperature 0.7
```

- 未指定 `--batches` 时，根据 `meta.json` 的失败批或缺键推断。
- 整批仍失败时，默认按 10→5→2→1 cue 缩小；Provider 仍可拒绝单 cue，repair 不保证成功。
- repair 必须沿用原 run 的 API 模式与显式采样参数；旧 Responses run 要写 `--APImode Responses`，省略采样字段时继续使用 OMIT。
- 主 attempt、批目录、`repair.json`、合并后的 `parsed.json` 和最终 SRT 共同构成证据，不能只看最后的 `status`。

## 冻结 40-case 参数矩阵

`pipeline.sampling_matrix` 是当前 Opus 4.6-Low 对齐采集器，不是通用六模型 benchmark。它固定：

- API 模式：`Responses`。这是已冻结采集契约；通用 CLI 改为默认 Chat 后，矩阵内部仍显式固定 Responses，避免续跑混入不同 wire contract。

- 模型：`deepseek-v4-flash`、`qwen3.7-plus`。
- 剧集：S01E03（747 cue）、S01E06（647 cue）。
- 每个模型 10 个采样臂：
  - `temperature/top_p = OMIT/OMIT`；
  - `temperature ∈ {0.1, 0.3, 0.7, 1.0, 1.3, 1.5}`，`top_p=OMIT`；
  - `top_p ∈ {0.7, 0.8, 1.0}`，`temperature=OMIT`。
- 共 40 个整集 case、560 个主翻译批；每个 case 为 50 cue/批、`batch_jobs=1`。
- 两个 Provider 流可并行；同一模型内部按 case 顺序执行。
- 每集摘要只生成一次并冻结复用；Glossary 固定为 `docs/Un_Village_francais_Glossary.md`。
- 输出文件名包含模型、episode、temperature 与 topP，OMIT 与显式值可直接识别。

### 与既有 pipeline 的调用关系

`pipeline.sampling_matrix` 只负责矩阵、状态和阶段编排，不另建一套翻译流水线。各子命令与既有模块的关系如下：

| 子命令 | 调用关系 | 串并行行为 |
|---|---|---|
| `summaries` | 逐集调用 `pipeline.summary.generate_episode_summary`，生成或复用冻结摘要 | S01E03、S01E06 依次串行 |
| `run` | `execute_case` 调用既有 `pipeline.orchestrator.run_once`；后者继续使用 prompt、Glossary、SRT 分批、`batch_client`、retry、validate、字幕度量与 persist | 两个目标模型/Provider 流可并行；同一模型内 case 串行；每个 case 固定 `batch_jobs=1`，批次串行 |
| `repair` | 对 `status=failed` 的 case 逐个调用既有 `pipeline.repair.repair_run_dir` | case 串行；不会由 `run` 自动触发 |
| `inspection-rescue` | 对符合条件的失败/refusal case 逐个调用 `pipeline.inspection_rescue`；内部复用 `batch_client.call_one_batch`、`srt_io` 和 persist | case 串行；不会由 `run` 或 `repair` 自动触发 |

因此，可以先用 `summaries` 独立生成冻结摘要，也可以直接执行 `run`（它会先生成或复用摘要）；`run` 结束后，再按需要分别显式执行 `repair` 和经授权的 `inspection-rescue`。`out/.../TQA-evaluation/` 属于采集完成后的结果评审工具，不被 `sampling_matrix` 导入或调用，也不参与参数采集。

```bash
MATRIX_OUT=out/opus46-low-parity-full-matrix-20260809

# 只生成 40-case manifest，不调 API
python -m pipeline.sampling_matrix plan --out "$MATRIX_OUT"

# 每集只生成一次冻结摘要；会产生真实 API 调用
python -m pipeline.sampling_matrix summaries \
  --out "$MATRIX_OUT" \
  --summary-model deepseek-v4-flash

# 断点续跑采集；已完成 case 会跳过
python -m pipeline.sampling_matrix run \
  --out "$MATRIX_OUT" \
  --summary-model deepseek-v4-flash

python -m pipeline.sampling_matrix status --out "$MATRIX_OUT"

# 对 status=failed 的 case 做普通失败批修复
python -m pipeline.sampling_matrix repair --out "$MATRIX_OUT"
```

### Provider refusal 与 inspection rescue

普通 repair 仍无法补齐时，case 可以保留为 `provider_refusal`。`inspection-rescue` 是本轮冻结实验的专用、可审计补采手段，不是通用翻译功能：

- 只有用户明确授权且符合 Provider 条款时才可运行。
- 当前实现仅处理已记录缺键、且原文包含冻结源词 `Communists` 的 cue，并在返回后恢复为 `共产党`；其它内容直接拒绝。
- 请求使用不透明术语占位符，返回后按冻结 Glossary 恢复；原主 attempt 不改写。
- case 的主状态仍是 `provider_refusal`，不能算作原始 50-cue 成功。
- rescue SRT 仍写入普通 `bilingual/` 目录，但文件名永久带 `__inspection-rescue.srt`。
- `case.json`、`inspection_rescue/pass-*/manifest.json` 和输出哈希记录完整变换。

```bash
python -m pipeline.sampling_matrix inspection-rescue --out "$MATRIX_OUT"
```

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

通用 `run` 的单模型目录通常包含：

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
bilingual.srt              # 全部校验通过时生成
bilingual.PARTIAL.txt      # 未完成时的说明文件
```

矩阵采集根目录额外包含：

```text
matrix.json
progress.json
context/<episode>/fixed_summary.json
cases/<case-id>/case.json
cases/<case-id>/attempt-*/
bilingual/<parameter-visible-name>.srt
```

双语格式：

```text
1
00:00:00,500 --> 00:00:03,340
跨越界线
CROSSING THE LINE
```

## Python API

```python
from pipeline import repair_run_dir, run_once, self_check_offline

self_check_offline("sample/A.French.Village.S01E03_eng.srt")

result = run_once(
    srt_path="sample/A.French.Village.S01E03_eng.srt",
    model="deepseek-v4-flash",
    glossary_path="docs/Un_Village_francais_Glossary.csv",
    batch_size=50,
    batch_jobs=1,
    max_output_tokens=8192,
    timeout=300,
    api_mode="ChatCompletion",
    out_dir="out/demo/deepseek-v4-flash",
)
print(result.ok, result.validate.stats, result.sampling)
```

`run_once` 是较早的库级接口，若不显式传入，上限/超时仍为
`max_output_tokens=131072`、`timeout=1200`；上例显式使用当前 `main.py run` 的生产默认值。

直连模型：

```python
from model_client import OMIT, call, list_models

print(list_models())
result = call(
    "deepseek-v4-flash",
    "Reply with exactly: OK",
    temperature=OMIT,
    top_p=OMIT,
    max_output_tokens=16,
    # 默认 ChatCompletion；复核旧路径时传 api_mode="Responses"
)
print(result.ok, result.api_mode, result.text, result.usage)
```

## 文档口径

- [`docs/baseinfo.md`](docs/baseinfo.md)：当前 API、模型配置、采样 OMIT 语义和校验结论。
- [`docs/translation_prompt.md`](docs/translation_prompt.md)：翻译 instructions 模板。
- [`docs/Un_Village_francais_Glossary.csv`](docs/Un_Village_francais_Glossary.csv)：通用翻译命令推荐的 `source,target,note` Glossary。
- [`docs/Un_Village_francais_Glossary.md`](docs/Un_Village_francais_Glossary.md)：冻结 40-case 矩阵继续使用的 Markdown Glossary。
- [`docs/deepseek-qwen-temperature-top-p-defaults.md`](docs/deepseek-qwen-temperature-top-p-defaults.md)：两 Provider 的采样参数证据。
- [`sample/字幕翻译质量评估框架_TQA_v1.md`](sample/字幕翻译质量评估框架_TQA_v1.md)：当前 TQA 评价维度与样例规则。
- [`docs/quality_control.md`](docs/quality_control.md)：早期六模型固定 `1.0/1.0` 对比协议；不代表当前 CLI 默认值，也不覆盖冻结两模型矩阵。
- [`.scratch/opus46-low-parity/`](.scratch/opus46-low-parity/)：全量 40-case 决策前的研究路线与 tickets，现保留为后续 TQA 评价的规划历史，不是当前采集器执行契约。

## 开发与仓库约定

```bash
python main.py selfcheck
PYTHONPATH=. pytest -q
git diff --check
```

- `.env` 含密钥和模型 ID，禁止提交。
- `out/`、虚拟环境、缓存和日志已加入 `.gitignore`。
- `.scratch/<feature>/` 是本仓库的本地 Markdown issue tracker；约定见 `docs/agents/issue-tracker.md`。
- 修改行为时先写失败测试，再实现；提交前运行全量测试和 `git diff --check`。

## 许可证与数据

字幕样例、Low 参考译文和术语表仅用于本仓库内 benchmark。使用与分发时请遵守片源版权及模型 Provider 服务条款。
