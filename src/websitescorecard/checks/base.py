"""Base types for pluggable website checks."""

from __future__ import annotations
from typing import Optional

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CheckResult:
    status: str
    error: str | None = None
    details: Optional[str] = None

class Check(Protocol):
    name: str
    column: str
    error_column: str | None
    details_column: Optional[str] = None

    def run(self, url: str) -> CheckResult: ...
