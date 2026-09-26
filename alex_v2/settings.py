"""The Alex v2 toggle and its settings.

v2 keeps its OWN settings file: ``<data_dir>/settings.json``. It never
reads or writes ``autopilot/runtime_flags.json``.

Modes
-----
off     v2 does nothing; your current Alex texting runs exactly as before.
on      v2 writes every Alex autopilot reply.
ab      each thread is permanently assigned to v1 or v2 (by a hash of its
        thread id) using ``ab_split``; both arms are logged for comparison.
shadow  v1 keeps sending; v2 computes what it WOULD have sent and logs it.

Environment overrides (take precedence and are shown as "locked" in the page):
ALEX_V2_MODE=off|on|ab|shadow, ALEX_V2_AB_SPLIT=0.5, ALEX_V2_MODEL=<id>,
ALEX_V2_DATA_DIR=/path/to/dir
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

MODES = ("off", "on", "ab", "shadow")

DEFAULTS: dict[str, Any] = {
    "mode": "off",
    "ab_split": 0.5,            # share of threads that get v2 in "ab" mode
    "model": "",                # "" = ALEX_V2_MODEL env, else wingman LIVE_MODEL, else QUICK/FLASH model
    "fallback_model": "",       # used only if the main model errors ("" = wingman QUICK/FLASH model)
    "candidates": 4,            # candidates written per call (one model call)
    "temperature": 0.7,
    "max_output_tokens": 1200,
    "thinking_budget": 0,       # keep the fast model fast
    "repair_call": True,        # one extra call if every candidate fails the checks
    "template_followups": True, # canon follow-up lines (double text, confirm, takeaways) without a model call
    "allow_skip": True,         # may leave a tiny "lol"/"nice" unanswered for a while (canon), then reopen fresh
    "context_messages": 30,     # messages shown to the model (older ones live in the computed state)
    "log_prompts": False,       # store full prompts in the decision log (lab runs always do)
    "timeout_s": 25,
    "honor_timing": True,       # human-like reply delays (returned as due_at / send_after_s)
    "followups": True,          # v2 may send double/triple texts, reminders, confirms, sweeps
    "sweep_days": 10,           # dormant-thread sweep interval
    "max_sweeps": 3,
    "quiet_start": 1,           # her local hour: no sends from quiet_start ...
    "quiet_end": 8,             # ... until quiet_end (unless she is texting at that hour)
    "sexual_cap": 60,           # 0..100; explicit sexual content needs green light AND cap >= 70
    "handoff_address": True,    # hand address/Uber details to you instead of sending
    "handoff_calls": True,      # voice memo / FaceTime / call requests -> you
    "handoff_photos": True,     # "send a pic" -> you (unless approved assets exist)
    "handoff_serious": True,    # death/illness/crisis -> you (v2 suggests a sincere reply)
    "max_sends_per_thread_per_day": 8,
    "strip_trailing_period": True,
    "namespace": "live",
}

_LOCK = threading.RLock()
_CACHE: dict[str, Any] = {"mtime": None, "data": None}


def data_dir() -> Path:
    env = os.getenv("ALEX_V2_DATA_DIR")
    p = Path(env) if env else Path(__file__).parent / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def settings_path() -> Path:
    return data_dir() / "settings.json"


def _env_overrides() -> dict[str, Any]:
    out: dict[str, Any] = {}
    mode = (os.getenv("ALEX_V2_MODE") or "").strip().lower()
    if mode in MODES:
        out["mode"] = mode
    split = os.getenv("ALEX_V2_AB_SPLIT")
    if split:
        try:
            out["ab_split"] = max(0.0, min(1.0, float(split)))
        except ValueError:
            pass
    model = os.getenv("ALEX_V2_MODEL")
    if model:
        out["model"] = model.strip()
    return out


def load() -> dict[str, Any]:
    """Current settings = defaults <- settings.json <- env overrides."""
    path = settings_path()
    with _LOCK:
        mtime = path.stat().st_mtime if path.exists() else None
        if _CACHE["data"] is None or _CACHE["mtime"] != mtime:
            data: dict[str, Any] = {}
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                except Exception:
                    data = {}
            _CACHE["data"] = data
            _CACHE["mtime"] = mtime
        file_data = dict(_CACHE["data"] or {})
    merged = {**DEFAULTS, **{k: v for k, v in file_data.items() if k in DEFAULTS}}
    env = _env_overrides()
    merged.update(env)
    merged["_locked_by_env"] = sorted(env.keys())
    return merged


def save(patch: dict[str, Any]) -> dict[str, Any]:
    """Merge ``patch`` into settings.json (unknown keys ignored)."""
    path = settings_path()
    with _LOCK:
        current: dict[str, Any] = {}
        if path.exists():
            try:
                current = json.loads(path.read_text())
            except Exception:
                current = {}
        for k, v in patch.items():
            if k not in DEFAULTS:
                continue
            if k == "mode" and v not in MODES:
                continue
            if k == "ab_split":
                v = max(0.0, min(1.0, float(v)))
            current[k] = v
        current["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, indent=2))
        os.replace(tmp, path)
        _CACHE["data"] = None
    return load()


def mode() -> str:
    return load()["mode"]


def is_enabled() -> bool:
    return mode() != "off"


def arm_for_thread(thread_id: str, s: dict[str, Any] | None = None) -> str:
    """Which arm handles this thread: "v2", "v1" or "shadow".

    In "ab" mode the assignment is a stable hash of the thread id, so a girl
    never flips between styles mid-conversation.
    """
    s = s or load()
    m = s["mode"]
    if m == "on":
        return "v2"
    if m == "shadow":
        return "shadow"
    if m == "ab":
        h = int(hashlib.sha256(("alex_v2_ab:" + thread_id).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        return "v2" if h < float(s.get("ab_split", 0.5)) else "v1"
    return "v1"
