# AGENTS.md — 字幕翻译项目工作指导

本文件是《法兰西小镇》(Un Village Français / A French Village) 字幕翻译项目的 AI 助手工作规范。任何执行本项目的 AI 助手必须先读本文件。

## 一、项目目标

将全七季（S01-S07）英文 SRT 字幕翻译为中文：
- **不漏对白**：英文每一条都有中文译文，不允许英文残留
- **人名/地名跨集统一**：同一角色全季译名一致
- **无竖杠 `|`**：输出中不得出现 `|` 分隔符
- **中英不错位**：中文条目必须与英文条目时间码严格对应
- **杜绝低级翻译错误**：每集翻译后必须做漏译扫描

## 二、翻译脚本

主脚本：`translate_srt.py`（本目录同级的 scripts/ 或与本文档同目录）

```bash
# 基本用法（默认 v4-flash）
python3 translate_srt.py <输入英文.srt> <输出中文.srt>

# 高质量模式（v4-pro，质量最好）
python3 translate_srt.py <输入.srt> <输出.srt> --v4pro

# 带人名地名表（保证译名统一）
python3 translate_srt.py <输入.srt> <输出.srt> --names "Daniel:丹尼尔,Monsieur:先生"

# 本地 Ollama（免费，慢，质量稍差）
python3 translate_srt.py <输入.srt> <输出.srt> --local
```

辅助脚本：
```bash
# 清洗无效条目（省 ~18% token）：去音效/空条目/纯符号
python3 clean_srt.py <输入.srt> <清洗后.srt>
```

API Key 获取优先级：环境变量 `DEEPSEEK_API_KEY` → 脚本同目录 `api_key.txt` → `~/.hermes/.env`

## 三、模型配置（2026-08 定稿）

| 模式 | 模型 | 参数 | 速度 | 质量 |
|---|---|---|---|---|
| 默认 | deepseek-v4-flash | temp 1.0, thinking 关闭 | ~3.5s/批 | 好 |
| `--v4pro` | deepseek-v4-pro | temp 0.3, thinking 关闭 | ~4s/批 | **最好** |

**关键参数（不可改）**：
- 思考必须关闭：`"thinking": {"type": "disabled"}`（`enable_thinking: false` 无效！reasoning 仍会生成）
- 批次 30 条/批（思考关闭后无需小批次）
- max_tokens 8192
- v4-flash/pro 端点不带 `/v1`：`https://api.deepseek.com/chat/completions`
- 所有模式 system prompt 均要求：`Ensure the translation is natural and avoids "translationese"`（避免翻译腔）

**历史教训**：v4-flash 思考模式会把 8192 token 预算烧光返回 0 字符（finish_reason=length）。必须关思考。

## 四、时间码规范（核心铁律，依据 Netflix 官方规范定制）

### 4.1 严格 1:1 映射
- **每个英文 Cue 必须且仅能对应一个中文 Cue**
- 只负责输出文本，**绝对禁止修改、合并、拆分、新增或删除任何时间码**
- 判断中英对位**必须用时间码对照**（ZH 条目时间段须吻合视频说话时段）
- **严禁用 seq 编号一一对应判断对位**（zh 编号体系可独立于 EN，560 vs 561 正常）
- 严禁用内容长度启发式判断对位

### 4.2 长句跨 Cue 重构（允许语序调整）
当同一英文长句被拆分至两个或多个**连续** Cue 时：
- 允许根据中文语法习惯灵活调整句式结构与语义分布（如语序倒装），确保中文自然流畅
- **必须严格保持与原时间码数量完全对等**——绝不可因语序重组而合并 Cue

### 4.3 内容边界锁定（禁止跨 Cue 迁移）
- 除上述长句重构外，**禁止无故跨 Cue 迁移独立语义**
- 不得为"凑整句"或"视觉排版"将独立的前后 Cue 文本强行揉合
- **短 Cue 必须对应短译文**

### 4.4 全局连贯优先
- 允许适度压缩文本
- 接受因时间码固定导致的单条 Cue 语义孤立或不完整现象
- 遇特殊断点时，以**连续播放的语感流畅、音画同步且无歧义**为最高优先级

