# 字幕翻译质量评估 TQA v2

TQA v2 是 Sub-trans-llm 的多语言字幕翻译质量评测流水线。它将候选翻译、匿名模型评分、分歧复评、聚合判定和可复现产物统一在一份 YAML Profile 中。

本文是公开仓库中的 TQA 使用说明。运行时的固定机器契约位于 `pipeline/tqa/framework_v2.md`，字段约束位于 `pipeline/tqa/profile_v2.schema.yaml`；本文件整理并取代 `sample/` 中两份本地研究草案作为公开入口。

## 快速开始

复制模板，不要直接修改模板来代表某次实验：

```bash
cp pipeline/tqa/profile.default.yaml ./my-tqa-profile.yaml
python3 main.py bench --all --profile "./my-tqa-profile.yaml"
```

`bench --all` 依次完成 `plan`、`collect`、`evaluate` 和 `report`，最后停在 `awaiting_user_decision`，等待人工确认。也可以逐阶段运行：

```bash
python3 main.py bench plan     --profile "./my-tqa-profile.yaml"
python3 main.py bench collect  --profile "./my-tqa-profile.yaml"
python3 main.py bench evaluate --profile "./my-tqa-profile.yaml"
python3 main.py bench report   --profile "./my-tqa-profile.yaml"
python3 main.py bench status   --profile "./my-tqa-profile.yaml"
```

## Profile 的职责

统一 Profile 同时定义：

- 项目名称、源语言和目标语言；
- 一个或多个 episode 的完整源字幕；
- 候选模型和显式采样参数臂；
- 进入 TQA 评分的定向样例及维度；
- evaluator 模型及复评策略；
- TQA 权重、门槛、硬失败和参考模式；
- 输出目录及翻译执行参数。

模板位于 `pipeline/tqa/profile.default.yaml`。模板注释使用三种标记：

- `【必须修改】`：必须按本次作品或实验填写；
- `【按需修改】`：默认可运行，但应根据实验目的确认；
- `【通常保留】`：框架默认值，普通用户不建议修改。

YAML 中 `null` 是空值，不能写成字符串 `"null"`。实际文件路径建议统一使用双引号，例如 `"./path/to/file.srt"`。相对路径以 Profile 所在目录为基准解析。

## 完整字幕与定向样例

每个 `inputs.episodes[]` 表示一个独立评估单元：

```yaml
inputs:
  episodes:
    - id: "E01"
      source_srt: "./episode-01.source.srt"
      samples:
        - cue_id: 120
          dimensions: ["上下文依赖", "情感张力"]
          note: "代词指向依赖前文，且需要保留角色的愤怒语气。"
```

`collect` 始终翻译 `source_srt` 的整份字幕。`samples` 只决定完整候选译文中的哪些 cue 会进入 evaluator，不会截断翻译范围。

TQA v2 使用定向抽样：优先选择高信息密度、高误译风险、高上下文依赖、高情感强度或发生语域切换的条目。普通礼貌用语、无语义负载的短应答和纯动作指示通常无需进入评分样例。样例必须引用源 SRT 中真实存在的 cue id。

当前 Profile 不会自动生成抽样列表。用户或上游工具需要显式填写 `samples`；若要逐条评价整份字幕，则必须把全部 cue 逐条列入，评测调用量和费用会显著增加。

## 参考模式

### 无参考模式

普通用户建议保持：

```yaml
tqa:
  reference_mode: "no_reference"
```

此模式不需要 `reference_role`，evaluator 根据源文、候选译文、上下文、维度定义和测试说明独立评分。

### 单参考模式

只有拥有可靠参考字幕时才使用：

```yaml
inputs:
  episodes:
    - id: "E01"
      source_srt: "./episode-01.source.srt"
      reference_srt: "./episode-01.reviewed.zh.srt"
      samples:
        - cue_id: 120
          dimensions: ["上下文依赖"]
          note: "指代依赖前文。"
    - id: "E02"
      source_srt: "./episode-02.source.srt"
      reference_srt: "./episode-02.reviewed.zh.srt"
      samples:
        - cue_id: 85
          dimensions: ["习语/口语"]
          note: "习语需要采用自然的功能对等表达。"

tqa:
  reference_mode: "single_reference"
  reference_role: "anchor"
```

`reference_role` 在 `single_reference` 时必填，并且只能是 `anchor` 或 `hint`。缺少角色或任一 episode 缺少 `reference_srt`，`bench plan` 都会拒绝冻结实验。

参考文件按 `inputs.episodes[]` 一一对应：一集对应一个 `reference_srt`。Profile 可以包含多集，每集使用自己的参考文件；这不是整个实验共用一个全局参考文件。当前一集最多支持一个参考字幕文件，当前不支持 `multi_reference`。

参考 SRT 至少必须对所有定向样例与对应源 SRT 使用相同的 cue id；每个样例的 `cue_id` 必须同时存在于源字幕和该集参考字幕中，否则 `bench plan` 会拒绝运行。

| 角色 | evaluator 行为 | 适用场景 |
|---|---|---|
| `anchor` | 把参考译文作为可信评分锚点；候选出现实质偏差时扣分，语义等价表达仍可接受 | 参考字幕经过可靠人工审核，可作为标准答案 |
| `hint` | 参考译文只用于辅助理解；独立判断候选质量，不惩罚合理的不同译法 | 参考字幕有帮助，但不应成为唯一正确答案 |

