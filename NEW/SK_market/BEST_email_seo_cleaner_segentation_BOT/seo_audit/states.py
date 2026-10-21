"""Evidence status: how much we could actually verify about a page."""
from __future__ import annotations

from enum import Enum


class EvidenceStatus(str, Enum):
    CONFIRMED = "confirmed"      # page loaded cleanly, full HTML in hand
    PARTIAL = "partial"          # loaded but looks like a JS shell (SPA) -- limited evidence
    BLOCKED = "blocked"          # WAF / bot-challenge / 403/429 -- couldn't really see the page
    UNAVAILABLE = "unavailable"  # fetch failed outright

    def __str__(self) -> str:   # so f"{status}" prints the plain value
        return self.value
