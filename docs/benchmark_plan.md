# 六模型 Benchmark 实现方案

依据：`baseinfo.md`、`quality_control.md`、现有 `model_client.py`。  
目标：整文件（或切片）英进 → JSON 协议 → 校验 → 双语 SRT（**译文在上、原文在下**）；六模型可比对照。

**状态：已实现 `translate.py` + `main.py`；六模型 smoke（8 条）并行通过（2026-08-05）。**

---

## 1. 模块划分

```text
translation-test/
├── .env / .env.example
├── model_client.py          # 已有：Responses 调用、关思考、读 .env
├── translate.py             # 新建：SRT↔JSON、instructions、单次翻译、校验、写 SRT
├── main.py                  # 新建：CLI 调度（smoke / run / bench）
├── docs/
│   ├── translation_prompt.md
│   ├── Un_Village_francais_Glossary.md
│   └── quality_control.md
└── out/                     # gitignore；烟测与 benchmark 产物
    └── bench/<run_id>/
```

| 模块 | 职责 | 不负责 |
|---|---|---|
| `model_client.py` | `call(model, input, instructions, …)` | SRT、Glossary、业务校验 |
| `translate.py` | 读 SRT→JSON、拼 instructions、调模型、校验、拼双语 SRT | CLI 参数解析可给 main |
| `main.py` | 参数、模式切换、并发调度、汇总报告 | 不直接拼 prompt 细节 |

可选（实现时若 `translate.py` 过大再拆）：

- `srt_io.py`：parse / dump  
- `validate.py`：JSON 协议与软质量规则  

首版建议 **全部放在 `translate.py`**，保持 3 文件清晰。

---

## 2. `translate.py` 设计

### 2.1 核心数据结构

```python
@dataclass
class Cue:
    id: str          # "0","1",… 稳定字符串键（与 JSON 协议一致）
    seq: int         # 原 SRT 序号（可选保留）
    start: str       # "00:00:00,500"
    end: str
    text: str        # 原文（可多行，用 \n 连接进 JSON 值）

@dataclass
class TranslateRequest:
    cues: list[Cue]
    input_json: str          # json.dumps({id: text})
    instructions: str

@dataclass  
class ValidateReport:
    ok: bool
    errors: list[str]        # 硬失败：缺键、非 JSON、缺 tr
    warnings: list[str]      # 软问题：，。|、省略号码位、src 不一致
    parsed: dict | None      # id -> {src, tr}

@dataclass
class TranslateResult:
    model_alias: str
    model_id: str
    usage: Usage             # from model_client
    status: str
    incomplete_reason: str | None
    validate: ValidateReport
    bilingual_srt: str | None
    raw_text: str
    elapsed_sec: float
```

### 2.2 必要参数（函数 / CLI 对齐）

| 参数 | 默认 | 说明 |
|---|---|---|
| `srt_path` | 必填 | 英文字幕 `.srt` |
| `model` | 必填（bench 时循环） | `model_client` alias |
| `source_language` | `"英语"` | 替换 `${sourceLanguage}` |
| `target_language` | `"简体中文"` | 替换 `${targetLanguage}` |
| `prompt_path` | `docs/translation_prompt.md` | system 模板 |
| `glossary_path` | `docs/Un_Village_francais_Glossary.md` | 可 `None` 跳过 |
| `max_cues` | `None` | 烟测：只取前 N 条 |
| `cue_offset` | `0` | 从第几条开始取 |
| `max_output_tokens` | `131072` | 与 quality_control 统一；烟测可改小如 4096 |
| `timeout` | 全量建议 `600+` | 整集一次生成较慢 |
| `bilingual_order` | **译文上 / 原文下** | 固定：`tr\nsrc` |

### 2.3 流水线（单模型一次）

```text
1. parse_srt(path) → list[Cue]
2. 可选 slice(cues, offset, max_cues)
3. build_input_json(cues) → str
     {"0": "CROSSING THE LINE", "1": "Marcel ?", ...}
4. build_instructions(prompt_path, glossary_path, source_lang, target_lang) → str
5. model_client.call(
       model,
       input=input_json,          # JSON 字符串
       instructions=instructions,
       temperature=1.0,           # 默认走 .env / 模块默认
       top_p=1.0,
       max_output_tokens=131072,
       timeout=...
   )
6. validate_response(raw_text, cues) → ValidateReport
7. 若硬校验通过：build_bilingual_srt(cues, parsed) → 译文在上、原文在下
8. 落盘：raw.json.txt / result.json / out.srt / report.json
```

### 2.4 `build_instructions`

