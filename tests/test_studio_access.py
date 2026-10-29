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


def _age(project_id, hours, status=None):
    """Make a study look as if nothing happened on it for this many hours (and optionally set its status)."""
    at = studio.time.time() - hours * 3600
    with studio.db._conn() as conn:
        conn.execute(studio.db._q("UPDATE frontier_projects SET updated_at=?, status=COALESCE(?, status) WHERE id=?"),
                     (at, status, project_id))


def test_a_study_killed_by_closing_studio_stops_counting_after_six_idle_hours(env):
    """JamesElyse20 (2026-10-29): two studies died with Studio, never sent a finish, and used two clones forever."""
    creator = user(1, "daily")
    started = [studio.native_begin(creator, study(env, n))["project_id"] for n in range(3)]
    for project in started:
        _release(project, "5.3-admin.111")           # begun on a Studio that never settles its own studies
    _age(started[0], 7, "running")                   # a Claude stage was asked, then Studio was closed
    _age(started[1], 5, "running")                   # quiet for 5 h: may still be working, still counts
    assert studio.style_clone_usage(creator)["used"] == 2
    studio.native_begin(creator, study(env, 9))      # the freed clone can be used again
    with pytest.raises(HTTPException) as err:
        studio.native_begin(creator, study(env, 10))
    assert err.value.status_code == 402


def test_an_idle_study_counts_again_when_it_does_anything_or_finishes(env):
    creator = user(1, "daily")
    first = studio.native_begin(creator, study(env, 0))["project_id"]
    _release(first, "5.3-admin.111")
    _age(first, 30, "ready")
    assert studio.style_clone_usage(creator)["used"] == 0
    _age(first, 0)                                   # a stage asked or answered: it is working again
    assert studio.style_clone_usage(creator)["used"] == 1
    _age(first, 30, "done")                          # finished (late receipt): always counts, however old
    assert studio.style_clone_usage(creator)["used"] == 1
    _age(first, 30, "failed")
    assert studio.style_clone_usage(creator)["used"] == 0


def _release(project_id, version):
    """The Studio release a study was begun on, as its native session saved it."""
    with studio.db._conn() as conn:
        row = conn.execute(studio.db._q("SELECT setup_json FROM frontier_native_sessions WHERE project_id=?"),
                           (project_id,)).fetchone()
        setup = {**studio.json.loads(row["setup_json"]), "local_version": version}
        conn.execute(studio.db._q("UPDATE frontier_native_sessions SET setup_json=? WHERE project_id=?"),
                     (studio.json.dumps(setup), project_id))


def test_studio_112_studies_never_stop_counting_by_idling(env):
    """112+ sends a stopped study's finish itself: holding back a done finish offline never gets a clone back."""
    creator = user(1, "daily")
    new, old = (studio.native_begin(creator, study(env, n))["project_id"] for n in range(2))
    _release(new, "5.3-admin.112")
    _release(old, "5.3-admin.111")
    for project in (new, old):
        _age(project, 30, "ready")
    assert studio.style_clone_usage(creator)["used"] == 1, "only the pre-112 one stops counting"
    _age(new, 30, "failed")                          # its own stopped finish frees it
    assert studio.style_clone_usage(creator)["used"] == 0
    assert studio._release_number('{"local_version": "5.3-admin.112"}') == 112
    assert studio._release_number("not json") == studio._release_number("{}") == 0


def _save_completed(env, creator, n, version):
    saved = studio.native_begin(creator, studio.NativeBegin(
        begin_id=f"save_completed_{n:04d}", title=f"Save completed style: Channel {n}", style="history",
        setup={**env.proof, "purpose": "creative"}, max_calls=1))["project_id"]
    _release(saved, version)
    return saved


def test_saving_a_completed_study_counts_as_its_clone_from_studio_112(env):
    """112 settles a closed Studio's completed study to failed; its Save completed style then uses the clone, so a
    study plus its save is one clone, never zero."""
    creator = user(1, "daily")
    begun = studio.native_begin(creator, study(env, 0))["project_id"]
    _release(begun, "5.3-admin.112")
    _age(begun, 0, "failed")
    assert studio.style_clone_usage(creator)["used"] == 0
    saved = _save_completed(env, creator, 0, "5.3-admin.112")
    assert studio.style_clone_usage(creator)["used"] == 1
    _age(saved, 30, "done")
    assert studio.style_clone_usage(creator)["used"] == 1, "study + save stays one clone"
    _age(saved, 30, "failed")
    assert studio.style_clone_usage(creator)["used"] == 0, "a save that failed saved nothing"


