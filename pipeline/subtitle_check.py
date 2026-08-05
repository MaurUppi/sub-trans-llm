"""译文侧字幕约束校验：阅读速度（CPS）、每行字数、行数、时长。

与 `validate.py` 的分工：

- `validate.py` 管**契约**——键集、字段、类型、对齐。违反即 error，触发重试/拆批。
- 本模块管**成片质量**——译文太长、闪得太快、行数超标。这些不是「翻错了」，
  片子照样能出，所以**默认不阻断流程**，而是产出可比较的度量值。

之所以做成度量而非硬门禁：本仓库要跑六模型质量消融
（见 `docs/quality_ablation_plan.md`）。「超速条数占比」「p95 CPS」这类数字
横向对比才有意义；把它做成 pass/fail 反而丢掉了区分度。

阈值默认取 Netflix 简体中文时间轴规范（每行 16 字、最多 2 行、9 字/秒），
可按需覆盖。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from pipeline.config import (
    ZH_MAX_CHARS_PER_LINE,
    ZH_MAX_CPS,
    ZH_MAX_DURATION_SEC,
    ZH_MAX_LINES,
    ZH_MIN_DURATION_SEC,
)
from pipeline.models import Cue

_TS_RE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})$")


def parse_ts_to_sec(ts: str) -> Optional[float]:
    """"HH:MM:SS,mmm" → 秒；解析不了返回 None（不抛，校验不该拖垮主流程）。"""
    m = _TS_RE.match((ts or "").strip())
    if not m:
        return None
    h, mi, s, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def _count_chars(line: str) -> int:
    """按字符计（中文按字，拉丁按字母），与 Netflix 的 per-line 字数口径一致。"""
    return len(line.strip())


@dataclass(frozen=True)
class CueQuality:
    id: str
    duration_sec: Optional[float]
    chars: int
    cps: Optional[float]
    lines: int
    max_line_chars: int
    issues: tuple[str, ...] = ()


@dataclass
class SubtitleQualityReport:
    n: int = 0
    n_missing: int = 0
    n_cps_over: int = 0
    n_line_over: int = 0
    n_lines_over: int = 0
    n_bad_duration: int = 0
    max_cps: Optional[float] = None
    p95_cps: Optional[float] = None
    per_cue: list[CueQuality] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """只输出聚合值——per_cue 可能上千条，不进 meta.json。"""
        return {
            "n": self.n,
            "n_missing": self.n_missing,
            "n_cps_over": self.n_cps_over,
            "n_line_over": self.n_line_over,
            "n_lines_over": self.n_lines_over,
            "n_bad_duration": self.n_bad_duration,
            "max_cps": self.max_cps,
            "p95_cps": self.p95_cps,
            "thresholds": {
                "max_cps": ZH_MAX_CPS,
                "max_chars_per_line": ZH_MAX_CHARS_PER_LINE,
                "max_lines": ZH_MAX_LINES,
            },
        }

    @property
    def issue_cues(self) -> list[CueQuality]:
        return [c for c in self.per_cue if c.issues]


def _percentile(values: list[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * frac, 2)


def check_subtitle_quality(
    cues: list[Cue],
    tr_map: dict[str, str],
    *,
    max_cps: float = ZH_MAX_CPS,
    max_chars_per_line: int = ZH_MAX_CHARS_PER_LINE,
    max_lines: int = ZH_MAX_LINES,
    min_duration_sec: float = ZH_MIN_DURATION_SEC,
    max_duration_sec: float = ZH_MAX_DURATION_SEC,
) -> SubtitleQualityReport:
    rep = SubtitleQualityReport(n=len(cues))
    cps_values: list[float] = []

    for c in cues:
        tr = (tr_map.get(c.id) or "").strip()
        if not tr:
            rep.n_missing += 1
            rep.per_cue.append(
                CueQuality(c.id, None, 0, None, 0, 0, ("missing translation",))
            )
            continue

        issues: list[str] = []
        lines = [ln for ln in tr.split("\n") if ln.strip()]
        max_line = max((_count_chars(ln) for ln in lines), default=0)
        chars = sum(_count_chars(ln) for ln in lines)

        start = parse_ts_to_sec(c.start)
        end = parse_ts_to_sec(c.end)
        duration = None if start is None or end is None else round(end - start, 3)

        cps: Optional[float] = None
        if duration is None or duration <= 0:
            rep.n_bad_duration += 1
            issues.append(f"bad duration: {c.start} --> {c.end}")
        else:
            if duration < min_duration_sec:
                rep.n_bad_duration += 1
                issues.append(f"duration {duration}s < {min_duration_sec}s")
            elif duration > max_duration_sec:
                issues.append(f"duration {duration}s > {max_duration_sec}s")
            cps = round(chars / duration, 2)
            cps_values.append(cps)
            if cps > max_cps:
                rep.n_cps_over += 1
                issues.append(f"cps {cps} > {max_cps}")

        if max_line > max_chars_per_line:
            rep.n_line_over += 1
            issues.append(f"line {max_line} chars > {max_chars_per_line}")
        if len(lines) > max_lines:
            rep.n_lines_over += 1
            issues.append(f"{len(lines)} lines > {max_lines}")

        rep.per_cue.append(
            CueQuality(c.id, duration, chars, cps, len(lines), max_line, tuple(issues))
        )

    rep.max_cps = round(max(cps_values), 2) if cps_values else None
    rep.p95_cps = _percentile(cps_values, 0.95)
    return rep
