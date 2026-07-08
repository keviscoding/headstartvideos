"""
ChannelRecipe — Complete Web App

Pipeline + Tools + Settings + History.
Run:  python -m webapp.server
"""

from __future__ import annotations
import asyncio
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import Cookie, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

import config

WEBAPP_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEBAPP_DIR / "static"
NICHES_DIR = WEBAPP_DIR / "niches"
OUTPUT_DIR = ROOT / "output"
UPLOAD_DIR = OUTPUT_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Telemetry (all optional — completely inert if keys are not configured)
# ---------------------------------------------------------------------------
if config.SENTRY_DSN:
    try:
        import sentry_sdk
        sentry_sdk.init(dsn=config.SENTRY_DSN, traces_sample_rate=0.1, send_default_pii=False)
        print("[telemetry] Sentry initialized")
    except Exception as e:
        print(f"[telemetry] Sentry init failed: {e}")

_posthog = None
if config.POSTHOG_KEY:
    try:
        from posthog import Posthog
        _posthog = Posthog(project_api_key=config.POSTHOG_KEY, host=config.POSTHOG_HOST)
        print("[telemetry] PostHog initialized")
    except Exception as e:
        print(f"[telemetry] PostHog init failed: {e}")


def track(distinct_id: str | int, event: str, props: dict | None = None) -> None:
    """Fire-and-forget server-side analytics event. No-op without PostHog."""
    if not _posthog:
        return
    try:
        _posthog.capture(distinct_id=str(distinct_id), event=event, properties=props or {})
    except Exception as e:
        print(f"[telemetry] capture failed for {event}: {e}")


app = FastAPI(title="ChannelRecipe", docs_url="/docs")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_jobs: dict[str, dict[str, Any]] = {}


@app.on_event("startup")
async def _startup_tasks():
    try:
        removed = cleanup_expired()
        print(f"[db] Cleaned {removed} expired sessions/codes on startup")
    except Exception as e:
        print(f"[db] cleanup_expired failed: {e}")

    async def _periodic_cleanup():
        while True:
            await asyncio.sleep(3600)
            try:
                cleanup_expired()
            except Exception:
                pass

    asyncio.create_task(_periodic_cleanup())

from webapp.database import (
    get_user_by_email, create_user, get_user_by_id, update_user,
    get_user_by_sub_id, deduct_credit, refund_credit, add_credits,
    create_verify_code, verify_code,
    create_session, get_session_user, delete_session,
    log_render_event, render_stats, backend_name, cleanup_expired,
    create_video, list_videos, get_video, update_video_kit, delete_video,
)
from webapp import storage

# Rough COGS estimate in GBP pence per finished minute, per recipe. These are
# tunable placeholders — refine once real per-render token/TTS data is captured.
_COST_PENCE_PER_MIN = {
    "animated_explainer": 15.0,
    "broll_only": 5.0,
    "broll_cinematic": 12.0,
    "avatar_plus_broll": 40.0,
}


def _estimate_cost_pence(recipe: str, minutes: float) -> float:
    return round(_COST_PENCE_PER_MIN.get(recipe, 10.0) * max(minutes, 0.1), 2)


def _current_user(request: Request) -> dict | None:
    """Extract the logged-in user from session cookie."""
    token = request.cookies.get("session")
    if not token:
        return None
    return get_session_user(token)


def require_user(request: Request) -> dict:
    """FastAPI dependency: reject anonymous requests to protected endpoints."""
    user = _current_user(request)
    if not user:
        raise HTTPException(401, "Sign in to continue.")
    return user


def require_active_plan(request: Request) -> dict:
    """Require sign-in AND an active subscription (or admin).

    Free users MUST go through Stripe checkout to start a trial.
    No generation is allowed on plan='free' regardless of credits.
    """
    user = require_user(request)
    if _is_admin_email(user.get("email", "")):
        return user
    if user.get("plan") not in ("starter", "daily", "pro", "starter_trial", "daily_trial"):
        raise HTTPException(402, "Start your free trial to generate videos.")
    return user


def require_admin(request: Request) -> dict:
    """FastAPI dependency: restrict ops-only endpoints (API-key settings) to admins."""
    user = require_user(request)
    admins = getattr(config, "ADMIN_EMAILS", [])
    if not admins or user.get("email", "").lower() not in admins:
        raise HTTPException(403, "Admin access required.")
    return user


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------
class AuthSendCodeRequest(BaseModel):
    email: str

class AuthVerifyRequest(BaseModel):
    email: str
    code: str


# Deletes the lazy majority of trial-farming at zero cost to honest users.
_DISPOSABLE_EMAIL_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "guerrillamail.info", "sharklasers.com",
    "grr.la", "10minutemail.com", "10minutemail.net", "temp-mail.org", "tempmail.com",
    "tempmailo.com", "throwawaymail.com", "yopmail.com", "yopmail.fr", "getnada.com",
    "trashmail.com", "trashmail.de", "maildrop.cc", "dispostable.com", "mailnesia.com",
    "fakeinbox.com", "spam4.me", "mohmal.com", "emailondeck.com", "moakt.com",
    "mailcatch.com", "tempinbox.com", "burnermail.io", "temp-mail.io", "mintemail.com",
    "1secmail.com", "1secmail.org", "1secmail.net", "mailtemp.net", "tempr.email",
    "discard.email", "einrot.com", "spambog.com", "harakirimail.com", "inboxbear.com",
    "vomoto.com", "tafmail.com", "byom.de", "gishpuppy.com", "mytemp.email",
}


@app.post("/api/auth/send-code")
async def auth_send_code(req: AuthSendCodeRequest):
    email = req.email.strip().lower()
    if not email or "@" not in email or email.count("@") != 1 or "." not in email.split("@")[1]:
        raise HTTPException(400, "Invalid email")
    domain = email.split("@")[1]
    if domain in _DISPOSABLE_EMAIL_DOMAINS:
        raise HTTPException(400, "Please use a permanent email address — temporary inboxes aren't supported.")
    code = create_verify_code(email)
    try:
        from webapp.email_service import send_verification_code
        sent = send_verification_code(email, code)
        if not sent:
            print(f"[auth] Email delivery not configured for {email}")
    except Exception as e:
        print(f"[auth] Email send failed for {email}: {e}")
    return {"ok": True, "message": "Verification code sent"}


