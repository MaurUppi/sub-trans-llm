#!/usr/bin/env python3
"""
sub_processor.py — Single-file SRT subtitle processor (based on srt_handler v2.6)

Multi-language subtitle processing with Netflix-compliant standards,
intelligent line breaking, and automatic SDH removal.

Usage:
    python sub_processor.py input.srt [output.srt]

Supported languages: Chinese, English, Korean (auto-detected)
"""

import re
import sys
from collections import Counter
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Type

try:
    import chardet
except ImportError:
    chardet = None


# ============================================================================
#  Models
# ============================================================================


class Language(Enum):
    AUTO = "auto"
    CHINESE = "zh"
    ENGLISH = "en"
    KOREAN = "ko"
    JAPANESE = "ja"


class ContentType(Enum):
    ADULT = "adult"
    CHILDREN = "children"


@dataclass
class TimeCode:
    start: timedelta
    end: timedelta

    @classmethod
    def from_srt_time(cls, time_str: str) -> "TimeCode":
        start_str, end_str = time_str.split(" --> ")
        return cls(start=cls._parse_time(start_str), end=cls._parse_time(end_str))

    @staticmethod
    def _parse_time(time_str: str) -> timedelta:
        hours, minutes, seconds_ms = time_str.split(":")
        seconds, milliseconds = seconds_ms.split(",")
        return timedelta(
            hours=int(hours),
            minutes=int(minutes),
            seconds=int(seconds),
            milliseconds=int(milliseconds),
        )

    def to_srt_format(self) -> str:
        return f"{self._format_time(self.start)} --> {self._format_time(self.end)}"

    def _format_time(self, td: timedelta) -> str:
        total_seconds = int(td.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        milliseconds = td.microseconds // 1000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


@dataclass
class SubtitleBlock:
    index: int
    time_code: TimeCode
    lines: List[str]
    language: Optional[Language] = None
    is_sdh: bool = False
    is_split: bool = False  # True when produced by timeline splitting

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def character_count(self) -> int:
        return sum(len(line) for line in self.lines)

    def is_dialogue(self) -> bool:
        return any(line.strip().startswith("-") for line in self.lines)

    def is_sdh_only_block(self) -> bool:
        """Check if this block contains ONLY SDH markers without dialogue content."""
        if not self.lines:
            return False

        full_text = self.text.strip()
        if not full_text:
            return False

        music_patterns = [r"^♪+$", r"^🎵+$", r"^🎶+$"]
        audio_patterns = [
            r"^\[\s*.*?\s*\]$",
            r"^\(\s*.*?\s*\)$",
            r"^（\s*.*?\s*）$",
            r"^【\s*.*?\s*】$",
            r"^《\s*.*?\s*》$",
            r"^［\s*.*?\s*］$",
            r"^〔\s*.*?\s*〕$",
            r"^〈\s*.*?\s*〉$",
        ]

        for pattern in music_patterns:
            if re.match(pattern, full_text):
                return True
        for pattern in audio_patterns:
            if re.match(pattern, full_text):
                return True

        # Check each line individually
        for line in self.lines:
            line = line.strip()
            if not line:
                continue
            temp = line
            temp = re.sub(r"♪+|🎵+|🎶+", "", temp)
            temp = re.sub(r"\[.*?\]|\(.*?\)|【.*?】|《.*?》", "", temp)
            temp = re.sub(r"^-\s*", "", temp).strip()
            if temp:
                return False

        return True

    def clean_sdh_markers(self) -> "SubtitleBlock":
        """Create a new SubtitleBlock with SDH markers removed from dialogue lines."""
        cleaned_lines = []
        for line in self.lines:
            original = line.strip()
            if not original:
                continue
            cleaned = self._remove_sdh_from_line(original)
            if cleaned.strip():
                cleaned_lines.append(cleaned)

        return SubtitleBlock(
            index=self.index,
            time_code=self.time_code,
            lines=cleaned_lines,
            language=self.language,
            is_sdh=self.is_sdh,
        )

    def _remove_sdh_from_line(self, line: str) -> str:
        sdh_patterns = [
            r"\[\s*[^\]]*\s*\]",
            r"\(\s*[^)]*\s*\)",
            r"（\s*[^）]*\s*）",
            r"【\s*[^】]*\s*】",
            r"《\s*[^》]*\s*》",
            r"♪+", r"🎵+", r"🎶+",
            r"［\s*[^］]*\s*］",
            r"〔\s*[^〕]*\s*〕",
            r"〈\s*[^〉]*\s*〉",
            r"「\s*[^」]*\s*」",
        ]
        cleaned = line
        for pattern in sdh_patterns:
            cleaned = re.sub(pattern, "", cleaned)
        # Clean whitespace
        cleaned = re.sub(r"\s+", " ", cleaned)
        cleaned = re.sub(r"^-\s*", "- ", cleaned)
        cleaned = re.sub(r"^-\s*-\s*", "- ", cleaned)
        cleaned = cleaned.strip()
        if cleaned == "-":
            return ""
        return cleaned

    def get_reading_speed(self) -> float:
        duration_seconds = self.time_code.duration.total_seconds()
        if duration_seconds <= 0:
            return 0.0
        return self.character_count / duration_seconds


@dataclass
class SRTDocument:
    blocks: List[SubtitleBlock]
    source_file: Optional[str] = None
    detected_language: Optional[Language] = None
    encoding: str = "utf-8"

    @property
    def total_blocks(self) -> int:
        return len(self.blocks)

    def remove_sdh_blocks_and_clean_content(self) -> "SRTDocument":
        """Remove SDH-only blocks and clean SDH markers from remaining blocks."""
        processed = []
        for block in self.blocks:
            if block.is_sdh_only_block():
                continue
            cleaned = block.clean_sdh_markers()
            if cleaned and cleaned.lines and any(l.strip() for l in cleaned.lines):
                processed.append(cleaned)

        for i, block in enumerate(processed):
            block.index = i + 1

        return SRTDocument(
            blocks=processed,
            source_file=self.source_file,
            detected_language=self.detected_language,
            encoding=self.encoding,
        )

    def fix_rolling_window_overlaps(self) -> "SRTDocument":
        """Clip overlapping timecodes and drop empty-text blocks.

        YouTube auto-generated subtitles use a rolling-window format where
        consecutive entries deliberately overlap (each block ends ~2 s after
        the next one starts). When burned into video this causes two subtitle
        lines to appear simultaneously. This pass makes every end ≤ next start
        so ffmpeg never renders more than one line at a time.

        Empty-text blocks (no non-whitespace content) are also removed here
        because they can arise after translation or timecode clipping and will
        cause downstream parsers (e.g. burn_subtitles.py) to abort with
        "No subtitle text found".
        """
        if not self.blocks:
            return self

        fixed = list(self.blocks)

        # Clip end times to prevent overlap
        for i in range(len(fixed) - 1):
            if fixed[i].time_code.end > fixed[i + 1].time_code.start:
                clipped_tc = replace(fixed[i].time_code, end=fixed[i + 1].time_code.start)
                fixed[i] = replace(fixed[i], time_code=clipped_tc)

        # Drop blocks with no text content (translation gaps, clipping artefacts)
        fixed = [b for b in fixed if any(l.strip() for l in b.lines)]

        # Re-index sequentially
        for i, b in enumerate(fixed):
            b.index = i + 1

        return SRTDocument(
            blocks=fixed,
            source_file=self.source_file,
            detected_language=self.detected_language,
            encoding=self.encoding,
        )

    def to_srt_format(self) -> str:
        result = []
        for block in self.blocks:
            result.append(str(block.index))
            result.append(block.time_code.to_srt_format())
            result.extend(block.lines)
            result.append("")
        return "\n".join(result)


@dataclass
class ProcessingConfig:
    language: Language = Language.AUTO
    content_type: ContentType = ContentType.ADULT
    sdh_mode: bool = False
    force_encoding: Optional[str] = None
    no_speed_check: bool = False
    no_punct_fix: bool = False
    remove_sdh: bool = True
    remove_disfluency: bool = True  # Remove oral disfluencies (fillers, stutters, repeats)
    split_speed_factor: float = 1.5  # Speed limit multiplier for timeline-split blocks

    def get_character_limit(self, language: Language) -> int:
        limits = {
            Language.CHINESE: 18 if self.sdh_mode else 16,
            Language.ENGLISH: 42,
            Language.KOREAN: 16,
            Language.JAPANESE: 16 if self.sdh_mode else 13,
        }
        return limits.get(language, 42)

    def get_reading_speed_limit(self, language: Language) -> float:
        adult = {
            Language.CHINESE: 9.0,
            Language.ENGLISH: 20.0,
            Language.KOREAN: 12.0,
            Language.JAPANESE: 7.0 if self.sdh_mode else 4.0,
        }
        children = {
            Language.CHINESE: 7.0,
            Language.ENGLISH: 17.0,
            Language.KOREAN: 9.0,
            Language.JAPANESE: 7.0 if self.sdh_mode else 4.0,
        }
        speeds = adult if self.content_type == ContentType.ADULT else children
        return speeds.get(language, 20.0)


# ============================================================================
#  SRT Parser
# ============================================================================


class SRTParseError(Exception):
    def __init__(self, message: str, line_number: Optional[int] = None) -> None:
        self.line_number = line_number
        super().__init__(f"Line {line_number}: {message}" if line_number else message)


class SRTParser:
    def __init__(self) -> None:
        self.time_pattern = re.compile(
            r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})"
        )

    def parse_file(self, file_path: str, encoding: Optional[str] = None) -> SRTDocument:
        path = Path(file_path)
        if not path.exists():
            raise SRTParseError(f"File not found: {file_path}")

        content, encoding = self._read_text(path, encoding)

        blocks = self._parse_content(content)
        return SRTDocument(blocks=blocks, source_file=str(path), encoding=encoding)

    def _read_text(self, file_path: Path, encoding: Optional[str]) -> tuple[str, str]:
        candidates: List[str] = []
        seen = set()

        def add_candidate(value: Optional[str]) -> None:
            if not value:
                return
            normalized = value.strip()
            if not normalized:
                return
            lowered = normalized.lower()
            if lowered in seen:
                return
            seen.add(lowered)
            candidates.append(normalized)

        if encoding is not None:
            add_candidate(encoding)
            if encoding.lower().replace("_", "-") in {"utf-8", "utf8"}:
                add_candidate("utf-8-sig")
        else:
            if self._has_utf8_bom(file_path):
                add_candidate("utf-8-sig")
            add_candidate(self._detect_encoding(file_path))

        for fallback in ("utf-8-sig", "utf-8", "gb18030", "gbk", "cp936", "utf-16", "utf-16-le", "utf-16-be"):
            add_candidate(fallback)

        last_error: Optional[UnicodeDecodeError] = None
        for candidate in candidates:
            try:
                with open(file_path, "r", encoding=candidate) as f:
                    return f.read(), candidate
            except UnicodeDecodeError as e:
                last_error = e

        if last_error is not None:
            raise SRTParseError(f"Encoding error with {encoding or 'auto'}: {last_error}")
        raise SRTParseError(f"Unable to read file: {file_path}")

    def _has_utf8_bom(self, file_path: Path) -> bool:
        with open(file_path, "rb") as f:
            return f.read(3).startswith(b"\xef\xbb\xbf")

    def _detect_encoding(self, file_path: Path) -> str:
        if chardet is None:
            return "utf-8"
        with open(file_path, "rb") as f:
            raw_data = f.read()
        result = chardet.detect(raw_data)
        encoding = result.get("encoding", "utf-8")
        if encoding is None or encoding.lower() in ["ascii"]:
            encoding = "utf-8"
        return encoding

    def _parse_content(self, content: str) -> List[SubtitleBlock]:
        blocks = []
        lines = content.lstrip("\ufeff").strip().splitlines()
        current_line = 0

        while current_line < len(lines):
            try:
                block, next_line = self._parse_block(lines, current_line)
                if block:
                    blocks.append(block)
                current_line = next_line
            except Exception as e:
                raise SRTParseError(str(e), current_line + 1)

        return blocks

    def _parse_block(self, lines: List[str], start_line: int):
        current_line = start_line

        while current_line < len(lines) and not lines[current_line].strip():
            current_line += 1

        if current_line >= len(lines):
            return None, current_line

        index_line = lines[current_line].strip()
        if not index_line.isdigit():
            raise SRTParseError(f"Expected subtitle index, got: {index_line}")

        index = int(index_line)
        current_line += 1

        if current_line >= len(lines):
            raise SRTParseError("Unexpected end of file after index")

        time_line = lines[current_line].strip()
        if not self.time_pattern.match(time_line):
            raise SRTParseError(f"Invalid time format: {time_line}")

        time_code = TimeCode.from_srt_time(time_line)
        current_line += 1

        text_lines = []
        while current_line < len(lines):
            line = lines[current_line]
            stripped = line.strip()
            if stripped.isdigit() and (current_line + 1 < len(lines)):
                next_l = lines[current_line + 1].strip()
                if self.time_pattern.match(next_l):
                    break
            text_lines.append(line.rstrip())
            current_line += 1

        while text_lines and not text_lines[-1].strip():
            text_lines.pop()

        if not text_lines:
            # Bilingual translation may produce a block with an empty first line
            # (missing translation) but valid timing — skip silently rather than
            # raising, so the surrounding blocks are preserved.
            return None, current_line

        return SubtitleBlock(index=index, time_code=time_code, lines=text_lines), current_line

    def write_file(self, document: SRTDocument, output_path: str, encoding: Optional[str] = None) -> None:
        if encoding is None:
            encoding = document.encoding
        content = document.to_srt_format()
        with open(output_path, "w", encoding=encoding) as f:
            f.write(content)


