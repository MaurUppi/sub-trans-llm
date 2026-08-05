# 质量控制：整文件英进 / 双语出（定稿约定）

配置与 token 上限见 `baseinfo.md`。

**外部文件角色（重要）**

| 文件 | 角色 |
|---|---|
| `translation_prompt.md` | **System prompt 模板**（变量替换后写入 `instructions`） |
| `Un_Village_francais_Glossary.md` | Glossary 源（解析后拼入 `instructions`） |
| `Netflix-Chinese_(Simplified)_Timed_Text_Style_Guide.md` | **参考**：简中 Timed Text 官方约束来源 |
| `AGENTS-字幕翻译.md` | **参考**：项目历史约定与流程；**不**当作最终 instruction 正文 |

**最终 `instructions` 由后续 Python 脚本读取外部文件组装**（模板 + Glossary ± 可选附录），不在本文手写死全文。

**适用范围（六模型）**

| 厂商 | 模型 |
|---|---|
| 火山方舟 Ark | `deepseek-v4-flash-260425` / `deepseek-v4-pro-260425` / `doubao-seed-2-1-turbo-260628` |
| 阿里云百炼 | `qwen3.7-plus` / `qwen3.7-max` / `qwen3.8-max` |

调用：OpenAI SDK · **Responses API** · 密钥仅 `.env`。

---

## 0. 定稿决策（2026-08-05）

| # | 决策 | 说明 |
|---|---|---|
| 1 | **必须关闭 thinking / reasoning** | 全模型、全请求；不得依赖默认 |
| 2 | **统一 `temperature=1.0`、`top_p=1.0`** | 六模型公平对比与复现 |
| 3 | **统一 `max_output_tokens` = 六模型输出上限的公共最大值** | 保证测试字幕**一次输出且有余量**（见 §3） |
| 4 | **除 system 文案外，其它采样参数暂不调** | 不设 penalty / seed / stop / top_k 等 |
| 5 | **外部 Glossary 必须支持** | 读本地术语表；六模型**无**原生 glossary API 字段（见 §5） |
| 6 | **字段分工已确认** | **system 规范 + Glossary → `instructions`**；**待译内容 → `input`**（见 §5） |
| 7 | **System prompt 采用 `translation_prompt.md`** | 使用前替换变量；**`input` / 模型输出均为 JSON**（见 §6） |

---

## 1. 思考必须关闭

| 厂商 | 关闭方式 | 注意 |
|---|---|---|
| Ark（DeepSeek / Doubao） | `extra_body={"thinking": {"type": "disabled"}}` | 仅 `enable_thinking:false` 可能无效 |
| Aliyun（Qwen） | `reasoning={"effort": "none"}` | 优先于 `enable_thinking`；默认 effort 可能很高 |

**必须关的原因（本项目）：**

- 思考 token 与最终译文共享输出预算；预算不够会出现 **截断 / 空答**（历史教训）。  
- DeepSeek：思考开启时 `temperature` / `top_p` 等**不生效**（不报错）。  
- 整文件双语出对输出预算敏感，禁止思考抢额度。

`model_client.py` 已强制按上表关闭。

---

## 2. temperature / top_p

### 2.1 本项目统一取值（对比阶段 · 已定）

| 参数 | 统一值 | 含义 |
|---|---|---|
| `temperature` | **1.0** | 中性采样；与常见 API 默认对齐 |
| `top_p` | **1.0** | 不额外截断候选集 |

- 六模型对比时**不得**因模型切换改这两个值。  
- 官方场景推荐（见 §2.2）仅作参考与后续 A/B；**本项目对比阶段仍以 1.0 / 1.0 为准**。  
- 多家文档建议「`temperature` 与 `top_p` 二选一调节」；本阶段两者固定，不再叠加其它采样旋钮。

### 2.2 官方对「翻译」或通用生成的采样建议（检索摘要）