@app.post("/api/auth/verify")
async def auth_verify(req: AuthVerifyRequest, request: Request):
    email = req.email.strip().lower()
    if not verify_code(email, req.code):
        raise HTTPException(400, "Invalid or expired code")
    user = get_user_by_email(email)
    is_new = user is None
    if not user:
        user = create_user(email)
    if _posthog:
        try:
            _posthog.identify(distinct_id=str(user["id"]), properties={"email": email, "plan": user["plan"]})
        except Exception:
            pass
    track(user["id"], "signup_completed" if is_new else "login", {"new_user": is_new})
    token = create_session(user["id"])
    resp = JSONResponse({"ok": True, "user": _safe_user(user)})
    is_secure = request.url.scheme == "https" or os.getenv("FORCE_SECURE_COOKIES") == "1"
    resp.set_cookie("session", token, httponly=True, samesite="lax", secure=is_secure, max_age=30 * 86400, path="/")
    return resp


@app.get("/api/auth/me")
async def auth_me(request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse({"user": None}, status_code=200)
    return {"user": _safe_user(user)}


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    token = request.cookies.get("session")
    if token:
        delete_session(token)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session")
    return resp


def _is_admin_email(email: str) -> bool:
    admins = getattr(config, "ADMIN_EMAILS", [])
    return bool(admins) and (email or "").lower() in admins


def _is_pro(user: dict | None) -> bool:
    """Admins always get full Pro treatment (clean renders, full length, no credit drain)."""
    if not user:
        return False
    if _is_admin_email(user.get("email", "")):
        return True
    return user.get("plan") in ("pro", "starter", "daily", "starter_trial", "daily_trial")


def _safe_user(u: dict) -> dict:
    admins = getattr(config, "ADMIN_EMAILS", [])
    is_admin = bool(admins) and u.get("email", "").lower() in admins
    return {
        "id": u["id"],
        "email": u["email"],
        "plan": "pro" if is_admin else u["plan"],
        "credits": u["credits"],
        "created_at": u["created_at"],
        "is_admin": is_admin,
    }


# ---------------------------------------------------------------------------
# Stripe billing
# ---------------------------------------------------------------------------
class CheckoutRequest(BaseModel):
    plan: str = "starter_monthly"

class TopupRequest(BaseModel):
    credits: int = 5

_PLAN_PRICE_MAP = {
    "starter_monthly": lambda: config.STRIPE_PRICE_STARTER_MONTHLY or config.STRIPE_PRICE_ID,
    "starter_annual": lambda: config.STRIPE_PRICE_STARTER_ANNUAL or config.STRIPE_PRICE_ID_ANNUAL,
    "daily_monthly": lambda: config.STRIPE_PRICE_DAILY_MONTHLY,
    "daily_annual": lambda: config.STRIPE_PRICE_DAILY_ANNUAL,
    "monthly": lambda: config.STRIPE_PRICE_STARTER_MONTHLY or config.STRIPE_PRICE_ID,
    "annual": lambda: config.STRIPE_PRICE_STARTER_ANNUAL or config.STRIPE_PRICE_ID_ANNUAL,
}

_PLAN_CREDITS = {
    "starter_monthly": 15, "starter_annual": 15,
    "daily_monthly": 35, "daily_annual": 35,
    "monthly": 15, "annual": 15,
}


@app.post("/api/billing/checkout")
async def create_checkout(req: CheckoutRequest, request: Request):
    """Create a Stripe Checkout session with 7-day trial (card required)."""
    import stripe
    if not config.STRIPE_SECRET_KEY:
        raise HTTPException(500, "Stripe not configured")
    stripe.api_key = config.STRIPE_SECRET_KEY

    resolver = _PLAN_PRICE_MAP.get(req.plan)
    price_id = resolver() if resolver else None
    print(f"[stripe] Checkout: plan={req.plan} → price_id={price_id}")
    if not price_id:
        raise HTTPException(400, f"Plan '{req.plan}' not configured. Please set Stripe price IDs.")

    user = _current_user(request)
    if not user:
        raise HTTPException(401, "Sign in first")

    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        customer = stripe.Customer.create(email=user["email"])
        customer_id = customer.id
        update_user(user["id"], stripe_customer_id=customer_id)

    base_url = str(request.base_url).rstrip("/")
    try:
        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            subscription_data={"trial_period_days": 7},
            payment_method_collection="always",
            allow_promotion_codes=True,
            success_url=f"{base_url}/app#pipeline",
            cancel_url=f"{base_url}/app#pipeline",
            metadata={"user_id": str(user["id"]), "plan": req.plan},
        )
        return {"url": session.url}
    except Exception as e:
        print(f"[stripe] Checkout session creation failed: {e}")
        raise HTTPException(500, f"Payment setup failed: {e}")


@app.post("/api/billing/topup")
async def create_topup(req: TopupRequest, request: Request):
    """Create a Stripe Checkout session for a one-time credit top-up."""
    import stripe
    if not config.STRIPE_SECRET_KEY:
        raise HTTPException(500, "Stripe not configured")
    stripe.api_key = config.STRIPE_SECRET_KEY

    if req.credits == 15 and config.STRIPE_PRICE_TOPUP_15:
        price_id = config.STRIPE_PRICE_TOPUP_15
        credit_amount = 15
    elif config.STRIPE_PRICE_TOPUP_5:
        price_id = config.STRIPE_PRICE_TOPUP_5
        credit_amount = 5
    else:
        raise HTTPException(500, "Top-up pricing not configured")

    user = _current_user(request)
    if not user:
        raise HTTPException(401, "Sign in first")
    if user.get("plan") not in ("starter", "daily", "pro"):
        raise HTTPException(403, "Top-ups require an active subscription.")

    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        customer = stripe.Customer.create(email=user["email"])
        customer_id = customer.id
        update_user(user["id"], stripe_customer_id=customer_id)

    base_url = str(request.base_url).rstrip("/")
    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="payment",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{base_url}/app#pipeline",
        cancel_url=f"{base_url}/app#pipeline",
        metadata={"user_id": str(user["id"]), "topup_credits": str(credit_amount)},
    )
    return {"url": session.url}