# ============================================================================
#  Language Detector
# ============================================================================


class LanguageDetector:
    def __init__(self) -> None:
        self.chinese_pattern = re.compile(r"[\u4e00-\u9fff]")
        self.korean_pattern = re.compile(r"[\uac00-\ud7af]")
        self.hiragana_pattern = re.compile(r"[\u3040-\u309f]")
        self.katakana_pattern = re.compile(r"[\u30a0-\u30ff]")
        self.ascii_pattern = re.compile(r"[a-zA-Z]")
        self.chinese_punct = re.compile(r"[。！？，：\u201c\u201d（）【】《》]")
        self.korean_punct = re.compile(r"[。！？，：\u201c\u201d（）【】《》]")
        self.japanese_punct = re.compile(r"[。！？、：\u201c\u201d（）【】《》〈〉]")

    def detect_language(self, document: SRTDocument) -> Language:
        if not document.blocks:
            return Language.ENGLISH
        combined_text = " ".join(block.text for block in document.blocks)
        char_counts = self._count_characters(combined_text)
        scores = self._calculate_language_scores(char_counts)
        return max(scores.keys(), key=lambda lang: scores[lang])

    def detect_block_languages(self, document: SRTDocument) -> None:
        for block in document.blocks:
            block.language = self._detect_block_language(block)

    def _detect_block_language(self, block: SubtitleBlock) -> Language:
        text = block.text
        if not text.strip():
            return Language.ENGLISH
        char_counts = self._count_characters(text)
        scores = self._calculate_language_scores(char_counts)
        return max(scores.keys(), key=lambda lang: scores[lang])

    def detect_line_language(self, line: str) -> Language:
        if not line.strip():
            return Language.ENGLISH
        char_counts = self._count_characters(line)
        scores = self._calculate_language_scores(char_counts)
        return max(scores.keys(), key=lambda lang: scores[lang])

    def _count_characters(self, text: str) -> Dict[str, int]:
        return {
            "chinese": len(self.chinese_pattern.findall(text)),
            "korean": len(self.korean_pattern.findall(text)),
            "hiragana": len(self.hiragana_pattern.findall(text)),
            "katakana": len(self.katakana_pattern.findall(text)),
            "ascii": len(self.ascii_pattern.findall(text)),
            "chinese_punct": len(self.chinese_punct.findall(text)),
            "korean_punct": len(self.korean_punct.findall(text)),
            "japanese_punct": len(self.japanese_punct.findall(text)),
            "total_chars": len(text.replace(" ", "")),
        }

    def _calculate_language_scores(self, cc: Dict[str, int]) -> Dict[Language, float]:
        total = max(cc["total_chars"], 1)
        scores: Dict[Language, float] = {
            Language.CHINESE: 0.0,
            Language.ENGLISH: 0.0,
            Language.KOREAN: 0.0,
            Language.JAPANESE: 0.0,
        }

        scores[Language.CHINESE] = (cc["chinese"] / total) * 10 + (cc["chinese_punct"] / total) * 2

        cjk_total = cc["chinese"] + cc["korean"] + cc["hiragana"] + cc["katakana"]
        cjk_ratio = cjk_total / total
        ascii_ratio = cc["ascii"] / total
        scores[Language.ENGLISH] = ascii_ratio * (10 if cjk_ratio < 0.1 else 2)

        scores[Language.KOREAN] = (cc["korean"] / total) * 10 + (cc["korean_punct"] / total) * 2

        jp_script = (cc["hiragana"] + cc["katakana"]) / total
        jp_with_kanji = jp_script + (cc["chinese"] / total) * 0.5
        scores[Language.JAPANESE] = jp_with_kanji * 10 + (cc["japanese_punct"] / total) * 2

        for lang in scores:
            if scores[lang] < 0.01:
                scores[lang] = 0.0

        return scores