```text
instructions =
  read(prompt_path)
    .replace("${sourceLanguage}", source_language)
    .replace("${targetLanguage}", target_language)
  + "\n\n## 专有名词（必须遵守，不得另译）\n"
  + compact_glossary(glossary_path)   # 从 Markdown 表抽「原名 → 中文」
```

- **不**注入 Netflix / AGENTS 全文（参考文档）。  
- Glossary：解析 `| 中文 | 法文/英文 |` 表行，压成 `EN = 中文` 多行；多别名拆分。

### 2.5 SRT 解析注意

- 去 BOM；统一 `\n`。  
- 块：`序号` + `时间码` + `文本行…`。  
- JSON 的 `id` 用 **切片后的稳定下标** `"0"…"n-1"`（与 prompt Examples 一致），时间码仍挂在 `Cue` 上本地拼回。  
- 原文多行：JSON 值内用 `\n` 连接；双语输出时 `tr` / `src` 各自可多行。

### 2.6 双语 SRT 格式（译文上、原文下）

```text
1
00:00:00,500 --> 00:00:03,340
越过边界
CROSSING THE LINE

2
00:00:06,790 --> 00:00:07,790
马塞尔？
Marcel ?
```

- 时间码：**原样**来自英文 SRT，模型不碰。  
- 仅当 `tr` 缺失时该条硬失败，不写残缺文件（或写 `.partial.srt` 并标 fail）。

---

## 3. 返回结果检查（`validate_response`）

### 3.1 硬错误（`ok=False`，benchmark 记 FAIL）

| 检查 | 规则 |
|---|---|
| JSON 可解析 | 去 markdown 围栏后 `json.loads`；失败则 FAIL |
| 顶层为 object | 非 array |
| 键集合一致 | `set(out.keys()) == set(input_ids)`；多键/少键均 FAIL |
| 每值结构 | 必须是 object 且含非空字符串 `tr` |
| `src` 存在 | 建议必填；若缺可降为 warning（按严格模式可升硬错误） |
| API 层 | `status != completed` 或 `incomplete_reason` → FAIL |

### 3.2 软警告（不单独判死，写入 report）

| 检查 | 规则 |
|---|---|
| `src` 与原文 | 规范化空白后不等 → warning（模型偶发改 src） |
| 标点 Netflix | `tr` 含 `，` `。` → warning |
| 竖杠 | 含 `\|` 或 `｜` → warning |
| 省略号 | 含 U+22EF `⋯` 或 `...` → warning（可后处理修） |
| 英文残留 | `tr` 拉丁字母远多于汉字 → warning（漏译启发式） |
| 空 `tr` | 硬错误 |

### 3.3 建议返回结构

```json
{
  "ok": false,
  "errors": ["missing keys: 12,15", "id 3: missing tr"],
  "warnings": ["id 7: contains '，'", "id 9: src mismatch"],
  "stats": {"n_in": 10, "n_out": 8, "n_tr_ok": 7}
}
```

Benchmark 汇总表：`model | ok | errors | warnings | in_tok | out_tok | sec`。

---

## 4. `main.py` 调度

### 4.1 子命令（推荐）

```bash
# A. 模型连通（已有，可转发）
python main.py ping

# B. 小规模烟测（默认前 8 条，单模型或全部顺序）
python main.py smoke \
  --srt A.French.Village.S01E03.Passer.la.ligne_eng.srt \
  --max-cues 8 \
  --models deepseek-v4-flash
  # 或 --models all

# C. 单模型全量（整文件一次）
python main.py run \
  --srt ... \
  --model deepseek-v4-flash \
  --out out/run_flash/

# D. 六模型 benchmark（默认顺序；--jobs N 并发）
python main.py bench \
  --srt ... \
  --jobs 1 \
  --out out/bench/<timestamp>/
```

### 4.2 `main` 只做

1. 解析 argparse  
2. 解析模型列表（`all` → `list_models()`）  
3. 调 `translate.run_once(...)` 或线程池 map  
4. 写 `summary.json` / `summary.md`  
5. 退出码：硬失败数 > 0 → `1`

---

## 5. 烟测 → 并发实测路径

### Phase 0 — 环境与连通（已有）

```bash
python model_client.py
# 期望 6/6 OK，个位数 token
```

- 确认 `.env` 中 `DEFAULT_MAX_OUTPUT_TOKENS=131072`（或 CLI 显式传入）。  
- `DEFAULT_TEMPERATURE=1.0` / `DEFAULT_TOP_P=1.0`。

### Phase 1 — 离线单测（无 API）

| 测试 | 内容 |
|---|---|
| `parse_srt` | S01E03 → 747 cues；时间码不丢 |
| `build_input_json` | 键连续、无时间码 |
| `build_instructions` | 变量已替换、含 Glossary 若干行 |
| `validate_response` | 用 fixture：合法 JSON / 缺键 / 非 JSON / 带 markdown 围栏 |
| `build_bilingual_srt` | 译文上原文下 |

