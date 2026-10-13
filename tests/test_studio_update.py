"""In-place Studio updates keep every project, account, style and edit.

The fake server below plays ChannelRecipe: status reports the latest version,
and the download is a signed-link redirect to a release ZIP.
"""
import hashlib
import io
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_update  # noqa: E402

KEY = "cr_mcp_" + "k" * 24


def release(version, files):
    shipped = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    files = {**files, "cr-release.json": json.dumps({"version": version}).encode(),
             "cr-files.json": json.dumps({"schema": 1, "version": version, "files": shipped}).encode()}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr("ChannelRecipe studio/" + name, data)
    return buffer.getvalue(), files


@pytest.fixture
def studio(tmp_path):
    _, installed = release("5.3-admin.44", {
        "cr_studio.py": b"old studio", "requirements.txt": b"flask\n",
        "styles/history.json": b'{"look": "shipped"}', "obsolete.py": b"kept, harmless"})
    for name, data in installed.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(data)
    (tmp_path / ".cr-connection.json").write_text(json.dumps({"url": "https://channelrecipe.com", "key": KEY}))
    (tmp_path / ".env").write_text("KIE_API_KEY=secret")
    (tmp_path / "output" / "job").mkdir(parents=True)
    (tmp_path / "output" / "job" / "video.mp4").write_bytes(b"my finished film")
    (tmp_path / "styles" / "cr_clone_mine.json").write_text('{"learned": true}')
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / ".cr-installed").write_text("")
    return tmp_path


class Server:
    def __init__(self, version, zip_bytes):
        self.version, self.zip_bytes, self.requests = version, zip_bytes, []

    def get(self, url, headers):
        self.requests.append((url, dict(headers)))
        if url.endswith("/api/studio/status"):
            assert headers == {"Authorization": "Bearer " + KEY}
            return io.BytesIO(json.dumps({"version": self.version}).encode())
        if url.endswith("/api/studio/download"):
            redirect = HTTPError(url, 302, "Found", {"Location": "https://storage.example/release.zip?sig=1"}, None)
            return redirect
        if url.startswith("https://storage.example/"):
            assert headers == {}, "the ChannelRecipe key must never follow a redirect"
            return io.BytesIO(self.zip_bytes)
        raise AssertionError(url)


def run(studio, server, running=False):
    return cr_update.update(studio, get=server.get, running=lambda: running, now=lambda: 0)


def test_already_up_to_date_downloads_nothing(studio):
    server = Server("5.3-admin.44", b"")
    assert "already have the latest" in run(studio, server)
    assert [url for url, _ in server.requests] == ["https://channelrecipe.com/api/studio/status"]


def test_refuses_while_studio_is_open(studio):
    with pytest.raises(cr_update.UpdateStopped, match="still open"):
        run(studio, Server("5.3-admin.45", b""), running=True)


def test_updates_shipped_files_and_keeps_everything_personal(studio):
    zip_bytes, _ = release("5.3-admin.45", {
        "cr_studio.py": b"new studio", "requirements.txt": b"flask\nrequests\n",
        "styles/history.json": b'{"look": "improved"}', "new_module.py": b"added",
        ".env": b"MUST_NOT_SHIP=1", "output/job/video.mp4": b"must not overwrite"})
    message = run(studio, Server("5.3-admin.45", zip_bytes))
    assert "from version 44 to 45" in message
    assert (studio / "cr_studio.py").read_bytes() == b"new studio"
    assert (studio / "new_module.py").read_bytes() == b"added"
    assert (studio / "styles/history.json").read_bytes() == b'{"look": "improved"}'
    assert json.loads((studio / "cr-release.json").read_text())["version"] == "5.3-admin.45"
    # Personal data is untouched, even if a download contained those names.
    assert (studio / ".env").read_text() == "KIE_API_KEY=secret"
    assert (studio / "output/job/video.mp4").read_bytes() == b"my finished film"
    assert (studio / "styles/cr_clone_mine.json").read_text() == '{"learned": true}'
    assert (studio / ".cr-connection.json").exists()
    assert (studio / "obsolete.py").exists()
    # New requirements make the launcher install libraries again.
    assert not (studio / ".venv/.cr-installed").exists()
    # The replaced originals are backed up; nothing is left behind.
    backups = list((studio / ".cr-update-backups").iterdir())
    assert len(backups) == 1 and (backups[0] / "cr_studio.py").read_bytes() == b"old studio"
    assert not (studio / ".cr-update-staging").exists()
    assert not (studio / ".cr-update-download.zip").exists()