# ============================================================================
#  Chinese Processor
# ============================================================================


class ChineseProcessor:
    def __init__(self, config: ProcessingConfig) -> None:
        self.config = config
        self.punctuation = "。！？，：；\u201c\u201d''（）【】《》"
        self.helper_words = {"的", "地", "得", "了", "吧", "呢", "啊", "哦", "嗯", "呀", "哇", "吗", "嘛"}
        self.sentence_endings = "。！？"
        self.dialogue_pattern = re.compile(r"^-\s*(.*)$")

    def process_block(self, block: SubtitleBlock) -> SubtitleBlock:
        if not block.lines:
            return block

        lines = self._process_dialogue_format(block.lines)
        if len(lines) > 1:
            lines = self._smart_merge_lines(lines)
        lines = self._apply_line_breaking(lines)
        if not self.config.no_punct_fix:
            lines = self._add_missing_punctuation(lines)

        return SubtitleBlock(
            index=block.index, time_code=block.time_code, lines=lines,
            language=block.language, is_sdh=block.is_sdh,
        )

    def _process_dialogue_format(self, lines: List[str]) -> List[str]:
        result = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            m = self.dialogue_pattern.match(line)
            if m:
                result.append(f"- {m.group(1).strip()}")
            else:
                result.append(line)
        return result

    def _smart_merge_lines(self, lines: List[str]) -> List[str]:
        if len(lines) <= 1:
            return lines
        merged = []
        current = ""
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if self._should_merge(current, line):
                current = current + line if current else line
            else:
                if current:
                    merged.append(current)
                current = line
        if current:
            merged.append(current)
        return merged

    def _should_merge(self, current: str, next_line: str) -> bool:
        if not current:
            return False
        if current[-1] in self.sentence_endings:
            return False
        if current.startswith("- ") != next_line.startswith("- "):
            return False
        limit = self.config.get_character_limit(self.config.language)
        return len(current) + len(next_line) <= limit

    def _apply_line_breaking(self, lines: List[str]) -> List[str]:
        result = []
        for line in lines:
            if line.strip():
                result.extend(self._break_line(line))
        return result

    def _break_line(self, line: str) -> List[str]:
        limit = self.config.get_character_limit(self.config.language)
        if len(line) <= limit:
            return [line]
        if len(line) - limit < 3:
            return [line]

        pos = self._find_break_pos(line, limit)
        if pos == -1:
            pos = limit

        first = line[:pos].rstrip()
        second = line[pos:].lstrip()

        if len(second) < 5:
            return [line]

        result = [first]
        if second and len(second) < len(line):
            result.extend(self._break_line(second))
        return result

    def _find_break_pos(self, line: str, limit: int) -> int:
        start = max(0, limit - 10)
        end = min(len(line), limit + 3)

        for i in range(end - 1, start - 1, -1):
            if i < len(line) and line[i] in self.helper_words and i + 1 <= limit:
                return i + 1
        for i in range(end - 1, start - 1, -1):
            if i < len(line) and line[i] in self.punctuation and i + 1 <= limit:
                return i + 1
        for i in range(end - 1, start - 1, -1):
            if i < len(line) and line[i] == " " and i <= limit:
                return i
        return -1

    def _add_missing_punctuation(self, lines: List[str]) -> List[str]:
        if not lines:
            return lines
        result = lines.copy()
        last = result[-1].strip()
        if (
            last
            and last[-1] not in self.punctuation
            and not last.endswith("...")
            and not last.startswith("♪")
            and not last.endswith("，")
            and not self._is_continuation(last)
        ):
            result[-1] = last + "。"
        return result

    def _is_continuation(self, line: str) -> bool:
        line = line.strip()
        if not line:
            return False
        if line[-1] in "，、":
            return True
        words = line.split()
        if words and words[-1] in {"，", "、", "和", "或", "但", "而", "因为", "所以", "如果", "那么"}:
            return True
        if len(line) < 8:
            return True
        return False

    def validate_reading_speed(self, block: SubtitleBlock) -> bool:
        if self.config.no_speed_check:
            return True
        return block.get_reading_speed() <= self.config.get_reading_speed_limit(self.config.language)


# ============================================================================
#  English Processor
# ============================================================================


