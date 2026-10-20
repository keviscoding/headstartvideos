"""The money-niche quiz on the web: its API, the share card picture, and the free tools' lead capture.

- POST /api/money-niche/quiz            answers -> a result id and the taste (niche #1 and one proof channel)
- GET  /api/money-niche/result/{id}      the taste for a visitor; the full result for anyone signed in
- POST /api/money-niche/choose           a signed-in person's pick (niche, angle, look, first title), stored on their
                                         account so the app opens on their recipe
- GET  /api/money-niche/pulse            the live strip: small channels the Niche Finder caught breaking out this week
- POST /api/channel-check                "I already have a channel": their channel as a dashboard + the biggest problem
- GET  /api/channel-check/{id}           the taste for a visitor; every finding, plan and idea for anyone signed in
- GET  /money-niche/card/{id}.png        the share picture (og:image); /money-niche/card.png is the generic one
- POST /api/leads                        "email me" with consent, from the quiz or /3d-harness
- GET  /api/admin/leads, POST /api/admin/leads/breakout-email   the leads list, sent only when an admin presses send

The share card sits outside /api/ because robots.txt keeps crawlers out of /api/, and the social sites' crawlers
that fetch og:image obey it.
"""
from __future__ import annotations

import io
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

import os

from webapp import channel_check
from webapp import lifecycle_email
from webapp import money_niche

PRIVATE = {"Cache-Control": "private, no-store"}
CONSENT = {
    "quiz": "Email me new breakout channels in my niche. About every week or two. Unsubscribe anytime.",
    "harness": "Email me the PDF and the next niche lists. Unsubscribe anytime.",
}

# ---- a small per-visitor limit for the endpoints that write or send ---------------------------------------

_hits: dict = defaultdict(list)
_hits_lock = threading.Lock()


def client_ip(request: Request) -> str:
    """The visitor's address behind Cloudflare and DigitalOcean's proxy."""
    h = request.headers
    return (h.get("cf-connecting-ip") or (h.get("x-forwarded-for") or "").split(",")[0]
            or (request.client.host if request.client else "") or "unknown").strip()[:64]


def allow(bucket: str, limit: int, window_s: int = 3600) -> bool:
    now = time.time()
    with _hits_lock:
        if len(_hits) > 20000:
            _hits.clear()
        recent = [t for t in _hits[bucket] if now - t < window_s]
        if len(recent) >= limit:
            _hits[bucket] = recent
            return False
        recent.append(now)
        _hits[bucket] = recent
        return True


# ---- the share card -----------------------------------------------------------------------------------------

FONT_BOLD = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf")
FONT_REGULAR = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans.ttf")


def _font(names, size):
    from PIL import ImageFont
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _wrap(draw, text, font, width):
    words, lines, line = text.split(), [], ""
    for w in words:
        trial = (line + " " + w).strip()
        if draw.textlength(trial, font=font) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def _count(n: int) -> str:
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1000:
        return f"{round(n / 1000):.0f}K"
    return str(n)


def proof_line(teaser: dict) -> str:
    p = teaser.get("proof") or {}
    if p.get("kind") == "fresh":
        started = f"started {p['months']} months ago, " if p.get("months") else ""
        return f"Proof: {p['name']}, {started}{_count(p['recent_avg_views'])} views per recent video"
    if p.get("kind") == "established":
        return f"Proof: {p['name']}, {p['views_label']} views on its breakout video"
    return ""


@lru_cache(maxsize=64)
def card_png(kicker: str, title: str, line: str) -> bytes:
    """A 1200x630 picture worth posting: the niche, its proof, and channelrecipe.com."""
    from PIL import Image, ImageDraw, ImageFilter
    w, h = 1200, 630
    img = Image.new("RGB", (w, h), (11, 11, 16))
    glow = Image.new("RGB", (w, h), (11, 11, 16))
    g = ImageDraw.Draw(glow)
    g.ellipse((700, -360, 1500, 420), fill=(109, 90, 224))
    g.ellipse((-380, 330, 380, 1050), fill=(176, 72, 128))
    g.ellipse((420, 380, 900, 820), fill=(255, 140, 90))
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(150)), 0.55)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((28, 28, w - 28, h - 28), radius=36, outline=(255, 255, 255, 40), width=2)
    x = 80
    d.text((x, 78), kicker.upper(), font=_font(FONT_BOLD, 26), fill=(183, 156, 255))
    size = 84
    while size > 44:
        big = _font(FONT_BOLD, size)
        lines = _wrap(d, title, big, w - 2 * x)
        if len(lines) <= 2:
            break
        size -= 6
    y = 128
    for ln in lines[:3]:
        d.text((x, y), ln, font=big, fill=(247, 245, 240))
        y += int(size * 1.12)
    if line:
        small = _font(FONT_REGULAR, 30)
        for ln in _wrap(d, line, small, w - 2 * x)[:2]:
            d.text((x, y + 22), ln, font=small, fill=(207, 201, 227))
            y += 40
    foot = _font(FONT_BOLD, 30)
    d.text((x, h - 112), "Find yours in 60 seconds", font=_font(FONT_REGULAR, 28), fill=(180, 178, 190))
    label = "channelrecipe.com"
    lw = d.textlength(label, font=foot)
    d.rounded_rectangle((w - x - lw - 48, h - 128, w - x, h - 70), radius=29, fill=(109, 90, 224))
    d.text((w - x - lw - 24, h - 117), label, font=foot, fill=(255, 255, 255))
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def share_text(rid: str) -> dict | None:
    """Title, description and picture for a result's share page."""
    result = money_niche.load(rid)
    if not result:
        return None
    t = money_niche.teaser(rid, result)
    line = proof_line(t)
    return {"name": t["niche"]["name"], "line": line,
            "title": f"My money niche: {t['niche']['name']}",
            "description": (line + ". " if line else "") + "Find yours in 60 seconds, with real channels as proof."}


