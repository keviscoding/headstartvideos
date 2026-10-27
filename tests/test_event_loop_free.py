"""The site's one event loop never waits on the database, Stripe, Fly or a file (2026-10-26).

Cloudflare 52x errors stopped customers' videos: the single container sometimes didn't answer. Many `async def` routes
did blocking work (a database query, a Fly spawn, a boto3 upload, ffprobe) on the event loop, and the MCP sign-in
middleware looked up every Studio key in the database there too. While one of them ran, nothing else was answered,
/api/health included, so the platform saw the site as down. Now blocking work runs in worker threads, and a route that
stays `async def` must say why (ASYNC_ON_PURPOSE)."""
import ast
import asyncio
import threading
import time
from pathlib import Path

import httpx
import pytest

from webapp import database as db
from webapp import server

WEBAPP = Path(server.__file__).resolve().parent

# Routes that stay `async def`, and why. Anything else that's async must not block: add it here only with a reason.
ASYNC_ON_PURPOSE = {
    # server.py: no blocking work at all (an in-memory answer, a cached page, a stat before FileResponse streams)
    "server.py": {"health", "landing", "favicon", "apple_touch_icon", "index", "privacy_page", "terms_page",
                  "harness_3d_page", "course_page", "money_niche_page", "money_niche_channel_check", "affiliates_page", "robots_txt",
                  "sitemap_xml", "get_client_config", "get_voices", "get_all_voices", "avatar_gen_cost", "serve_file",
                  "get_settings", "save_settings",
                  # await the request itself, then hand the blocking part to a thread
                  "stripe_webhook", "build_progress", "money_niche_shared", "email_unsubscribe",
                  "oauth_mcp_login_get", "oauth_mcp_login_post"},
    "frontier_studio.py": {"media_relay", "upload_relay"},     # streams with async httpx / reads the body, then a thread
    "studio_connect.py": {"connect_page"},
    "cloud_jobs.py": {"get_relay_next", "post_relay_ytdlp", "post_relay_messages"},  # long-polls: asyncio.sleep
    "studio_ranking.py": {"get_allowance", "post_watch", "post_watch_upload", "get_watch", "post_tiktok_search",
                          "post_youtube_search", "post_clean", "get_clean"},  # every blocking part through _call()
}