运行时会把当前 episode、当前 cue 对应的 `reference_text`、`reference_role` 和明确的 `reference_instruction` 一起发送给匿名 evaluator，确保两个角色的评分约束不同。

## 候选翻译参数与 evaluator

`sampling.arms[].temperature` 和 `sampling.arms[].top_p` 控制候选翻译模型，是被比较的实验变量。每个 arm 是一个显式组合，不做笛卡尔积；`OMIT` 表示请求体不发送该字段，不能解释为不同 Provider 采用同一默认数值。

`evaluator` 是匿名评分模型，不负责生成候选字幕。它逐一读取源文、候选译文、邻近上下文、评分维度、测试说明及可选参考信息，然后输出：

- 0–10 整数分；
- 硬失败类别；
- 评分理由；
- 置信度；
- 唯一 `evaluator_run_id`。

`evaluator.temperature` 仅控制裁判评分的随机性，与候选翻译的 sampling arms 完全独立。为提高复评一致性，通常使用较低值。当前只支持一个 evaluator 模型，可通过 `runs` 对同一样例和维度进行多轮独立评分。

当多轮分差达到 `divergence_threshold` 时：

- `re_evaluate` 追加 `re_evaluate_extra_runs` 轮评分；
- `flag_only` 只标记分歧；
- 最终按 `median`、`mean` 或 `trimmed_mean` 聚合多轮评分。

## 九个核心维度

| 维度 | 核心问题 |
|---|---|
| 领域术语 | 专业概念、专名和统一译名是否准确一致 |
| 话语体系 | 政治、宗教、阵营或亚文化用语是否保留其立场与内部语义 |
| 长句/复杂句 | 信息、逻辑关系和多层句法是否完整清晰 |
| 习语/口语 | 习语、俚语、粗细程度和口语自然度是否功能对等 |
| 上下文依赖 | 指代、伏笔、角色关系和言外之意是否结合语境处理 |
| 正式语体 | 官方、法律、仪式或学术语域是否准确得体 |
| 非母语口语 | 是否在可理解前提下适度保留非母语特征 |
| 情感张力 | 情绪强度、戏剧节奏和简短台词的冲击力是否保留 |
| 讽刺 | 反话、暗讽、黑色幽默和双关是否仍然成立 |

普通用户可以直接保留模板中的通用维度实例和权重。只有熟悉作品及 TQA 规则、确有作品级评测需求时才建议调整。

## 评分、聚合与判定

每个“样例 × 维度”获得一组 0–10 分。严重度由 Profile 阈值派生：

- 0–3：`CRITICAL`；
- 4–5：`MAJOR`；
- 6–7：`MINOR`；
- 8–10：`PASS`。

实际边界由 `severity_thresholds` 配置。固定聚合路径只有一条：

```text
样例 × 维度原始分
  → 集内维度平均分
  → 维度加权集分
  → 按有效样例数加权的模型分
```

`sample_aggregation` 只生成报告中的样例展示分，用于排序和定位差样例，不进入集分或模型分。

判定优先级固定为：

```text
VETO > FAIL > CONDITIONAL_PASS > PASS
```

所有 `max_*` 字段都是包含上界，即实际值 `<= max_*` 时仍在容忍范围内。`bench --all` 不会把机器判定自动提升为最终人工结论。

## Provider refusal、技术故障与 rescue

- Provider refusal 固定计 0，计入质量分母，并按 Profile 配置标记硬失败；
- 空的 Provider 输出视为 refusal；
- 超时、网络异常和格式失败在重试耗尽后记为 technical failure，单独统计且不进入质量分母；
- rescue 译文进入独立匿名评分 lane，只产生 `rescued_quality_score`，不会覆盖 refusal 主评分，也不进入模型总分。

## 匿名性与可复现性

Evaluator 输入不包含候选模型 alias、Provider、参数臂、原始文件名或路径、refusal/rescue 来源。候选顺序按 `random_seed` 打散，每轮评分使用不同但可复现的种子。

`plan` 会冻结 Profile、Framework、Schema 和输入配置。主要产物包括：

```text
output.root/
  profile.source.yaml
  profile.resolved.yaml
  profile.lock.json
  manifest.json
  progress.json
  cases/
  anonymized/eval_input.jsonl
  blind_map.json
  assessments/
  report.json
  report.md
```

`blind_map.json` 采用私有权限原子写入，只在报告阶段解盲。Evaluator 原始响应及其哈希会保留，便于审计评分解析过程。

## 当前支持边界

- 支持 `no_reference` 和 `single_reference`；当前不支持 `multi_reference`；
- 单参考模式是一集一个参考 SRT，不是多个参考译本；
- 只支持一个 evaluator 模型，多 evaluator 交叉验证尚未实现；
- `samples` 必须显式列出，当前不会自动按比例抽样；
- TQA 面向 SRT cue id 对齐，不执行基于时间轴的模糊参考匹配；
- 最终人工复核策略和跨模型统计显著性检验不属于当前自动流水线。