def test_an_older_studios_save_never_counts_so_no_count_jumps_on_deploy(env):
    creator = user(1, "daily")
    begun = studio.native_begin(creator, study(env, 0))["project_id"]
    _age(begun, 0, "failed")
    saved = _save_completed(env, creator, 0, "5.3-admin.111")
    _age(saved, 0, "done")
    assert studio.style_clone_usage(creator)["used"] == 0


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
    assert op == "get_object" and params["Key"] == studio.RELEASE_KEY and expires <= 3600   # an hour: slow updates finish
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


def test_a_new_release_never_forces_an_update_only_the_deliberate_minimum_does(env):
    """Five releases in one night made creators stop their work to update ("the newest two" were supported). Every
    release from MIN_SUPPORTED_RELEASE on may start and resume videos; only older ones are asked to update."""
    member = user(1, "studio_pro")
    floor = studio.APPROVED_RELEASES.index(studio.MIN_SUPPORTED_RELEASE)
    supported = studio.status(member)["supported_versions"]
    assert supported == list(studio.APPROVED_RELEASES[floor:]) and supported[-1] == studio.VERSION
    assert len(supported) >= 10, "a night of releases leaves yesterday's Studio working"
    for at, release in enumerate((studio.MIN_SUPPORTED_RELEASE, studio.APPROVED_RELEASES[-4])):
        begun = studio.native_begin(member, studio.NativeBegin(begin_id=f"older_release_000{at}", title="A video",
                                                               style="history", setup={**env.proof, "local_version": release}))
        assert begun["workflow"]["required_release"] == release, "the project pins the release it started on"
    with pytest.raises(HTTPException) as err:
        studio.native_begin(member, studio.NativeBegin(begin_id="older_release_0009", title="A video", style="history",
                                                       setup={**env.proof, "local_version": studio.APPROVED_RELEASES[floor - 1]}))
    assert err.value.status_code == 409 and "Download the release" in err.value.detail


def test_an_admin_can_restart_and_raise_one_accounts_style_clones(env, monkeypatch):
    customer = user(5, "daily", "drawer@example.com")
    for n in range(3):
        studio.native_begin(customer, study(env, n))
    assert studio.style_clone_usage(customer)["remaining"] == 0
    monkeypatch.setattr(studio.db, "get_user_by_email", lambda email: customer if email == customer["email"] else None)
    with pytest.raises(studio.HTTPException) as no:
        studio.grant_style_clones(user(6, "studio_max"), customer["email"], 5, True)
    assert no.value.status_code == 403
    out = studio.grant_style_clones(user(1, "daily", "owner@example.com"), "Drawer@Example.com ", 5, True, "support call")
    assert out["used"] == 0 and out["limit"] == 8 and out["remaining"] == 8, "count restarted and 3 + 5 clones"
    studio.native_begin(customer, study(env, 9))
    assert studio.style_clone_usage(customer)["remaining"] == 7
    again = studio.grant_style_clones(user(1, "daily", "owner@example.com"), customer["email"], 2, False)
    assert again["used"] == 1 and again["limit"] == 5, "without reset the restart time is kept"
    assert studio.style_clone_usage(user(7, "daily"))["limit"] == 3, "nobody else changes"


def test_an_admin_can_give_back_a_clone_on_top_of_earlier_extras(env, monkeypatch):
    customer = user(5, "daily", "mixer@example.com")
    for n in range(3):
        studio.native_begin(customer, study(env, n))
    monkeypatch.setattr(studio.db, "get_user_by_email", lambda email: customer if email == customer["email"] else None)
    admin = user(1, "daily", "owner@example.com")
    first = studio.grant_style_clones(admin, customer["email"], 0, False, "clone stopped", give_back=1)
    assert first["used"] == 3 and first["limit"] == 4 and first["remaining"] == 1, "nothing to add to: one back"
    second = studio.grant_style_clones(admin, customer["email"], 0, False, "mix of three failed", give_back=1)
    assert second["limit"] == 5 and second["remaining"] == 2, "a second refund adds to the first"


