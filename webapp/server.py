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

from fastapi import Cookie, Depends, FastAPI, File, Form, HTTPException, Header, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse
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
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
        def _sentry_before_send(event, hint):
            """Drop expected client/user errors so Sentry stays signal-heavy."""
            exc_info = hint.get("exc_info")
            if exc_info:
                exc = exc_info[1]
                # Fly machine stop / deploy / Ctrl-C — not app bugs
                if isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                    return None
                # FastAPI HTTPException — drop expected client / ops responses
                try:
                    from fastapi import HTTPException as _HTTPExc
                    if isinstance(exc, _HTTPExc):
                        if exc.status_code < 500:
                            return None
                        detail = str(exc.detail or "").lower()
                        # Known ops 503s — not product bugs
                        if exc.status_code == 503 and any(s in detail for s in (
                            "gemini access denied",
                            "niche video analysis",
                            "pick a recipe manually",
                            "coming soon",
                        )):
                            return None
                except Exception:
                    pass
                msg = str(exc).lower()
                # Provider outages / bad user input — ops issue, not a product bug
                if any(s in msg for s in (
                    "insufficient balance",
                    "no youtube channel found",
                    "could not extract channel",
                    "provider balance",
                    "gemini access denied",
                    "nicheanalysisunavailable",
                    "tts synthesis failed",
                    "atlas tts poll failed",
                    "atlas tts request failed",
                    "atlas tts generation failed",
                    "voiceover service is temporarily unavailable",
                )):
                    return None
            # Browser-extension / injected-script noise (e.g. nativeIframe redeclare)
            try:
                values = (event.get("exception") or {}).get("values") or []
                frames_blob = ""
                for v in values:
                    vtype = (v.get("type") or "")
                    vval = (v.get("value") or "")
                    if vtype == "SyntaxError" and "nativeiframe" in vval.lower():
                        return None
                    if "has already been declared" in vval.lower() and "nativeiframe" in vval.lower():
                        return None
                    # Wallet / random Chrome extensions injecting into the page
                    if "reading 'emit'" in vval.lower() or 'reading "emit"' in vval.lower():
                        return None
                    for frame in ((v.get("stacktrace") or {}).get("frames") or []):
                        frames_blob += " " + str(frame.get("filename") or "")
                        frames_blob += " " + str(frame.get("abs_path") or "")
                if "chrome-extension://" in frames_blob.lower():
                    return None
                # Also check request / breadcrumbs URL when present
                req_url = str(((event.get("request") or {}).get("url") or "")).lower()
                if "chrome-extension://" in req_url:
                    return None
            except Exception:
                pass
            return event

        sentry_sdk.init(
            dsn=config.SENTRY_DSN,
            integrations=[
                StarletteIntegration(transaction_style="endpoint"),
                FastApiIntegration(transaction_style="endpoint"),
            ],
            traces_sample_rate=0.15,
            send_default_pii=False,
            environment=os.getenv("APP_ENV", "production"),
            before_send=_sentry_before_send,
        )
        print("[telemetry] Sentry initialized (FastAPI)")
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


def identify_user(user_id: str | int, props: dict) -> None:
    """Update PostHog person properties (plan, credits, etc.)."""
    if not _posthog:
        return
    try:
        _posthog.identify(distinct_id=str(user_id), properties=props)
    except Exception as e:
        print(f"[telemetry] identify failed: {e}")


def capture_error(exc: Exception, context: dict | None = None) -> None:
    """Send an exception to Sentry with optional context tags."""
    try:
        import sentry_sdk
        with sentry_sdk.push_scope() as scope:
            if context:
                for k, v in context.items():
                    scope.set_extra(k, v)
            sentry_sdk.capture_exception(exc)
    except Exception:
        pass


app = FastAPI(title="ChannelRecipe", docs_url="/docs")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_jobs: dict[str, dict[str, Any]] = {}


@app.on_event("startup")
async def _startup_tasks():
    # Sync routes (voiceover/thumbnail/Gemini) run in this pool — keep it large
    # so a few 90s Atlas jobs don't starve the rest of the site.
    try:
        import concurrent.futures
        loop = asyncio.get_running_loop()
        loop.set_default_executor(
            concurrent.futures.ThreadPoolExecutor(
                max_workers=config.WEB_THREADPOOL_SIZE,
                thread_name_prefix="web-sync",
            )
        )
        print(f"[web] ThreadPoolExecutor max_workers={config.WEB_THREADPOOL_SIZE}")
    except Exception as e:
        print(f"[web] Could not enlarge threadpool: {e}")

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
    get_user_by_sub_id, get_user_by_customer_id, billing_plan_counts, list_billing_users,
    deduct_credit, deduct_credits, refund_credit, refund_credits, add_credits,
    create_verify_code, verify_code,
    create_session, get_session_user, delete_session,
    log_render_event, render_stats, backend_name, cleanup_expired,
    create_video, list_videos, get_video, update_video_kit, delete_video,
    create_cook_job, update_cook_job, get_cook_job,
    cook_queue_stats, announce_queued_jobs,
    set_user_heygen_key, get_user_heygen_key, user_heygen_status,
    set_user_atlas_key, get_user_atlas_key, user_atlas_status,
    create_voice_clone, list_voice_clones, count_voice_clones_since,
    list_unread_notices, mark_notice_read,
    upsert_niche_channels, list_niche_channels, count_niche_channels,
    create_niche_hunt_run, finish_niche_hunt_run, list_niche_hunt_runs,
    append_niche_hunt_progress, get_niche_hunt_run_by_job_id, get_latest_running_niche_hunt,
    cancel_niche_hunt_run, cancel_all_running_niche_hunts,
)
from webapp import storage
from webapp import job_queue
from webapp.cook_runner import run_cook_job, hydrate_job_from_row, job_credits_charged
from config import COOK_ON_WEB, COOK_ON_MODAL, COOK_ON_FLY


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
            identify_user(user["id"], {"email": email, "plan": user["plan"]})
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
    payload = {"user": _safe_user(user)}
    try:
        payload["notices"] = list_unread_notices(user.get("email") or "")
    except Exception:
        payload["notices"] = []
    return payload


@app.post("/api/notices/{notice_id}/ack")
async def ack_notice(notice_id: int, request: Request):
    user = require_user(request)
    ok = mark_notice_read(notice_id, user.get("email") or "")
    return {"ok": bool(ok)}


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


def _is_byok_email(email: str) -> bool:
    allow = getattr(config, "BYOK_EMAILS", []) or []
    return bool(allow) and (email or "").lower() in allow


def _is_pro(user: dict | None) -> bool:
    """Admins always get full Pro treatment (clean renders, full length, no credit drain)."""
    if not user:
        return False
    if _is_admin_email(user.get("email", "")):
        return True
    return user.get("plan") in ("pro", "starter", "daily", "starter_trial", "daily_trial")


def _is_trial(user: dict | None) -> bool:
    return bool(user) and user.get("plan") in ("starter_trial", "daily_trial")


# Trial (and free) users cannot generate scripts / cook videos longer than this.
TRIAL_MAX_MINUTES = 8


def _enforce_length_cap(user: dict, target_minutes: int | float, *, label: str = "Video") -> None:
    """Raise 402 if a trial/free user requests more than the allowed minutes."""
    if _is_admin_email(user.get("email", "")):
        return
    plan = user.get("plan") or "free"
    if plan in ("starter", "daily", "pro"):
        return
    cap = TRIAL_MAX_MINUTES
    if float(target_minutes or 0) > cap:
        raise HTTPException(
            402,
            f"{label} length is capped at {cap} minutes on trial. Start your plan for up to 20 min.",
        )


def _estimate_script_minutes(script: str) -> float:
    words = len((script or "").split())
    return round(words / 150, 2) if words else 0.0