### 4.5 铁律摘要
1. 英文一条 = 中文一条，严格一一对应
2. 时间码原样保留，绝不改动
3. 长句跨 Cue 可调语序，但 Cue 数量必须对等
4. 短 Cue 译短文，禁止揉合独立语义
5. 音画同步、语感流畅优先

## 五、漏译判断标准

**真漏译（需要补译）**：
- 英文残留：ZH 行根本没有中文翻译，ZH 就是英文原文
- EN 两行是不同说话人的两句对白，而中文只翻了一句

**不是漏译**：
- EN 两行是同一句话的 SRT 断行，中文合并成一行完整翻译（正常）

## 六、输出规范（Netflix 中文字幕风格，官方规范见 https://partnerhelp.netflixstudios.com/hc/en-us/articles/215986007-Chinese-Simplified-Timed-Text-Style-Guide）

- 无句号 `。` 无逗号 `，`——用空格分隔意群
- 省略号用 `…`（U+2026）！**唯一偏离官方规范处：禁止 `⋯`（U+22EF 数学中线省略号）**——很多播放软件不支持，PotPlayer 显示为方块 `□`
- 问号感叹号用全角 `？！`
- 人名之间用间隔号 `·`（全角）
- 中文数字 1-10 写汉字，其他量用数字
- 双引号用全角
- 输出 UTF-8 BOM + CRLF（Windows 播放器兼容）
- 文件放进视频目录时后缀用 `.pro`（最新规范）；历史版本用过 `.zh`、`.v4`

## 七、专有名词对照表（跨集统一，翻译时通过 --names 传入）

```
Villeneuve:维勒纳夫,Besançon:贝桑松,Lyon:里昂,Dijon:第戎,Marseille:马赛,
Paris:巴黎,Daniel:丹尼尔,Schwartz:施瓦茨,Gustave:古斯塔夫,Marie:玛丽,
Marcel:马塞尔,Suzanne:苏珊娜,Lucienne:吕西安娜,Marchetti:马尔凯蒂,
Hortense:霍滕斯,Servier:塞尔维耶,Bériot:贝里奥,Larcher:拉尔谢,
Ritter:里特,Helmut:赫尔穆特,Heinrich:海因里希,Muller:穆勒,Albert:阿尔贝,
Victor:维克托,Vincent:樊尚,Roger:罗歇,Edmond:埃德蒙,Sarah:莎拉,
Jeannine:让尼娜,Balthazar:巴尔塔扎,Raymond:雷蒙,Jeanne:让娜,Léon:莱昂,
Lorraine:洛琳,André:安德烈,Marguerite:玛格丽特,Jacques:雅克,Antoine:安托万,
Julien:朱利安,Edouard:爱德华,Amélie:艾米丽,Berthier:贝尔蒂埃,
Claudine:克洛迪娜,Anna:安娜,Kerven:凯尔文,Monsieur:先生,Madame:太太,
Boches:德国鬼子,Pétain:贝当
```

**同一部剧所有集必须用同一份表**，否则人名会翻乱。

## 八、故事背景（翻译前必读，以符合故事背景的口吻翻译）

**翻译前必须先看故事背景**，然后以符合故事背景的口吻进行翻译——这是用户明确要求，与通读全片功能配合使用。

脚本默认自动加载 `story_background.txt`（脚本同目录），内容为：
- 《法兰西小镇》二战背景：1940 年德军占领法国东部小镇维勒纳夫
- 主要角色身份：市长塞尔维耶、德军驻镇军官里特/赫尔穆特、抵抗运动成员马塞尔、犹太教师古斯塔夫等
- 时代要点：物资配给、宵禁、黑市、犹太人驱逐、抵抗运动兴起
- 翻译提示：Boches 译"德国鬼子"、Monsieur/Madame 译"先生/太太"

## 九、通读全片功能（2026-08 新增，质量大幅提升）

翻译前脚本自动执行 `read_full_episode()`：
1. 把整集英文字幕（seq + 文本，去时间码省 token）发给模型
2. 模型输出剧情摘要（梗概 + 人物状态 + 对话语境/潜台词/伏笔 + 翻译注意事项，≤400 字）
3. 摘要注入每一批翻译的 prompt——每批都"读过全片"，理解完整剧情

