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
    # Starter predates Studio and doesn't include it; is_admin from a client is ignored.
    regular = {"id": 3, "email": "paid@example.com", "plan": "starter", "is_admin": True}
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
    ("projects/crv_" + "1" * 24 + "/requests", "get"),
    ("projects/crv_" + "1" * 24 + "/requests/request_123456/retry", "post"),
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
    data = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "An outline."}]}}],
            "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 60, "thoughtsTokenCount": 10}, **changes}
    return SimpleNamespace(status_code=200, json=lambda: data,
                           iter_lines=lambda **k: iter([('data: '+json.dumps(data)).encode(), b'']), close=lambda: None)


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


@pytest.mark.parametrize("provider,model,body", [
    ("kie", "gemini-3.8-flash", {"code": 200, "data": 10}),
    ("atlas", "google/gemini-3.8-flash", {"data": [{"id": "google/gemini-3.8-flash"}]}),
])
def test_hub_account_is_checked_without_paid_call_and_kept_private(env, monkeypatch, provider, model, body):
    calls = []
    monkeypatch.setattr(studio.requests, "get", lambda *a, **kw: calls.append((a, kw)) or SimpleNamespace(status_code=200, json=lambda: body))
    monkeypatch.setattr(studio.requests, "post", lambda *a, **k: pytest.fail("Account checks must not generate content"))
    result = studio.save_key(env.owner, studio.BrainKey(api_key="customer-hub-key-for-test", provider=provider, model=model))
    assert result["model"] == model and result["provider"] == provider
    assert len(calls) == 1 and calls[0][1]["headers"] == {"Authorization": "Bearer customer-hub-key-for-test"}
    assert studio.credential(1)["provider"] == provider
    assert studio.decrypt_secret(studio.credential(1)["key_enc"]) == "customer-hub-key-for-test"
    assert "customer-hub-key-for-test" not in json.dumps(studio.status(env.owner))


def test_kie_http_200_error_does_not_replace_working_google_connection(env, monkeypatch):
    env.connect(env.owner)
    monkeypatch.setattr(studio.requests, "get", lambda *a, **kw: SimpleNamespace(status_code=200, json=lambda: {"code": 401, "msg": "secret upstream body"}))
    with pytest.raises(HTTPException) as error:
        studio.save_key(env.owner, studio.BrainKey(api_key="customer-hub-key-for-test", provider="kie"))
    assert "secret upstream" not in error.value.detail
    assert studio.credential(1)["provider"] == "gemini"


def test_existing_credentials_migrate_without_key_or_model_rotation(env):
    with studio.db._conn() as conn:
        conn.execute("CREATE TABLE frontier_credentials (user_id INTEGER PRIMARY KEY,key_enc TEXT NOT NULL,model TEXT NOT NULL,updated_at DOUBLE PRECISION NOT NULL)")
        conn.execute("INSERT INTO frontier_credentials VALUES (?,?,?,?)", (1, studio.encrypt_secret("old-google-key-for-test"), "gemini-3.8-flash", 100))
    studio.initialize()
    c = studio.credential(1)
    assert c["provider"] == "gemini" and c["model"] == "gemini-3.8-flash"
    assert studio.decrypt_secret(c["key_enc"]) == "old-google-key-for-test"
    assert c["visual_model"] == ""
    assert studio.status(env.owner)["brain"]["visual_model"] == "gemini-3.8-flash"


def connect_claude_profile(env, monkeypatch, visual="claude-opus-5-5"):
    checked = []
    monkeypatch.setattr(studio.providers, "check_key", lambda provider, key, model: checked.append((provider, model)))
    studio.save_key(env.owner, studio.BrainKey(provider="kie", api_key="owned-claude-key-for-test",
                    model="claude-sonnet-5-5", visual_model=visual))
    return checked


