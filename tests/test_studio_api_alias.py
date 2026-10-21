"""The branded API is a presentation alias, never a new authorization journal."""
import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from webapp import frontier_studio as studio


LEGACY_WORKFLOW = "frontier-5.3-claudecode.1"
BRANDED_WORKFLOW = "channelrecipe-5.3-claudecode.1"


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(studio.db, "IS_PG", False)
    monkeypatch.setattr(studio.db, "DB_PATH", tmp_path / "studio-alias.db")
    monkeypatch.setattr(studio.config, "ADMIN_EMAILS", ["owner@example.com", "other@example.com"])
    monkeypatch.setattr(studio, "release_ready", lambda: True)
    monkeypatch.setattr(studio.providers, "generate", lambda *a, **k: pytest.fail("An alias must not call a provider"))
    owner = {"id": 1, "email": "owner@example.com"}
    users = {"owner": owner, "other": {"id": 2, "email": "other@example.com"},
             # A Starter subscriber's plan doesn't include Studio; a client-sent
             # is_admin flag is never authoritative.
             "paid": {"id": 3, "email": "paid@example.com", "plan": "starter", "is_admin": True}}
    monkeypatch.setattr(studio.db, "get_user_by_mcp_api_key", lambda key: owner if key == "cr_mcp_valid" else None)
    app = FastAPI()
    session = lambda req: users.get(req.headers.get("x-test-user"))
    app.include_router(studio.make_router(session))
    app.include_router(studio.make_router(session, prefix="/api/studio"))
    proof = {"local_version": studio.VERSION, "general_model": "claude-sonnet-5-5", "visual_model": "claude-opus-5-5",
             "claude_installed": True, "claude_authenticated": True, "api_provider": "firstParty",
             "ffmpeg_ready": True, "browser_ready": True, "node_ready": True, "costs_accepted": True,
             "models_verified": True, "media_ready": True}
    return SimpleNamespace(client=TestClient(app), owner=owner, proof=proof, tmp=tmp_path,
                           headers={"x-test-user": "owner"})


def begin(api, prefix="/api/studio"):
    response = api.client.post(prefix + "/projects/begin", headers=api.headers, json={
        "begin_id": "identical_alias_begin", "title": LEGACY_WORKFLOW, "style": "history", "setup": api.proof,
        "options": {"extra": LEGACY_WORKFLOW, "script": "A literal Frontier example."}})
    assert response.status_code == 200, response.text
    return response.json()


def test_status_and_prepare_brand_contract_only(api):
    legacy = api.client.get("/api/frontier/status", headers=api.headers).json()
    branded = api.client.get("/api/studio/status", headers=api.headers).json()
    assert legacy["production_workflow"]["workflow_version"] == LEGACY_WORKFLOW
    assert branded["production_workflow"]["workflow_version"] == BRANDED_WORKFLOW
    assert branded["production_workflow"]["prompt_visibility"] == studio.workflow_contract()["prompt_visibility"]
    assert "Frontier" not in branded["production_workflow"]["prompt_visibility"]
    branded["production_workflow"]["workflow_version"] = LEGACY_WORKFLOW
    assert branded == legacy
    response = api.client.post("/api/studio/runtime/prepare", headers=api.headers, json=api.proof)
    assert response.status_code == 200 and response.json()["ready"]
    assert response.json()["workflow"]["workflow_version"] == BRANDED_WORKFLOW
    assert int(response.headers["content-length"]) == len(response.content)


def test_progress_and_begin_keep_titles_options_artifacts_and_stored_contract(api):
    first = begin(api)
    assert first["workflow"]["workflow_version"] == BRANDED_WORKFLOW
    assert first["project"]["title"] == first["job_spec"]["extra"] == LEGACY_WORKFLOW
    assert first["job_spec"]["script"] == "A literal Frontier example."
    project_id = first["project_id"]
    artifact = {"name": LEGACY_WORKFLOW + ".mp4", "sha256": "e" * 64, "media_type": "video/mp4"}
    done = api.client.post("/api/studio/projects/finish", headers=api.headers,
                           json={"project_id": project_id, "status": "done", "artifacts": [artifact]})
    assert done.status_code == 200
    branded = api.client.get("/api/studio/projects/" + project_id + "/progress", headers=api.headers).json()
    legacy = api.client.get("/api/frontier/projects/" + project_id + "/progress", headers=api.headers).json()
    assert branded["workflow"]["workflow_version"] == BRANDED_WORKFLOW
    assert legacy["workflow"]["workflow_version"] == LEGACY_WORKFLOW
    assert branded["artifacts"][0]["name"] == artifact["name"]
    branded["workflow"]["workflow_version"] = LEGACY_WORKFLOW
    assert branded == legacy
    with studio.db._conn() as conn:
        stored = conn.execute("SELECT workflow_json FROM frontier_native_sessions WHERE project_id=?", (project_id,)).fetchone()
    assert json.loads(stored["workflow_json"])["workflow_version"] == LEGACY_WORKFLOW