@app.post("/api/billing/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events."""
    import stripe
    if not config.STRIPE_SECRET_KEY:
        raise HTTPException(500, "Stripe not configured")
    stripe.api_key = config.STRIPE_SECRET_KEY

    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        if not config.STRIPE_WEBHOOK_SECRET:
            raise HTTPException(500, "STRIPE_WEBHOOK_SECRET not configured")
        event = stripe.Webhook.construct_event(payload, sig, config.STRIPE_WEBHOOK_SECRET)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Webhook signature verification failed: {e}")

    # Convert Stripe objects to plain dicts for safe .get() access
    evt_type = event["type"]
    try:
        obj = json.loads(str(event["data"]["object"]))
    except Exception:
        obj = event["data"]["object"]
        if hasattr(obj, "to_dict"):
            obj = obj.to_dict()

    if evt_type == "checkout.session.completed":
        meta = obj.get("metadata") or {}
        user_id = meta.get("user_id")
        if user_id:
            topup = meta.get("topup_credits")
            if topup:
                add_credits(int(user_id), int(topup))
                print(f"[stripe] User {user_id} topped up {topup} credits")
            else:
                plan_key = meta.get("plan", "starter_monthly")
                plan_label = "daily_trial" if "daily" in plan_key else "starter_trial"
                update_user(int(user_id), plan=plan_label, credits=3,
                            stripe_sub_id=obj.get("subscription", ""))
                print(f"[stripe] User {user_id} started trial ({plan_label}, 3 credits)")

    elif evt_type == "invoice.paid":
        sub_id = obj.get("subscription")
        if sub_id:
            row = get_user_by_sub_id(sub_id)
            if row:
                plan = row.get("plan", "starter")
                if plan in ("starter_trial", "daily_trial"):
                    new_plan = "daily" if "daily" in plan else "starter"
                    credits = 35 if new_plan == "daily" else 15
                    update_user(row["id"], plan=new_plan, credits=credits)
                    print(f"[stripe] Trial converted: user {row['id']} → {new_plan} ({credits} credits)")
                else:
                    credits = 35 if plan == "daily" else 15
                    update_user(row["id"], credits=credits)
                    print(f"[stripe] Refilled {credits} credits for user {row['id']} ({plan})")

    elif evt_type in ("customer.subscription.deleted", "customer.subscription.updated"):
        sub_id = obj.get("id")
        if sub_id and obj.get("status") in ("canceled", "unpaid", "past_due"):
            row = get_user_by_sub_id(sub_id)
            if row:
                update_user(row["id"], plan="free")
                print(f"[stripe] User {row['id']} downgraded to free")

    return {"ok": True}


@app.get("/api/billing/status")
async def billing_status(request: Request):
    user = _current_user(request)
    if not user:
        return {"plan": "free", "credits": 0}
    return {
        "plan": user["plan"],
        "credits": user["credits"],
        "has_stripe": bool(config.STRIPE_SECRET_KEY),
        "publishable_key": config.STRIPE_PUBLISHABLE_KEY,
    }


@app.post("/api/billing/portal")
async def create_portal_session(request: Request):
    """Create a Stripe Customer Portal session for subscription management."""
    import stripe
    if not config.STRIPE_SECRET_KEY:
        raise HTTPException(500, "Stripe not configured")
    stripe.api_key = config.STRIPE_SECRET_KEY

    user = _current_user(request)
    if not user:
        raise HTTPException(401, "Sign in first")

    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(400, "No billing account found. Start a subscription first.")

    base_url = str(request.base_url).rstrip("/")
    try:
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{base_url}/app#billing",
        )
        return {"url": session.url}
    except Exception as e:
        print(f"[stripe] Portal session failed: {e}")
        raise HTTPException(500, f"Could not open billing portal: {e}")


@app.post("/api/billing/end-trial")
async def end_trial_early(request: Request):
    """End the 7-day trial immediately and start billing (grants full credits)."""
    import stripe
    if not config.STRIPE_SECRET_KEY:
        raise HTTPException(500, "Stripe not configured")
    stripe.api_key = config.STRIPE_SECRET_KEY

    user = _current_user(request)
    if not user:
        raise HTTPException(401, "Sign in first")
    if user.get("plan") not in ("starter_trial", "daily_trial"):
        raise HTTPException(400, "No active trial to end.")

    sub_id = user.get("stripe_sub_id")
    if not sub_id:
        raise HTTPException(400, "No subscription found.")

    try:
        stripe.Subscription.modify(sub_id, trial_end="now")
        print(f"[stripe] Trial ended early for user {user['id']} (sub {sub_id})")
        return {"ok": True, "message": "Trial ended. Your plan is now active."}
    except Exception as e:
        print(f"[stripe] End trial failed: {e}")
        raise HTTPException(500, f"Could not end trial: {e}")


CURATED_VOICES = [
    {"id": "Charon", "name": "Charon", "tag": "Informative", "desc": "Clear, authoritative narrator — best for documentaries", "default": True},
    {"id": "Kore", "name": "Kore", "tag": "Firm", "desc": "Strong, confident delivery with gravitas"},
    {"id": "Gacrux", "name": "Gacrux", "tag": "Mature", "desc": "Deep, seasoned voice with natural warmth"},
    {"id": "Schedar", "name": "Schedar", "tag": "Even", "desc": "Calm, steady pacing — great for explainers"},
    {"id": "Puck", "name": "Puck", "tag": "Upbeat", "desc": "Energetic, engaging — ideal for listicles"},
    {"id": "Sulafat", "name": "Sulafat", "tag": "Warm", "desc": "Gentle, approachable storytelling tone"},
]


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class TitleRequest(BaseModel):
    niche: str
    topic: str = ""

class ScriptRequest(BaseModel):
    title: str
    niche: str
    target_minutes: int = 8

class VoiceoverRequest(BaseModel):
    script: str
    voice: str = "Charon"

class VoicePreviewRequest(BaseModel):
    voice: str
    text: str = "Welcome to this episode. Today we uncover one of history's greatest untold stories."

class ThumbnailRequest(BaseModel):
    title: str
    niche_style: str = ""
    count: int = 2

class BuildRequest(BaseModel):
    script: str
    voiceover_path: str
    title: str = ""
    niche: str = "animated_explainer"
    recipe: str = "animated_explainer"
    thumbnail_path: str = ""
    notify_email: str = ""

class UploadKitRequest(BaseModel):
    title: str
    script: str
    niche: str = ""

class ChannelFetchRequest(BaseModel):
    channel_url: str
    max_videos: int = 20

class ChannelAnalyzeRequest(BaseModel):
    channel_data: dict | None = None

class IdeasRequest(BaseModel):
    channel_data: dict | None = None
    num_ideas: int = 7
    analysis: str = ""

class ClaudeTitlesRequest(BaseModel):
    video_idea: str
    channel_data: dict | None = None

class ClaudeScriptRequest(BaseModel):
    title: str
    video_idea: str = ""
    channel_data: dict | None = None
    target_minutes: int = 8

class VoiceoverStudioRequest(BaseModel):
    script: str
    voice: str = "Charon"
    style_preset: str = "Narrator"
    custom_notes: str = ""

class NicheAnalyzeRequest(BaseModel):
    youtube_url: str
    minutes: int = 5

