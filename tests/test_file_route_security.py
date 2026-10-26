"""/api/files has no sign-in. It used to serve the whole app folder: the Dockerfile, the code, and the .env that
Settings > keys wrote there (security review, 2026-10-26). Now: files the site made, under output/, only."""
import pytest
from fastapi.testclient import TestClient

from webapp import server


@pytest.fixture
def client():
    return TestClient(server.app)


@pytest.fixture
def made(monkeypatch):
    out = server.OUTPUT_DIR / "security-test"
    out.mkdir(parents=True, exist_ok=True)
    (out / "film.mp4").write_bytes(b"film")
    (out / ".hidden").write_text("no")
    yield out
    for f in out.iterdir():
        f.unlink()
    out.rmdir()


def test_a_file_the_site_made_is_served(client, made):
    r = client.get("/api/files/output/security-test/film.mp4")
    assert r.status_code == 200 and r.content == b"film"


@pytest.mark.parametrize("path", ["Dockerfile", ".env", "webapp/server.py", "output/security-test/.hidden",
                                  "output/../Dockerfile", "output/security-test/../../.env", ".git/config",
                                  "output/security-test/missing.mp4", "output"])
def test_nothing_outside_output_or_hidden_is_served_and_all_misses_look_alike(client, made, path):
    r = client.get("/api/files/" + path)
    assert r.status_code == 404


def test_keys_are_never_written_to_the_app_folder_on_the_hosted_site(monkeypatch):
    import asyncio
    from fastapi import HTTPException
    monkeypatch.setattr(server.storage, "is_remote", lambda: True)
    written = []
    monkeypatch.setattr("builtins.open", lambda *a, **k: written.append(a))
    with pytest.raises(HTTPException) as refused:
        asyncio.run(server.save_settings({"gemini": "x"}, admin={"id": 1}))
    assert refused.value.status_code == 409 and "DigitalOcean" in refused.value.detail
    assert not written, "no .env written"


@pytest.mark.parametrize("sid", ["../7/abc_123", "..", "a/b", "abc_123/../../x", "x" * 200])
def test_a_storyboard_match_id_that_is_a_path_is_refused(monkeypatch, sid):
    """Assemble built a folder path from the form's staging_id: '../<another account>/<job>_<stamp>' read another
    account's staging and assembled their clips."""
    from fastapi import HTTPException
    monkeypatch.setattr(server, "_storyboard_pack_job_or_404", lambda job_id, user: {"result": {}})
    monkeypatch.setattr(server, "_require_storyboard_stills_ready", lambda job: None)
    with pytest.raises(HTTPException) as refused:
        server.start_storyboard_assemble("job1", staging_id=sid, burn_captions="1", add_music="1", notify_email="",
                                         clips=[], admin={"id": 5})
    assert refused.value.status_code == 400 and "Match again" in refused.value.detail
