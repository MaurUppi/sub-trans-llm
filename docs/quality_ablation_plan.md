# 译文质量诊断与对比测试方案

> 背景：重构后感觉 **qwen3.7-max** 等译文质量下降。  
> 目的：验证 **instructions 三部分是否真实生效**，并设计可重复的批次对比实验，确认主流程稳定且符合预期。  
> 执行脚本：`scripts/ablation_instructions.py`  
> 实测目录：`out/ablation_qwen37max/`（2026-08-05）

---

## 1. 问题假设（优先验证）

| 假设 | 说明 | 优先级 |
|------|------|--------|
| **H1 Glossary 默认关闭** | 重构后 `--glossary` 默认 `None`；旧全量 run 默认注入 `Un_Village_francais_Glossary.md`。专名漂移（马塞尔 vs 马赛尔）会直接表现为「质量下降」 | **P0** |
| **H2 摘要未注入** | `--no-summary` 或摘要失败降级时，instructions 无「本集剧情摘要」节 | P1 |
| **H3 Prompt 模板失效** | 路径错误/未替换变量 → 无 `# Role` / 仍含 `${sourceLanguage}` | P0（应立即失败） |
| **H4 前处理改源** | `--preprocess` 改文/改条后，与历史对照不可比 | P1 |
| **H5 内容审核截断** | qwen 对 Communists 等块 `DataInspectionFailed`，整批缺失 | P1（稳定性） |
| **H6 采样变化** | temp/top_p 与历史不一致 | P2 |

**已确认（代码 + 离线 + API 消融）：H1 高度成立。**

---

## 2. Instructions 三部分：契约与调试探针

`pipeline.prompt.build_instructions` 拼装顺序：

```text
[1] translation_prompt.md（变量替换后）
[2] ## 专有名词（必须遵守，不得另译）  + compact_glossary   ← 仅当 glossary_path 非空
[3] ## 本集剧情摘要（…） + episode_summary               ← 仅当摘要非空
```

### 2.1 磁盘探针（每个 run 目录必查）

路径：`out/<run>/<model>/instructions.txt`

| 部分 | 必须出现的标记 | 相关日志 |
|------|----------------|----------|
| Prompt | `# Role: 资深字幕翻译专家` | `instructions ≈ N chars` |
| Glossary | `## 专有名词（必须遵守，不得另译）` | 有 glossary 时 chars 通常 **+2000+** |
| Summary | `## 本集剧情摘要（翻译时请参考语境与人物状态，勿写入输出 JSON）` | `summary=yes` / `summary_chars=…`；`meta.episode_summary_chars` |

离线断言（无 API）：

```bash
python scripts/ablation_instructions.py   # 仅 offline，应 4 配置全 OK
```

### 2.2 行为探针（译文是否「吃到」该部分）

| 部分 | 探针字幕（样例剧） | 生效判据 |
|------|-------------------|----------|
| Glossary | `Marcel ?` → 表内 **马赛尔**（非「马塞尔」） | 有 glossary 时优先表内译名 |
| Glossary | `CROSSING THE LINE` / `the Line` | 可出现「分界线」等表内用语 |
| Summary | 人物关系依赖上下文的短句 | 有摘要时称谓/关系更稳（软指标） |
| Prompt | 禁 `，。`、口语、2 行 | `validate.json` 软警告 + 抽检 |

---

## 3. 消融配置（推荐 4 组）

固定：**模型、切片、batch_size、temp/top_p、无 preprocess**，只变 instructions 组成。

| ID | Prompt | Glossary | Summary | CLI 要点 |
|----|:------:|:--------:|:-------:|----------|
| **A_prompt** | ✓ | ✗ | ✗ | 默认无 glossary + `--no-summary` |
| **B_gloss** | ✓ | ✓ | ✗ | `--glossary docs/Un_Village_francais_Glossary.md --no-summary` |
| **C_summary** | ✓ | ✗ | ✓ | 默认摘要，无 glossary |
| **D_full** | ✓ | ✓ | ✓ | `--glossary …`（**接近旧版全量配置**） |

