"""Fetch diagnostics.

Single domain (prints status or the raw exception):
    .venv_unified/Scripts/python -B -m unibot.probe seznam.cz

Load test -- runs the FIRST N input domains through the real fetch_domain at
concurrency C and reports alive/dead/error counts + any raised exception:
    .venv_unified/Scripts/python -B -m unibot.probe --load 200 48
"""
from __future__ import annotations

import asyncio
import collections
import sys
import time
import traceback

import httpx

from . import fetch as F
from .config import Config
from .pipeline import _session, _iter_domains


async def _single(domain: str) -> None:
    cfg = Config()
    print(f"[probe] domain={domain}")
    alive, host, port, reason = await F._liveness(domain, cfg)
    print(f"[probe] liveness: alive={alive} host={host!r} port={port} reason={reason!r}")
    if not alive:
        print("[probe] -> DEAD by DNS. Not an httpx problem.")
        return
    url = f"https://{host}" if port == 443 else f"http://{host}"
    async with _session(cfg) as c:
        print(f"[probe] GET {url}  (bot client: verify=False)")
        try:
            r = await c.get(url, headers=F._headers(cfg, domain))
            print(f"[probe] OK status={r.status_code} http={r.http_version} "
                  f"content-encoding={r.headers.get('content-encoding','(none)')} "
                  f"decoded_chars={len(r.text)}")
        except Exception as e:
            traceback.print_exc()
            print(f"[probe] FAILED: {type(e).__name__}: {e}")


async def _load(n: int, conc: int) -> None:
    cfg = Config()
    cfg.max_concurrency = conc
    domains = []
    for d in _iter_domains(cfg):
        domains.append(d)
        if len(domains) >= n:
            break
    print(f"[probe] load test: {len(domains)} domains @ concurrency {conc}")
    alive = dead = raised = 0
    exc_types = collections.Counter()
    first_tb = []
    sem = asyncio.Semaphore(conc)
    async with _session(cfg) as c:
        async def one(dom):
            nonlocal alive, dead, raised
            async with sem:
                try:
                    hp, _ = await F.fetch_domain(c, dom, cfg)
                except Exception as e:
                    raised += 1
                    exc_types[type(e).__name__] += 1
                    if not first_tb:
                        first_tb.append((dom, traceback.format_exc()))
                    return
                if hp.get("ok"):
                    alive += 1
                else:
                    dead += 1
        t0 = time.monotonic()
        await asyncio.gather(*[one(d) for d in domains], return_exceptions=True)
        el = time.monotonic() - t0
    print(f"[probe] done in {el:.1f}s  ALIVE={alive}  DEAD={dead}  RAISED={raised}  "
          f"rate={len(domains)/el:.1f}/s")
    print(f"[probe] fetch errors (httpx GET): {F.error_summary()}")
    if exc_types:
        print(f"[probe] fetch_domain RAISED: {dict(exc_types)}")
        dom, tb = first_tb[0]
        print(f"[probe] --- first raise, domain={dom} ---")
        print(tb)
    if alive == 0:
        print("[probe] -> STILL broken. Paste this whole output.")
    else:
        print(f"[probe] -> WORKS ({alive}/{len(domains)} alive).")


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--load":
        n = int(args[1]) if len(args) > 1 else 200
        conc = int(args[2]) if len(args) > 2 else 48
        asyncio.run(_load(n, conc))
    else:
        asyncio.run(_single(args[0] if args else "seznam.cz"))


if __name__ == "__main__":
    main()
