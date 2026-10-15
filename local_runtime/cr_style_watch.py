"""Have Gemini watch a reference video for a style study, through ChannelRecipe.

ChannelRecipe's server holds the Gemini key and the limits; this side picks the
windows worth watching (the opening, where a channel shows its hook and pace,
and one stretch from the middle, where its everyday style lives), starts each
watch and waits for the logs. When watching isn't available (limits reached,
the service busy, a private video), the study carries on with its own
measurements, and the profile says what it could not see.
"""
from __future__ import annotations

import re
import time

OPENING_S = 180
LATER_S = 90
CTA_S = 75
POLL_S = 5
WAIT_S = 600
# What a call to action sounds like in captions: a product, a guide, a code, a sponsor. "Subscribe" alone isn't one.
CTA_WORDS = re.compile(
    r"\b(link (is )?(in|below)|in the description|description below|scan (the|this|that) (qr )?code|qr code"
    r"|my (new |free )?(book|ebook|e-book|guide|course|pdf|checklist|cookbook|workbook|newsletter)"
    r"|free (guide|pdf|download|checklist|ebook|e-book|cheat ?sheet)|download (my|the|it|your)"
    r"|sponsor(ed)? by|today'?s sponsor|use (my )?code|promo code|discount code|percent off|% off"
    r"|patreon|skillshare|brilliant|nordvpn|incogni|squarespace|audible|ground news)\b", re.I)


def cta_times(transcript) -> list:
    """Seconds where the captions promote something ([(second, words)] as Studio's transcript helper gives them)."""
    out = []
    for row in transcript or []:
        if isinstance(row, (list, tuple)) and len(row) == 2 and isinstance(row[0], (int, float)):
            if CTA_WORDS.search(str(row[1])) and (not out or row[0] - out[-1] > 30):
                out.append(float(row[0]))
    return out


def windows(duration: float, cta: list = ()) -> list:
    """[(start, end)] seconds to watch in a video of this length: the opening, one stretch from the middle, and the
    first call to action the captions mention that those two miss (how a channel sells is part of its style)."""
    duration = float(duration or 0)
    if duration <= 0:
        return [(0, OPENING_S)]
    first = (0, int(min(OPENING_S, duration)))
    out = [first]
    if duration >= OPENING_S + LATER_S + 60:
        start = int(max(OPENING_S + 30, duration * 0.45))
        out.append((start, int(min(duration - 5, start + LATER_S))))
    for t in cta or []:
        if any(a <= t <= b for a, b in out) or t >= duration - 10:
            continue
        a = int(max(0, t - 15))
        b = int(min(duration, a + CTA_S))
        if b - a >= 20 and not any(a < y and x < b for x, y in out):
            out.append((a, b))
            break
    return out


def watch(video_id: str, duration: float, *, call, log=print, sleep=time.sleep, clock=time.monotonic,
          wait_s: float = WAIT_S, transcript=None) -> dict:
    """{"windows": [{"span": [a, b], "log": {...}, "cached": bool}], "missing": [reasons]} for one reference.
    `call` is cr_bridge.call (POST with a payload, GET without)."""
    out = {"video_id": video_id, "windows": [], "missing": []}
    jobs = []
    spans = windows(duration, cta_times(transcript))
    purpose = {span: ("opening" if span[0] == 0 else "middle" if k == 1 else "cta") for k, span in enumerate(spans)}
    for a, b in spans:
        try:
            res = call("style/watch", {"video_id": video_id, "start": a, "end": b})
        except RuntimeError as exc:
            out["missing"].append(f"{_mmss(a)}-{_mmss(b)}: {exc}")
            log(f"  watch {video_id} {_mmss(a)}-{_mmss(b)}: not watched ({exc})")
            if "today" in str(exc) or "available" in str(exc) or "busy" in str(exc):
                break                                       # the same answer for every other window
            continue
        jobs.append(((a, b), res))
    deadline = clock() + wait_s
    for (a, b), res in jobs:
        while res.get("status") == "running" and clock() < deadline:
            sleep(POLL_S)
            try:
                res = call("style/watch/" + res["job"])
            except RuntimeError as exc:
                res = {"status": "failed", "error": str(exc)}
        if res.get("status") == "done" and isinstance(res.get("log"), dict):
            out["windows"].append({"span": [a, b], "purpose": purpose[(a, b)], "log": res["log"],
                                   "cached": bool(res.get("cached"))})
            log(f"  watch {video_id} {_mmss(a)}-{_mmss(b)}: {len(res['log'].get('shots') or [])} shots logged"
                + (" (already watched)" if res.get("cached") else ""))
        else:
            why = res.get("error") or ("still running after " + str(int(wait_s)) + " s")
            out["missing"].append(f"{_mmss(a)}-{_mmss(b)}: {why}")
            log(f"  watch {video_id} {_mmss(a)}-{_mmss(b)}: not watched ({why})")
    return out


def _mmss(t: float) -> str:
    t = int(round(t))
    return f"{t // 60}:{t % 60:02d}"
