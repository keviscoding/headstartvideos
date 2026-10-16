"""Helpful emails at the moments that matter on the way to a first video (owner-approved, 2026-10-16).

- welcome:       right after a subscription that includes Studio starts
- install_nudge: a day later, only while Studio is still not connected (no download, connect or Studio call, no project)
- first_video:   the first time one of the customer's Studio videos finishes
- release:<n>:   what's new in Studio release n, sent only when an admin presses send (never automatic)

Each kind goes to a customer once (a row in lifecycle_emails is claimed before sending), never to someone who
unsubscribed, from noreply@ with replies going to hello@. The nudge and release emails skip anyone mailed in the last
20 hours. The nudge only considers subscriptions recorded after this shipped (the "subscribed" milestone), so it never
mails the existing customer list. Sending never raises into the request that caused it.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import os
import threading
import time

from webapp import database as db
from webapp import milestones
from webapp import plans

SUBSCRIBED = "subscribed"                       # milestone recorded by the Stripe webhook
APP_URL = "https://channelrecipe.com/app#local-studio"
SITE = "https://channelrecipe.com"
REPLY_TO = "hello@channelrecipe.com"
NUDGE_AFTER_S = 24 * 3600
NUDGE_WITHIN_S = 7 * 24 * 3600
QUIET_S = 20 * 3600

_ready: set = set()
_lock = threading.Lock()


def initialize() -> None:
    key = (db.IS_PG, db.DATABASE_URL if db.IS_PG else str(db.DB_PATH))
    if key in _ready:
        return
    with _lock:
        if key in _ready:
            return
        with db._conn() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS lifecycle_emails (
                user_id INTEGER NOT NULL, kind TEXT NOT NULL, sent_at DOUBLE PRECISION NOT NULL,
                PRIMARY KEY (user_id, kind))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS email_opt_outs (
                user_id INTEGER PRIMARY KEY, at DOUBLE PRECISION NOT NULL)""")
        _ready.add(key)


# ---- unsubscribe links --------------------------------------------------------------------------------------

def _link_key() -> bytes | None:
    base = (os.getenv("FRONTIER_SECRETS_KEY") or os.getenv("CRON_SECRET") or "").strip()
    return hashlib.sha256(("cr-unsubscribe:" + base).encode()).digest() if base else None


def unsubscribe_token(user_id: int) -> str:
    key = _link_key()
    return hmac.new(key, str(int(user_id)).encode(), hashlib.sha256).hexdigest()[:32] if key else ""


def unsubscribe_url(user_id: int) -> str:
    return f"{SITE}/email/unsubscribe?u={int(user_id)}&t={unsubscribe_token(user_id)}"


def opt_out(user_id: int, token: str) -> bool:
    """Record an unsubscribe from a signed link. False when the link isn't genuine."""
    expected = unsubscribe_token(user_id)
    if not expected or not hmac.compare_digest(expected, str(token or "")):
        return False
    initialize()
    with db._conn() as conn:
        conn.execute(db._q("INSERT INTO email_opt_outs (user_id, at) VALUES (?,?) ON CONFLICT(user_id) DO NOTHING"),
                     (int(user_id), time.time()))
    return True


def opted_out(user_id: int) -> bool:
    initialize()
    with db._conn() as conn:
        return conn.execute(db._q("SELECT 1 FROM email_opt_outs WHERE user_id=?"), (int(user_id),)).fetchone() is not None


# ---- sending ------------------------------------------------------------------------------------------------

def _claim(user_id: int, kind: str, *, quiet: bool) -> bool:
    """Reserve this email for this customer (once per kind); with quiet, not within QUIET_S of any other email."""
    initialize()
    now = time.time()
    with db._conn() as conn:
        if quiet:
            recent = conn.execute(db._q("SELECT 1 FROM lifecycle_emails WHERE user_id=? AND sent_at>?"),
                                  (int(user_id), now - QUIET_S)).fetchone()
            if recent:
                return False
        cur = conn.execute(db._q("INSERT INTO lifecycle_emails (user_id, kind, sent_at) VALUES (?,?,?) "
                                 "ON CONFLICT(user_id, kind) DO NOTHING"), (int(user_id), kind, now))
        return (cur.rowcount or 0) > 0


def _release_claim(user_id: int, kind: str) -> None:
    with db._conn() as conn:
        conn.execute(db._q("DELETE FROM lifecycle_emails WHERE user_id=? AND kind=?"), (int(user_id), kind))