class EnglishProcessor:
    def __init__(self, config: ProcessingConfig) -> None:
        self.config = config
        self.conjunctions = {
            "and", "but", "or", "nor", "for", "so", "yet", "because", "since",
            "although", "though", "while", "whereas", "however", "therefore",
            "moreover", "furthermore", "nevertheless", "nonetheless",
        }
        self.prepositions = {
            "in", "on", "at", "by", "for", "with", "from", "to", "of", "about",
            "under", "over", "through", "between", "among", "during", "before",
            "after", "above", "below", "across", "around", "behind", "beside",
        }
        self.sentence_endings = ".!?"
        self.punctuation = ".,!?;:\"\\'()[]{}—–-"
        self.dialogue_pattern = re.compile(r"^-\s*(.*)$")

    def process_block(self, block: SubtitleBlock) -> SubtitleBlock:
        if not block.lines:
            return block

        lines = self._process_dialogue_format(block.lines)
        if len(lines) > 1:
            lines = self._smart_merge_lines(lines)
        lines = self._apply_line_breaking(lines)
        lines = self._merge_short_lines(lines)

        return SubtitleBlock(
            index=block.index, time_code=block.time_code, lines=lines,
            language=block.language, is_sdh=block.is_sdh,
        )

    def _process_dialogue_format(self, lines: List[str]) -> List[str]:
        result = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            m = self.dialogue_pattern.match(line)
            if m:
                result.append(f"- {m.group(1).strip()}")
            else:
                result.append(line)
        return result

    def _smart_merge_lines(self, lines: List[str]) -> List[str]:
        if len(lines) <= 1:
            return lines
        merged = []
        current = ""
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if self._should_merge(current, line):
                if current:
                    if current.startswith("- ") and line.startswith("- "):
                        merged.append(current)
                        current = line
                    elif current.startswith("- ") and not line.startswith("- "):
                        current += " " + line
                    elif not current.startswith("- ") and line.startswith("- "):
                        merged.append(current)
                        current = line
                    else:
                        current += " " + line
                else:
                    current = line
            else:
                if current:
                    merged.append(current)
                current = line
        if current:
            merged.append(current)
        return merged

    def _should_merge(self, current: str, next_line: str) -> bool:
        if not current:
            return False
        if current[-1] in self.sentence_endings:
            return False
        if current.startswith("- ") and next_line.startswith("- "):
            return False

        cur_len = len(current.strip())
        nxt_len = len(next_line.strip())
        limit = self.config.get_character_limit(self.config.language)

        if cur_len < 25 or nxt_len < 25:
            return len(current) + 1 + len(next_line) <= limit
        if current.rstrip().endswith(".") and nxt_len < 20:
            return len(current) + 1 + len(next_line) <= limit

        current_words = current.strip().split()
        if current_words and current_words[-1].lower() in {
            "a", "an", "the", "of", "in", "on", "at", "to", "for", "with", "by", "and", "or", "but",
        }:
            return len(current) + 1 + len(next_line) <= limit

        return len(current) + 1 + len(next_line) <= limit

    def _apply_line_breaking(self, lines: List[str]) -> List[str]:
        result = []
        for line in lines:
            if line.strip():
                result.extend(self._break_line(line))
        return result

    def _break_line(self, line: str) -> List[str]:
        limit = self.config.get_character_limit(self.config.language)
        if len(line) <= limit:
            return [line]
        if not self._should_break(line, limit):
            return [line]

        pos = self._find_break_pos(line, limit)
        if pos == -1:
            pos = self._find_word_boundary(line, limit)
        if pos == -1:
            pos = limit

        first = line[:pos].rstrip()
        second = line[pos:].lstrip()
        result = [first]
        if second and len(second) < len(line):
            result.extend(self._break_line(second))
        return result

    def _should_break(self, line: str, limit: int) -> bool:
        if len(line) <= limit:
            return False
        remaining = line[limit:].strip()
        if not remaining:
            return False
        if len(remaining.split()) < 4:
            return False
        pos = self._find_break_pos(line, limit)
        if pos > 0:
            second = line[pos:].strip()
            if len(second) < 20:
                return False
            if len(second.split()) < 3:
                return False
        else:
            return False
        return True

    def _find_break_pos(self, line: str, limit: int) -> int:
        start = max(0, limit - 20)
        end = min(len(line), limit)

        for i in range(end - 1, start - 1, -1):
            if i < len(line) and line[i] in ".,!?;:":
                return i + 1

        # Search for conjunctions/prepositions as whole words within [start, end].
        # Use word-boundary regex to avoid matching substrings inside longer
        # words (e.g. "in" inside "transcribing").
        line_lower = line.lower()
        for target_set in (self.conjunctions, self.prepositions):
            best_pos = -1
            for m in re.finditer(r"\b(\w+)\b", line_lower):
                if m.group(1) in target_set and start <= m.start() <= end:
                    # Prefer the rightmost match within range (closest to limit)
                    if m.start() > best_pos:
                        best_pos = m.start()
            if best_pos >= 0:
                return best_pos

        return -1

    def _find_word_boundary(self, line: str, limit: int) -> int:
        start = max(0, limit - 15)
        end = min(len(line), limit + 5)
        for i in range(end - 1, start - 1, -1):
            if i < len(line) and line[i] == " ":
                return i
        return -1

    def _merge_short_lines(self, lines: List[str]) -> List[str]:
        if len(lines) <= 1:
            return lines
        result = []
        i = 0
        while i < len(lines):
            current = lines[i].strip()
            if not current:
                result.append(lines[i])
                i += 1
                continue

            if i + 1 < len(lines) and lines[i + 1].strip():
                nxt = lines[i + 1].strip()
                cur_dlg = current.startswith("- ")
                nxt_dlg = nxt.startswith("- ")
                should_merge = False
                if len(nxt) < 20:
                    should_merge = True
                elif len(current) < 25:
                    should_merge = True
                if cur_dlg and nxt_dlg:
                    should_merge = False

                if should_merge:
                    merged = None
                    if cur_dlg and not nxt_dlg:
                        merged = f"- {current[2:].strip()} {nxt}"
                    elif not cur_dlg and nxt_dlg:
                        merged = None
                    else:
                        merged = f"{current} {nxt}"

                    limit = self.config.get_character_limit(self.config.language)
                    if merged and len(merged) <= limit:
                        result.append(merged)
                        i += 2
                        continue

            result.append(lines[i])
            i += 1
        return result

    def validate_reading_speed(self, block: SubtitleBlock) -> bool:
        if self.config.no_speed_check:
            return True
        return block.get_reading_speed() <= self.config.get_reading_speed_limit(self.config.language)


# ============================================================================
#  Korean Processor
# ============================================================================