# ---- requests -----------------------------------------------------------------------------------------------

class QuizRequest(BaseModel):
    face: str
    hours: str
    loves: list[str] = Field(default_factory=list, max_length=9)
    budget: str


class ChooseRequest(BaseModel):
    result: str = Field(max_length=40)
    niche: str = Field(max_length=60)
    angle: str = Field("", max_length=20)
    look: str = Field("", max_length=120)
    title: str = Field("", max_length=200)


class CheckRequest(BaseModel):
    url: str = Field(max_length=300)


class CheckChooseRequest(BaseModel):
    niche: str = Field(max_length=60)
    angle: str = Field("", max_length=20)
    look: str = Field("", max_length=120)
    title: str = Field("", max_length=200)


class LeadRequest(BaseModel):
    source: Literal["quiz", "harness"]
    consent: bool = False
    email: str = Field("", max_length=254)
    result: str = Field("", max_length=40)
    niche: str = Field("", max_length=60)


class BreakoutSendRequest(BaseModel):
    issue: str = Field("", max_length=20)


def make_router(current_user, require_user, require_admin, track, disposable=frozenset(),
                youtube_key=lambda: "") -> APIRouter:
    router = APIRouter()

    @router.get("/api/money-niche/niches", include_in_schema=False)
    def get_niches():
        """The 15 niches with their mood boards, looks and angles (public catalogue data, no proof or money)."""
        return JSONResponse([{"id": n["id"], "name": n["name"], "pitch": n["pitch"], "look": n["look"],
                              "pacing": n["pacing"], "length": n["length"],
                              "format": money_niche.FORMATS[n["formats"][0]][0], "board": money_niche.board(n),
                              "looks": money_niche.looks(n), "angles": money_niche.angles(n)}
                             for n in money_niche.NICHES], headers={"Cache-Control": "public, max-age=3600"})

    @router.get("/api/money-niche/pulse", include_in_schema=False)
    def get_pulse():
        return JSONResponse(money_niche.pulse(), headers={"Cache-Control": "public, max-age=300"})

    @router.post("/api/channel-check", include_in_schema=False)
    def post_check(req: CheckRequest, request: Request):
        if not allow("check:" + client_ip(request), 20):
            raise HTTPException(429, "That's a lot of channels. Try again in a little while.")
        try:
            done = channel_check.check(req.url, api_key=youtube_key(),
                                       daily_cap=int(os.getenv("CHANNEL_CHECK_DAILY", "2000") or 2000))
        except channel_check.CheckError as e:
            raise HTTPException(400, str(e)) from None
        user = current_user(request)
        track(user["id"] if user else "check:" + done["id"], "channel_check_completed",
              {"niche": (done["result"].get("niche") or {}).get("id", ""), "signed_in": bool(user),
               "cached": done["cached"], "subscribers": done["result"]["channel"]["subscribers"]})
        return JSONResponse(channel_check.teaser(done["id"], done["result"]), headers=PRIVATE)

    @router.get("/api/channel-check/{cid}", include_in_schema=False)
    def get_check(cid: str, request: Request):
        result = channel_check.load(cid)
        if not result:
            raise HTTPException(404, "That check has expired. Run it again: it takes a few seconds.")
        return JSONResponse(channel_check.public(cid, result, current_user(request)), headers=PRIVATE)

    @router.post("/api/channel-check/{cid}/choose", include_in_schema=False)
    def post_check_choose(cid: str, req: CheckChooseRequest, user: dict = Depends(require_user)):
        if not channel_check.load(cid) or req.niche not in money_niche.BY_ID:
            raise HTTPException(404, "Run the channel check again, then pick your niche.")
        chosen = money_niche.choice_for(req.niche, source=cid, angle_id=req.angle, look_id=req.look, title=req.title)
        money_niche.store_choice(user, chosen)
        track(user["id"], "money_niche_chosen", {"niche": chosen["id"], "from": "channel_check",
                                                  "plan": user.get("plan") or "free"})
        return JSONResponse({"ok": True, "niche": chosen, "next": "/app#local-studio"}, headers=PRIVATE)

    @router.post("/api/money-niche/quiz", include_in_schema=False)
    def post_quiz(req: QuizRequest, request: Request):
        if not allow("quiz:" + client_ip(request), 60):
            raise HTTPException(429, "That's a lot of quizzes. Try again in a little while.")
        try:
            taken = money_niche.take_quiz(req.model_dump())
        except money_niche.BadAnswers as e:
            raise HTTPException(400, str(e)) from None
        user = current_user(request)
        track(user["id"] if user else "quiz:" + taken["id"], "money_niche_quiz_completed",
              {**taken["result"]["answers"], "loves": ",".join(taken["result"]["answers"]["loves"]),
               "top_niche": taken["result"]["niches"][0]["id"], "signed_in": bool(user), "cached": taken["cached"]})
        return JSONResponse(money_niche.teaser(taken["id"], taken["result"]), headers=PRIVATE)

    @router.get("/api/money-niche/result/{rid}", include_in_schema=False)
    def get_result(rid: str, request: Request):
        result = money_niche.load(rid)
        if not result:
            raise HTTPException(404, "That result has expired. Take the quiz again: it takes a minute.")
        return JSONResponse(money_niche.public(rid, result, current_user(request)), headers=PRIVATE)

    @router.post("/api/money-niche/choose", include_in_schema=False)
    def post_choose(req: ChooseRequest, user: dict = Depends(require_user)):
        try:
            chosen = money_niche.choose(user, req.result, req.niche, req.angle, req.look, req.title)
        except LookupError as e:
            raise HTTPException(404, str(e)) from None
        track(user["id"], "money_niche_chosen", {"niche": chosen["id"], "plan": user.get("plan") or "free"})
        # TODO(owner decision): a free first hosted video in this niche would start here. Not built: decide once a
        # hosted video's cost is known. For now everyone goes on to sign-up/plans, with the niche pre-selected.
        return JSONResponse({"ok": True, "niche": chosen, "next": "/app#local-studio"}, headers=PRIVATE)

    @router.get("/money-niche/card.png", include_in_schema=False)
    def generic_card():
        png = card_png("Free quiz", "Find your money niche", "Which faceless channel should you make, and would it "
                       "make money? Real channels as proof.")
        return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

    @router.get("/money-niche/card/{rid}.png", include_in_schema=False)
    def result_card(rid: str):
        text = share_text(rid)
        if not text:
            raise HTTPException(404, "No such result.")
        png = card_png("My money niche", text["name"], text["line"])
        return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

    @router.post("/api/leads", include_in_schema=False)
    def post_lead(req: LeadRequest, request: Request):
        if not req.consent:
            raise HTTPException(400, "Tick the box to say we may email you.")
        user = current_user(request)
        if req.source == "quiz" and not user:
            raise HTTPException(401, "Sign in to get emails about your niche.")
        email = (user["email"] if user else req.email).strip().lower()
        if "@" in email and email.rsplit("@", 1)[1] in disposable:
            raise HTTPException(400, "Please use a permanent email address.")
        if not allow("lead:" + client_ip(request), 8) or not allow("lead-email:" + email, 3, 86400):
            raise HTTPException(429, "Too many sign-ups from here. Try again later.")
        niche = req.niche if req.niche in money_niche.BY_ID else ""
        if req.source == "quiz" and req.result:
            result = money_niche.load(req.result)
            ids = [n["id"] for n in (result or {}).get("niches", [])]
            niche = niche if niche in ids else (ids[0] if ids else niche)
        try:
            lead = lifecycle_email.capture_lead(email, req.source, CONSENT[req.source], niche)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        if req.source == "harness":
            lifecycle_email._later(lifecycle_email.harness_pdf, lead)
        track(user["id"] if user else "lead:" + str(lead["id"]), "lead_captured", {"source": req.source,
              "niche": niche, "signed_in": bool(user)})
        return JSONResponse({"ok": True, "pdf": "/static/harness/3D-Documentary-Harness.pdf"
                             if req.source == "harness" else None}, headers=PRIVATE)

    @router.get("/api/admin/leads", include_in_schema=False)
    def admin_leads(admin: dict = Depends(require_admin)):
        stats = lifecycle_email.lead_stats()
        stats["breakouts_now"] = len(money_niche.breakouts(None, limit=20))
        return JSONResponse(stats, headers=PRIVATE)

    @router.post("/api/admin/leads/breakout-email", include_in_schema=False)
    def admin_breakout_email(req: BreakoutSendRequest, admin: dict = Depends(require_admin)):
        """Email every subscribed lead this issue's breakout channels (in the background). Each issue goes once."""
        issue = req.issue.strip() or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if not all(c.isalnum() or c in "-_" for c in issue):
            raise HTTPException(400, "Name the issue with letters, numbers and dashes, like 2026-10-20.")
        threading.Thread(target=lambda: print("[lifecycle] breakouts", lifecycle_email.send_lead_breakouts(issue)),
                         daemon=True).start()
        return {"started": True, "issue": issue}

    return router
