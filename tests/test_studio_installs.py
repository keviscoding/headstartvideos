"""Setup's one-click Fix it for FFmpeg, Node.js and the Claude Code CLI (cr_installs). New customers got stuck at
"Install X, then reopen the launcher": on a Mac that meant Homebrew and their password (2026-10-26).

Every download here is faked: no network. Each test builds small archives shaped like the real ones (the same member
paths), pins them by their own SHA-256 the way cr_installs pins the real files, and serves them from a fake `get`.
The Windows layouts (.exe names, zips, PATH with ';') run on every computer, so the Windows CI runs them natively."""
import hashlib
import io
import os
import re
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local_runtime"))
import cr_installs  # noqa: E402

HEX = re.compile(r"[0-9a-f]{64}\Z")


# ── fakes ─────────────────────────────────────────────────────────────────────────────────────────────────────

class Response:
    def __init__(self, status=200, body=b"", error=None):
        self.status_code, self.body, self.error = status, body, error

    def __enter__(self):
        if self.error:
            raise self.error
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, size):
        for i in range(0, len(self.body), max(1, size // 4 or 1)):
            yield self.body[i:i + max(1, size // 4 or 1)]


class Server:
    """url -> bytes (or an exception, or a status), recording what was asked."""
    def __init__(self, files):
        self.files, self.asked = dict(files), []

    def __call__(self, url):
        self.asked.append(url)
        found = self.files.get(url)
        if isinstance(found, BaseException):
            return Response(error=found)
        if isinstance(found, int):
            return Response(status=found)
        if found is None:
            return Response(status=404)
        return Response(body=found)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def zip_bytes(members: dict) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as bundle:
        for name, data in members.items():
            bundle.writestr(name, data)
    return out.getvalue()


def tar_bytes(members: dict, links: dict | None = None) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as bundle:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755
            bundle.addfile(info, io.BytesIO(data))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(name)
            info.type, info.linkname = tarfile.SYMTYPE, target
            bundle.addfile(info)
    return out.getvalue()


def item(url, data, take=None):
    return {"urls": [url] if isinstance(url, str) else url, "size": len(data), "sha256": sha(data),
            **({"take": take} if take else {})}


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    for name in cr_installs.FIXES:
        cr_installs.FIXES[name].clear()
        cr_installs.FIXES[name].update(state="idle", done=0, total=0, message="", tail="")
    monkeypatch.setattr(cr_installs.shutil, "disk_usage", lambda path: SimpleNamespace(free=50 << 30))


# ── what is pinned ────────────────────────────────────────────────────────────────────────────────────────────

def test_every_download_is_pinned_to_a_https_address_a_size_and_a_sha256():
    for key, items in cr_installs.FFMPEG.items():
        names = set()
        for entry in items:
            assert HEX.match(entry["sha256"]) and entry["size"] > 10 << 20, key
            assert all(u.startswith("https://") for u in entry["urls"]), key
            names |= set(entry["take"])
        want = {"ffmpeg.exe", "ffprobe.exe"} if key.startswith("windows") else {"ffmpeg", "ffprobe"}
        assert names == want, key
    assert set(cr_installs.FFMPEG) == {"darwin-arm64", "darwin-x64", "windows-x64"}
    for version, release in cr_installs.NODE.items():
        assert int(version.split(".")[0]) >= cr_installs.NODE_MINIMUM
        for key, (filename, size, digest) in release["builds"].items():
            assert HEX.match(digest) and size > 10 << 20 and version in filename, (version, key)
            assert filename.endswith(".zip" if key.startswith("windows") else ".tar.gz")
    assert set(cr_installs.NODE[cr_installs.NODE_LATEST]["builds"]) == {"darwin-arm64", "darwin-x64", "windows-x64"}
    assert cr_installs.CLAUDE_INSTALL_SH == "https://claude.ai/install.sh"
    assert cr_installs.CLAUDE_INSTALL_PS1 == "https://claude.ai/install.ps1"


@pytest.mark.parametrize("system,machine,translated,key", [
    ("darwin", "arm64", False, "darwin-arm64"), ("darwin", "x86_64", False, "darwin-x64"),
    ("darwin", "x86_64", True, "darwin-arm64"),                 # an Intel Python under Rosetta: the native build
    ("win32", "AMD64", False, "windows-x64"), ("win32", "ARM64", False, "windows-x64"),
    ("linux", "x86_64", False, ""), ("win32", "x86", False, "")])
def test_each_computer_gets_its_own_build_or_none(system, machine, translated, key):
    assert cr_installs.platform_key(system, machine, translated) == key


def test_a_mac_too_old_for_node_24_gets_node_22_and_one_too_old_for_both_gets_none():
    assert cr_installs.node_release("darwin-arm64", (14, 6)) == "24.22.1"
    assert cr_installs.node_release("darwin-x64", (12, 7)) == "22.23.4"
    assert cr_installs.node_release("darwin-x64", (10, 15)) == ""
    assert cr_installs.node_release("windows-x64") == "24.22.1"
    assert cr_installs.node_release("") == ""


# ── downloading ───────────────────────────────────────────────────────────────────────────────────────────────

def test_a_download_is_checked_as_it_arrives_and_the_next_address_is_tried(tmp_path):
    data = b"ffmpeg build" * 1000
    pinned = item(["https://first.example/f.zip", "https://second.example/f.zip"], data)
    server = Server({"https://first.example/f.zip": ConnectionError("reset"), "https://second.example/f.zip": data})
    fix = {"done": 0}
    path = cr_installs.fetch(pinned, tmp_path / "dl", fix, get=server)
    assert path.read_bytes() == data and fix["done"] == len(data)
    assert server.asked == ["https://first.example/f.zip", "https://second.example/f.zip"]
    assert not list((tmp_path / "dl").glob("*.part"))


@pytest.mark.parametrize("served", [b"x" * 1000 + b"tampered", b"y" * 1008, b"x" * 1008 + b"longer than pinned"])
def test_a_file_that_isnt_the_pinned_one_is_refused_and_deleted(tmp_path, served):
    pinned = item("https://host.example/f.zip", b"x" * 1008)
    with pytest.raises(cr_installs.Stop, match="SHA-256") as stop:
        cr_installs.fetch(pinned, tmp_path / "dl", {"done": 0}, get=Server({"https://host.example/f.zip": served}),
                          what="FFmpeg 9.0.2")
    assert "refused it and installed nothing" in str(stop.value) and "FFmpeg 9.0.2" in str(stop.value)
    assert list((tmp_path / "dl").iterdir()) == []


def test_no_answer_says_what_to_check_in_plain_words(tmp_path):
    pinned = item(["https://a.example/f.zip", "https://b.example/f.zip"], b"data")
    server = Server({"https://a.example/f.zip": 403, "https://b.example/f.zip": TimeoutError("read timed out")})
    with pytest.raises(cr_installs.Stop) as stop:
        cr_installs.fetch(pinned, tmp_path / "dl", {"done": 0}, get=server)
    text = str(stop.value)
    assert "a.example answered 403" in text and "b.example: the connection timed out" in text
    assert "phone hotspot" in text and "Fix it" in text


def test_take_copies_only_the_named_members_under_studios_own_names(tmp_path):
    archive = tmp_path / "a.zip"
    archive.write_bytes(zip_bytes({"top/bin/ffmpeg.exe": b"MZ ffmpeg", "top/bin/ffprobe.exe": b"MZ probe",
                                   "../../evil.txt": b"never written", "top/README.txt": b"docs"}))
    cr_installs.take(archive, {"ffmpeg.exe": "top/bin/ffmpeg.exe", "ffprobe.exe": "top/bin/ffprobe.exe"}, tmp_path / "out")
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["ffmpeg.exe", "ffprobe.exe"]
    assert not (tmp_path.parent / "evil.txt").exists()
    with pytest.raises(cr_installs.Stop, match="doesn't hold top/bin/missing"):
        cr_installs.take(archive, {"x": "top/bin/missing"}, tmp_path / "out2")


def test_take_from_a_tarball_refuses_a_link_where_a_program_should_be(tmp_path):
    archive = tmp_path / "node.tar.gz"
    archive.write_bytes(tar_bytes({"node-v1/bin/node": b"#!node"}, links={"node-v1/bin/npm": "../lib/npm-cli.js"}))
    cr_installs.take(archive, {"node": "node-v1/bin/node"}, tmp_path / "ok")
    assert (tmp_path / "ok" / "node").read_bytes() == b"#!node"
    if os.name != "nt":
        assert (tmp_path / "ok" / "node").stat().st_mode & stat.S_IXUSR
    with pytest.raises(cr_installs.Stop, match="isn't a plain file"):
        cr_installs.take(archive, {"npm": "node-v1/bin/npm"}, tmp_path / "bad")


# ── FFmpeg ────────────────────────────────────────────────────────────────────────────────────────────────────

def ffmpeg_pins(monkeypatch, key):
    """Fake builds shaped like the real ones for key, pinned by their own hashes."""
    if key == "windows-x64":
        top = cr_installs._GYAN
        data = zip_bytes({top + "/bin/ffmpeg.exe": b"MZ ffmpeg", top + "/bin/ffprobe.exe": b"MZ ffprobe",
                          top + "/LICENSE": b"GPL"})
        items = [item(["https://github.example/" + top + ".zip", "https://gyan.example/" + top + ".zip"], data,
                      {"ffmpeg.exe": top + "/bin/ffmpeg.exe", "ffprobe.exe": top + "/bin/ffprobe.exe"})]
        files = {"https://github.example/" + top + ".zip": ConnectionError("blocked"),
                 "https://gyan.example/" + top + ".zip": data}
    else:
        a, b = zip_bytes({"ffmpeg": b"\xcf\xfa ffmpeg"}), zip_bytes({"ffprobe": b"\xcf\xfa ffprobe"})
        items = [item("https://riedl.example/ffmpeg.zip", a, {"ffmpeg": "ffmpeg"}),
                 item("https://riedl.example/ffprobe.zip", b, {"ffprobe": "ffprobe"})]
        files = {"https://riedl.example/ffmpeg.zip": a, "https://riedl.example/ffprobe.zip": b}
    monkeypatch.setitem(cr_installs.FFMPEG, key, items)
    return Server(files)


@pytest.mark.parametrize("key", ["darwin-arm64", "darwin-x64", "windows-x64"])
def test_fix_ffmpeg_installs_into_studios_own_folder_checks_it_and_puts_it_first_on_path(tmp_path, monkeypatch, key):
    server = ffmpeg_pins(monkeypatch, key)
    windows = key.startswith("windows")
    checked, environ = [], {"PATH": "C:\\Windows" if windows else "/usr/bin"}

    def check(ffmpeg, ffprobe):
        checked.append((Path(ffmpeg).name, Path(ffprobe).name, Path(ffmpeg).read_bytes()))
        return ""
    started = cr_installs.fix_ffmpeg(home=tmp_path, key=key, get=server, check=check, background=False,
                                     macos=(14, 0), environ=environ)
    assert started["state"] == "starting"
    state = cr_installs.status("ffmpeg")
    assert state["state"] == "done" and "FFmpeg 9.0.2 is installed in Studio's own folder" in state["message"]
    exe = ".exe" if windows else ""
    folder = tmp_path / "tools" / "ffmpeg"
    assert sorted(p.name for p in folder.iterdir()) == sorted(["ffmpeg" + exe, "ffprobe" + exe])
    assert checked and checked[0][:2] == ("ffmpeg" + exe, "ffprobe" + exe), "run once before it is moved into place"
    sep = ";" if windows else os.pathsep
    assert environ["PATH"].split(sep)[0] == str(folder) and environ["PATH"].endswith("Windows" if windows else "/usr/bin")
    assert [p.name for p in (tmp_path / "tools").iterdir()] == ["ffmpeg"], "no download or staging left behind"


def test_an_ffmpeg_that_fails_the_check_is_not_installed_and_the_one_before_stays(tmp_path, monkeypatch):
    server = ffmpeg_pins(monkeypatch, "darwin-arm64")
    old = tmp_path / "tools" / "ffmpeg"
    old.mkdir(parents=True)
    (old / "ffmpeg").write_bytes(b"old")
    cr_installs.fix_ffmpeg(home=tmp_path, key="darwin-arm64", get=server, background=False, macos=(14, 0),
                           environ={}, check=lambda f, p: "This computer's FFmpeg can't burn captions.")
    state = cr_installs.status("ffmpeg")
    assert state["state"] == "failed" and "can't burn captions" in state["message"] and "Discord" in state["message"]
    assert (old / "ffmpeg").read_bytes() == b"old" and sorted(p.name for p in (tmp_path / "tools").iterdir()) == ["ffmpeg"]


def test_a_tampered_ffmpeg_download_installs_nothing(tmp_path, monkeypatch):
    server = ffmpeg_pins(monkeypatch, "darwin-arm64")
    server.files["https://riedl.example/ffprobe.zip"] = zip_bytes({"ffprobe": b"something else"})
    cr_installs.fix_ffmpeg(home=tmp_path, key="darwin-arm64", get=server, background=False, macos=(14, 0),
                           environ={}, check=lambda f, p: "")
    state = cr_installs.status("ffmpeg")
    assert state["state"] == "failed" and "SHA-256" in state["message"]
    assert not (tmp_path / "tools" / "ffmpeg").exists() and list((tmp_path / "tools").iterdir()) == []


def test_ffmpeg_says_plainly_when_this_computer_cant_have_studios_build(tmp_path, monkeypatch):
    cr_installs.fix_ffmpeg(home=tmp_path, key="darwin-x64", get=Server({}), background=False, macos=(11, 7), environ={})
    assert "needs macOS 12 or newer" in cr_installs.status("ffmpeg")["message"]
    cr_installs.fix_ffmpeg(home=tmp_path, key="", get=Server({}), background=False, environ={})
    assert "Studio has no FFmpeg of its own for this computer" in cr_installs.status("ffmpeg")["message"]
    monkeypatch.setattr(cr_installs.shutil, "disk_usage", lambda path: SimpleNamespace(free=100 << 20))
    server = ffmpeg_pins(monkeypatch, "windows-x64")
    cr_installs.fix_ffmpeg(home=tmp_path, key="windows-x64", get=server, background=False, environ={})
    assert "100 MB free" in cr_installs.status("ffmpeg")["message"] and server.asked == []


def test_one_fix_at_a_time_and_progress_in_megabytes(monkeypatch):
    cr_installs.FIXES["ffmpeg"].update(state="downloading", done=30 << 20, total=57 << 20, what="FFmpeg 9.0.2")
    assert cr_installs.status("ffmpeg")["message"] == "Downloading FFmpeg 9.0.2: 30 of 57 MB…"
    ran = []
    again = cr_installs._start("ffmpeg", lambda fix: ran.append(1), background=False)
    assert again["state"] == "downloading" and ran == [], "a second press while it runs starts nothing"


# ── Node.js ───────────────────────────────────────────────────────────────────────────────────────────────────

def node_pins(monkeypatch, key, version="24.22.1", program=b"node binary", sums_hash=None):
    windows = key.startswith("windows")
    top = f"node-v{version}-" + ("win-x64" if windows else key.replace("darwin", "darwin"))
    filename = top + (".zip" if windows else ".tar.gz")
    data = (zip_bytes({top + "/node.exe": program, top + "/npm.cmd": b"npm"}) if windows else
            tar_bytes({top + "/bin/node": program, top + "/lib/node_modules/npm/x.js": b"npm"},
                      links={top + "/bin/npm": "../lib/node_modules/npm/x.js"}))
    builds = dict(cr_installs.NODE[version]["builds"])
    builds[key] = (filename, len(data), sha(data))
    monkeypatch.setitem(cr_installs.NODE, version, {**cr_installs.NODE[version], "builds": builds})
    sums = f"{sums_hash or sha(data)}  {filename}\n0000  other-file.tar.gz\n".encode()
    base = f"{cr_installs.NODE_DIST}v{version}/"
    return Server({base + "SHASUMS256.txt": sums, base + filename: data})


@pytest.mark.parametrize("key", ["darwin-arm64", "windows-x64"])
def test_fix_node_takes_only_the_node_program_checks_it_and_puts_it_first_on_path(tmp_path, monkeypatch, key):
    server = node_pins(monkeypatch, key)
    windows = key.startswith("windows")
    environ = {"PATH": "/usr/local/bin"}
    started = []
    cr_installs.fix_node(home=tmp_path, key=key, get=server, background=False, macos=(15, 0), environ=environ,
                         check=lambda path: started.append(Path(path).name) or (24, 22, 1))
    state = cr_installs.status("node")
    assert state["state"] == "done" and "Node.js 24.22.1 is installed" in state["message"], state
    program = "node.exe" if windows else "node"
    folder = tmp_path / "tools" / "node"
    assert [p.name for p in folder.iterdir()] == [program] and started == [program]
    assert (folder / program).read_bytes() == b"node binary"
    assert environ["PATH"].startswith(str(folder))
    assert server.asked[0].endswith("/SHASUMS256.txt"), "nodejs.org's own list is read before the download"


def test_node_refuses_when_nodejs_orgs_list_disagrees_with_the_pin(tmp_path, monkeypatch):
    server = node_pins(monkeypatch, "darwin-x64", sums_hash="f" * 64)
    cr_installs.fix_node(home=tmp_path, key="darwin-x64", get=server, background=False, macos=(15, 0), environ={},
                         check=lambda path: (24, 22, 1))
    state = cr_installs.status("node")
    assert state["state"] == "failed" and "checksum list doesn't match" in state["message"]
    assert len(server.asked) == 1 and not (tmp_path / "tools" / "node").exists()


def test_node_22_for_an_older_mac_and_a_node_that_wont_start_is_not_installed(tmp_path, monkeypatch):
    server = node_pins(monkeypatch, "darwin-x64", version="22.23.4")
    cr_installs.fix_node(home=tmp_path, key="darwin-x64", get=server, background=False, macos=(12, 7), environ={},
                         check=lambda path: (22, 23, 4))
    assert "Node.js 22.23.4 is installed" in cr_installs.status("node")["message"]
    server = node_pins(monkeypatch, "darwin-arm64")
    cr_installs.fix_node(home=tmp_path / "other", key="darwin-arm64", get=server, background=False, macos=(15, 0),
                         environ={}, check=lambda path: None)
    assert "didn't start on this computer" in cr_installs.status("node")["message"]
    assert not (tmp_path / "other" / "tools" / "node").exists()
    cr_installs.fix_node(home=tmp_path, key="darwin-x64", get=Server({}), background=False, macos=(10, 15), environ={})
    assert "too old for Node.js 22" in cr_installs.status("node")["message"]


# ── checks ────────────────────────────────────────────────────────────────────────────────────────────────────

def fake_ffmpeg(filters=" T. ass V->V\n TS subtitles V->V", encoders=" V libx264 H.264\n A aac AAC\n A libmp3lame MP3",
                code=0):
    def run(command, **kw):
        out = filters if command[-1] == "-filters" else encoders if command[-1] == "-encoders" else ""
        return SimpleNamespace(returncode=code, stdout=out)
    return run


def test_video_tools_problem_names_what_this_ffmpeg_cant_do():
    which = lambda name: "/bin/" + name
    assert cr_installs.video_tools_problem(which=which, run=fake_ffmpeg()) == ""
    assert cr_installs.video_tools_problem(which=which, run=fake_ffmpeg(filters=" T. scale V->V")) == \
        "This computer's FFmpeg can't burn captions."
    assert cr_installs.video_tools_problem(which=which, run=fake_ffmpeg(encoders=" A aac AAC")) == \
        "This computer's FFmpeg can't make H.264 video or make MP3s."
    assert cr_installs.video_tools_problem(which=lambda n: None, run=fake_ffmpeg()) == \
        "FFmpeg's ffprobe isn't installed or won't start."
    assert cr_installs.video_tools_problem(which=lambda n: "/bin/ffprobe" if n == "ffprobe" else None,
                                           run=fake_ffmpeg()) == "FFmpeg isn't installed."


@pytest.mark.parametrize("output,ready,words", [("v24.22.1\n", True, ""), ("v22.0.0", True, ""),
                                                ("v20.11.1", False, "Node.js 20.11.1 is too old"),
                                                ("", False, "won't start")])
def test_node_status_needs_node_22_for_the_youtube_downloader(output, ready, words):
    run = lambda command, **kw: SimpleNamespace(returncode=0 if output else 1, stdout=output)
    status = cr_installs.node_status(which=lambda name: "/usr/local/bin/node", run=run)
    assert status["ready"] is ready and words in status["reason"]
    assert cr_installs.node_status(which=lambda name: None)["reason"] == "Node.js isn't installed."


@pytest.mark.parametrize("windows", [False, True])
def test_use_studio_tools_puts_studios_folders_first_once(tmp_path, windows):
    exe = ".exe" if windows else ""
    sep = ";" if windows else os.pathsep
    environ = {"PATH": sep.join(["/a", "/b"])}
    assert cr_installs.use_studio_tools(tmp_path, environ, windows) == [] and environ["PATH"] == sep.join(["/a", "/b"])
    for name in ("ffmpeg/ffmpeg", "ffmpeg/ffprobe", "node/node"):
        path = tmp_path / "tools" / (name + exe)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    ours = [str(tmp_path / "tools" / "ffmpeg"), str(tmp_path / "tools" / "node")]
    assert cr_installs.use_studio_tools(tmp_path, environ, windows) == ours
    cr_installs.use_studio_tools(tmp_path, environ, windows)
    assert environ["PATH"].split(sep) == ours + ["/a", "/b"], "idempotent: never added twice"
    (tmp_path / "tools" / "ffmpeg" / ("ffprobe" + exe)).unlink()
    assert cr_installs.tool_dirs(tmp_path, windows) == [tmp_path / "tools" / "node"], "half an FFmpeg isn't used"


# ── Claude Code ───────────────────────────────────────────────────────────────────────────────────────────────

def test_claude_is_installed_with_anthropics_installers_downloaded_whole_before_they_run():
    """Piped into bash, a script cut off mid-download could run half way (review, 2026-10-26): it's saved first."""
    bash, flag, script = cr_installs.claude_install_command(False)
    assert (bash, flag) == ("/bin/bash", "-c")
    assert 'curl -fsSL -o "$t" https://claude.ai/install.sh && /bin/bash "$t"' in script
    assert "| bash" not in script and "trap 'rm -f \"$t\"' EXIT" in script and "sudo" not in script
    command = cr_installs.claude_install_command(True)
    assert command[0].lower().endswith(("powershell.exe", "pwsh", "pwsh.exe", "powershell"))
    assert command[1:4] == ["-NoProfile", "-NonInteractive", "-Command"]
    assert command[4] == "$ProgressPreference = 'SilentlyContinue'; irm https://claude.ai/install.ps1 | iex"
    assert "ExecutionPolicy" not in " ".join(command)


@pytest.mark.skipif(os.name == "nt", reason="the Mac and Linux command runs in bash")
def test_the_saved_script_runs_only_when_its_download_finished(tmp_path, monkeypatch):
    script = tmp_path / "install.sh"
    script.write_text('#!/bin/bash\necho "ran $0"\nexit 3\n', encoding="utf-8")
    monkeypatch.setattr(cr_installs, "CLAUDE_INSTALL_SH", script.as_uri())
    bash, flag, command = cr_installs.claude_install_command(False)
    env = {**os.environ, "TMPDIR": str(tmp_path)}
    done = subprocess.run([bash, flag, command], capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)
    assert done.returncode == 3 and "ran " + str(tmp_path) in done.stdout, "the installer's own answer comes back"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["install.sh"], "the saved copy is removed"
    monkeypatch.setattr(cr_installs, "CLAUDE_INSTALL_SH", (tmp_path / "missing.sh").as_uri())
    failed = subprocess.run(list(cr_installs.claude_install_command(False)), capture_output=True, text=True,
                            encoding="utf-8", env=env, timeout=30)
    assert failed.returncode != 0 and "ran " not in failed.stdout, "a failed download runs nothing"


class Child:
    """A stand-in installer process."""
    def __init__(self, output="", code=0, hang=False):
        self.output, self.returncode, self.hang, self.pid, self.waited = output, code, hang, 4242, 0

    def communicate(self, timeout=None):
        self.waited += 1
        if self.hang and self.waited == 1:
            raise subprocess.TimeoutExpired("installer", timeout)
        return self.output, None


@pytest.mark.parametrize("windows", [False, True])
def test_fix_claude_runs_the_installer_in_a_clean_environment_then_finds_claude(monkeypatch, windows):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-never-passed")
    monkeypatch.setenv("PSModulePath", r"C:\Program Files\PowerShell\7\Modules")   # Studio started from pwsh
    found = []
    ran = []

    def popen(command, **kw):
        ran.append((command, kw))
        found.append(r"C:\Users\Zoé\.local\bin\claude.exe" if windows else "/Users/zoe/.local/bin/claude")
        return Child("Setting up Claude Code...\n✅ Installation complete!\n")
    cr_installs.fix_claude(find=lambda: found[-1] if found else None, popen=popen, background=False, windows=windows)
    state = cr_installs.status("claude")
    assert state["state"] == "done" and "Claude Code is installed" in state["message"]
    assert "Open the sign-in window" in state["message"] and "auth login" in state["message"]
    (command, kw), = ran
    assert command == cr_installs.claude_install_command(windows)
    assert kw["stdin"] is subprocess.DEVNULL and kw["encoding"] == "utf-8" and kw["stderr"] is subprocess.STDOUT
    assert "ANTHROPIC_API_KEY" not in kw["env"], "the installer never sees a key"
    # Windows PowerShell 5.1 started with PowerShell 7's module path can't load Get-FileHash (Windows CI, 2026-10-26)
    assert any(k.upper() == "PSMODULEPATH" for k in kw["env"]) is (not windows)    # Windows' names are upper-case
    # its own process group (Mac, Linux) or tree root (Windows): a timeout stops all of it
    assert kw.get("start_new_session") is True if os.name != "nt" else "creationflags" in kw


def test_fix_claude_does_nothing_when_claude_is_there_and_says_why_an_install_failed():
    calls = []
    cr_installs.fix_claude(find=lambda: "/x/claude", popen=lambda *a, **k: calls.append(a), background=False)
    assert calls == [] and "already installed" in cr_installs.status("claude")["message"]
    refused = "curl: (22) The requested URL returned error: 403\nsee https://www.anthropic.com/supported-countries"
    cr_installs.fix_claude(find=lambda: None, background=False, popen=lambda *a, **k: Child(refused, 22))
    state = cr_installs.status("claude")
    assert state["state"] == "failed" and "supported countries" in state["message"] and "403" in state["tail"]
    stuck, killed = Child(hang=True), []
    cr_installs.fix_claude(find=lambda: None, popen=lambda *a, **k: stuck, kill=killed.append, background=False)
    assert "didn't finish within 15 minutes" in cr_installs.status("claude")["message"]
    assert killed == [stuck], "the whole installer is stopped, so a second Install never runs beside it"


def test_the_sign_in_window_runs_claude_by_its_full_path(tmp_path, monkeypatch):
    opened = []
    popen = lambda command, **kw: opened.append((command, kw))
    path = "/Users/Zoé O'Neil/.local/share/claude/versions/2.1.300"
    result = cr_installs.open_sign_in(find=lambda: path, popen=popen, home=tmp_path, windows=False, mac=True)
    script = tmp_path / "tools" / "claude-sign-in.command"
    assert result["opened"] is True and opened == [(["/usr/bin/open", "-a", "Terminal", str(script)], {})]
    text = script.read_text(encoding="utf-8")
    assert "'/Users/Zoé O'\"'\"'Neil/.local/share/claude/versions/2.1.300' auth login --claudeai" in text
    if os.name != "nt":
        assert script.stat().st_mode & stat.S_IXUSR
    opened.clear()
    path = r"C:\Users\Zoé O'Neil\.local\bin\claude.exe"
    cr_installs.open_sign_in(find=lambda: path, popen=popen, home=tmp_path, windows=True, mac=False)
    (command, kw), = opened
    assert command[1:4] == ["-NoExit", "-NoProfile", "-Command"]
    assert command[4].endswith(r"& 'C:\Users\Zoé O''Neil\.local\bin\claude.exe' auth login --claudeai")
    assert kw["creationflags"] == getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10)
    assert not any(k.upper() == "PSMODULEPATH" for k in kw["env"]), "Windows PowerShell builds its own module path"
    with pytest.raises(cr_installs.Stop, match="Install Claude Code first"):
        cr_installs.open_sign_in(find=lambda: None, popen=popen, home=tmp_path)


def test_claude_from_the_official_installer_is_found_where_it_puts_it(tmp_path, monkeypatch):
    """The installer puts claude in ~/.local/bin (Windows: %USERPROFILE%\\.local\\bin\\claude.exe) and leaves PATH
    alone: Studio finds it there without a restart."""
    import cr_claude
    monkeypatch.setattr(cr_claude.shutil, "which", lambda name: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(cr_claude.Path, "home", classmethod(lambda cls: tmp_path))
    assert cr_claude.find_executable() is None
    claude = tmp_path / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude")
    claude.parent.mkdir(parents=True)
    claude.write_bytes(b"binary")
    claude.chmod(0o755)
    assert Path(cr_claude.find_executable()) == claude.resolve()


def test_the_swap_waits_out_an_antivirus_scan_with_a_growing_pause(tmp_path):
    """Norton, McAfee and Avast can scan a fresh ffmpeg.exe for half a minute (review, 2026-10-26)."""
    waits, notes = [], []
    source, target = tmp_path / "a", tmp_path / "b"
    with pytest.raises(OSError):
        cr_installs._rename(source, target, sleep=waits.append, waiting=lambda: notes.append(1))   # never there
    assert waits[:5] == [0.5, 1.0, 2.0, 4.0, 8.0] and set(waits[5:]) == {8.0}
    assert 30 <= sum(waits) <= 60 and len(notes) == len(waits)


def test_the_swap_waits_out_a_brief_lock_and_keeps_the_old_install_when_it_cant(tmp_path, monkeypatch):
    """Windows refuses a rename while an antivirus scans a program just written: the swap tries again."""
    real = Path.rename
    locked = {"left": 2}

    def rename(self, target):
        if self.name == ".ffmpeg-1-2" and locked["left"]:
            locked["left"] -= 1
            raise PermissionError(13, "in use")
        return real(self, target)
    monkeypatch.setattr(Path, "rename", rename)
    final = tmp_path / "tools" / "ffmpeg"
    final.mkdir(parents=True)
    (final / "ffmpeg").write_bytes(b"old")
    stage = tmp_path / "tools" / ".ffmpeg-1-2"
    stage.mkdir()
    (stage / "ffmpeg").write_bytes(b"new")
    fix = {"message": ""}
    cr_installs._swap(stage, final, sleep=lambda s: None, fix=fix)
    assert "antivirus" in fix["message"], "the progress line says why it waits"
    assert (final / "ffmpeg").read_bytes() == b"new" and locked["left"] == 0
    assert sorted(p.name for p in (tmp_path / "tools").iterdir()) == ["ffmpeg"]
    stage.mkdir()
    (stage / "ffmpeg").write_bytes(b"newer")
    locked["left"] = 99
    with pytest.raises(cr_installs.Stop, match="still using it"):
        cr_installs._swap(stage, final, sleep=lambda s: None, patience=2)
    assert (final / "ffmpeg").read_bytes() == b"new", "the working install stays"


def test_an_install_left_aside_by_a_failed_undo_comes_back_and_is_kept_until_a_new_one_is_in(tmp_path):
    tools = tmp_path / "tools"
    (tools / ".ffmpeg-old-100").mkdir(parents=True)
    (tools / ".ffmpeg-old-100" / "ffmpeg").write_bytes(b"working")
    (tools / ".ffmpeg-stage-1-2").mkdir()
    stage = cr_installs._stage(tmp_path, "ffmpeg")
    assert (tools / "ffmpeg" / "ffmpeg").read_bytes() == b"working", "put back before anything is downloaded"
    assert not (tools / ".ffmpeg-stage-1-2").exists() and stage.name.startswith(".ffmpeg-stage-")
    (tools / ".ffmpeg-old-200").mkdir()                          # another left aside: kept until the swap succeeds
    (stage / "ffmpeg").write_bytes(b"new")
    cr_installs._swap(stage, tools / "ffmpeg", sleep=lambda s: None)
    assert (tools / "ffmpeg" / "ffmpeg").read_bytes() == b"new" and sorted(p.name for p in tools.iterdir()) == ["ffmpeg"]


def test_a_download_redirected_to_plain_http_is_not_followed(tmp_path):
    data = b"x" * 100

    class Moved(Response):
        url = "http://mirror.example/f.zip"
    pinned = item("https://host.example/f.zip", data)
    with pytest.raises(cr_installs.Stop, match="insecure address"):
        cr_installs.fetch(pinned, tmp_path / "dl", {"done": 0}, get=lambda url: Moved(body=data))


def test_fix_ffmpeg_and_node_say_already_installed_when_studios_own_copy_works(tmp_path, monkeypatch):
    """Free tools Claude Code can run unasked: nothing is downloaded or switched when Studio's copy works."""
    for name in ("ffmpeg/ffmpeg", "ffmpeg/ffprobe", "node/node"):
        (tmp_path / "tools" / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "tools" / name).write_bytes(b"x")
    server, environ = Server({}), {"PATH": "/usr/bin"}
    cr_installs.fix_ffmpeg(home=tmp_path, key="darwin-arm64", get=server, check=lambda f, p: "", background=False,
                           macos=(14, 0), environ=environ, busy=lambda: True)
    assert "already installed in Studio's own folder" in cr_installs.status("ffmpeg")["message"]
    cr_installs.fix_node(home=tmp_path, key="darwin-arm64", get=server, check=lambda path: (24, 22, 1),
                         background=False, macos=(14, 0), environ=environ, busy=lambda: True)
    assert "Node.js 24.22.1 is already installed" in cr_installs.status("node")["message"]
    assert server.asked == [] and environ["PATH"].split(os.pathsep)[0] == str(tmp_path / "tools" / "ffmpeg")
    cr_installs.fix_node(home=tmp_path, key="darwin-arm64", get=node_pins(monkeypatch, "darwin-arm64"),
                         check=lambda path: (24, 22, 1) if "-stage-" in path else (20, 1, 0),
                         background=False, macos=(14, 0), environ={})
    assert "Node.js 24.22.1 is installed" in cr_installs.status("node")["message"], "an old copy of its own is replaced"


def test_nothing_is_switched_while_a_video_is_being_made(tmp_path, monkeypatch):
    server = ffmpeg_pins(monkeypatch, "darwin-arm64")
    cr_installs.fix_ffmpeg(home=tmp_path, key="darwin-arm64", get=server, check=lambda f, p: "", background=False,
                           macos=(14, 0), environ={}, busy=lambda: True)
    state = cr_installs.status("ffmpeg")
    assert state["state"] == "failed" and "never switches its FFmpeg in the middle of one" in state["message"]
    assert server.asked == [], "refused before anything is downloaded"
    started = iter([False, True])                               # a video starts while it downloads
    old = tmp_path / "tools" / "ffmpeg"
    old.mkdir(parents=True)
    (old / "ffmpeg").write_bytes(b"old")
    cr_installs.fix_ffmpeg(home=tmp_path, key="darwin-arm64", get=server, check=lambda f, p: "", background=False,
                           macos=(14, 0), environ={}, busy=lambda: next(started))
    assert "never switches" in cr_installs.status("ffmpeg")["message"] and (old / "ffmpeg").read_bytes() == b"old"
    assert sorted(p.name for p in (tmp_path / "tools").iterdir()) == ["ffmpeg"]
    node = node_pins(monkeypatch, "windows-x64")
    cr_installs.fix_node(home=tmp_path, key="windows-x64", get=node, check=lambda path: (24, 22, 1), background=False,
                         environ={}, busy=lambda: True)
    assert "never switches its Node.js" in cr_installs.status("node")["message"] and node.asked == []


def test_the_hint_says_how_big_the_download_is_on_this_computer(monkeypatch):
    assert cr_installs.download_mb("ffmpeg", "darwin-arm64") == 54 and cr_installs.download_mb("ffmpeg", "windows-x64") == 109
    assert cr_installs.download_mb("node", "windows-x64") == 35 and cr_installs.download_mb("ffmpeg", "") == 0
    monkeypatch.setattr(cr_installs, "platform_key", lambda *a: "windows-x64")
    assert "(a 109 MB download, no password needed)" in cr_installs.fix_hint("ffmpeg")
    monkeypatch.setattr(cr_installs, "platform_key", lambda *a: "")
    assert cr_installs.fix_hint("node").startswith("Install Node.js 22 or newer from https://nodejs.org")


# ── an old Node.js stops only videos that download from YouTube (coordinator, 2026-10-26) ──────────────────────

import cr_readiness  # noqa: E402


def test_an_old_node_is_a_note_in_setup_and_a_missing_one_still_blocks(monkeypatch):
    """Customers on Node 18/20 who make recipes without YouTube kept working: an old Node never blocks setup."""
    monkeypatch.setattr(cr_readiness.cr_bridge, "load_settings", lambda: {"brain_mode": "channelrecipe",
                                                                          "costs_accepted": True})
    monkeypatch.setattr(cr_readiness.cr_claude, "status", lambda *a, **k: {"installed": True, "ready": True})
    monkeypatch.setattr(cr_readiness.cr_claude, "models_verified", lambda *a: True)
    monkeypatch.setattr(cr_readiness.cr_mcp, "is_registered", lambda cfg: True)
    base = {"python": True, "ffmpeg": True, "browser": True, "local_version": "v",
            "account": {"version": "v", "brain": {"connected": True}}}
    old = cr_readiness.basic({**base, "node": True, "node_youtube": False, "node_reason": "Node.js 20.11.1 is too old "
                              "for the YouTube downloader (it needs 22 or newer). Press Fix it next to YouTube helper."})
    row = next(r for r in old["checks"] if r["key"] == "node")
    assert old["ready"] is True and row["ready"] is True and "Node.js 20.11.1 is too old" in row["message"]
    missing = cr_readiness.basic({**base, "node": False, "node_youtube": False,
                                  "node_reason": "Node.js isn't installed. Press Fix it next to YouTube helper."})
    assert missing["ready"] is False and "Node.js isn't installed" in " ".join(missing["blockers"])
    current = cr_readiness.basic({**base, "node": True, "node_youtube": True})
    assert next(r for r in current["checks"] if r["key"] == "node")["message"] == ""


def preset_studio(features, shorts=None):
    info = {"features": {"offered": list(features), "on": list(features)}, **({"shorts": shorts} if shorts else {})}
    return SimpleNamespace(
        mv=SimpleNamespace(prompts=SimpleNamespace(PROMPT_SETS={"doc": {}}), STYLE_INFO={"doc": info}),
        styles=SimpleNamespace(load_all=lambda: None, STYLES={"doc": info}),
        features=SimpleNamespace(capabilities=lambda: {"base": {"voice": {"ok": True}}, "features": {}},
                                 KEYS=list(features), EXPLICIT=[], BY_KEY={},
                                 resolve=lambda info, asked: {k: True for k in features}))


@pytest.mark.parametrize("features,shorts,node_youtube,refused", [
    ([], None, False, False),                                   # a stories video with Node 20: made as before
    (["youtube"], None, False, True),                           # Research footage downloads from YouTube
    ([], {"pipeline": "ranking"}, False, True),                 # Ranking Shorts download their clips from YouTube
    (["youtube"], None, True, False),                           # Node 22 or newer (or Deno)
    (["youtube"], None, None, False)])                          # an older check without node_youtube: never refused
def test_an_old_node_refuses_only_videos_that_download_from_youtube(monkeypatch, features, shorts, node_youtube,
                                                                    refused):
    monkeypatch.setattr(cr_readiness, "basic", lambda checks: {"checks": [], "claude_code": {}, "brain_mode": "claudecode"})
    monkeypatch.setattr(cr_readiness, "proof", lambda *a, **k: {})
    monkeypatch.setattr(cr_readiness.cr_media, "active", lambda task: False)
    checks = {"node": True} if node_youtube is None else {"node": True, "node_youtube": node_youtube}
    result = cr_readiness.preset(preset_studio(features, shorts), checks, {"style": "doc"})
    stops = [c for c in result["checks"] if c["key"] == "youtube_node"]
    assert bool(stops) is refused and result["ready"] is (not refused)
    if refused:
        assert stops[0]["message"] == ("This video downloads from YouTube, which needs Node.js 22 or newer: press "
                                       "Fix it in Setup.")
        assert stops[0]["message"] in result["blockers"]


def test_setup_status_reports_node_for_youtube_apart_from_node_at_all(monkeypatch):
    import cr_studio
    monkeypatch.setattr(cr_studio, "video_tools_ready", lambda *a: True)
    monkeypatch.setattr(cr_studio, "tool_ready", lambda name, flag="-version": name == "node")   # Node runs, no Deno
    monkeypatch.setattr(cr_installs, "node_status", lambda **k: {"ready": False, "version": "20.11.1",
                                                                 "reason": "Node.js 20.11.1 is too old for the YouTube "
                                                                           "downloader (it needs 22 or newer)."})
    checks = cr_studio.tool_checks()
    assert checks["node"] is True and checks["node_youtube"] is False and checks["node_version"] == "20.11.1"
    assert checks["node_reason"].startswith("Node.js 20.11.1 is too old") and "Fix it" in checks["node_reason"]
    monkeypatch.setattr(cr_studio, "tool_ready", lambda name, flag="-version": name == "deno")
    assert cr_studio.tool_checks()["node_youtube"] is True, "Deno solves YouTube's challenge too"
    monkeypatch.setattr(cr_studio, "tool_ready", lambda *a, **k: False)
    monkeypatch.setattr(cr_installs, "node_status", lambda **k: {"ready": False, "version": "",
                                                                 "reason": "Node.js isn't installed."})
    assert cr_studio.tool_checks()["node"] is False
