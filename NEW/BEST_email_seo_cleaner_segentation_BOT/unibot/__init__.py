"""Unified Lead Intelligence Bot.

One crawl per domain -> email extraction (EmailHunter) + SEO/tech audit
(seo_audit) + agency/competitor classification (Cleaner) -> global cleanup ->
one unified export. Replaces running three separate bots that each re-crawled the
same homepages.

This package reuses the two mature sibling packages in place rather than copying
their code:
  * ../seo_audit          -- SEO/tech/platform analysis (pure functions)
  * ../EmailHunterV2/hunter-- email extraction + scoring (pure functions)
The bootstrap below puts both on sys.path.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))              # .../UnifiedBot/unibot
_ROOT = os.path.dirname(os.path.dirname(_HERE))                 # .../SEO_segentacia_BOT
_EH = os.path.join(_ROOT, "EmailHunterV2")                      # EmailHunter package root
for _p in (_ROOT, _EH):
    if _p not in sys.path:
        sys.path.insert(0, _p)

__version__ = "1.0.0"
