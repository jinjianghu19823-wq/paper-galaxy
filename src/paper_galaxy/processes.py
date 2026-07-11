"""Non-destructive cross-platform process liveness checks."""

from __future__ import annotations

import errno
import os
from typing import Any

_WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WINDOWS_STILL_ACTIVE = 259
_WINDOWS_ACCESS_DENIED = 5
_WINDOWS_INVALID_PARAMETER = 87


def process_is_alive(pid: int) -> bool:
    """Return whether a PID appears live without terminating it.

    Unknown or access-denied results are treated conservatively as live.
    Windows never uses ``os.kill(pid, 0)`` because CPython maps non-console
    signals there to ``TerminateProcess``.
    """

    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if _uses_windows_process_probe():
        return _windows_process_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OverflowError:
        return False
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        if exc.errno in {errno.EPERM, errno.EACCES}:
            return True
        return True
    return True


def _uses_windows_process_probe() -> bool:
    return os.name == "nt"


def _windows_process_is_alive(pid: int) -> bool:
    """Probe a Windows process handle without sending a signal."""

    import ctypes
    from ctypes import wintypes

    kernel32 = _windows_kernel32()
    handle = kernel32.OpenProcess(
        _WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        pid,
    )
    if not handle:
        ctypes_api: Any = ctypes
        error_code = int(ctypes_api.get_last_error())
        if error_code == _WINDOWS_INVALID_PARAMETER:
            return False
        if error_code == _WINDOWS_ACCESS_DENIED:
            return True
        return True
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return int(exit_code.value) == _WINDOWS_STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _windows_kernel32() -> Any:
    """Load and type the minimal non-destructive Windows process API."""

    import ctypes
    from ctypes import wintypes

    ctypes_api: Any = ctypes
    kernel32 = ctypes_api.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32