class KoreanProcessor:
    def __init__(self, config: ProcessingConfig) -> None:
        self.config = config
        self.punctuation = "。！？，：；\u201c\u201d''（）【】《》"
        self.helper_particles = {
            "은", "는", "이", "가", "을", "를", "에", "에서", "로", "으로",
            "와", "과", "의", "도", "만", "까지", "부터", "보다", "처럼",
            "다", "요", "죠", "네", "지", "니", "까", "야", "아", "어",
            "고", "서", "면", "려고", "하고", "때문에", "가지고",
        }
        self.sentence_endings = "。！？"
        self.dialogue_pattern = re.compile(r"^-\s*(.*)$")

    def process_block(self, block: SubtitleBlock) -> SubtitleBlock:
        if not block.lines:
            return block

        lines = self._process_dialogue_format(block.lines)
        if len(lines) > 1:
            lines = self._smart_merge_lines(lines)
        lines = self._apply_line_breaking(lines)
        if not self.config.no_punct_fix:
            lines = self._add_missing_punctuation(lines)

        return SubtitleBlock(
            index=block.index, time_code=block.time_code, lines=lines,
            language=block.language, is_sdh=block.is_sdh,
        )

    def _process_dialogue_format(self, lines: List[str]) -> List[str]:
        result = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            m = self.dialogue_pattern.match(line)
            if m:
                result.append(f"- {m.group(1).strip()}")
            else:
                result.append(line)
        return result

    def _smart_merge_lines(self, lines: List[str]) -> List[str]:
        if len(lines) <= 1:
            return lines
        merged = []
        current = ""
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if self._should_merge(current, line):
                current = (current + " " + line) if current else line
            else:
                if current:
                    merged.append(current)
                current = line
        if current:
            merged.append(current)
        return merged

    def _should_merge(self, current: str, next_line: str) -> bool:
        if not current:
            return False
        if current[-1] in self.sentence_endings:
            return False
        if current.startswith("- ") != next_line.startswith("- "):
            return False
        limit = self.config.get_character_limit(self.config.language)
        return len(current) + 1 + len(next_line) <= limit

    def _apply_line_breaking(self, lines: List[str]) -> List[str]:
        result = []
        for line in lines:
            if line.strip():
                result.extend(self._break_line(line))
        return result

    def _break_line(self, line: str) -> List[str]:
        limit = self.config.get_character_limit(self.config.language)
        if len(line) <= limit:
            return [line]
        if len(line) - limit < 3:
            return [line]

        pos = self._find_break_pos(line, limit)
        if pos == -1:
            pos = limit

        first = line[:pos].rstrip()
        second = line[pos:].lstrip()
        if len(second) < 4:
            return [line]

        result = [first]
        if second and len(second) < len(line):
            result.extend(self._break_line(second))
        return result

    def _find_break_pos(self, line: str, limit: int) -> int:
        start = max(0, limit - 8)
        end = min(len(line), limit + 3)

        for i in range(end - 1, start - 1, -1):
            if i < len(line) and line[i] == " " and i <= limit:
                return i
        for i in range(end - 2, start - 1, -1):
            if i >= 0 and i + 2 <= len(line):
                two = line[i:i + 2]
                if two in self.helper_particles and i + 2 <= limit:
                    return i + 2
        for i in range(end - 1, start - 1, -1):
            if i < len(line) and line[i] in self.helper_particles and i + 1 <= limit:
                return i + 1
        for i in range(end - 1, start - 1, -1):
            if i < len(line) and line[i] in self.punctuation and i + 1 <= limit:
                return i + 1
        return -1

    def _add_missing_punctuation(self, lines: List[str]) -> List[str]:
        if not lines:
            return lines
        result = lines.copy()
        last = result[-1].strip()
        if (
            last
            and last[-1] not in self.punctuation
            and not last.endswith("...")
            and not last.startswith("♪")
            and not last.endswith(("고", "서", "면", "며", "는데", "지만", "하고"))
            and not self._is_continuation(last)
        ):
            result[-1] = last + "."
        return result

    def _is_continuation(self, line: str) -> bool:
        line = line.strip()
        if not line:
            return False
        endings = {
            "고", "서", "면", "며", "는데", "지만", "하고", "가지고", "때문에",
            "하면서", "다가", "으면서", "으니까", "니까", "하여", "해서", "에서",
            "으로", "로", "와", "과",
        }
        for e in endings:
            if line.endswith(e):
                return True
        if len(line) < 6:
            return True
        return False

    def validate_reading_speed(self, block: SubtitleBlock) -> bool:
        if self.config.no_speed_check:
            return True
        return block.get_reading_speed() <= self.config.get_reading_speed_limit(self.config.language)


# ============================================================================
#  Disfluency Remover
# ============================================================================


class DisfluencyRemover:
    """Remove oral disfluencies from English subtitle text.

    Targets three categories:
      1. Filler words/phrases: uh, um, you know, I mean
      2. Stutters and immediate word repeats: "they they", "e- e- each"
      3. Whitespace/punctuation artifacts left after removal

    Only operates on English text.  CJK content is passed through unchanged.
    """

    # --- Filler patterns (applied first) ---
    _FILLER_PATTERNS = [
        # Standalone fillers with optional trailing comma
        (re.compile(r"\b[Uu]h\b,?\s*"), ""),
        (re.compile(r"\b[Uu]m\b,?\s*"), ""),
        # "you know" as mid-sentence filler
        (re.compile(r",?\s*\byou know\b,?\s*"), ", "),
        # "I mean" as mid-sentence filler
        (re.compile(r",?\s*\bI mean\b,?\s*"), ", "),
        # Redundant "like" after comma: ", like, X" → ", X"
        (re.compile(r"(?<=,)\s*like,?\s+"), " "),
        # Double "like": "like like" → "like"
        (re.compile(r"\blike like\b"), "like"),
    ]

    # --- Repetition patterns (applied second) ---
    _REPETITION_PATTERNS = [
        # Stutters: "e- e- each" → "each", "s- something" → "something"
        (re.compile(r"\b(\w)-\s+(?:\w+-\s+)*"), ""),
        # Triple+ same word: "the the the" → "the"
        (re.compile(r"\b(\w+)(\s+\1){2,}\b", re.IGNORECASE), r"\1"),
        # Two-word phrase repeat: "you know you know" → "you know"
        (re.compile(r"\b(\w+\s+\w+)\s+\1\b", re.IGNORECASE), r"\1"),
        # Immediate word repeat: "they they" → "they"
        (re.compile(r"\b(\w+)\s+\1\b", re.IGNORECASE), r"\1"),
    ]

    # --- Cleanup patterns (applied last) ---
    _CLEANUP_PATTERNS = [
        (re.compile(r"\s*,\s*,\s*"), ", "),     # double commas
        (re.compile(r"^\s*,\s*"), ""),           # leading comma
        (re.compile(r",\s*\."), "."),            # comma before period
        (re.compile(r"\s{2,}"), " "),            # multiple spaces
    ]

    # CJK detection for skipping non-English lines
    _CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]")

    def clean_line(self, text: str) -> str:
        """Remove disfluencies from a single subtitle line."""
        # Skip lines that are primarily CJK
        if self._CJK_RE.search(text) and len(self._CJK_RE.findall(text)) > len(text) * 0.3:
            return text

        result = text

        for pattern, replacement in self._FILLER_PATTERNS:
            result = pattern.sub(replacement, result)

        for pattern, replacement in self._REPETITION_PATTERNS:
            result = pattern.sub(replacement, result)

        for pattern, replacement in self._CLEANUP_PATTERNS:
            result = pattern.sub(replacement, result)

        result = result.strip()

        # Restore leading uppercase if it was lost
        if result and result[0].islower() and text and text[0].isupper():
            result = result[0].upper() + result[1:]

        return result

    def process_document(self, document: SRTDocument) -> SRTDocument:
        """Apply disfluency removal to all blocks in a document."""
        new_blocks = []
        for block in document.blocks:
            new_lines = []
            for line in block.lines:
                cleaned = self.clean_line(line)
                if cleaned:
                    new_lines.append(cleaned)
            if new_lines:
                new_blocks.append(SubtitleBlock(
                    index=block.index,
                    time_code=block.time_code,
                    lines=new_lines,
                    language=block.language,
                    is_sdh=block.is_sdh,
                    is_split=block.is_split,
                ))
            # Drop blocks that become empty after cleaning
        # Re-index
        for i, b in enumerate(new_blocks):
            b.index = i + 1
        return SRTDocument(
            blocks=new_blocks,
            source_file=document.source_file,
            detected_language=document.detected_language,
            encoding=document.encoding,
        )