def test_the_download_also_streams_through_this_site_for_providers_that_cannot_reach_storage(env, monkeypatch):
    """A creator's browser showed DNS_PROBE_FINISHED_NXDOMAIN for the storage host the signed link names; this site
    loads for them, so the website's button streams the ZIP through it, byte ranges included."""
    import io
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from webapp import storage
    data = bytes(range(256)) * 400
    asked = []

    class Body(io.BytesIO):
        def iter_chunks(self, size):
            while True:
                chunk = self.read(size)
                if not chunk:
                    return
                yield chunk

    class Client:
        def get_object(self, **params):
            asked.append(params)
            wanted = params.get("Range")
            if wanted:
                first = int(wanted.split("=")[1].split("-")[0])
                part = data[first:]
                return {"Body": Body(part), "ContentLength": len(part),
                        "ContentRange": f"bytes {first}-{len(data) - 1}/{len(data)}", "Metadata": {"sha256": "abc"}}
            return {"Body": Body(data), "ContentLength": len(data), "Metadata": {"sha256": "abc"}}
    monkeypatch.setattr(storage, "is_remote", lambda: True)
    monkeypatch.setattr(storage, "_get_client", lambda: Client())
    monkeypatch.setattr(storage, "_spaces_creds", lambda: ("k", "s", "bucket", "r", "e"))
    app = FastAPI()
    app.include_router(studio.make_router(lambda req: user(1, "daily") if req.headers.get("x-test-user") else None,
                                          prefix="/api/studio"))
    client = TestClient(app)
    assert client.get("/api/studio/download/file").status_code == 401
    res = client.get("/api/studio/download/file", headers={"x-test-user": "1"})
    assert res.status_code == 200 and res.content == data and asked[-1]["Key"] == studio.RELEASE_KEY
    assert res.headers["content-disposition"] == 'attachment; filename="%s"' % studio.download_name()
    assert res.headers["accept-ranges"] == "bytes"
    assert res.headers["x-amz-meta-sha256"] == "abc" and "no-store" in res.headers["cache-control"]
    part = client.get("/api/studio/download/file", headers={"x-test-user": "1", "Range": "bytes=1000-"})
    assert part.status_code == 206 and part.content == data[1000:]
    assert part.headers["content-range"] == f"bytes 1000-{len(data) - 1}/{len(data)}"
    pack = client.get("/api/studio/shorts-pack/file", headers={"x-test-user": "1"})
    assert pack.status_code == 200 and asked[-1]["Key"] == studio.SHORTS_PACK_KEY


