"""Audit thresholds. All knobs the scoring/detection logic reads live here so
they can be tuned without touching audit.py."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple


@dataclass
class Config:
    # PHP versions still receiving upstream security support (heuristic --
    # we have no live CVE feed, so "EOL" = major.minor below this floor).
    php_supported_floor: Tuple[int, int] = (8, 3)

    # jQuery major versions considered outdated/insecure.
    jquery_eol_majors: Tuple[int, ...] = (1, 2)

    # title / meta description length bounds considered "ok" for SEO.
    title_len_range: Tuple[int, int] = (10, 70)
    meta_desc_len_range: Tuple[int, int] = (20, 320)

    good_word_count: int = 300
    min_word_count: int = 60

    fast_load_s: float = 1.5
    ok_load_s: float = 3.0
    slow_load_s: float = 6.0

    large_page_kb: int = 1500

    many_plugins_threshold: int = 25

    excellent_score: int = 90
