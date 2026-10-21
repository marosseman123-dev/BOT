"""Finding severity, ordered worst-to-best for ranking/sorting."""
from __future__ import annotations

from enum import IntEnum


class Severity(IntEnum):
    GOOD = 0        # explicitly fine, not an issue
    INFO = 1        # informational, no action needed
    LOW = 2
    MEDIUM = 3
    HIGH = 4
    CRITICAL = 5

    def __str__(self) -> str:
        return self.name