def test_specialist_profile_routes_to_owned_opus_without_changing_general_calls(env, monkeypatch):
    checked = connect_claude_profile(env, monkeypatch)
    assert checked == [("kie", "claude-sonnet-5-5"), ("kie", "claude-opus-5-5")]
    p = studio.create_project(env.owner, studio.ProjectRequest(title="Visual story"))
    calls = []
    monkeypatch.setattr(studio.providers, "generate", lambda *args: calls.append(args) or ("Code", 50, 100))
    general = studio.generate(env.owner, request_for(p["id"]))
    visual_req = studio.BrainRequest(project_id=p["id"], request_id="visual_request_1234", prompt="Write the scene.",
                                    requested_model="claude-opus-5-5", model_role="visual")
    visual = studio.generate(env.owner, visual_req)
    assert [row[2] for row in calls] == ["claude-sonnet-5-5", "claude-opus-5-5"]
    assert all(row[0:2] == ("kie", "owned-claude-key-for-test") for row in calls)
    assert general["model_role"] == "general" and visual["model_role"] == "visual"
    assert studio.generate(env.owner, visual_req) == visual and len(calls) == 2
    assert studio.request_history(env.owner, p["id"])["requests"][0]["context"]["thinking_level"] == "model_default"


def test_omitted_visual_choice_never_silently_upgrades_to_opus(env, monkeypatch):
    monkeypatch.setattr(studio.providers, "check_key", lambda *args: None)
    studio.save_key(env.owner, studio.BrainKey(provider="kie", api_key="owned-claude-key-for-test", model="claude-sonnet-5-5"))
    assert studio.status(env.owner)["brain"]["visual_model"] == "claude-sonnet-5-5"
    monkeypatch.setattr(studio.providers, "check_key", lambda *args: pytest.fail("Invalid model should not reach a provider"))
    with pytest.raises(HTTPException):
        studio.save_key(env.owner, studio.BrainKey(provider="kie", api_key="owned-claude-key-for-test",
                        model="claude-sonnet-5-5", visual_model="unknown-model"))
    assert studio.credential(1)["visual_model"] == "claude-sonnet-5-5"


def test_changing_specialist_model_invalidates_old_retry_approval(env, monkeypatch):
    p, req = stopped_request(env, monkeypatch)
    connect_claude_profile(env, monkeypatch)
    studio.approve_retry(env.owner, p["id"], req.request_id, approval())
    connect_claude_profile(env, monkeypatch, visual="claude-sonnet-5-5")
    monkeypatch.setattr(studio.providers, "generate", lambda *a: pytest.fail("Changed model profile must not spend an approved retry"))
    with pytest.raises(HTTPException):
        studio.generate(env.owner, req)
    assert studio.project(env.owner, p["id"])["calls"] == 1


def test_legacy_completed_and_stopped_stage_ids_do_not_create_fresh_paid_attempts(env, monkeypatch):
    import hashlib
    p, req = stopped_request(env, monkeypatch)
    legacy_hash = hashlib.sha256(req.model_dump_json(exclude={"model_role", "requested_model"}).encode()).hexdigest()
    with studio.db._conn() as conn:
        conn.execute("UPDATE frontier_requests SET fingerprint=?,context_json='{}' WHERE id=?", (legacy_hash, req.request_id))
    updated_req = req.model_copy(update={"model_role": "visual", "requested_model": "claude-opus-5-5"})
    monkeypatch.setattr(studio.providers, "generate", lambda *a: pytest.fail("An old interrupted request still needs explicit recovery"))
    with pytest.raises(HTTPException):
        studio.generate(env.owner, updated_req)
    assert studio.project(env.owner, p["id"])["calls"] == 1
    saved = {"text": "Already saved", "model": "gemini-3.8-flash"}
    with studio.db._conn() as conn:
        conn.execute("UPDATE frontier_requests SET status='complete',result_json=? WHERE id=?", (json.dumps(saved), req.request_id))
    assert studio.generate(env.owner, updated_req) == saved


def test_legacy_recovery_is_still_cached_after_new_routing_metadata_is_saved(env, monkeypatch):
    import hashlib
    p, req = stopped_request(env, monkeypatch)
    old_hash = hashlib.sha256(req.model_dump_json(exclude={"model_role", "requested_model"}).encode()).hexdigest()
    with studio.db._conn() as conn:
        conn.execute("UPDATE frontier_requests SET fingerprint=?,context_json='{}' WHERE id=?", (old_hash, req.request_id))
    updated = req.model_copy(update={"model_role": "visual", "requested_model": "claude-opus-5-5"})
    studio.approve_retry(env.owner, p["id"], req.request_id, approval())
    calls = []
    monkeypatch.setattr(studio.providers, "generate", lambda *args: calls.append(args) or ("Saved scene", 40, 60))
    result = studio.generate(env.owner, updated)
    assert studio.generate(env.owner, updated) == result and len(calls) == 1
    assert studio.project(env.owner, p["id"])["calls"] == 2