class KeyTestRequest(BaseModel):
    key_name: str
    key_value: str = ""


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def landing():
    lp = STATIC_DIR / "landing.html"
    if lp.exists():
        return FileResponse(str(lp))
    return RedirectResponse("/app")


@app.get("/app", response_class=HTMLResponse)
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page():
    return FileResponse(str(STATIC_DIR / "privacy.html"))


@app.get("/terms", response_class=HTMLResponse)
async def terms_page():
    return FileResponse(str(STATIC_DIR / "terms.html"))


# ---------------------------------------------------------------------------
# Niches
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health():
    """Public health check. Reports which DB backend is active (no secrets)."""
    return {"status": "ok", "db": backend_name()}


@app.get("/api/config")
async def get_client_config():
    """Public front-end config: analytics keys only (safe to expose)."""
    return {
        "posthog_key": config.POSTHOG_KEY,
        "posthog_host": config.POSTHOG_HOST,
        "sentry_dsn": config.SENTRY_DSN,
    }


@app.get("/api/niches")
async def get_niches():
    niches = []
    for f in sorted(NICHES_DIR.glob("*.json")):
        with open(f) as fh:
            niches.append(json.load(fh))
    return niches


# ---------------------------------------------------------------------------
# Voices
# ---------------------------------------------------------------------------
@app.get("/api/voices")
async def get_voices():
    return CURATED_VOICES


@app.get("/api/voices/all")
async def get_all_voices():
    from core.voiceover_gen import VOICES
    return [{"id": name, "name": name, "tag": desc} for name, desc in VOICES.items()]


# ---------------------------------------------------------------------------
# Titles (Gemini-based)
# ---------------------------------------------------------------------------
@app.post("/api/titles")
async def generate_titles(req: TitleRequest, user: dict = Depends(require_active_plan)):
    from google import genai

    if not config.GEMINI_KEY:
        raise HTTPException(500, "GEMINI_KEY not configured on backend")

    client = genai.Client(api_key=config.GEMINI_KEY)
    niche_data = _load_niche(req.niche)
    niche_name = niche_data.get("name", req.niche) if niche_data else req.niche
    topic_hint = f"\nTopic hint from user: {req.topic}" if req.topic else ""

    prompt = (
        f"Generate exactly 3 viral YouTube video titles for the '{niche_name}' niche. "
        f"These should be compelling, curiosity-driven titles that get clicks. "
        f"Each title should be a different angle on a fascinating topic. "
        f"Return ONLY a JSON array of 3 strings, nothing else.{topic_hint}"
    )

    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
        )
        raw = resp.text.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        titles = json.loads(raw)
        if not isinstance(titles, list) or len(titles) < 1:
            raise ValueError("Expected list of titles")
        return {"titles": titles[:3]}
    except Exception as e:
        raise HTTPException(500, f"Title generation failed: {e}")


# ---------------------------------------------------------------------------
# Script (Gemini-based)
# ---------------------------------------------------------------------------
@app.post("/api/script")
async def generate_script(req: ScriptRequest, user: dict = Depends(require_active_plan)):
    from google import genai

    if not config.GEMINI_KEY:
        raise HTTPException(500, "GEMINI_KEY not configured on backend")

    client = genai.Client(api_key=config.GEMINI_KEY)
    niche_data = _load_niche(req.niche)
    style_hint = ""
    if niche_data:
        style_hint = f"\nVideo style: {niche_data.get('description', '')}"

    word_target = req.target_minutes * 150

    prompt = (
        f"Write a YouTube video script for this title: \"{req.title}\"\n\n"
        f"Target length: approximately {word_target} words ({req.target_minutes} minutes when narrated).{style_hint}\n\n"
        f"Rules:\n"
        f"- Write ONLY the narration script — no stage directions, no [brackets], no scene descriptions\n"
        f"- Open with a strong hook in the first 2 sentences\n"
        f"- Use short, punchy sentences for pacing\n"
        f"- Include specific facts, names, dates, numbers — not vague statements\n"
        f"- End with a thought-provoking conclusion\n"
        f"- Do NOT include any intro/outro channel plugs\n\n"
        f"Return ONLY the script text, nothing else."
    )

    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
        )
        script = resp.text.strip()
        return {"script": script, "word_count": len(script.split())}
    except Exception as e:
        raise HTTPException(500, f"Script generation failed: {e}")


# ---------------------------------------------------------------------------
# Voiceover
# ---------------------------------------------------------------------------
@app.post("/api/voiceover")
async def generate_voiceover(req: VoiceoverRequest, user: dict = Depends(require_active_plan)):
    from core.voiceover_gen import generate_voiceover as gen_vo

    out_dir = str(OUTPUT_DIR / "voiceovers" / str(int(time.time())))
    try:
        wav_path = gen_vo(script=req.script, voice=req.voice, style_preset="Narrator", output_dir=out_dir)
        rel = os.path.relpath(wav_path, str(ROOT))
        return {"path": wav_path, "url": f"/api/files/{rel}"}
    except Exception as e:
        raise HTTPException(500, f"Voiceover generation failed: {e}")


@app.post("/api/voiceover/upload")
async def upload_voiceover(file: UploadFile = File(...), user: dict = Depends(require_active_plan)):
    """Accept a user-uploaded voiceover file (WAV, MP3, M4A) and return its path."""
    import subprocess

    allowed = {".wav", ".mp3", ".m4a", ".ogg", ".webm"}
    ext = Path(file.filename or "audio.wav").suffix.lower()
    if ext not in allowed:
        raise HTTPException(400, f"Unsupported format '{ext}'. Use WAV, MP3, or M4A.")

    out_dir = OUTPUT_DIR / "voiceovers" / str(int(time.time()))
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_path = out_dir / f"upload_raw{ext}"
    with open(raw_path, "wb") as f:
        content = await file.read()
        f.write(content)

    wav_path = out_dir / "voiceover.wav"
    if ext == ".wav":
        shutil.copy(str(raw_path), str(wav_path))
    else:
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(raw_path), "-ar", "24000", "-ac", "1", str(wav_path)],
                capture_output=True, check=True, timeout=60,
            )
        except Exception as e:
            raise HTTPException(500, f"Audio conversion failed: {e}")

    rel = os.path.relpath(str(wav_path), str(ROOT))
    return {"path": str(wav_path), "url": f"/api/files/{rel}"}


