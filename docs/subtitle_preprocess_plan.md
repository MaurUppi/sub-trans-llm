# 字幕前处理与联动翻译方案（Preprocess Plan）

> 状态：方案稿（修订）  
> 日期：2026-08-05  
> 约束：Netflix 简中 Timed Text + `translation_prompt.md` 仍为**翻译与成片基线**。  
> **清洗 / 重切 / 调轴只在 Stage A（`run_once` 之前）**；Stage B 翻译 **禁止再拆并**，形成固定「输入真相」。

---

## 0. 决策共识（本次修订）

| # | 决策 | 说明 |
|---|------|------|
| 1 | **代码入库** | `sub_processor` 与 VideoCaptioner Split/Optimize **相关代码**（完整 vendoring 或抽包）放入 `pipeline/` 下（见 §3） |
| 2 | **Stage 闸门** | 改条数 / 时间轴 / 源文清洗 **仅 Stage A**；Stage B = 现有 `run_once` 1:1 |
| 3 | **成片命名** | 默认输出 **`{原字幕 stem}_zh.srt`**，与源 SRT **同目录**（替代默认写到 `out/.../bilingual.srt` 作为用户主交付物） |
| 4 | **Glossary** | **非强制**；默认无术语表；用户 **显式** `--glossary PATH` 才注入 |
| 5 | **fix-overlaps** | **自动检测**时间轴重叠 → 命中则启用；也可 **显式** `--fix-overlaps` / `--no-fix-overlaps` |
| 6 | **CLI 形态** | Stage A / B 可 **分跑或联动**；**不设** `--preprocess-profile`；主路径即：**原始字幕 → 前处理 → 翻译 → 输出双语** |
| 7 | **VC 复用清单** | 严格对齐 VideoCaptioner-SubtitleSpliter-Study.md **「四、相关文件速查」** 所列模块（见 §3.2） |

---

## 1. 目标与非目标

### 1.1 目标

1. 脏字幕可进主流程：SDH、口癖、超长、重叠轴、句界混乱等。  
2. 复用规则引擎（sub_processor）+ 语义断句/优化（VideoCaptioner），代码落在本仓库 `pipeline/`。  
3. 前处理完成后 **只** 走现有翻译栈（`pipeline.orchestrator` / `main.py`）。  
4. 基线文档不变：
   - `docs/Netflix-Chinese_(Simplified)_Timed_Text_Style_Guide.md`
   - `docs/translation_prompt.md`

### 1.2 非目标

- 翻译批内改 id / 拆并条。  
- 模型「边译边断句」。  
- 用 profile 枚举代替「检测 + 显式开关」。  
- 默认强制 Glossary。  
- 长期从 `docs/sub_processor.py` 运行时 import（应迁入 `pipeline/`）。  
- 对 **译文** 使用 sub_processor 中文补 `。`（违 Netflix 简中）。

---

## 2. 两阶段契约

```text
原始 .srt
    │
    ▼
┌──────────────────────────────────────────────┐
│ Stage A · Preprocess（可改条 / 改轴 / 改源文） │
│  自动或显式步骤 → clean 语义 + 时间轴          │
│  产物：规范化源 SRT（内存或旁路文件）+ meta    │
└──────────────────────┬───────────────────────┘
                       │  id 从 0 重排 = 新「输入真相」
                       ▼
┌──────────────────────────────────────────────┐
│ Stage B · Translate（禁止拆并）                │
│  现有：摘要 → 分批 JSON 译 → 校验 → 写出       │
│  默认交付：{stem}_zh.srt（与源同目录）         │
└──────────────────────────────────────────────┘
```

- **分跑**：只 A、只 B（B 的输入已是干净 SRT）。  
- **联动**：一次 CLI：原始 SRT 进 → A → B → `{stem}_zh.srt`。  
- **六模型对比**：各模型必须共享 **同一 Stage A 结果**（联动时内部只做一次 A）。

---

## 3. 代码落盘：`pipeline/` 内布局

### 3.1 总目录