def test_completed_stage_cross_alias_reuses_original_receipt_and_budget(api):
    saved = begin(api, "/api/frontier")
    project_id = saved["project_id"]
    request = {"project_id": project_id, "stage_id": "cc_" + "a" * 64, "input_fingerprint": "b" * 64,
               "requested_model": "claude-sonnet-5-5", "local_version": studio.VERSION}
    approval = api.client.post("/api/studio/stages/authorize", headers=api.headers, json=request)
    assert approval.status_code == 200 and approval.json()["execute"]
    assert approval.json()["workflow_version"] == BRANDED_WORKFLOW
    with studio.db._conn() as conn:
        original = dict(conn.execute("SELECT * FROM frontier_native_stages WHERE project_id=?", (project_id,)).fetchone())
    finish = {"project_id": project_id, "stage_id": request["stage_id"],
              "authorization_token": approval.json()["authorization_token"], "status": "complete",
              "result_fingerprint": "c" * 64, "usage": {"input_tokens": 2, "output_tokens": 3, "reported": True}}
    assert api.client.post("/api/frontier/stages/finish", headers=api.headers, json=finish).status_code == 200
    for prefix, workflow in (("/api/studio", BRANDED_WORKFLOW), ("/api/frontier", LEGACY_WORKFLOW)):
        reused = api.client.post(prefix + "/stages/authorize", headers=api.headers, json=request)
        assert reused.status_code == 200
        assert reused.json()["reuse"] and not reused.json()["execute"]
        assert reused.json()["workflow_version"] == workflow
        assert reused.json()["result_fingerprint"] == "c" * 64
    with studio.db._conn() as conn:
        after = dict(conn.execute("SELECT * FROM frontier_native_stages WHERE project_id=?", (project_id,)).fetchone())
    assert original["fingerprint"] == after["fingerprint"]
    assert original["authorization_hash"] == after["authorization_hash"]
    assert after["attempts"] == 1
    progress = studio.native_progress(api.owner, project_id)["project"]
    assert (progress["calls"], progress["input_tokens"], progress["output_tokens"]) == (1, 2, 3)
    assert after["authorization_hash"] == hashlib.sha256(finish["authorization_token"].encode()).hexdigest()


@pytest.mark.parametrize("prefix", ["/api/frontier", "/api/studio"])
@pytest.mark.parametrize("path,method", [("status", "get"), ("download", "get"), ("projects", "get"),
    ("runtime/prepare", "post"), ("projects/begin", "post"), ("stages/authorize", "post"),
    ("stages/finish", "post"), ("projects/finish", "post"), ("stages/retry", "post"),
    ("brain", "post"), ("brain-key", "post"), ("brain-key", "delete")])
def test_alias_retains_live_admin_and_bearer_access(api, prefix, path, method):
    options = {"json": {}} if method == "post" else {}
    action = getattr(api.client, method)
    assert action(prefix + "/" + path, **options).status_code == 401
    assert action(prefix + "/" + path, headers={"x-test-user": "paid"}, **options).status_code == 403
    assert action(prefix + "/" + path, headers={"Authorization": "Bearer cr_mcp_bad"}, **options).status_code == 401


def test_alias_enforces_tenant_and_live_key_revocation(api, monkeypatch):
    project_id = begin(api)["project_id"]
    assert api.client.get("/api/studio/projects/" + project_id + "/progress", headers={"x-test-user": "other"}).status_code == 404
    assert api.client.get("/api/studio/status", headers={"Authorization": "Bearer cr_mcp_valid"}).status_code == 200
    monkeypatch.setattr(studio.config, "ADMIN_EMAILS", [])
    assert api.client.get("/api/studio/status", headers={"Authorization": "Bearer cr_mcp_valid"}).status_code == 403


