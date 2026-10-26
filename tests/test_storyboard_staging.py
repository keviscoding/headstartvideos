"""Storyboard Match on one web container, Assemble on the other (two containers, 2026-10-26).

Match left staging.json and the matched clips on the disk of the container that answered it, and Assemble read them
from its own: with two containers about half the assembles answered 400. Now Match bundles the clips to a private
storage key with a record of the match, and Assemble gives the cook a presigned link to it, from either container."""
import io
import json
import shutil
import zipfile

import pytest
from fastapi import HTTPException, UploadFile

from webapp import server
from webapp import storyboard_staging as staging


@pytest.fixture
def two_containers(tmp_path, monkeypatch):
    """Spaces (private uploads and presigned links recorded), the storyboard engine stood in, cooks recorded."""
    stored, cooks = {}, []

    def store_private_file(local, key, content_type=None):
        stored[key] = (open(local, "rb").read(), content_type)
        return key

    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(server.storage, "is_remote", lambda: True)
    monkeypatch.setattr(server.storage, "store_private_file", store_private_file)
    monkeypatch.setattr(server.storage, "store_file",
                        lambda *a, **k: pytest.fail("a customer's clips never get a public link"))
    monkeypatch.setattr(server.storage, "presigned_get_url",
                        lambda key, expires=86400: f"https://spaces.test/{key}?signed=1&expires={expires}")
    monkeypatch.setattr(server, "_storyboard_pack_job_or_404",
                        lambda job_id, user: {"result": {"beats": [{"index": 1}], "zip_ready": True, "title": "Fox"},
                                              "status": "complete", "request": {"title": "Fox"}})
    monkeypatch.setattr(server, "_require_storyboard_stills_ready", lambda job: None)
    monkeypatch.setattr(server, "_enforce_user_cook_slot", lambda user: None)
    monkeypatch.setattr(server, "COOK_ON_WEB", False)
    monkeypatch.setattr(server, "COOK_ON_FLY", False)
    monkeypatch.setattr(server, "track", lambda *a, **k: None)
    monkeypatch.setattr(server, "update_cook_job", lambda *a, **k: None)
    monkeypatch.setattr(server, "create_cook_job", lambda **kw: cooks.append(kw))
    import core.storyboard_assemble as sa

    def extract(saved, dest):
        dest.mkdir(parents=True, exist_ok=True)
        out = []
        for p in saved:
            shutil.copy(p, dest / p.name)
            out.append(dest / p.name)
        return out
    monkeypatch.setattr(sa, "extract_clips_from_uploads", extract)
    monkeypatch.setattr(sa, "load_pack_beats", lambda pack_dir=None, beats=None: [{"index": 1}])
    monkeypatch.setattr(sa, "match_clips_to_beats", lambda clips, beats, pack_dir=None, work_dir=None: [
        {"index": 1, "clip": str(clips[0]), "method": "filename", "confidence": 1.0}])
    monkeypatch.setattr(sa, "parse_clip_index", lambda name: 1)
    return stored, cooks


def _clip(name="001_scene.mp4", data=b"\x00\x00\x00\x18ftypmp42" + b"v" * 400):
    return UploadFile(file=io.BytesIO(data), filename=name)


def _match(user_id=5, job_id="job1"):
    return server.match_storyboard_assemble(job_id, clips=[_clip()], admin={"id": user_id, "email": "a@x.test"})


def _assemble(staging_id, user_id=5, job_id="job1"):
    return server.start_storyboard_assemble(job_id, staging_id=staging_id, burn_captions="1", add_music="1",
                                            notify_email="", clips=[], admin={"id": user_id, "email": "a@x.test"})


def test_assemble_on_the_other_container_finds_the_match_and_the_cook_gets_a_presigned_link(two_containers):
    stored, cooks = two_containers
    matched = _match()
    sid = matched["staging_id"]
    assert matched["matched_count"] == 1 and staging.valid_id(sid)
    key = staging.bundle_key(5, sid)
    data, kind = stored[key]
    assert kind == "application/zip"
    assert zipfile.ZipFile(io.BytesIO(data)).namelist() == ["001_scene.mp4"], "the matched clips, bundled"

    shutil.rmtree(server.OUTPUT_DIR)                           # the other container: none of Match's files
    started = _assemble(sid)
    assert started["status"] == "queued" and started["staging_id"] == sid
    payload = json.loads(cooks[-1]["request_json"])
    assert payload["clips_zip_url"] == f"https://spaces.test/{key}?signed=1&expires={staging.LINK_SECONDS}"
    assert payload["clips_dir"] == "" and len(stored) == 1, "nothing bundled or uploaded a second time"


def test_assemble_on_the_same_container_uses_the_stored_bundle_too(two_containers):
    stored, cooks = two_containers
    sid = _match()["staging_id"]
    _assemble(sid)
    payload = json.loads(cooks[-1]["request_json"])
    assert payload["clips_zip_url"].startswith("https://spaces.test/" + staging.bundle_key(5, sid))
    assert len(stored) == 1


def test_another_accounts_match_is_never_found(two_containers):
    sid = _match(user_id=5)["staging_id"]
    shutil.rmtree(server.OUTPUT_DIR)
    with pytest.raises(HTTPException) as refused:
        _assemble(sid, user_id=6)
    assert refused.value.status_code == 400 and "run Match first" in refused.value.detail
    assert staging.load(6, sid) is None and staging.load(5, sid)["parent_job_id"] == "job1"


def test_a_match_for_another_storyboard_is_refused(two_containers):
    sid = _match(job_id="job1")["staging_id"]
    with pytest.raises(HTTPException) as refused:
        _assemble(sid, job_id="job2")
    assert refused.value.status_code == 400 and "another storyboard" in refused.value.detail


def test_a_match_whose_clips_cant_be_stored_says_so_at_match_time(two_containers, monkeypatch):
    def down(*a, **k):
        raise RuntimeError("Spaces unreachable")
    monkeypatch.setattr(server.storage, "store_private_file", down)
    with pytest.raises(HTTPException) as refused:
        _match()
    assert refused.value.status_code == 503 and "Match again" in refused.value.detail


def test_clips_uploaded_at_assemble_go_to_private_storage(two_containers):
    stored, cooks = two_containers
    server.start_storyboard_assemble("job1", staging_id="", burn_captions="1", add_music="1", notify_email="",
                                     clips=[_clip("002_scene.mp4")], admin={"id": 5, "email": "a@x.test"})
    (key, (data, kind)), = stored.items()
    assert key.startswith("storyboard/5/assemble_clips_") and kind == "application/zip"
    assert json.loads(cooks[-1]["request_json"])["clips_zip_url"].startswith("https://spaces.test/" + key)


@pytest.mark.parametrize("sid", ["../7/abc_123", "a/b", "x" * 161, ""])
def test_only_the_match_steps_own_ids_are_looked_up(sid):
    assert staging.load(5, sid) is None and not staging.valid_id(sid)


def test_without_spaces_the_bundle_stays_on_this_computer(tmp_path, monkeypatch):
    """Local development: one process, the bundle's local path is the cook's link."""
    monkeypatch.setattr(staging.storage, "is_remote", lambda: False)
    clips = tmp_path / "output" / "s" / "clips"
    clips.mkdir(parents=True)
    (clips / "001_scene.mp4").write_bytes(b"v" * 300)
    (clips / "notes.txt").write_text("not a clip")
    record = staging.save(9, "job9_1", "job9", clips, [])
    assert record["clip_count"] == 1 and record["clips_key"].startswith("/api/files/")
    assert staging.clips_link(staging.load(9, "job9_1")) == record["clips_key"]