```text
pipeline/
  # —— 现有翻译域 ——
  orchestrator.py / repair.py / srt_io.py / prompt.py / ...

  # —— 前处理域（新增）——
  preprocess/
    __init__.py              # run_preprocess / run_pipeline(A+B)
    config.py                # PreprocessConfig（开关，无 profile 枚举）
    types.py                 # PreprocessResult, OverlapStats, ...
    detect.py                # 重叠检测、超长检测等启发式
    bridge.py                # Cue ↔ SubtitleBlock / ASRData
    export_clean.py          # 写出规范化源 SRT + preprocess_meta
    orchestrate_a.py         # Stage A 步骤编排

  # —— vendored / 抽取的上游实现 ——
  rules/                     # 来自 docs/sub_processor.py
    __init__.py
    sub_processor.py         # 完整迁入或按需裁剪后的规则引擎
    # 或拆成 models.py parser.py sdh.py overlaps.py split_rules.py ...

  vc_split/                  # 来自 VideoCaptioner core/split（见 §3.2）
    __init__.py
    split.py
    split_by_llm.py
    # prompts 可放 vc_split/prompts/ 或 pipeline/prompts/split/

  vc_optimize/               # 来自 VideoCaptioner core/optimize
    __init__.py
    optimize.py
    # alignment 若 Optimize 依赖则一并 vendoring

  vc_asr_data/               # 可选：词级判定 / split_to_word_segments
    asr_data.py              # 从 videocaptioner/core/asr/asr_data.py 抽取所需部分
```

说明：

- **优先** 完整文件 vendoring + 薄适配，减少「剪坏」依赖；若体积/依赖过大再抽最小子集。  
- 注明上游来源、版本/commit、许可证（VideoCaptioner 仓库协议）。  
- **不**要求安装完整 VideoCaptioner GUI；LLM 调用优先对接本仓库 `model_client`（适配层替换其 `call_llm`）。

### 3.2 VideoCaptioner「四、相关文件速查」→ 本仓库映射

> **实际落地结果（2026-08-05 修订）**：下表是最初的 vendoring 计划。实施后发现
> vendored 的 `.py` 全部无法 import（依赖上游的 `videocaptioner.*` / `pipeline.llm` /
> `json_repair` / `langdetect`），且它们走的是 **Chat Completions**，与本仓库统一的
> Responses API 关思考机制不兼容。因此已删除全部 vendored Python，**只保留 prompt**：
>
> | 现存路径 | 用途 |
> |---|---|
> | `pipeline/prompts/split/sentence.md` | 断句 system prompt（`vc_split_adapter` 使用） |
> | `pipeline/prompts/split/semantic.md` | 语义分段，保留未接入 |
> | `pipeline/prompts/optimize/subtitle.md` | 优化 system prompt（`vc_optimize_adapter` 使用） |
>
> 断句/优化逻辑由 `pipeline/preprocess/vc_split_adapter.py`、`vc_optimize_adapter.py`
> 自行实现，模型访问一律经 `model_client.call`（Responses API）。

依据 `VideoCaptioner-SubtitleSpliter-Study.md` §四：

| 上游路径 | 作用 | 本仓库落点 |
|----------|------|------------|
| `videocaptioner/core/split/split.py` | 断句总控、分块、对齐、规则降级 | `pipeline/vc_split/split.py` |
| `videocaptioner/core/split/split_by_llm.py` | LLM 断句 + Agent Loop | `pipeline/vc_split/split_by_llm.py` |
| `videocaptioner/core/split/alignment.py` | 优化后文本对齐（`SubtitleAligner`） | `pipeline/vc_split/alignment.py` 或与 optimize 共用 `pipeline/vc_optimize/alignment.py` |
| `videocaptioner/core/optimize/optimize.py` | 字幕优化 + 验证 | `pipeline/vc_optimize/optimize.py` |
| `videocaptioner/core/prompts/split/sentence.md` | 断句 system prompt | `pipeline/vc_split/prompts/sentence.md`（固定使用；`semantic.md` 可选后续） |
| `videocaptioner/core/prompts/split/semantic.md` | 语义分段（上游未接入） | 可 vendoring 但 **默认不用** |
| `videocaptioner/core/prompts/optimize/subtitle.md` | 优化 system prompt | `pipeline/vc_optimize/prompts/subtitle.md` |
| `videocaptioner/core/asr/asr_data.py` | 词级判定、句→词拆分 | `pipeline/vc_asr_data/asr_data.py`（仅所需 API） |
| `ui/thread/subtitle_thread.py` / `cli/...` | 上游编排 | **不** vendoring；由本仓库 `preprocess/orchestrate_a.py` + `main.py` 编排 |
| `tests/test_split/`、`tests/test_optimize/` | 上游测试 | 择要迁入 `tests/preprocess/` 或对照改写 |

