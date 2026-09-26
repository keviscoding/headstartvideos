"""The Alex v2 page: one clean screen, separate from the main dashboard.

Mount inside your control app (one line):

    from alex_v2.dashboard import router as alex_v2_router
    app.include_router(alex_v2_router)          # -> http://<host>/alex-v2

or run it on its own:

    python -m alex_v2 serve --port 8799         # -> http://127.0.0.1:8799/alex-v2

If ALEX_V2_TOKEN is set, every request needs it (?token=..., header
X-Alex-V2-Token, or the cookie the page sets after the first visit).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import facts as F
from . import llm as LLM
from . import settings as S
from . import store as ST
from . import trace as TR
from .engine import due_threads, thread_control
from .lab import run_lab

UTC = timezone.utc
PAGE = Path(__file__).with_name("page.html")


def _auth(request: Request) -> None:
    token = os.getenv("ALEX_V2_TOKEN", "").strip()
    if not token:
        return
    got = (request.query_params.get("token") or request.headers.get("x-alex-v2-token") or request.cookies.get("alex_v2_token") or "")
    if got != token:
        raise HTTPException(status_code=401, detail="Alex v2: missing or wrong token")


router = APIRouter(prefix="/alex-v2", tags=["alex-v2"], dependencies=[Depends(_auth)])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def page(request: Request) -> HTMLResponse:
    resp = HTMLResponse(PAGE.read_text(encoding="utf-8"))
    tok = request.query_params.get("token")
    if tok:
        resp.set_cookie("alex_v2_token", tok, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 90)
    return resp


def _public_settings() -> dict[str, Any]:
    s = S.load()
    primary, fallback = LLM.resolve_model(s)
    return {**s, "resolved_model": primary, "resolved_fallback": fallback}


@router.get("/api/status")
def status() -> dict[str, Any]:
    threads = ST.list_threads("live")
    needs = [t for t in threads if (t.get("flags") or {}).get("needs_you")]
    return {
        "settings": _public_settings(),
        "defaults": S.DEFAULTS,
        "counts": {"threads": len(threads), "needs_you": len(needs), "due_now": len(due_threads()),
                   "paused": sum(1 for t in threads if (t.get("flags") or {}).get("paused")),
                   "closed": sum(1 for t in threads if (t.get("flags") or {}).get("closed"))},
        "model_errors": LLM.LAST_ERRORS[-5:],
        "server_time": datetime.now(UTC).isoformat(timespec="seconds"),
        "data_dir": str(S.data_dir()),
    }


@router.post("/api/mode")
def set_mode(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    if body.get("mode") in S.MODES:
        patch["mode"] = body["mode"]
    if body.get("ab_split") is not None:
        patch["ab_split"] = float(body["ab_split"])
    if not patch:
        raise HTTPException(400, "mode must be one of off/on/ab/shadow")
    S.save(patch)
    return _public_settings()


@router.post("/api/settings")
def set_settings(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    patch = {k: v for k, v in body.items() if k in S.DEFAULTS}
    for k, v in list(patch.items()):
        d = S.DEFAULTS[k]
        try:
            if isinstance(d, bool):
                patch[k] = bool(v) if not isinstance(v, str) else v.lower() in ("1", "true", "yes", "on")
            elif isinstance(d, int):
                patch[k] = int(v)
            elif isinstance(d, float):
                patch[k] = float(v)
            else:
                patch[k] = str(v)
        except (TypeError, ValueError):
            raise HTTPException(400, f"bad value for {k}")
    S.save(patch)
    return _public_settings()


@router.get("/api/facts")
def get_facts() -> dict[str, Any]:
    return {"facts": F.load(), "template": F.TEMPLATE, "prompt_preview": F.render_for_prompt(F.load())}


@router.post("/api/facts")
def post_facts(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    saved = F.save(body.get("facts", body))
    return {"facts": saved, "prompt_preview": F.render_for_prompt(saved)}


@router.get("/api/decisions")
def decisions(limit: int = 60, thread: str = "", namespace: str = "live") -> list[dict[str, Any]]:
    ns = "" if namespace == "all" else namespace
    out = TR.recent_decisions(limit=min(limit, 500), thread=thread, namespace=ns)
    if namespace != "lab":
        for e in out:
            e.pop("prompt", None)
    return out


@router.get("/api/decision/{trace_id}")
def decision(trace_id: str) -> dict[str, Any]:
    for e in TR.recent_decisions(limit=2000, namespace=""):
        if e.get("trace_id") == trace_id:
            return e
    raise HTTPException(404, "not found")


@router.get("/api/ab")
def ab() -> dict[str, Any]:
    return {"arms": TR.ab_summary(), "mode": S.load()["mode"], "split": S.load()["ab_split"]}


@router.post("/api/ab/reset")
def ab_reset() -> dict[str, Any]:
    TR.reset_outcomes()
    return {"ok": True}


@router.get("/api/threads")
def threads(limit: int = 200) -> list[dict[str, Any]]:
    out = []
    for t in ST.list_threads("live")[:limit]:
        fl = t.get("flags") or {}
        out.append({
            "thread_id": t.get("thread_id"),
            "contact": fl.get("contact", ""),
            "platform": fl.get("platform", ""),
            "arm": S.arm_for_thread(t.get("thread_id") or ""),
            "paused": bool(fl.get("paused")),
            "closed": bool(fl.get("closed")),
            "needs_you": fl.get("needs_you"),
            "pending": t.get("pending"),
            "next_check_at": fl.get("next_check_at"),
            "last_decision": fl.get("last_decision"),
            "sent": len(t.get("sent_log") or []),
        })
    return out


@router.post("/api/thread")
def thread_action(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    tid, action = body.get("thread_id"), body.get("action")
    if not tid or action not in ("pause", "resume", "reopen", "clear_handoff", "clear_pending"):
        raise HTTPException(400, "thread_id and a valid action are required")
    return thread_control(tid, action)


@router.post("/api/lab")
async def lab(body: dict[str, Any] = Body(...)) -> JSONResponse:
    conv = body.get("conversation") or ""
    if not conv.strip() and not body.get("allow_empty"):
        raise HTTPException(400, "paste a conversation first")
    now: Optional[datetime] = None
    if body.get("now"):
        try:
            now = datetime.fromisoformat(body["now"])
            if now.tzinfo is None:
                from .timeparse import tz as _tz
                now = now.replace(tzinfo=_tz(body.get("her_tz") or "Europe/London"))
        except ValueError:
            raise HTTPException(400, "bad 'now' (use 2026-09-24T21:14)")
    try:
        res = await run_lab(conv, platform=body.get("platform", ""), her_tz=body.get("her_tz") or "Europe/London", now=now,
                            call_model=bool(body.get("call_model", True)), her_profile=body.get("her_profile", ""))
    except Exception as exc:
        raise HTTPException(500, f"lab run failed: {type(exc).__name__}: {exc}")
    return JSONResponse(res)


def create_app():
    from fastapi import FastAPI
    from fastapi.responses import RedirectResponse
    app = FastAPI(title="Alex v2")
    app.include_router(router)

    @app.get("/")
    def root():
        return RedirectResponse("/alex-v2")

    return app
