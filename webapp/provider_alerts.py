"""Tell the team when one of the site's own provider accounts runs out of credit.

On 2026-10-31/11-01 the site's Gemini prepaid balance (style watch, Style Lab compare), Atlas Cloud (website titles and
scripts) and the Anthropic console (ideas) all ran dry, and nothing told anyone: the errors are filtered out of Sentry
as "ops, not bugs", and customers found out first. check(text) is called with any provider error the server sees; an
out-of-credit one mails the admins (studio_ranking._tell_team's way: Sentry warning + Resend to ADMIN_EMAILS), at most
once per provider every ALERT_SECONDS in this process (two web instances: at most two mails).
"""
from __future__ import annotations

import re
import threading
import time

ALERT_SECONDS = 6 * 3600

# provider, what its out-of-credit error says, where to top it up
OUT_OF_CREDIT = (
    ("Gemini (Google AI Studio prepaid)", re.compile(r"(?i)prepayment credits? (are|is) depleted|gemini http 402"),
     "https://aistudio.google.com/ (Billing)"),
    ("Atlas Cloud", re.compile(r"(?i)insufficient balance|provider balance"), "https://www.atlascloud.ai/ (Billing)"),
    ("Anthropic API", re.compile(r"(?i)credit balance is too low"), "https://console.anthropic.com/settings/billing"),
    ("Kie", re.compile(r"(?i)kie[^\n]{0,80}(insufficient|credits? (are|is) (not enough|insufficient))"),
     "https://kie.ai/ (Billing)"),
)

_last: dict = {}
_lock = threading.Lock()


def which(text: str) -> tuple | None:
    """(provider, top-up address) when the text is an out-of-credit error from one of the site's providers."""
    for provider, pattern, where in OUT_OF_CREDIT:
        if pattern.search(str(text or "")):
            return provider, where
    return None


def check(text, *, tell=None, now=None) -> bool:
    """Mail the admins if `text` says a provider is out of credit (once per provider per ALERT_SECONDS). Never
    raises: an alert must not break the request that hit the error."""
    try:
        found = which(str(text or ""))
        if not found:
            return False
        provider, where = found
        now = time.time() if now is None else now
        with _lock:
            if now - _last.get(provider, float("-inf")) < ALERT_SECONDS:
                return False
            _last[provider] = now
        line = re.sub(r"\s+", " ", str(text))[:300]
        message = (f"{provider} is out of credit: the site's calls to it are failing. Top it up at {where}. "
                   f"The error: {line}")
        print(f"[provider-alert] {message}", flush=True)
        if tell is None:                                    # mailed off the request's way (the event loop too)
            from webapp.studio_ranking import _tell_team
            threading.Thread(target=_tell_team, args=(message,), kwargs={"subject": f"{provider} is out of credit"},
                             daemon=True).start()
        else:
            tell(message, subject=f"{provider} is out of credit")
        return True
    except Exception as exc:                                    # noqa: BLE001
        print(f"[provider-alert] could not send: {exc}", flush=True)
        return False