@pytest.mark.parametrize("provider", ["kie", "atlas"])
@pytest.mark.parametrize("kind", ["text", "research", "vision"])
def test_each_hub_routes_text_grounding_and_images_to_customer_account(env, monkeypatch, provider, kind):
    from base64 import b64encode
    body = {"code": 200, "data": 10} if provider == "kie" else {"data": [{"id": "google/gemini-3.8-flash"}]}
    monkeypatch.setattr(studio.requests, "get", lambda *a, **kw: SimpleNamespace(status_code=200, json=lambda: body))
    legacy_model = "gemini-3.8-flash" if provider == "kie" else "google/gemini-3.8-flash"
    studio.save_key(env.owner, studio.BrainKey(api_key="customer-hub-key-for-test", provider=provider, model=legacy_model))
    p = studio.create_project(env.owner, studio.ProjectRequest(title="Hub video"))
    calls = []
    def answer(url, **kw):
        calls.append((url, kw))
        return SimpleNamespace(status_code=200, close=lambda: None, json=lambda: {"choices": [{"message": {"content": "Answer"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 15}}) if provider == "kie" else reply()
    monkeypatch.setattr(studio.requests, "post", answer)
    req = request_for(p["id"], kind=kind, images=[b64encode(b"\x89PNG\r\n\x1a\nreview").decode()] if kind == "vision" else [])
    result = studio.generate(env.owner, req)
    assert studio.generate(env.owner, req) == result and len(calls) == 1
    url, options = calls[0]
    assert ("api.kie.ai" if provider == "kie" else "api.atlascloud.ai") in url
    assert options["headers"] == {"Authorization": "Bearer customer-hub-key-for-test"}
    payload = options["json"]
    if kind == "research":
        assert payload["tools"] == ([{"type": "function", "function": {"name": "googleSearch"}}] if provider == "kie" else [{"google_search": {}}])
    if kind == "vision":
        assert "data:image/png;base64" in json.dumps(payload) if provider == "kie" else "inlineData" in json.dumps(payload)
    assert result["provider"] == provider


def test_unknown_hub_models_are_rejected_before_network(env, monkeypatch):
    monkeypatch.setattr(studio.requests, "get", lambda *a, **k: pytest.fail("Unsupported model should not reach provider"))
    with pytest.raises(HTTPException):
        studio.save_key(env.owner, studio.BrainKey(api_key="customer-hub-key-for-test", provider="atlas", model="unknown/model"))


def test_postgres_schema_initialization_does_not_require_sqlite_path(env, monkeypatch):
    from contextlib import contextmanager
    statements = []
    @contextmanager
    def connection():
        yield SimpleNamespace(execute=lambda sql: statements.append(sql))
    monkeypatch.setattr(studio.db, "IS_PG", True)
    monkeypatch.setattr(studio.db, "DATABASE_URL", "postgresql://test-schema-initialization")
    monkeypatch.delattr(studio.db, "DB_PATH")
    monkeypatch.setattr(studio.db, "_conn", connection)
    studio.initialize()
    count = len(statements)
    studio.initialize()
    assert len(statements) == count
    assert any("ADD COLUMN IF NOT EXISTS provider" in s for s in statements)


def test_initialized_schema_does_not_open_redundant_database_connections(env, monkeypatch):
    studio.initialize()
    monkeypatch.setattr(studio.db, "_conn", lambda: pytest.fail("Ready schema should not open a connection"))
    studio.initialize()


def stopped_request(env, monkeypatch, max_calls=120):
    env.connect(env.owner)
    p = studio.create_project(env.owner, studio.ProjectRequest(title="Stopped video", max_calls=max_calls))
    monkeypatch.setattr(studio.requests, "post", lambda *a, **k: (_ for _ in ()).throw(studio.requests.ReadTimeout("secret provider URL")))
    req = request_for(p["id"])
    with pytest.raises(HTTPException):
        studio.generate(env.owner, req)
    return p, req


def approval(**changes):
    return studio.RetryApproval(expected_attempt=1, acknowledge_costs=True, **changes)


def test_failure_history_is_owned_and_contains_no_private_payload(env, monkeypatch):
    p, req = stopped_request(env, monkeypatch)
    history = studio.request_history(env.owner, p["id"])
    item = history["requests"][0]
    assert item["status"] == "needs_review" and item["can_retry"]
    assert item["errors"][0]["code"] == "read_timeout"
    assert item["context"] == {"provider": "gemini", "model": "gemini-3.8-flash", "kind": "text", "max_tokens": 8000,
                               "prompt_chars": len(req.prompt), "image_count": 0, "image_bytes": 0, "thinking_level": "low",
                               "model_role": "general", "requested_model": None}
    assert not any(s in json.dumps(history) for s in ("secret provider URL", req.prompt, "private-google-key-for-test", "fingerprint", "result_json"))
    with pytest.raises(HTTPException) as denied:
        studio.request_history(env.other, p["id"])
    assert denied.value.status_code == 404


def test_opted_in_retry_is_single_use_and_reuses_completed_result(env, monkeypatch):
    p, req = stopped_request(env, monkeypatch)
    calls = []
    monkeypatch.setattr(studio.requests, "post", lambda *a, **k: calls.append(k) or reply())
    with pytest.raises(HTTPException):
        studio.generate(env.owner, req)
    assert not calls
    assert studio.approve_retry(env.owner, p["id"], req.request_id, approval())["allowed"]
    assert studio.approve_retry(env.owner, p["id"], req.request_id, approval())["allowed"]
    assert not calls and studio.project(env.owner, p["id"])["calls"] == 1
    answer = studio.generate(env.owner, req)
    assert studio.generate(env.owner, req) == answer and len(calls) == 1
    row = studio.request_history(env.owner, p["id"])["requests"][0]
    assert row["attempts"] == 2 and row["status"] == "complete"
    assert studio.project(env.owner, p["id"])["calls"] == 2
    with pytest.raises(HTTPException):
        studio.approve_retry(env.owner, p["id"], req.request_id, approval())


def test_recovery_requires_cost_acknowledgement_and_owner(env, monkeypatch):
    p, req = stopped_request(env, monkeypatch)
    for who, options, expected in [(env.owner, studio.RetryApproval(expected_attempt=1), 400), (env.other, approval(), 404)]:
        with pytest.raises(HTTPException) as error:
            studio.approve_retry(who, p["id"], req.request_id, options)
        assert error.value.status_code == expected
    assert studio.request_history(env.owner, p["id"])["requests"][0]["status"] == "needs_review"


def test_retry_never_raises_budget_or_repeats_running_work(env, monkeypatch):
    p, req = stopped_request(env, monkeypatch, max_calls=1)
    with pytest.raises(HTTPException):
        studio.approve_retry(env.owner, p["id"], req.request_id, approval())
    assert not studio.request_history(env.owner, p["id"])["requests"][0]["can_retry"]
    with studio.db._conn() as conn:
        conn.execute("UPDATE frontier_projects SET max_calls=120 WHERE id=?", (p["id"],))
        conn.execute("UPDATE frontier_requests SET status='running' WHERE id=?", (req.request_id,))
    with pytest.raises(HTTPException):
        studio.approve_retry(env.owner, p["id"], req.request_id, approval())


def test_recovery_cannot_switch_account_or_payload(env, monkeypatch):
    p, req = stopped_request(env, monkeypatch)
    studio.approve_retry(env.owner, p["id"], req.request_id, approval())
    monkeypatch.setattr(studio.requests, "post", lambda *a, **k: pytest.fail("Changed work/account must not be submitted"))
    with pytest.raises(HTTPException):
        studio.generate(env.owner, req.model_copy(update={"prompt": "Different work"}))
    with studio.db._conn() as conn:
        conn.execute("UPDATE frontier_credentials SET key_enc=? WHERE user_id=1", (studio.encrypt_secret("another-account-key-for-test"),))
    with pytest.raises(HTTPException):
        studio.generate(env.owner, req)
    assert studio.project(env.owner, p["id"])["calls"] == 1


def test_failed_recovery_does_not_allow_another_paid_retry(env, monkeypatch):
    p, req = stopped_request(env, monkeypatch)
    studio.approve_retry(env.owner, p["id"], req.request_id, approval())
    with pytest.raises(HTTPException):
        studio.generate(env.owner, req)
    item = studio.request_history(env.owner, p["id"])["requests"][0]
    assert item["attempts"] == 2 and not item["can_retry"] and len(item["errors"]) == 2
    with pytest.raises(HTTPException):
        studio.approve_retry(env.owner, p["id"], req.request_id, studio.RetryApproval(expected_attempt=2, acknowledge_costs=True))


def test_old_stopped_request_can_recover_without_original_error_log(env, monkeypatch):
    p, req = stopped_request(env, monkeypatch)
    with studio.db._conn() as conn:
        conn.execute("UPDATE frontier_requests SET error_json='',context_json='' WHERE id=?", (req.request_id,))
    studio.approve_retry(env.owner, p["id"], req.request_id, approval())
    monkeypatch.setattr(studio.requests, "post", lambda *a, **k: reply())
    studio.generate(env.owner, req)
    assert studio.request_history(env.owner, p["id"])["requests"][0]["errors"][0]["code"] == "unrecorded"


def test_truncated_attempt_retains_reported_usage_and_reason(env, monkeypatch):
    env.connect(env.owner)
    p = studio.create_project(env.owner, studio.ProjectRequest(title="Example"))
    monkeypatch.setattr(studio.requests, "post", lambda *a, **k: reply(candidates=[{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "Private partial script"}]}}]))
    with pytest.raises(HTTPException):
        studio.generate(env.owner, request_for(p["id"]))
    item = studio.request_history(env.owner, p["id"])["requests"][0]
    error = item["errors"][0]
    assert error["code"] == "truncated" and error["finish_reason"] == "MAX_TOKENS"
    assert error["usage_reported"] and error["output_tokens"] == 70
    assert "Private partial script" not in json.dumps(item)
    assert studio.project(env.owner, p["id"])["input_tokens"] == 120