def _safe_user(u: dict) -> dict:
    admins = getattr(config, "ADMIN_EMAILS", [])
    is_admin = bool(admins) and u.get("email", "").lower() in admins
    byok = _is_byok_email(u.get("email", ""))
    atlas = user_atlas_status(u["id"]) if byok else {"configured": False, "last4": ""}
    return {
        "id": u["id"],
        "email": u["email"],
        "plan": "pro" if is_admin else u["plan"],
        "credits": u["credits"],
        "created_at": u["created_at"],
        "is_admin": is_admin,
        "byok_enabled": byok,
        "atlas_connected": bool(atlas.get("configured")),
        "trial_used": bool(u.get("trial_used")),
        "trial_credits": int(getattr(config, "TRIAL_CREDITS", 2) or 2),
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


def _stripe_obj_to_dict(obj: Any) -> dict:
    """Normalize Stripe webhook objects to plain dicts."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        try:
            return obj.to_dict()
        except Exception:
            pass
    try:
        return json.loads(str(obj))
    except Exception:
        return {}


def _stripe_id(value: Any) -> str:
    """Stripe ids may arrive as strings or expanded objects."""
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("id") or "")
    return str(getattr(value, "id", "") or "")


def _invoice_subscription_id(invoice: dict) -> str:
    """Resolve subscription id across classic + Basil (2025-03-31+) invoice shapes.

    Basil removed top-level invoice.subscription in favor of
    invoice.parent.subscription_details.subscription. Without this fallback,
    invoice.paid silently skips trial conversions and renewals.
    """
    sub_id = _stripe_id(invoice.get("subscription"))
    if sub_id:
        return sub_id
    parent = invoice.get("parent") or {}
    if isinstance(parent, dict):
        details = parent.get("subscription_details") or {}
        if isinstance(details, dict):
            sub_id = _stripe_id(details.get("subscription"))
            if sub_id:
                return sub_id
    # Last-resort: some payloads still nest it under lines
    lines = (invoice.get("lines") or {}).get("data") or []
    for line in lines:
        if not isinstance(line, dict):
            continue
        sub_id = _stripe_id(line.get("subscription"))
        if sub_id:
            return sub_id
        parent = line.get("parent") or {}
        if isinstance(parent, dict):
            for key in ("subscription_item_details", "invoice_item_details"):
                details = parent.get(key) or {}
                if isinstance(details, dict):
                    sub_id = _stripe_id(details.get("subscription"))
                    if sub_id:
                        return sub_id
    return ""


def _find_user_for_stripe(*, sub_id: str = "", customer_id: str = "") -> dict | None:
    row = get_user_by_sub_id(sub_id) if sub_id else None
    if row:
        return row
    if customer_id:
        row = get_user_by_customer_id(customer_id)
        if row and sub_id and not row.get("stripe_sub_id"):
            # Heal race: checkout webhook lagged behind invoice.paid
            update_user(row["id"], stripe_sub_id=sub_id)
            row["stripe_sub_id"] = sub_id
        return row
    return None


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

    plan = user.get("plan", "free")
    if plan in ("starter_trial", "daily_trial"):
        raise HTTPException(400, "You already have an active trial. Use Billing → Start plan now to upgrade.")
    if plan in ("starter", "daily", "pro"):
        raise HTTPException(400, "You already have an active subscription. Manage it from Billing.")

    # One free trial per account — returning users pay immediately
    already_trialed = bool(user.get("trial_used"))

    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        customer = stripe.Customer.create(email=user["email"])
        customer_id = customer.id
        update_user(user["id"], stripe_customer_id=customer_id)

    base_url = str(request.base_url).rstrip("/")
    session_kwargs = {
        "customer": customer_id,
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "payment_method_collection": "always",
        "allow_promotion_codes": True,
        "success_url": f"{base_url}/app?welcome=1#pipeline",
        "cancel_url": f"{base_url}/app#pipeline",
        "metadata": {"user_id": str(user["id"]), "plan": req.plan},
    }
    if not already_trialed:
        session_kwargs["subscription_data"] = {"trial_period_days": 7}
        session_kwargs["success_url"] = f"{base_url}/app?welcome=trial#pipeline"
    else:
        session_kwargs["metadata"]["skip_trial"] = "1"
        session_kwargs["success_url"] = f"{base_url}/app?welcome=upgrade#pipeline"

    try:
        session = stripe.checkout.Session.create(**session_kwargs)
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
        success_url=f"{base_url}/app?topup=1#pipeline",
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

    # construct_event returns a StripeObject — convert once so .get() works.
    event_dict = _stripe_obj_to_dict(event)
    evt_type = event_dict.get("type") or ""
    data = event_dict.get("data") or {}
    if not isinstance(data, dict):
        data = _stripe_obj_to_dict(data)
    obj = _stripe_obj_to_dict(data.get("object"))
    print(f"[stripe] webhook {evt_type} id={event_dict.get('id', '')}")

    if evt_type == "checkout.session.completed":
        meta = obj.get("metadata") or {}
        user_id = meta.get("user_id")
        if not user_id:
            print(f"[stripe] checkout.session.completed missing user_id metadata: {obj.get('id')}")
        else:
            topup = meta.get("topup_credits")
            if topup:
                add_credits(int(user_id), int(topup))
                print(f"[stripe] User {user_id} topped up {topup} credits")
                track(user_id, "topup_completed", {"credits": int(topup)})
            else:
                plan_key = meta.get("plan", "starter_monthly")
                skip_trial = meta.get("skip_trial") == "1"
                sub_id = _stripe_id(obj.get("subscription"))
                if skip_trial:
                    # Returning customer — charge immediately, full credits
                    plan_label = "daily" if "daily" in plan_key else "starter"
                    credits = _PLAN_CREDITS.get(plan_key, 15)
                    update_user(int(user_id), plan=plan_label, credits=credits,
                                stripe_sub_id=sub_id,
                                trial_used=1)
                    print(f"[stripe] User {user_id} subscribed (no trial) → {plan_label} ({credits} credits)")
                    identify_user(user_id, {"plan": plan_label, "credits": credits, "trial_used": True})
                    track(user_id, "subscription_started", {
                        "plan": plan_label, "plan_key": plan_key, "credits": credits, "had_trial": False,
                    })
                else:
                    plan_label = "daily_trial" if "daily" in plan_key else "starter_trial"
                    trial_credits = int(getattr(config, "TRIAL_CREDITS", 2) or 2)
                    update_user(int(user_id), plan=plan_label, credits=trial_credits,
                                stripe_sub_id=sub_id,
                                trial_used=1)
                    print(f"[stripe] User {user_id} started trial ({plan_label}, {trial_credits} credits)")
                    identify_user(user_id, {"plan": plan_label, "credits": trial_credits, "trial_used": True})
                    track(user_id, "trial_started", {
                        "plan": plan_label, "plan_key": plan_key, "credits": trial_credits,
                    })

    elif evt_type == "invoice.paid":
        sub_id = _invoice_subscription_id(obj)
        customer_id = _stripe_id(obj.get("customer"))
        amount_paid = int(obj.get("amount_paid") or 0)
        if not sub_id:
            print(
                f"[stripe] invoice.paid missing subscription id "
                f"(invoice={obj.get('id')}, customer={customer_id}, amount={amount_paid})"
            )
        else:
            row = _find_user_for_stripe(sub_id=sub_id, customer_id=customer_id)
            if not row:
                print(
                    f"[stripe] invoice.paid no user for sub={sub_id} customer={customer_id} "
                    f"amount={amount_paid} invoice={obj.get('id')}"
                )
            else:
                plan = row.get("plan", "starter")
                if plan in ("starter_trial", "daily_trial"):
                    if amount_paid == 0:
                        print(f"[stripe] Skipping $0 trial invoice for user {row['id']} (trial credits already granted)")
                    else:
                        new_plan = "daily" if "daily" in plan else "starter"
                        credits = 35 if new_plan == "daily" else 15
                        update_user(row["id"], plan=new_plan, credits=credits, trial_used=1)
                        print(f"[stripe] Trial converted: user {row['id']} → {new_plan} ({credits} credits)")
                        identify_user(row["id"], {"plan": new_plan, "credits": credits})
                        track(row["id"], "trial_converted", {
                            "from_plan": plan, "to_plan": new_plan,
                            "credits": credits, "amount_paid": amount_paid,
                            "source": "invoice.paid",
                        })
                else:
                    credits = 35 if plan == "daily" else 15
                    update_user(row["id"], credits=credits)
                    print(f"[stripe] Refilled {credits} credits for user {row['id']} ({plan})")
                    track(row["id"], "credits_refilled", {"plan": plan, "credits": credits, "amount_paid": amount_paid})

    elif evt_type == "customer.subscription.deleted":
        sub_id = _stripe_id(obj.get("id"))
        customer_id = _stripe_id(obj.get("customer"))
        if sub_id:
            row = _find_user_for_stripe(sub_id=sub_id, customer_id=customer_id)
            if row:
                prev_plan = row.get("plan", "unknown")
                # Keep trial_used=1 so they cannot start another free trial
                update_user(row["id"], plan="free", credits=0, stripe_sub_id="")
                print(f"[stripe] Subscription deleted — user {row['id']} downgraded to free (trial_used preserved)")
                identify_user(row["id"], {"plan": "free", "credits": 0})
                track(row["id"], "subscription_canceled", {"from_plan": prev_plan})
            else:
                print(f"[stripe] subscription.deleted no user for sub={sub_id} customer={customer_id}")

    elif evt_type == "customer.subscription.updated":
        sub_id = _stripe_id(obj.get("id"))
        customer_id = _stripe_id(obj.get("customer"))
        status = obj.get("status")
        if sub_id and status in ("canceled", "unpaid"):
            row = _find_user_for_stripe(sub_id=sub_id, customer_id=customer_id)
            if row and row.get("plan") not in ("starter_trial", "daily_trial"):
                prev_plan = row.get("plan", "unknown")
                update_user(row["id"], plan="free", credits=0)
                print(f"[stripe] Subscription {status} — user {row['id']} downgraded to free")
                identify_user(row["id"], {"plan": "free", "credits": 0})
                track(row["id"], "subscription_canceled", {"from_plan": prev_plan, "status": status})
            elif row:
                print(f"[stripe] Ignoring {status} for trial user {row['id']} (handled by end-trial endpoint)")
            else:
                print(f"[stripe] subscription.updated({status}) no user for sub={sub_id}")
        elif sub_id and status == "active":
            # Safety net: if end-trial or day-7 conversion left plan as *_trial, fix it
            row = _find_user_for_stripe(sub_id=sub_id, customer_id=customer_id)
            if row and row.get("plan") in ("starter_trial", "daily_trial"):
                new_plan = "daily" if "daily" in row["plan"] else "starter"
                credits = 35 if new_plan == "daily" else 15
                update_user(row["id"], plan=new_plan, credits=credits, trial_used=1)
                print(f"[stripe] subscription.updated active — converted user {row['id']} → {new_plan}")
                identify_user(row["id"], {"plan": new_plan, "credits": credits})
                track(row["id"], "trial_converted", {
                    "from_plan": row["plan"], "to_plan": new_plan,
                    "credits": credits, "source": "subscription.updated",
                })

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


@app.get("/api/admin/billing")
async def admin_billing_health(sync: int = 0, admin: dict = Depends(require_admin)):
    """Admin billing snapshot: plan counts + optional Stripe subscription sync.

    ?sync=1 retrieves each Stripe subscription status and flags DB mismatches
    (e.g. Stripe active/paid while DB still on *_trial, or missing sub ids).
    """
    import stripe

    plans = billing_plan_counts()
    users = list_billing_users(limit=300)
    summary = {
        "plans": plans,
        "trialing_db": plans.get("starter_trial", 0) + plans.get("daily_trial", 0),
        "paid_db": plans.get("starter", 0) + plans.get("daily", 0) + plans.get("pro", 0),
        "free_db": plans.get("free", 0),
        "stripe_configured": bool(config.STRIPE_SECRET_KEY and config.STRIPE_WEBHOOK_SECRET),
        "price_ids": {
            "starter_monthly": bool(config.STRIPE_PRICE_STARTER_MONTHLY or config.STRIPE_PRICE_ID),
            "starter_annual": bool(config.STRIPE_PRICE_STARTER_ANNUAL or config.STRIPE_PRICE_ID_ANNUAL),
            "daily_monthly": bool(config.STRIPE_PRICE_DAILY_MONTHLY),
            "daily_annual": bool(config.STRIPE_PRICE_DAILY_ANNUAL),
            "topup_5": bool(config.STRIPE_PRICE_TOPUP_5),
            "topup_15": bool(config.STRIPE_PRICE_TOPUP_15),
        },
        "users": [
            {
                "id": u["id"],
                "email": u["email"],
                "plan": u.get("plan"),
                "credits": u.get("credits"),
                "trial_used": bool(u.get("trial_used")),
                "has_customer": bool(u.get("stripe_customer_id")),
                "has_sub": bool(u.get("stripe_sub_id")),
                "created_at": u.get("created_at"),
            }
            for u in users
        ],
        "mismatches": [],
    }

    if not sync:
        return summary
    if not config.STRIPE_SECRET_KEY:
        raise HTTPException(500, "Stripe not configured")

    stripe.api_key = config.STRIPE_SECRET_KEY
    mismatches = []
    healed = 0
    for u in users:
        sub_id = (u.get("stripe_sub_id") or "").strip()
        if not sub_id:
            if u.get("plan") in ("starter", "daily", "pro", "starter_trial", "daily_trial"):
                mismatches.append({
                    "email": u["email"], "plan": u.get("plan"),
                    "issue": "missing_stripe_sub_id",
                })
            continue
        try:
            sub = stripe.Subscription.retrieve(sub_id)
            status = sub.get("status") if isinstance(sub, dict) else getattr(sub, "status", None)
            db_plan = u.get("plan") or "free"
            if status == "active" and db_plan in ("starter_trial", "daily_trial"):
                new_plan = "daily" if "daily" in db_plan else "starter"
                credits = 35 if new_plan == "daily" else 15
                update_user(u["id"], plan=new_plan, credits=credits, trial_used=1)
                healed += 1
                mismatches.append({
                    "email": u["email"], "plan": db_plan, "stripe_status": status,
                    "issue": "trial_but_stripe_active", "healed_to": new_plan,
                })
            elif status in ("canceled", "unpaid", "incomplete_expired") and db_plan in ("starter", "daily", "pro"):
                mismatches.append({
                    "email": u["email"], "plan": db_plan, "stripe_status": status,
                    "issue": "paid_in_db_but_stripe_dead",
                })
            elif status == "trialing" and db_plan in ("starter", "daily"):
                mismatches.append({
                    "email": u["email"], "plan": db_plan, "stripe_status": status,
                    "issue": "paid_in_db_but_stripe_still_trialing",
                })
            elif status == "past_due":
                mismatches.append({
                    "email": u["email"], "plan": db_plan, "stripe_status": status,
                    "issue": "past_due",
                })
        except Exception as e:
            mismatches.append({
                "email": u["email"], "plan": u.get("plan"), "sub_id": sub_id,
                "issue": f"stripe_lookup_failed: {e}",
            })

    summary["mismatches"] = mismatches
    summary["healed"] = healed
    return summary


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
    """End the 7-day trial immediately, charge the card, grant full credits."""
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
        # End trial and create the first real invoice immediately
        import asyncio
        await asyncio.to_thread(stripe.Subscription.modify, sub_id, trial_end="now")
        print(f"[stripe] Trial end requested for user {user['id']} (sub {sub_id})")

        # Stripe may take a moment to charge — poll until active or failed
        sub_status = None
        for _ in range(8):
            sub = await asyncio.to_thread(stripe.Subscription.retrieve, sub_id)
            sub_status = sub.get("status") if isinstance(sub, dict) else getattr(sub, "status", None)
            if sub_status == "active":
                break
            if sub_status in ("past_due", "unpaid", "canceled", "incomplete_expired"):
                break
            await asyncio.sleep(0.75)

        print(f"[stripe] End-trial poll result: user {user['id']} status={sub_status}")

        if sub_status == "active":
            new_plan = "daily" if "daily" in user["plan"] else "starter"
            credits = 35 if new_plan == "daily" else 15
            update_user(user["id"], plan=new_plan, credits=credits, trial_used=1)
            print(f"[stripe] Converted user {user['id']} → {new_plan} ({credits} credits)")
            identify_user(user["id"], {"plan": new_plan, "credits": credits})
            track(user["id"], "trial_converted", {
                "from_plan": user["plan"], "to_plan": new_plan,
                "credits": credits, "source": "end_trial_early",
            })
            return {"ok": True, "plan": new_plan, "credits": credits}
        if sub_status in ("past_due", "unpaid", "incomplete", "incomplete_expired"):
            raise HTTPException(402, "Payment failed. Please update your card via Manage in Stripe, then try again.")
        if sub_status == "trialing":
            # Charge still processing — leave trial intact, ask user to wait
            raise HTTPException(503, "Payment is still processing. Wait a few seconds and try again.")
        raise HTTPException(500, f"Unexpected subscription status: {sub_status}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[stripe] End trial failed: {e}")
        raise HTTPException(500, f"Could not end trial: {e}")


# Atlas Cloud xAI TTS voices (real voice_ids + official sample URLs)
CURATED_VOICES = [
    {
        "id": "leo", "name": "Leo", "tag": "Narrator", "gender": "male",
        "desc": "Authoritative, instructional — best for documentaries",
        "preview_url": "https://data.x.ai/audio-samples/voice_leo.mp3",
        "default": True,
    },
    {
        "id": "rex", "name": "Rex", "tag": "Professional", "gender": "male",
        "desc": "Polished business tone — great for explainers",
        "preview_url": "https://data.x.ai/audio-samples/voice_rex.mp3",
    },
    {
        "id": "sal", "name": "Sal", "tag": "Neutral", "gender": "male",
        "desc": "Versatile, clear delivery that fits most niches",
        "preview_url": "https://data.x.ai/audio-samples/voice_sal.mp3",
    },
    {
        "id": "78a495fdbb39", "name": "James", "tag": "Engaging", "gender": "male",
        "desc": "Young, energetic English narrator — ideal for listicles",
        "preview_url": "https://static.atlascloud.ai/media/audios/47_James_78a495fdbb39.mp3",
    },
    {
        "id": "96819d0bd28d", "name": "Daniel", "tag": "Mature", "gender": "male",
        "desc": "Seasoned English voice with natural warmth",
        "preview_url": "https://static.atlascloud.ai/media/audios/42_Daniel_96819d0bd28d.mp3",
    },
    {
        "id": "ara", "name": "Ara", "tag": "Warm", "gender": "female",
        "desc": "Warm and conversational — great for storytelling",
        "preview_url": "https://data.x.ai/audio-samples/voice_ara.mp3",
    },
    {
        "id": "eve", "name": "Eve", "tag": "Upbeat", "gender": "female",
        "desc": "Energetic and upbeat — strong for viral formats",
        "preview_url": "https://data.x.ai/audio-samples/voice_eve.mp3",
    },
    {
        "id": "f8cf5c2c78d4", "name": "Grace", "tag": "Clear", "gender": "female",
        "desc": "Young, clear English voice — approachable and bright",
        "preview_url": "https://static.atlascloud.ai/media/audios/29_Grace_f8cf5c2c78d4.mp3",
    },
    {
        "id": "79f3a8b96d43", "name": "Claire", "tag": "Steady", "gender": "female",
        "desc": "Calm, middle-aged English narrator — steady pacing",
        "preview_url": "https://static.atlascloud.ai/media/audios/46_Claire_79f3a8b96d43.mp3",
    },
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
    voice: str = "leo"

class VoicePreviewRequest(BaseModel):
    voice: str
    text: str = "Welcome to this episode. Today we uncover one of history's greatest untold stories."

class ThumbnailRequest(BaseModel):
    title: str
    niche_style: str = ""
    count: int = 2

class BuildRequest(BaseModel):
    script: str
    voiceover_path: str = ""
    title: str = ""
    niche: str = "animated_explainer"
    recipe: str = "animated_explainer"
    thumbnail_path: str = ""
    notify_email: str = ""
    avatar_id: str = ""
    voice_id: str = ""
    # standard (1 credit) | high (HQ_CREDIT_COST, paid plans only, max HQ_MAX_MINUTES)
    image_quality: str = "standard"


class HeyGenKeyRequest(BaseModel):
    api_key: str = ""
    test: bool = True

class UploadKitRequest(BaseModel):
    title: str
    script: str
    niche: str = ""

class ChannelFetchRequest(BaseModel):
    channel_url: str
    max_videos: int = 20

class ChannelBatchItem(BaseModel):
    channel_url: str
    max_videos: int = 20

class ChannelBatchFetchRequest(BaseModel):
    channels: list[ChannelBatchItem]

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
    voice: str = "leo"
    style_preset: str = "Narrator"
    custom_notes: str = ""

class NicheAnalyzeRequest(BaseModel):
    youtube_url: str
    minutes: int = 5

class NicheIntelJobRequest(BaseModel):
    niche: str = "niche"
    channels: list[str] = []
    videos_per_channel: int = 10
    frames_per_video: int = 8


class StoryboardCastMember(BaseModel):
    id: str = ""
    name: str = ""
    included: bool = True
    look_prompt: str = ""
    portrait_url: str = ""
    sheet_url: str = ""
    portrait_path: str = ""
    sheet_path: str = ""


class StoryboardJobRequest(BaseModel):
    title: str = ""
    topic: str = ""  # legacy alias for story
    story: str = ""
    moral: str = ""  # optional takeaway
    cast: list[StoryboardCastMember] = []
    mistake_by: str = ""  # legacy / unused
    dialogue_mode: str = "generate"  # generate | paste
    script: str = ""
    target_minutes: float = 8
    thumbnail_path: str = ""
    pack_mode: str = "full"  # preview = first ~1 min · full = full length
    visual_style: str = ""
    template: str = ""  # e.g. easy_english_family | ""


class StoryboardSuggestMoralsRequest(BaseModel):
    story: str = ""
    template: str = ""


class StoryboardCastSaveRequest(BaseModel):
    cast: list[StoryboardCastMember] = []
    visual_style: str = ""
    template: str = ""


class StoryboardLookRequest(BaseModel):
    id: str = ""
    name: str = ""
    look_prompt: str = ""
    make_sheet: bool = True
    visual_style: str = ""


class StoryboardExtractCastRequest(BaseModel):
    story: str = ""
    script: str = ""
    visual_style: str = ""


class StoryboardRegenBeatRequest(BaseModel):
    index: int
    note: str = ""  # optional direction: "make them look sadder", etc.
    visual_style: str = ""

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
    """Liveness probe — no DB, no secrets. Safe for DigitalOcean health checks."""
    return {"status": "ok"}


@app.get("/robots.txt")
async def robots_txt():
    return PlainTextResponse("User-agent: *\nAllow: /\nDisallow: /api/\n")


@app.get("/api/config")
async def get_client_config():
    """Public front-end config: analytics keys + feature flags (safe to expose)."""
    from core.fish_clone import clone_enabled
    return {
        "posthog_key": config.POSTHOG_KEY,
        "posthog_host": config.POSTHOG_HOST,
        "sentry_dsn": config.SENTRY_DSN,
        "voice_clone_enabled": clone_enabled(),
        "voice_clone_credit_cost": int(getattr(config, "VOICE_CLONE_CREDIT_COST", 1) or 0),
        "recipe_brain_enabled": bool(getattr(config, "RECIPE_BRAIN_ENABLED", False)),
        "max_voiceover_minutes": int(getattr(config, "MAX_VOICEOVER_MINUTES", 25) or 25),
        "max_voiceover_words": int(getattr(config, "MAX_VOICEOVER_WORDS", 3750) or 3750),
        "hq_credit_cost": int(getattr(config, "HQ_CREDIT_COST", 3) or 3),
        "hq_max_minutes": int(getattr(config, "HQ_MAX_MINUTES", 12) or 12),
    }


@app.get("/api/niches")
async def get_niches(request: Request):
    niches = []
    user = _current_user(request)
    is_admin = bool(user and _is_admin_email(user.get("email", "")))
    for f in sorted(NICHES_DIR.glob("*.json")):
        with open(f) as fh:
            niche = json.load(fh)
        # Storyboard Pack: admin-only while testing (everyone else sees Coming soon)
        if niche.get("id") == "storyboard_pack" or niche.get("recipe") == "storyboard_pack":
            if is_admin:
                niche["status"] = niche.get("status") or "new"
                niche["available"] = True
            else:
                niche["status"] = "coming_soon"
                niche["available"] = False
        niches.append(niche)
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
# Gemini helpers (titles / scripts)
# ---------------------------------------------------------------------------
def _extract_gemini_text(resp) -> tuple[str, str]:
    """Return (visible_text, finish_reason). Skips thought-only parts."""
    text = (getattr(resp, "text", None) or "").strip()
    finish = ""
    try:
        cands = list(getattr(resp, "candidates", None) or [])
        if not cands:
            pf = getattr(resp, "prompt_feedback", None)
            br = getattr(pf, "block_reason", None) if pf else None
            return "", f"blocked:{br}" if br else "no_candidates"
        cand = cands[0]
        finish = str(getattr(cand, "finish_reason", "") or "")
        if text:
            return text, finish
        content = getattr(cand, "content", None)
        parts = list(getattr(content, "parts", None) or [])
        chunks: list[str] = []
        for p in parts:
            if getattr(p, "thought", None):
                continue
            t = getattr(p, "text", None) or ""
            if t:
                chunks.append(t)
        return "".join(chunks).strip(), finish
    except Exception:
        return text, finish or "extract_error"


def _gemini_generate_text(
    client,
    prompt: str,
    *,
    max_output_tokens: int = 8192,
    retries: int = 2,
    label: str = "Generation",
) -> str:
    """Text generation via Atlas (preferred) or Google Gemini; retry empty replies."""
    from core.atlas_llm import generate_text, has_atlas

    last_err = ""
    for attempt in range(retries + 1):
        tokens = max_output_tokens if attempt == 0 else max(max_output_tokens, 16384)
        try:
            text = generate_text(prompt, max_tokens=tokens).strip()
            if text:
                return text
            last_err = "empty"
        except Exception as e:
            last_err = str(e)
            # Legacy path only if Atlas is not configured
            if not has_atlas() and client is not None:
                try:
                    from google.genai import types
                    resp = client.models.generate_content(
                        model=config.GEMINI_TEXT_MODEL,
                        contents=[{"role": "user", "parts": [{"text": prompt}]}],
                        config=types.GenerateContentConfig(max_output_tokens=tokens),
                    )
                    text, finish = _extract_gemini_text(resp)
                    last_err = finish or last_err
                    if text:
                        return text
                except Exception as e2:
                    last_err = str(e2)
        time.sleep(0.35 * (attempt + 1))

    raise HTTPException(
        503,
        f"{label} returned empty text ({last_err or 'unknown'}). Try again in a moment.",
    )


# ---------------------------------------------------------------------------
# Titles (Gemini-based)
# ---------------------------------------------------------------------------
@app.post("/api/titles")
def generate_titles(req: TitleRequest, user: dict = Depends(require_user)):
    from core.atlas_llm import has_atlas

    if not config.GEMINI_KEY and not has_atlas():
        raise HTTPException(500, "ATLASCLOUD_KEY or GEMINI_KEY not configured on backend")

    niche_data = _load_niche(req.niche)
    niche_name = niche_data.get("name", req.niche) if niche_data else req.niche
    recipe = (niche_data or {}).get("recipe") or req.niche or ""
    topic_hint = f"\nTopic hint from user: {req.topic}" if req.topic else ""

    # Storyboard Pack: ground titles on Easy English family-channel style bible
    if recipe == "storyboard_pack" or req.niche == "storyboard_pack":
        try:
            from core.storyboard_pack import generate_story_ideas
            ideas = generate_story_ideas(seed=req.topic or "", count=6)
            titles = [i["title"] for i in ideas if i.get("title")]
            if titles:
                return {"titles": titles[:6]}
        except Exception as e:
            print(f"[titles] storyboard style titles failed, falling back: {e}")

    prompt = (
        f"Generate exactly 3 viral YouTube video titles for the '{niche_name}' niche. "
        f"These should be compelling, curiosity-driven titles that get clicks. "
        f"Each title should be a different angle on a fascinating topic. "
        f"Return ONLY a JSON array of 3 strings, nothing else.{topic_hint}"
    )

    try:
        raw = _gemini_generate_text(
            None, prompt, max_output_tokens=2048, label="Title generation"
        )
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        titles = json.loads(raw)
        if not isinstance(titles, list) or len(titles) < 1:
            raise ValueError("Expected list of titles")
        return {"titles": titles[:3]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Title generation failed: {e}")


# ---------------------------------------------------------------------------
# Script (Gemini-based)
# ---------------------------------------------------------------------------
@app.post("/api/script")
def generate_script(req: ScriptRequest, user: dict = Depends(require_user)):
    from core.atlas_llm import has_atlas

    _enforce_length_cap(user, req.target_minutes, label="Script")

    if not config.GEMINI_KEY and not has_atlas():
        raise HTTPException(500, "ATLASCLOUD_KEY or GEMINI_KEY not configured on backend")

    niche_data = _load_niche(req.niche)
    style_hint = ""
    if niche_data:
        style_hint = f"\nVideo style: {niche_data.get('description', '')}"

    word_target = req.target_minutes * 150
    # Keep scripts fast enough for App Platform HTTP timeouts (~100s).
    max_tokens = max(2048, min(12288, int(word_target * 1.8) + 1024))

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
        script = _gemini_generate_text(
            None,
            prompt,
            max_output_tokens=max_tokens,
            label="Script generation",
        )
        return {"script": script, "word_count": len(script.split())}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Script generation failed: {e}")


# ---------------------------------------------------------------------------
# Voiceover
# ---------------------------------------------------------------------------
def _provider_http_status(exc: Exception) -> int:
    """Map known provider/user errors to non-500 statuses (keeps Sentry quieter)."""
    msg = str(exc).lower()
    if "too many voiceovers" in msg:
        return 429
    if "insufficient balance" in msg or "provider balance" in msg or "temporarily unavailable" in msg:
        return 503
    if "tts synthesis failed" in msg or "atlas tts" in msg or "internal error" in msg:
        return 503
    if "image_other" in msg or "no parts found" in msg or "no_image" in msg:
        return 503
    if "thumbnail generation failed after retries" in msg:
        return 503
    if "output path missing" in msg:
        return 503
    if "not found" in msg or "no youtube channel" in msg or "could not extract channel" in msg:
        return 400
    if "playlistnotfound" in msg or "httperror 404" in msg:
        return 400
    if "script is empty" in msg or "nothing to narrate" in msg:
        return 400
    if "not configured" in msg:
        return 503
    return 500


def _stage_user_media(local_path: str, user_id: int, kind: str, content_type: str) -> tuple[str, str]:
    """
    Return (path_for_cook, url_for_browser).
    When Spaces is configured, path_for_cook is a public HTTPS URL so workers
    can fetch it; otherwise both refer to local /api/files/... .
    """
    ts = int(time.time())
    ext = Path(local_path).suffix or ".bin"
    key = f"inputs/{user_id}/{ts}_{kind}{ext}"
    staged = storage.stage_input(local_path, key, content_type=content_type)
    if staged.startswith("http://") or staged.startswith("https://"):
        return staged, staged
    rel = os.path.relpath(local_path, str(ROOT))
    url = f"/api/files/{rel}"
    return local_path, url


def _unique_media_dir(*parts: str) -> Path:
    """Timestamp + uuid so concurrent requests never share a folder."""
    return OUTPUT_DIR.joinpath(*parts, f"{int(time.time())}_{uuid.uuid4().hex[:10]}")


@app.post("/api/voiceover")
def generate_voiceover(req: VoiceoverRequest, user: dict = Depends(require_user)):
    from core.atlas_runtime import use_atlas_key
    from core.voiceover_gen import generate_voiceover as gen_vo

    byok = _is_byok_email(user.get("email", ""))
    user_atlas = get_user_atlas_key(user["id"]) if byok else None
    if byok and not user_atlas:
        raise HTTPException(
            400,
            "Add your Atlas API key in Settings → Integrations before generating voiceovers.",
        )

    out_dir = str(_unique_media_dir("voiceovers"))
    try:
        with use_atlas_key(user_atlas):
            wav_path = gen_vo(script=req.script, voice=req.voice, style_preset="Narrator", output_dir=out_dir)
        path, url = _stage_user_media(wav_path, user["id"], "voiceover", "audio/wav")
        return {"path": path, "url": url}
    except Exception as e:
        raise HTTPException(_provider_http_status(e), f"Voiceover generation failed: {e}")


@app.post("/api/voiceover/upload")
async def upload_voiceover(file: UploadFile = File(...), user: dict = Depends(require_user)):
    """Accept a user-uploaded voiceover file (WAV, MP3, M4A) and return its path."""
    import subprocess

    allowed = {".wav", ".mp3", ".m4a", ".ogg", ".webm"}
    ext = Path(file.filename or "audio.wav").suffix.lower()
    if ext not in allowed:
        raise HTTPException(400, f"Unsupported format '{ext}'. Use WAV, MP3, or M4A.")

    content = await file.read()

    def _convert() -> str:
        out_dir = _unique_media_dir("voiceovers")
        out_dir.mkdir(parents=True, exist_ok=True)
        raw_path = out_dir / f"upload_raw{ext}"
        with open(raw_path, "wb") as f:
            f.write(content)
        wav_path = out_dir / "voiceover.wav"
        if ext == ".wav":
            shutil.copy(str(raw_path), str(wav_path))
        else:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(raw_path), "-ar", "24000", "-ac", "1", str(wav_path)],
                capture_output=True, check=True, timeout=60,
            )
        return str(wav_path)

    try:
        wav_path = await asyncio.to_thread(_convert)
    except Exception as e:
        raise HTTPException(500, f"Audio conversion failed: {e}")

    path, url = _stage_user_media(wav_path, user["id"], "voiceover", "audio/wav")
    return {"path": path, "url": url}


@app.post("/api/voiceover/preview")
def voice_preview(req: VoicePreviewRequest, user: dict = Depends(require_user)):
    """Return a quick preview — prefer Atlas official sample URLs when available."""
    for v in CURATED_VOICES:
        if v["id"] == req.voice and v.get("preview_url"):
            return {"url": v["preview_url"], "cached": True}

    from core.voiceover_gen import generate_voiceover as gen_vo

    out_dir = str(OUTPUT_DIR / "voice_previews")
    safe_name = "".join(c if c.isalnum() else "_" for c in req.voice.lower())[:40]
    cache_path = Path(out_dir) / f"{safe_name}_preview.wav"

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


@app.get("/api/voice/clones")
def get_voice_clones(user: dict = Depends(require_user)):
    from core.fish_clone import clone_enabled
    if not clone_enabled():
        return {"enabled": False, "clones": [], "credit_cost": 0}
    clones = list_voice_clones(user["id"])
    return {
        "enabled": True,
        "credit_cost": int(getattr(config, "VOICE_CLONE_CREDIT_COST", 1) or 0),
        "clones": [
            {
                "id": c["id"],
                "voice_id": f"fish:{c['fish_model_id']}",
                "name": c["title"],
                "source": c["source"],
                "consent_at": c["consent_at"],
                "created_at": c["created_at"],
            }
            for c in clones
        ],
    }


@app.post("/api/voice/clone")
async def create_fish_voice_clone(
    request: Request,
    user: dict = Depends(require_user),
    file: UploadFile | None = File(None),
    youtube_url: str = Form(""),
    title: str = Form("My voice"),
    consent: str = Form(""),
):
    """
    Rights-gated Fish clone from upload or YouTube URL.
    Requires consent checkbox; refuses without it.
    """
    from core.fish_clone import (
        clone_enabled, create_voice_model, extract_youtube_audio, normalize_sample,
    )

    if not clone_enabled():
        raise HTTPException(503, "Voice clone is not enabled yet.")

    consent_ok = str(consent or "").strip().lower() in ("1", "true", "yes", "on")
    if not consent_ok:
        raise HTTPException(
            400,
            "Consent required: confirm you own this voice or have written permission.",
        )

    # Abuse rate limit: 3 clones / rolling 24h
    since = time.time() - 86400
    if count_voice_clones_since(user["id"], since) >= 3:
        raise HTTPException(429, "Clone limit reached (3 per day). Try again tomorrow.")

    cost = int(getattr(config, "VOICE_CLONE_CREDIT_COST", 1) or 0)
    charged = False
    if cost > 0:
        if not deduct_credits(user["id"], cost):
            raise HTTPException(402, f"Need {cost} credit(s) to create a voice clone.")
        charged = True

    tmp_dir = OUTPUT_DIR / "voice_clones" / str(user["id"]) / str(int(time.time()))
    tmp_dir.mkdir(parents=True, exist_ok=True)
    source = "upload"
    try:
        yt = (youtube_url or "").strip()
        has_file = file is not None and bool(file.filename)
        # Upload wins when both are present — people paste a YouTube link, it
        # gets bot-blocked, then drag in a screen recording without clearing the URL.
        if has_file:
            raw = await file.read()
            if len(raw) < 1000:
                raise HTTPException(400, "File is too small.")
            ext = Path(file.filename or "sample.wav").suffix.lower() or ".wav"
            video_exts = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
            audio_exts = {".wav", ".mp3", ".m4a", ".ogg", ".aac", ".flac"}
            # .webm can be audio or video — allow either
            allowed = video_exts | audio_exts | {".webm"}
            if ext not in allowed:
                raise HTTPException(
                    400,
                    "Use a screen recording (MP4/MOV/WebM) or audio (WAV/MP3/M4A).",
                )
            # Screen recordings are larger; audio stays tighter.
            max_bytes = 100 * 1024 * 1024 if ext in video_exts else 40 * 1024 * 1024
            if len(raw) > max_bytes:
                mb = max_bytes // (1024 * 1024)
                raise HTTPException(400, f"File too large (max {mb}MB). Trim the recording and retry.")
            raw_path = tmp_dir / f"upload{ext}"
            raw_path.write_bytes(raw)
            sample_path = await asyncio.to_thread(normalize_sample, str(raw_path), str(tmp_dir))
            source = "screen_recording" if ext in video_exts else "upload"
        elif yt:
            source = "youtube"
            sample_path = await asyncio.to_thread(extract_youtube_audio, yt, str(tmp_dir))
        else:
            raise HTTPException(
                400,
                "Paste a YouTube URL, or upload a screen recording / audio sample.",
            )

        result = await asyncio.to_thread(
            create_voice_model,
            sample_path,
            title=(title or "My voice").strip()[:80] or "My voice",
            description="Rights-gated ChannelRecipe clone",
        )
        row = create_voice_clone(
            user["id"],
            fish_model_id=result["fish_model_id"],
            title=(title or "My voice").strip()[:80] or "My voice",
            source=source,
            consent_at=time.time(),
        )
        refreshed = get_user_by_id(user["id"])
        return {
            "ok": True,
            "voice_id": f"fish:{row['fish_model_id']}",
            "title": row["title"],
            "source": source,
            "consent_at": row["consent_at"],
            "credits_remaining": refreshed.get("credits") if refreshed else None,
            "credit_cost": cost,
        }
    except HTTPException:
        if charged:
            add_credits(user["id"], cost)
        raise
    except ValueError as e:
        if charged:
            add_credits(user["id"], cost)
        raise HTTPException(400, str(e))
    except Exception as e:
        if charged:
            add_credits(user["id"], cost)
        raise HTTPException(500, f"Voice clone failed: {e}")


@app.post("/api/voiceover/studio")
def voiceover_studio(req: VoiceoverStudioRequest, user: dict = Depends(require_user)):
    from core.atlas_runtime import use_atlas_key
    from core.voiceover_gen import generate_voiceover as gen_vo

    if not (req.script or "").strip():
        raise HTTPException(400, "Paste a script first, then generate the voiceover.")

    byok = _is_byok_email(user.get("email", ""))
    user_atlas = get_user_atlas_key(user["id"]) if byok else None
    if byok and not user_atlas:
        raise HTTPException(
            400,
            "Add your Atlas API key in Settings → Integrations before generating voiceovers.",
        )

    out_dir = str(_unique_media_dir("voiceovers"))
    try:
        with use_atlas_key(user_atlas):
            wav_path = gen_vo(
                script=req.script,
                voice=req.voice,
                style_preset=req.style_preset,
                custom_notes=req.custom_notes,
                output_dir=out_dir,
            )
        path, url = _stage_user_media(wav_path, user["id"], "voiceover", "audio/wav")
        return {"path": path, "url": url}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(_provider_http_status(e), f"Voiceover generation failed: {e}")


# ---------------------------------------------------------------------------
# Thumbnails
# ---------------------------------------------------------------------------
@app.post("/api/thumbnail/upload")
async def upload_thumbnail(file: UploadFile = File(...), user: dict = Depends(require_user)):
    """Accept a finished thumbnail the user already has — no AI generation."""
    allowed = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    ext = Path(file.filename or "thumb.png").suffix.lower()
    if ext not in allowed:
        raise HTTPException(400, "Use PNG, JPG, or WEBP.")
    content = await file.read()
    if len(content) < 500:
        raise HTTPException(400, "Image file is too small.")
    if len(content) > 12 * 1024 * 1024:
        raise HTTPException(400, "Image too large (max 12MB).")
    out_dir = OUTPUT_DIR / "thumbnails" / str(user["id"]) / str(int(time.time()))
    out_dir.mkdir(parents=True, exist_ok=True)
    local = out_dir / f"upload{ext}"
    local.write_bytes(content)
    path, url = _stage_user_media(str(local), user["id"], "thumb_upload", "image/png" if ext == ".png" else "image/jpeg")
    return {"path": path, "url": url}


@app.post("/api/thumbnail")
def generate_thumbnail(req: ThumbnailRequest, user: dict = Depends(require_user)):
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
            raise ValueError("No thumbnails generated — try again in a moment")
        staged_paths = []
        urls = []
        for i, p in enumerate(paths[:req.count]):
            sp, su = _stage_user_media(p, user["id"], f"thumb_{i}", "image/png")
            staged_paths.append(sp)
            urls.append(su)
        return {"thumbnails": urls, "paths": staged_paths}
    except Exception as e:
        raise HTTPException(_provider_http_status(e), f"Thumbnail generation failed: {e}")


@app.post("/api/thumbnail/with-refs")
async def generate_thumbnail_with_refs(
    title: str = Form(...),
    style: str = Form(""),
    count: int = Form(2),
    refs: list[UploadFile] = File(default=[]),
    user: dict = Depends(require_user),
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

    def _run():
        return generate_thumbnails(
            title=title,
            reference_image_paths=ref_paths,
            style_prompt=style,
            num_images=count,
            output_dir=out_dir,
        )

    try:
        paths = await asyncio.to_thread(_run)
        if not paths:
            raise ValueError("No thumbnails generated — try again in a moment")
        staged_paths = []
        urls = []
        for i, p in enumerate(paths):
            sp, su = _stage_user_media(p, user["id"], f"thumbref_{i}", "image/png")
            staged_paths.append(sp)
            urls.append(su)
        return {"thumbnails": urls, "paths": staged_paths}
    except Exception as e:
        raise HTTPException(_provider_http_status(e), f"Thumbnail generation failed: {e}")


# ---------------------------------------------------------------------------
# Build (recipe-aware + SSE progress)
# ---------------------------------------------------------------------------
def _safe_user_path(path_str: str, label: str) -> None:
    """Validate that a user-supplied path is local under OUTPUT_DIR or a remote HTTPS URL."""
    if not path_str:
        return
    if path_str.startswith("https://") or path_str.startswith("http://"):
        # Spaces / CDN inputs for worker cooks
        return
    resolved = Path(path_str).resolve()
    if not resolved.is_relative_to(OUTPUT_DIR.resolve()):
        raise HTTPException(400, f"Invalid {label} path")
    if not resolved.is_file():
        raise HTTPException(400, f"{label} file not found")


def _queue_info_for(job_id: str) -> dict:
    """In-process queue info when cooking on web; DB stats when workers own cooks."""
    if COOK_ON_WEB:
        return job_queue.queue_info(job_id)
    try:
        return cook_queue_stats(job_id)
    except Exception:
        return {"status": "queued", "queue_position": 1, "queue_length": 1,
                "running_count": 0, "est_wait_minutes": 0}


def _refresh_job_from_db(job_id: str, job: dict) -> None:
    """Pull latest status/progress from DB into the in-memory view (worker mode)."""
    row = get_cook_job(job_id)
    if not row:
        return
    try:
        progress = json.loads(row.get("progress_json") or "[]")
    except Exception:
        progress = []
    if isinstance(progress, list):
        job["progress"] = progress
    job["status"] = row.get("status") or job.get("status")
    job["error"] = row.get("error") or job.get("error") or ""
    if row.get("result_json"):
        try:
            job["result"] = json.loads(row["result_json"])
        except Exception:
            pass


def _execute_cook_job(job_id: str) -> None:
    """In-process queue runner (COOK_ON_WEB=1)."""
    job = _jobs.get(job_id)
    if not job or job.get("status") == "cancelled":
        return
    try:
        update_cook_job(job_id, status="running", started=True, heartbeat=True)
    except Exception:
        pass

    def cancel_check() -> bool:
        if job.get("status") == "cancelled":
            return True
        row = get_cook_job(job_id)
        return bool(row and row.get("status") == "cancelled")

    run_cook_job(
        job_id,
        job,
        track=track,
        capture_error=capture_error,
        cancel_check=cancel_check,
    )


if COOK_ON_WEB:
    job_queue.configure(_jobs, _execute_cook_job)
    print("[build] COOK_ON_WEB=1 — cooks run in this process")
elif COOK_ON_FLY:
    print("[build] COOK_ON_FLY=1 — cooks spawn on Fly Machines (ephemeral)")
elif COOK_ON_MODAL:
    print("[build] COOK_ON_MODAL=1 — cooks spawn on Modal (scale-to-zero)")
else:
    print("[build] COOK_ON_WEB=0 — enqueue only; start `python -m webapp.worker`")


@app.post("/api/build")
async def start_build(req: BuildRequest, request: Request):
    user = _current_user(request)
    if not user:
        raise HTTPException(401, "Sign in to continue.")
    user_id = user["id"]

    is_admin = _is_admin_email(user.get("email", ""))
    is_byok = _is_byok_email(user.get("email", ""))
    byok_atlas = get_user_atlas_key(user_id) if is_byok else None
    if is_byok and not byok_atlas:
        raise HTTPException(
            400,
            "Add your Atlas API key in Settings → Integrations before cooking. "
            "Your cooks bill your Atlas account — not ChannelRecipe's.",
        )

    if not is_admin and user.get("plan") not in ("starter", "daily", "pro", "starter_trial", "daily_trial"):
        raise HTTPException(402, "Start your free trial to cook this video.")

    # Trial/free: hard-cap finished video length (~150 wpm)
    if not is_admin:
        _enforce_length_cap(user, _estimate_script_minutes(req.script), label="Video")

    recipe = req.recipe or "animated_explainer"
    if recipe == "avatar_plus_broll":
        if not (req.avatar_id or "").strip() or not (req.voice_id or "").strip():
            raise HTTPException(400, "Pick a HeyGen avatar and voice (or paste their IDs) before cooking.")
        if not get_user_heygen_key(user_id):
            raise HTTPException(
                400,
                "Connect your HeyGen API key in Settings → Integrations before cooking an avatar video.",
            )
    else:
        _safe_user_path(req.voiceover_path, "voiceover")
        if not COOK_ON_WEB and req.voiceover_path and not (
            req.voiceover_path.startswith("http://") or req.voiceover_path.startswith("https://")
        ):
            if not storage.is_remote():
                raise HTTPException(
                    503,
                    "Worker mode requires Spaces for voiceovers. Configure SPACES_* env vars, "
                    "or set COOK_ON_WEB=1 until Spaces is ready.",
                )

    _safe_user_path(req.thumbnail_path, "thumbnail")

    image_quality = (req.image_quality or "standard").strip().lower()
    if image_quality in ("high", "hq", "pro"):
        image_quality = "high"
    else:
        image_quality = "standard"

    hq_cost = int(getattr(config, "HQ_CREDIT_COST", 3) or 3)
    hq_max = int(getattr(config, "HQ_MAX_MINUTES", 12) or 12)
    credit_cost = hq_cost if image_quality == "high" else 1

    if image_quality == "high":
        if not is_admin and not is_byok and user.get("plan") in ("starter_trial", "daily_trial", "free"):
            raise HTTPException(
                402,
                "High quality is for paid plans. Upgrade to unlock GPT Image 2 renders.",
            )
        est_min = _estimate_script_minutes(req.script)
        if not is_admin and est_min > hq_max + 0.5:
            raise HTTPException(
                400,
                f"High quality caps at {hq_max} minutes. Shorten the script, "
                "or cook part 2 separately and stitch.",
            )

    # BYOK customers with Atlas connected: no ChannelRecipe credit drain.
    credit_deducted = 0
    if not is_admin and not (is_byok and byok_atlas):
        if not deduct_credits(user_id, credit_cost):
            have = int(user.get("credits") or 0)
            raise HTTPException(
                402,
                f"Need {credit_cost} credit{'s' if credit_cost != 1 else ''}. "
                f"You have {have}. Top up to cook this video.",
            )
        credit_deducted = credit_cost

    job_id = str(uuid.uuid4())
    lite_mode = (not is_admin) and user.get("plan") in ("starter_trial", "daily_trial", "free")
    req_payload = req.model_dump()
    req_payload["image_quality"] = image_quality
    req_payload["credits_charged"] = credit_deducted
    _jobs[job_id] = {
        "status": "queued",
        "progress": [],
        "result": None,
        "request": req_payload,
        "user_id": user_id,
        "credit_deducted": credit_deducted,
        "lite_mode": lite_mode,
        "queue_position": 0,
        "est_wait_minutes": 0,
        "created_at": time.time(),
    }

    try:
        create_cook_job(
            job_id=job_id,
            user_id=user_id,
            recipe=req.recipe or "animated_explainer",
            title=req.title or "",
            request_json=json.dumps(req_payload),
            credit_deducted=bool(credit_deducted),
            lite_mode=lite_mode,
            # Workers only claim status=queued. web_queued = in-process only.
            status="queued" if not COOK_ON_WEB else "web_queued",
        )
    except Exception as e:
        print(f"[build] create_cook_job failed: {e}")
        if credit_deducted:
            add_credits(user_id, credit_deducted)
        raise HTTPException(500, "Could not queue your cook. Please try again.")

    if COOK_ON_WEB:
        qinfo = job_queue.enqueue(job_id)
    else:
        try:
            announce_queued_jobs()
        except Exception:
            pass
        if COOK_ON_FLY:
            try:
                from webapp.fly_bridge import spawn_cook as fly_spawn
                if fly_spawn(job_id):
                    _jobs[job_id]["progress"].append({
                        "time": time.time(),
                        "message": "Starting cook (Fly elastic worker)...",
                        "phase": "queued",
                    })
                else:
                    print(f"[build] Fly spawn failed for {job_id} — left in queue for DO worker")
            except Exception as e:
                print(f"[build] Fly bridge error: {e}")
        elif COOK_ON_MODAL:
            try:
                from webapp.modal_bridge import spawn_cook
                if spawn_cook(job_id):
                    _jobs[job_id]["progress"].append({
                        "time": time.time(),
                        "message": "Starting cook (elastic worker)...",
                        "phase": "queued",
                    })
                else:
                    print(f"[build] Modal spawn failed for {job_id} — left in queue for DO worker")
            except Exception as e:
                print(f"[build] Modal bridge error: {e}")
        qinfo = cook_queue_stats(job_id)
        # Seed first queue message into memory for immediate SSE
        wait_m = int(qinfo.get("est_wait_minutes") or 0)
        pos = qinfo.get("queue_position") or 1
        msg = (
            "You're next — starting shortly..."
            if (pos <= 1 and not qinfo.get("running_count"))
            else f"Queued — position {pos} (~{max(wait_m, 1)} min wait)"
        )
        if not any(p.get("message") == msg for p in _jobs[job_id].get("progress") or []):
            _jobs[job_id]["progress"].append({
                "time": time.time(), "message": msg, "phase": "queued",
            })
        try:
            update_cook_job(
                job_id,
                progress_json=json.dumps(_jobs[job_id]["progress"]),
                status="queued",
            )
        except Exception:
            pass

    track(user_id, "cook_queued", {
        "recipe": req.recipe or "animated_explainer",
        "queue_position": qinfo.get("queue_position"),
        "queue_length": qinfo.get("queue_length"),
        "running_count": qinfo.get("running_count"),
        "est_wait_minutes": qinfo.get("est_wait_minutes"),
        "est_minutes_per_cook": qinfo.get("est_minutes_per_cook"),
        "lite_mode": lite_mode,
        "image_quality": image_quality,
        "credits_charged": credit_deducted,
        "plan": user.get("plan") or "",
        "cook_on_web": COOK_ON_WEB,
        "cook_on_modal": COOK_ON_MODAL,
        "cook_on_fly": COOK_ON_FLY,
    })
    return {
        "job_id": job_id,
        "status": qinfo.get("status", "queued"),
        "queue_position": qinfo.get("queue_position", 1),
        "queue_length": qinfo.get("queue_length", 1),
        "est_wait_minutes": qinfo.get("est_wait_minutes", 0),
        "max_concurrent": job_queue.MAX_CONCURRENT_COOKS,
        "cook_on_web": COOK_ON_WEB,
        "cook_on_modal": COOK_ON_MODAL,
        "cook_on_fly": COOK_ON_FLY,
        "image_quality": image_quality,
        "credits_charged": credit_deducted,
    }


def _get_user_job(job_id: str, request: Request) -> dict:
    """Validate job exists and belongs to the requesting user."""
    user = _current_user(request)
    if not user:
        raise HTTPException(401, "Sign in to continue.")
    job = _jobs.get(job_id)
    if job:
        if job.get("user_id") != user["id"]:
            raise HTTPException(403, "Access denied")
        if not COOK_ON_WEB:
            _refresh_job_from_db(job_id, job)
        return job
    # Fall back to durable row (e.g. after soft restart / worker-owned cook)
    row = get_cook_job(job_id)
    if not row or row.get("user_id") != user["id"]:
        raise HTTPException(404, "Job not found")
    job = hydrate_job_from_row(row)
    _jobs[job_id] = job
    return job


@app.get("/api/build/{job_id}/progress")
async def build_progress(job_id: str, request: Request):
    job = _get_user_job(job_id, request)

    async def stream():
        seen = 0
        last_announce = 0.0
        while True:
            if await request.is_disconnected():
                break
            if not COOK_ON_WEB:
                _refresh_job_from_db(job_id, job)
                now = time.time()
                if job.get("status") == "queued" and now - last_announce >= 5:
                    try:
                        announce_queued_jobs()
                        _refresh_job_from_db(job_id, job)
                    except Exception:
                        pass
                    last_announce = now
            elif job.get("status") == "queued":
                job_queue.queue_info(job_id)
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
    if job["status"] not in ("queued", "running"):
        return {"status": job["status"]}

    was_queued = False
    if COOK_ON_WEB:
        was_queued = job_queue.cancel_queued(job_id)
    else:
        was_queued = job.get("status") == "queued"
    job["status"] = "cancelled"
    job["error"] = "Cancelled by user"
    job["progress"].append({
        "time": time.time(),
        "message": "Cancelled",
        "phase": "cancelled",
    })
    if job.get("credit_deducted"):
        amt = job_credits_charged(job)
        refund_credits(job["user_id"], amt)
        job["credit_deducted"] = False
        print(f"[build] Refunded {amt} credit(s) on cancel for user {job['user_id']} (queued={was_queued})")
    try:
        update_cook_job(
            job_id, status="cancelled", error="Cancelled by user",
            credit_deducted=False, finished=True,
            progress_json=json.dumps(job["progress"][-40:]),
        )
    except Exception:
        pass
    return {"status": "cancelled", "was_queued": was_queued}


@app.get("/api/build/{job_id}/result")
async def build_result(job_id: str, request: Request):
    user = _current_user(request)
    job = _jobs.get(job_id)
    if job and not COOK_ON_WEB:
        _refresh_job_from_db(job_id, job)
    if not job:
        row = get_cook_job(job_id)
        if not row:
            raise HTTPException(404, "Job not found")
        if user and row.get("user_id") != user["id"]:
            raise HTTPException(403, "Access denied")
        if row["status"] != "complete":
            return {
                "status": row["status"],
                "progress": 0,
                **_queue_info_for(job_id),
            }
        try:
            return json.loads(row["result_json"] or "{}")
        except Exception:
            raise HTTPException(404, "Job result missing")
    if user and job.get("user_id") != user["id"]:
        raise HTTPException(403, "Access denied")
    if job["status"] != "complete":
        return {
            "status": job["status"],
            "progress": len(job["progress"]),
            **_queue_info_for(job_id),
        }
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
def generate_upload_kit(req: UploadKitRequest, user: dict = Depends(require_user)):
    from core.atlas_llm import generate_text, has_atlas

    if not has_atlas() and not config.GEMINI_KEY:
        return _maybe_attribute({"description": f"Check out this video: {req.title}", "tags": ["youtube", "video"], "hashtags": []}, user)

    prompt = (
        f"Generate YouTube upload metadata for this video:\n"
        f"Title: \"{req.title}\"\nScript excerpt: \"{req.script[:500]}\"\n\n"
        f"Return a JSON object with:\n"
        f"- \"description\": a 150-200 word YouTube description with relevant keywords, 3 paragraph breaks, and a call to action\n"
        f"- \"tags\": array of 15-20 relevant YouTube tags for SEO\n"
        f"- \"hashtags\": array of 3 hashtags\n\nReturn ONLY valid JSON."
    )
    try:
        raw = generate_text(prompt, max_tokens=2048).strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return _maybe_attribute(json.loads(raw), user)
    except Exception:
        return _maybe_attribute({"description": f"Check out this video: {req.title}", "tags": ["youtube", "video"], "hashtags": []}, user)


# ---------------------------------------------------------------------------
# Channel Data + Analysis (Script Studio)
# ---------------------------------------------------------------------------
def _channel_fetch_or_raise(channel_url: str, max_videos: int) -> dict:
    from core.channel_data import fetch_channel_data

    if not config.YOUTUBE_API_KEY:
        raise HTTPException(400, "YouTube API key not configured. Add it in Settings.")
    try:
        return fetch_channel_data(
            channel_url=channel_url,
            yt_api_key=config.YOUTUBE_API_KEY,
            downsub_key=config.DOWNSUB_KEY,
            max_videos=max(1, min(int(max_videos or 20), 50)),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except HTTPException:
        raise
    except Exception as e:
        # YouTube HttpError 404s are bad channel input, not server bugs.
        status = _provider_http_status(e)
        detail = str(e)
        if "playlistNotFound" in detail or "HttpError 404" in detail:
            status = 400
            detail = (
                "That YouTube channel has no accessible uploads playlist. "
                "Try another channel URL with public videos."
            )
        raise HTTPException(status, f"Channel fetch failed: {detail}")


@app.post("/api/channel/fetch")
def fetch_channel(req: ChannelFetchRequest, user: dict = Depends(require_user)):
    return _channel_fetch_or_raise(req.channel_url, req.max_videos)


@app.post("/api/channel/fetch-batch")
def fetch_channel_batch(req: ChannelBatchFetchRequest, admin: dict = Depends(require_admin)):
    """Admin-only: fetch multiple channels in parallel."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    channels = (req.channels or [])[:12]
    if not channels:
        raise HTTPException(400, "Add at least one channel URL.")
    if not config.YOUTUBE_API_KEY:
        raise HTTPException(400, "YouTube API key not configured. Add it in Settings.")

    slots: list[dict | None] = [None] * len(channels)
    errors: list[dict] = []

    def _one(idx: int, item: ChannelBatchItem) -> tuple[int, str, dict | None, str]:
        url = (item.channel_url or "").strip()
        if not url:
            return idx, url, None, "Empty channel URL"
        try:
            data = _channel_fetch_or_raise(url, item.max_videos)
            data = dict(data)
            data["_input_url"] = url
            data["_max_videos"] = max(1, min(int(item.max_videos or 20), 50))
            return idx, url, data, ""
        except HTTPException as e:
            return idx, url, None, str(e.detail)
        except Exception as e:
            return idx, url, None, str(e)

    workers = min(6, max(1, len(channels)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_one, i, c) for i, c in enumerate(channels)]
        for fut in as_completed(futs):
            idx, url, data, err = fut.result()
            if data is not None:
                slots[idx] = data
            else:
                errors.append({"channel_url": url, "error": err, "index": idx})

    ordered = [r for r in slots if r is not None]
    return {
        "ok": True,
        "count": len(ordered),
        "channels": ordered,
        "errors": sorted(errors, key=lambda e: e.get("index", 0)),
        "fetched_by": admin.get("email"),
    }
@app.post("/api/channel/analyze")
def analyze_channel(req: ChannelAnalyzeRequest, user: dict = Depends(require_user)):
    if not config.ANTHROPIC_KEY:
        return {"analysis": "Claude API key not configured. Add it in Settings to enable channel analysis."}

    try:
        from core.script_gen import analyze_channel as _analyze
        result = _analyze(channel_data=req.channel_data, api_key=config.ANTHROPIC_KEY)
        return {"analysis": result}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Channel analysis failed: {e}")


@app.post("/api/ideas")
def generate_ideas(req: IdeasRequest, user: dict = Depends(require_user)):
    if not config.ANTHROPIC_KEY:
        raise HTTPException(400, "Claude API key not configured. Add it in Settings.")

    try:
        from core.script_gen import generate_ideas as _gen, parse_ideas_response
        result = _gen(
            channel_data=req.channel_data,
            api_key=config.ANTHROPIC_KEY,
            num_ideas=req.num_ideas,
            analysis=req.analysis,
        )
        ideas = parse_ideas_response(result, limit=req.num_ideas or 7)
        return {"ideas": ideas, "raw": result}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Idea generation failed: {e}")


@app.post("/api/titles/claude")
def generate_titles_claude(req: ClaudeTitlesRequest, user: dict = Depends(require_user)):
    if not config.ANTHROPIC_KEY:
        raise HTTPException(400, "Claude API key not configured. Add it in Settings.")

    try:
        from core.script_gen import generate_titles as _gen, parse_titles_response
        result = _gen(
            video_idea=req.video_idea,
            channel_data=req.channel_data,
            api_key=config.ANTHROPIC_KEY,
        )
        titles = parse_titles_response(result, limit=5)
        return {"titles": titles, "raw": result}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Title generation failed: {e}")


@app.post("/api/script/claude")
def generate_script_claude(req: ClaudeScriptRequest, user: dict = Depends(require_user)):
    if not config.ANTHROPIC_KEY:
        raise HTTPException(400, "Claude API key not configured. Add it in Settings.")

    _enforce_length_cap(user, req.target_minutes, label="Script")

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
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Script generation failed: {e}")


# ---------------------------------------------------------------------------
# Niche Finder (shared niche database — browse for users, hunt for admin/cron)
# ---------------------------------------------------------------------------
# Jobs persist in niche_hunt_runs. Prefer a dedicated Fly Machine (same cook
# app image, different cmd) so refresh/restarts don't kill the scrape.
# Fallback: local daemon thread on the web dyno. Never uses the cook queue.
_niche_scrape_lock = __import__("threading").Lock()


def _niche_finder_can_browse(user: dict) -> bool:
    if _is_admin_email(user.get("email", "")):
        return True
    plan = (user.get("plan") or "free").lower()
    return plan in ("starter", "daily", "pro")


def _niche_finder_can_run(user: dict) -> bool:
    return _is_admin_email(user.get("email", ""))


class NicheFinderJobRequest(BaseModel):
    keywords: list[str] = []
    max_per_keyword: int = 12
    max_channels: int = 60
    min_recent_avg_views: int = 0
    max_subscribers: int = 150_000
    scroll_count: int = 20
    max_video_age_days: int = 180


def _niche_job_response(run: dict) -> dict:
    return {
        "id": run.get("job_id") or "",
        "status": run.get("status") or "running",
        "progress": run.get("progress") or [],
        "hits": [],
        "meta": run.get("meta") or {},
        "channels_upserted": run.get("channels_upserted") or 0,
        "error": run.get("error") or None,
        "trigger": run.get("trigger") or "",
        "runner": (run.get("meta") or {}).get("runner") if isinstance(run.get("meta"), dict) else None,
    }


def _run_niche_hunt_locally(
    *,
    job_id: str,
    run_id: int,
    kws: list[str],
    max_per_keyword: int,
    max_channels: int,
    min_recent_avg_views: int,
    max_subscribers: int,
    scroll_count: int,
    max_video_age_days: int,
) -> None:
    from core.niche_finder import run_niche_finder

    def _progress(msg: str):
        try:
            append_niche_hunt_progress(job_id, msg)
        except Exception:
            pass

    try:
        _progress("Running niche scrape on web (Fly unavailable)…")
        result = run_niche_finder(
            api_key=config.YOUTUBE_API_KEY,
            keywords=kws,
            max_per_keyword=max(3, min(int(max_per_keyword or 12), 25)),
            max_channels=max(5, min(int(max_channels or 60), 100)),
            min_recent_avg_views=max(0, int(min_recent_avg_views or 0)),
            max_subscribers=max(10_000, int(max_subscribers or 150_000)),
            scroll_count=max(5, min(int(scroll_count or 20), 40)),
            max_video_age_days=max(30, min(int(max_video_age_days or 180), 365)),
            progress=_progress,
        )
        hits = result.get("hits") or []
        n = upsert_niche_channels(hits)
        meta = dict(result.get("meta") or {})
        meta["runner"] = "web"
        finish_niche_hunt_run(
            run_id,
            status="completed",
            meta=meta,
            channels_upserted=n,
        )
        _progress(f"Saved {n} channels to the niche library")
    except Exception as e:
        finish_niche_hunt_run(
            run_id,
            status="error",
            channels_upserted=0,
            error=str(e),
        )
        print(f"[niche_finder] job {job_id} failed: {e}")
    finally:
        try:
            _niche_scrape_lock.release()
        except Exception:
            pass


def _start_niche_hunt(
    *,
    keywords: list[str],
    max_per_keyword: int,
    max_channels: int,
    min_recent_avg_views: int,
    max_subscribers: int,
    trigger: str,
    user_id: int | None = None,
    scroll_count: int = 20,
    max_video_age_days: int = 180,
) -> str:
    """
    Kick off scroll discovery + upsert. Prefer Fly Machine; fall back to web thread.
    Job state is in Postgres so page refresh can keep polling.
    """
    import threading
    from core.niche_finder import DEFAULT_KEYWORDS

    existing = get_latest_running_niche_hunt()
    if existing and existing.get("job_id"):
        age = time.time() - float(existing.get("started_at") or 0)
        # Fly Machines that crash before writing progress leave a zombie "running" row.
        if age > 5 * 60 and existing.get("id"):
            finish_niche_hunt_run(
                int(existing["id"]),
                status="error",
                error="Timed out (no finish within 5m) — safe to start a new scrape.",
            )
        else:
            raise HTTPException(
                409,
                detail={
                    "message": "A niche discovery scrape is already running. Re-attach to that job.",
                    "job_id": existing["job_id"],
                },
            )

    kws = [k.strip() for k in (keywords or []) if k and str(k).strip()]
    if not kws:
        kws = list(DEFAULT_KEYWORDS)

    job_id = str(uuid.uuid4())
    request = {
        "keywords": kws,
        "max_per_keyword": max_per_keyword,
        "max_channels": max_channels,
        "min_recent_avg_views": min_recent_avg_views,
        "max_subscribers": max_subscribers,
        "scroll_count": scroll_count,
        "max_video_age_days": max_video_age_days,
        "user_id": user_id,
    }
    run_id = create_niche_hunt_run(
        job_id=job_id,
        trigger=trigger,
        keywords=kws,
        request=request,
    )
    append_niche_hunt_progress(job_id, "Queued niche discovery…")

    spawned = False
    machine_id = ""
    if COOK_ON_FLY:
        try:
            from webapp.fly_bridge import spawn_niche_scrape_machine
            machine_id = spawn_niche_scrape_machine(job_id) or ""
            spawned = bool(machine_id)
        except Exception as e:
            print(f"[niche_finder] Fly spawn error: {e}")
            spawned = False

    if spawned:
        mid_note = f" ({machine_id})" if machine_id else ""
        append_niche_hunt_progress(job_id, f"Spawned Fly Machine for scroll scrape…{mid_note}")
        return job_id

    # Local fallback — only one web-thread scrape at a time
    if not _niche_scrape_lock.acquire(blocking=False):
        finish_niche_hunt_run(
            run_id,
            status="error",
            error="Could not start local scrape (busy) and Fly spawn failed.",
        )
        raise HTTPException(409, "A niche discovery scrape is already running on the web dyno.")

    threading.Thread(
        target=_run_niche_hunt_locally,
        kwargs=dict(
            job_id=job_id,
            run_id=run_id,
            kws=kws,
            max_per_keyword=max_per_keyword,
            max_channels=max_channels,
            min_recent_avg_views=min_recent_avg_views,
            max_subscribers=max_subscribers,
            scroll_count=scroll_count,
            max_video_age_days=max_video_age_days,
        ),
        daemon=True,
        name="niche-scroll-scrape",
    ).start()
    return job_id


@app.get("/api/niche-finder/access")
def niche_finder_access(user: dict = Depends(require_user)):
    from core.niche_finder import DEFAULT_KEYWORDS, MIN_DURATION_SEC

    can_browse = _niche_finder_can_browse(user)
    can_run = _niche_finder_can_run(user)
    plan = (user.get("plan") or "free").lower()
    if can_browse:
        access = "ok"
        message = None
    elif plan in ("starter_trial", "daily_trial", "free"):
        access = "pro_only"
        message = "Niche Finder is only available on Pro."
    else:
        access = "pro_only"
        message = "Niche Finder is only available on Pro."

    active_job_id = None
    if can_run:
        running = get_latest_running_niche_hunt()
        if running and running.get("job_id"):
            active_job_id = running["job_id"]

    return {
        "access": access,
        "can_browse": can_browse,
        "can_run": can_run,
        "message": message,
        "default_keywords": DEFAULT_KEYWORDS if can_run else [],
        "min_duration_sec": MIN_DURATION_SEC,
        "channel_count": count_niche_channels() if can_browse else 0,
        "active_job_id": active_job_id,
        "cook_on_fly": bool(COOK_ON_FLY),
    }


@app.get("/api/niche-finder/channels")
def niche_finder_channels(
    sort: str = "recent_revenue",
    limit: int = 40,
    offset: int = 0,
    min_recent_avg: float = 0,
    max_recent_avg: float = 0,
    min_subscribers: int = 0,
    max_subscribers: int = 0,
    min_videos: int = 0,
    max_videos: int = 0,
    min_recent_revenue: float = 0,
    max_recent_revenue: float = 0,
    active_recently: bool = False,
    has_recent_avg: bool = False,
    q: str = "",
    user: dict = Depends(require_user),
):
    if not _niche_finder_can_browse(user):
        raise HTTPException(402, "Niche Finder is only available on Pro.")
    filters = dict(
        min_recent_avg=min_recent_avg or None,
        max_recent_avg=max_recent_avg or None,
        min_subscribers=min_subscribers or None,
        max_subscribers=max_subscribers or None,
        min_videos=min_videos or None,
        max_videos=max_videos or None,
        min_recent_revenue=min_recent_revenue or None,
        max_recent_revenue=max_recent_revenue or None,
        active_recently=bool(active_recently),
        has_recent_avg=bool(has_recent_avg),
        q=(q or "").strip(),
    )
    channels = list_niche_channels(sort=sort or "recent_revenue", limit=limit, offset=offset, **filters)
    total = count_niche_channels(**filters)
    return {
        "channels": channels,
        "total": total,
        "limit": max(1, min(int(limit or 40), 100)),
        "offset": max(0, int(offset or 0)),
        "sort": sort or "recent_revenue",
        "filters": filters,
    }


@app.post("/api/niche-finder/jobs")
def start_niche_finder_job(
    req: NicheFinderJobRequest,
    admin: dict = Depends(require_admin),
):
    if not config.YOUTUBE_API_KEY:
        raise HTTPException(400, "YouTube API key not configured. Add it in Settings.")
    job_id = _start_niche_hunt(
        keywords=req.keywords or [],
        max_per_keyword=req.max_per_keyword,
        max_channels=req.max_channels,
        min_recent_avg_views=req.min_recent_avg_views,
        max_subscribers=req.max_subscribers,
        scroll_count=req.scroll_count,
        max_video_age_days=req.max_video_age_days,
        trigger="admin",
        user_id=admin["id"],
    )
    return {"job_id": job_id, "status": "running"}


@app.get("/api/niche-finder/jobs/{job_id}")
def get_niche_finder_job(job_id: str, admin: dict = Depends(require_admin)):
    run = get_niche_hunt_run_by_job_id(job_id)
    if not run:
        raise HTTPException(404, "Job not found.")
    return _niche_job_response(run)


@app.post("/api/niche-finder/jobs/{job_id}/cancel")
def cancel_niche_finder_job(job_id: str, admin: dict = Depends(require_admin)):
    run = cancel_niche_hunt_run(job_id, reason="Cancelled by admin")
    if not run:
        raise HTTPException(404, "Job not found.")
    return _niche_job_response(run)


@app.post("/api/niche-finder/jobs/cancel-running")
def cancel_running_niche_finder_jobs(admin: dict = Depends(require_admin)):
    """Force-clear zombie 'running' hunts so Add niches unlocks."""
    n = cancel_all_running_niche_hunts(reason="Cancelled by admin (force clear)")
    return {"cancelled": n, "status": "ok"}


@app.get("/api/niche-finder/runs")
def niche_finder_runs(limit: int = 20, admin: dict = Depends(require_admin)):
    return {"runs": list_niche_hunt_runs(limit=limit)}


# ---------------------------------------------------------------------------
# Niche Intel (admin-only Shorts competitor packs for LLM drag-and-drop)
# ---------------------------------------------------------------------------
_NICHE_INTEL_JOBS: dict[str, dict[str, Any]] = {}
_niche_intel_lock = __import__("threading").Lock()


def _niche_intel_job_public(job: dict) -> dict:
    return {
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "progress": list(job.get("progress") or [])[-40:],
        "error": job.get("error") or "",
        "niche": job.get("niche") or "",
        "channels_ok": job.get("channels_ok") or 0,
        "errors": job.get("errors") or [],
        "zip_ready": bool(job.get("zip_path") and Path(job["zip_path"]).is_file()),
        "created_at": job.get("created_at"),
    }


def _run_niche_intel_job(job_id: str) -> None:
    import threading
    with _niche_intel_lock:
        job = _NICHE_INTEL_JOBS.get(job_id)
    if not job:
        return

    def progress(msg: str) -> None:
        with _niche_intel_lock:
            j = _NICHE_INTEL_JOBS.get(job_id)
            if not j:
                return
            j.setdefault("progress", []).append({"t": time.time(), "msg": msg})
            j["progress"] = j["progress"][-80:]

    try:
        with _niche_intel_lock:
            _NICHE_INTEL_JOBS[job_id]["status"] = "running"
        from core.niche_intel import build_pack
        result = build_pack(
            niche=job.get("niche") or "niche",
            channel_urls=list(job.get("channels") or []),
            videos_per_channel=int(job.get("videos_per_channel") or 10),
            frames_per_video=int(job.get("frames_per_video") or 8),
            out_root=OUTPUT_DIR / "niche_intel",
            progress=progress,
        )
        with _niche_intel_lock:
            j = _NICHE_INTEL_JOBS.get(job_id) or {}
            j.update({
                "status": "complete",
                "zip_path": result.get("zip_path") or "",
                "pack_dir": result.get("pack_dir") or "",
                "channels_ok": result.get("channels_ok") or 0,
                "errors": result.get("errors") or [],
            })
            _NICHE_INTEL_JOBS[job_id] = j
    except Exception as e:
        progress(f"FAILED: {e}")
        with _niche_intel_lock:
            j = _NICHE_INTEL_JOBS.get(job_id) or {}
            j["status"] = "error"
            j["error"] = str(e)
            _NICHE_INTEL_JOBS[job_id] = j


@app.post("/api/niche-intel/jobs")
def start_niche_intel_job(req: NicheIntelJobRequest, admin: dict = Depends(require_admin)):
    import threading
    if not config.YOUTUBE_API_KEY:
        raise HTTPException(400, "YouTube API key not configured. Add it in Settings.")
    channels = [c.strip() for c in (req.channels or []) if (c or "").strip()]
    if not channels:
        raise HTTPException(400, "Paste at least one channel URL.")
    if len(channels) > 12:
        raise HTTPException(400, "Max 12 channels per run.")

    job_id = str(uuid.uuid4())
    job = {
        "job_id": job_id,
        "status": "queued",
        "progress": [{"t": time.time(), "msg": "Queued…"}],
        "error": "",
        "niche": (req.niche or "niche").strip() or "niche",
        "channels": channels,
        "videos_per_channel": max(1, min(int(req.videos_per_channel or 10), 30)),
        "frames_per_video": max(2, min(int(req.frames_per_video or 8), 24)),
        "zip_path": "",
        "pack_dir": "",
        "channels_ok": 0,
        "errors": [],
        "created_at": time.time(),
        "user_id": admin["id"],
    }
    with _niche_intel_lock:
        _NICHE_INTEL_JOBS[job_id] = job
    threading.Thread(target=_run_niche_intel_job, args=(job_id,), daemon=True).start()
    return _niche_intel_job_public(job)


@app.get("/api/niche-intel/jobs/{job_id}")
def get_niche_intel_job(job_id: str, admin: dict = Depends(require_admin)):
    with _niche_intel_lock:
        job = _NICHE_INTEL_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found.")
    return _niche_intel_job_public(job)


@app.get("/api/niche-intel/jobs/{job_id}/download")
def download_niche_intel_job(job_id: str, admin: dict = Depends(require_admin)):
    with _niche_intel_lock:
        job = _NICHE_INTEL_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found.")
    zip_path = Path(job.get("zip_path") or "")
    if not zip_path.is_file():
        raise HTTPException(404, "Zip not ready yet.")
    return FileResponse(
        str(zip_path),
        media_type="application/zip",
        filename=zip_path.name,
    )


# ---------------------------------------------------------------------------
# Storyboard Pack (admin-only v1 — stills + I2V prompts zip)
# ---------------------------------------------------------------------------
@app.get("/api/storyboard/cast")
async def get_storyboard_cast(admin: dict = Depends(require_admin)):
    """Load persistent cast settings (empty cast by default — no forced family)."""
    from core.storyboard_pack import (
        VISUAL_STYLE_PRESETS,
        family_template_cast,
        normalize_cast,
        resolve_visual_style,
    )
    from webapp.database import get_user_storyboard_settings
    settings = get_user_storyboard_settings(int(admin["id"]))
    cast = normalize_cast(settings.get("cast") or [])
    style_id, _, _ = resolve_visual_style(settings.get("visual_style") or "")
    template = (settings.get("template") or "").strip()
    styles = [
        {"id": sid, "label": meta.get("label") or sid}
        for sid, meta in VISUAL_STYLE_PRESETS.items()
    ]
    return {
        "cast": cast,
        "visual_style": style_id,
        "template": template,
        "styles": styles,
        "family_template_available": True,
        "family_template_cast": family_template_cast(),
    }


@app.put("/api/storyboard/cast")
async def save_storyboard_cast(req: StoryboardCastSaveRequest, admin: dict = Depends(require_admin)):
    from core.storyboard_pack import normalize_cast, resolve_visual_style
    from webapp.database import set_user_storyboard_settings
    rows = []
    for m in req.cast or []:
        rows.append({
            "id": (m.id or "").strip().lower(),
            "name": (m.name or m.id or "").strip(),
            "included": bool(m.included),
            "look_prompt": (m.look_prompt or "").strip(),
            "portrait_url": (m.portrait_url or "").strip(),
            "sheet_url": (m.sheet_url or "").strip(),
            "portrait_path": (m.portrait_path or "").strip(),
            "sheet_path": (m.sheet_path or "").strip(),
        })
    cast = normalize_cast(rows)
    style_id, _, _ = resolve_visual_style(req.visual_style or "")
    template = (req.template or "").strip()
    set_user_storyboard_settings(
        int(admin["id"]),
        cast=cast,
        visual_style=style_id,
        template=template,
    )
    return {"cast": cast, "visual_style": style_id, "template": template, "saved": True}


@app.post("/api/storyboard/cast/extract")
async def extract_storyboard_cast(
    req: StoryboardExtractCastRequest,
    admin: dict = Depends(require_admin),
):
    """Propose recurring cast from story/script — user confirms before looks."""
    story = (req.story or "").strip()
    script = (req.script or "").strip()
    if not story and not script:
        raise HTTPException(400, "Paste a story or script first so we can find the characters.")
    try:
        from core.storyboard_pack import extract_cast_from_text, resolve_visual_style
        style_id, _, _ = resolve_visual_style(req.visual_style or "")
        cast = await asyncio.to_thread(
            extract_cast_from_text,
            story=story,
            script=script,
            visual_style=style_id,
        )
    except Exception as e:
        print(f"[storyboard] cast extract failed: {e}")
        raise HTTPException(500, "Could not extract characters. Add them manually.")
    return {"cast": cast, "visual_style": style_id}


@app.post("/api/storyboard/cast/generate-look")
async def generate_storyboard_cast_look(
    req: StoryboardLookRequest,
    admin: dict = Depends(require_admin),
):
    """Generate portrait (+ optional sheet) for one cast member — Cast studio dopamine."""
    from core.storyboard_pack import (
        generate_character_portrait,
        generate_character_sheet,
        normalize_cast,
        resolve_visual_style,
    )
    from webapp.database import get_user_storyboard_settings, set_user_storyboard_settings

    cid = (req.id or "").strip().lower()
    name = (req.name or cid).strip() or cid
    if not cid and not name:
        raise HTTPException(400, "Character name required.")
    if not cid:
        from core.storyboard_pack import _cast_id_from_name
        cid = _cast_id_from_name(name)
    look = (req.look_prompt or "").strip() or f"consistent animated character named {name}"
    style_id, _, _ = resolve_visual_style(req.visual_style or "")

    out_dir = OUTPUT_DIR / "storyboard_cast" / str(admin["id"]) / cid
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())

    try:
        portrait_local = await asyncio.to_thread(
            generate_character_portrait,
            name=name,
            look_prompt=look,
            out_path=out_dir / f"portrait_{stamp}.png",
            visual_style=style_id,
        )
        portrait_path, portrait_url = _stage_user_media(
            portrait_local, admin["id"], f"cast_{cid}_portrait", "image/png",
        )
        sheet_path, sheet_url = "", ""
        if req.make_sheet:
            sheet_local = await asyncio.to_thread(
                generate_character_sheet,
                name=name,
                look_prompt=look,
                out_path=out_dir / f"sheet_{stamp}.png",
                portrait_path=portrait_local,
                visual_style=style_id,
            )
            sheet_path, sheet_url = _stage_user_media(
                sheet_local, admin["id"], f"cast_{cid}_sheet", "image/png",
            )
    except Exception as e:
        raise HTTPException(_provider_http_status(e), f"Look generation failed: {e}")

    settings = get_user_storyboard_settings(int(admin["id"]))
    cast = normalize_cast(settings.get("cast") or [])
    for row in cast:
        if row.get("id") == cid:
            row["name"] = name
            row["look_prompt"] = look
            row["portrait_url"] = portrait_url
            row["portrait_path"] = portrait_path
            if sheet_url:
                row["sheet_url"] = sheet_url
                row["sheet_path"] = sheet_path
            row["included"] = True
            break
    else:
        cast.append({
            "id": cid,
            "name": name,
            "included": True,
            "look_prompt": look,
            "portrait_url": portrait_url,
            "sheet_url": sheet_url,
            "portrait_path": portrait_path,
            "sheet_path": sheet_path,
        })
        cast = normalize_cast(cast)
    set_user_storyboard_settings(
        int(admin["id"]),
        cast=cast,
        visual_style=style_id or settings.get("visual_style") or "pixar_lite",
        template=settings.get("template") or "",
    )
    member = next((c for c in cast if c.get("id") == cid), cast[0] if cast else {})
    return {"member": member, "cast": cast, "visual_style": style_id}


    return {"member": member, "cast": cast, "visual_style": style_id}


