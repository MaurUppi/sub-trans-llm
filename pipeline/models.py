from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from model_client import Usage


@dataclass
class Cue:
    id: str
    seq: int
    start: str
    end: str
    text: str


@dataclass
class ValidateReport:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    parsed: Optional[dict[str, dict[str, str]]] = None
    stats: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "stats": self.stats,
            "parsed_keys": list(self.parsed.keys()) if self.parsed else [],
        }


@dataclass
class TranslateResult:
    model_alias: str
    model_id: str
    usage: Usage
    status: str
    incomplete_reason: Optional[str]
    validate: ValidateReport
    bilingual_srt: Optional[str]
    raw_text: str
    elapsed_sec: float
    input_map: dict[str, str] = field(default_factory=dict)
    instructions: str = ""
    cues: list[Cue] = field(default_factory=list)
    batch_count: int = 1
    batch_size: int = 0
    batch_jobs: int = 1
    batch_reports: list[dict[str, Any]] = field(default_factory=list)
    episode_summary: str = ""
    summary_usage: Optional[Usage] = None

    @property
    def ok(self) -> bool:
        return (
            self.status == "completed"
            and not self.incomplete_reason
            and self.validate.ok
            and bool(self.bilingual_srt)
        )

    def meta_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "model_alias": self.model_alias,
            "model_id": self.model_id,
            "status": self.status,
            "incomplete_reason": self.incomplete_reason,
            "elapsed_sec": round(self.elapsed_sec, 3),
            "ok": self.ok,
            "batch_count": self.batch_count,
            "batch_size": self.batch_size,
            "batch_jobs": self.batch_jobs,
            "batch_reports": self.batch_reports,
            "episode_summary_chars": len(self.episode_summary or ""),
            "usage": {
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
                "reasoning_tokens": self.usage.reasoning_tokens,
                "total_tokens": self.usage.total_tokens,
            },
            "validate": self.validate.to_dict(),
        }
        if self.summary_usage is not None:
            d["summary_usage"] = {
                "input_tokens": self.summary_usage.input_tokens,
                "output_tokens": self.summary_usage.output_tokens,
                "reasoning_tokens": self.summary_usage.reasoning_tokens,
                "total_tokens": self.summary_usage.total_tokens,
            }
        return d


@dataclass
class BatchOutcome:
    batch_index: int
    cues: list[Cue]
    input_map: dict[str, str]
    raw_text: str
    status: str
    incomplete_reason: Optional[str]
    usage: Usage
    model_id: str
    alias: str
    validate: ValidateReport
    attempt_notes: list[str] = field(default_factory=list)