### 3.1 建议切片（合理批次）

| 切片 | cue-offset | max-cues | 用途 |
|------|------------|----------|------|
| **tiny** | 0 | 8 | 快速冒烟 + 三节探针 + 专名 Marcel |
| **open** | 0 | 30 | 开场名物（Schwartz、Meyer、锯木厂） |
| **names** | 100 | 50 | 警察线、Communists、Marcel Larcher（**qwen 易审核拦截**） |
| **mid** | 300 | 50 | 常规对白稳定性 |
| **tail** | 700 | 47 | 收尾稳定性 |

单批 `batch_size = max-cues`，`batch_jobs=1`，排除并行干扰。

### 3.2 一键执行

```bash
# 离线：三节拼装
python scripts/ablation_instructions.py

# API：tiny 四配置（qwen3.7-max）
python scripts/ablation_instructions.py \
  --model qwen3.7-max --slice tiny --run \
  --out out/ablation_qwen37max

# names 切片（若 DataInspectionFailed → 改用 repair --sub-batch-size 或 flash 作对照）
python scripts/ablation_instructions.py \
  --model qwen3.7-max --slice names --configs A_prompt,D_full --run \
  --out out/ablation_qwen37max
```

产物：`out/ablation_qwen37max/report_<slice>.json` + 各配置 `instructions.txt` / `parsed.json` / `deliver_zh.srt`。

---

## 4. 阶段生效检查清单（全链路）

### Stage A（仅当 `--preprocess`）

| 检查 | 方法 |
|------|------|
| 是否进入 A | 日志 `Stage A done` / 存在 `*.clean.srt` |
| fix-overlaps | `preprocess_meta.json` → `steps.fix_overlaps.detected/applied` |
| SDH/口癖 | meta `steps.remove_sdh` / `remove_disfluency` |
| resplit | `steps.resplit.applied` + 条数 in/out |
| 默认无 preprocess | 样例剧 **747→747** 且无 clean 旁路 |

### Stage B（翻译）

| 检查 | 方法 |
|------|------|
| 输入源 | 日志 `加载 SRT: …clean.srt` 或原文件 |
| 摘要 | `summary=yes` + `episode_summary.txt` + instructions 含摘要节 |
| Glossary | instructions 含专有名词节 **当且仅当** 传了 `--glossary` |
| 批译 | `batches=…` 各 `batch_XX/parsed.json` |
| 校验 | `validate.ok`、`n_out == n_in` |
| 交付 | 默认 `{stem}_zh.srt` 或 `--output` |

### 稳定性

| 检查 | 方法 |
|------|------|
| 同配置复跑 | tiny D_full 跑 2 次，专名一致率（允许 paraphrasing 非专名） |
| 审核失败 | names 切片失败时 repair 子批能否恢复 |
| 无摘要降级 | 强制摘要失败路径时仍完成批译（软降级） |

---

## 5. 实测结果摘要（2026-08-05，qwen3.7-max）

### 5.1 离线拼装

四配置 section 标记 **全部与预期一致**（A/B/C/D）。

### 5.2 tiny（8 条）API 消融 — **全部成功**

| 配置 | instructions 探针 p/g/s | chars | Marcel(1) | 备注 |
|------|-------------------------|------:|-----------|------|
| A_prompt | 1/0/0 | 1731 | **马塞尔？** | 无表 → 自由译名 |
| B_gloss | 1/1/0 | 4210 | **马赛尔？** | **表内名生效** |
| C_summary | 1/0/1 | 2157 | 马塞尔？ | 有摘要、无表 |
| D_full | 1/1/1 | 4642 | **马赛尔？** | 全量三节；`CROSSING THE LINE`→「跨越分界线」 |

**结论：**