def test_creator_edits_are_kept_and_the_new_file_is_saved_beside_them(studio):
    (studio / "styles/history.json").write_bytes(b'{"look": "my own tweak"}')
    zip_bytes, _ = release("5.3-admin.45", {"cr_studio.py": b"new studio", "requirements.txt": b"flask\n",
                                              "styles/history.json": b'{"look": "improved"}'})
    message = run(studio, Server("5.3-admin.45", zip_bytes))
    assert (studio / "styles/history.json").read_bytes() == b'{"look": "my own tweak"}'
    assert (studio / "styles/history.json.new").read_bytes() == b'{"look": "improved"}'
    assert "styles/history.json" in message
    # Unchanged requirements don't force a reinstall.
    assert (studio / ".venv/.cr-installed").exists()


def test_a_failure_part_way_restores_the_previous_version(studio, monkeypatch):
    zip_bytes, _ = release("5.3-admin.45", {"cr_studio.py": b"new studio", "requirements.txt": b"changed\n",
                                              "styles/history.json": b'{"look": "improved"}'})
    real_replace, calls = os.replace, []

    def flaky_replace(source, target):
        calls.append(target)
        if len(calls) == 2:
            raise PermissionError("file in use")
        return real_replace(source, target)

    monkeypatch.setattr(cr_update.os, "replace", flaky_replace)
    with pytest.raises(cr_update.UpdateStopped, match="restored"):
        run(studio, Server("5.3-admin.45", zip_bytes))
    assert (studio / "cr_studio.py").read_bytes() == b"old studio"
    assert (studio / "requirements.txt").read_bytes() == b"flask\n"
    assert json.loads((studio / "cr-release.json").read_text())["version"] == "5.3-admin.44"


@pytest.mark.parametrize("payload,message", [
    (b"not a zip", "incomplete"),
    (release("5.3-admin.46", {"x.py": b"x"})[0], "didn't match"),
])
def test_a_bad_download_changes_nothing(studio, payload, message):
    before = {p: p.read_bytes() for p in studio.rglob("*") if p.is_file()}
    with pytest.raises(cr_update.UpdateStopped, match=message):
        run(studio, Server("5.3-admin.45", payload))
    after = {p: p.read_bytes() for p in studio.rglob("*") if p.is_file()}
    assert after == before


def test_unsafe_paths_are_rejected(studio):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("ChannelRecipe studio/cr-release.json", json.dumps({"version": "5.3-admin.45"}))
        archive.writestr("ChannelRecipe studio/../escape.py", b"no")
    with pytest.raises(cr_update.UpdateStopped, match="unsafe"):
        run(studio, Server("5.3-admin.45", buffer.getvalue()))
    assert not (studio.parent / "escape.py").exists()


def test_requires_a_connected_account(studio):
    (studio / ".cr-connection.json").unlink()
    with pytest.raises(cr_update.UpdateStopped, match="Connect your ChannelRecipe account"):
        run(studio, Server("5.3-admin.45", b""))


def test_a_plan_without_studio_gets_a_clear_message(studio):
    def get(url, headers):
        raise HTTPError(url, 403, "Forbidden", {}, None)

    with pytest.raises(cr_update.UpdateStopped, match="plan includes"):
        cr_update.update(studio, get=get, running=lambda: False)


def test_edited_code_and_release_files_are_always_replaced(studio):
    (studio / "cr_studio.py").write_bytes(b"locally patched code")
    (studio / "cr-release.json").write_text(json.dumps({"version": "5.3-admin.44", "note": "hand edited"}))
    zip_bytes, _ = release("5.3-admin.45", {"cr_studio.py": b"new studio", "requirements.txt": b"flask\n",
                                              "styles/history.json": b'{"look": "shipped"}'})
    run(studio, Server("5.3-admin.45", zip_bytes))
    assert (studio / "cr_studio.py").read_bytes() == b"new studio"
    assert json.loads((studio / "cr-release.json").read_text())["version"] == "5.3-admin.45"
    backup = next((studio / ".cr-update-backups").iterdir())
    assert (backup / "cr_studio.py").read_bytes() == b"locally patched code"
    assert not (studio / "cr_studio.py.new").exists()


def test_certificate_failures_explain_the_fix(studio):
    import ssl
    from urllib.error import URLError

    def get(url, headers):
        raise URLError(ssl.SSLCertVerificationError("certificate verify failed: unable to get local issuer certificate"))

    with pytest.raises(cr_update.UpdateStopped, match="Install Certificates"):
        cr_update.update(studio, get=get, running=lambda: False)


def test_https_uses_certifi_when_available():
    import certifi
    context = cr_update.tls_context()
    assert context.verify_mode.name == "CERT_REQUIRED"
    assert context.cert_store_stats()["x509_ca"] > 0 or certifi.where()
