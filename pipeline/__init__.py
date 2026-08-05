"""Modular subtitle translation pipeline (extracted from translate.py)."""
from __future__ import annotations

# Public surface re-exported as modules land; orchestrator fills later.
from pipeline.models import Cue, TranslateResult, ValidateReport

__all__ = ["Cue", "ValidateReport", "TranslateResult"]