def test_a_studio_that_keeps_asking_for_the_same_link_gets_it_through_this_site(env, monkeypatch):
    """A creator's internet provider reset every connection to the storage host (six asks, a second apart), and
    Studios before 92 only switch by themselves when the host's name doesn't resolve: from the third ask in ten
    minutes, the redirect is this site's own link, signed for the account and the file, byte ranges included."""
    import io
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from webapp import storage
    data = bytes(range(256)) * 50

    class Body(io.BytesIO):
        def iter_chunks(self, size):
            while chunk := self.read(size):
                yield chunk

    class Client:
        def generate_presigned_url(self, op, Params, ExpiresIn):
            return "https://storage.example/" + Params["Key"] + "?sig=1"

        def get_object(self, **params):
            first = int((params.get("Range") or "bytes=0-").split("=")[1].split("-")[0])
            out = {"Body": Body(data[first:]), "ContentLength": len(data) - first, "Metadata": {"sha256": "abc"}}
            if params.get("Range"):
                out["ContentRange"] = f"bytes {first}-{len(data) - 1}/{len(data)}"
            return out
    monkeypatch.setenv("FRONTIER_SECRETS_KEY", "stream-test-secret")
    monkeypatch.setattr(studio, "_link_asks", {})
    monkeypatch.setattr(studio, "release_ready", lambda: True)
    monkeypatch.setattr(storage, "is_remote", lambda: True)
    monkeypatch.setattr(storage, "_get_client", lambda: Client())
    monkeypatch.setattr(storage, "_spaces_creds", lambda: ("k", "s", "bucket", "r", "e"))
    app = FastAPI()
    app.include_router(studio.make_router(lambda req: user(int(req.headers["x-test-user"]), "daily")
                                          if req.headers.get("x-test-user") else None, prefix="/api/studio"))
    client = TestClient(app)
    ask = lambda who="1", path="/api/studio/shorts-pack": client.get(path, headers={"x-test-user": who},
                                                                       follow_redirects=False).headers["location"]
    assert ask().startswith("https://storage.example/") and ask().startswith("https://storage.example/")
    link = ask()
    assert "/api/studio/stream/pack?t=" in link, "the third ask in ten minutes: through this site"
    assert ask("2").startswith("https://storage.example/"), "only that account"
    assert ask(path="/api/studio/download").startswith("http://testserver/api/studio/stream/release?t="), \
        "and its Studio updates come through this site too"
    path = link.split("testserver", 1)[1]
    whole = client.get(path)
    assert whole.status_code == 200 and whole.content == data and whole.headers["x-amz-meta-sha256"] == "abc"
    part = client.get(path, headers={"Range": "bytes=1000-"})
    assert part.status_code == 206 and part.content == data[1000:]
    token = path.split("t=")[1]
    assert client.get("/api/studio/stream/release?t=" + token).status_code == 403, "signed for one file"
    assert client.get(path[:-2] + ("A" if path[-2] != "A" else "B") + path[-1]).status_code == 403
    assert studio.read_stream_token(token, "pack", now=studio.time.time() + studio.DOWNLOAD_LINK_SECONDS + 5) is None


def test_asks_spread_over_more_than_ten_minutes_keep_the_storage_link():
    studio._link_asks.pop(99, None)
    assert not studio.storage_blocked(99, "pack", now=0)
    assert not studio.storage_blocked(99, "pack", now=700)
    assert not studio.storage_blocked(99, "pack", now=1400)
    assert not studio.storage_blocked(99, "release", now=1401), "a pack and an update are counted apart"
    assert not studio.storage_blocked(99, "pack", now=1450)
    assert studio.storage_blocked(99, "pack", now=1500), "the third ask within ten minutes"
    assert studio.storage_blocked(99, "release", now=1600), "then the account's links all come through this site"
    assert not studio.storage_blocked(99, "release", now=1500 + 86400 + 1)
    studio._link_asks.pop(99, None)


def _kie_file_host(data, asked):
    """A stand-in for Kie's file host behind the relay's async HTTP client (byte ranges answered as 206)."""
    import httpx

    def answer(request):
        asked.append(request)
        wanted = request.headers.get("Range")
        first = int((wanted or "bytes=0-").split("=")[1].split("-")[0])
        body = data[first:]
        headers = {"Content-Length": str(len(body)), "Content-Type": "audio/wav"}
        if wanted:
            headers["Content-Range"] = f"bytes {first}-{len(data) - 1}/{len(data)}"
        return httpx.Response(206 if wanted else 200, headers=headers, content=body)
    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(answer))


