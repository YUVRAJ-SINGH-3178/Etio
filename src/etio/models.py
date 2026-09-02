"""Public data models used by Etio."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Confidence = Literal["low", "medium", "high"]


@dataclass(frozen=True, slots=True)
class FailureContext:
    """Failure text plus optional file hints retained for compatibility."""

    message: str
    file_hints: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Diagnosis:
    """A redacted, structured diagnosis that is safe to report."""

    summary: str
    root_cause: str
    suggested_patch: str | None
    confidence: Confidence = "low"