def _page(heading: str, paragraphs: list[str], button: tuple[str, str] | None, user_id: int) -> tuple[str, str]:
    """(html, text) in the house style of the other ChannelRecipe emails."""
    e = lambda s: html.escape(s, quote=True)
    body = "".join(f'<p style="margin: 0 0 14px; font-size: 15px; line-height: 1.55; color: #45454E;">{p}</p>'
                   for p in paragraphs)
    cta = (f'<div style="margin: 22px 0 6px;"><a href="{e(button[1])}" style="display: inline-block; background: #6D5AE0; '
           f'color: #FFFFFF; text-decoration: none; padding: 12px 26px; border-radius: 8px; font-weight: 600; font-size: 15px;">'
           f'{e(button[0])}</a></div>') if button else ""
    footer = (f'You get this because you subscribed to ChannelRecipe. '
              f'<a href="{e(unsubscribe_url(user_id))}" style="color: #6E6E79;">Unsubscribe from these emails</a>. '
              f'Sign-in codes still arrive.')
    page = (f'<div style="font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', sans-serif; max-width: 520px; '
            f'margin: 0 auto; padding: 32px 24px;"><h1 style="font-size: 22px; font-weight: 700; color: #16161A; '
            f'margin: 0 0 18px;">{e(heading)}</h1>{body}{cta}<p style="margin-top: 30px; font-size: 12px; '
            f'line-height: 1.5; color: #8A8794;">{footer}</p></div>')
    plain = heading + "\n\n" + "\n\n".join(html.unescape(_strip(p)) for p in paragraphs)
    if button:
        plain += f"\n\n{button[0]}: {button[1]}"
    plain += f"\n\nUnsubscribe from these emails: {unsubscribe_url(user_id)}"
    return page, plain


