"""Helpful emails at the moments that matter on the way to a first video (owner-approved, 2026-10-16).

- welcome:       right after a subscription that includes Studio starts
- install_nudge: a day later, only while Studio is still not connected (no download, connect or Studio call, no project)
- first_video:   the first time one of the customer's Studio videos finishes
- release:<n>:   what's new in Studio release n, sent only when an admin presses send (never automatic)

Leads (people who asked for emails from the free tools, with consent: the money-niche quiz and the /3d-harness page)
are a separate list in `leads`, never mixed with customers:
- harness_pdf:      the PDF they asked for on /3d-harness, once per address
- breakouts:<issue>: "new breakout channels in your niche" from the Niche Finder, sent only when an admin presses send
Leads unsubscribe with the same kind of signed link (/email/unsubscribe?l=<lead>&t=...); someone who unsubscribed as a
customer is not mailed as a lead either.

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
            pk = "BIGSERIAL PRIMARY KEY" if db.IS_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
            conn.execute(f"""CREATE TABLE IF NOT EXISTS leads (
                id {pk}, email TEXT UNIQUE NOT NULL, sources TEXT NOT NULL DEFAULT '',
                niche_id TEXT NOT NULL DEFAULT '', consent_text TEXT NOT NULL DEFAULT '',
                consent_at DOUBLE PRECISION NOT NULL, created_at DOUBLE PRECISION NOT NULL,
                opted_out_at DOUBLE PRECISION NOT NULL DEFAULT 0)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS lead_emails (
                lead_id INTEGER NOT NULL, kind TEXT NOT NULL, sent_at DOUBLE PRECISION NOT NULL,
                PRIMARY KEY (lead_id, kind))""")
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


def _page(heading: str, paragraphs: list[str], button: tuple[str, str] | None, user_id: int, *,
          unsubscribe: str = "", reason: str = "You get this because you subscribed to ChannelRecipe.") -> tuple[str, str]:
    """(html, text) in the house style of the other ChannelRecipe emails."""
    e = lambda s: html.escape(s, quote=True)
    unsubscribe = unsubscribe or unsubscribe_url(user_id)
    body = "".join(f'<p style="margin: 0 0 14px; font-size: 15px; line-height: 1.55; color: #45454E;">{p}</p>'
                   for p in paragraphs)
    cta = (f'<div style="margin: 22px 0 6px;"><a href="{e(button[1])}" style="display: inline-block; background: #6D5AE0; '
           f'color: #FFFFFF; text-decoration: none; padding: 12px 26px; border-radius: 8px; font-weight: 600; font-size: 15px;">'
           f'{e(button[0])}</a></div>') if button else ""
    footer = (f'{e(reason)} '
              f'<a href="{e(unsubscribe)}" style="color: #6E6E79;">Unsubscribe from these emails</a>. '
              f'Sign-in codes still arrive.')
    page = (f'<div style="font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', sans-serif; max-width: 520px; '
            f'margin: 0 auto; padding: 32px 24px;"><h1 style="font-size: 22px; font-weight: 700; color: #16161A; '
            f'margin: 0 0 18px;">{e(heading)}</h1>{body}{cta}<p style="margin-top: 30px; font-size: 12px; '
            f'line-height: 1.5; color: #8A8794;">{footer}</p></div>')
    plain = heading + "\n\n" + "\n\n".join(html.unescape(_strip(p)) for p in paragraphs)
    if button:
        plain += f"\n\n{button[0]}: {button[1]}"
    plain += f"\n\nUnsubscribe from these emails: {unsubscribe}"
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
        "headers": {"List-Unsubscribe": f"<{user.get('unsubscribe') or unsubscribe_url(user['id'])}>",
                    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"},
        "tags": [{"name": "lifecycle", "value": kind.split(":")[0]}],
    })
    print(f"[lifecycle] sent {kind} to {'' if str(user['id']).startswith('lead:') else 'user '}{user['id']}")
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


# ---- leads: the free tools' email list (separate from customers) -------------------------------------------

LEAD_SOURCES = ("quiz", "harness")
LEAD_REASON = "You get this because you asked for emails from ChannelRecipe's free tools."
HARNESS_PDF = f"{SITE}/static/harness/3D-Documentary-Harness.pdf"
QUIZ_URL = f"{SITE}/money-niche"
_EMAIL = __import__("re").compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def lead_unsubscribe_token(lead_id: int) -> str:
    key = _link_key()
    return hmac.new(key, f"lead:{int(lead_id)}".encode(), hashlib.sha256).hexdigest()[:32] if key else ""


def lead_unsubscribe_url(lead_id: int) -> str:
    return f"{SITE}/email/unsubscribe?l={int(lead_id)}&t={lead_unsubscribe_token(lead_id)}"


def lead_opt_out(lead_id: int, token: str) -> bool:
    """Record a lead's unsubscribe from a signed link. False when the link isn't genuine."""
    expected = lead_unsubscribe_token(lead_id)
    if not expected or not hmac.compare_digest(expected, str(token or "")):
        return False
    initialize()
    with db._conn() as conn:
        conn.execute(db._q("UPDATE leads SET opted_out_at=? WHERE id=? AND opted_out_at=0"), (time.time(), int(lead_id)))
    return True


def capture_lead(email: str, source: str, consent_text: str, niche_id: str = "") -> dict:
    """Add someone who ticked the consent box to the leads list (or add this source to them). Fresh consent after
    an unsubscribe signs them up again: they asked."""
    email = (email or "").strip().lower()
    if source not in LEAD_SOURCES or not _EMAIL.fullmatch(email) or len(email) > 254:
        raise ValueError("Enter a valid email address.")
    initialize()
    now = time.time()
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT * FROM leads WHERE email=?"), (email,)).fetchone()
        if row:
            row = dict(row)
            sources = ",".join(sorted(set(filter(None, row["sources"].split(","))) | {source}))
            conn.execute(db._q("UPDATE leads SET sources=?, niche_id=?, consent_text=?, consent_at=?, opted_out_at=0 "
                               "WHERE id=?"), (sources, niche_id or row["niche_id"], consent_text[:300], now, row["id"]))
        else:
            conn.execute(db._q("INSERT INTO leads (email, sources, niche_id, consent_text, consent_at, created_at) "
                               "VALUES (?,?,?,?,?,?) ON CONFLICT(email) DO NOTHING"),
                         (email, source, niche_id or "", consent_text[:300], now, now))
        return dict(conn.execute(db._q("SELECT * FROM leads WHERE email=?"), (email,)).fetchone())


def _lead_blocked(lead: dict) -> bool:
    """Unsubscribed as a lead, or as a customer with the same address."""
    if float(lead.get("opted_out_at") or 0) > 0:
        return True
    user = db.get_user_by_email(lead["email"])
    return bool(user) and opted_out(int(user["id"]))


def _claim_lead(lead_id: int, kind: str) -> bool:
    with db._conn() as conn:
        cur = conn.execute(db._q("INSERT INTO lead_emails (lead_id, kind, sent_at) VALUES (?,?,?) "
                                 "ON CONFLICT(lead_id, kind) DO NOTHING"), (int(lead_id), kind, time.time()))
        return (cur.rowcount or 0) > 0


def deliver_lead(lead: dict, kind: str, subject: str, heading: str, paragraphs: list[str], button=None) -> bool:
    """Send one email to a lead at most once per kind. Never raises."""
    try:
        if not lead or not _link_key() or _lead_blocked(lead):
            return False
        if not _claim_lead(lead["id"], kind):
            return False
        unsubscribe = lead_unsubscribe_url(lead["id"])
        page, plain = _page(heading, paragraphs, button, 0, unsubscribe=unsubscribe, reason=LEAD_REASON)
        try:
            if _send({"id": f"lead:{lead['id']}", "email": lead["email"], "unsubscribe": unsubscribe},
                     kind, subject, page, plain):
                return True
        except Exception as e:                                      # noqa: BLE001
            print(f"[lifecycle] {kind} to lead {lead.get('id')} failed: {e}")
        with db._conn() as conn:                                    # not sent: may be tried again later
            conn.execute(db._q("DELETE FROM lead_emails WHERE lead_id=? AND kind=?"), (int(lead["id"]), kind))
    except Exception as e:                                          # noqa: BLE001
        print(f"[lifecycle] lead {kind} skipped: {e}")
    return False


def harness_pdf(lead: dict) -> bool:
    """The PDF someone asked for on /3d-harness (once per address)."""
    return deliver_lead(lead, "harness_pdf", "Your 3D Documentary Harness (PDF)", "Here's your harness", [
        "The 3D Documentary Harness: Visual DNA, Rule 13 and the 15 proven niches, as a PDF.",
        "When the Niche Finder spots new small channels breaking out, we'll send you the list. "
        "Not sure which niche is yours? The free 60-second quiz matches you to three, with real channels as proof: "
        f'<a href="{QUIZ_URL}" style="color: #5A48C9;">Find my money niche</a>.'],
        ("Download the PDF", HARNESS_PDF))


def _fmt_count(n: int) -> str:
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1000:
        return f"{n / 1000:.0f}K"
    return str(n)


def breakout_email(lead: dict, issue: str, channels: list[dict], niche_name: str) -> bool:
    """'New breakout channels in your niche' (Niche Finder data), once per issue."""
    e = lambda s: html.escape(str(s or ""), quote=True)
    rows = []
    for c in channels[:3]:
        started = f"started about {c['months']} months ago, " if c.get("months") else ""
        latest = c["recent"][0] if c.get("recent") else None
        line = (f'<b><a href="{e(c["url"])}" style="color: #16161A;">{e(c["name"])}</a></b>: {started}'
                f'{_fmt_count(c["subscribers"])} subscribers, about {_fmt_count(c["recent_avg_views"])} views '
                f'per recent video.')
        if latest and latest.get("title"):
            line += f' Latest: <a href="{e(latest["url"])}" style="color: #5A48C9;">{e(latest["title"])}</a>.'
        rows.append(line)
    where = f"in {niche_name}" if niche_name else "right now"
    return deliver_lead(lead, f"breakouts:{issue}", f"New breakout channels {where}",
                        f"New breakout channels {where}", [
        "Small channels the Niche Finder caught growing fast in the last two weeks:", *rows,
        "Study their last three videos: the look, the pacing, the first 20 seconds. "
        "Views are YouTube's public counts. New channels earn nothing from ads until they join the YouTube Partner "
        "Program, so treat these as proof the topic works, not as an earnings promise."],
        ("Find my money niche", QUIZ_URL))


def lead_stats() -> dict:
    initialize()
    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT sources, niche_id, opted_out_at FROM leads").fetchall()]
    by_source = {s: sum(1 for r in rows if s in r["sources"].split(",")) for s in LEAD_SOURCES}
    return {"total": len(rows), "subscribed": sum(1 for r in rows if not r["opted_out_at"]),
            "unsubscribed": sum(1 for r in rows if r["opted_out_at"]), "by_source": by_source}


def send_lead_breakouts(issue: str, *, pause_s: float = 0.6, find=None) -> dict:
    """Admin: email every subscribed lead this issue's new breakout channels in their niche (or across niches when
    they haven't picked one). A lead whose niche has no new breakouts this time is skipped, not sent filler."""
    from webapp import money_niche
    find = find or (lambda niche_id: money_niche.breakouts(niche_id or None))
    initialize()
    with db._conn() as conn:
        leads = [dict(r) for r in conn.execute("SELECT * FROM leads WHERE opted_out_at=0 ORDER BY id").fetchall()]
    found: dict = {}
    sent = skipped = 0
    for lead in leads:
        niche_id = lead.get("niche_id") or ""
        if niche_id not in found:
            found[niche_id] = find(niche_id)
        niche = money_niche.BY_ID.get(niche_id)
        if found[niche_id] and breakout_email(lead, issue, found[niche_id], niche["name"] if niche else ""):
            sent += 1
            time.sleep(pause_s)                                     # within the email provider's rate limit
        else:
            skipped += 1
    return {"sent": sent, "skipped": skipped, "issue": issue}