@app.post("/api/voiceover/preview")
async def voice_preview(req: VoicePreviewRequest, user: dict = Depends(require_active_plan)):
    from core.voiceover_gen import generate_voiceover as gen_vo

    out_dir = str(OUTPUT_DIR / "voice_previews")
    cache_path = Path(out_dir) / f"{req.voice.lower()}_preview.wav"

    if cache_path.exists():
        rel = os.path.relpath(str(cache_path), str(ROOT))
        return {"url": f"/api/files/{rel}"}

    try:
        wav_path = gen_vo(script=req.text, voice=req.voice, style_preset="Narrator", output_dir=out_dir)
        if Path(wav_path).exists() and not cache_path.exists():
            Path(wav_path).rename(cache_path)
            wav_path = str(cache_path)
        rel = os.path.relpath(wav_path, str(ROOT))
        return {"url": f"/api/files/{rel}"}
    except Exception as e:
        raise HTTPException(500, f"Voice preview failed: {e}")


@app.post("/api/voiceover/studio")
async def voiceover_studio(req: VoiceoverStudioRequest, user: dict = Depends(require_active_plan)):
    from core.voiceover_gen import generate_voiceover as gen_vo

    out_dir = str(OUTPUT_DIR / "voiceovers" / str(int(time.time())))
    try:
        wav_path = gen_vo(
            script=req.script,
            voice=req.voice,
            style_preset=req.style_preset,
            custom_notes=req.custom_notes,
            output_dir=out_dir,
        )
        rel = os.path.relpath(wav_path, str(ROOT))
        return {"path": wav_path, "url": f"/api/files/{rel}"}
    except Exception as e:
        raise HTTPException(500, f"Voiceover generation failed: {e}")


# ---------------------------------------------------------------------------
# Thumbnails
# ---------------------------------------------------------------------------
@app.post("/api/thumbnail")
async def generate_thumbnail(req: ThumbnailRequest, user: dict = Depends(require_active_plan)):
    from core.thumbnail_gen import generate_thumbnail_no_refs

    out_dir = str(OUTPUT_DIR / "thumbnails" / str(int(time.time())))
    try:
        paths = generate_thumbnail_no_refs(
            title=req.title,
            style_description=req.niche_style or "Bold, eye-catching YouTube thumbnail with dramatic lighting",
            output_dir=out_dir,
            count=req.count,
        )
        if not paths:
            raise ValueError("No thumbnails generated")
        urls = [f"/api/files/{os.path.relpath(p, str(ROOT))}" for p in paths[:req.count]]
        return {"thumbnails": urls, "paths": paths[:req.count]}
    except Exception as e:
        raise HTTPException(500, f"Thumbnail generation failed: {e}")


@app.post("/api/thumbnail/with-refs")
async def generate_thumbnail_with_refs(
    title: str = Form(...),
    style: str = Form(""),
    count: int = Form(2),
    refs: list[UploadFile] = File(default=[]),
    user: dict = Depends(require_active_plan),
):
    from core.thumbnail_gen import generate_thumbnails

    ref_paths = []
    for ref in refs:
        dest = UPLOAD_DIR / f"ref_{int(time.time())}_{ref.filename}"
        with open(dest, "wb") as f:
            content = await ref.read()
            f.write(content)
        ref_paths.append(str(dest))

    out_dir = str(OUTPUT_DIR / "thumbnails" / str(int(time.time())))
    try:
        paths = generate_thumbnails(
            title=title,
            reference_image_paths=ref_paths,
            style_prompt=style,
            num_images=count,
            output_dir=out_dir,
        )
        if not paths:
            raise ValueError("No thumbnails generated")
        urls = [f"/api/files/{os.path.relpath(p, str(ROOT))}" for p in paths]
        return {"thumbnails": urls, "paths": paths}
    except Exception as e:
        raise HTTPException(500, f"Thumbnail generation failed: {e}")


# ---------------------------------------------------------------------------
# Build (recipe-aware + SSE progress)
# ---------------------------------------------------------------------------
def _safe_user_path(path_str: str, label: str) -> None:
    """Validate that a user-supplied path stays inside OUTPUT_DIR."""
    if not path_str:
        return
    resolved = Path(path_str).resolve()
    if not resolved.is_relative_to(OUTPUT_DIR.resolve()):
        raise HTTPException(400, f"Invalid {label} path")
    if not resolved.is_file():
        raise HTTPException(400, f"{label} file not found")


@app.post("/api/build")
async def start_build(req: BuildRequest, request: Request):
    user = _current_user(request)
    if not user:
        raise HTTPException(401, "Sign in to continue.")
    user_id = user["id"]

    _safe_user_path(req.voiceover_path, "voiceover")
    _safe_user_path(req.thumbnail_path, "thumbnail")

    is_admin = _is_admin_email(user.get("email", ""))
    credit_deducted = False
    if not is_admin:
        if not deduct_credit(user_id):
            raise HTTPException(402, "No credits remaining. Upgrade your plan for more videos.")
        credit_deducted = True

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "status": "queued",
        "progress": [],
        "result": None,
        "request": req.model_dump(),
        "user_id": user_id,
        "credit_deducted": credit_deducted,
    }

    import threading
    t = threading.Thread(target=_run_build, args=(job_id, req), daemon=True)
    t.start()
    return {"job_id": job_id}