### 3.3 sub_processor 落点

| 来源 | 落点 |
|------|------|
| `docs/sub_processor.py`（全文或裁剪） | `pipeline/rules/sub_processor.py`（推荐先全文迁入，再逐步拆文件） |

迁入后：`docs/sub_processor.py` 可删或保留为「已迁移」说明指针，**运行时只 import `pipeline.rules`**。

迁入时改造清单：

- 关闭默认对中文源的「补 `。`」在联动路径上的使用（配置 `no_punct_fix=True` 当语种为译出侧时；源英用 EnglishProcessor）。  
- 暴露细粒度 API：`detect_overlaps` / `fix_overlaps` / `remove_sdh` / `split_overlong`，供 Stage A 逐步调用，而不仅是 `process_file` 一把梭。

---

## 4. Stage A 步骤与开关（无 profile）

固定顺序；**每步：自动启发式 和/或 显式 CLI 标志**。

| 序 | 步骤 | 行为 | 默认 |
|:--:|------|------|------|
| A0 | 编码规范化 | BOM / utf-8 | 总是 |
| A1 | **fix-overlaps** | 见 §5 | **自动检测**；可强制开/关 |
| A2 | remove-sdh | SDH 块/标记 | 显式 `--remove-sdh`（或 `--sdh`）；默认 **关** 亦可改为「检测到 SDH 模式再开」——建议：**显式默认关，检测命中时 log 提示用户**，避免误删对白括号 |
| A3 | remove-disfluency | 口癖规则 | 显式 `--remove-disfluency`；默认关 |
| A4 | optimize | VC Optimize（LLM） | 显式 `--optimize`；需模型；默认关 |
| A5 | resplit | 见 §6 | **自动**：存在词级戳 → VC Split；否则若检测超长 → rules split；可 `--resplit` / `--no-resplit` |
| A6 | report | cps/行长 | 总是写 report（不阻断） |
| A7 | 交付 clean | 内存 Cue 或旁路 `*.clean.srt` | 联动时可用临时文件 |

**不引入** `--preprocess-profile` / `dirty_en_asr` 等套餐名；需要组合时用户叠开关即可。

---

## 5. fix-overlaps：自动检查

### 5.1 检测启发式（建议）

对相邻块 \(i, i+1\)：

- 若 `end_i > start_{i+1}`（严格时间重叠），计一次 overlap。  
- 可选：重叠时长 ≥ 阈值（如 50ms）才计，避免浮点噪声。  
- 统计：`overlap_count`、`overlap_ratio = overlap_count / max(1, n-1)`。

### 5.2 启用策略

| 模式 | 行为 |
|------|------|
| **auto（默认）** | `overlap_count > 0`（或 ratio ≥ ε）→ **自动执行** fix；否则跳过 |
| **强制开** | `--fix-overlaps` → 总是跑（幂等） |
| **强制关** | `--no-fix-overlaps` → 永不跑 |

`preprocess_meta` 记录：`detected_overlaps`、`fix_overlaps_applied`、原因。

---

## 6. resplit 后端选择（自动，无 profile）

```text
if --no-resplit:
    skip
elif 提供词级时间戳（side-car / 内嵌 / ASRData）:
    pipeline.vc_split（LLM sentence.md + 对齐；失败 → 规则降级）
elif 检测超长（英文行/词超阈 或 >2 行）:
    pipeline.rules timeline/gap 拆条 + 碎片回并
elif --resplit:
    强制 rules（或 VC 均分词戳 fallback，并 meta 警告）
else:
    skip
```

阈值可配置，默认对齐上游：

- VC：`MAX_WORD_COUNT_ENGLISH≈18` 词/段，CJK≈25（源为中文时）  
- rules：sub_processor 英文 42 字符/行、最多 2 行等  

---

## 7. Stage B 与交付物

### 7.1 翻译逻辑

