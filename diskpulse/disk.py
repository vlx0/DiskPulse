"""Volume space and per-process IO rates. Win32 only."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time
from dataclasses import dataclass

kernel32 = ctypes.windll.kernel32
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", wt.WCHAR * 260),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


@dataclass
class Volume:
    letter: str
    label: str
    kind: str
    total: int
    free: int

    @property
    def used(self) -> int:
        return max(0, self.total - self.free)

    @property
    def used_pct(self) -> float:
        if self.total <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 * self.used / self.total))

    @property
    def title(self) -> str:
        letter = self.letter.rstrip(":")
        if self.kind == "съёмный":
            return f"Флешка {letter}" + (f" — {self.label}" if self.label else "")
        if self.kind == "сеть":
            return f"Сетевая папка {letter}"
        return f"Диск {letter}" + (f" — {self.label}" if self.label else "")


@dataclass
class IoProc:
    pid: int
    name: str
    read_bps: float
    write_bps: float

    @property
    def total_bps(self) -> float:
        return self.read_bps + self.write_bps


def _kind(code: int) -> str | None:
    if code == DRIVE_FIXED:
        return "диск"
    if code == DRIVE_REMOVABLE:
        return "съёмный"
    if code == DRIVE_REMOTE:
        return "сеть"
    return None


def list_volumes() -> list[Volume]:
    mask = int(kernel32.GetLogicalDrives() or 0)
    out: list[Volume] = []
    for i in range(26):
        if not mask & (1 << i):
            continue
        letter = f"{chr(ord('A') + i)}:"
        root = letter + "\\"
        kind = _kind(int(kernel32.GetDriveTypeW(root)))
        if kind is None:
            continue
        total = ctypes.c_uint64(0)
        free = ctypes.c_uint64(0)
        if not kernel32.GetDiskFreeSpaceExW(root, None, ctypes.byref(total), ctypes.byref(free)):
            continue
        label_buf = ctypes.create_unicode_buffer(261)
        kernel32.GetVolumeInformationW(root, label_buf, 260, None, None, None, None, 0)
        out.append(
            Volume(
                letter=letter,
                label=label_buf.value.strip(),
                kind=kind,
                total=int(total.value),
                free=int(free.value),
            )
        )
    return out


def _io(pid: int) -> tuple[int, int] | None:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        counters = IO_COUNTERS()
        if not kernel32.GetProcessIoCounters(h, ctypes.byref(counters)):
            return None
        return int(counters.ReadTransferCount), int(counters.WriteTransferCount)
    finally:
        kernel32.CloseHandle(h)


def list_io() -> list[tuple[int, str, int, int]] | None:
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return None
    pe = PROCESSENTRY32W()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    out: list[tuple[int, str, int, int]] = []
    try:
        if not kernel32.Process32FirstW(snap, ctypes.byref(pe)):
            return None
        while True:
            pid = int(pe.th32ProcessID)
            if pid > 4:
                io = _io(pid)
                if io is not None:
                    out.append((pid, pe.szExeFile, io[0], io[1]))
            if not kernel32.Process32NextW(snap, ctypes.byref(pe)):
                break
    finally:
        kernel32.CloseHandle(snap)
    return out


class IoTracker:
    def __init__(self) -> None:
        self._prev: dict[int, tuple[int, int, float]] = {}
        self._last: list[IoProc] = []
        self._sum_bps = 0.0

    @property
    def sum_bps(self) -> float:
        return self._sum_bps

    def top(self, limit: int = 12) -> list[IoProc] | None:
        rows = list_io()
        if rows is None:
            return None
        if not rows:
            return list(self._last)
        now = time.perf_counter()
        live: set[int] = set()
        procs: list[IoProc] = []
        total = 0.0
        for pid, name, read, write in rows:
            live.add(pid)
            prev = self._prev.get(pid)
            rps = wps = 0.0
            if prev is not None:
                dt = now - prev[2]
                if dt > 0.05:
                    rps = max(0.0, (read - prev[0]) / dt)
                    wps = max(0.0, (write - prev[1]) / dt)
            self._prev[pid] = (read, write, now)
            procs.append(IoProc(pid=pid, name=name, read_bps=rps, write_bps=wps))
            total += rps + wps
        self._prev = {pid: val for pid, val in self._prev.items() if pid in live}
        procs.sort(key=lambda p: p.total_bps, reverse=True)
        self._sum_bps = total
        self._last = procs[:limit]
        return list(self._last)