def _async_routes(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if isinstance(target, ast.Attribute) and target.attr in {"get", "post", "put", "delete", "patch",
                                                                         "api_route"}:
                    names.add(node.name)
    return names


@pytest.mark.parametrize("name", sorted(p.name for p in WEBAPP.glob("*.py")))
def test_every_async_route_is_one_that_never_blocks(name):
    unexpected = _async_routes(WEBAPP / name) - ASYNC_ON_PURPOSE.get(name, set())
    assert not unexpected, (f"{name}: {sorted(unexpected)} are async routes. Make them plain def (FastAPI runs them in "
                            "a worker thread), or await only async work and add them to ASYNC_ON_PURPOSE with why.")


class Slow:
    """A call that takes `seconds` (a slow database, Stripe or Fly), noting when it started."""

    def __init__(self, seconds: float = 1.5, result=None, error: Exception | None = None):
        self.seconds, self.result, self.error = seconds, result, error
        self.started = threading.Event()
        self.at = 0.0

    def __call__(self, *args, **kwargs):
        self.at = time.monotonic()
        self.started.set()
        time.sleep(self.seconds)
        if self.error:
            raise self.error
        return self.result


async def _health_while(path: str, slow: Slow, **kwargs) -> tuple[float, int]:
    """How long after the slow call started /api/health answered, asked as soon as the slow call was seen running.
    On a blocked event loop health can't be asked or answered until the slow call ends, so this is the call's length."""
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        busy = asyncio.ensure_future(client.request(kwargs.pop("method", "GET"), path, **kwargs))
        for _ in range(300):
            if slow.started.is_set():
                break
            await asyncio.sleep(0.01)
        assert slow.started.is_set(), "the slow call started"
        health = await client.get("/api/health")
        took = time.monotonic() - slow.at
        answer = await busy
    assert health.status_code == 200
    return took, answer.status_code


def test_health_answers_while_a_sign_in_lookup_is_slow(monkeypatch):
    slow = Slow()
    monkeypatch.setattr(server, "get_session_user", slow)
    took, status = asyncio.run(_health_while("/api/auth/me", slow, headers={"Cookie": "session=s"}))
    assert status == 200 and took < 0.5, f"/api/health took {took:.2f}s behind a slow database"


def test_health_answers_while_a_studio_key_lookup_is_slow(monkeypatch):
    """Every Studio request carries its account key, and MCP's sign-in middleware looks it up for every request."""
    slow = Slow()
    monkeypatch.setattr(db, "get_user_by_mcp_api_key", slow)
    took, _ = asyncio.run(_health_while("/api/studio/status", slow,
                                        headers={"Authorization": "Bearer cr_mcp_" + "k" * 24}))
    assert took < 0.5, f"/api/health took {took:.2f}s behind a Studio key lookup"


def test_health_answers_while_a_stripe_webhook_is_applied(monkeypatch):
    import sys
    import types
    slow = Slow(error=ValueError("bad signature"))
    monkeypatch.setitem(sys.modules, "stripe", types.SimpleNamespace(api_key="", Webhook=types.SimpleNamespace(
        construct_event=slow)))
    monkeypatch.setattr(server.config, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(server.config, "STRIPE_WEBHOOK_SECRET", "whsec_x")
    took, status = asyncio.run(_health_while("/api/billing/webhook", slow, method="POST", content=b"{}"))
    assert status == 400 and took < 0.5, f"/api/health took {took:.2f}s behind a webhook"


def test_health_answers_while_a_cook_is_queued_on_fly(monkeypatch):
    """Starting a video spawned its Fly machine over urllib on the event loop (server.py start_build)."""
    slow = Slow(result="")
    monkeypatch.setattr(server, "_current_user", slow)
    took, status = asyncio.run(_health_while("/api/build", slow, method="POST", json={"script": "A story."}))
    assert status == 401 and took < 0.5, f"/api/health took {took:.2f}s behind a cook start"


def test_health_answers_while_a_progress_stream_reads_the_database(monkeypatch):
    slow = Slow(1.0, {"status": "error", "error": "stopped", "progress_json": "[]"})
    monkeypatch.setattr(server, "COOK_ON_WEB", False)
    monkeypatch.setattr(server, "_current_user", lambda request: {"id": 1})
    server._jobs["loop-free-job"] = {"user_id": 1, "status": "error", "error": "x", "progress": []}
    monkeypatch.setattr(server, "get_cook_job", slow)
    try:
        took, status = asyncio.run(_health_while("/api/build/loop-free-job/progress", slow))
    finally:
        server._jobs.pop("loop-free-job", None)
    assert status == 200 and took < 0.5, f"/api/health took {took:.2f}s behind a progress stream"


def test_progress_streams_read_the_database_every_two_seconds_not_every_second():
    assert server.SSE_DB_POLL_SECONDS >= 2


def test_a_database_blip_doesnt_end_a_progress_stream(monkeypatch):
    def broken(job_id):
        raise RuntimeError("connection reset")
    monkeypatch.setattr(server, "COOK_ON_WEB", False)
    monkeypatch.setattr(server, "get_cook_job", broken)
    job = {"status": "running", "progress": []}
    assert server._poll_build_progress("j", job, 7.0) == 7.0 and job["status"] == "running"


def test_readiness_checks_the_database_with_a_short_wait(monkeypatch):
    from fastapi.testclient import TestClient
    client = TestClient(server.app)
    ok = client.get("/api/ready")
    assert ok.status_code == 200 and ok.json()["db"] == "ok" and ok.headers["cache-control"] == "no-store"

    def down(timeout):
        assert timeout <= 2.0, "a short wait: readiness must not hang"
        raise TimeoutError("pool timeout")
    monkeypatch.setattr(db, "ping", down)
    gone = client.get("/api/ready")
    assert gone.status_code == 503 and gone.json() == {"status": "unavailable", "db": "unreachable"}
    assert client.get("/api/health").status_code == 200, "liveness doesn't touch the database"


def test_readiness_takes_a_pooled_connection_within_its_timeout(monkeypatch):
    from contextlib import contextmanager
    asked = []

    class Conn:
        def execute(self, sql):
            asked.append(sql)
            return self

        def fetchone(self):
            return (1,)

    class Pool:
        @contextmanager
        def connection(self, timeout=None):
            asked.append(timeout)
            yield Conn()
    monkeypatch.setattr(db, "IS_PG", True)
    monkeypatch.setattr(db, "_pool", lambda: Pool())
    db.ping(timeout=2.0)
    assert asked == [2.0, "SELECT 1"]


def test_sync_routes_get_threads_sized_to_the_container():
    import anyio.to_thread
    import config

    async def sized():
        server._size_sync_threads()
        return anyio.to_thread.current_default_thread_limiter().total_tokens
    assert asyncio.run(sized()) == config.WEB_SYNC_THREADS
    assert 40 <= config.WEB_SYNC_THREADS <= 200 or config.os.getenv("WEB_SYNC_THREADS")


def test_mcp_sign_in_looks_keys_up_in_a_worker_thread(monkeypatch):
    from webapp import mcp_oauth
    threads = []

    def lookup(token):
        threads.append(threading.current_thread())
        return {"id": 7}
    monkeypatch.setattr(db, "get_user_by_mcp_api_key", lookup)
    got = asyncio.run(mcp_oauth.oauth_provider.load_access_token("cr_mcp_" + "k" * 24))
    assert got.subject == "7" and threads and threads[0] is not threading.main_thread()