不烧 token，CI 可跑。

### Phase 2 — 小规模 API 烟测（强烈建议）

| 项 | 建议值 | 原因 |
|---|---|---|
| `max_cues` | **5–10** | 协议+校验够用，成本低 |
| 模型 | 先 **1 个** flash，再 **6 个顺序** | 隔离问题 |
| `max_output_tokens` | 烟测可用 **4096–8192** | 足够 10 条；避免误配 |
| `jobs` | **1** | 不做并发 |
| 超时 | 120s | 足够 |

通过标准：

1. `validate.ok == True`  
2. 产出可读双语 `.srt`  
3. `usage.reasoning_tokens == 0`（思考已关）  
4. 六模型 smoke 均硬通过（允许标点类 warning）

```bash
python main.py smoke --srt ... --max-cues 8 --models all --jobs 1
```

### Phase 3 — 单模型全量试跑

```bash
python main.py run --srt ... --model deepseek-v4-flash --jobs 1
```

- `max_output_tokens=131072`，`timeout≥600`  
- 观察：是否 `incomplete` / JSON 截断 / 耗时 / 费用  
- 全量只先跑 **1 个最便宜模型**（flash），确认窗口与协议后再 bench

### Phase 4 — 六模型 benchmark

| 策略 | 配置 | 适用 |
|---|---|---|
| **顺序（默认）** | `--jobs 1` | 稳妥、日志清晰、易对照限流 |
| **有限并发** | `--jobs 2` 或 `3` | 加速；注意 Ark/Aliyun RPM |
| **满并发** | `--jobs 6` | 仅在 smoke+单全量都稳后；成本与限流风险最高 |

并发实现建议：

```python
# concurrent.futures.ThreadPoolExecutor
# 每任务独立 translate.run_once → 独立 out 子目录
# 主线程汇总 summary；单任务异常不拖垮其它
```

**不要**用多进程复制 OpenAI client 复杂状态；线程 + 每任务新建 client（`model_client.call` 已每次 `_build_client`）即可。

### Phase 5 — 产物与对比

```text
out/bench/20260805_120000/
  summary.json
  summary.md
  deepseek-v4-flash/
    input.json
    instructions.txt          # 可选 debug
    raw_output.txt
    parsed.json
    validate.json
    bilingual.srt
    meta.json                 # usage, elapsed, model id
  deepseek-v4-pro/
    ...
```

`summary.md` 列：模型、硬通过、error 数、warning 数、in/out tokens、秒数、路径。

---

## 6. 与现有代码的衔接

| 已有 | 用法 |
|---|---|
| `model_client.call` | 直接调用；全量时提高 `timeout` |
| `list_models()` | bench 模型列表 |
| 关思考 / temp / top_p | 保持模块默认，**不要**在 translate 里再开思考 |
| `docs/sub_processor.py` | 可选参考换行；**不**强依赖（协议以 JSON+本地时间码为准） |
| `docs/translate_subtitles.py` | Skill 占位脚本；**可借鉴**：阶段日志、双语顺序开关、异常落盘；**不借鉴**：假翻译占位、强制 pysrt、批 20（全量一次策略不同） |

### 6.1 已落地防御（Phase3 前）

| 项 | 实现 |
|---|---|
| 全量 timeout | `run`/`bench` 默认 **1200s** |
| 重试 | `max_retries=2` + 指数退避；429/5xx/超时/incomplete/JSON 硬失败可重试 |
| 失败仍落盘 | 启动即写 input/instructions；每次 raw；失败写 `bilingual.PARTIAL.txt` |
| incomplete/length 提示 | validate errors 带 max_out 提示 |
| 进度日志 | 加载 / 尝试 / 成功失败（风格参考 translate_subtitles 阶段 print） |

### 6.2 分批策略（方案 A，已实现）

整集一次 JSON 在 flash 上约 50 条后截断 → 改为：

| 参数 | 默认 | 含义 |
|---|---|---|
| `--batch-size` | **50** | 每批 cue 数；`<=0` 整包单批 |
| `--batch-jobs` | **1** | **1=顺序送批**；**>1=多批并行**请求，完成后按全局 id 合并 |

- 全局 id `"0".."n-1"`，批间不重编号冲突。  
- 每批独立目录 `batch_00/`…；汇总 `bilingual.srt` + `meta.batch_reports`。  
- 拼装时：**tr=模型，原文=本地 Cue.text**（时间码本地）。  
- **不** `import translate_subtitles`；仅借鉴分批步进 + 阶段日志 + 本地拼双语。

### 6.4 通读摘要 + 分批（已实现）

