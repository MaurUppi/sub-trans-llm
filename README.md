# translation-test

六模型英→中字幕翻译 benchmark：整集（或切片）英文 SRT → JSON 协议 → 校验 → **中英双语 SRT**（译文在上、原文在下）。

样例剧集：*Un Village français* S01E03（`A.French.Village.S01E03.Passer.la.ligne_eng.srt`，747 条）。

## 功能概览

- **六模型统一调用**：火山方舟（DeepSeek flash/pro、Doubao）+ 阿里云百炼（Qwen 3.7 plus/max、3.8-max）
- **强制关闭 thinking**，避免思考 token 挤占输出预算
- **剧集摘要 → 分批翻译**：先通读摘要注入 instructions，再按批（默认 50 条）顺序或并行请求
- **JSON 输入/输出协议** + 离线 JSON 修复与业务校验
- **失败批 repair**：可重跑指定批，整批失败时 sub-batch 级联（10→5→2→1）绕过内容审核/截断
- **质量约定**：Netflix 简中 Timed Text 向标点 + 外部 Glossary 注入（见 `docs/quality_control.md`）

## 架构（重构后）

```text
main.py                 # CLI 编排（ping / selfcheck / smoke / run / bench / repair）
model_client.py         # LLM 适配层（Responses API、.env、关思考）— 不放入 pipeline
translate.py            # 薄门面：re-export pipeline 公共 API（兼容 import translate）
pipeline/               # 字幕领域流水线（实现本体）
tests/                  # pytest 表征/单元测试
docs/                   # 质量约定、prompt、术语表、方案文档
out/                    # 运行产物（gitignore）
```

### 分层与依赖

```text
main.py
  ├─ model_client        # ping / 直连模型
  └─ translate (门面)
         └─ pipeline/*   # 领域逻辑
                └─ model_client
```

- **`model_client`**：跨任务的基础设施，不进 `pipeline`（便于 ping、非字幕任务复用）。
- **`translate.py`**：稳定入口，实现已迁出；新代码可 `from pipeline import run_once`。
- **`pipeline/`**：只承载「字幕翻译」领域模块。

### `pipeline/` 模块

| 模块 | 职责 |
|------|------|
| `config` | 默认常量、`RunConfig`、省略号码位 |
| `models` | `Cue` / `ValidateReport` / `TranslateResult` / `BatchOutcome` |
| `srt_io` | 解析 SRT、切片/分批、input JSON、双语 SRT |
| `prompt` | `translation_prompt.md` 变量替换 + Glossary 紧凑表 |
| `summary` | 全量通读生成剧集摘要 |
| `json_repair` | 代码围栏剥离、残缺 JSON 加固 |
| `validate` | 键集合 / src·tr 协议 / Netflix 软规则 |
| `retry` | 可重试异常与结果重试判定 |
| `batch_client` | 单批 API 调用（含重试与批目录落盘） |
| `persist` | run 目录产物写入（meta/parsed/bilingual…） |
| `orchestrator` | `run_once`：摘要 → 分批（可并行）→ 合并 |
| `repair` | `repair_run_dir`：失败批重跑 + sub-batch 级联 |
| `selfcheck` | 无 API 离线自检 |
| `logging_util` | 阶段日志 |

## 环境准备

```bash
# 建议使用 venv
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -r requirements.txt

# 配置密钥与模型 ID
cp .env.example .env
# 编辑 .env：填写 ARK_API_KEY / ALI_API_KEY 及 MODEL_* 
```

依赖：`openai`、`python-dotenv`、`pytest`（开发/测试）。

## 快速开始

```bash
# 最少 token 连通六模型
python main.py ping

# 离线自检（解析 / 校验 / 分批逻辑，不调 API）
python main.py selfcheck

# 单元测试
pytest -q

# 烟测（默认前 8 条）
python main.py smoke --models deepseek-v4-flash --out out/smoke_flash

# 单模型全量（摘要 + 批大小 50 + 3 路并行）
python main.py run \
  --model deepseek-v4-flash \
  --batch-size 50 \
  --batch-jobs 3 \
  --out out/run_flash_full

# 多模型 benchmark
python main.py bench --models all --jobs 2 --batch-size 50 --batch-jobs 3 --out out/bench
```

### 常用参数

