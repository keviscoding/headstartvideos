"""An account-bound usage report, never a live balance or generation lock."""
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
        saved = json.loads((Path(home) / ".cr-claude-allowance.json").read_text(encoding="utf-8"))
        remaining = float(saved["reset_at"]) - (time.time() if now is None else now)
        if saved.get("account") != account or remaining <= 0 or remaining > 25 * 3600:
            return {"blocked": False}
        # Extra usage can be purchased before the reported reset. A saved CLI
        # error cannot tell us the account's current allowance; the next
        # explicitly requested action must reach Claude to check it.
        return {"blocked": False, "reported_limit": True,
                "reset_at": saved["reset_at"], "reset_label": saved["reset_label"],
                "message": "Claude last reported a usage limit with a reset at " + saved["reset_label"] +
                ". If you added usage or access has returned, you can create a new video now. "
                "Claude checks your current allowance when it starts. Stopped projects stay saved and do not restart automatically."}
    except (OSError, ValueError, KeyError, TypeError):
        return {"blocked": False}


def stopped_message(reset_label=None, *, threed=False):
    """What a video that stopped at Claude's usage limit says (the progress card's note). Carrying it on is the way
    forward, never a new video (which pays again for everything): Swapnil's 18-minute 3D video, 2026-10-29."""
    when = f" ({reset_label})" if reset_label else ""
    kept = "everything already made is kept" + (", including finished 3D review rounds" if threed else "")
    return (f"Claude stopped at your plan's usage limit. When your Claude limit resets{when}, press Try again and "
            f"carry on: {kept}.")


def record(home, account, message, *, now=None):
    reset = reset_from_message(message, now=now)
    if not account or not reset:
        return None
    import cr_claude
    cr_claude._private_write(Path(home) / ".cr-claude-allowance.json", {**reset, "account": account})
    return status(home, account, now=now)