```text
1) generate_episode_summary: input=全量 id\\ttext → 摘要 S
   落盘: episode_summary.txt / episode_summary.meta.json / episode_summary_input.txt
2) instructions = prompt + glossary + S
3) 分批 input（50 条）顺序或并行 → 本地按全局 id 合并
```

| CLI | 默认 |
|---|---|
| （无 flag） | **启用**通读摘要 |
| `--no-summary` | 跳过通读 |

摘要失败 → **降级无摘要分批**（不阻断）。对白 input 约 2×；instructions 按批重复。

### 6.3 方案 B（本地填 src）错位风险评估

| 做法 | 错位风险 |
|---|---|
| **模型回 src+tr，拼装用模型 src** | 模型改写/抄错 src；tr 仍可能挂错 id |
| **模型回 src+tr，拼装用本地 text 作原文行**（当前） | **时间码↔原文不会错**；错位只可能是 **tr 挂到错误 id** |
| **协议只出 tr、本地填 src** | 输出更短；若模型漏键/错键，仍会 tr 错位；**不降低 id 绑定风险** |

结论：

1. **本地填原文行不引入「译文贴到错误时间码」以外的新风险**——时间码与英文永远跟 SRT 对齐。  
2. **真正的错位风险在「id → tr」映射**（漏键、串键、少批）。靠：键集合硬校验、批失败不合并进成片、可选校验模型 `src` 与本地 text 是否一致作 warning。  
3. 数组协议（按下标顺序）比 **id 键对象** 更容易静默错位；我们坚持 id 键。  
4. 方案 B 可作**减输出体积**的优化，但应与方案 A 分批叠加，不能替代分批。  

**建议小改 `model_client`（实现时）：**

- 默认 `timeout` 支持更大，或 `call(..., timeout=600)` 由 translate 传入（**已支持**）。  
- `.env` 写入 `DEFAULT_MAX_OUTPUT_TOKENS=131072`（与 quality_control 一致）。  
- 可选：剥离输出里的 ` ```json ` 围栏可在 `validate` 做，不必改 client。

---

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| 整集 JSON 输出截断 | `max_output_tokens=131072`；检查 `incomplete_reason`；fail 重试 1 次 |
| 模型包 markdown 围栏 | validate 前 strip ` ```json ... ``` ` |
| 并发 429 | 默认 jobs=1；退避重试（最多 2 次，指数等待） |
| 费用 | smoke 强制 max_cues；全量先 flash |
| Glossary 解析漏行 | 单测样例表；smoke 人工看 instructions 片段 |
| src 被改写 | warning；拼 SRT 时 **原文优先用本地 Cue.text**，不用模型 src（更稳） |

**推荐拼双语时：`tr` 用模型，`src` 用本地 `Cue.text`**（仍校验模型 src 作质量信号）。这样「译文上、原文下」的原文永远与输入 SRT 一致。

---

## 8. 实现顺序（编码 checklist）

1. [x] `translate.py`：`parse_srt` / `build_input_json` / `build_instructions` / `compact_glossary`  
2. [x] `validate_response` + offline selfcheck  
3. [x] `build_bilingual_srt`（tr 上 / 本地 text 下）  
4. [x] `run_once` 调 `model_client.call`  
5. [x] `main.py`：`ping` / `selfcheck` / `smoke` / `run` / `bench`  
6. [x] Phase 2 smoke（8 条 × 6 模型并行 agent，均 OK，reasoning=0）  
7. [ ] Phase 3 单模型全量 flash  
8. [ ] Phase 4 bench `--jobs 1`，需要时再 `--jobs 2..6`  

---

## 9. 接口草图（实现时照此签名即可）

```python
# translate.py
def parse_srt(path: Path) -> list[Cue]: ...
def build_input_json(cues: list[Cue]) -> tuple[str, dict[str, str]]: ...
def build_instructions(
    prompt_path: Path,
    glossary_path: Path | None,
    source_language: str = "英语",
    target_language: str = "简体中文",
) -> str: ...
def validate_response(raw: str, input_map: dict[str, str]) -> ValidateReport: ...
def build_bilingual_srt(cues: list[Cue], translations: dict[str, str]) -> str: ...
def run_once(
    srt_path: Path,
    model: str,
    *,
    source_language: str = "英语",
    target_language: str = "简体中文",
    prompt_path: Path = ...,
    glossary_path: Path | None = ...,
    max_cues: int | None = None,
    cue_offset: int = 0,
    max_output_tokens: int = 131072,
    out_dir: Path | None = None,
    timeout: float = 600.0,
) -> TranslateResult: ...
```

```python
# main.py
# smoke | run | bench | ping
```

---

*方案版本：2026-08-05。确认后按 §8 顺序实现。*
