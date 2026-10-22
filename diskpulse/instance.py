"""Single instance guard."""

from __future__ import annotations

import ctypes
import sys
import time

_MUTEX_HANDLE = None
MUTEX_NAME = "Local\\DiskPulse_Mutex_vlx0"
WINDOW_TITLE = "DiskPulse - darkshade"

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
SW_RESTORE = 9


def claim_or_exit() -> None:
    global _MUTEX_HANDLE
    kernel32.SetLastError(0)
    _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if kernel32.GetLastError() != 183:
        return
    hwnd = 0
    for _ in range(10):
        hwnd = user32.FindWindowW(None, WINDOW_TITLE)
        if hwnd:
            break
        time.sleep(0.05)
    if hwnd:
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
    sys.exit(0)