def _strip(fragment: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", fragment)


def _send(user: dict, kind: str, subject: str, page: str, plain: str) -> bool:
    from webapp import email_service
    resend = email_service._get_client()
    if not resend or not user.get("email"):
        return False
    resend.Emails.send({
        "from": email_service.FROM_EMAIL, "to": [user["email"]], "reply_to": REPLY_TO,
        "subject": subject, "html": page, "text": plain,
        "headers": {"List-Unsubscribe": f"<{unsubscribe_url(user['id'])}>",
                    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"},
        "tags": [{"name": "lifecycle", "value": kind.split(":")[0]}],
    })
    print(f"[lifecycle] sent {kind} to user {user['id']}")
    return True


def deliver(user: dict, kind: str, subject: str, heading: str, paragraphs: list[str], button=None, *,
            quiet: bool = False) -> bool:
    """Send one lifecycle email at most once. Never raises."""
    try:
        if not user or not _link_key() or opted_out(user["id"]):
            return False
        if not _claim(user["id"], kind, quiet=quiet):
            return False
        page, plain = _page(heading, paragraphs, button, user["id"])
        try:
            if _send(user, kind, subject, page, plain):
                return True
        except Exception as e:                                      # noqa: BLE001
            print(f"[lifecycle] {kind} to user {user.get('id')} failed: {e}")
        _release_claim(user["id"], kind)                            # not sent: may be tried again later
    except Exception as e:                                          # noqa: BLE001
        print(f"[lifecycle] {kind} skipped: {e}")
    return False


def _later(fn, *args, **kwargs) -> None:
    threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True).start()


# ---- the emails ---------------------------------------------------------------------------------------------

def welcome(user: dict) -> bool:
    return deliver(user, "welcome", "Your Studio is ready to download", "Welcome to ChannelRecipe", [
        "Three steps to your first video:",
        "1. Download Studio (Mac or Windows) from your Studio page.<br>"
        "2. Open the folder in Claude Code and paste the install prompt from that page. It installs everything for you.<br>"
        "3. On the setup page press <b>Connect</b>, then pick a recipe and type a title.",
        "Stuck anywhere? Reply to this email or ask in the Discord. We answer every day."],
        ("Open your Studio page", APP_URL))


def install_nudge(user: dict) -> bool:
    return deliver(user, "install_nudge", "Need a hand installing Studio?", "You're one step from your first video", [
        "Studio isn't connected to your account yet.",
        "The quickest way: open the Studio folder in Claude Code and paste the prompt from your Studio page. "
        "Claude checks your computer and installs what's missing.",
        "If something went wrong, reply with what you see and we'll get you going."],
        ("Open your Studio page", APP_URL), quiet=True)


def first_video(user: dict, title: str) -> bool:
    return deliver(user, "first_video", "Your first video is done", "Your first video is done", [
        f"Nice work: “{html.escape(title or 'your video')}” is finished.",
        "Before you upload, open it in Studio's timeline to fix any scene, then export the thumbnail and description too.",
        "Looking for the next one? This week's fresh niches are on your Studio page."],
        ("See this week's niches", APP_URL))


def release(user: dict, entry: dict) -> bool:
    import re
    build, title = int(entry["build"]), str(entry.get("title") or "Fixes and improvements")
    # the first sentence of each of the first four items keeps the email short; the full notes are in Studio
    items = [re.sub(r"^(New|Improved|Fixed):\s*", "", str(i)).split(". ")[0].rstrip(".")
             for i in (entry.get("items") or [])][:4]
    listed = "".join(f"• {html.escape(i)}<br>" for i in items)
    return deliver(user, f"release:{build}", f"Studio {build} is out: {title}", f"What's new in Studio {build}", [
        listed or "Fixes and improvements.",
        "To update: open Studio and press <b>Update now</b> at the top. It takes a minute and keeps your projects."],
        ("See what's new", APP_URL), quiet=True)


# ---- hooks and the hourly check -----------------------------------------------------------------------------

def on_subscribed(user_id: int) -> None:
    """Stripe webhook: a subscription started. Records the moment and, for a Studio plan, sends the welcome."""
    try:
        milestones.record(int(user_id), SUBSCRIBED)
        user = db.get_user_by_id(int(user_id))
        if user and plans.has_studio(user.get("plan")):
            _later(welcome, user)
    except Exception as e:                                          # noqa: BLE001
        print(f"[lifecycle] on_subscribed {user_id}: {e}")


def on_video_finished(user: dict, title: str) -> None:
    """A Studio video project finished: the first one gets the first-video email."""
    try:
        if milestones.of(user["id"])["finished"] == 1:
            _later(first_video, user, title)
    except Exception as e:                                          # noqa: BLE001
        print(f"[lifecycle] on_video_finished {user.get('id')}: {e}")


def due_install_nudges(now: float | None = None) -> list[int]:
    """Customers who subscribed 1-7 days ago (recorded since this shipped) and whose Studio never called in."""
    now = time.time() if now is None else now
    milestones.initialize()
    with db._conn() as conn:
        rows = conn.execute(db._q("""SELECT user_id FROM user_milestones WHERE name=? AND first_at<=? AND first_at>?"""),
                            (SUBSCRIBED, now - NUDGE_AFTER_S, now - NUDGE_WITHIN_S)).fetchall()
    due = []
    for row in rows:
        got = milestones.of(int(row["user_id"]))
        if not got["connected_at"] and not got["seen_at"] and not got["projects"]:
            due.append(int(row["user_id"]))
    return due


def send_due(now: float | None = None) -> int:
    sent = 0
    try:
        for uid in due_install_nudges(now):
            user = db.get_user_by_id(uid)
            if user and plans.has_studio(user.get("plan")) and install_nudge(user):
                sent += 1
    except Exception as e:                                          # noqa: BLE001
        print(f"[lifecycle] hourly check failed: {e}")
    return sent


async def run_loop(interval_sec: int = 3600) -> None:
    import asyncio
    await asyncio.sleep(120)
    while True:
        await asyncio.to_thread(send_due)
        await asyncio.sleep(max(300, int(interval_sec)))


def send_release(entry: dict, *, pause_s: float = 0.6) -> dict:
    """Admin: email one release's notes to every Studio customer who hasn't had them (or unsubscribed)."""
    sent = skipped = 0
    with db._conn() as conn:
        users = [dict(r) for r in conn.execute(db._q(
            "SELECT id, email, plan FROM users WHERE COALESCE(plan, 'free') != 'free' ORDER BY id")).fetchall()]
    for user in users:
        if not plans.has_studio(user.get("plan")):
            continue
        if release(user, entry):
            sent += 1
            time.sleep(pause_s)                                     # within the email provider's rate limit
        else:
            skipped += 1
    return {"sent": sent, "skipped": skipped, "build": entry.get("build")}
