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


def sha(data):
    return hashlib.sha256(data).hexdigest()


def release(version, files, retired=None):
    shipped = {name: sha(data) for name, data in files.items()}
    files = {**files, "cr-release.json": json.dumps({"version": version}).encode(),
             "cr-files.json": json.dumps({"schema": 1, "version": version, "files": shipped,
                                          "retired": retired or {}}).encode()}
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


class Body(io.BytesIO):
    """An HTTP response body with status and headers, like urllib's."""

    def __init__(self, data, status=200, headers=None, fail_after=None):
        super().__init__(data)
        self.status, self.headers, self.fail_after = status, headers or {}, fail_after

    def read(self, size=-1):
        if self.fail_after is not None and self.tell() >= self.fail_after:
            raise ConnectionResetError("connection dropped")
        if self.fail_after is not None and size and size > 0:
            size = min(size, self.fail_after - self.tell())
        return super().read(size)


class Server:
    def __init__(self, version, zip_bytes, drop_first_after=None, sha=None, ranges=False):
        self.version, self.zip_bytes, self.requests, self.ranges = version, zip_bytes, [], ranges
        self.drop_first_after, self.downloads = drop_first_after, 0
        self.sha = sha if sha is not None else hashlib.sha256(zip_bytes).hexdigest()

    def get(self, url, headers):
        self.requests.append((url, dict(headers)))
        if url.endswith("/api/studio/status"):
            assert headers == {"Authorization": "Bearer " + KEY}
            return io.BytesIO(json.dumps({"version": self.version}).encode())
        if url.endswith("/api/studio/download"):
            redirect = HTTPError(url, 302, "Found", {"Location": "https://storage.example/release.zip?sig=1"}, None)
            return redirect
        if url.startswith("https://storage.example/"):
            assert "Authorization" not in headers, "the ChannelRecipe key must never follow a redirect"
            wanted = headers.get("Range", "").partition("=")[2]
            if wanted and not wanted.endswith("-"):
                if not self.ranges:
                    return Body(self.zip_bytes, headers={"Content-Length": str(len(self.zip_bytes))})
                first, last = (int(n) for n in wanted.split("-"))
                part = self.zip_bytes[first:last + 1]
                self.served = getattr(self, "served", 0) + len(part)
                return Body(part, status=206, headers={
                    "Content-Range": "bytes %d-%d/%d" % (first, first + len(part) - 1, len(self.zip_bytes))})
            self.downloads += 1
            start = int(wanted.rstrip("-")) if wanted else 0
            if start:
                return Body(self.zip_bytes[start:], status=206)
            drop = self.drop_first_after if self.downloads == 1 else None
            return Body(self.zip_bytes, headers={"Content-Length": str(len(self.zip_bytes)),
                                                 "x-amz-meta-sha256": self.sha}, fail_after=drop)
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