@app.post("/api/storyboard/thumbnail")
async def storyboard_thumbnail(
    title: str = Form(...),
    story: str = Form(""),
    script: str = Form(""),
    moral: str = Form(""),
    visual_style: str = Form(""),
    niche_style: str = Form(""),
    cast_json: str = Form("[]"),
    count: int = Form(2),
    refs: list[UploadFile] = File(default=[]),
    admin: dict = Depends(require_admin),
):
    """Story-aware thumbnail: cheap gist + main cast portraits as edit refs."""
    from core.storyboard_pack import (
        normalize_cast,
        resolve_visual_style,
        suggest_storyboard_thumbnail_brief,
    )
    from core.thumbnail_gen import generate_thumbnail_no_refs, generate_thumbnails
    from webapp.storage import fetch_to_local

    title = (title or "").strip()
    if not title:
        raise HTTPException(400, "Add a title first.")
    try:
        cast_raw = json.loads(cast_json or "[]")
    except Exception:
        cast_raw = []
    cast = normalize_cast(cast_raw if isinstance(cast_raw, list) else [])
    style_id, _, _ = resolve_visual_style(visual_style or "")
    n = max(1, min(int(count or 2), 3))

    try:
        brief = await asyncio.to_thread(
            suggest_storyboard_thumbnail_brief,
            title=title,
            story=(story or "").strip(),
            script=(script or "").strip(),
            cast=cast,
            visual_style=style_id,
            moral=(moral or "").strip(),
        )
    except Exception as e:
        print(f"[storyboard] thumb brief failed: {e}")
        brief = {
            "style_prompt": (
                f"{niche_style or 'animated story YouTube thumbnail'}. "
                f"Title: {title}. Bold emotional character faces, 16:9."
            ),
            "cast_focus": [],
        }

    style_prompt = " ".join(
        p for p in [
            (brief.get("style_prompt") or "").strip(),
            (niche_style or "").strip(),
        ] if p
    )[:1400]

    # Resolve cast portrait refs (prefer focus names)
    focus = {str(n).strip().lower() for n in (brief.get("cast_focus") or []) if n}
    cache = OUTPUT_DIR / "storyboard_thumb_refs" / str(admin["id"]) / str(int(time.time()))
    cache.mkdir(parents=True, exist_ok=True)
    ref_paths: list[str] = []

    def _want(row: dict) -> bool:
        if not row.get("included", True):
            return False
        if not focus:
            return True
        name = str(row.get("name") or "").strip().lower()
        cid = str(row.get("id") or "").strip().lower()
        return name in focus or cid in focus

    prioritized = [r for r in cast if _want(r)] + [r for r in cast if not _want(r)]
    for row in prioritized:
        if len(ref_paths) >= 2:
            break
        for key in ("portrait_path", "portrait_url", "sheet_path", "sheet_url"):
            raw = (row.get(key) or "").strip()
            if not raw:
                continue
            try:
                if raw.startswith("http"):
                    local = fetch_to_local(raw, cache)
                elif Path(raw).is_file():
                    local = raw
                else:
                    continue
                if local and Path(local).is_file():
                    ref_paths.append(local)
                    break
            except Exception as e:
                print(f"[storyboard] thumb cast ref skip: {e}")

    # Optional channel/user refs (cap total 3)
    for ref in refs or []:
        if len(ref_paths) >= 3:
            break
        try:
            dest = cache / f"user_{int(time.time())}_{ref.filename or 'ref.png'}"
            with open(dest, "wb") as f:
                f.write(await ref.read())
            if dest.is_file() and dest.stat().st_size > 100:
                ref_paths.append(str(dest))
        except Exception as e:
            print(f"[storyboard] user thumb ref failed: {e}")

    out_dir = str(OUTPUT_DIR / "thumbnails" / str(admin["id"]) / str(int(time.time())))
    try:
        if ref_paths:
            paths = await asyncio.to_thread(
                generate_thumbnails,
                title,
                ref_paths,
                style_prompt,
                "",
                n,
                out_dir,
            )
        else:
            paths = await asyncio.to_thread(
                generate_thumbnail_no_refs,
                title,
                style_prompt,
                "",
                out_dir,
                n,
            )
        if not paths:
            raise ValueError("No thumbnails generated")
        staged_paths, urls = [], []
        for i, p in enumerate(paths[:n]):
            sp, su = _stage_user_media(p, admin["id"], f"sb_thumb_{i}", "image/png")
            staged_paths.append(sp)
            urls.append(su)
        return {
            "thumbnails": urls,
            "paths": staged_paths,
            "brief": {
                "hook": brief.get("hook"),
                "emotion": brief.get("emotion"),
                "cast_focus": brief.get("cast_focus"),
                "composition": brief.get("composition"),
            },
            "visual_style": style_id,
        }
    except Exception as e:
        raise HTTPException(_provider_http_status(e), f"Thumbnail generation failed: {e}")