> 检索日期：2026-08-05。下表区分 **「明确写给翻译场景」** 与 **「通用 / 旁系型号默认」**。  
> 本项目六模型中：DeepSeek / Doubao 走 **火山方舟**；Qwen 走 **阿里云百炼通用对话型号**（非 Qwen-MT）。

#### DeepSeek（官方 API · 场景表明确写「翻译」）

| 项 | 值 / 说明 | 来源 |
|---|---|---|
| API 默认 `temperature` | **1.0** | [DeepSeek API · Temperature 设置](https://api-docs.deepseek.com/zh-cn/quick_start/parameter_settings/) |
| **翻译场景推荐 `temperature`** | **1.3** | 同上（场景表：代码/数学 0.0 · 数据抽取 1.0 · **通用对话 1.3 · 翻译 1.3** · 创意写作 1.5） |
| `top_p` 与翻译 | **无单独「翻译」推荐** | Chat Completions 文档：默认 **1**；建议改 `temperature` **或** `top_p`，**不建议同时大改两者**（[create-chat-completion](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/)） |
| 与本项目关系 | 方舟托管的 deepseek-v4-flash/pro 沿用同一家族采样语义；官方场景表是 **直连 DeepSeek API** 文档，方舟未另发「翻译=1.3」专表 | 对比阶段仍 **1.0/1.0**；若单开「按官方翻译最优」实验可试 **temp=1.3、top_p 保持 1.0** |

#### 阿里 Qwen（通用 Qwen3 vs 翻译专用 Qwen-MT）

| 层级 | temperature | top_p | 其它 | 来源 / 适用 |
|---|---:|---:|---|---|
| **Qwen3 非思考 / Instruct**（通用） | **0.7** | **0.8** | `top_k=20`，`min_p=0`；可调 `presence_penalty` 0–2 降重复 | [Qwen Quickstart](https://qwen.readthedocs.io/en/latest/getting_started/quickstart.html) · HuggingFace model card Best Practices |
| **Qwen3 思考模式**（本项目已关） | **0.6** | **0.95** | `top_k=20`；**勿用贪心解码** | 同上 |
| **Qwen-MT（专用翻译型号）** | 默认 **0.65** | 默认 **0.8** | `top_k` 默认 **1**；文档写 temperature 与 top_p **建议只设其一** | [百炼 · Qwen-MT API](https://help.aliyun.com/zh/model-studio/qwen-mt-api) / [EN](https://www.alibabacloud.com/help/en/model-studio/qwen-mt-api) |

要点：

- **没有**「qwen3.7-plus / max / 3.8-max 翻译场景专用 temperature」的官方单表；最接近的是 **Qwen3 非思考通用推荐 0.7 / 0.8**，以及 **Qwen-MT 默认 0.65 / 0.8**。  
- **Qwen-MT 不在本项目六模型内**；其默认值仅作「阿里官方翻译产品线」旁证，不能直接改写六模型对比参数。  
- 本项目 Qwen 三模型：**关 thinking** → 若做「跟官方通用推荐」对照，可试 **0.7 / 0.8**；对比主线仍 **1.0 / 1.0**。

#### 豆包 Doubao / 火山方舟（通用型号）

| 项 | 结论 | 来源 / 说明 |
|---|---|---|
| **翻译场景专用 temperature / top_p** | **未检索到**方舟或豆包官方文档中「翻译 → 某固定 temp/top_p」场景表 | 与 DeepSeek 官方场景表不同 |
| 通用 Chat / Responses 参数 | 支持 `temperature`、`top_p` 等采样旋钮；文档侧重参数含义与取值范围，**未绑定「翻译」推荐值** | 火山方舟 Chat API 等（[对话 API](https://www.volcengine.com/docs/82379/1494384)） |
| 旁系：`doubao-seed-translation` 等翻译增强型号 | 产品定位为翻译；**公开材料强调能力/语种，不给出与本表同级的 temp/top_p 场景推荐** | **不在本项目六模型内**（见 §5.2） |
| 与本项目关系 | `doubao-seed-2-1-turbo` 按 **通用生成** 处理；对比阶段 **1.0 / 1.0**；无官方「翻译最优」可对齐时，不以第三方博客默认值（如 0.7/0.9）替代官方 |

#### 三家对照（翻译相关 · 便于扫一眼）

| 厂商 | 本项目型号 | 官方是否写明「翻译」采样 | 官方给出的相关值 | 本项目对比取值 |
|---|---|---|---|---|
| DeepSeek | deepseek-v4-flash / pro（方舟） | **是**（API 场景表） | 翻译 **temperature=1.3**；`top_p` 默认 1、无翻译专值 | **1.0 / 1.0** |
| 豆包 | doubao-seed-2-1-turbo（方舟） | **否**（通用型号无翻译专表） | 无官方翻译推荐；通用可调 | **1.0 / 1.0** |
| 阿里 Qwen | qwen3.7-plus / max / 3.8-max | **否**（通用型号）；**旁证** Qwen-MT | 非思考通用 **0.7 / 0.8**；Qwen-MT 默认 **0.65 / 0.8** | **1.0 / 1.0** |

### 2.3 后续可选实验（不改变当前主线）

若在对比主线之外做「贴官方」采样实验，建议**一次只改一家、且与 1.0/1.0 基线对照**：

| 实验标签 | 建议参数 | 依据 |
|---|---|---|
| `ds-official-translate` | temp=**1.3**，top_p=**1.0**（或只动 temp） | DeepSeek 场景表「翻译」 |
| `qwen-nontthinking-best` | temp=**0.7**，top_p=**0.8**（可选 top_k=20，若网关支持） | Qwen3 非思考官方推荐 |
| `qwen-mt-like-defaults` | temp=**0.65**，top_p=**0.8** | Qwen-MT API 默认（**旁证**，非本项目型号） |
| `doubao` | **暂无官方翻译锚点**；保持 1.0/1.0 或仅与 Qwen/DeepSeek 做同参数对照 | 避免无出处调参 |

---


## 3. max_output_tokens（统一最大值）

### 3.1 为何统一「最大值」

目标：**整文件英进、双语一次返回**，且六模型同一套参数可比。

各模型**最大输出**（厂商文档，见 `baseinfo.md`）：

| 模型 | 最大输出 |
|---|---:|
| deepseek-v4-flash / pro | 384k |
| doubao-seed-2-1-turbo | 256k |
| qwen3.7-plus / max / 3.8-max | **131072（128k）** |

**统一取值（六模型都能接受的最大公共上限）：**

```text
MAX_OUTPUT_TOKENS_UNIFIED = 131072
```

- 受 **Qwen 128k** 约束；不能统一成 256k/384k（Qwen 会拒参或无效）。  
- 对 S01E03 级双语输出（粗估中位 ~26k–40k，高位 ~40k）相对 128k 仍有 **约 3× 余量**。  
- Ark 省略时实测默认常为 **32768**，双语整集**不够**，故必须**显式传入 131072**，不可省略。  
- 阿里云：`max_output_tokens` 最小 16；131072 在合法范围内。

### 3.2 输入侧

- **不需要**请求参数 `max_input_tokens`（六模型 Responses 调用无此标准旋钮）。  
- 只需保证实际输入 ≤ 模型最大输入 / 上下文（S01E03 英文整文件 ~16k–20k，远小于 256k–1M）。  
- 注意：`input + output ≲ context`；整集场景下输出按 128k 预留时，输入仍极宽裕。

### 3.3 建议写入 `.env`

```env
DEFAULT_MAX_OUTPUT_TOKENS=131072
DEFAULT_TEMPERATURE=1.0
DEFAULT_TOP_P=1.0
```

---

## 4. 其它参数：暂不考虑

对比与首版流水线阶段：

| 类别 | 处理 |
|---|---|
| `frequency_penalty` / `presence_penalty` / `top_k` / `seed` / `stop` / `n` | **不传** |
| tools / 联网 / 代码解释器 | **关闭** |
| structured output / JSON schema | 待 system 文案确定后再定 |
| **system 文案正文** | **待确认**（字段归属已定：进 `instructions`；见 §5 / §6） |

---

## 5. `instructions` / `input` 与 Glossary（已确认）

### 5.1 字段分工（已确认）

**定论：system 规范 + Glossary → `instructions`；待译字幕 → `input`（JSON 字符串，非原始 SRT 文本）。**

| 内容 | Responses 字段 | 备注 |
|---|---|---|
| **System prompt**（`translation_prompt.md` 替换变量后） | **`instructions`** | ≈ Chat `system` |
| **Glossary**（专有名词对照） | **`instructions`**（接在 system 之后，**同一字符串**） | 非独立 API 参数 |
| **待译字幕**（由 SRT 解析得到的 JSON） | **`input`** | **合法 JSON 字符串**；键=字幕 ID，值=原文（见 §6） |

```text
instructions = system_prompt_substituted   # from translation_prompt.md
             + "\n\n## 专有名词（必须遵守，不得另译）\n"
             + glossary_compact

input        = JSON.stringify({ "0": "原文…", "1": "原文…", ... })
               # 由整文件 .srt 解析；不是 raw SRT 原文塞进 input
```

**为何这样拆：**

- `instructions` 管「怎么译 / 必须遵守什么」。  
- `input` 管「译哪些条目（JSON）」。  
- 六模型 Responses 均支持顶层 `instructions`；`input` 传 **JSON 文本** 即可（string 形态）。  
- Glossary **没有**单独字段，只能作为 `instructions` 的一部分。

**明确不要：**

- 把 Glossary 放到 `input` 前缀（默认全部进 `instructions`）。  
- 把 **原始 .srt 全文**（含序号/时间码）直接当 `input`（与 system 的 JSON 协议不一致）。  
- 依赖 Qwen-MT 的 `terms` 等字段（非本项目六模型）。

### 5.2 六模型 API：有没有原生「术语表」字段？

| 模型 / 接口 | 原生 Glossary 参数 | 结论 |
|---|---|---|
| deepseek-v4-flash / pro（Ark Responses） | **无** | 文档无 terms/glossary 字段 |
| doubao-seed-2-1-turbo（Ark Responses） | **无** | 通用生成模型；术语靠 prompt |
| qwen3.7-plus / max / 3.8-max（百炼 Responses） | **无** | 通用对话模型；未列出术语干预参数 |
| （对照）**Qwen-MT** 专用翻译模型 | **有** `translation_options.terms` | **不在本项目六模型内** |
| （对照）方舟 `doubao-seed-translation` | 翻译增强型号 | **不在本项目六模型内**；上下文过小 |

**结论：六模型统一「应用层注入」——Glossary 拼进 `instructions`，与 system 规范同行。**

### 5.3 实现流水线

```
┌──────────────────────┐   替换 ${sourceLanguage}=英语
│ translation_prompt.md│   ${targetLanguage}=简体中文
└──────────┬───────────┘
           v
┌──────────────────────┐     ┌──────────────────┐     ┌─────────────────────────────┐
│ Glossary.md          │ --> │ 紧凑对照表        │ --> │ instructions =              │
└──────────────────────┘     └──────────────────┘     │   system_prompt_substituted │
                                                      │   + glossary_compact        │
┌──────────────────────┐     ┌──────────────────┐     └──────────────┬──────────────┘
│ 英文 .srt 整文件     │ --> │ 解析为 JSON 对象  │ ----------------> │ input = json_string        │
│ (仅本地用时间码)     │     │ {"0":"…","1":"…"} │                  │ thinking 关 / temp 1.0    │
└──────────────────────┘     └──────────────────┘                  │ max_output_tokens=131072  │
                                                                   └─────────────────────────────┘
输出：JSON {"0":{"src":"…","tr":"…"}, ...} → 本地用原 SRT 时间码拼回双语/中文字幕
```

**1）加载与解析（本地代码，不经过模型）**

- 读 `Un_Village_francais_Glossary.md`（路径可配置，如 `GLOSSARY_PATH`）。  
- 从表格提取：`source`（法文/英文原名）→ `target`（中文译名）。  
- 建议压成紧凑行，例如：

```text
Daniel Larcher = 达尼埃尔·拉尔谢
Marcel Larcher = 马赛尔·拉尔谢
Villeneuve = 维勒纳夫
the Line = 分界线
Sicherheitsdienst/SD = 安全处
...
```

- 可选：同一人物多个写法（`Natasha/Odile`、`Portais/Morten`）拆成多条 source → 同一 target。

**2）调用示意（定稿形态 · JSON 输入）**

```python
import json

system_prompt = (
    Path("docs/translation_prompt.md")
    .read_text(encoding="utf-8")
    .replace("${sourceLanguage}", "英语")
    .replace("${targetLanguage}", "简体中文")
)

# cues: dict[str, str]，由整文件 SRT 解析，键为稳定 ID（如 "0","1",…）
input_json = json.dumps(cues, ensure_ascii=False, separators=(",", ":"))

client.responses.create(
    model=...,
    instructions=(
        system_prompt
        + "\n\n## 专有名词（必须遵守，不得另译）\n"
        + glossary_compact_text
    ),
    input=input_json,  # 必须是 JSON 对象字符串，不是 raw .srt
    temperature=1.0,
    top_p=1.0,
    max_output_tokens=131072,
    # 若平台支持，可加 structured output 强化 JSON（见 §6.3）
    # text={"format": {"type": "json_object"}},
    # Ark: extra_body thinking disabled
    # Ali: reasoning effort none
)
```

**3）不要用的做法（本阶段）**

| 做法 | 原因 |
|---|---|
| `input` 传原始 `.srt` 全文 | 与 system 的「JSON 键=ID」协议冲突 |
| 改调 Qwen-MT 的 `terms` 字段 | 型号与接口不同，无法六模型统一对比 |
| 依赖模型「自带记忆」不传表 | 跨集/跨模型译名会漂 |
| Glossary 放 `input` | 与已确认分工不符；术语进 `instructions` |
| 未替换 `${sourceLanguage}` / `${targetLanguage}` | 字面量会进模型，行为未定义 |

### 5.4 术语表规模与输入预算

| 项 | 粗估 |
|---|---|
| 当前 Glossary 全文 | 约数百行 Markdown，紧凑对照约 **1k–3k tokens** 量级 |
| 英文字幕 S01E03 | ~16k tokens |
| 合计输入 | 远小于 Doubao 256k / 其余 1M |

整文件一次请求时：**全文 Glossary 可整表注入 `instructions`**，不必按批裁剪（仍建议紧凑格式）。

### 5.5 质量侧约定

- 表中有的专名：**必须用表内中文**，禁止模型自由发挥。  
- 表中没有的：按 system 规范意译，并标记后续可补进 Glossary（流程项，非 API）。  
- 后处理可做：扫描输出是否出现未按表翻译的常见英文专名（可选校验脚本）。

### 5.6 与 Qwen-MT 原生术语的差异（仅文档对照）

百炼 **Qwen-MT**（非本项目模型）支持：

```json
"translation_options": {
  "source_lang": "English",
  "target_lang": "Chinese",
  "terms": [
    {"source": "Daniel Larcher", "target": "达尼埃尔·拉尔谢"}
  ]
}
```

六模型**不能**依赖该字段；若未来单开 Qwen-MT 对照实验，再单独分支，不与六模型主线混用参数表。

---

## 6. System prompt 与参考规范

### 6.1 结论

| 项 | 判定 |
|---|---|
| **System 模板** | `docs/translation_prompt.md`（变量替换后 → `instructions`） |
| **标点 / 禁 `\|` / 省略号** | **按 Netflix 简中 Timed Text**（已写入模板「标点与符号」节） |
| **AGENTS / Netflix 全文** | **仅参考**；脚本组装 instruction 时读模板（+ Glossary），不整篇粘贴 Netflix/AGENTS |
| **Responses** | **`input` = JSON**；输出 = **src/tr JSON**（见 §6.3） |

### 6.2 变量替换（本任务）

| 占位符 | 本任务取值 |
|---|---|
| `${sourceLanguage}` | **英语** |
| `${targetLanguage}` | **简体中文** |

替换须在拼 `instructions` **之前**由 Python 完成；禁止把未替换的 `${…}` 发给模型。

### 6.3 Responses API：JSON 输入 / JSON 输出（已确认）

1. **输入**：JSON 对象，键 = 字幕 ID，值 = 该条**原文**（纯文本，无时间码）。  
2. **输出**：JSON 对象，键与输入完全一致。  
3. 每个值：`{"src": "<原文逐字回显>", "tr": "<简体中文译文>"}`。  
4. 只返回纯 JSON。

```text
.srt → 解析 cues（本地保留时间码）
    → input = {"0": text0, "1": text1, ...}
    → Responses
    → 校验键集合 / src / tr
    → 本地用原时间码 + tr 拼中文或双语 SRT
```

可选：`text={"format": {"type": "json_object"}}`（平台支持时）；失败须 `json.loads` 校验后重试。

### 6.4 从 Netflix 简中指南提取的约束（对照模板）

来源：`Netflix-Chinese_(Simplified)_Timed_Text_Style_Guide.md`（官方 Timed Text Style Guide 文本）。

| # | Netflix 约束（摘要） | `translation_prompt.md` |
|---|---|---|
| C1 | **每行最多 16 字符** | ✅ 规则 6 |
| C2 | **最多 2 行**；优先单行；多行时 **bottom-heavy pyramid**，避免上行仅 1–2 词 | ✅ 规则 6 |
| C3 | **禁止逗号、句号**；意群用**一个空格** | ✅「标点与符号」§1 |
| C4 | 列举可用 **`、`**，勿在行末/条末 | ✅ §2 |
| C5 | 疑问/感叹用全角 **`？` `！`**；禁止 `!?` `??` `!!` | ✅ §3 |
| C6 | 省略号仅用 **U+2026 `…`**；**不支持 U+22EF `⋯`** | ✅ §4 |
| C7 | 连续句跨多条字幕：**不要**用省略号/破折号接龙 | ✅ §6 |
| C8 | 停顿≥2s 或打断可用 `…`；句中开始可用 `…` 开头 | ✅ §6 |
| C9 | 双说话人：行首 **半角 `-` 无空格**，每行一人 | ✅ §5（并 **禁 `\|`**，见下） |
| C10 | 引号全角 `“”` / 嵌套 `‘’` | ✅ §7 |
| C11 | 外国人名用间隔号 **`·`** | ✅ §8 |
| C12 | 数字 1–10 可写汉字；半角阿拉伯数字；星期用「星期二」 | ✅ §9 |
| C13 | 勿用斜体 | 未写入（JSON 文本场景通常无斜体） |
| C14 | 阅读速度 9/7 cps 等 | **不进模型 prompt**（属时间轴/后处理，非本 JSON 协议职责） |
| C15 | 对白不删脏话力度等 | 未写入模板；需要时可脚本追加 |

**关于 `|`：** Netflix 双说话人规范用的是 **`-`**，不是竖杠。项目参考 `AGENTS` 亦要求译文中**无 `|`**（含全角 `｜`），已写入模板 §5，避免模型用 `|` 分隔说话人。

**关于省略号字符：** Netflix 正文要求 **U+2026**；指南示例里偶发 `⋯` 字形，**以条文「U+2026；U+22EF 不支持」为准**。AGENTS 禁止 `⋯`、采用 `…`，与 Netflix 条文一致（实现后处理可统一规范化为 U+2026）。

### 6.5 与旧版模板 / AGENTS 的对照说明

| 点 | 旧 `translation_prompt` | 现模板 | AGENTS（参考） |
|---|---|---|---|
| 标点 | 「适当使用逗号、句号」 | **禁用 `，` `。`，空格分意群**（Netflix） | 同 Netflix 向 |
| 省略号 | 未限定码位 | **仅 `…` U+2026** | 同；禁 U+22EF |
| 竖杠 `\|` | 未写 | **禁止** | 禁止 |
| 16 字 / 2 行 / 金字塔 | 已有 | 保留（= Netflix C1–C2） | 未强制写死，与 Netflix 一致即可 |
| 1:1 条目 | 有 | 保留 | 有（时间码侧由本地保证） |
| Glossary | 无 | 由脚本拼入 instructions | 有 |

### 6.6 Python 组装 `instructions`（约定）

最终请求**不**手工粘贴 Netflix/AGENTS 全文。脚本侧建议：

```text
instructions =
    load("translation_prompt.md")
      .replace("${sourceLanguage}", "英语")
      .replace("${targetLanguage}", "简体中文")
  + "\n\n## 专有名词（必须遵守，不得另译）\n"
  + compact_glossary_from("Un_Village_francais_Glossary.md")
  # 可选：+ 其它外部片段（故事背景等），仍由脚本读文件拼接

input = json.dumps(cues_dict, ensure_ascii=False)  # 仅字幕正文 JSON
```

Netflix / AGENTS 文件用于：**维护模板时的对照**、评审、后处理规则；**默认不整篇注入**（避免撑爆输入且与「模板 + Glossary」架构重复）。

### 6.7 可用性清单

- [x] 模板可作 `instructions` 主体（变量替换后）  
- [x] 标点 / `…` / 禁 `|` **按 Netflix 写入模板**  
- [x] `input` 为 JSON；输出 src/tr JSON  
- [x] Glossary 由脚本拼接  
- [x] Netflix / AGENTS 定位为**参考**，非最终 instruction 全文  
- [ ] 后处理：强制 `…` 码位、剔除 `|`、扫描 `，。`（推荐，防模型偶发违规）  

---

## 7. 请求参数清单（当前有效）

| 参数 | 值 | 状态 |
|---|---|---|
| thinking / reasoning | **关闭** | 已定 |
| temperature | **1.0** | 已定（官方翻译建议见 §2.2，对比阶段不跟） |
| top_p | **1.0** | 已定（同上） |
| max_output_tokens | **131072** | 已定 |
| **`instructions`** | **脚本读取 `translation_prompt.md`（变量替换）+ Glossary 等外部文件拼接** | **已定** |
| **`input`** | **字幕 JSON 字符串** `{"id":"原文",...}` | **已定（必须 JSON）** |
| 期望输出 | **JSON** `{"id":{"src","tr"},...}` | **已定** |
| 其它采样 / tools | 不传 / 关闭 | 已定 |

---

## 8. 相关文件

| 文件 | 内容 |
|---|---|
| `baseinfo.md` | 六模型输入输出上限、API 与 `.env` |
| `translation_prompt.md` | **System prompt 模板**（含 Netflix 向标点；变量 `${sourceLanguage}` / `${targetLanguage}`） |
| `Un_Village_francais_Glossary.md` | Glossary 源（脚本注入） |
| `Netflix-Chinese_(Simplified)_Timed_Text_Style_Guide.md` | **参考**：官方简中 Timed Text 约束 |
| `AGENTS-字幕翻译.md` | **参考**：项目流程与历史约定 |
| `model_client.py` | 统一调用（关思考；读 `.env`） |

---

*定稿反馈纳入日期：2026-08-05。*  
*字段分工：system 模板 + Glossary → `instructions`（Python 读文件组装）；待译 JSON → `input`。*  
*标点 / 禁 `|` / 省略号 `…`：对齐 Netflix 简中 Timed Text；AGENTS 与 Netflix 全文仅作参考。*  
*§2.2 官方采样：DeepSeek 翻译 temp=1.3；Qwen 非思考 0.7/0.8、Qwen-MT 默认 0.65/0.8；Doubao 通用型号无翻译专表（2026-08-05 检索）。*
