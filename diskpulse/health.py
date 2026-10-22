"""Physical disk health via Storage CIM. No extra packages."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_VERDICT = {
    "healthy": ("Хорошо", "good"),
    "warning": ("Внимание", "warn"),
    "caution": ("Внимание", "warn"),
    "unhealthy": ("Плохо", "bad"),
    "unknown": ("Неизвестно", "unknown"),
}


@dataclass
class DiskHealth:
    text: str
    kind: str
    model: str
    media: str


def _media(bus: str, media: str) -> str:
    bus = (bus or "").lower()
    media = (media or "").lower()
    if bus == "nvme" or "nvme" in media:
        return "NVMe"
    if media == "ssd" or bus == "ssd":
        return "SSD"
    if media == "hdd" or bus == "sata" or bus == "raid":
        return "HDD"
    if bus == "usb":
        return "USB"
    return media.upper() or bus.upper() or ""


def _one_letter(letter: str) -> str:
    return letter.replace(":", "").replace("\\", "").strip().upper()[:1]


def query(letter: str) -> DiskHealth | None:
    drive = _one_letter(letter)
    if not drive.isalpha():
        return None
    script = (
        f"Get-Partition -DriveLetter {drive} -ErrorAction SilentlyContinue | "
        "Get-Disk -ErrorAction SilentlyContinue | "
        "Select-Object -First 1 Number, FriendlyName, HealthStatus, "
        "OperationalStatus, MediaType, BusType | ConvertTo-Json -Compress"
    )
    try:
        raw = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", script],
            text=True,
            timeout=8,
            creationflags=CREATE_NO_WINDOW,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return None
    status = str(data.get("HealthStatus") or "Unknown").strip()
    op = str(data.get("OperationalStatus") or "").strip().lower()
    key = status.lower()
    text, kind = _VERDICT.get(key, ("Неизвестно", "unknown"))
    if op in {"failed", "offline", "missing", "error"}:
        text, kind = "Плохо", "bad"
    model = str(data.get("FriendlyName") or "").strip()
    media = _media(str(data.get("BusType") or ""), str(data.get("MediaType") or ""))
    return DiskHealth(text=text, kind=kind, model=model, media=media)