def test_a_dropped_download_resumes_where_it_stopped(studio):
    zip_bytes, _ = release("5.3-admin.45", {"cr_studio.py": b"new studio" * 5000, "requirements.txt": b"flask\n"})
    server = Server("5.3-admin.45", zip_bytes, drop_first_after=len(zip_bytes) // 3)
    assert "from version 44 to 45" in run(studio, server)
    # This storage serves no closed byte ranges, so the whole download is used and resumed.
    ranged = [h for url, h in server.requests if url.startswith("https://storage.example/")
              and h.get("Range", "").endswith("-")]
    assert ranged == [{"Range": "bytes=%d-" % (len(zip_bytes) // 3)}]
    assert (studio / "cr_studio.py").read_bytes() == b"new studio" * 5000


def test_a_download_that_does_not_match_the_published_release_changes_nothing(studio):
    zip_bytes, _ = release("5.3-admin.45", {"cr_studio.py": b"new studio", "requirements.txt": b"flask\n"})
    with pytest.raises(cr_update.UpdateStopped, match="didn't match the published release"):
        run(studio, Server("5.3-admin.45", zip_bytes, sha="0" * 64))
    assert (studio / "cr_studio.py").read_bytes() == b"old studio"


def test_retired_files_are_removed_only_while_unchanged(studio):
    (studio / "assets/brand").mkdir(parents=True)
    (studio / "assets/brand/old sample.mp4").write_bytes(b"retired film")
    (studio / "frames").mkdir()
    (studio / "frames/old.jpg").write_bytes(b"old frame")
    (studio / "frames/edited.jpg").write_bytes(b"creator changed this")
    (studio / "output/job/keep.mp4").write_bytes(b"retired film")
    zip_bytes, _ = release("5.3-admin.45", {"cr_studio.py": b"new studio"}, retired={
        "assets/brand/old sample.mp4": [sha(b"retired film")],
        "frames/old.jpg": [sha(b"old frame")],
        "frames/edited.jpg": [sha(b"the shipped frame")],
        # Never honoured, even if a release listed them.
        "output/job/keep.mp4": [sha(b"retired film")],
        "../outside.mp4": [sha(b"retired film")],
    })
    run(studio, Server("5.3-admin.45", zip_bytes))
    assert not (studio / "assets/brand").exists(), "empty retired folders are tidied too"
    assert not (studio / "frames/old.jpg").exists()
    assert (studio / "frames/edited.jpg").read_bytes() == b"creator changed this"
    assert (studio / "output/job/keep.mp4").exists()
    assert (studio / "output/job/video.mp4").exists()


def test_retire_runs_on_its_own_for_installs_updated_by_an_older_updater(studio):
    (studio / "assets").mkdir()
    (studio / "assets/old.mp4").write_bytes(b"retired film")
    (studio / "assets/logo.png").write_bytes(b"still shipped")
    listing = json.loads((studio / "cr-files.json").read_text())
    listing["retired"] = {"assets/old.mp4": [sha(b"retired film")]}
    (studio / "cr-files.json").write_text(json.dumps(listing))
    assert cr_update.retire(studio) == ["assets/old.mp4"]
    assert (studio / "assets/logo.png").exists()
    assert cr_update.retire(studio) == []


def test_an_update_downloads_only_the_files_that_changed(studio):
    big = bytes(range(256)) * 20000  # ~5 MB that both releases share
    for name, data in {"assets/film.bin": big}.items():
        (studio / name).parent.mkdir(parents=True, exist_ok=True)
        (studio / name).write_bytes(data)
    listing = json.loads((studio / "cr-files.json").read_text())
    listing["files"]["assets/film.bin"] = sha(big)
    (studio / "cr-files.json").write_text(json.dumps(listing))
    zip_bytes, _ = release("5.3-admin.45", {"cr_studio.py": b"new studio", "requirements.txt": b"flask\n",
                                            "styles/history.json": b'{"look": "shipped"}', "obsolete.py": b"kept, harmless",
                                            "assets/film.bin": big})
    server = Server("5.3-admin.45", zip_bytes, ranges=True)
    assert "from version 44 to 45" in run(studio, server)
    assert (studio / "cr_studio.py").read_bytes() == b"new studio"
    assert (studio / "assets/film.bin").read_bytes() == big
    assert server.served < len(zip_bytes) / 2, "the unchanged 5 MB file was not downloaded again"


def test_prepare_stages_while_studio_runs_and_apply_installs_later(studio):
    zip_bytes, _ = release("5.3-admin.45", {"cr_studio.py": b"new studio", "requirements.txt": b"flask\n",
                                            "Start on Windows.cmd": b"new launcher"})
    (studio / "Start on Windows.cmd").write_bytes(b"old launcher")
    staged = cr_update.prepare(studio, get=Server("5.3-admin.45", zip_bytes, ranges=True).get)
    assert staged["state"] == "staged" and staged["version"] == "5.3-admin.45"
    assert (studio / "cr_studio.py").read_bytes() == b"old studio", "nothing changes until it's applied"
    result = cr_update.apply_staged(studio, keep={"Start on Windows.cmd"}, now=lambda: 0)
    assert result["version"] == "5.3-admin.45" and not result["requirements_changed"]
    assert (studio / "cr_studio.py").read_bytes() == b"new studio"
    assert (studio / "Start on Windows.cmd").read_bytes() == b"old launcher", "a running launcher is never rewritten"
    assert json.loads((studio / "cr-release.json").read_text())["version"] == "5.3-admin.45"
    assert not (studio / ".cr-update-staging").exists()
    with pytest.raises(cr_update.UpdateStopped, match="No update is ready"):
        cr_update.apply_staged(studio)


def test_prepare_says_when_studio_is_current(studio):
    assert cr_update.prepare(studio, get=Server("5.3-admin.44", b"").get)["state"] == "current"


class Done:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def test_youtube_listing_explains_failures_and_heals_an_outdated_helper(monkeypatch):
    import cr_style_clone as clone
    calls = []
    good = json.dumps({"id": "Pv-xbgsglGE", "title": "Your Life as Every Rank", "duration": 1353, "channel": "POV Tales"})

    def run(command, **kwargs):
        calls.append(command)
        if "pip" in command:
            return Done()
        return Done(stderr="ERROR: [youtube] Unable to extract yt initial data") if len(calls) == 1 else Done(stdout=good)

    monkeypatch.setitem(clone._YT_HELPER, "updated", False)
    rows = clone.list_videos("https://www.youtube.com/@YourPOVtales/videos", 12, run=run)
    assert rows[0]["id"] == "Pv-xbgsglGE"
    assert any("pip" in c and "yt-dlp" in c for c in calls), "an outdated helper is updated once, then retried"
    assert calls[0][1:3] == ["-m", "yt_dlp"], "Studio's own yt-dlp is used, not an older one on PATH"

    def bot(command, **kwargs):
        return Done(stderr="ERROR: Sign in to confirm you're not a bot")
    with pytest.raises(RuntimeError, match="confirm it isn't a bot"):
        clone.list_videos("https://www.youtube.com/@x/videos", 12, run=bot)

    def other(command, **kwargs):
        return Done(stderr="WARNING: x\nERROR: Something unusual happened")
    with pytest.raises(RuntimeError, match=r"\(Something unusual happened\)"):
        clone.list_videos("https://www.youtube.com/@x/videos", 12, run=other)
