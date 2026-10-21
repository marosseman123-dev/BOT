"""seo_audit -- pure-function SEO/tech/security audit engine.

Rebuilt as a standalone sibling package consumed by unibot (see
unibot/__init__.py, which puts this package's parent dir on sys.path).
Operates entirely on an already-fetched page (models.FetchResult) -- it never
performs its own HTTP requests, so it can be reused by any crawler.
"""
from __future__ import annotations

__version__ = "1.0.0"
