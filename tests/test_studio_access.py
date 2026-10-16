"""Studio access follows the live plan, and style clones follow the plan allowance."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from webapp import frontier_studio as studio


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(studio.db, "IS_PG", False)
    monkeypatch.setattr(studio.db, "DB_PATH", tmp_path / "access.db")
    monkeypatch.setattr(studio.config, "ADMIN_EMAILS", ["owner@example.com"])
    monkeypatch.setattr(studio, "release_ready", lambda: True)
    proof = {"local_version": studio.VERSION, "general_model": "claude-sonnet-5-5",
             "visual_model": "claude-opus-5-5", "claude_installed": True, "claude_authenticated": True,
             "api_provider": "firstParty", "ffmpeg_ready": True, "browser_ready": True, "node_ready": True,
             "costs_accepted": True, "models_verified": True, "media_ready": True}
    return SimpleNamespace(proof=proof)


def user(uid, plan, email=None):
    return {"id": uid, "email": email or f"user{uid}@example.com", "plan": plan}


def study(env, n, *, begin_id=None):
    return studio.NativeBegin(begin_id=begin_id or f"style_study_{n:04d}", title=f"Style study: Channel {n}",
                              style="history", setup={**env.proof, "purpose": "creative"}, max_calls=40)


@pytest.mark.parametrize("plan", ["daily", "studio_pro", "studio_max", "daily_trial", "pro"])
def test_studio_plans_are_admitted(env, plan):
    status = studio.status(user(1, plan))
    assert status["access"] == "subscriber"
    assert status["plan_label"] in ("Creator", "Pro", "Max", "Creator (trial)")


@pytest.mark.parametrize("plan", ["free", "starter", "starter_trial", None])
def test_other_plans_get_an_upgrade_message(env, plan):
    with pytest.raises(HTTPException) as err:
        studio.status(user(1, plan))
    assert err.value.status_code == 403
    assert "Creator, Pro and Max" in err.value.detail


def test_cancelling_removes_access_on_the_next_request(env):
    member = user(1, "daily")
    project = studio.native_begin(member, studio.NativeBegin(begin_id="video_before_cancel", title="A video",
                                                             style="history", setup=env.proof))
    member["plan"] = "free"  # subscription.deleted downgrades the live row
    with pytest.raises(HTTPException):
        studio.native_progress(member, project["project_id"])


def test_creator_can_clone_three_styles_then_sees_the_upgrade(env):
    creator = user(1, "daily")
    for n in range(3):
        studio.native_begin(creator, study(env, n))
    assert studio.style_clone_usage(creator)["remaining"] == 0
    with pytest.raises(HTTPException) as err:
        studio.native_begin(creator, study(env, 3))
    assert err.value.status_code == 402
    assert "Upgrade to Pro" in err.value.detail


def test_replaying_an_existing_study_is_not_blocked_by_the_limit(env):
    creator = user(1, "daily")
    first = [studio.native_begin(creator, study(env, n)) for n in range(3)]
    again = studio.native_begin(creator, study(env, 0))
    assert again["project_id"] == first[0]["project_id"]


def test_failed_studies_free_their_slot(env):
    creator = user(1, "daily")
    started = [studio.native_begin(creator, study(env, n)) for n in range(3)]
    with studio.db._conn() as conn:
        conn.execute(studio.db._q("UPDATE frontier_projects SET status='failed' WHERE id=?"),
                     (started[0]["project_id"],))
    assert studio.style_clone_usage(creator)["used"] == 2
    studio.native_begin(creator, study(env, 9))


def test_videos_and_other_creative_work_never_count(env):
    creator = user(1, "daily")
    for n in range(5):
        studio.native_begin(creator, studio.NativeBegin(begin_id=f"plain_video_{n:04d}", title=f"Video {n}",
                                                        style="history", setup=env.proof))
    studio.native_begin(creator, studio.NativeBegin(begin_id="review_3d_scenes", title="Review 3D scenes: Video 1",
                                                    style="history", setup={**env.proof, "purpose": "creative"}))
    assert studio.style_clone_usage(creator)["used"] == 0


def test_trial_gets_one_clone_and_max_and_admins_are_unlimited(env):
    trial = user(1, "studio_pro_trial")
    studio.native_begin(trial, study(env, 0))
    with pytest.raises(HTTPException):
        studio.native_begin(trial, study(env, 1))
    for member in (user(2, "studio_max"), user(3, "free", "owner@example.com")):
        for n in range(20):
            studio.native_begin(member, study(env, n, begin_id=f"unlimited_{member['id']}_{n:04d}"))
        assert studio.style_clone_usage(member)["limit"] is None


def test_style_clone_counts_are_per_account(env):
    first, second = user(1, "daily"), user(2, "daily")
    for n in range(3):
        studio.native_begin(first, study(env, n))
    assert studio.style_clone_usage(second)["used"] == 0


def test_remote_download_redirects_to_a_short_lived_private_link(env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from webapp import storage
    monkeypatch.delenv("FRONTIER_RELEASE_PATH", raising=False)
    signed = []

    class Client:
        def generate_presigned_url(self, op, Params, ExpiresIn):
            signed.append((op, Params, ExpiresIn))
            return "https://bucket.example/private.zip?X-Amz-Signature=abc"

    monkeypatch.setattr(storage, "is_remote", lambda: True)
    monkeypatch.setattr(storage, "_get_client", lambda: Client())
    monkeypatch.setattr(storage, "_spaces_creds", lambda: ("k", "s", "bucket", "r", "e"))
    users = {"creator": user(1, "daily"), "starter": user(2, "starter")}
    app = FastAPI()
    app.include_router(studio.make_router(lambda req: users.get(req.headers.get("x-test-user"))))
    client = TestClient(app)

    assert client.get("/api/frontier/download", follow_redirects=False).status_code == 401
    assert client.get("/api/frontier/download", headers={"x-test-user": "starter"},
                      follow_redirects=False).status_code == 403
    assert not signed
    res = client.get("/api/frontier/download", headers={"x-test-user": "creator"}, follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"].startswith("https://bucket.example/private.zip")
    assert "no-store" in res.headers["cache-control"]
    op, params, expires = signed[0]
    assert op == "get_object" and params["Key"] == studio.RELEASE_KEY and expires <= 900
    assert "attachment" in params["ResponseContentDisposition"]



def test_studio_status_carries_whats_new(env):
    entries = studio.status(user(1, "studio_pro"))["changelog"]
    assert entries and all(isinstance(entry["build"], int) and entry["items"] for entry in entries)
    assert [entry["build"] for entry in entries] == sorted((entry["build"] for entry in entries), reverse=True)


def test_studio_status_lists_hosted_voice_samples(env):
    from pathlib import Path
    samples = studio.status(user(1, "studio_pro"))["voice_samples"]["google/gemini-3-8-flash-tts"]
    assert len(samples) == 70 and "Kore" in samples
    static = Path(studio.__file__).resolve().parent / "static"
    for url in samples.values():
        assert url.startswith("https://channelrecipe.com/static/voices/")
        assert (static / url.split("/static/", 1)[1]).is_file()


def test_status_says_how_far_the_customer_has_got(env, monkeypatch):
    from webapp import milestones
    monkeypatch.setattr(milestones, "_ready", set())
    member = user(1, "studio_pro")
    assert studio.status(member)["milestones"]["connected_at"] is None
    milestones.record(1, milestones.CONNECTED)
    studio.native_begin(member, studio.NativeBegin(begin_id="first_video_0001", title="A video", style="history",
                                                   setup=env.proof))
    got = studio.status(member)["milestones"]
    assert got["connected_at"] and got["projects"] == 1 and got["finished"] == 0


def test_a_studio_calling_with_its_key_is_seen_and_a_download_is_recorded(env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from webapp import milestones
    monkeypatch.setattr(milestones, "_ready", set())
    monkeypatch.setattr(milestones, "_seen", {})
    member = user(1, "studio_pro")
    monkeypatch.setattr(studio.db, "get_user_by_mcp_api_key", lambda key: member if key == "cr_mcp_good" else None)
    release = studio.db.DB_PATH.parent / "release.zip"
    release.write_bytes(b"PK\x05\x06" + b"\0" * 18)
    monkeypatch.setenv("FRONTIER_RELEASE_PATH", str(release))
    app = FastAPI(); app.include_router(studio.make_router(lambda req: member))
    client = TestClient(app)
    assert client.get("/api/frontier/status", headers={"Authorization": "Bearer cr_mcp_good"}).status_code == 200
    assert client.get("/api/frontier/download").status_code == 200
    got = milestones.of(1)
    assert got["seen_at"] and got["downloaded_at"]