| 参数 | 说明 | 默认 |
|------|------|------|
| `--srt` | 英文 SRT 路径 | 仓库内样例 SRT |
| `--model` / `--models` | 模型 alias，`all` 表示六模型 | — |
| `--batch-size` | 每批字幕条数；`≤0` 整包一批 | 50（run/bench） |
| `--batch-jobs` | 批并行度 | 1 |
| `--temperature` / `--top-p` | 采样；省略则读 `.env` | 1.0 / 1.0 |
| `--max-output-tokens` | 输出上限 | 131072（全量）/ 8192（smoke） |
| `--no-summary` | 跳过通读摘要 | 默认开启摘要 |
| `--out` | 输出目录 | `out/<mode>_…` |
| `--max-retries` / `--retry-backoff` | API 重试 | 2 / 3s |

### 修复失败批

```bash
python main.py repair \
  --run-dir out/run_xxx/qwen3.7-plus \
  --model qwen3.7-plus \
  --batches 2,3 \
  --sub-batch-size 10 \
  --temperature 0.7 \
  --srt A.French.Village.S01E03.Passer.la.ligne_eng.srt
```

- 未指定 `--batches` 时，从 `meta.json` 失败批或缺键自动推断。
- 整批仍失败时按 `sub_batch_size` 级联拆小（默认 10→5→2→1）。

## 模型 alias

| Alias | 提供商 |
|-------|--------|
| `deepseek-v4-flash` | 火山方舟 Ark |
| `deepseek-v4-pro` | 火山方舟 Ark |
| `doubao-seed-2-1-turbo` | 火山方舟 Ark |
| `qwen3.7-plus` | 阿里云百炼 |
| `qwen3.7-max` | 阿里云百炼 |
| `qwen3.8-max` | 阿里云百炼 |

具体 model id 写在 `.env` 的 `MODEL_*` 中。

## 运行产物（`out/<run>/<model>/`）

```text
episode_summary.txt       # 通读摘要
instructions.txt          # 发给模型的 instructions
input.json                # 全集 id→原文
batches_plan.json / batch_00/ …
  input.json / raw_output.txt / parsed.json / validate.json
parsed.json               # 合并后的 id→{src,tr}
meta.json                 # 用量、批报告、ok 等
validate.json
bilingual.srt             # 成功时写出
bilingual.PARTIAL.txt     # 失败时的占位说明
```

双语格式示例：

```text
1
00:00:00,500 --> 00:00:03,340
跨越界线
CROSSING THE LINE
```

## 质量与参数约定

详见：

- [`docs/quality_control.md`](docs/quality_control.md) — 关思考、统一 temp/top_p（对比阶段 1.0/1.0）、官方采样对照、JSON 协议、Glossary 注入
- [`docs/translation_prompt.md`](docs/translation_prompt.md) — system prompt 模板（Netflix 向标点）
- [`docs/Un_Village_francais_Glossary.md`](docs/Un_Village_francais_Glossary.md) — 专名表
- [`docs/benchmark_plan.md`](docs/benchmark_plan.md) — 早期方案（模块划分已演进为 `pipeline/`）

**对比主线采样（默认）**：`temperature=1.0`，`top_p=1.0`。官方「翻译」推荐值见 quality_control §2.2，可用 CLI 覆盖做 A/B。

## 库用法（Python）

```python
from translate import run_once, repair_run_dir, self_check_offline
# 或: from pipeline import run_once, repair_run_dir, self_check_offline

self_check_offline("A.French.Village.S01E03.Passer.la.ligne_eng.srt")

result = run_once(
    srt_path="A.French.Village.S01E03.Passer.la.ligne_eng.srt",
    model="deepseek-v4-flash",
    batch_size=50,
    batch_jobs=3,
    out_dir="out/demo/deepseek-v4-flash",
)
print(result.ok, result.validate.stats)
```

直连模型：

```python
from model_client import call, list_models, smoke_test

print(list_models())
r = call("deepseek-v4-flash", "Reply with exactly: OK", max_output_tokens=16)
print(r.ok, r.text, r.usage)
```

## 开发

```bash
# 离线自检 + 单测
python main.py selfcheck
pytest -q

# 小改 pipeline 后优先跑 selfcheck 与 smoke，再全量
python main.py smoke --models deepseek-v4-flash --out out/smoke_dev
```

重构采用 TDD：先测再抽模块，每模块单独提交；`translate.py` 保持 re-export，避免打断 `main.py`。

## 仓库约定

| 路径 | 说明 |
|------|------|
| `.env` | 密钥与模型 ID，**勿提交**（已在 `.gitignore`） |
| `out/` | 运行输出，gitignore |
| `docs/` | 规范、prompt、术语表与参考文档 |

## 许可证与数据

字幕样例与术语表仅用于本仓库内 benchmark；请遵守片源与模型服务商的使用条款。
