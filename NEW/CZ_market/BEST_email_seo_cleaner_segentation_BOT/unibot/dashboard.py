"""Live console dashboard for the unified bot -- adds lead-focused stats:
alive/dead domains, domains with a contact, and total emails found.
"""
from __future__ import annotations

from seo_audit.dashboard import _bar, _hms, _pct, _enable_vt  # reuse ANSI helpers

_RESET = "\033[0m"; _BOLD = "\033[1m"; _CYAN = "\033[36m"
_GREEN = "\033[32m"; _YELLOW = "\033[33m"; _RED = "\033[31m"; _GREY = "\033[90m"


def build_panel(*, run, total, done, pending, rate, eta, elapsed, conc,
                alive, dead, with_contact, emails, ok, retrying, failed,
                note="", color=True, width=32) -> list:
    frac = (done / total) if total else 0.0
    C = (lambda s, c: f"{c}{s}{_RESET}") if color else (lambda s, c: s)
    n = lambda x: f"{x:,}"
    bar = _bar(frac, width, color)
    lines = [
        C("  unibot", _BOLD + _CYAN) + C(f"  ·  {run}", _GREY),
        f"  [{bar}]  {C(f'{frac*100:5.1f}%', _BOLD)}   {C(n(done), _BOLD)} / {n(total)}",
        f"  {C('rate', _GREY)} {rate:6.1f}/s   {C('elapsed', _GREY)} {_hms(elapsed)}   "
        f"{C('ETA', _GREY)} {_hms(eta)}   {C('pending', _GREY)} {n(pending)}",
        f"  {C('alive', _GREEN)} {n(alive)}   {C('dead', _RED)} {n(dead)}   "
        f"{C('with contact', _CYAN)} {n(with_contact)}   {C('emails', _BOLD)} {n(emails)}",
        f"  {C('ok', _GREEN)} {n(ok)}   {C('retrying', _YELLOW)} {n(retrying)}   "
        f"{C('failed', _RED if failed else _GREY)} {n(failed)}    {C('conc', _GREY)} {conc}",
    ]
    if note:
        lines.append(f"  {C('fetch', _GREY)} {note}")
    return lines
