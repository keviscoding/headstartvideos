"""What this Studio's setup still lacks, told to ChannelRecipe so the team sees where new customers get stuck.

When a customer was stuck in setup (no FFmpeg, Claude Code signed out, a blocked network), the server never heard of
it: it only saw that Studio called in. A connected Studio now sends, in the background, the keys of the setup checks
that don't pass yet (cr_readiness: ffmpeg, node, claude, models, network…), whether setup is ready, the computer's
family, the Studio's version and, for a check that knows it, a short reason code (the graphics browser's
"disk_full", say). Nothing else leaves: no names, paths, keys or messages. It sends when that changes,
or every few hours, and never holds up the setup page; an older server that doesn't know the report is ignored.
"""
from __future__ import annotations

import json
import platform
import re
import threading
import time

SEND_EVERY_S = 6 * 3600
RETRY_AFTER_S = 15 * 60          # a refused or lost send (a lapsed plan, a revoked key, no network) is tried again then
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
_last = {"sent": None, "at": 0.0, "wait_until": 0.0}
_lock = threading.Lock()


def os_family(system=None) -> str:
    name = (system or platform.system()).lower()
    return {"darwin": "mac", "windows": "windows", "linux": "linux"}.get(name, "other")


def snapshot(readiness: dict, version: str) -> dict:
    rows = [row for row in (readiness or {}).get("checks", [])
            if not row.get("ready") and _KEY.match(str(row.get("key") or ""))]
    failing = sorted({str(row["key"]) for row in rows})[:24]
    snap = {"ready": bool((readiness or {}).get("ready")), "failing": failing, "os": os_family(),
            "version": str(version or "")[:40]}
    # a failing check's short reason code (the graphics browser's: disk_full, missing…), never its message
    why = {str(row["key"]): str(row["code"]) for row in rows
           if str(row["key"]) in failing and _KEY.match(str(row.get("code") or ""))}
    if why:
        snap["why"] = dict(sorted(why.items()))
    return snap


def maybe_send(readiness: dict, version: str, *, connected: bool, send=None, now=None, background=True) -> bool:
    """Send this setup's snapshot if it changed or the last one is old; -> whether a send was started."""
    if not connected:
        return False
    snap = snapshot(readiness, version)
    sent = json.dumps(snap, sort_keys=True)
    now = time.time() if now is None else now
    with _lock:
        if now < _last["wait_until"]:
            return False                                    # the last send failed: not on every /cr-status (4 s)
        if sent == _last["sent"] and now - _last["at"] < SEND_EVERY_S:
            return False
        _last.update(sent=sent, at=now)
    if send is None:
        def send(payload):
            import cr_bridge
            cr_bridge.call("setup-report", payload)

    def go():
        try:
            send(snap)
        except Exception as error:                          # noqa: BLE001 - a report must never trouble setup
            # an older server without the report answers 404 ("Not Found"): ask again only after the full interval;
            # anything else (a refusal, no network) after RETRY_AFTER_S
            wait = SEND_EVERY_S if "not found" in str(error).lower() else RETRY_AFTER_S
            with _lock:
                if _last["sent"] == sent:
                    _last.update(sent=None, at=0.0, wait_until=time.time() + wait)
    if background:
        threading.Thread(target=go, daemon=True, name="cr-setup-report").start()
    else:
        go()
    return True
