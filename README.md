# translation-test

英→简中字幕翻译与参数采集工具：读取整集或切片英文 SRT，以 JSON 协议调用模型，完成回显对齐、结果校验和失败重试，最后生成“译文在上、原文在下”的双语 SRT。

仓库目前有三层用途：

- 通用六模型翻译 CLI：单模型运行、六模型 benchmark、前处理和失败批修复。
- Opus 4.6-Low 参数对齐采集器：仅针对方舟 `deepseek-v4-flash` 与阿里云 `qwen3.7-plus` 的冻结 40-case 矩阵。
- TQA v1 评估材料：两集重点检查字幕及对应辅助说明；它们用于后续统一评价，不作为候选模型输入。

默认英文字幕是 [`sample/A.French.Village.S01E03_eng.srt`](sample/A.French.Village.S01E03_eng.srt)，共 747 个 cue。

## 主要能力

- 六个模型 alias 统一走 OpenAI SDK Responses API。
- Ark 使用 `thinking={"type":"disabled"}`，阿里云使用 `reasoning={"effort":"none"}`，显式关闭思考。
- `temperature` / `top_p` 默认均不写入请求体；只有 CLI 或 Python 调用显式给值时才发送。
- 可先通读当前输入范围生成剧集摘要，再按默认 50 cue 分批翻译。
- JSON 加固、键集合校验、`src` 回显对齐和字幕质量度量。
- 失败批顺序重跑；仍失败时可按 10→5→2→1 cue 逐级定位和补采。
- 可选 Stage A 英文字幕前处理，以及最终 `{stem}_zh.srt` 交付。
- 冻结矩阵支持固定摘要复用、参数 wire evidence、断点续跑和 Provider refusal 留痕。

## 仓库结构

```text
main.py                    # 通用 CLI：ping/selfcheck/repair/smoke/preprocess/run/bench
model_client.py            # 六模型 Responses API 适配、.env、采样字段 OMIT 语义
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
| `pipeline.sampling_matrix` | 冻结 40-case 参数矩阵、摘要复用、进度与恢复 |
| `pipeline.inspection_rescue` | 获明确授权后，对已记录 Provider refusal 执行专用占位补采 |

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
# 六模型最小连通检查；会产生真实 API 调用
python main.py ping

# 使用当前默认 E03 样例做离线解析/校验自检
python main.py selfcheck

# 单元测试
PYTHONPATH=. pytest -q

# 默认取前 8 个 cue 的烟测
python main.py smoke \
  --models deepseek-v4-flash \
  --out out/smoke_flash

# 单模型全量；默认 50 cue/批、batch_jobs=1、启用摘要
python main.py run \
  --srt sample/A.French.Village.S01E03_eng.srt \
  --model deepseek-v4-flash \
  --glossary docs/Un_Village_francais_Glossary.md \
  --batch-size 50 \
  --batch-jobs 1 \
  --out out/run_flash_full \
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
  --out out/run_preprocessed
```

### 常用参数

| 参数 | 当前行为 / 默认值 |
|---|---|
| `--srt` | `sample/A.French.Village.S01E03_eng.srt` |
| `--model` / `--models` | 单个 alias、逗号列表或 `all` |
| `--glossary` | 默认不注入；给路径才加入 instructions |
| `--batch-size` | 50；`≤0` 表示整包一批 |
| `--batch-jobs` | 1；大于 1 时同一模型多批并行 |
| `--jobs` | 1；多模型命令的模型并发数 |
| `--temperature` | `[0,2)`；默认 OMIT，不向 API 发送 |
| `--top-p` | `(0,1]`；默认 OMIT，不向 API 发送 |
| `--max-output-tokens` | run/bench 131072，smoke 8192 |
| `--no-summary` | 默认生成摘要；传入后跳过 |
| `--max-retries` / `--retry-backoff` | 2 次额外重试 / 3 秒指数退避 |
| `--out` | 内部证据目录；默认写入带时间戳的 `out/` 子目录 |
| `--output` | `run` 成功后的最终双语 SRT；默认写到源字幕旁边 `{stem}_zh.srt` |

同时显式指定 `temperature` 和 `top_p` 时 CLI 会警告但不阻断。做可归因实验时建议一次只改变一个；`OMIT` 只表示字段未发送，不能写成跨 Provider 的共同数值。

## 修复已有 run

```bash
python main.py repair \
  --run-dir out/run_xxx/qwen3.7-plus \
  --model qwen3.7-plus \
  --srt sample/A.French.Village.S01E03_eng.srt \
  --batches 2,3 \
  --sub-batch-size 10 \
  --temperature 0.7
```

- 未指定 `--batches` 时，根据 `meta.json` 的失败批或缺键推断。
- 整批仍失败时，默认按 10→5→2→1 cue 缩小；Provider 仍可拒绝单 cue，repair 不保证成功。
- repair 必须沿用原测试的显式采样参数；省略字段时继续使用 OMIT。
- 主 attempt、批目录、`repair.json`、合并后的 `parsed.json` 和最终 SRT 共同构成证据，不能只看最后的 `status`。

## 冻结 40-case 参数矩阵

`pipeline.sampling_matrix` 是当前 Opus 4.6-Low 对齐采集器，不是通用六模型 benchmark。它固定：

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
- 仅处理已记录缺键、且命中冻结 Glossary 术语的 cue；其它内容直接拒绝。
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
    glossary_path="docs/Un_Village_francais_Glossary.md",
    batch_size=50,
    batch_jobs=1,
    out_dir="out/demo/deepseek-v4-flash",
)
print(result.ok, result.validate.stats, result.sampling)
```

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
)
print(result.ok, result.text, result.usage)
```

## 文档口径

- [`docs/baseinfo.md`](docs/baseinfo.md)：当前 API、模型配置、采样 OMIT 语义和校验结论。
- [`docs/translation_prompt.md`](docs/translation_prompt.md)：翻译 instructions 模板。
- [`docs/Un_Village_francais_Glossary.md`](docs/Un_Village_francais_Glossary.md)：本剧 Glossary。
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
