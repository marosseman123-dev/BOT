"""Exclusive ownership of the entire local bot installation."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import ctypes
from ctypes import wintypes


class RunLock:
    def __init__(self, out_dir: str):
        # One bot process owns all runs.  A run-scoped lock would still allow
        # --run A and --run B to overload the same PC, Wi-Fi and disk.
        self.path = os.path.join(out_dir, ".unibot.global.lock")
        self._owned = False

    def acquire(self) -> None:
        payload = json.dumps({"pid": os.getpid(), "host": socket.gethostname(), "started": time.time(),
                              "process_started": _process_started(os.getpid())})
        for attempt in range(3):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                if attempt < 2 and self._remove_stale_or_replace_confirmed_lock():
                    continue
                fd = None
                break
        if fd is None:
            try:
                with open(self.path, encoding="utf-8") as f:
                    holder = json.load(f)
            except Exception:
                holder = {}
            raise RuntimeError("bot is already active: %s (pid=%s host=%s). Stop the existing bot before starting another."
                               % (self.path, holder.get("pid", "unknown"), holder.get("host", "unknown")))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        self._owned = True

    def _remove_stale_or_replace_confirmed_lock(self) -> bool:
        """Recover stale locks or replace only an exactly identified bot tree."""
        try:
            with open(self.path, encoding="utf-8") as f:
                holder = json.load(f)
            pid = int(holder.get("pid"))
            if holder.get("host") != socket.gethostname() or pid <= 0:
                return False
            process_started = _process_started(pid)
            if process_started is None:
                os.unlink(self.path)
                return True
            expected = holder.get("process_started")
            if expected is None:
                # Legacy lock: a live PID without an exact birth time is not
                # enough proof to kill it. The operator must end it manually.
                return False
            try:
                confirmed = abs(float(expected) - process_started) < 2.0
            except (TypeError, ValueError):
                return False
            if not confirmed:
                # PID reuse: this is a different process and must never be
                # touched. Its lock is stale, but do not unlink it either.
                return False
            if os.name == "nt":
                # /T includes ProcessPoolExecutor children, preventing orphan
                # parsers from surviving a replaced parent process.
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               check=False, timeout=8)
            else:
                os.kill(pid, 15)
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline:
                if _process_started(pid) is None:
                    os.unlink(self.path)
                    return True
                time.sleep(0.1)
            return False
        except (OSError, ValueError, TypeError, json.JSONDecodeError, subprocess.TimeoutExpired):
            return False

    def release(self) -> None:
        if self._owned:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
            self._owned = False


def _process_started(pid: int) -> float | None:
    """Return a process birth time, or None when that PID does not exist.

    Windows PID values are reusable; ``os.kill(pid, 0)`` cannot prove a lock
    belongs to the process currently holding that numeric PID.  Creation time
    gives the lock an unforgeable local identity without third-party modules.
    """
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return 0.0
        except OSError:
            return None
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not ctypes.windll.kernel32.GetProcessTimes(handle, ctypes.byref(creation),
                                                      ctypes.byref(exit_time), ctypes.byref(kernel),
                                                      ctypes.byref(user)):
            return None
        ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return ticks / 10_000_000 - 11_644_473_600
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)
