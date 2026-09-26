"""Alex v2: state-tracked, precondition-gated Alex texting engine (toggleable).

Public API:
    decide / decide_async     one decision for one chat
    mark_sent                 tell v2 a message went out
    due_threads               chats with a scheduled reply / follow-up due now
    maybe_handle(_async)      the autopilot hook (respects the OFF/ON/A-B/SHADOW toggle)
    settings.mode()           current mode; ``python -m alex_v2 status``
"""

from .engine import decide, decide_async, due_threads, mark_sent, thread_control  # noqa: F401
from .integration import Handled, arm_for, maybe_handle, maybe_handle_async, observe  # noqa: F401
from .models import Decision  # noqa: F401

__all__ = ["decide", "decide_async", "mark_sent", "due_threads", "thread_control", "maybe_handle", "maybe_handle_async",
           "observe", "arm_for", "Handled", "Decision"]