@app.post("/api/storyboard/suggest-morals")
async def storyboard_suggest_morals(
    req: StoryboardSuggestMoralsRequest,
    admin: dict = Depends(require_admin),
):
    """Optional takeaway suggestions from the user's story (admin-only)."""
    story = (req.story or "").strip()
    if not story:
        raise HTTPException(400, "Describe the story first, then suggest takeaways.")
    try:
        from core.storyboard_pack import suggest_morals_from_story
        morals = suggest_morals_from_story(story, template=(req.template or "").strip())
    except Exception as e:
        print(f"[storyboard] suggest-morals failed: {e}")
        raise HTTPException(500, "Could not suggest takeaways. Try writing one yourself.")
    return {"morals": morals}


@app.post("/api/storyboard/jobs")
async def start_storyboard_job(req: StoryboardJobRequest, admin: dict = Depends(require_admin)):
    """Queue a storyboard pack build (0 credits while admin-testing)."""
    title = (req.title or "").strip()
    story = (req.story or req.topic or "").strip()
    topic = story  # keep topic for older cook workers
    script = (req.script or "").strip()
    moral = (req.moral or "").strip()
    dialogue_mode = (req.dialogue_mode or "generate").strip().lower()
    if dialogue_mode not in ("generate", "paste"):
        dialogue_mode = "paste" if script else "generate"
    mistake_by = (req.mistake_by or "").strip()
    from core.storyboard_pack import normalize_cast, resolve_visual_style
    style_id, _, _ = resolve_visual_style(req.visual_style or "")
    template = (req.template or "").strip()

    cast_rows: list[dict] = []
    for m in req.cast or []:
        cast_rows.append({
            "id": (m.id or "").strip().lower(),
            "name": (m.name or m.id or "").strip(),
            "included": bool(m.included),
            "look_prompt": (m.look_prompt or "").strip(),
            "portrait_url": (m.portrait_url or "").strip(),
            "sheet_url": (m.sheet_url or "").strip(),
            "portrait_path": (m.portrait_path or "").strip(),
            "sheet_path": (m.sheet_path or "").strip(),
        })
    cast_rows = normalize_cast(cast_rows)
    has_look = any(
        c.get("included") and (c.get("portrait_url") or c.get("portrait_path") or c.get("sheet_url"))
        for c in cast_rows
    )
    if not has_look:
        raise HTTPException(
            400,
            "Generate at least one character look in Cast studio before packing.",
        )

    if not title:
        raise HTTPException(400, "Add a title for this video.")
    if dialogue_mode == "paste":
        if not script:
            raise HTTPException(400, "Paste your script, or switch to “Write dialogue for me”.")
    else:
        if not story:
            raise HTTPException(400, "Describe what happens in this story.")

    if not story and not script:
        raise HTTPException(400, "Describe the story you want to make, or paste a script.")

    thumb_path = (req.thumbnail_path or "").strip()
    if thumb_path:
        _safe_user_path(thumb_path, "thumbnail")

    try:
        mins = float(req.target_minutes or 8)
    except (TypeError, ValueError):
        mins = 8.0
    from core.storyboard_pack import clamp_minutes, MAX_PAID_MINUTES
    mins = clamp_minutes(mins, is_admin=True, is_paid=True)
    if mins > MAX_PAID_MINUTES:
        raise HTTPException(400, f"Max length is {MAX_PAID_MINUTES} minutes.")

    pack_mode = (req.pack_mode or "full").strip().lower()
    if pack_mode not in ("preview", "full"):
        pack_mode = "full"

    plan = (admin.get("plan") or "free").strip()
    is_paid = plan in ("starter", "daily", "pro")

    job_id = str(uuid.uuid4())
    req_payload = {
        "recipe": "storyboard_pack",
        "title": title or (story[:80] if story else "Storyboard Pack"),
        "topic": topic,
        "story": story,
        "moral": moral,
        "cast": cast_rows,
        "mistake_by": mistake_by,
        "dialogue_mode": dialogue_mode,
        "script": script if dialogue_mode == "paste" else (script or ""),
        "target_minutes": mins,
        "thumbnail_path": thumb_path,
        "pack_mode": pack_mode,
        "visual_style": style_id,
        "template": template,
        "is_admin": True,
        "is_paid": is_paid,
        "credits_charged": 0,
        "notify_email": admin.get("email") or "",
    }

    try:
        create_cook_job(
            job_id=job_id,
            user_id=admin["id"],
            recipe="storyboard_pack",
            title=req_payload["title"],
            request_json=json.dumps(req_payload),
            credit_deducted=False,
            lite_mode=False,
            status="web_queued" if COOK_ON_WEB else "queued",
        )
    except Exception as e:
        print(f"[storyboard] create_cook_job failed: {e}")
        raise HTTPException(500, "Could not queue storyboard pack.")

    job = {
        "status": "queued",
        "progress": [{"time": time.time(), "message": (
            "Queued first-minute preview…" if pack_mode == "preview" else "Queued storyboard pack…"
        ), "phase": "queued"}],
        "result": None,
        "request": req_payload,
        "user_id": admin["id"],
        "credit_deducted": False,
        "lite_mode": False,
        "error": "",
        "created_at": time.time(),
    }
    _jobs[job_id] = job

    if COOK_ON_WEB:
        job_queue.enqueue(job_id)
    else:
        if COOK_ON_FLY:
            try:
                from webapp.fly_bridge import spawn_cook as fly_spawn
                if fly_spawn(job_id):
                    job["progress"].append({
                        "time": time.time(),
                        "message": "Starting your cook…",
                        "phase": "queued",
                    })
                else:
                    print(f"[storyboard] Fly spawn failed for {job_id}")
            except Exception as e:
                print(f"[storyboard] Fly bridge error: {e}")
        try:
            update_cook_job(job_id, progress_json=json.dumps(job["progress"]), status="queued")
        except Exception:
            pass

    track(admin["id"], "storyboard_pack_queued", {
        "job_id": job_id,
        "target_minutes": mins,
        "has_script": bool(script),
        "dialogue_mode": dialogue_mode,
        "cook_on_fly": bool(COOK_ON_FLY),
        "cook_on_web": bool(COOK_ON_WEB),
        "has_thumbnail": bool(req_payload.get("thumbnail_path")),
    })
    return {
        "job_id": job_id,
        "status": "queued",
        "target_minutes": mins,
        "title": req_payload["title"],
    }