沿用现有：`run_once`（摘要、分批、校验、repair 能力不变）。  
输入 SRT = Stage A 的「输入真相」（联动时内部传递；分跑时用户给已清洗路径）。

### 7.2 输出规范调整（成片）

| 项 | 旧（benchmark） | 新（产品默认） |
|----|-----------------|----------------|
| 主交付文件名 | `out/.../bilingual.srt` | **`{源文件stem}_zh.srt`** |
| 默认目录 | `out/<run>/<model>/` | **与源 SRT 相同目录** |
| 例 | `episode_eng.srt` → | `episode_eng_zh.srt` 同目录 |

补充：

- **benchmark / 调试** 仍可写 `out/<run>/...` 的完整产物（meta、batch、parsed）；主交付 `_zh.srt` 可额外 copy 或仅当用户未指定 `--out` 时用同目录规则。  
- 建议 CLI：
  - 默认：`{parent}/{stem}_zh.srt`
  - `--output PATH` 覆盖最终双语路径  
  - `--work-dir PATH` 可选：批处理/meta 工作目录（默认 `out/...` 或临时目录）

双语内容格式不变：时间码保留；**中文在上、源文在下**（与现 `build_bilingual_srt` 一致）；规范仍服从 Netflix 简中 + prompt。

### 7.3 Glossary

| 项 | 行为 |
|----|------|
| 默认 | **不加载**任何 Glossary 文件 |
| 启用 | 用户 **`--glossary PATH`**（非空路径且文件存在） |
| 空字符串 / 省略 | 与「无术语表」相同 |
| 与旧默认差异 | 旧代码默认 `Un_Village_francais_Glossary.md` → **改为默认 None** |

`build_instructions(..., glossary_path=None)` 已支持；改 CLI/main 默认值即可。

---

## 8. CLI 设计（分跑 + 联动，无 profile）

### 8.1 子命令

```bash
# —— 仅 Stage A ——
python main.py preprocess \
  --srt /path/to/raw.srt \
  [--work-dir out/preprocess/xxx] \
  [--fix-overlaps | --no-fix-overlaps] \
  [--remove-sdh] [--remove-disfluency] \
  [--optimize] [--resplit | --no-resplit] \
  [--words path/to/word_timestamps.json] \
  [--model deepseek-v4-flash]   # optimize/split LLM 时需要

# —— 仅 Stage B（输入已是干净源）——
python main.py run \
  --srt /path/to/clean_or_raw.srt \
  --model deepseek-v4-flash \
  [--glossary docs/Un_Village_francais_Glossary.md] \
  [--output /path/to/custom_zh.srt] \
  ...

# —— 联动：原始 → A → B → {stem}_zh.srt ——
python main.py run \
  --srt /path/to/raw.srt \
  --preprocess \
  --model deepseek-v4-flash \
  [--fix-overlaps | --no-fix-overlaps] \
  [--remove-sdh] ... \
  [--glossary PATH] \
  [--output PATH]
```

说明：

- **`--preprocess`**：联动开关；为 true 时在 `run_once` 前跑 Stage A。  
- **不要** `--preprocess-profile`。  
- 默认 **不** preprocess 时行为接近现状（便于 747 条规范样例）；用户要「一条龙」时加 `--preprocess` 与各清洗开关。  
  - 若产品坚持「永远先过 A0+auto overlaps」：可在无 `--preprocess` 时仍跑 **极轻量** A0 + auto fix-overlaps only；全文清洗仍要 `--preprocess` 或显式步。**推荐**：`run` 默认仅 A0（编码）；`--preprocess` 打开完整 A 编排 + auto overlaps/resplit 检测。

### 8.2 推荐默认（联动 `--preprocess` 时）

| 项 | 默认 |
|----|------|
| fix-overlaps | **auto** |
| remove-sdh | false（可加检测提示） |
| remove-disfluency | false |
| optimize | false |
| resplit | **auto**（有词级 / 超长才动） |
| glossary | **无** |
| 输出 | `{stem}_zh.srt` 同源目录 |

### 8.3 库 API 草图

```python
# Stage A
result_a = run_preprocess(srt_path, PreprocessConfig(...))
# result_a.clean_path / result_a.cues / result_a.meta

# Stage B
result_b = run_once(srt_path=result_a.clean_path, model=..., glossary_path=None, ...)
write_zh_srt(result_b, source_srt_path)  # → {stem}_zh.srt

# 联动
result = run_translate_pipeline(srt_path, model=..., preprocess=True, ...)
```

