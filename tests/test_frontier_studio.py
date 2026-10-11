"""Access, credential isolation and no-duplicate-charge contracts for the preview."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from webapp import frontier_studio as studio


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(studio.db, "IS_PG", False)
    monkeypatch.setattr(studio.db, "DB_PATH", tmp_path / "preview.db")
    monkeypatch.setattr(studio.config, "ADMIN_EMAILS", ["owner@example.com", "other@example.com"])
    monkeypatch.setenv("FRONTIER_SECRETS_KEY", studio.Fernet.generate_key().decode())
    monkeypatch.setenv("FRONTIER_RELEASE_PATH", str(tmp_path / "release.zip"))
    owner = {"id": 1, "email": "owner@example.com"}
    other = {"id": 2, "email": "other@example.com"}
    regular = {"id": 3, "email": "paid@example.com", "plan": "pro", "is_admin": True}
    users = {"owner": owner, "other": other, "paid": regular}
    monkeypatch.setattr(studio.db, "get_user_by_mcp_api_key", lambda key: owner if key == "cr_mcp_valid" else None)
    app = FastAPI()
    app.include_router(studio.make_router(lambda req: users.get(req.headers.get("x-test-user"))))
    client = TestClient(app)
    def connect(user):
        monkeypatch.setattr(studio.requests, "get", lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: {"supportedGenerationMethods": ["generateContent"]}))
        studio.save_key(user, studio.BrainKey(api_key="private-google-key-for-test"))
    return SimpleNamespace(client=client, owner=owner, other=other, connect=connect, tmp=tmp_path)


@pytest.mark.parametrize("path,method", [("status", "get"), ("download", "get"),
    ("projects", "get"), ("projects/crv_" + "1" * 24, "get"),
    ("brain-key", "delete"), ("brain-key", "post"), ("projects", "post"), ("brain", "post")])
def test_every_endpoint_denies_anonymous_and_paid_non_admins(env, path, method):
    options = {"json": {}} if method == "post" else {}
    assert getattr(env.client, method)("/api/frontier/" + path, **options).status_code == 401
    # is_admin supplied by a user/client is not authoritative.
    assert getattr(env.client, method)("/api/frontier/" + path, headers={"x-test-user": "paid"}, **options).status_code == 403


def test_runner_bearer_requires_live_admin_identity(env):
    assert env.client.get("/api/frontier/status", headers={"Authorization": "Bearer cr_mcp_invalid"}).status_code == 401
    assert env.client.get("/api/frontier/status", headers={"Authorization": "Bearer cr_mcp_valid"}).status_code == 200
    studio.config.ADMIN_EMAILS = []
    assert env.client.get("/api/frontier/status", headers={"Authorization": "Bearer cr_mcp_valid"}).status_code == 403


def test_download_is_authenticated_and_not_cacheable(env):
    (env.tmp / "release.zip").write_bytes(b"protected engine")
    res = env.client.get("/api/frontier/download", headers={"x-test-user": "owner"})
    assert res.status_code == 200 and res.content == b"protected engine"
    assert "no-store" in res.headers["cache-control"]
    assert "attachment" in res.headers["content-disposition"]


def test_key_is_encrypted_and_never_in_status_or_download(env):
    env.connect(env.owner)
    key = studio.credential(1)
    assert "private-google-key-for-test" not in key["key_enc"]
    assert studio.decrypt_secret(key["key_enc"]) == "private-google-key-for-test"
    status = env.client.get("/api/frontier/status", headers={"x-test-user": "owner"}).json()
    assert status["brain"]["connected"] is True
    assert "private-google-key-for-test" not in json.dumps(status)
    assert studio.credential(2) is None


def test_invalid_key_does_not_replace_the_previous_key(env, monkeypatch):
    env.connect(env.owner)
    monkeypatch.setattr(studio.requests, "get", lambda *a, **k: SimpleNamespace(status_code=403))
    with pytest.raises(HTTPException):
        studio.save_key(env.owner, studio.BrainKey(api_key="wrong-google-key-for-test"))
    assert studio.decrypt_secret(studio.credential(1)["key_enc"]) == "private-google-key-for-test"


def request_for(project_id, **changes):
    return studio.BrainRequest(project_id=project_id, request_id="request_123456", prompt="Write a factual outline.", **changes)


def reply(**changes):
    return SimpleNamespace(status_code=200, json=lambda: {"candidates": [{"content": {"parts": [{"text": "An outline."}]}}],
                           "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 60, "thoughtsTokenCount": 10}, **changes})


def test_completed_request_is_reused_without_second_provider_call(env, monkeypatch):
    env.connect(env.owner)
    p = studio.create_project(env.owner, studio.ProjectRequest(title="Example", max_calls=1))
    calls = []
    def provider(*args, **kwargs):
        calls.append(kwargs)
        return reply()
    monkeypatch.setattr(studio.requests, "post", provider)
    req = request_for(p["id"])
    first = studio.generate(env.owner, req)
    assert studio.generate(env.owner, req) == first
    assert len(calls) == 1
    assert calls[0]["headers"]["x-goog-api-key"] == "private-google-key-for-test"
    usage = studio.project(env.owner, p["id"])
    assert (usage["calls"], usage["input_tokens"], usage["output_tokens"]) == (1, 120, 70)
    second = req.model_copy(update={"request_id": "new_request_123456"})
    with pytest.raises(HTTPException) as error:
        studio.generate(env.owner, second)
    assert error.value.status_code == 409 and len(calls) == 1


def test_projects_and_request_ids_are_tenant_isolated(env, monkeypatch):
    env.connect(env.owner); env.connect(env.other)
    p = studio.create_project(env.owner, studio.ProjectRequest(title="Owner's video"))
    assert env.client.get("/api/frontier/projects/" + p["id"], headers={"x-test-user": "other"}).status_code == 404
    q = studio.create_project(env.other, studio.ProjectRequest(title="Other video"))
    with studio.db._conn() as conn:
        conn.execute("INSERT INTO frontier_requests (id,project_id,fingerprint,status,created_at) VALUES (?,?,?,?,?)",
                     ("request_123456", p["id"], "original", "running", 1))
    with pytest.raises(HTTPException):
        studio.generate(env.other, request_for(q["id"]))
    with studio.db._conn() as conn:
        row = conn.execute("SELECT status FROM frontier_requests WHERE id='request_123456'").fetchone()
    assert row["status"] == "running"


def test_provider_failure_is_recorded_and_cannot_silently_retry_on_cr_key(env, monkeypatch):
    env.connect(env.owner)
    p = studio.create_project(env.owner, studio.ProjectRequest(title="Example"))
    calls = []
    def failed(*a, **kw):
        calls.append(kw)
        raise studio.requests.ReadTimeout("private details must not be exposed")
    monkeypatch.setattr(studio.requests, "post", failed)
    req = request_for(p["id"])
    with pytest.raises(HTTPException) as error:
        studio.generate(env.owner, req)
    assert "private details" not in error.value.detail
    with pytest.raises(HTTPException) as retry:
        studio.generate(env.owner, req)
    assert retry.value.status_code == 409 and len(calls) == 1


def test_research_uses_grounding_and_images_are_bounded(env, monkeypatch):
    env.connect(env.owner)
    p = studio.create_project(env.owner, studio.ProjectRequest(title="Example"))
    calls = []
    monkeypatch.setattr(studio.requests, "post", lambda *a, **kw: calls.append(kw) or reply())
    studio.generate(env.owner, request_for(p["id"], kind="research"))
    assert calls[0]["json"]["tools"] == [{"google_search": {}}]
    with pytest.raises(HTTPException):
        studio.generate(env.owner, request_for(p["id"], images=["not base64"]))
    assert len(calls) == 1


def test_catalog_contains_no_private_prompts(env):
    catalog = studio.catalog()
    assert len(catalog) == 26
    assert {"avatar", "3d", "documentary", "illustrated"} <= {s["family"] for s in catalog}
    assert not any("prompts" in s or "look" in s for s in catalog)


def test_truncated_answer_stops_the_engine_instead_of_saving_a_broken_script(env, monkeypatch):
    env.connect(env.owner)
    p = studio.create_project(env.owner, studio.ProjectRequest(title="Example"))
    monkeypatch.setattr(studio.requests, "post", lambda *a, **kw: reply(candidates=[{
        "finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "Incomplete script"}]}}]))
    with pytest.raises(HTTPException, match="502"):
        studio.generate(env.owner, request_for(p["id"]))
    assert studio.project(env.owner, p["id"])["status"] == "needs_review"


def test_key_storage_requires_a_dedicated_secret_before_contacting_google(env, monkeypatch):
    monkeypatch.delenv("FRONTIER_SECRETS_KEY")
    monkeypatch.setattr(studio.requests, "get", lambda *a, **kw: pytest.fail("No provider lookup should occur"))
    with pytest.raises(HTTPException) as err:
        studio.save_key(env.owner, studio.BrainKey(api_key="test-key-not-for-production"))
    assert err.value.status_code == 503
