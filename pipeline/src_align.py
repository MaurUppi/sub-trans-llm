"""原文回显对齐判定。

模型按契约回显 `src`，但成片里的原文**始终取本地 `Cue.text`**
（见 `orchestrator.build_bilingual_srt` 调用处）。所以 `src` 字段唯一的用途
就是**验证这一条译文确实对应这一条原文**——字幕翻译最危险的事故是整批错位：
键都在、`tr` 都非空、JSON 完全合法，但第 37 条的译文其实是第 36 条的。
json_schema / json_object 对这种事故零作用（见 `docs/baseinfo.md`），
只能靠回显比对。

因此这里把「不匹配」细分，避免用一刀切的相等判断误伤正常的回显走样：

- ``ok``          归一化后相等
- ``drift``       与本条足够相似（大小写/标点/空白变体）→ 警告即可
- ``misaligned``  归一化后**恰好等于本批另一条**的原文 → 几乎可以确定错位
- ``mismatch``    既不像本条、也不是别条 → 幻觉或跨批错位
- ``missing``     没回显
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

from pipeline.config import SRC_DRIFT_THRESHOLD

# 归一化时抹掉的字符：空白、常见标点及其全角变体。
# 目的是让「Marcel ?」与「Marcel?」判等，而不放过真正的内容差异。
_PUNCT = r"""!"'(),\-.:;?\[\]`{}…‘’“”—–，。！？；：（）【】《》、"""
_PUNCT_RE = re.compile(f"[{re.escape(_PUNCT)}]+")
_WS_RE = re.compile(r"\s+")


def normalize_src(s: Optional[str]) -> str:
    """用于比较的归一化：NFKC + 小写 + 去标点 + 压空白。"""
    if not s:
        return ""
    t = unicodedata.normalize("NFKC", str(s)).casefold()
    t = _PUNCT_RE.sub(" ", t)
    return _WS_RE.sub(" ", t).strip()


@dataclass(frozen=True)
class SrcVerdict:
    kind: str  # ok | drift | misaligned | mismatch | missing
    other_id: Optional[str] = None
    ratio: float = 0.0

    @property
    def is_fatal(self) -> bool:
        """严格模式下应判为 error 的类别。"""
        return self.kind in ("misaligned", "mismatch")


def build_src_index(input_map: dict[str, str]) -> dict[str, list[str]]:
    """归一化原文 → 拥有该原文的 id 列表（重复台词很常见，故用 list）。"""
    index: dict[str, list[str]] = {}
    for kid, src in input_map.items():
        index.setdefault(normalize_src(src), []).append(kid)
    return index


def classify_src(
    kid: str,
    actual: Optional[str],
    input_map: dict[str, str],
    index: dict[str, list[str]],
    *,
    drift_threshold: float = SRC_DRIFT_THRESHOLD,
) -> SrcVerdict:
    if actual is None or not str(actual).strip():
        return SrcVerdict("missing")

    expected_n = normalize_src(input_map.get(kid, ""))
    actual_n = normalize_src(actual)

    # 先判自身相等：重复台词场景下必须优先，否则会被索引误判为错位
    if actual_n == expected_n:
        return SrcVerdict("ok", ratio=1.0)

    owners = [o for o in index.get(actual_n, []) if o != kid]
    if owners:
        return SrcVerdict("misaligned", other_id=owners[0])

    ratio = SequenceMatcher(None, expected_n, actual_n).ratio()
    if ratio >= drift_threshold:
        return SrcVerdict("drift", ratio=ratio)
    return SrcVerdict("mismatch", ratio=ratio)