---

## 9. 与基线文档边界

| 阶段 | 规则来源 |
|------|----------|
| Stage A 源语 | 英文行长/词数、SDH、重叠；**不用**中文 16 字切英源 |
| Stage B `tr` | **全文** `translation_prompt` + Netflix 简中（1:1、禁 `，。`、16 字/2 行、`…`、`-` 说话人） |
| Stage B `src` | Stage A 输出原文逐字回显 |
| Glossary | 仅用户显式指定时注入 instructions |

---

## 10. 风险与缓解

| 风险 | 缓解 |
|------|------|
| Vendoring VC 依赖过重 | 抽 split/optimize/asr_data + prompts；LLM 走 `model_client` |
| auto overlaps 误触发 | 阈值 + meta 可审计；`--no-fix-overlaps` |
| auto resplit 误切 | 超长阈值偏保守；`--no-resplit` |
| 同目录写 `_zh.srt` 覆盖 | 存在则备份或 `--output`；文档说明 |
| Glossary 默认变更 | 破坏旧默认剧集术语 → changelog 写明；剧集脚本显式传 glossary |
| 工作区 vs 交付分离 | `work-dir` 存 batch/meta；交付只放 `_zh.srt` |

---

## 11. 实施阶段（修订）

| 阶段 | 内容 | 验收 |
|------|------|------|
| **P0** | 迁入 `pipeline/rules`（sub_processor）；`detect_overlaps` + auto/强制 fix；Glossary CLI 默认 None；`{stem}_zh.srt` 写出逻辑 | 单元测重叠检测；无 glossary 时 instructions 无术语节 |
| **P1** | Stage A 编排 + `preprocess` 子命令；SDH/disfluency 显式开关 | 分跑 A 产出 clean |
| **P2** | rules resplit auto（超长） | 超长英 SRT 条数变化合理 |
| **P3** | Vendoring VC optimize + 对接 model_client | `--optimize` 条数不变 |
| **P4** | Vendoring VC split + asr_data + sentence.md | 有词级戳时语义切 |
| **P5** | `run --preprocess` 联动；默认交付同源 `_zh.srt` | 一条龙：raw → `_zh.srt`；Flash smoke |

测试：

- 检测：人造重叠轴 → auto 启用 fix。  
- 联动：raw → 工作区 meta + 同目录 `_zh.srt`。  
- 回归：无 `--preprocess` + 显式 glossary 时剧集路径与现网一致（若需术语）。  
- 无 glossary：instructions 仅 prompt。

---

## 12. 决策摘要

1. **代码**：`sub_processor` → `pipeline/rules/`；VC Split/Optimize/相关 prompts/asr_data/alignment → `pipeline/vc_*`，对齐 Study 文档速查表。  
2. **闸门**：改结构只在 A；B 严格 1:1。  
3. **交付**：默认 **`{stem}_zh.srt` 与源同目录**。  
4. **Glossary**：默认无；**显式**才有。  
5. **overlaps**：默认 **检测后自动修**；可强制开/关。  
6. **CLI**：可分可合；**一条龙** = 原始 SRT + 前处理 + 翻译 + 双语；**无 profile**。  
7. **基线**：Netflix 简中 + `translation_prompt` 只管译文与译时 1:1。

---

## 13. 相关文档

| 文档 | 角色 |
|------|------|
| `docs/Netflix-Chinese_(Simplified)_Timed_Text_Style_Guide.md` | 简中 Timed Text 基线 |
| `docs/translation_prompt.md` | 翻译 system 基线 |
| `docs/quality_control.md` | API/采样/JSON 协议 |
| `docs/sub_processor.py` | 迁入前参考 → `pipeline/rules` |
| `VideoCaptioner/.../VideoCaptioner-SubtitleSpliter-Study.md` | Split/Optimize 设计与文件清单 |
| `README.md` | 用户入口（实现后同步 CLI） |

---

*修订后作为实现依据；落地按 P0–P5 分 PR，并更新 README / quality_control 中与 glossary 默认、输出路径相关的描述。*