def test_unexpected_failure_is_structured_and_preserved_for_recovery(env, monkeypatch):
    env.connect(env.owner)
    p = studio.create_project(env.owner, studio.ProjectRequest(title="Example"))
    monkeypatch.setattr(studio.providers, "generate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret implementation details")))
    with pytest.raises(HTTPException) as error:
        studio.generate(env.owner, request_for(p["id"]))
    assert error.value.status_code == 502 and "secret" not in error.value.detail
    item = studio.request_history(env.owner, p["id"])["requests"][0]
    assert item["status"] == "needs_review" and item["errors"][0]["code"] == "internal_error"
    assert "secret" not in json.dumps(item)


def test_concurrent_resumption_consumes_one_retry_only(env, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    p, req = stopped_request(env, monkeypatch)
    studio.approve_retry(env.owner, p["id"], req.request_id, approval())
    started, release = Event(), Event()
    calls = []
    def pending(*a, **k):
        calls.append(k)
        started.set()
        assert release.wait(5)
        return reply()
    monkeypatch.setattr(studio.requests, "post", pending)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(studio.generate, env.owner, req)
        try:
            assert started.wait(5)
            duplicate = pool.submit(studio.generate, env.owner, req)
        finally:
            release.set()
        assert first.result(timeout=10)["text"] == "An outline."
        # The duplicate waits for the same answer; it never starts a second paid attempt.
        assert duplicate.result(timeout=10) == first.result()
    assert len(calls) == 1 and studio.project(env.owner, p["id"])["calls"] == 2


def test_concurrent_approval_is_idempotent(env, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    p, req = stopped_request(env, monkeypatch)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: studio.approve_retry(env.owner, p["id"], req.request_id, approval()), range(3)))
    assert all(r["allowed"] for r in results)
    with studio.db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM frontier_retries").fetchone()["n"] == 1
    assert studio.project(env.owner, p["id"])["calls"] == 1