def _run_build(job_id: str, req: BuildRequest):
    job = _jobs[job_id]
    job["status"] = "running"
    started_at = time.time()
    user_id = job.get("user_id")
    est_minutes = round(len(req.script.split()) / 150, 2) if req.script else 0

    def on_progress(msg: str):
        job["progress"].append({"time": time.time(), "message": msg})

    recipe = req.recipe or "animated_explainer"
    track(user_id or "anon", "render_started", {"recipe": recipe, "target_minutes": est_minutes})

    try:
        if recipe == "animated_explainer":
            from core.explainer_pipeline import run_explainer_pipeline
            result = run_explainer_pipeline(
                script=req.script,
                voiceover_path=req.voiceover_path,
                output_name="pipeline_video.mp4",
                style_preset="default",
                progress_callback=on_progress,
            )
        elif recipe == "broll_only":
            from core.pipeline import run_pipeline
            result = run_pipeline(
                script=req.script,
                voiceover_path=req.voiceover_path,
                output_name="pipeline_video.mp4",
                progress_callback=on_progress,
            )
        elif recipe == "broll_cinematic":
            from core.pipeline import run_cinematic_pipeline
            result = run_cinematic_pipeline(
                script=req.script,
                voiceover_path=req.voiceover_path,
                output_name="pipeline_video.mp4",
                progress_callback=on_progress,
            )
        elif recipe == "avatar_plus_broll":
            from core.avatar_pipeline import run_avatar_pipeline
            result = run_avatar_pipeline(
                script=req.script,
                voiceover_path=req.voiceover_path if req.voiceover_path else None,
                output_name="pipeline_video.mp4",
                progress_callback=on_progress,
            )
        else:
            raise ValueError(f"Unknown recipe: {recipe}")

        # Persist the finished video (and thumbnail) to durable storage.
        ts = int(time.time())
        try:
            output_url = storage.store_file(
                result["output_path"], f"videos/{user_id}/{ts}_{job_id}.mp4", "video/mp4"
            )
        except Exception as up_err:
            print(f"[storage] video upload failed, falling back to local: {up_err}")
            output_url = f"/api/files/{os.path.relpath(result['output_path'], str(ROOT))}"

        thumb_url = ""
        if req.thumbnail_path and os.path.exists(req.thumbnail_path):
            try:
                ext = os.path.splitext(req.thumbnail_path)[1] or ".png"
                thumb_url = storage.store_file(
                    req.thumbnail_path, f"thumbnails/{user_id}/{ts}_{job_id}{ext}"
                )
            except Exception as th_err:
                print(f"[storage] thumbnail upload failed: {th_err}")

        video_id = None
        if user_id:
            try:
                video_id = create_video(
                    user_id=user_id,
                    title=req.title or "Untitled",
                    recipe=recipe,
                    video_url=output_url,
                    thumbnail_url=thumb_url,
                )
            except Exception as rec_err:
                print(f"[videos] failed to save video record: {rec_err}")

        job["status"] = "complete"
        job["result"] = {
            "output_path": result["output_path"],
            "output_url": output_url,
            "thumbnail_url": thumb_url,
            "video_id": video_id,
            "job_dir": result.get("job_dir", ""),
            "concepts": len(result.get("slots", [])),
            "timing": result.get("timing", {}),
        }

        duration = round(time.time() - started_at, 1)
        cost = _estimate_cost_pence(recipe, est_minutes)
        try:
            log_render_event(user_id, job_id, recipe, "succeeded", duration, est_minutes, cost)
        except Exception as log_err:
            print(f"[telemetry] render log failed: {log_err}")
        track(user_id or "anon", "render_succeeded", {
            "recipe": recipe, "duration_sec": duration,
            "target_minutes": est_minutes, "cost_pence": cost,
        })

        if req.notify_email:
            try:
                from webapp.email_service import send_video_ready
                send_video_ready(req.notify_email, req.title, output_url)
            except Exception as email_err:
                print(f"[build] Email notification failed: {email_err}")

    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(e)
        except Exception:
            pass
        duration = round(time.time() - started_at, 1)
        err_class = type(e).__name__
        try:
            log_render_event(user_id, job_id, recipe, "failed", duration, est_minutes, 0, err_class)
        except Exception as log_err:
            print(f"[telemetry] render log failed: {log_err}")
        track(user_id or "anon", "render_failed", {
            "recipe": recipe, "duration_sec": duration, "error_class": err_class,
        })
        if user_id and job.get("credit_deducted"):
            refund_credit(user_id)
            job["credit_deducted"] = False
            print(f"[build] Auto-refunded credit for user {user_id} after build failure")


def _get_user_job(job_id: str, request: Request) -> dict:
    """Validate job exists and belongs to the requesting user."""
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")
    user = _current_user(request)
    job = _jobs[job_id]
    if not user or job.get("user_id") != user["id"]:
        raise HTTPException(403, "Access denied")
    return job


@app.get("/api/build/{job_id}/progress")
async def build_progress(job_id: str, request: Request):
    job = _get_user_job(job_id, request)

    async def stream():
        seen = 0
        while True:
            if await request.is_disconnected():
                break
            for msg in job["progress"][seen:]:
                yield {"event": "progress", "data": json.dumps(msg)}
                seen += 1
            if job["status"] == "complete":
                yield {"event": "complete", "data": json.dumps(job["result"])}
                break
            elif job["status"] in ("error", "cancelled"):
                yield {"event": "error", "data": json.dumps({"error": job.get("error", "Unknown")})}
                break
            await asyncio.sleep(1)

    return EventSourceResponse(stream())


@app.delete("/api/build/{job_id}")
async def cancel_build(job_id: str, request: Request):
    job = _get_user_job(job_id, request)
    if job["status"] in ("queued", "running"):
        job["status"] = "cancelled"
        job["error"] = "Cancelled by user"
        if job.get("credit_deducted"):
            refund_credit(job["user_id"])
            job["credit_deducted"] = False
            print(f"[build] Refunded credit on cancel for user {job['user_id']}")
    return {"status": "cancelled"}


