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
    lab: bool = False,
    shadow_background: bool = True,
) -> Handled:
    """``lab=True`` (or a thread id containing "lab:") keeps lab drills out of the live A/B numbers.
    In shadow mode v2 runs in the background by default, so the current Alex's reply isn't delayed."""
    try:
        arm = arm_for(thread_id)
    except Exception:  # unreadable settings / data folder: behave exactly as OFF
        return Handled(False, None, "off")
    if arm == "off":
        return Handled(False, None, "off")
    is_lab = lab or thread_id.startswith("lab:") or ":lab:" in thread_id
    kw = dict(thread_id=thread_id, contact_name=contact_name, platform=platform, origin=origin, her_tz=her_tz,
              her_profile=her_profile, llm=llm, now=now, timestamps=timestamps, namespace="lab" if is_lab else None)
    if arm == "v2":
        try:
            d = await decide_async(messages, arm="v2", **kw)
        except Exception as exc:  # never break texting: an unexpected v2 error hands this reply back to the current Alex
            _log_crash(thread_id, contact_name, exc)
            return Handled(False, None, "v2-error")
        if d.action == "handoff" and on_handoff is not None:
            try:
                r = on_handoff(thread_id, d)
                if asyncio.iscoroutine(r):
                    await r
            except Exception:
                pass
        return Handled(True, d, "v2")
    # v1 sends; measure it the same way, and in shadow mode also compute v2's answer
    if not is_lab:
        observe(thread_id, messages, arm="v1", now=now, her_tz=her_tz, platform=platform, timestamps=timestamps)
    if arm == "shadow":
        if not is_lab:
            kw["namespace"] = None          # engine uses its own "shadow" store
        if shadow_background:
            task = asyncio.get_running_loop().create_task(_shadow(messages, kw))
            _BACKGROUND.add(task)
            task.add_done_callback(_BACKGROUND.discard)
            return Handled(False, None, "shadow")
        return Handled(False, await _shadow(messages, kw), "shadow")
    return Handled(False, None, "v1")


_BACKGROUND: set = set()


async def _shadow(messages: list[Any], kw: dict[str, Any]) -> Optional[Decision]:
    try:
        return await decide_async(messages, arm="shadow", **kw)
    except Exception as exc:
        _log_crash(kw.get("thread_id", ""), kw.get("contact_name", ""), exc)
        return None


def _log_crash(thread_id: str, contact_name: str, exc: Exception) -> None:
    import traceback
    try:
        TR.log_decision({"thread_key": TR.thread_key(thread_id), "contact": contact_name, "namespace": "live", "arm": "v2",
                         "action": "error", "reason": f"v2 crashed, current Alex handled this reply: {type(exc).__name__}: {exc}"[:300],
                         "error": traceback.format_exc()[-2000:]})
    except Exception:
        pass


def maybe_handle(thread_id: str, messages: list[Any], **kw: Any) -> Handled:
    """Sync version of :func:`maybe_handle_async`."""
    kw["shadow_background"] = False   # a sync call's event loop ends when it returns, so shadow runs inline here
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
