# VideoCaptioner 字幕断句 / 优化实现分析

> 仓库：https://github.com/WEIFENG2333/VideoCaptioner  
> 本地路径：`VideoCaptioner/`  
> 分析范围：字幕智能断句（Split）与字幕优化（Optimize）

---

## 总览：两条独立能力

整体流水线（GUI `SubtitleThread` / CLI `subtitle` 命令）：

```
ASR 字幕
  → [可选] 拆成词级时间戳
  → [可选] 智能断句 (SubtitleSplitter)
  → [可选] 字幕优化 (SubtitleOptimizer)
  → [可选] 翻译
```

| 能力 | 核心模块 | 作用 | 依赖 |
|------|----------|------|------|
| **断句** | `core/split/` | 重划句段边界 + 对齐时间戳 | 词级时间戳 + LLM（可降级规则） |
| **优化** | `core/optimize/` | 纠错、去语气词、标点/术语 | LLM |

---

## 一、断句（Split）怎么实现

### 1. 入口与前置条件

入口位置：

- GUI：`videocaptioner/ui/thread/subtitle_thread.py`
- CLI：`videocaptioner/cli/commands/subtitle.py`

断句只在**词级时间戳**上做：

1. ASR 若已有词级时间戳 → 直接用  
2. 否则 `need_split` 时调用 `ASRData.split_to_word_segments()`：按音素粗估，把句级时间戳均分到词  

词级判定（`is_word_timestamp`）：约 80% 以上片段符合「英文单词 / CJK 1–2 字」模式。

```python
# subtitle_thread.py 核心逻辑（摘要）
if subtitle_config.need_split and not asr_data.is_word_timestamp():
    asr_data.split_to_word_segments()

if asr_data.is_word_timestamp():
    splitter = SubtitleSplitter(...)
    asr_data = splitter.split_subtitle(asr_data)
```

### 2. 主流程：`SubtitleSplitter.split_subtitle`

文件：`videocaptioner/core/split/split.py`

处理步骤：

1. 读字幕 → 确保词级时间戳  
2. 预处理：去除纯标点段；对空格分隔语言补空格  
3. 按字数切成大块（约每 **500** 词一块），在时间空隙最大处切  
4. 线程池并发：每块优先 LLM 断句，失败则规则降级  
5. 合并结果并按 `start_time` 排序  

关键常量：

| 常量 | 默认值 | 含义 |
|------|--------|------|
| `MAX_WORD_COUNT_CJK` | 25 | CJK 单行最大字数 |
| `MAX_WORD_COUNT_ENGLISH` | 18 | 英文单行最大词数 |
| `SEGMENT_WORD_THRESHOLD` | 500 | 长文分块阈值 |
| `MAX_GAP` | 1500 ms | 合并后时间跨度过大则再拆 |
| `MATCH_SIMILARITY_THRESHOLD` | 0.5 | 句-词对齐相似度下限 |

### 3. LLM 断句：`split_by_llm` + Agent Loop

文件：`videocaptioner/core/split/split_by_llm.py`  
Prompt：`videocaptioner/core/prompts/split/sentence.md`

**Prompt 约束：**

- 在自然停顿 / 语义断点插入 `<br>`
- **禁止增删改原文、禁止翻译**，仅插入分隔符
- 遵守 CJK / 英文长度上限
- 倒计时、关键信息揭示前等位置可适当分割

**Agent Loop（最多 `MAX_STEPS = 2` 轮）：**

1. 调用 LLM → 按 `<br>` 切成句子列表  
2. `_validate_split_result` 校验：
   - 合并后与原文 **相似度 ≥ 96%**（`difflib.SequenceMatcher`，防止改字）
   - 每段不超过字数限制  
3. 失败则把差异反馈给模型，要求「只修错误、输出完整带 `<br>` 文本」  
4. 全部失败则退回 `[原文整段]`

> 注：仓库另有 `prompts/split/semantic.md`（更细的语义分段），但代码中**固定使用** `split/sentence`。

### 4. 把 LLM 句子「贴回」词级时间戳

LLM 只返回文本段，**没有时间戳**。  
`_merge_segments_based_on_sentences` 用滑动窗口 + `SequenceMatcher` 匹配回 ASR 词序列：

1. 对每个 LLM 句子，在词序列上找最佳窗口（相似度 ≥ 0.5）  
2. 匹配成功 → 合并这些词的 `start_time` / `end_time`  
3. 若中间时间空隙过大（> 1500ms）→ 再按空隙拆开  
4. 仍超长 → `_split_long_segment`（在最大时间间隔处切）  
5. 连续未匹配句子超过阈值则中止  

**设计本质：语义边界由 LLM 定，时间轴仍来自 ASR 词级戳。**

### 5. 规则降级（LLM 失败时）

`_process_by_rules`：

1. **按时间间隔分组**（默认 500ms；可选检测异常大间隔）  
2. **在常见连接词处切**：
   - 前缀词：and/or/but/if…、和/但/而/我/你…  
   - 后缀词：标点、的/了/吗/呢…  
3. **仍超长** → 在最大时间 gap 处二分（避免两端切）  

### 6. 其它辅助逻辑

- `preprocess_segments`：去纯标点；空格语言补 trailing space  
- `merge_short_segment`：合并过短片段（已标记 deprecated）  
- `_group_by_time_gaps`：按时间空隙分组，断句后二次切分也会用到  

---

## 二、优化（Optimize）怎么实现

### 1. 入口：`SubtitleOptimizer.optimize_subtitle`

文件：`videocaptioner/core/optimize/optimize.py`

流程：