@app.get("/api/storyboard/jobs/{job_id}")
async def get_storyboard_job(job_id: str, admin: dict = Depends(require_admin)):
    job = _jobs.get(job_id)
    if job:
        if job.get("user_id") != admin["id"] and not _is_admin_email(admin.get("email", "")):
            raise HTTPException(403, "Access denied")
        _refresh_job_from_db(job_id, job)
    else:
        row = get_cook_job(job_id)
        if not row:
            raise HTTPException(404, "Job not found.")
        if row.get("user_id") != admin["id"] and not _is_admin_email(admin.get("email", "")):
            raise HTTPException(403, "Access denied")
        recipe = (row.get("recipe") or "")
        if recipe not in ("storyboard_pack", "storyboard_assemble", "storyboard_animate"):
            raise HTTPException(404, "Not a storyboard job.")
        job = hydrate_job_from_row(row)
        _jobs[job_id] = job

    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    kind = result.get("kind") or ((job.get("request") or {}).get("recipe") or "storyboard_pack")
    return {
        "job_id": job_id,
        "status": job.get("status") or "queued",
        "progress": list(job.get("progress") or [])[-40:],
        "error": job.get("error") or "",
        "kind": kind,
        "zip_ready": bool(result.get("zip_url") or result.get("zip_path") or result.get("zip_ready")),
        "zip_url": result.get("zip_url") or "",
        "video_ready": bool(result.get("video_url") or result.get("video_path") or result.get("video_ready")),
        "video_url": result.get("video_url") or "",
        "match_report": result.get("match_report") or [],
        "title": result.get("title") or (job.get("request") or {}).get("title") or "",
        "beat_count": result.get("beat_count") or len(result.get("beats") or []),
        "target_minutes": result.get("target_minutes") or 0,
        "duration_sec": result.get("duration_sec") or 0,
        "caption_count": result.get("caption_count") or 0,
        "beats": result.get("beats") or [],
        "pack_mode": result.get("pack_mode") or (job.get("request") or {}).get("pack_mode") or "full",
        "parent_job_id": result.get("parent_job_id") or (job.get("request") or {}).get("parent_job_id") or "",
    }