这解决了分段翻译"剧情理解分裂"的根本问题（用户反馈：通读版质量明显优于分段直译）。

## 十、工作流程（用户偏好）

1. **一集一集翻**（单集串行），不要一次性翻全季
2. **高效执行**：直接用一次写入完整的中文字幕文件（如 create_file 一次性写出整集 SRT），节省 token——不要分批多次输出
3. 翻完先通知用户等确认，**确认后再部署**到视频目录
4. 部署命名规则：后缀 `.pro`（如 `xxx.pro.srt`）
5. 用户 token 敏感：他说"先别测试，等晚上再测试"就等到晚上（token 便宜）
6. 日志禁止 `2>&1 | tail -8` 管道（会死锁）
7. 翻译完必须做漏译扫描（英文残留检测），发现残留先补译再交付

## 十一、已知坑与教训

| 坑 | 说明 |
|---|---|
| 法文组合字符 | S02/S03 文件名 `Un.Village.français...` 的 ç/é 是组合字符，直接写路径会 FileNotFoundError。**必须用 os.listdir 枚举匹配** |
| 文件名后缀差异 | S01: `_track5.eng.srt`；S02: `.eng.srt`；S03/S07: `_track6_eng.srt`。匹配用通配 `"eng.srt" in f` |
| `f"S{season}"` 双 S bug | season 已含 "S"（如 "S04"），`f"S{season}"` 会变 "SS04"。直接 `season in d` |
| U+22EF 省略号 | PotPlayer 显示方块 `□`。已全局修复为 U+2026，新脚本不会再产生 |
| 思考模式烧 token | v4-flash/pro 必须 `thinking: {"type":"disabled"}` |
| `2>&1 \| tail` 死锁 | 管道缓冲导致进程看似卡死，日志用文件重定向 |
| S01E01 | 用户恢复的原版 zh.srt，禁止改动 |
| S05/S06 | 待授权翻译（用户说"先不用，等着"） |

## 十二、当前进度（2026-08-03）

- ✅ S02、S03、S04、S07：v4-pro 全季翻译完成，`.pro` 已入目录（S02 六集、S03 十二集、S04 十二集、S07 十二集）
- ✅ S01E04/E05/E06：v4 版完成入目录；S01E02：v4-flash 背景版（用户已自行拷贝）
- ✅ 全季英文残留 0
- ⏳ S01E01（原版保留）、S01E03（通读版测试完成待确认）、S05、S06 未翻
- 🔬 通读全片功能：S02E03 测试版已放桌面待用户对比确认

## 十三、验证命令

```bash
# 条目数对齐 + 缺失检查
python3 -c "
import re
def parse(p):
    raw=open(p,'rb').read()
    while raw[:3]==b'\xef\xbb\xbf': raw=raw[3:]
    c=raw.decode('utf-8').replace('\r\n','\n')
    d={}
    for b in c.strip().split('\n\n'):
        l=b.strip().split('\n')
        if len(l)>=3 and l[0].isdigit(): d[int(l[0])]=l[2:]
    return d
en=parse('en.srt'); zh=parse('zh.srt')
print(f'EN {len(en)} = ZH {len(zh)} | 缺失 {sum(1 for s in en if s not in zh)}')
"

# 漏译扫描（英文残留检测）
python3 -c "
import re
def parse(p):
    raw=open(p,'rb').read()
    while raw[:3]==b'\xef\xbb\xbf': raw=raw[3:]
    c=raw.decode('utf-8').replace('\r\n','\n')
    d={}
    for b in c.strip().split('\n\n'):
        l=b.strip().split('\n')
        if len(l)>=3 and l[0].isdigit(): d[int(l[0])]=l[2:]
    return d
def is_res(lines):
    t=' '.join(lines).strip()
    if not t: return False
    lat=len(re.findall(r'[a-zA-Z]',t)); cjk=len(re.findall(r'[\u4e00-\u9fff]',t))
    return lat>8 and lat>cjk
zh=parse('zh.srt')
print('残留:', [s for s in sorted(zh) if is_res(zh[s])])
"
```