1. 字幕 → `{ "1": text, "2": text, ... }` 字典  
2. 按 `batch_size` 分批  
3. 线程池并发执行 `agent_loop`  
4. 用优化后文本替换，**时间戳不变**  
5. 流水线结束后常调用 `remove_punctuation()` 去掉句尾 `，。`  

优化**不改时间轴**，只改每条字幕文案。

### 2. Agent Loop（最多 `MAX_STEPS = 3` 轮）

**Prompt**：`videocaptioner/core/prompts/optimize/subtitle.md`

能力范围：

- 修 ASR 错别字、术语  
- 去语气词（um/uh/呃/嗯）与非语言声  
- 规范标点、大小写、公式/代码写法  
- **不改句义、不合并/拆分条目、不翻译**  
- 输出纯 JSON  

用户侧可附带 `custom_prompt`（术语表、文稿提示、文件名上下文等）。

**循环：**

1. `call_llm`（temperature ≈ 0.2）  
2. `json_repair.loads` 解析（容忍不完美 JSON）  
3. `_validate_optimization_result`：
   - 键必须与输入完全一致  
   - 单条相似度：短句（≤10 词）≥ 0.3，普通 ≥ 0.7  
   - 改动过大则反馈重试  
4. 通过后 `_repair_subtitle`：`SubtitleAligner`（基于 `difflib.ndiff`）对齐原文与优化文，处理偶发合并/拆分错位  
5. 达上限则返回最后一次结果；整批失败则**保留原文**

### 3. 与断句的职责边界

| | 断句 | 优化 |
|--|------|------|
| 改文本内容？ | 否（只插边界） | 是（纠错/润色） |
| 改时间戳？ | 是（重组合并词） | 否（沿用原段） |
| 输出格式 | 文本 + `<br>` | JSON 字典 |
| 验证重点 | 内容几乎不变 + 长度 | 键完整 + 改动幅度 |
| 失败策略 | 规则降级 | 该批保留原文 |

---

## 三、数据流示意

```
词级 ASR: [大][家][好][今][天][我][们]...
              │
              ▼  LLM 只输出文本边界（插入 <br>）
         ["大家好", "今天我们..."]
              │
              ▼  滑动窗口对齐词序列 + 取时间戳
字幕段:  "大家好"      [t0–t2]
         "今天我们..."  [t3–tN]
              │
              ▼  优化（batch JSON，Agent Loop）
         "大家好"      → 修正后文本（时间不变）
         "今天我们..."  → 修正后文本（时间不变）
```

---

## 四、相关文件速查

| 路径 | 作用 |
|------|------|
| `videocaptioner/core/split/split.py` | 断句总控、分块、对齐、规则降级 |
| `videocaptioner/core/split/split_by_llm.py` | LLM 断句 + 验证 Agent Loop |
| `videocaptioner/core/split/alignment.py` | 优化后文本对齐器（`SubtitleAligner`） |
| `videocaptioner/core/optimize/optimize.py` | 字幕优化 + 验证 Agent Loop |
| `videocaptioner/core/prompts/split/sentence.md` | 断句 system prompt |
| `videocaptioner/core/prompts/split/semantic.md` | 语义分段 prompt（当前未接入） |
| `videocaptioner/core/prompts/optimize/subtitle.md` | 优化 system prompt |
| `videocaptioner/core/asr/asr_data.py` | 词级判定、句→词拆分、去标点 |
| `videocaptioner/ui/thread/subtitle_thread.py` | GUI 流水线编排 |
| `videocaptioner/cli/commands/subtitle.py` | CLI 流水线编排 |
| `tests/test_split/` | 断句相关测试 |
| `tests/test_optimize/` | 优化相关测试 |

---

## 五、配置与开关

常见配置项（GUI / CLI / config 文件）：

| 配置 | 含义 |
|------|------|
| `need_split` / `--no-split` | 是否智能断句 |
| `need_optimize` / `--no-optimize` | 是否 LLM 优化 |
| `thread_num` | 并发线程数 |
| `batch_size` | 优化每批条数 |
| `max_word_count_cjk` | CJK 段长上限 |
| `max_word_count_english` | 英文段长上限 |
| `custom_prompt` / `--prompt` | 术语/文稿提示（辅助优化） |
| LLM `api_key` / `base_url` / `model` | 断句与优化共用 |

CLI 示例：

```bash
# 仅优化（不翻译）
videocaptioner subtitle raw.srt --no-translate --api-key $OPENAI_API_KEY -o optimized.srt

# 跳过断句或优化
videocaptioner subtitle input.srt --no-split --no-optimize --translator google --target-language ja
```

---

## 六、设计要点

1. **LLM 管语义，时间戳管声学**  
   断句不让模型发明时间，而是把句子匹配回词级 ASR 戳。

2. **Agent Loop + 硬校验**  
   相似度、键完整性、长度限制降低胡改、丢条、超长问题。

3. **并发分批**  
   长视频可按线程数 + batch 打满 API，提高吞吐。

4. **可降级**  
   - 断句失败 → 时间间隔 + 连接词规则  
   - 优化失败 → 该批保留原文  

5. **职责清晰**  
   断句只负责「怎么切」、优化只负责「文案怎么改」，时间轴与文案问题解耦。

---

## 七、可关注的后续方向

- `semantic.md` 是否应作为可切换的断句策略  
- 对齐失败（未匹配句子）时的更稳健恢复策略  
- 无词级时间戳时，`split_to_word_segments` 音素均分的误差边界  
- 优化与翻译共用 `custom_prompt` 时的提示词冲突  

---

*报告基于 VideoCaptioner 源码静态分析整理。*