@app.get("/api/build/{job_id}/result")
async def build_result(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")
    job = _jobs[job_id]
    if job["status"] != "complete":
        return {"status": job["status"], "progress": len(job["progress"])}
    return job["result"]


# ---------------------------------------------------------------------------
# Upload Kit
# ---------------------------------------------------------------------------
# Trial outputs carry a clickable, measurable attribution line in the default
# description — the only trackable layer of the distribution loop.
_TRIAL_ATTRIBUTION = (
    "\n\n———\nMade with ChannelRecipe → "
    "https://channelrecipe.com/?utm_source=youtube&utm_medium=description&utm_campaign=trial"
)


def _maybe_attribute(kit: dict, user: dict) -> dict:
    if user and not _is_pro(user):
        desc = (kit.get("description") or "").rstrip()
        if "channelrecipe.com" not in desc:
            kit["description"] = desc + _TRIAL_ATTRIBUTION
    return kit


@app.post("/api/upload-kit")
async def generate_upload_kit(req: UploadKitRequest, user: dict = Depends(require_active_plan)):
    from google import genai

    if not config.GEMINI_KEY:
        return _maybe_attribute({"description": f"Check out this video: {req.title}", "tags": ["youtube", "video"], "hashtags": []}, user)

    client = genai.Client(api_key=config.GEMINI_KEY)
    prompt = (
        f"Generate YouTube upload metadata for this video:\n"
        f"Title: \"{req.title}\"\nScript excerpt: \"{req.script[:500]}\"\n\n"
        f"Return a JSON object with:\n"
        f"- \"description\": a 150-200 word YouTube description with relevant keywords, 3 paragraph breaks, and a call to action\n"
        f"- \"tags\": array of 15-20 relevant YouTube tags for SEO\n"
        f"- \"hashtags\": array of 3 hashtags\n\nReturn ONLY valid JSON."
    )
    try:
        resp = client.models.generate_content(model="gemini-2.5-flash", contents=[{"role": "user", "parts": [{"text": prompt}]}])
        raw = resp.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return _maybe_attribute(json.loads(raw), user)
    except Exception:
        return _maybe_attribute({"description": f"Check out this video: {req.title}", "tags": ["youtube", "video"], "hashtags": []}, user)


# ---------------------------------------------------------------------------
# Channel Data + Analysis (Script Studio)
# ---------------------------------------------------------------------------
@app.post("/api/channel/fetch")
async def fetch_channel(req: ChannelFetchRequest, user: dict = Depends(require_active_plan)):
    from core.channel_data import fetch_channel_data

    if not config.YOUTUBE_API_KEY:
        raise HTTPException(400, "YouTube API key not configured. Add it in Settings.")

    try:
        data = fetch_channel_data(
            channel_url=req.channel_url,
            yt_api_key=config.YOUTUBE_API_KEY,
            downsub_key=config.DOWNSUB_KEY,
            max_videos=req.max_videos,
        )
        return data
    except Exception as e:
        raise HTTPException(500, f"Channel fetch failed: {e}")


@app.post("/api/channel/analyze")
async def analyze_channel(req: ChannelAnalyzeRequest, user: dict = Depends(require_active_plan)):
    if not config.ANTHROPIC_KEY:
        return {"analysis": "Claude API key not configured. Add it in Settings to enable channel analysis."}

    try:
        from core.script_gen import analyze_channel as _analyze
        result = _analyze(channel_data=req.channel_data, api_key=config.ANTHROPIC_KEY)
        return {"analysis": result}
    except Exception as e:
        raise HTTPException(500, f"Channel analysis failed: {e}")


@app.post("/api/ideas")
async def generate_ideas(req: IdeasRequest, user: dict = Depends(require_active_plan)):
    if not config.ANTHROPIC_KEY:
        raise HTTPException(400, "Claude API key not configured. Add it in Settings.")

    try:
        from core.script_gen import generate_ideas as _gen
        result = _gen(
            channel_data=req.channel_data,
            api_key=config.ANTHROPIC_KEY,
            num_ideas=req.num_ideas,
            analysis=req.analysis,
        )
        ideas = [line.strip() for line in result.split("\n") if line.strip()]
        return {"ideas": ideas, "raw": result}
    except Exception as e:
        raise HTTPException(500, f"Idea generation failed: {e}")


@app.post("/api/titles/claude")
async def generate_titles_claude(req: ClaudeTitlesRequest, user: dict = Depends(require_active_plan)):
    if not config.ANTHROPIC_KEY:
        raise HTTPException(400, "Claude API key not configured. Add it in Settings.")

    try:
        from core.script_gen import generate_titles as _gen
        result = _gen(
            video_idea=req.video_idea,
            channel_data=req.channel_data,
            api_key=config.ANTHROPIC_KEY,
        )
        titles = [line.strip().lstrip("0123456789.-) ") for line in result.split("\n") if line.strip()]
        return {"titles": titles[:5], "raw": result}
    except Exception as e:
        raise HTTPException(500, f"Title generation failed: {e}")


@app.post("/api/script/claude")
async def generate_script_claude(req: ClaudeScriptRequest, user: dict = Depends(require_active_plan)):
    if not config.ANTHROPIC_KEY:
        raise HTTPException(400, "Claude API key not configured. Add it in Settings.")

    try:
        from core.script_gen import generate_script as _gen
        result = _gen(
            title=req.title,
            video_idea=req.video_idea,
            channel_data=req.channel_data,
            api_key=config.ANTHROPIC_KEY,
            target_length_min=req.target_minutes,
        )
        return {"script": result, "word_count": len(result.split())}
    except Exception as e:
        raise HTTPException(500, f"Script generation failed: {e}")


# ---------------------------------------------------------------------------
# Niche Screener
# ---------------------------------------------------------------------------
@app.post("/api/niche/analyze")
async def analyze_niche(req: NicheAnalyzeRequest, user: dict = Depends(require_active_plan)):
    try:
        from core.video_analyzer import analyze_video
        profile = analyze_video(req.youtube_url, analyze_minutes=req.minutes)
        profile_dict = {
            "niche_name": profile.niche_name,
            "recipe": profile.recipe,
            "broll_type": profile.broll_type,
            "default_swap_rate": profile.default_swap_rate,
            "visual_style": profile.visual_style,
            "avatar_config": profile.avatar_config,
            "automatable_pct": profile.automatable_pct,
            "sample_queries": profile.sample_queries,
            "notes": profile.notes,
        }
        summary = (
            f"Niche: {profile.niche_name}\n"
            f"Recommended Recipe: {profile.recipe}\n"
            f"B-Roll Type: {profile.broll_type}\n"
            f"Swap Rate: {profile.default_swap_rate}\n"
            f"Automatable: {profile.automatable_pct}%\n"
            f"Notes: {profile.notes}"
        )
        return {"profile": profile_dict, "summary": summary}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        err = str(e)
        if "MIME type" in err or "text/html" in err:
            raise HTTPException(400, "Could not analyze this video. Make sure the URL is a public YouTube video (not a channel, playlist, or private video).")
        raise HTTPException(500, f"Niche analysis failed. Please try a different video URL.")


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------
RETENTION_FREE_DAYS = 7
RETENTION_PAID_DAYS = 30


def _retention_days(request: Request | None = None) -> int:
    if request:
        user = _current_user(request)
        if user and user.get("plan") in ("pro", "starter", "daily", "starter_trial", "daily_trial"):
            return RETENTION_PAID_DAYS
    return RETENTION_FREE_DAYS


def _video_to_entry(v: dict, retention_secs: float, now: float) -> dict:
    age = now - float(v.get("created_at") or now)
    expires_in_days = max(0, round((retention_secs - age) / 86400, 1))
    try:
        tags = json.loads(v.get("tags") or "[]")
    except Exception:
        tags = []
    try:
        hashtags = json.loads(v.get("hashtags") or "[]")
    except Exception:
        hashtags = []
    return {
        "id": v.get("id"),
        "type": "video",
        "title": v.get("title") or "Untitled",
        "recipe": v.get("recipe") or "",
        "url": v.get("video_url") or "",
        "thumbnail_url": v.get("thumbnail_url") or "",
        "description": v.get("description") or "",
        "tags": tags,
        "hashtags": hashtags,
        "timestamp": float(v.get("created_at") or now) * 1000,
        "expires_in_days": expires_in_days,
        "expired": age > retention_secs,
    }


@app.get("/api/history")
async def get_history(type: str = "all", request: Request = None, user: dict = Depends(require_user)):
    now = time.time()
    retention_secs = _retention_days(request) * 86400
    videos = list_videos(user["id"])
    entries = [_video_to_entry(v, retention_secs, now) for v in videos]
    return {"entries": entries, "retention_days": _retention_days(request)}


@app.get("/api/videos")
async def api_list_videos(request: Request = None, user: dict = Depends(require_user)):
    now = time.time()
    retention_secs = _retention_days(request) * 86400
    videos = [_video_to_entry(v, retention_secs, now) for v in list_videos(user["id"])]
    return {"videos": videos, "retention_days": _retention_days(request)}


@app.get("/api/videos/{video_id}")
async def api_get_video(video_id: int, request: Request = None, user: dict = Depends(require_user)):
    v = get_video(video_id, user["id"])
    if not v:
        raise HTTPException(status_code=404, detail="Video not found")
    return _video_to_entry(v, _retention_days(request) * 86400, time.time())


class VideoKitRequest(BaseModel):
    description: str = ""
    tags: list[str] | str = ""
    hashtags: list[str] | str = ""


@app.post("/api/videos/{video_id}/kit")
async def api_save_video_kit(video_id: int, req: VideoKitRequest, user: dict = Depends(require_user)):
    v = get_video(video_id, user["id"])
    if not v:
        raise HTTPException(status_code=404, detail="Video not found")
    tags = req.tags if isinstance(req.tags, list) else [t.strip() for t in str(req.tags).split(",") if t.strip()]
    hashtags = req.hashtags if isinstance(req.hashtags, list) else [h.strip() for h in str(req.hashtags).split(",") if h.strip()]
    update_video_kit(video_id, user["id"], req.description, json.dumps(tags), json.dumps(hashtags))
    return {"ok": True}


@app.delete("/api/videos/{video_id}")
async def api_delete_video(video_id: int, user: dict = Depends(require_user)):
    row = delete_video(video_id, user["id"])
    if not row:
        raise HTTPException(status_code=404, detail="Video not found")
    # Best-effort removal of stored objects when they live in Spaces.
    for url_field in ("video_url", "thumbnail_url"):
        url = row.get(url_field) or ""
        if storage.is_remote() and url and "digitaloceanspaces.com/" in url:
            key = url.split("digitaloceanspaces.com/", 1)[1]
            storage.delete_key(key)
    return {"ok": True}


@app.post("/api/history/cleanup")
async def cleanup_history(request: Request, user: dict = Depends(require_user)):
    """Remove expired output directories."""
    output = ROOT / "output"
    if not output.exists():
        return {"removed": 0}
    now = time.time()
    retention_secs = _retention_days(request) * 86400
    removed = 0
    for d in output.iterdir():
        if not d.is_dir():
            continue
        if (now - d.stat().st_mtime) > retention_secs:
            shutil.rmtree(str(d), ignore_errors=True)
            removed += 1
    return {"removed": removed}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
KEY_MAP = {
    "gemini": "GEMINI_KEY",
    "claude": "ANTHROPIC_KEY",
    "youtube": "YOUTUBE_API_KEY",
    "atlascloud": "ATLASCLOUD_KEY",
    "heygen": "HEYGEN_KEY",
    "pexels": "PEXELS_KEY",
    "downsub": "DOWNSUB_KEY",
}


@app.get("/api/admin/stats")
async def admin_stats(days: int = 30, admin: dict = Depends(require_admin)):
    """Lightweight COGS / render-health snapshot (admin only).

    PostHog owns the pretty funnels; this is the authoritative unit-economics
    view straight from our own render log.
    """
    return render_stats(days=days)


@app.get("/api/settings/keys")
async def get_settings(admin: dict = Depends(require_admin)):
    result = {}
    for short, env_name in KEY_MAP.items():
        val = os.environ.get(env_name, "") or getattr(config, env_name, "")
        result[short] = {"configured": bool(val), "env_name": env_name}
    return result


@app.post("/api/settings/keys")
async def save_settings(keys: dict, admin: dict = Depends(require_admin)):
    env_path = ROOT / ".env"
    existing = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                existing[k.strip()] = v.strip()

    for short, value in keys.items():
        env_name = KEY_MAP.get(short)
        if env_name and value:
            existing[env_name] = value
            os.environ[env_name] = value
            setattr(config, env_name, value)

    with open(env_path, "w") as f:
        for k, v in existing.items():
            f.write(f"{k}={v}\n")

    return {"message": "Keys saved successfully"}


@app.post("/api/settings/test-key")
async def test_key(req: KeyTestRequest, admin: dict = Depends(require_admin)):
    env_name = KEY_MAP.get(req.key_name)
    key_val = req.key_value or os.environ.get(env_name or "", "") or getattr(config, env_name or "", "")

    if not key_val:
        return {"ok": False, "error": "Key not provided"}

    try:
        if req.key_name == "gemini":
            from google import genai
            client = genai.Client(api_key=key_val)
            client.models.generate_content(model="gemini-2.5-flash", contents="Say hi")
            return {"ok": True}

        elif req.key_name == "claude":
            import anthropic
            client = anthropic.Anthropic(api_key=key_val)
            client.messages.create(model="claude-haiku-4-5", max_tokens=10, messages=[{"role": "user", "content": "Hi"}])
            return {"ok": True}

        elif req.key_name == "youtube":
            import httpx
            r = httpx.get(f"https://www.googleapis.com/youtube/v3/channels?part=id&id=UC_x5XG1OV2P6uZZ5FSM9Ttw&key={key_val}", timeout=10)
            return {"ok": r.status_code == 200}

        elif req.key_name == "pexels":
            import httpx
            r = httpx.get("https://api.pexels.com/v1/search?query=test&per_page=1", headers={"Authorization": key_val}, timeout=10)
            return {"ok": r.status_code == 200}

        elif req.key_name == "heygen":
            import httpx
            r = httpx.get("https://api.heygen.com/v2/avatars", headers={"x-api-key": key_val}, timeout=10)
            return {"ok": r.status_code == 200}

        elif req.key_name == "atlascloud":
            return {"ok": bool(key_val)}

        else:
            return {"ok": bool(key_val)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ---------------------------------------------------------------------------
# File serving
# ---------------------------------------------------------------------------
@app.get("/api/files/{file_path:path}")
async def serve_file(file_path: str):
    full = (ROOT / file_path).resolve()
    if not full.is_relative_to(ROOT.resolve()):
        raise HTTPException(403, "Access denied")
    if not full.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(str(full))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_niche(niche_key: str) -> dict | None:
    path = NICHES_DIR / f"{niche_key}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    print(f"\n  ChannelRecipe")
    print(f"  http://localhost:8000\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