# ============================================================================
#  Main Processor
# ============================================================================


class SRTProcessor:
    def __init__(self, config: ProcessingConfig) -> None:
        self.config = config
        self.parser = SRTParser()
        self.language_detector = LanguageDetector()
        self.processors: Dict[Language, Type] = {
            Language.CHINESE: ChineseProcessor,
            Language.ENGLISH: EnglishProcessor,
            Language.KOREAN: KoreanProcessor,
        }

    # Minimum gap between sub-blocks after timeline splitting (milliseconds)
    SPLIT_GAP_MS = 40

    def process_file(self, input_path: str, output_path: str) -> SRTDocument:
        document = self.parser.parse_file(input_path, encoding=self.config.force_encoding)

        # Phase 0: fix YouTube rolling-window overlapping timecodes
        document = document.fix_rolling_window_overlaps()

        if self.config.language == Language.AUTO:
            detected = self.language_detector.detect_language(document)
            document.detected_language = detected
            self.config.language = detected
        else:
            document.detected_language = self.config.language

        self.language_detector.detect_block_languages(document)

        if self.config.remove_sdh:
            document = document.remove_sdh_blocks_and_clean_content()

        # Phase 1b: remove oral disfluencies (fillers, stutters, repeats)
        if self.config.remove_disfluency:
            document = DisfluencyRemover().process_document(document)

        processed = self._process_document(document)

        # Phase 2: split overlong blocks by timeline
        split_doc = self._split_overlong_blocks(processed)

        self.parser.write_file(split_doc, output_path, encoding=self.config.force_encoding)
        return split_doc

    def _process_document(self, document: SRTDocument) -> SRTDocument:
        processed_blocks = []
        for block in document.blocks:
            if self._is_bilingual_block(block):
                processed_blocks.append(self._process_bilingual_block(block))
            else:
                lang = block.language or document.detected_language or Language.ENGLISH
                proc_cls = self.processors.get(lang)
                if proc_cls:
                    processed_blocks.append(proc_cls(self.config).process_block(block))
                else:
                    processed_blocks.append(block)

        return SRTDocument(
            blocks=processed_blocks,
            source_file=document.source_file,
            detected_language=document.detected_language,
            encoding=document.encoding,
        )

    # ------------------------------------------------------------------
    #  Timeline splitting — break oversized blocks into timed sub-blocks
    # ------------------------------------------------------------------

    def _block_needs_split(self, block: SubtitleBlock) -> bool:
        """Determine whether a block must be split.

        Triggers:
          1. More than 2 non-empty lines
          2. Any single line exceeds its language's character limit
          3. Reading speed exceeds the language limit
        """
        non_empty = [l for l in block.lines if l.strip()]
        if len(non_empty) > 2:
            return True

        for line in non_empty:
            lang = self.language_detector.detect_line_language(line)
            if len(line) > self.config.get_character_limit(lang):
                return True

        if not self.config.no_speed_check:
            lang = block.language or self.config.language or Language.ENGLISH
            speed_limit = self.config.get_reading_speed_limit(lang)
            if block.get_reading_speed() > speed_limit:
                return True

        return False

    def _flatten_block_text(self, block: SubtitleBlock) -> str:
        """Join all lines of a block into a single string for re-chunking."""
        parts = []
        for line in block.lines:
            stripped = line.strip()
            if stripped:
                parts.append(stripped)
        # For Chinese / Korean, consecutive CJK lines merge without space;
        # for English, merge with space.  Detect per-pair.
        if not parts:
            return ""
        result = parts[0]
        for i in range(1, len(parts)):
            prev_char = result[-1] if result else ""
            next_char = parts[i][0] if parts[i] else ""
            # If both sides are CJK, no space; otherwise add space
            if self._is_cjk_char(prev_char) and self._is_cjk_char(next_char):
                result += parts[i]
            else:
                result += " " + parts[i]
        return result

    @staticmethod
    def _is_cjk_char(ch: str) -> bool:
        if not ch:
            return False
        cp = ord(ch)
        return (
            0x4E00 <= cp <= 0x9FFF      # CJK Unified
            or 0x3400 <= cp <= 0x4DBF   # CJK Extension A
            or 0xAC00 <= cp <= 0xD7AF   # Korean Hangul
            or 0x3040 <= cp <= 0x309F   # Hiragana
            or 0x30A0 <= cp <= 0x30FF   # Katakana
            or ch in "。！？，：；""''（）【】《》"
        )

    def _rechunk_lines(self, text: str, lang: Language) -> List[List[str]]:
        """Split flat text into groups of ≤2 lines, each within char limit.

        Returns a list of chunks; each chunk is a list of 1-2 line strings.
        """
        char_limit = self.config.get_character_limit(lang)

        # First, produce all individual lines (each ≤ char_limit)
        raw_lines = self._break_text_to_lines(text, lang, char_limit)

        # Group into chunks of max 2 lines
        chunks: List[List[str]] = []
        i = 0
        while i < len(raw_lines):
            if i + 1 < len(raw_lines):
                chunks.append([raw_lines[i], raw_lines[i + 1]])
                i += 2
            else:
                chunks.append([raw_lines[i]])
                i += 1
        return chunks

    def _break_text_to_lines(self, text: str, lang: Language, char_limit: int) -> List[str]:
        """Break text into lines each within char_limit, using language-aware logic."""
        if len(text) <= char_limit:
            return [text]

        proc_cls = self.processors.get(lang)
        if proc_cls:
            # Use the language processor's line-breaking, but with forced splitting
            # for lines that still exceed the limit
            processor = proc_cls(self.config)
            temp_block = SubtitleBlock(
                index=0,
                time_code=TimeCode(start=timedelta(), end=timedelta(seconds=1)),
                lines=[text],
                language=lang,
            )
            result_block = processor.process_block(temp_block)
            lines = [l for l in result_block.lines if l.strip()]

            # Force-split any lines still over the limit
            final = []
            for line in lines:
                if len(line) <= char_limit:
                    final.append(line)
                else:
                    final.extend(self._force_split_line(line, char_limit, lang))
            return final if final else [text]

        # Fallback: brute-force word-boundary split
        return self._force_split_line(text, char_limit, lang)

    def _force_split_line(self, text: str, char_limit: int, lang: Language) -> List[str]:
        """Last-resort split that guarantees every line ≤ char_limit."""
        if len(text) <= char_limit:
            return [text]

        results = []
        remaining = text
        while len(remaining) > char_limit:
            # Try to find a space/boundary near the limit
            pos = -1
            for i in range(min(char_limit, len(remaining)) - 1, max(0, char_limit - 20) - 1, -1):
                if i < len(remaining) and remaining[i] == " ":
                    pos = i
                    break
            # For CJK, any position is a valid break
            if pos == -1 and lang in (Language.CHINESE, Language.KOREAN, Language.JAPANESE):
                pos = char_limit
            if pos == -1:
                pos = char_limit  # hard break as absolute fallback

            results.append(remaining[:pos].rstrip())
            remaining = remaining[pos:].lstrip()

        if remaining.strip():
            results.append(remaining.strip())
        return results

    def _allocate_timecodes(
        self, chunks: List[List[str]], original_tc: TimeCode
    ) -> List[TimeCode]:
        """Allocate time proportionally to each chunk by character count, with gaps.

        Each chunk gets time proportional to its total character count.
        A 40ms gap is inserted between consecutive chunks.
        """
        n = len(chunks)
        if n <= 1:
            return [original_tc]

        gap = timedelta(milliseconds=self.SPLIT_GAP_MS)
        total_duration = original_tc.duration
        total_gap = gap * (n - 1)

        # If gaps would consume ≥50% of duration, reduce gap
        if total_gap >= total_duration * 0.5:
            gap = timedelta(milliseconds=max(10, int(total_duration.total_seconds() * 500 / (n - 1))))
            total_gap = gap * (n - 1)

        usable = total_duration - total_gap
        if usable.total_seconds() <= 0:
            usable = total_duration
            gap = timedelta(0)

        # Character counts per chunk
        char_counts = []
        for chunk in chunks:
            char_counts.append(sum(len(line) for line in chunk))
        total_chars = sum(char_counts) or 1

        timecodes = []
        cursor = original_tc.start
        for i, cc in enumerate(char_counts):
            ratio = cc / total_chars
            chunk_dur = timedelta(seconds=usable.total_seconds() * ratio)
            # Ensure minimum duration of 200ms per chunk
            if chunk_dur.total_seconds() < 0.2:
                chunk_dur = timedelta(milliseconds=200)
            chunk_end = cursor + chunk_dur
            # Clamp to not exceed original end
            if chunk_end > original_tc.end:
                chunk_end = original_tc.end
            timecodes.append(TimeCode(start=cursor, end=chunk_end))
            cursor = chunk_end + gap
            if cursor > original_tc.end:
                cursor = original_tc.end

        return timecodes

    def _split_overlong_blocks(self, document: SRTDocument) -> SRTDocument:
        """Post-processing pass: split blocks that exceed Netflix standards.

        For each block that needs splitting:
          1. Flatten all lines into a single text
          2. Re-chunk into groups of ≤2 lines, each within char limit
          3. Allocate time proportionally with gaps
          4. Produce new sub-blocks

        Preserves blocks that are already compliant.
        """
        new_blocks: List[SubtitleBlock] = []
        split_count = 0

        for block in document.blocks:
            if not self._block_needs_split(block):
                new_blocks.append(block)
                continue

            # Determine primary language for this block
            lang = block.language or document.detected_language or Language.ENGLISH

            # For bilingual blocks, detect if lines have mixed languages
            line_langs = []
            for line in block.lines:
                if line.strip():
                    line_langs.append(self.language_detector.detect_line_language(line))

            is_bilingual = len(set(line_langs)) > 1

            if is_bilingual:
                # Bilingual: split each language's lines separately,
                # then interleave them into paired sub-blocks
                sub_blocks = self._split_bilingual_block(block, document.detected_language)
                new_blocks.extend(sub_blocks)
                if len(sub_blocks) > 1:
                    split_count += 1
                continue
            else:
                # Monolingual: straightforward split
                flat_text = self._flatten_block_text(block)
                if not flat_text.strip():
                    new_blocks.append(block)
                    continue

                chunks = self._rechunk_lines(flat_text, lang)
                if len(chunks) <= 1:
                    # Re-chunking didn't split, keep the processed block
                    new_blocks.append(SubtitleBlock(
                        index=block.index, time_code=block.time_code,
                        lines=chunks[0] if chunks else block.lines,
                        language=block.language, is_sdh=block.is_sdh,
                    ))
                    continue

                timecodes = self._allocate_timecodes(chunks, block.time_code)
                for chunk_lines, tc in zip(chunks, timecodes):
                    new_blocks.append(SubtitleBlock(
                        index=0, time_code=tc, lines=chunk_lines,
                        language=block.language, is_sdh=block.is_sdh,
                        is_split=True,
                    ))
                split_count += 1

        # Post-pass: merge trailing fragments back into the previous cue.
        # A "fragment" is a split-produced cue whose text is too short to be
        # independently translatable (< 15 chars or < 3 words).  Merging it
        # back may cause the previous cue's lines to exceed the character
        # limit, but a slightly overlong cue is far better than a fragment
        # that downstream LLM translation will either leave empty or
        # duplicate.
        new_blocks = self._merge_trailing_fragments(new_blocks)

        # Re-index all blocks sequentially
        for i, blk in enumerate(new_blocks):
            blk.index = i + 1

        if split_count > 0:
            print(f"Timeline split: {split_count} blocks expanded into sub-blocks")

        return SRTDocument(
            blocks=new_blocks,
            source_file=document.source_file,
            detected_language=document.detected_language,
            encoding=document.encoding,
        )

    # Minimum text length / word count for a cue to stand alone.
    # Below these thresholds a split-produced cue is considered a fragment
    # and will be merged back into the preceding cue.
    FRAGMENT_MIN_CHARS = 15
    FRAGMENT_MIN_WORDS = 3

    def _merge_trailing_fragments(
        self, blocks: List[SubtitleBlock]
    ) -> List[SubtitleBlock]:
        """Merge trailing fragment cues back into the previous cue.

        Scans the block list and, for every cue produced by timeline
        splitting whose text is below the fragment thresholds, absorbs it
        into the preceding cue:

          * The fragment's text lines are appended to the previous cue
            (joined with a space for English, no space for CJK).
          * The previous cue's end time is extended to the fragment's end
            time, so the merged cue covers both time spans.
          * The fragment cue is removed from the list.

        Only split-produced cues (``is_split=True``) are candidates.
        The very first cue in the list is never treated as a fragment
        because there is nothing before it to merge into.
        """
        if len(blocks) < 2:
            return blocks

        merged: List[SubtitleBlock] = [blocks[0]]

        for blk in blocks[1:]:
            if blk.is_split and self._is_fragment(blk):
                prev = merged[-1]
                # Merge text: flatten both cues, join, then re-wrap into
                # a simple line list (no re-breaking — accept overlong).
                prev_text = self._flatten_block_text(prev)
                frag_text = self._flatten_block_text(blk)

                if prev_text and frag_text:
                    # Use space joiner for Latin text, no space for CJK
                    if self._is_cjk_char(prev_text[-1]) and self._is_cjk_char(frag_text[0]):
                        joined = prev_text + frag_text
                    else:
                        joined = prev_text + " " + frag_text
                    new_lines = [joined]
                elif frag_text:
                    new_lines = [frag_text]
                else:
                    new_lines = prev.lines  # nothing to merge

                # Extend timeline to cover the fragment
                new_tc = TimeCode(start=prev.time_code.start, end=blk.time_code.end)

                merged[-1] = SubtitleBlock(
                    index=prev.index,
                    time_code=new_tc,
                    lines=new_lines,
                    language=prev.language,
                    is_sdh=prev.is_sdh,
                    is_split=prev.is_split,
                )
            else:
                merged.append(blk)

        if len(merged) < len(blocks):
            print(f"Fragment merge: {len(blocks) - len(merged)} trailing fragments absorbed")

        return merged

    def _is_fragment(self, block: SubtitleBlock) -> bool:
        """Check whether a block's text is too short to stand alone."""
        text = " ".join(l.strip() for l in block.lines if l.strip())
        if not text:
            return True
        if len(text) < self.FRAGMENT_MIN_CHARS:
            return True
        words = text.split()
        if len(words) < self.FRAGMENT_MIN_WORDS:
            return True
        return False

    def _split_bilingual_block(
        self, block: SubtitleBlock, doc_lang: Optional[Language]
    ) -> List[SubtitleBlock]:
        """Split a bilingual block while keeping language pairs aligned.

        Strategy: separate lines by language, split each independently,
        then zip them back together so each sub-block has 1 line per language
        (max 2 lines total).
        """
        lang_a_lines = []  # primary language lines
        lang_b_lines = []  # secondary language lines
        lang_a = None
        lang_b = None

        for line in block.lines:
            stripped = line.strip()
            if not stripped:
                continue
            line_lang = self.language_detector.detect_line_language(stripped)
            if lang_a is None:
                lang_a = line_lang
            if line_lang == lang_a:
                lang_a_lines.append(stripped)
            else:
                if lang_b is None:
                    lang_b = line_lang
                lang_b_lines.append(stripped)

        if not lang_a:
            lang_a = doc_lang or Language.ENGLISH
        if not lang_b:
            lang_b = doc_lang or Language.ENGLISH

        # Flatten each language group
        flat_a = (" " if lang_a == Language.ENGLISH else "").join(lang_a_lines)
        flat_b = (" " if lang_b == Language.ENGLISH else "").join(lang_b_lines)

        # Break into single lines
        limit_a = self.config.get_character_limit(lang_a)
        limit_b = self.config.get_character_limit(lang_b)

        lines_a = self._break_text_to_lines(flat_a, lang_a, limit_a) if flat_a.strip() else []
        lines_b = self._break_text_to_lines(flat_b, lang_b, limit_b) if flat_b.strip() else []

        # Pair them up: each sub-block gets [line_a_i, line_b_i]
        n = max(len(lines_a), len(lines_b))
        if n == 0:
            return [block]

        chunks = []
        for i in range(n):
            pair = []
            if i < len(lines_a):
                pair.append(lines_a[i])
            if i < len(lines_b):
                pair.append(lines_b[i])
            chunks.append(pair)

        if len(chunks) <= 1:
            return [SubtitleBlock(
                index=block.index, time_code=block.time_code,
                lines=chunks[0] if chunks else block.lines,
                language=block.language, is_sdh=block.is_sdh,
            )]

        timecodes = self._allocate_timecodes(chunks, block.time_code)
        result = []
        for chunk_lines, tc in zip(chunks, timecodes):
            result.append(SubtitleBlock(
                index=0, time_code=tc, lines=chunk_lines,
                language=block.language, is_sdh=block.is_sdh,
                is_split=True,
            ))
        return result

    def _is_bilingual_block(self, block: SubtitleBlock) -> bool:
        if len(block.lines) < 2:
            return False
        languages = set()
        for line in block.lines:
            if line.strip():
                languages.add(self.language_detector.detect_line_language(line))
                if len(languages) > 1:
                    return True
        return False

    def _process_bilingual_block(self, block: SubtitleBlock) -> SubtitleBlock:
        processed_lines = []
        i = 0
        while i < len(block.lines):
            line = block.lines[i]
            if not line.strip():
                processed_lines.append(line)
                i += 1
                continue

            line_lang = self.language_detector.detect_line_language(line)
            consecutive = [line]
            j = i + 1
            while j < len(block.lines):
                nxt = block.lines[j]
                if not nxt.strip():
                    j += 1
                    continue
                if self.language_detector.detect_line_language(nxt) == line_lang:
                    consecutive.append(nxt)
                    j += 1
                else:
                    break

            proc_cls = self.processors.get(line_lang)
            if proc_cls:
                temp_block = SubtitleBlock(
                    index=block.index, time_code=block.time_code,
                    lines=consecutive, language=line_lang, is_sdh=block.is_sdh,
                )
                temp_config = replace(self.config, language=line_lang)
                result_block = proc_cls(temp_config).process_block(temp_block)
                processed_lines.extend(result_block.lines)
            else:
                processed_lines.extend(consecutive)

            i = j

        return SubtitleBlock(
            index=block.index, time_code=block.time_code,
            lines=processed_lines, language=block.language, is_sdh=block.is_sdh,
        )

    def validate_document(self, document: SRTDocument) -> Dict:
        warnings = []
        for block in document.blocks:
            lang = block.language or document.detected_language or Language.ENGLISH
            proc_cls = self.processors.get(lang)
            if proc_cls:
                proc = proc_cls(self.config)
                for idx, line in enumerate(block.lines):
                    if not line.strip():
                        continue
                    line_lang = self.language_detector.detect_line_language(line)
                    limit = self.config.get_character_limit(line_lang)
                    if len(line) > limit:
                        warnings.append(
                            f"Block {block.index}: Line {idx + 1} exceeds "
                            f"character limit ({len(line)} > {limit} {line_lang.value})"
                        )
                if not self.config.no_speed_check:
                    # Timeline-split blocks get a relaxed speed limit because
                    # their display duration is constrained by the original
                    # audio pace — we cannot slow down speech.
                    base_limit = self.config.get_reading_speed_limit(lang)
                    speed_limit = (
                        base_limit * self.config.split_speed_factor
                        if block.is_split
                        else base_limit
                    )
                    actual = block.get_reading_speed()
                    if actual > speed_limit:
                        warnings.append(
                            f"Block {block.index}: Reading speed too fast "
                            f"({actual:.1f} > {speed_limit:.0f} chars/sec)"
                            + (" [split]" if block.is_split else "")
                        )

        total = len(document.blocks)
        warn_blocks = len(set(
            w.split(":")[0] for w in warnings if "Block" in w
        ))
        compliant = total - warn_blocks
        rate = (compliant / total * 100) if total > 0 else 0

        return {
            "total_blocks": total,
            "warnings": warnings,
            "compliance_rate": rate,
            "compliant_blocks": compliant,
        }


