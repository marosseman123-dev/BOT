"""Local, dependency-free runtime sizing.

The profile is a ceiling, not a promise of a network rate.  The adaptive gate
still backs off on real latency and blocks; this module merely prevents a small
VPS from being treated like the development laptop (or vice versa).
"""
from __future__ import annotations

import ctypes
import os
import platform
import re
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeProfile:
    logical_cpus: int
    memory_bytes: int
    tcp_dynamic_ports: int | None
    process_workers: int
    min_concurrency: int
    max_concurrency: int
    max_http_concurrency: int


def _memory_bytes() -> int:
    if os.name == "nt":
        class _MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        stat = _MEMORYSTATUSEX(); stat.dwLength = ctypes.sizeof(stat)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return int(stat.ullTotalPhys)
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, ValueError, OSError):
        return 0


def _windows_tcp_port_count() -> int | None:
    if os.name != "nt":
        return None
    try:
        out = subprocess.check_output(["netsh", "int", "ipv4", "show", "dynamicport", "tcp"],
                                      text=True, stderr=subprocess.DEVNULL, timeout=2)
        found = re.search(r"Number of Ports\s*:\s*(\d+)", out)
        return int(found.group(1)) if found else None
    except (OSError, subprocess.SubprocessError):
        return None


def detect_runtime() -> RuntimeProfile:
    cpus = max(1, os.cpu_count() or 1)
    memory = _memory_bytes()
    ports = _windows_tcp_port_count()
    # Eight parse workers leave physical CPU capacity for the event loop, DNS,
    # Windows and other applications on a 16-thread laptop.  Smaller machines
    # get proportionally fewer workers and a lower connection ceiling.
    workers = min(8, max(2, cpus // 2))
    if cpus >= 12 and memory >= 16 * 1024**3:
        # Live measurement on the Ryzen 7 / Wi-Fi profile: 40 concurrent
        # domains raised homepage p50 to 4.7 s and cut settled throughput from
        # 7.1/s (at 28) to 3.7/s.  The request gate therefore stays at the
        # measured 28-request ceiling while 32 domains leave scheduling
        # headroom for DNS, parsing and slow hosts.
        low, high, http_high = 24, 32, 28
    elif cpus >= 6 and memory >= 8 * 1024**3:
        low, high, http_high = 16, 28, 20
    else:
        low, high, http_high = 8, 16, 12
    # At most one quarter of the ephemeral range may be budgeted.  Six is the
    # intentional worst-case request fan-out of one domain.
    if ports:
        high = min(high, max(8, ports // 24))
    return RuntimeProfile(cpus, memory, ports, workers, low, high, http_high)


def apply_runtime_profile(cfg) -> RuntimeProfile:
    profile = detect_runtime()
    if not getattr(cfg, "auto_profile", True):
        return profile
    cfg.process_workers = profile.process_workers
    if not getattr(cfg, "_concurrency_explicit", False):
        cfg.min_concurrency = profile.min_concurrency
        cfg.max_concurrency = profile.max_concurrency
    if not getattr(cfg, "_http_concurrency_explicit", False):
        cfg.max_http_concurrency = profile.max_http_concurrency
    return profile
