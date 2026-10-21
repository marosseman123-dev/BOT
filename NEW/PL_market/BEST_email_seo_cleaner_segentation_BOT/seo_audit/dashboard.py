"""Small ANSI console helpers shared by unibot's live dashboard."""
from __future__ import annotations

import sys


def _enable_vt() -> None:
    """Enable ANSI escape processing on Windows consoles. No-op elsewhere."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


def _bar(frac: float, width: int = 32, color: bool = True) -> str:
    frac = max(0.0, min(1.0, frac))
    filled = int(round(frac * width))
    body = "#" * filled + "-" * (width - filled)
    if not color:
        return body
    reset = "\033[0m"
    c = "\033[32m" if frac >= 1.0 else "\033[36m"
    return f"{c}{body}{reset}"


def _hms(seconds: float) -> str:
    seconds = max(0, int(seconds or 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


def _pct(frac: float) -> str:
    return f"{max(0.0, min(1.0, frac)) * 100:.1f}%"