1. **三部分均可按开关真实写入 `instructions.txt`。**  
2. **Glossary 对专名有可观测因果效应**（马塞尔→马赛尔）。  
3. 重构后若按 CLI 默认（无 glossary + 有 summary）跑剧集，**等价于 C**，相对旧默认 **D 会表现为专名质量下降** —— 这与「译文质量下降」高度吻合，**不一定是模型变差**。  
4. 摘要节存在时 chars 增加、`summary=yes`；tiny 上摘要对 Marcel 字面影响弱于 glossary（符合预期：摘要偏语境，表偏硬约束）。

### 5.3 names（offset=100, 50 条）— **整批 DataInspectionFailed**

A 与 D 均在 50 条含 Communists 等内容时输出审核失败。  
→ 该切片用于质量对比前，需 **sub-batch repair** 或换 flash/对照模型；不能单独用「失败」否定 instructions 注入。

---

## 6. 推荐「质量对照」工作流（剧集）

### 6.1 公平对比旧全量（恢复历史 instructions）

```bash
python main.py run \
  --model qwen3.7-max \
  --srt A.French.Village.S01E03.Passer.la.ligne_eng.srt \
  --glossary docs/Un_Village_francais_Glossary.md \
  --batch-size 30 --batch-jobs 3 \
  --out out/run_qwen37max_quality_D
# 勿加 --preprocess（除非要测脏字幕）
```

与 `out/run_qwen37max_full`（旧 D 配置）抽检专名表一致率。

### 6.2 最小质量门禁（CI/手跑）

1. `python scripts/ablation_instructions.py`（离线）  
2. `… --slice tiny --run`（4 配置，&lt;1 min）  
3. 断言：B/D 的 Marcel 为「马赛尔」；A/C 允许「马塞尔」  
4. 断言：各 run `instructions` 三节标记与配置一致  
5. （可选）`repair` 覆盖 names 敏感批后人工抽检

### 6.3 全量稳定性（qwen3.7-max）

```bash
python main.py run --model qwen3.7-max \
  --glossary docs/Un_Village_francais_Glossary.md \
  --batch-size 30 --batch-jobs 3 \
  --out out/run_qwen37max_full_recheck
# 失败批：
python main.py repair --run-dir out/run_qwen37max_full_recheck/qwen3.7-max \
  --model qwen3.7-max --sub-batch-size 10 \
  --glossary docs/Un_Village_francais_Glossary.md
```

验收：`validate n_out=747`，专名抽检表通过率 ≥ 阈值（自定）。

---

## 7. 产品/默认值建议

| 项 | 建议 |
|----|------|
| 剧集 benchmark | **显式** `--glossary docs/Un_Village_francais_Glossary.md`，与历史可比 |
| 通用产品默认 | 可保持「无 glossary」；README 写明「专名表需显式传入」 |
| 文档 | `README` / `quality_control` 同步：默认无表 ≠ 关闭能力 |
| 调试 | 固定用 `scripts/ablation_instructions.py` 做回归，避免口头「感觉变差」 |

---

## 8. 结论（直接回答疑问）

1. **各阶段是否生效？**  
   - **Prompt / Glossary / Summary 拼装与落盘：是**（离线 + tiny 实测）。  
   - **Glossary 对译文：是**（Marcel 译名消融）。  
   - **Summary：已注入**；短开场上对专名字面弱于 Glossary。  
   - **Preprocess：默认不跑**；仅 `--preprocess` 时 Stage A 生效（样例 747 恒等已验）。  

2. **质量下降主因（当前证据）**  
   - 默认 **不再自动带 Glossary** → 专名与剧集术语一致性下降，易被感知为质量变差。  
   - 应用 **D_full 配置** 再与旧产物对比。  

3. **对比测试**  
   - 用本文 §3–§6 + `scripts/ablation_instructions.py` 做四配置 × 多切片；敏感切片配合 repair。  

---

## 9. 相关路径