@app.post("/api/storyboard/jobs/{job_id}/regen-beat")
async def regen_storyboard_beat(
    job_id: str,
    req: StoryboardRegenBeatRequest,
    admin: dict = Depends(require_admin),
):
    """Regenerate one weak still; updates live board + re-zips when pack_dir exists."""
    job = _jobs.get(job_id)
    if not job:
        row = get_cook_job(job_id)
        if not row:
            raise HTTPException(404, "Job not found.")
        job = hydrate_job_from_row(row)
        _jobs[job_id] = job
    else:
        _refresh_job_from_db(job_id, job)

    if job.get("user_id") != admin["id"] and not _is_admin_email(admin.get("email", "")):
        raise HTTPException(403, "Access denied")
    if (job.get("request") or {}).get("recipe") != "storyboard_pack" and (
        get_cook_job(job_id) or {}
    ).get("recipe") not in ("storyboard_pack", None):
        pass

    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    beats = list(result.get("beats") or [])
    target = None
    for b in beats:
        if int(b.get("index") or 0) == int(req.index):
            target = b
            break
    if not target:
        raise HTTPException(404, f"Beat {req.index} not found on this job.")

    cast = (job.get("request") or {}).get("cast") or []
    # Prefer live Cast studio settings — job cast often has Fly-local paths that
    # don't exist on the web dyno; settings keep portrait_url/sheet_url.
    try:
        from webapp.database import get_user_storyboard_settings
        settings = get_user_storyboard_settings(int(admin["id"]))
        live_cast = settings.get("cast") if isinstance(settings.get("cast"), list) else []
        if live_cast:
            cast = live_cast
        settings_style = (settings.get("visual_style") or "").strip()
    except Exception:
        settings_style = ""
    visual_style = (
        (req.visual_style or "").strip()
        or ((job.get("request") or {}).get("visual_style") or "").strip()
        or settings_style
        or "pixar_lite"
    )
    # Neighbor stills as style anchors so recreate matches the rest of the board.
    style_anchors: list[str] = []
    for b in beats:
        if int(b.get("index") or 0) == int(req.index):
            continue
        u = (b.get("image_url") or "").strip()
        if u.startswith("http"):
            style_anchors.append(u)
        if len(style_anchors) >= 2:
            break
    from core.storyboard_pack import Beat, regenerate_beat_still, zip_pack
    from webapp import storage as _storage

    out_dir = OUTPUT_DIR / "storyboard_regen" / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    beat_obj = Beat(
        index=int(target.get("index") or req.index),
        target_sec=float(target.get("target_sec") or 8),
        dialogue=str(target.get("dialogue") or ""),
        image_prompt=str(target.get("image_prompt") or ""),
        i2v_prompt=str(target.get("i2v_prompt") or ""),
        location=str(target.get("location") or ""),
        characters=str(target.get("characters") or ""),
        time_of_day=str(target.get("time_of_day") or ""),
        outfit_continuity=str(target.get("outfit_continuity") or ""),
    )

    try:
        updated = await asyncio.to_thread(
            regenerate_beat_still,
            beat_obj,
            out_dir / f"{beat_obj.index:03d}_scene.jpg",
            cast=cast,
            note=(req.note or "").strip(),
            visual_style=visual_style,
            style_anchor_urls=style_anchors,
        )
    except Exception as e:
        raise HTTPException(_provider_http_status(e), f"Regen failed: {e}")
    if not updated.image_path or not Path(updated.image_path).is_file():
        raise HTTPException(500, updated.error or "Regen produced no image")

    url_path, url = _stage_user_media(
        updated.image_path, admin["id"], f"regen_{job_id}_{beat_obj.index}", "image/jpeg",
    )
    target.update({
        "image_url": url,
        "filename": Path(updated.image_path).name,
        "dialogue": updated.dialogue,
        "i2v_prompt": updated.i2v_prompt,
    })
    for i, b in enumerate(beats):
        if int(b.get("index") or 0) == beat_obj.index:
            beats[i] = target
            break
    result["beats"] = beats
    job["result"] = result

    # Best-effort: replace in pack_dir and rezip
    pack_dir = Path(result.get("pack_dir") or "")
    if pack_dir.is_dir():
        try:
            ext = Path(updated.image_path).suffix or ".jpg"
            dest = pack_dir / f"{beat_obj.index:03d}_scene{ext}"
            dest.write_bytes(Path(updated.image_path).read_bytes())
            zip_local = Path(result.get("zip_path") or (pack_dir.parent / f"{pack_dir.name}.zip"))
            zip_pack(pack_dir, zip_local)
            try:
                zip_url = _storage.store_file(
                    str(zip_local),
                    f"storyboard/{admin['id']}/{int(time.time())}_{job_id}.zip",
                    "application/zip",
                )
                result["zip_url"] = zip_url
                result["zip_path"] = str(zip_local)
            except Exception as up_e:
                print(f"[storyboard] regen zip upload failed: {up_e}")
        except Exception as e:
            print(f"[storyboard] pack_dir regen update failed: {e}")

    job["result"] = result
    try:
        update_cook_job(job_id, result_json=json.dumps(result), heartbeat=True)
    except Exception:
        pass
    return {"beat": target, "beats": beats, "zip_url": result.get("zip_url") or ""}


