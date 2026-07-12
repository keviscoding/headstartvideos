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
                # FastAPI HTTPException — only keep unexpected 5xx
                try:
                    from fastapi import HTTPException as _HTTPExc
                    if isinstance(exc, _HTTPExc) and exc.status_code < 500:
                        return None
                except Exception:
                    pass
                msg = str(exc).lower()
                # Provider out-of-credit / bad user input — ops issue, not a bug
                if any(s in msg for s in (
                    "insufficient balance",
                    "no youtube channel found",
                    "could not extract channel",
                    "provider balance",
                )):
                    return None
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
    get_user_by_sub_id, deduct_credit, refund_credit, add_credits,
    create_verify_code, verify_code,
    create_session, get_session_user, delete_session,
    log_render_event, render_stats, backend_name, cleanup_expired,
    create_video, list_videos, get_video, update_video_kit, delete_video,
    create_cook_job, update_cook_job, get_cook_job,
    cook_queue_stats, announce_queued_jobs,
    set_user_heygen_key, get_user_heygen_key, user_heygen_status,
)
from webapp import storage
from webapp import job_queue
from webapp.cook_runner import run_cook_job, hydrate_job_from_row
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
    return {
        "id": u["id"],
        "email": u["email"],
        "plan": "pro" if is_admin else u["plan"],
        "credits": u["credits"],
        "created_at": u["created_at"],
        "is_admin": is_admin,
        "trial_used": bool(u.get("trial_used")),
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
                track(user_id, "topup_completed", {"credits": int(topup)})
            else:
                plan_key = meta.get("plan", "starter_monthly")
                skip_trial = meta.get("skip_trial") == "1"
                if skip_trial:
                    # Returning customer — charge immediately, full credits
                    plan_label = "daily" if "daily" in plan_key else "starter"
                    credits = _PLAN_CREDITS.get(plan_key, 15)
                    update_user(int(user_id), plan=plan_label, credits=credits,
                                stripe_sub_id=obj.get("subscription", ""),
                                trial_used=1)
                    print(f"[stripe] User {user_id} subscribed (no trial) → {plan_label} ({credits} credits)")
                    identify_user(user_id, {"plan": plan_label, "credits": credits, "trial_used": True})
                    track(user_id, "subscription_started", {
                        "plan": plan_label, "plan_key": plan_key, "credits": credits, "had_trial": False,
                    })
                else:
                    plan_label = "daily_trial" if "daily" in plan_key else "starter_trial"
                    update_user(int(user_id), plan=plan_label, credits=3,
                                stripe_sub_id=obj.get("subscription", ""),
                                trial_used=1)
                    print(f"[stripe] User {user_id} started trial ({plan_label}, 3 credits)")
                    identify_user(user_id, {"plan": plan_label, "credits": 3, "trial_used": True})
                    track(user_id, "trial_started", {
                        "plan": plan_label, "plan_key": plan_key, "credits": 3,
                    })

    elif evt_type == "invoice.paid":
        sub_id = obj.get("subscription")
        amount_paid = obj.get("amount_paid", 0)
        if sub_id:
            row = get_user_by_sub_id(sub_id)
            if row:
                plan = row.get("plan", "starter")
                if plan in ("starter_trial", "daily_trial"):
                    if amount_paid == 0:
                        print(f"[stripe] Skipping $0 trial invoice for user {row['id']} (already has 3 trial credits)")
                    else:
                        new_plan = "daily" if "daily" in plan else "starter"
                        credits = 35 if new_plan == "daily" else 15
                        update_user(row["id"], plan=new_plan, credits=credits)
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
        sub_id = obj.get("id")
        if sub_id:
            row = get_user_by_sub_id(sub_id)
            if row:
                prev_plan = row.get("plan", "unknown")
                # Keep trial_used=1 so they cannot start another free trial
                update_user(row["id"], plan="free", credits=0, stripe_sub_id="")
                print(f"[stripe] Subscription deleted — user {row['id']} downgraded to free (trial_used preserved)")
                identify_user(row["id"], {"plan": "free", "credits": 0})
                track(row["id"], "subscription_canceled", {"from_plan": prev_plan})

    elif evt_type == "customer.subscription.updated":
        sub_id = obj.get("id")
        status = obj.get("status")
        if sub_id and status in ("canceled", "unpaid"):
            row = get_user_by_sub_id(sub_id)
            if row and row.get("plan") not in ("starter_trial", "daily_trial"):
                prev_plan = row.get("plan", "unknown")
                update_user(row["id"], plan="free", credits=0)
                print(f"[stripe] Subscription {status} — user {row['id']} downgraded to free")
                identify_user(row["id"], {"plan": "free", "credits": 0})
                track(row["id"], "subscription_canceled", {"from_plan": prev_plan, "status": status})
            elif row:
                print(f"[stripe] Ignoring {status} for trial user {row['id']} (handled by end-trial endpoint)")
        elif sub_id and status == "active":
            # Safety net: if end-trial or day-7 conversion left plan as *_trial, fix it
            row = get_user_by_sub_id(sub_id)
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
    topic_hint = f"\nTopic hint from user: {req.topic}" if req.topic else ""

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
    # ~1.4 tokens/word + headroom so thinking tokens don't starve the script body.
    max_tokens = max(4096, min(32768, int(word_target * 2.5) + 2048))

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
    if "insufficient balance" in msg or "provider balance" in msg or "temporarily unavailable" in msg:
        return 503
    if "image_other" in msg or "no parts found" in msg or "no_image" in msg:
        return 503
    if "thumbnail generation failed after retries" in msg:
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


@app.post("/api/voiceover")
def generate_voiceover(req: VoiceoverRequest, user: dict = Depends(require_user)):
    from core.voiceover_gen import generate_voiceover as gen_vo

    out_dir = str(OUTPUT_DIR / "voiceovers" / str(int(time.time())))
    try:
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
        out_dir = OUTPUT_DIR / "voiceovers" / str(int(time.time()))
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


@app.post("/api/voiceover/studio")
def voiceover_studio(req: VoiceoverStudioRequest, user: dict = Depends(require_user)):
    from core.voiceover_gen import generate_voiceover as gen_vo

    if not (req.script or "").strip():
        raise HTTPException(400, "Paste a script first, then generate the voiceover.")

    out_dir = str(OUTPUT_DIR / "voiceovers" / str(int(time.time())))
    try:
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

    credit_deducted = False
    if not is_admin:
        if not deduct_credit(user_id):
            raise HTTPException(402, "No credits remaining. Upgrade your plan for more videos.")
        credit_deducted = True

    job_id = str(uuid.uuid4())
    lite_mode = (not is_admin) and user.get("plan") in ("starter_trial", "daily_trial", "free")
    _jobs[job_id] = {
        "status": "queued",
        "progress": [],
        "result": None,
        "request": req.model_dump(),
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
            request_json=json.dumps(req.model_dump()),
            credit_deducted=credit_deducted,
            lite_mode=lite_mode,
            # Workers only claim status=queued. web_queued = in-process only.
            status="queued" if not COOK_ON_WEB else "web_queued",
        )
    except Exception as e:
        print(f"[build] create_cook_job failed: {e}")
        if credit_deducted:
            refund_credit(user_id)
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
        refund_credit(job["user_id"])
        job["credit_deducted"] = False
        print(f"[build] Refunded credit on cancel for user {job['user_id']} (queued={was_queued})")
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
@app.post("/api/channel/fetch")
def fetch_channel(req: ChannelFetchRequest, user: dict = Depends(require_user)):
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
    except ValueError as e:
        raise HTTPException(400, str(e))
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
# Niche Screener
# ---------------------------------------------------------------------------
@app.post("/api/niche/analyze")
def analyze_niche(req: NicheAnalyzeRequest, user: dict = Depends(require_user)):
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
    return {
        "heygen": user_heygen_status(user["id"]),
        "guide": {"heygen": _HEYGEN_GUIDE},
    }


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