def test_branding_has_no_recursive_user_data_rewrite_or_unknown_contract_alias():
    data = {"workflow_version": "future-workflow", "workflow": {"workflow_version": "frontier-6-other"},
            "production_workflow": {"workflow_version": LEGACY_WORKFLOW},
            "job_spec": {"workflow_version": LEGACY_WORKFLOW}, "title": LEGACY_WORKFLOW,
            "artifacts": [{"name": LEGACY_WORKFLOW}], "text": "Frontier " + LEGACY_WORKFLOW}
    assert studio._brand_workflow_response(data)
    assert data["production_workflow"]["workflow_version"] == BRANDED_WORKFLOW
    assert data["workflow_version"] == "future-workflow" and data["workflow"]["workflow_version"] == "frontier-6-other"
    assert data["job_spec"]["workflow_version"] == data["title"] == data["artifacts"][0]["name"] == LEGACY_WORKFLOW
    assert data["text"] == "Frontier " + LEGACY_WORKFLOW


def test_download_alias_keeps_file_bytes_headers_and_private_stream(api, monkeypatch):
    content = b"Frontier is arbitrary binary package content, not JSON metadata."
    local = api.tmp / "release.zip"
    local.write_bytes(content)
    monkeypatch.setenv("FRONTIER_RELEASE_PATH", str(local))
    response = api.client.get("/api/studio/download", headers=api.headers)
    assert response.status_code == 200 and response.content == content
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "ChannelRecipe-local-studio" in response.headers["content-disposition"]
    assert int(response.headers["content-length"]) == len(content)

    # Remote releases are handed out as a short-lived signed link instead of
    # streaming through the web process; the alias must pass the redirect through.
    monkeypatch.delenv("FRONTIER_RELEASE_PATH")
    from webapp import storage
    signed = []
    def presign(op, Params, ExpiresIn):
        signed.append(Params)
        return "https://private-bucket.example/release.zip?sig=1"
    monkeypatch.setattr(storage, "is_remote", lambda: True)
    monkeypatch.setattr(studio, "release_ready", lambda: True)
    monkeypatch.setattr(storage, "_spaces_creds", lambda: ("unused", "unused", "private-bucket"))
    monkeypatch.setattr(storage, "_get_client", lambda: SimpleNamespace(generate_presigned_url=presign))
    response = api.client.get("/api/studio/download", headers=api.headers, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "https://private-bucket.example/release.zip?sig=1"
    assert response.headers["cache-control"] == "private, no-store"
    assert signed[0]["Bucket"] == "private-bucket" and "ChannelRecipe-local-studio" in signed[0]["ResponseContentDisposition"]


def test_shorts_pack_is_a_signed_private_link_for_a_studio_plan_only(api, monkeypatch):
    assert api.client.get("/api/studio/shorts-pack", follow_redirects=False).status_code == 401
    assert api.client.get("/api/studio/shorts-pack", headers={"Authorization": "Bearer cr_mcp_bad"},
                          follow_redirects=False).status_code == 401
    from webapp import storage
    signed = []
    def presign(op, Params, ExpiresIn):
        signed.append((Params, ExpiresIn))
        return "https://private-bucket.example/shorts.zip?sig=1"
    monkeypatch.setattr(storage, "is_remote", lambda: True)
    monkeypatch.setattr(storage, "_spaces_creds", lambda: ("unused", "unused", "private-bucket"))
    monkeypatch.setattr(storage, "_get_client", lambda: SimpleNamespace(generate_presigned_url=presign))
    response = api.client.get("/api/studio/shorts-pack", headers=api.headers, follow_redirects=False)
    assert response.status_code == 302 and response.headers["location"].startswith("https://private-bucket.example/")
    assert response.headers["cache-control"] == "private, no-store"
    params, expires = signed[0]
    # minted per signed-in, entitled request; an hour so a slow connection (storage is in San Francisco) finishes
    assert params["Key"] == studio.SHORTS_PACK_KEY and params["Key"].startswith("private/") and expires <= 3600