@app.get("/api/storyboard/jobs/{job_id}/download")
async def download_storyboard_job(job_id: str, admin: dict = Depends(require_admin)):
    job = _jobs.get(job_id)
    if not job:
        row = get_cook_job(job_id)
        if not row:
            raise HTTPException(404, "Job not found.")
        job = hydrate_job_from_row(row)
        _jobs[job_id] = job
    else:
        _refresh_job_from_db(job_id, job)

    if job.get("user_id") != admin["id"] and not _is_admin_email(admin.get("email", "")):
        raise HTTPException(403, "Access denied")

    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    # Assemble MP4
    video_url = (result.get("video_url") or "").strip()
    if video_url.startswith("http://") or video_url.startswith("https://"):
        return RedirectResponse(video_url)
    video_path = Path(result.get("video_path") or result.get("output_path") or "")
    if video_path.is_file() and video_path.suffix.lower() in (".mp4", ".mov", ".webm", ".mkv"):
        return FileResponse(
            str(video_path),
            media_type="video/mp4",
            filename=video_path.name,
        )
    if video_url.startswith("/api/files/"):
        return RedirectResponse(video_url)

    zip_url = (result.get("zip_url") or "").strip()
    if zip_url.startswith("http://") or zip_url.startswith("https://"):
        return RedirectResponse(zip_url)
    zip_path = Path(result.get("zip_path") or "")
    if zip_path.is_file():
        return FileResponse(
            str(zip_path),
            media_type="application/zip",
            filename=zip_path.name,
        )
    if zip_url.startswith("/api/files/"):
        return RedirectResponse(zip_url)
    raise HTTPException(404, "Download not ready yet.")


def _storyboard_pack_job_or_404(job_id: str, admin: dict) -> dict:
    job = _jobs.get(job_id)
    if not job:
        row = get_cook_job(job_id)
        if not row:
            raise HTTPException(404, "Job not found.")
        job = hydrate_job_from_row(row)
        _jobs[job_id] = job
    else:
        _refresh_job_from_db(job_id, job)
    if job.get("user_id") != admin["id"] and not _is_admin_email(admin.get("email", "")):
        raise HTTPException(403, "Access denied")
    recipe = ((job.get("request") or {}).get("recipe") or "").strip()
    row_recipe = (get_cook_job(job_id) or {}).get("recipe") or ""
    if recipe not in ("storyboard_pack", "") and row_recipe not in ("storyboard_pack", ""):
        # allow if result kind is pack
        kind = ((job.get("result") or {}) if isinstance(job.get("result"), dict) else {}).get("kind")
        if kind != "storyboard_pack":
            raise HTTPException(400, "Assemble requires a completed Storyboard Pack job.")
    return job


def _storyboard_beat_has_still(beat: dict) -> bool:
    if not isinstance(beat, dict):
        return False
    return bool(
        (beat.get("image_url") or beat.get("image_path")
         or beat.get("still_url") or beat.get("still_path") or "").strip()
    )


def _require_storyboard_stills_ready(
    job: dict,
    *,
    beat_from: int | None = None,
    beat_to: int | None = None,
) -> list:
    """
    Selected contiguous scenes must have stills.
    Full-board cook (no range) also requires the pack job to be finished.
    """
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    ranged = beat_from is not None and beat_to is not None
    if not ranged and not (result.get("zip_ready") or job.get("status") == "complete"):
        raise HTTPException(400, "Wait until every scene still is ready before cooking.")
    from core.storyboard_assemble import load_pack_beats
    pack_dir = Path(result.get("pack_dir") or "")
    beats = load_pack_beats(
        pack_dir=pack_dir if pack_dir.is_dir() else None,
        beats=result.get("beats") or [],
    )
    if not beats:
        raise HTTPException(400, "No scenes in this pack to cook.")
    beats = sorted(beats, key=lambda b: int(b.get("index") or 0))
    if ranged:
        lo, hi = (beat_from, beat_to) if beat_from <= beat_to else (beat_to, beat_from)
        sliced = [b for b in beats if lo <= int(b.get("index") or 0) <= hi]
        if not sliced:
            raise HTTPException(400, "That scene stretch isn’t in this storyboard.")
        present = {int(b.get("index") or 0) for b in sliced}
        gaps = [i for i in range(lo, hi + 1) if i not in present]
        if gaps:
            raise HTTPException(
                400,
                "That stretch has gaps in the storyboard — pick a continuous run of scenes.",
            )
        beats = sliced
    missing = [
        str(int(b.get("index") or 0)).zfill(3)
        for b in beats if not _storyboard_beat_has_still(b)
    ]
    if missing:
        label = ", ".join(missing[:8])
        more = f" (+{len(missing) - 8} more)" if len(missing) > 8 else ""
        raise HTTPException(
            400,
            f"Still waiting on scene{'s' if len(missing) != 1 else ''} {label}{more}.",
        )
    return beats


async def _save_assemble_uploads(
    files: list[UploadFile],
    dest_dir: Path,
) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for f in files or []:
        name = Path(f.filename or "clip.bin").name
        dest = dest_dir / name
        with open(dest, "wb") as out:
            out.write(await f.read())
        if dest.is_file() and dest.stat().st_size > 100:
            saved.append(dest)
    return saved


@app.post("/api/storyboard/jobs/{job_id}/assemble/match")
async def match_storyboard_assemble(
    job_id: str,
    clips: list[UploadFile] = File(default=[]),
    admin: dict = Depends(require_admin),
):
    """Upload I2V clips (or a zip) and preview filename/pHash matching — no stitch yet."""
    from core.storyboard_assemble import (
        extract_clips_from_uploads,
        load_pack_beats,
        match_clips_to_beats,
        parse_clip_index,
    )

    job = _storyboard_pack_job_or_404(job_id, admin)
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    if not (result.get("zip_ready") or result.get("beats") or job.get("status") == "complete"):
        raise HTTPException(400, "Finish the storyboard pack first, then assemble.")

    stamp = int(time.time())
    stage = OUTPUT_DIR / "storyboard_assemble_uploads" / str(admin["id"]) / f"{job_id}_{stamp}"
    saved = await _save_assemble_uploads(clips, stage)
    if not saved:
        raise HTTPException(400, "Upload at least one .mp4/.webm/.mov clip or a zip of clips.")

    ready_dir = stage / "clips"
    clip_paths = extract_clips_from_uploads(saved, ready_dir)
    if not clip_paths:
        raise HTTPException(400, "No video clips found in the upload.")

    pack_dir = Path(result.get("pack_dir") or "")
    if not pack_dir.is_dir():
        pack_dir = None
    beats = load_pack_beats(pack_dir=pack_dir, beats=result.get("beats") or [])
    matched = match_clips_to_beats(
        clip_paths, beats, pack_dir=pack_dir, work_dir=stage / "match",
    )
    # Also surface unmatched uploads for the UI
    matched_names = {Path(m["clip"]).name for m in matched}
    unmatched = [
        {
            "filename": c.name,
            "guess_index": parse_clip_index(c.name),
        }
        for c in clip_paths
        if c.name not in matched_names
    ]
    # Persist staging id for assemble start
    staging_id = f"{job_id}_{stamp}"
    meta_path = stage / "staging.json"
    meta_path.write_text(json.dumps({
        "staging_id": staging_id,
        "clips_dir": str(ready_dir),
        "parent_job_id": job_id,
        "user_id": admin["id"],
        "match_report": matched,
    }), encoding="utf-8")

    return {
        "staging_id": staging_id,
        "matched": [
            {
                "index": m["index"],
                "filename": Path(m["clip"]).name,
                "method": m.get("method"),
                "confidence": m.get("confidence"),
                "dialogue": (m.get("dialogue") or "")[:120],
            }
            for m in matched
        ],
        "unmatched": unmatched,
        "beat_count": len(beats),
        "matched_count": len(matched),
    }


@app.post("/api/storyboard/jobs/{job_id}/assemble")
async def start_storyboard_assemble(
    job_id: str,
    staging_id: str = Form(""),
    burn_captions: str = Form("1"),
    add_music: str = Form("1"),
    notify_email: str = Form(""),
    clips: list[UploadFile] = File(default=[]),
    admin: dict = Depends(require_admin),
):
    """Start assemble cook from a prior match staging_id and/or fresh clip uploads."""
    from core.storyboard_assemble import extract_clips_from_uploads, load_pack_beats

    job = _storyboard_pack_job_or_404(job_id, admin)
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    _require_storyboard_stills_ready(job)

    clips_dir: Path | None = None
    sid = (staging_id or "").strip()
    if sid:
        stage = OUTPUT_DIR / "storyboard_assemble_uploads" / str(admin["id"]) / sid
        meta_path = stage / "staging.json"
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                cand = Path(meta.get("clips_dir") or "")
                if cand.is_dir():
                    clips_dir = cand
            except Exception:
                pass

    if clips:
        stamp = int(time.time())
        stage = OUTPUT_DIR / "storyboard_assemble_uploads" / str(admin["id"]) / f"{job_id}_{stamp}"
        saved = await _save_assemble_uploads(clips, stage)
        ready = extract_clips_from_uploads(saved, stage / "clips")
        if ready:
            clips_dir = stage / "clips"
            sid = f"{job_id}_{stamp}"

    if not clips_dir or not clips_dir.is_dir() or not any(clips_dir.iterdir()):
        raise HTTPException(
            400,
            "Upload your I2V clips (or run Match first), then assemble.",
        )

    # Zip + upload clips so Fly cooks can fetch them (local paths die with the web dyno).
    clips_zip_local = clips_dir.parent / "clips_bundle.zip"
    clips_zip_url = ""
    try:
        import zipfile as _zf
        with _zf.ZipFile(clips_zip_local, "w", _zf.ZIP_DEFLATED) as zf:
            for p in sorted(clips_dir.iterdir()):
                if p.is_file() and p.suffix.lower() in (".mp4", ".webm", ".mov", ".mkv", ".m4v"):
                    zf.write(p, arcname=p.name)
        if clips_zip_local.is_file() and clips_zip_local.stat().st_size > 200:
            from webapp import storage as _storage
            clips_zip_url = _storage.store_file(
                str(clips_zip_local),
                f"storyboard/{admin['id']}/assemble_clips_{int(time.time())}_{job_id}.zip",
            )
    except Exception as e:
        print(f"[sb-assemble] clips zip upload failed: {e}")
        if not COOK_ON_WEB:
            raise HTTPException(
                500,
                "Could not stage clips for assemble. Check storage and try again.",
            )

    pack_dir = Path(result.get("pack_dir") or "")
    if not pack_dir.is_dir():
        pack_dir_s = ""
    else:
        pack_dir_s = str(pack_dir)

    beats = load_pack_beats(
        pack_dir=Path(pack_dir_s) if pack_dir_s else None,
        beats=result.get("beats") or [],
    )
    title = (
        (result.get("title") or "")
        or ((job.get("request") or {}).get("title") or "")
        or "Storyboard"
    ).strip()

    assemble_id = str(uuid.uuid4())
    req_payload = {
        "recipe": "storyboard_assemble",
        "parent_job_id": job_id,
        "title": title,
        "clips_dir": str(clips_dir),
        "clips_zip_url": clips_zip_url or "",
        "pack_dir": pack_dir_s,
        "beats": beats,
        "burn_captions": (burn_captions or "1").strip() not in ("0", "false", "no"),
        "add_music": (add_music or "1").strip() not in ("0", "false", "no"),
        "staging_id": sid,
        "credits_charged": 0,
        "is_admin": True,
        "notify_email": (notify_email or "").strip() or (admin.get("email") or ""),
    }
    try:
        create_cook_job(
            job_id=assemble_id,
            user_id=admin["id"],
            recipe="storyboard_assemble",
            title=f"Assemble · {title}"[:120],
            request_json=json.dumps(req_payload),
            credit_deducted=False,
            lite_mode=False,
            status="web_queued" if COOK_ON_WEB else "queued",
        )
    except Exception as e:
        print(f"[sb-assemble] create_cook_job failed: {e}")
        raise HTTPException(500, "Could not queue assemble.")

    ajob = {
        "status": "queued",
        "progress": [{"time": time.time(), "message": "Queued — cooking soon…", "phase": "queued"}],
        "result": None,
        "request": req_payload,
        "user_id": admin["id"],
        "credit_deducted": False,
        "lite_mode": False,
        "error": "",
        "created_at": time.time(),
    }
    _jobs[assemble_id] = ajob

    if COOK_ON_WEB:
        job_queue.enqueue(assemble_id)
    else:
        if COOK_ON_FLY:
            try:
                from webapp.fly_bridge import spawn_cook as fly_spawn
                if fly_spawn(assemble_id):
                    ajob["progress"].append({
                        "time": time.time(),
                        "message": "Starting your cook…",
                        "phase": "queued",
                    })
                else:
                    print(f"[sb-assemble] Fly spawn failed for {assemble_id}")
            except Exception as e:
                print(f"[sb-assemble] Fly bridge error: {e}")
        try:
            update_cook_job(assemble_id, progress_json=json.dumps(ajob["progress"]), status="queued")
        except Exception:
            pass

    track(admin["id"], "storyboard_assemble_queued", {
        "job_id": assemble_id,
        "parent_job_id": job_id,
        "clip_count": len(list(clips_dir.iterdir())),
    })
    return {
        "job_id": assemble_id,
        "parent_job_id": job_id,
        "status": "queued",
        "staging_id": sid,
    }


def _storyboard_animate_credit_cost(target_minutes: float, *, byok: bool = False) -> int:
    """Placeholder credit math — 0 while admin-testing; knobs in config for pricing pass."""
    import math
    per_min = float(getattr(config, "STORYBOARD_ANIMATE_CREDITS_PER_MIN", 0) or 0)
    floor = int(getattr(config, "STORYBOARD_ANIMATE_CREDITS_MIN", 0) or 0)
    if per_min <= 0 and floor <= 0:
        return 0
    raw = max(floor, int(math.ceil(max(target_minutes, 0.5) * per_min)))
    if byok:
        return max(0, min(raw, max(1, int(math.ceil(max(target_minutes, 0.5) / 4.0)))))
    return raw


@app.post("/api/storyboard/jobs/{job_id}/animate")
async def start_storyboard_animate(
    job_id: str,
    burn_captions: str = Form("1"),
    add_music: str = Form("1"),
    notify_email: str = Form(""),
    beat_from: str = Form(""),
    beat_to: str = Form(""),
    admin: dict = Depends(require_admin),
):
    """Queue on-site Seedance I2V + assemble cook from a finished pack."""
    from webapp.database import get_user_atlas_key, deduct_credits

    job = _storyboard_pack_job_or_404(job_id, admin)
    result = job.get("result") if isinstance(job.get("result"), dict) else {}

    range_from = range_to = None
    bf = (beat_from or "").strip()
    bt = (beat_to or "").strip()
    if bf and bt:
        try:
            range_from = int(bf)
            range_to = int(bt)
        except (TypeError, ValueError):
            raise HTTPException(400, "Scene stretch must be whole scene numbers.")

    beats = _require_storyboard_stills_ready(
        job, beat_from=range_from, beat_to=range_to,
    )
    # Full beat list (for credit scaling) — may include unfinished scenes
    from core.storyboard_assemble import load_pack_beats as _load_beats
    _pack = Path((result.get("pack_dir") or ""))
    all_beats = _load_beats(
        pack_dir=_pack if _pack.is_dir() else None,
        beats=result.get("beats") or [],
    ) or beats

    pack_dir = Path(result.get("pack_dir") or "")
    pack_dir_s = str(pack_dir) if pack_dir.is_dir() else ""

    title = (
        (result.get("title") or "")
        or ((job.get("request") or {}).get("title") or "")
        or "Storyboard"
    ).strip()
    try:
        target_minutes = float(
            result.get("target_minutes")
            or (job.get("request") or {}).get("target_minutes")
            or 8
        )
    except (TypeError, ValueError):
        target_minutes = 8.0
    pack_mode = str(result.get("pack_mode") or (job.get("request") or {}).get("pack_mode") or "full").lower()
    if pack_mode == "preview":
        target_minutes = min(target_minutes, 1.2)
    if range_from is not None and all_beats and len(beats) < len(all_beats):
        target_minutes = max(0.5, round(target_minutes * len(beats) / max(1, len(all_beats)), 1))

    byok = False
    try:
        byok = bool(get_user_atlas_key(int(admin["id"])))
    except Exception:
        byok = False
    credit_cost = _storyboard_animate_credit_cost(target_minutes, byok=byok)
    credit_deducted = False
    if credit_cost > 0:
        if not deduct_credits(admin["id"], credit_cost):
            raise HTTPException(
                402,
                f"Need {credit_cost} credit{'s' if credit_cost != 1 else ''} to generate on ChannelRecipe.",
            )
        credit_deducted = True

    animate_id = str(uuid.uuid4())
    req_payload = {
        "recipe": "storyboard_animate",
        "parent_job_id": job_id,
        "title": title,
        "pack_dir": pack_dir_s,
        "beats": beats,
        "beat_from": range_from,
        "beat_to": range_to,
        "burn_captions": (burn_captions or "1").strip() not in ("0", "false", "no"),
        "add_music": (add_music or "1").strip() not in ("0", "false", "no"),
        "credits_charged": credit_cost,
        "is_admin": True,
        "notify_email": (notify_email or "").strip() or (admin.get("email") or ""),
        "target_minutes": target_minutes,
    }
    try:
        stretch = ""
        if range_from is not None and range_to is not None:
            stretch = f" · {min(range_from, range_to):03d}–{max(range_from, range_to):03d}"
        create_cook_job(
            job_id=animate_id,
            user_id=admin["id"],
            recipe="storyboard_animate",
            title=f"Animate · {title}{stretch}"[:120],
            request_json=json.dumps(req_payload),
            credit_deducted=credit_deducted,
            lite_mode=False,
            status="web_queued" if COOK_ON_WEB else "queued",
        )
    except Exception as e:
        if credit_deducted:
            try:
                from webapp.database import refund_credits
                refund_credits(admin["id"], credit_cost)
            except Exception:
                pass
        print(f"[sb-animate] create_cook_job failed: {e}")
        raise HTTPException(500, "Could not queue on-site generate.")

    ajob = {
        "status": "queued",
        "progress": [{"time": time.time(), "message": "Queued — cooking soon…", "phase": "queued"}],
        "result": None,
        "request": req_payload,
        "user_id": admin["id"],
        "credit_deducted": credit_deducted,
        "lite_mode": False,
        "error": "",
        "created_at": time.time(),
    }
    _jobs[animate_id] = ajob

    if COOK_ON_WEB:
        job_queue.enqueue(animate_id)
    else:
        if COOK_ON_FLY:
            try:
                from webapp.fly_bridge import spawn_cook as fly_spawn
                if fly_spawn(animate_id):
                    ajob["progress"].append({
                        "time": time.time(),
                        "message": "Starting your cook…",
                        "phase": "queued",
                    })
                else:
                    print(f"[sb-animate] Fly spawn failed for {animate_id}")
            except Exception as e:
                print(f"[sb-animate] Fly bridge error: {e}")
        try:
            update_cook_job(animate_id, progress_json=json.dumps(ajob["progress"]), status="queued")
        except Exception:
            pass

    track(admin["id"], "storyboard_animate_queued", {
        "job_id": animate_id,
        "parent_job_id": job_id,
        "beat_count": len(beats),
        "credits": credit_cost,
    })
    return {
        "job_id": animate_id,
        "parent_job_id": job_id,
        "status": "queued",
        "credits_charged": credit_cost,
        "beat_count": len(beats),
    }


