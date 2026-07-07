"""
Lightweight email service using Resend.
Gracefully no-ops if RESEND_KEY is not configured.
"""
from __future__ import annotations

import os

RESEND_KEY = os.getenv("RESEND_KEY", "")
FROM_EMAIL = "ChannelRecipe <noreply@channelrecipe.com>"


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