| 路径 | 作用 |
|------|------|
| `scripts/ablation_instructions.py` | 离线探针 + API 消融 |
| `out/ablation_qwen37max/` | 实测报告与各配置产物 |
| `pipeline/prompt.py` | 三节拼装 |
| `docs/Un_Village_francais_Glossary.md` | 剧集术语表 |
| `docs/translation_prompt.md` | Prompt 基线 |
| `docs/subtitle_preprocess_plan.md` | 前处理 Stage A 方案 |

---

## 10. 预期输出报告（Report Schema）

> 适用：对齐 **配置 D**（prompt + glossary + summary，无 preprocess）；  
> 本次战役默认：`A.French.Village.S04E01.Le.Train_eng.srt`，`--batch-jobs 1`，输出根目录 `out/quality_ablation_test/`。

### 10.1 交付文件

| 文件 | 说明 |
|------|------|
| `out/quality_ablation_test/report_D_full.json` | 机器可读完整报告 |
| `out/quality_ablation_test/report_D_full.md` | 人读摘要 |
| `out/quality_ablation_test/D_full/<model>/` | 标准 run 产物（instructions / summary / batches / parsed / validate / meta / bilingual） |
| `out/quality_ablation_test/{stem}_zh.srt` | 成片双语（与战役 out 根或 `--output`） |

生成：

```bash
python scripts/quality_report_d.py --run \
  --srt A.French.Village.S04E01.Le.Train_eng.srt \
  --model qwen3.7-max \
  --batch-jobs 1 \
  --batch-size 30 \
  --out out/quality_ablation_test
```

### 10.2 `report_D_full.json` 必须包含的信息

| 块 | 字段 | 含义 / 预期（D） |
|----|------|------------------|
| **campaign** | `config_id=D_full` | 指令三节全开 |
| **run** | `model`, `srt`, `batch_jobs`, `batch_size`, `preprocess=false`, `glossary_path` | 可复现实验参数 |
| **stage_A** | `enabled=false`（本战役） | 未前处理则注明 |
| **stage_B.instructions** | `has_prompt/has_glossary/has_summary`、`chars`、`has_unreplaced_vars` | **三者均 True**；无 `${…}` |
| **stage_B.instructions_ok** | bool | 三节探针总判 |
| **stage_B.episode_summary** | `chars`, `preview`, `file_exists` | 摘要真实落盘 |
| **stage_B.meta** | `ok`, `status`, `batch_count/size/jobs`, `usage`, `elapsed_sec` | 运行健康度 |
| **stage_B.validate** | `ok`, `stats.n_in/n_out/n_tr_ok`, `errors`, `warnings_count` | **n_out==n_in 且 ok** |
| **stage_B.batches** | `dirs`, `ok` | 批次数与成功批 |
| **stage_B.netflix_soft** | tr 含 `，。` / `\|` / 坏省略号计数 | 软规范抽检 |
| **stage_B.glossary_probes** | `matched_cues`, `hits`, `rate`, `misses[]` | 表内专名是否出现在译文 |
| **artifacts** | 各关键文件绝对/相对路径 | 可点击复核 |
| **verdict** | `pipeline_stable`, `instructions_three_parts`, `coverage_complete`, `notes[]` | 总判定 |

### 10.3 Markdown 报告章节结构

1. **Verdict** — 是否稳定、三节是否齐、覆盖是否完整  
2. **Instructions 三部分** — 表格式 present vs expect  
3. **Coverage / validate** — n_in/n_out、errors、usage、耗时  
4. **Netflix soft** — 标点软违规计数  
5. **Glossary probes** — 命中率 + miss 样例  
6. **Artifacts** — 路径清单  

### 10.4 通过标准（本战役）

| 项 | 标准 |
|----|------|
| instructions 三节 | prompt ∧ glossary ∧ summary 均为 true |
| 覆盖 | `validate.ok` 且 `n_out == n_in`（全量 695 或切片 max_cues） |
| batch_jobs | meta 中为 **1** |
| glossary | `glossary_path` 非空；专名探针 rate 作参考（非硬失败，但 miss 需列样例） |
| 无 preprocess | stage_A.enabled = false |

---
