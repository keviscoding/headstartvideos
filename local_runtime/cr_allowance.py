"""A local, account-bound cooldown from an explicit Claude reset message."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def reset_from_message(message, *, now=None):
    # Only a documented time AND timezone are evidence. A generic rate limit,
    # weekly limit or ambiguous date must not become an invented countdown.
    match = re.search(r"resets\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*\(([A-Za-z_]+/[A-Za-z_+\-/]+)\)", str(message), re.I)
    if not match:
        return None
    hour, minute, period, zone = match.groups()
    if not 1 <= int(hour) <= 12 or int(minute or 0) > 59:
        return None
    try:
        tz = ZoneInfo(zone)
    except (ValueError, ZoneInfoNotFoundError):
        return None
    current = datetime.fromtimestamp(time.time() if now is None else now, timezone.utc).astimezone(tz)
    end = current.replace(hour=int(hour) % 12 + (12 if period.lower() == "pm" else 0),
                          minute=int(minute or 0), second=0, microsecond=0)
    if end <= current:
        end += timedelta(days=1)
    return {"reset_at": end.timestamp(), "reset_label": end.strftime("%d %b, %I:%M %p") + " (" + zone + ")"}


def status(home, account, *, now=None):
    if not account:
        return {"blocked": False}
    try:
        saved = json.loads((Path(home) / ".cr-claude-allowance.json").read_text())
        remaining = float(saved["reset_at"]) - (time.time() if now is None else now)
        if saved.get("account") != account or remaining <= 0 or remaining > 25 * 3600:
            return {"blocked": False}
        return {"blocked": True, "reset_at": saved["reset_at"], "reset_label": saved["reset_label"],
                "message": "Claude reported a usage limit until " + saved["reset_label"] +
                ". Your work is saved. Review the stopped action after the reset; it will not restart automatically."}
    except (OSError, ValueError, KeyError, TypeError):
        return {"blocked": False}


def record(home, account, message, *, now=None):
    reset = reset_from_message(message, now=now)
    if not account or not reset:
        return None
    import cr_claude
    cr_claude._private_write(Path(home) / ".cr-claude-allowance.json", {**reset, "account": account})
    return status(home, account, now=now)
