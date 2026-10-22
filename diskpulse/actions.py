"""Kill process / open exe folder."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os

from .disk import PROCESSENTRY32W, TH32CS_SNAPPROCESS

kernel32 = ctypes.windll.kernel32
kernel32.OpenProcess.restype = ctypes.c_void_p
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

_CRITICAL = {
    "system",
    "registry",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "services.exe",
    "lsass.exe",
    "winlogon.exe",
    "svchost.exe",
    "explorer.exe",
    "dwm.exe",
}


def _open(pid: int, access: int) -> int:
    return int(kernel32.OpenProcess(access, False, pid) or 0)


def process_path(pid: int) -> str | None:
    h = _open(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    if not h:
        return None
    try:
        size = wt.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return None
        return buf.value or None
    finally:
        kernel32.CloseHandle(h)


def exe_name(pid: int, fallback: str) -> str:
    path = process_path(pid)
    if path:
        return os.path.basename(path)
    return fallback


def pids_named(exe: str) -> list[int]:
    want = exe.lower()
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE or not snap:
        return []
    pe = PROCESSENTRY32W()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    out: list[int] = []
    me = os.getpid()
    try:
        if not kernel32.Process32FirstW(snap, ctypes.byref(pe)):
            return []
        while True:
            pid = int(pe.th32ProcessID)
            if pe.szExeFile.lower() == want and pid and pid != me:
                out.append(pid)
            if not kernel32.Process32NextW(snap, ctypes.byref(pe)):
                break
    finally:
        kernel32.CloseHandle(snap)
    return out


def _blocked(exe: str) -> bool:
    return exe.lower() in _CRITICAL


def kill_pid(pid: int, exe: str) -> str | None:
    name = exe_name(pid, exe)
    if pid <= 4 or _blocked(name):
        return "этот процесс трогать нельзя"
    if pid == os.getpid():
        return "нельзя снять сам DiskPulse"
    h = _open(pid, PROCESS_TERMINATE)
    if not h:
        return "нет доступа (попробуй от имени администратора)"
    try:
        if not kernel32.TerminateProcess(h, 1):
            return "Windows не завершил процесс"
        return None
    finally:
        kernel32.CloseHandle(h)


def kill_named(exe: str) -> tuple[int, str | None]:
    name = exe if exe.lower().endswith(".exe") else exe
    if _blocked(name):
        return 0, "этот процесс трогать нельзя"
    n = 0
    last: str | None = None
    for pid in pids_named(name):
        err = kill_pid(pid, name)
        if err:
            last = err
            continue
        n += 1
    if n == 0:
        return 0, last or "нечего снимать"
    return n, None


def open_folder(pid: int) -> str | None:
    path = process_path(pid)
    if not path or not os.path.isfile(path):
        return "не удалось найти файл процесса"
    folder = os.path.dirname(path)
    try:
        ctypes.windll.shell32.ShellExecuteW(None, "open", "explorer.exe", f'/select,"{path}"', folder, 1)
        return None
    except Exception:
        os.startfile(folder)  # noqa: S606
        return None
