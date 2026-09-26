"""Per-thread JSON store, isolated by namespace ("live", "lab_*", ...).

Holds only what alex_v2 itself needs to remember between calls:
- first-seen resolution of message times (so "Yesterday" never drifts),
- what v2 sent and when (for timers and A/B outcome tracking),
- a pending (planned, not yet sent) message,
- small per-thread flags (troll in progress, closed, overrides).

It deliberately stores NO prose summary. The conversation state is always
recomputed from the thread's own messages.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any

from .settings import data_dir

_LOCK = threading.RLock()


def _thread_key(thread_id: str) -> str:
    return hashlib.sha1(thread_id.encode("utf-8")).hexdigest()[:20]


def _dir(namespace: str) -> Path:
    safe = "".join(c for c in namespace if c.isalnum() or c in "-_") or "live"
    p = data_dir() / "threads" / safe
    p.mkdir(parents=True, exist_ok=True)
    return p


def load(thread_id: str, namespace: str = "live") -> dict[str, Any]:
    path = _dir(namespace) / f"{_thread_key(thread_id)}.json"
    with _LOCK:
        if not path.exists():
            return {"thread_id": thread_id, "msg_times": {}, "sent_log": [], "pending": None, "flags": {}, "overrides": {}}
        try:
            data = json.loads(path.read_text())
        except Exception:
            data = {}
    data.setdefault("thread_id", thread_id)
    data.setdefault("msg_times", {})
    data.setdefault("sent_log", [])
    data.setdefault("pending", None)
    data.setdefault("flags", {})
    data.setdefault("overrides", {})
    return data


def save(thread_id: str, data: dict[str, Any], namespace: str = "live") -> None:
    path = _dir(namespace) / f"{_thread_key(thread_id)}.json"
    tmp = path.with_suffix(".tmp")
    with _LOCK:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str))
        os.replace(tmp, path)


def list_threads(namespace: str = "live") -> list[dict[str, Any]]:
    out = []
    for p in sorted(_dir(namespace).glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            out.append(json.loads(p.read_text()))
        except Exception:
            continue
    return out