# ============================================================================
#  CLI Entry Point
# ============================================================================


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__.strip())
        print()
        print("Arguments:")
        print("  input.srt          Input SRT file to process")
        print("  output.srt         Output file (default: <input>_processed.srt)")
        sys.exit(0 if len(sys.argv) > 1 else 1)

    input_path = sys.argv[1]
    if not Path(input_path).exists():
        print(f"Error: File not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    if len(sys.argv) >= 3 and not sys.argv[2].startswith("-"):
        output_path = sys.argv[2]
    else:
        p = Path(input_path)
        output_path = str(p.parent / f"{p.stem}_processed{p.suffix}")

    config = ProcessingConfig()
    processor = SRTProcessor(config)

    try:
        print(f"Processing: {input_path}")
        result = processor.process_file(input_path, output_path)

        lang = result.detected_language.value if result.detected_language else "unknown"
        print(f"Language detected: {lang}")
        print(f"Total blocks: {result.total_blocks}")

        validation = processor.validate_document(result)
        rate = validation["compliance_rate"]
        icon = "✅" if rate >= 90 else "⚠️" if rate >= 70 else "❌"
        print(f"{icon} Compliance: {rate:.1f}% ({validation['compliant_blocks']}/{validation['total_blocks']})")
        if validation["warnings"]:
            print(f"Warnings: {len(validation['warnings'])}")

        print(f"Output: {output_path}")
    except SRTParseError as e:
        print(f"Parse error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()