def test_kies_files_come_through_the_relay_for_a_studio_that_cant_reach_kies_file_host(env, monkeypatch):
    """A creator's network cut every connection to aiquickdraw.com: their Studio fetches Kie's finished files here
    (local_runtime/cr_relay.py). Only Kie's file hosts, only for a Studio account, byte ranges passed on."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    data = b"RIFF" + bytes(range(256)) * 20
    asked = []
    monkeypatch.setattr(studio, "relay_client", _kie_file_host(data, asked))
    monkeypatch.setattr(studio.requests, "get", lambda *a, **k: pytest.fail("a relay never takes a worker thread"))
    app = FastAPI()
    app.include_router(studio.make_router(lambda req: user(1, "daily") if req.headers.get("x-test-user") else None,
                                          prefix="/api/studio"))
    client = TestClient(app)
    url = "https://tempfile.aiquickdraw.com/r/voice-123.wav"
    assert client.get("/api/studio/media-relay", params={"url": url}).status_code == 401
    got = client.get("/api/studio/media-relay", params={"url": url}, headers={"x-test-user": "1"})
    assert got.status_code == 200 and got.content == data and got.headers["content-type"] == "audio/wav"
    assert str(asked[-1].url) == url and asked[-1].headers["accept-encoding"] == "identity"
    part = client.get("/api/studio/media-relay", params={"url": url}, headers={"x-test-user": "1", "Range": "bytes=100-"})
    assert part.status_code == 206 and part.content == data[100:] and asked[-1].headers["Range"] == "bytes=100-"
    for other in ("https://example.com/x.wav", "http://tempfile.aiquickdraw.com/x.wav",
                  "https://aiquickdraw.com.evil.example/x.wav", "https://user:pw@tempfile.aiquickdraw.com/x.wav",
                  "https://tempfile.aiquickdraw.com:8443/x.wav"):
        assert client.get("/api/studio/media-relay", params={"url": other}, headers={"x-test-user": "1"}).status_code == 400
    assert len(asked) == 2, "nothing else is ever fetched"


def test_relays_share_one_client_per_event_loop_closed_at_shutdown():
    """A client per relay built a TLS context on the event loop each time (about 9 ms); one is shared and kept open
    between relays, and the server closes it when it stops."""
    import asyncio

    async def life():
        first, again = studio.relay_client(), studio.relay_client()
        assert first is again and not first.is_closed
        await studio.close_relay_client()
        assert first.is_closed and studio.relay_client() is not first
        await studio.close_relay_client()
    asyncio.run(life())


def test_a_finished_relay_leaves_the_shared_client_open(env, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    shared = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"voice")))
    monkeypatch.setattr(studio, "relay_client", lambda: shared)
    app = FastAPI()
    app.include_router(studio.make_router(lambda req: user(1, "daily"), prefix="/api/studio"))
    client = TestClient(app)
    for _ in range(2):
        got = client.get("/api/studio/media-relay", params={"url": "https://tempfile.aiquickdraw.com/r/v.mp3"})
        assert got.status_code == 200 and got.content == b"voice"
    assert not shared.is_closed


def test_a_relay_never_follows_a_redirect_and_frees_its_slot_on_every_answer(env, monkeypatch):
    """Kie's host answering a redirect, an error or a file over the limit: refused, and the account's six slots all
    come back (a seventh relay still gets through)."""
    import httpx
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    answers = iter([httpx.Response(302, headers={"Location": "https://example.com/x"}), httpx.Response(500),
                    httpx.Response(404), httpx.Response(200, headers={"Content-Length": str(700 * 1024 * 1024)}),
                    httpx.Response(200, content=b"x"), httpx.Response(200, content=b"x"), httpx.Response(200, content=b"x"),
                    httpx.Response(200, content=b"ok")])
    monkeypatch.setattr(studio, "relay_client",
                        lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda request: next(answers))))
    app = FastAPI()
    app.include_router(studio.make_router(lambda req: user(1, "daily"), prefix="/api/studio"))
    client = TestClient(app)
    url = "https://tempfile.aiquickdraw.com/r/voice-123.wav"
    codes = [client.get("/api/studio/media-relay", params={"url": url}).status_code for _ in range(8)]
    assert codes == [404, 502, 404, 413, 200, 200, 200, 200]


def test_a_relay_waiting_for_its_accounts_slot_holds_no_worker_thread(env, monkeypatch):
    """Six files streaming for one account, a seventh waits for a slot without taking a thread from the site."""
    import asyncio
    import anyio.to_thread
    import httpx

    release = asyncio.Event()

    async def slow_body():
        await release.wait()
        yield b"done"

    monkeypatch.setattr(studio, "relay_client", lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=slow_body()))))
    router = studio.make_router(lambda req: user(1, "daily"), prefix="/api/studio")
    relay = next(r.endpoint for r in router.routes if r.path == "/api/studio/media-relay")

    class Asked:
        headers = {}

    async def scenario():
        streams = [await relay(Asked(), url="https://tempfile.aiquickdraw.com/r/%d.wav" % i, user=user(1, "daily"))
                   for i in range(6)]
        waiting = asyncio.ensure_future(relay(Asked(), url="https://tempfile.aiquickdraw.com/r/7.wav",
                                              user=user(1, "daily")))
        await asyncio.sleep(0.05)
        busy = anyio.to_thread.current_default_thread_limiter().borrowed_tokens
        assert not waiting.done() and busy == 0, "the seventh waits on the event loop, not in a thread"
        release.set()
        bodies = [b"".join([c async for c in s.body_iterator]) for s in streams]
        seventh = await asyncio.wait_for(waiting, 5)
        return bodies, seventh.status_code

    bodies, status = asyncio.run(scenario())
    assert bodies == [b"done"] * 6 and status == 200


def test_uploads_to_kies_cut_upload_host_go_through_the_relay(env, monkeypatch):
    """A Studio whose network can't reach kieai.redpandaai.co uploads here (local_runtime/cr_relay.py). Only Kie's two
    upload addresses, only for a Studio account, the body passed on as it came, Kie's answer back as it is."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    posted = []

    def post(url, data=None, headers=None, **kw):
        posted.append({"url": url, "body": data.read(), "headers": dict(headers), **kw})
        return SimpleNamespace(status_code=200, headers={"Content-Type": "application/json"},
                               content=b'{"success":true,"code":200,"data":{"downloadUrl":"https://tempfile.redpandaai.co/a.png"}}')
    monkeypatch.setattr(studio.requests, "post", post)
    app = FastAPI()
    app.include_router(studio.make_router(lambda req: user(1, "daily") if req.headers.get("x-test-user") else None,
                                          prefix="/api/studio"))
    client = TestClient(app)
    body = b"--b\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.png\"\r\n\r\nPNG\r\n--b--\r\n"
    kie = {"x-test-user": "1", "X-CR-Kie-Authorization": "Bearer kie-secret",
           "Content-Type": "multipart/form-data; boundary=b"}
    ask = lambda path="/api/file-stream-upload", headers=kie, content=body: client.post(
        "/api/studio/upload-relay", params={"path": path}, headers=headers, content=content)
    assert ask(headers={k: v for k, v in kie.items() if k != "x-test-user"}).status_code == 401
    got = ask()
    assert got.status_code == 200 and got.json()["data"]["downloadUrl"] == "https://tempfile.redpandaai.co/a.png"
    sent = posted[-1]
    assert sent["url"] == "https://kieai.redpandaai.co/api/file-stream-upload" and sent["body"] == body
    assert sent["headers"]["Authorization"] == "Bearer kie-secret"
    assert sent["headers"]["Content-Type"] == "multipart/form-data; boundary=b"
    assert sent["headers"]["Content-Length"] == str(len(body)) and sent["allow_redirects"] is False
    assert ask("/api/file-base64-upload").status_code == 200
    assert posted[-1]["url"] == "https://kieai.redpandaai.co/api/file-base64-upload"
    for wrong in ("/api/file-url-upload", "https://evil.example/x", "/api/v1/jobs/createTask", ""):
        assert ask(wrong).status_code == 400
    assert ask(headers={**kie, "X-CR-Kie-Authorization": ""}).status_code == 400
    assert ask(content=b"").status_code == 400
    assert len(posted) == 2, "nothing else is ever sent to Kie"
    monkeypatch.setattr(studio, "MEDIA_UPLOAD_MAX_BYTES", 10)
    assert ask().status_code == 413 and len(posted) == 2, "the size cap"


def test_a_failed_upload_relay_never_echoes_the_kie_key(env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    def post(*a, **k):
        raise studio.requests.exceptions.ConnectionError("Bearer kie-secret could not connect")
    monkeypatch.setattr(studio.requests, "post", post)
    app = FastAPI()
    app.include_router(studio.make_router(lambda req: user(1, "daily"), prefix="/api/studio"))
    got = TestClient(app).post("/api/studio/upload-relay", params={"path": "/api/file-stream-upload"},
                               headers={"X-CR-Kie-Authorization": "Bearer kie-secret"}, content=b"x")
    assert got.status_code == 502 and "kie-secret" not in got.text and "upload host" in got.text