def test_a_provider_rate_limit_is_released_and_the_same_request_works_later(env, monkeypatch):
    env.connect(env.owner)
    p = studio.create_project(env.owner, studio.ProjectRequest(title="Example"))
    answers = [SimpleNamespace(status_code=429, headers={"retry-after": "7"}, close=lambda: None), reply()]
    calls = []

    def provider(*args, **kwargs):
        calls.append(1)
        return answers.pop(0)

    monkeypatch.setattr(studio.requests, "post", provider)
    req = request_for(p["id"])
    with pytest.raises(HTTPException) as busy:
        studio.generate(env.owner, req)
    assert busy.value.status_code == 429 and busy.value.headers["Retry-After"] == "7"
    with studio.db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM frontier_requests").fetchone()["n"] == 0
    assert studio.project(env.owner, p["id"])["calls"] == 0, "a refusal at the door isn't a paid call"
    assert studio.generate(env.owner, req)["text"]
    assert len(calls) == 2 and studio.project(env.owner, p["id"])["calls"] == 1


def test_asking_again_collects_a_request_still_being_written(env, monkeypatch):
    import threading
    env.connect(env.owner)
    p = studio.create_project(env.owner, studio.ProjectRequest(title="Example"))
    monkeypatch.setattr(studio.requests, "post", lambda *a, **k: pytest.fail("never a second paid request"))
    monkeypatch.setattr(studio, "RUNNING_WAIT_S", 3)
    req = request_for(p["id"])
    fingerprint = studio.hashlib.sha256(req.model_dump_json().encode()).hexdigest()
    with studio.db._conn() as conn:
        conn.execute("INSERT INTO frontier_requests (id,project_id,fingerprint,status,created_at,started_at) VALUES (?,?,?,?,?,?)",
                     (req.request_id, p["id"], fingerprint, "running", studio.time.time(), studio.time.time()))
    saved = {"text": "the saved answer", "request_id": req.request_id}

    def finish():
        studio.time.sleep(1)
        with studio.db._conn() as conn:
            conn.execute("UPDATE frontier_requests SET status='complete',result_json=? WHERE id=?",
                         (json.dumps(saved), req.request_id))

    threading.Thread(target=finish).start()
    assert studio.generate(env.owner, req) == saved

    # Still running after the wait: Studio is told to come back, not that it stopped.
    later = req.model_copy(update={"request_id": "still_running_123456"})
    print_ = studio.hashlib.sha256(later.model_dump_json().encode()).hexdigest()
    with studio.db._conn() as conn:
        conn.execute("INSERT INTO frontier_requests (id,project_id,fingerprint,status,created_at,started_at) VALUES (?,?,?,?,?,?)",
                     (later.request_id, p["id"], print_, "running", studio.time.time(), studio.time.time()))
    monkeypatch.setattr(studio, "RUNNING_WAIT_S", 0)
    with pytest.raises(HTTPException) as running:
        studio.generate(env.owner, later)
    assert running.value.status_code == 409 and running.value.headers["X-CR-Request-State"] == "running"

    # Running for longer than any answer can take: it was cut off, so it needs review.
    with studio.db._conn() as conn:
        conn.execute("UPDATE frontier_requests SET started_at=? WHERE id=?", (studio.time.time() - 2000, later.request_id))
    with pytest.raises(HTTPException) as stopped:
        studio.generate(env.owner, later)
    assert stopped.value.status_code == 409 and "X-CR-Request-State" not in (stopped.value.headers or {})
    with studio.db._conn() as conn:
        assert conn.execute("SELECT status FROM frontier_requests WHERE id=?", (later.request_id,)).fetchone()["status"] == "needs_review"


def test_model_requests_are_shared_fairly(monkeypatch):
    monkeypatch.setattr(studio, "_brain_active", {})
    assert all(studio._take_slot(1) for _ in range(studio.BRAIN_PER_USER))
    assert not studio._take_slot(1), "one creator can't take every slot"
    assert studio._take_slot(2), "others still get through"
    studio._give_slot(1)
    assert studio._take_slot(1)
