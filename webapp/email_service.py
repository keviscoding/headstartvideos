"""
Lightweight email service using Resend.
Gracefully no-ops if RESEND_KEY is not configured.
"""
from __future__ import annotations

import os

RESEND_KEY = os.getenv("RESEND_KEY", "")
# Override with the FROM_EMAIL env var. Must be an address on a domain you've
# verified in Resend (resend.com/domains). Use "Acme <onboarding@resend.dev>"
# for quick testing before your domain is verified.
FROM_EMAIL = os.getenv("FROM_EMAIL", "ChannelRecipe <noreply@channelrecipe.com>")


def _get_client():
    if not RESEND_KEY:
        return None
    import resend
    resend.api_key = RESEND_KEY
    return resend


def send_video_ready(to_email: str, video_title: str, video_url: str) -> bool:
    """Send a 'your video is ready' notification email."""
    resend = _get_client()
    if not resend or not to_email:
        return False

    try:
        resend.Emails.send({
            "from": FROM_EMAIL,
            "to": [to_email],
            "subject": f"Your video is ready: {video_title}",
            "html": f"""
            <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 480px; margin: 0 auto; padding: 32px 24px;">
                <div style="text-align: center; margin-bottom: 28px;">
                    <h1 style="font-size: 22px; font-weight: 700; color: #16161A; margin: 0;">Your video is ready</h1>
                </div>
                <div style="background: #F7F5F0; border-radius: 12px; padding: 20px; margin-bottom: 24px;">
                    <p style="margin: 0 0 4px; font-size: 13px; color: #6E6E79; text-transform: uppercase; letter-spacing: 0.05em;">Title</p>
                    <p style="margin: 0; font-size: 16px; font-weight: 600; color: #16161A;">{video_title}</p>
                </div>
                <div style="text-align: center;">
                    <a href="{video_url}" style="display: inline-block; background: #6D5AE0; color: #FFFFFF; text-decoration: none; padding: 12px 32px; border-radius: 8px; font-weight: 600; font-size: 15px;">View &amp; download</a>
                </div>
                <p style="margin-top: 28px; font-size: 12px; color: #A7ACC4; text-align: center;">
                    ChannelRecipe &mdash; Proven recipes for faceless YouTube channels
                </p>
            </div>
            """,
        })
        print(f"[email] Sent 'video ready' to {to_email}")
        return True
    except Exception as e:
        print(f"[email] Failed to send: {e}")
        return False


def send_verification_code(to_email: str, code: str) -> bool:
    """Send a 6-digit verification code for email auth."""
    resend = _get_client()
    if not resend or not to_email:
        return False

    try:
        resend.Emails.send({
            "from": FROM_EMAIL,
            "to": [to_email],
            "subject": f"Your verification code: {code}",
            "html": f"""
            <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 400px; margin: 0 auto; padding: 32px 24px; text-align: center;">
                <h1 style="font-size: 20px; font-weight: 700; color: #16161A; margin: 0 0 8px;">Verify your email</h1>
                <p style="font-size: 14px; color: #6E6E79; margin: 0 0 24px;">Enter this code in ChannelRecipe to continue:</p>
                <div style="background: #F7F5F0; border-radius: 12px; padding: 20px; margin-bottom: 24px;">
                    <span style="font-size: 32px; font-weight: 700; letter-spacing: 0.2em; color: #6D5AE0; font-family: monospace;">{code}</span>
                </div>
                <p style="font-size: 12px; color: #A7ACC4;">This code expires in 10 minutes.</p>
            </div>
            """,
        })
        print(f"[email] Sent verification code to {to_email}")
        return True
    except Exception as e:
        print(f"[email] Failed to send verification: {e}")
        return False


def send_niche_hunt_complete(
    *,
    keywords: list[str],
    channels_upserted: int,
    job_id: str,
    trigger: str = "cron",
    runner: str = "",
) -> int:
    """Email all ADMIN_EMAILS after a successful niche populate. Returns send count."""
    resend = _get_client()
    if not resend:
        return 0
    try:
        import config
        admins = list(getattr(config, "ADMIN_EMAILS", []) or [])
    except Exception:
        admins = []
    if not admins:
        return 0

    kw_list = [str(k).strip() for k in (keywords or []) if str(k).strip()]
    kw_html = "".join(
        f'<li style="margin:0 0 4px;font-family:ui-monospace,Menlo,monospace;font-size:13px;">{k}</li>'
        for k in kw_list
    ) or "<li>(none)</li>"
    subject = f"Niche library updated: {channels_upserted} channels"
    html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 520px; margin: 0 auto; padding: 32px 24px;">
        <h1 style="font-size: 20px; font-weight: 700; color: #16161A; margin: 0 0 8px;">Niche scrape finished</h1>
        <p style="font-size: 14px; color: #6E6E79; margin: 0 0 20px;">
            A ChannelRecipe niche populate completed successfully.
        </p>
        <div style="background: #F7F5F0; border-radius: 12px; padding: 16px 18px; margin-bottom: 16px;">
            <p style="margin: 0 0 6px; font-size: 12px; color: #6E6E79; text-transform: uppercase; letter-spacing: 0.05em;">Result</p>
            <p style="margin: 0; font-size: 15px; color: #16161A;">
                <strong>{int(channels_upserted)}</strong> channels upserted
                · trigger <strong>{trigger or '—'}</strong>
                · runner <strong>{runner or '—'}</strong>
            </p>
            <p style="margin: 8px 0 0; font-size: 12px; color: #A7ACC4; font-family: monospace;">job {job_id}</p>
        </div>
        <div style="background: #F7F5F0; border-radius: 12px; padding: 16px 18px; margin-bottom: 20px;">
            <p style="margin: 0 0 8px; font-size: 12px; color: #6E6E79; text-transform: uppercase; letter-spacing: 0.05em;">
                Keywords used ({len(kw_list)})
            </p>
            <ul style="margin: 0; padding-left: 18px; color: #16161A;">{kw_html}</ul>
        </div>
        <p style="margin: 0; font-size: 12px; color: #A7ACC4; text-align: center;">ChannelRecipe ops</p>
    </div>
    """
    sent = 0
    for to_email in admins:
        try:
            resend.Emails.send({
                "from": FROM_EMAIL,
                "to": [to_email],
                "subject": subject,
                "html": html,
            })
            print(f"[email] Sent niche hunt complete to {to_email}")
            sent += 1
        except Exception as e:
            print(f"[email] Failed niche hunt mail to {to_email}: {e}")
    return sent