@app.get("/api/storyboard/animate-cost")
async def storyboard_animate_cost(
    minutes: float = 8,
    admin: dict = Depends(require_admin),
):
    """Preview credit cost for on-site animate (UI blurb)."""
    from webapp.database import get_user_atlas_key
    byok = False
    try:
        byok = bool(get_user_atlas_key(int(admin["id"])))
    except Exception:
        byok = False
    cost = _storyboard_animate_credit_cost(float(minutes or 8), byok=byok)
    return {"credits": cost, "byok": byok, "minutes": float(minutes or 8)}

@app.post("/api/internal/niche-finder/cron")
def niche_finder_cron(
    req: NicheFinderJobRequest | None = None,
    authorization: str | None = Header(default=None),
):
    """
    Scheduled library refresh (1–2×/day). Auth: Authorization: Bearer <CRON_SECRET>.

    Example:
      curl -X POST https://channelrecipe.com/api/internal/niche-finder/cron \\
        -H "Authorization: Bearer $CRON_SECRET" \\
        -H "Content-Type: application/json" \\
        -d '{}'
    """
    secret = getattr(config, "CRON_SECRET", "") or ""
    if not secret:
        raise HTTPException(503, "CRON_SECRET not configured.")
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token or token != secret:
        raise HTTPException(401, "Invalid cron secret.")
    if not config.YOUTUBE_API_KEY:
        raise HTTPException(400, "YouTube API key not configured.")

    body = req or NicheFinderJobRequest()
    job_id = _start_niche_hunt(
        keywords=body.keywords or [],
        max_per_keyword=body.max_per_keyword or 12,
        max_channels=body.max_channels or 60,
        min_recent_avg_views=body.min_recent_avg_views or 0,
        max_subscribers=body.max_subscribers or 150_000,
        scroll_count=body.scroll_count or 20,
        max_video_age_days=body.max_video_age_days or 180,
        trigger="cron",
        user_id=None,
    )
    return {"job_id": job_id, "status": "running", "trigger": "cron"}


# ---------------------------------------------------------------------------
# Niche Screener
# ---------------------------------------------------------------------------
@app.post("/api/niche/analyze")
def analyze_niche(req: NicheAnalyzeRequest, user: dict = Depends(require_user)):
    try:
        from core.video_analyzer import analyze_video, NicheAnalysisUnavailable
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
    except NicheAnalysisUnavailable as e:
        raise HTTPException(503, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:
        err = str(e)
        if "MIME type" in err or "text/html" in err:
            raise HTTPException(400, "Could not analyze this video. Make sure the URL is a public YouTube video (not a channel, playlist, or private video).") from None
        if any(x in err for x in ("PERMISSION_DENIED", "denied access", "403")):
            raise HTTPException(
                503,
                "Niche video analysis is temporarily unavailable (Google Gemini access denied). "
                "Pick a recipe manually for now — cooking still works.",
            ) from None
        raise HTTPException(500, "Niche analysis failed. Please try a different video URL.") from None


# ---------------------------------------------------------------------------
# Recipe Brain
# ---------------------------------------------------------------------------
class BrainChatRequest(BaseModel):
    messages: list[dict] = []


@app.post("/api/brain/starter")
def brain_starter(user: dict = Depends(require_user)):
    """Always available — returns the curated 20-mistakes starter pack."""
    try:
        from core.recipe_brain import starter_pack
        return starter_pack()
    except Exception as e:
        raise HTTPException(500, f"Could not load starter pack: {e}")


@app.post("/api/brain/chat")
def brain_chat(req: BrainChatRequest, user: dict = Depends(require_user)):
    """Gated chat — 503 Coming Soon unless RECIPE_BRAIN_ENABLED=1."""
    if not getattr(config, "RECIPE_BRAIN_ENABLED", False):
        raise HTTPException(
            503,
            "Recipe Brain chat is Coming Soon. Use the starter pack for now.",
        )
    try:
        from core.recipe_brain import chat as brain_chat_fn
        return brain_chat_fn(req.messages or [])
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(500, f"Recipe Brain chat failed: {e}")


# ---------------------------------------------------------------------------
# Sauce / free resources (account to download, no paid plan)
# Paths avoid "/api/resources" — some ad blockers swallow that URL pattern.
# ---------------------------------------------------------------------------
def _sauce_catalog_payload() -> dict:
    from webapp.resources_catalog import any_new_resources, list_resources
    items = list_resources()
    return {"resources": items, "has_new": any_new_resources()}


def _sauce_file_response(resource_id: str):
    from webapp.resources_catalog import get_resource, resource_file_path
    item = get_resource(resource_id)
    if not item:
        raise HTTPException(404, "Resource not found")
    path = resource_file_path(item)
    if not path.is_file():
        raise HTTPException(404, "Resource file missing")
    download_name = item.get("download_name") or path.name
    return FileResponse(
        path,
        filename=download_name,
        media_type="text/plain; charset=utf-8",
    )


@app.get("/api/sauce")
def api_list_sauce():
    """Public catalog. Download still requires a signed-in account."""
    return _sauce_catalog_payload()


@app.get("/api/sauce/{resource_id}/download")
def api_download_sauce(resource_id: str, user: dict = Depends(require_user)):
    """Signed-in account only — no card, trial, or active plan required."""
    return _sauce_file_response(resource_id)


# Back-compat aliases (may be blocked by ad blockers in some browsers)
@app.get("/api/resources")
def api_list_resources_legacy():
    return _sauce_catalog_payload()


@app.get("/api/resources/{resource_id}/download")
def api_download_resource_legacy(resource_id: str, user: dict = Depends(require_user)):
    return _sauce_file_response(resource_id)


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
    video_url = v.get("video_url") or ""
    thumb_url = v.get("thumbnail_url") or ""
    # Presign only — do NOT ACL every object on list (that made Refresh feel stuck).
    try:
        from webapp import storage as _storage
        if video_url:
            video_url = _storage.playable_url(video_url, ensure_public=False)
        if thumb_url:
            thumb_url = _storage.playable_url(thumb_url, ensure_public=False)
    except Exception as e:
        print(f"[media] playable_url rewrite failed: {e}")
    return {
        "id": v.get("id"),
        "type": "video",
        "title": v.get("title") or "Untitled",
        "recipe": v.get("recipe") or "",
        "url": video_url,
        "thumbnail_url": thumb_url,
        "description": v.get("description") or "",
        "tags": tags,
        "hashtags": hashtags,
        "timestamp": float(v.get("created_at") or now) * 1000,
        "expires_in_days": expires_in_days,
        "expired": age > retention_secs,
    }


@app.get("/api/media/playable")
async def media_playable(url: str = "", user: dict = Depends(require_user)):
    """Return a browser-playable URL for a Spaces object (ACL + presign)."""
    if not url or not url.startswith("http"):
        raise HTTPException(status_code=400, detail="url required")
    from webapp import storage as _storage
    try:
        playable = _storage.playable_url(url)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)[:300]) from e
    return {"url": playable}


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
# User integrations (BYOK — HeyGen)
# ---------------------------------------------------------------------------
_HEYGEN_GUIDE = {
    "title": "Connect HeyGen",
    "steps": [
        "Create a free account or sign in at app.heygen.com",
        "Open Settings → API (or Account → API token) and create an API key",
        "Copy the key and paste it below, then Save & test",
        "Optional: in HeyGen, copy an Avatar ID or Voice ID if you prefer pasting over the grid",
    ],
    "docs_url": "https://docs.heygen.com/docs/quick-start",
    "app_url": "https://app.heygen.com",
}


@app.get("/api/me/integrations")
async def get_my_integrations(user: dict = Depends(require_user)):
    byok = _is_byok_email(user.get("email", ""))
    payload = {
        "heygen": user_heygen_status(user["id"]),
        "byok_enabled": byok,
        "guide": {"heygen": _HEYGEN_GUIDE},
    }
    if byok:
        payload["atlas"] = user_atlas_status(user["id"])
    return payload


class AtlasKeyRequest(BaseModel):
    api_key: str = ""
    test: bool = True


def _require_byok_user(user: dict) -> None:
    if not _is_byok_email(user.get("email", "")):
        raise HTTPException(403, "Atlas BYOK is not enabled for this account.")


def _sanitize_atlas_key(raw: str) -> str:
    """Strip paste junk (Bearer prefix, quotes, zero-width chars, whitespace)."""
    key = (raw or "").strip()
    # Common copy/paste artifacts
    for ch in ("\u200b", "\u200c", "\u200d", "\ufeff", "\xa0"):
        key = key.replace(ch, "")
    key = key.strip().strip('"').strip("'")
    if key.lower().startswith("bearer "):
        key = key[7:].strip()
    # Collapse accidental newlines/spaces from password managers
    key = "".join(key.split())
    return key


def _test_atlas_key(key: str) -> tuple[bool, str]:
    """
    Auth-only probe. Do NOT require a successful generation:
    - 401/403 => invalid key
    - 200 / 402 (no balance) / 400 / 422 / 429 => key is accepted
    New Atlas accounts often have 0 balance (402) which used to false-fail Save & test.
    """
    import httpx

    key = _sanitize_atlas_key(key)
    if not key or len(key) < 16:
        return False, "That doesn’t look like a full Atlas API key. Paste the whole key."

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    def _classify(status: int, body: str) -> tuple[bool, str] | None:
        snippet = (body or "").strip().replace("\n", " ")[:180]
        if status in (401, 403):
            return False, snippet or "Atlas says this API key is invalid."
        # Valid key: success, empty wallet, bad payload, or rate limit
        if status in (200, 201, 400, 402, 422, 429):
            if status == 402:
                return True, "Key works — Atlas balance looks empty. Top up at atlascloud.ai/console/billing before cooking."
            return True, ""
        return None

    try:
        # 1) Media API (what voice/cook actually use) — empty body → 400 if key ok
        r = httpx.post(
            "https://api.atlascloud.ai/api/v1/model/generateAudio",
            headers=headers,
            json={},
            timeout=25,
        )
        classified = _classify(r.status_code, r.text or "")
        if classified is not None:
            return classified

        # 2) LLM chat as fallback (model/balance issues must not fail auth)
        r2 = httpx.post(
            "https://api.atlascloud.ai/v1/chat/completions",
            headers=headers,
            json={
                "model": getattr(config, "ATLAS_TEXT_MODEL", "google/gemini-3.1-flash-lite"),
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 4,
            },
            timeout=30,
        )
        classified = _classify(r2.status_code, r2.text or "")
        if classified is not None:
            return classified
        return False, f"Atlas returned HTTP {r2.status_code}: {(r2.text or '')[:160]}"
    except Exception as e:
        return False, f"Could not reach Atlas to verify the key ({e}). Try again in a moment."


@app.post("/api/me/integrations/atlas")
def save_atlas_key(req: AtlasKeyRequest, user: dict = Depends(require_user)):
    _require_byok_user(user)
    key = _sanitize_atlas_key(req.api_key or "")
    if not key:
        raise HTTPException(400, "Paste your Atlas Cloud API key.")
    warning = ""
    if req.test:
        ok, detail = _test_atlas_key(key)
        if not ok:
            raise HTTPException(
                400,
                detail or "Atlas rejected that key. Copy the full API key from atlascloud.ai/console/api-keys.",
            )
        warning = detail or ""
    set_user_atlas_key(user["id"], key)
    track(user["id"], "atlas_byok_connected", {})
    out = {"ok": True, "atlas": user_atlas_status(user["id"])}
    if warning:
        out["warning"] = warning
    return out


@app.delete("/api/me/integrations/atlas")
async def delete_atlas_key(user: dict = Depends(require_user)):
    _require_byok_user(user)
    set_user_atlas_key(user["id"], None)
    return {"ok": True, "atlas": {"configured": False, "last4": ""}}


@app.post("/api/me/integrations/atlas/test")
def test_atlas_user_key(req: AtlasKeyRequest, user: dict = Depends(require_user)):
    _require_byok_user(user)
    key = _sanitize_atlas_key(req.api_key or "") or (get_user_atlas_key(user["id"]) or "")
    if not key:
        return {"ok": False, "error": "No Atlas key to test. Paste one first."}
    ok, detail = _test_atlas_key(key)
    return {"ok": ok, "error": "" if ok else (detail or "Atlas rejected that key."), "warning": detail if ok and detail else ""}


@app.post("/api/me/integrations/heygen")
def save_heygen_key(req: HeyGenKeyRequest, user: dict = Depends(require_user)):
    key = (req.api_key or "").strip()
    if not key:
        raise HTTPException(400, "Paste your HeyGen API key.")
    if req.test:
        from core.heygen import test_api_key
        if not test_api_key(key):
            raise HTTPException(
                400,
                "HeyGen rejected that key. Double-check you copied the full API token from app.heygen.com → Settings → API.",
            )
    set_user_heygen_key(user["id"], key)
    track(user["id"], "heygen_connected", {})
    return {"ok": True, "heygen": user_heygen_status(user["id"])}


@app.delete("/api/me/integrations/heygen")
async def delete_heygen_key(user: dict = Depends(require_user)):
    set_user_heygen_key(user["id"], None)
    return {"ok": True, "heygen": {"configured": False, "last4": ""}}


@app.post("/api/me/integrations/heygen/test")
def test_heygen_key(req: HeyGenKeyRequest, user: dict = Depends(require_user)):
    from core.heygen import test_api_key
    key = (req.api_key or "").strip() or (get_user_heygen_key(user["id"]) or "")
    if not key:
        return {"ok": False, "error": "No HeyGen key to test. Paste one first."}
    ok = test_api_key(key)
    return {"ok": ok, "error": "" if ok else "HeyGen rejected that key."}


def _user_heygen_or_400(user: dict) -> str:
    key = get_user_heygen_key(user["id"])
    if not key:
        raise HTTPException(
            400,
            "Connect your HeyGen API key in Settings → Integrations first.",
        )
    return key


@app.get("/api/heygen/avatars")
def heygen_avatars(user: dict = Depends(require_user)):
    from core.heygen import list_avatars
    key = _user_heygen_or_400(user)
    try:
        avatars = list_avatars(api_key=key)
    except Exception as e:
        raise HTTPException(502, f"Could not load HeyGen avatars: {e}")
    return {"avatars": avatars, "guide": _HEYGEN_GUIDE}


@app.get("/api/heygen/voices")
def heygen_voices(user: dict = Depends(require_user)):
    from core.heygen import list_voices
    key = _user_heygen_or_400(user)
    try:
        voices = list_voices(api_key=key)
    except Exception as e:
        raise HTTPException(502, f"Could not load HeyGen voices: {e}")
    return {"voices": voices, "guide": _HEYGEN_GUIDE}


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


class CreditGrantRequest(BaseModel):
    email: str
    amount: int = 1
    reason: str = ""


@app.post("/api/admin/credits")
async def admin_grant_credits(req: CreditGrantRequest, admin: dict = Depends(require_admin)):
    """Support: grant cook credits to a user by email."""
    email = (req.email or "").lower().strip()
    amount = int(req.amount or 0)
    if not email or "@" not in email:
        raise HTTPException(400, "Valid email required")
    if amount < 1 or amount > 100:
        raise HTTPException(400, "amount must be 1–100")
    user = get_user_by_email(email)
    if not user:
        raise HTTPException(404, f"No user with email {email}")
    add_credits(user["id"], amount)
    updated = get_user_by_id(user["id"])
    print(
        f"[admin] {admin.get('email')} granted +{amount} credits to {email} "
        f"(now {updated['credits']}) reason={req.reason!r}"
    )
    return {"ok": True, "email": email, "granted": amount, "credits": updated["credits"]}


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
            client.models.generate_content(model=config.GEMINI_TEXT_MODEL, contents="Say hi")
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
            import httpx
            r = httpx.post(
                "https://api.atlascloud.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {key_val}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": getattr(config, "ATLAS_TEXT_MODEL", "google/gemini-3.1-flash-lite"),
                    "messages": [{"role": "user", "content": "Say hi"}],
                    "max_tokens": 8,
                },
                timeout=30,
            )
            if r.status_code >= 400:
                return {"ok": False, "error": r.text[:200]}
            return {"ok": True}

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
