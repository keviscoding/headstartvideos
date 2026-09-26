"""The one hook your autopilot needs.

    from alex_v2.integration import maybe_handle_async

    h = await maybe_handle_async(thread_id, messages, contact_name=name, platform="hinge")
    if h.handled:                      # v2 owns this thread: v1 must NOT send
        d = h.decision
        if d.should_send:
            await send(d.text)
            mark_sent(thread_id, d.text)
        return
    ... your existing Alex texting (v1) runs exactly as before ...

Mode OFF  -> handled is always False (v1 as before, v2 does nothing).
Mode ON   -> handled is always True.
Mode A/B  -> each thread is permanently v1 or v2 (hash split); v1 threads are
             observed so the A/B page compares like with like.
Mode SHADOW -> handled is False; v2 logs what it would have sent.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from . import facts as F
from . import settings as S
from . import store as ST
from . import trace as TR
from .engine import decide_async, mark_sent, due_threads  # noqa: F401  (re-exported)
from .models import Decision
from .normalize import normalize_messages
from .state import build_state

UTC = timezone.utc
__all__ = ["Handled", "arm_for", "thread_id_for", "maybe_handle", "maybe_handle_async", "observe", "decide_async", "mark_sent", "due_threads"]


@dataclass
class Handled:
    handled: bool                    # True: v2 owns this thread, skip v1 entirely
    decision: Optional[Decision]
    arm: str                         # "v2" | "v1" | "shadow" | "off"


def thread_id_for(platform: str, contact_name: str, messages: list[Any] | None = None, match_id: str = "") -> str:
    """A stable, unique chat id. Use your platform's real match/chat id if you have one.

    Display names are NOT unique (two "Sofia"s on Hinge would share memory), so
    without a match id the first two messages of the chat are mixed in.
    """
    import hashlib
    if match_id:
        return f"{platform}:{match_id}"
    first = ""
    for m in (messages or [])[:2]:
        t = m.get("text") if isinstance(m, dict) else getattr(m, "text", "")
        first += "|" + " ".join(str(t or "").lower().split())
    return f"{platform}:{contact_name}:{hashlib.sha1(first.encode()).hexdigest()[:10]}"


def arm_for(thread_id: str) -> str:
    s = S.load()
    if s["mode"] == "off":
        return "off"
    return S.arm_for_thread(thread_id, s)


async def maybe_handle_async(
    thread_id: str,
    messages: list[Any],
    *,
    contact_name: str = "",
    platform: str = "",
    origin: str = "",
    her_tz: Optional[str] = None,
    her_profile: str = "",
    llm: Optional[Callable[..., Any]] = None,
    now: Optional[datetime] = None,
    timestamps: str = "seen",
    on_handoff: Optional[Callable[[str, Decision], Any]] = None,
) -> Handled:
    arm = arm_for(thread_id)
    if arm == "off":
        return Handled(False, None, "off")
    kw = dict(thread_id=thread_id, contact_name=contact_name, platform=platform, origin=origin, her_tz=her_tz,
              her_profile=her_profile, llm=llm, now=now, timestamps=timestamps)
    if arm == "v2":
        d = await decide_async(messages, arm="v2", **kw)
        if d.action == "handoff" and on_handoff is not None:
            try:
                r = on_handoff(thread_id, d)
                if asyncio.iscoroutine(r):
                    await r
            except Exception:
                pass
        return Handled(True, d, "v2")
    # v1 sends; measure it the same way, and in shadow mode also compute v2's answer
    observe(thread_id, messages, arm="v1", now=now, her_tz=her_tz, platform=platform, timestamps=timestamps)
    if arm == "shadow":
        try:
            d = await decide_async(messages, arm="shadow", **kw)
        except Exception:
            d = None
        return Handled(False, d, "shadow")
    return Handled(False, None, "v1")


def maybe_handle(thread_id: str, messages: list[Any], **kw: Any) -> Handled:
    """Sync version of :func:`maybe_handle_async`."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(maybe_handle_async(thread_id, messages, **kw))
    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        return ex.submit(asyncio.run, maybe_handle_async(thread_id, messages, **kw)).result()


def observe(thread_id: str, messages: list[Any], *, arm: str = "v1", now: Optional[datetime] = None,
            her_tz: Optional[str] = None, platform: str = "", timestamps: str = "seen") -> None:
    """Record outcome metrics for a thread v2 is NOT texting (for the A/B page)."""
    try:
        now = now or datetime.now(UTC)
        facts = F.load()
        my_tz = facts.get("timezone") or "Europe/London"
        st = ST.load(thread_id, "observe")
        msgs, new_times = normalize_messages(messages, now=now, user_tz=my_tz, stored_times=st["msg_times"], timestamps=timestamps)
        if new_times:
            st["msg_times"].update(new_times)
            ST.save(thread_id, st, "observe")
        tz = her_tz or st["flags"].get("her_tz") or my_tz
        state = build_state(msgs, thread_id=thread_id, now=now, her_tz=tz, my_tz=my_tz, platform=platform)
        TR.observe(thread_id, arm, msgs, state, now, "live")
    except Exception:
        pass
