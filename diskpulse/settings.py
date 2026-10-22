"""Remember poll interval."""

from __future__ import annotations

import json
from pathlib import Path

_DIR = Path.home() / ".diskpulse"
_FILE = _DIR / "settings.json"

INTERVALS = (
    (0.5, "0,5 с"),
    (1.0, "1 с"),
    (2.0, "2 с"),
    (5.0, "5 с"),
    (10.0, "10 с"),
)
DEFAULT = 1.0


def _load() -> dict:
    try:
        raw = json.loads(_FILE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    _DIR.mkdir(parents=True, exist_ok=True)
    cur = _load()
    cur.update(data)
    _FILE.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")


def load_interval() -> float:
    try:
        sec = float(_load().get("sec", DEFAULT))
    except (TypeError, ValueError):
        return DEFAULT
    allowed = {s for s, _ in INTERVALS}
    return sec if sec in allowed else DEFAULT


def save_interval(sec: float) -> None:
    _save({"sec": sec})
