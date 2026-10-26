"""Setup's one-click Fix it for the tools Studio needs from outside Python: FFmpeg (with ffprobe), Node.js and the
Claude Code command-line tool. Nothing installs until the creator presses Fix it on the setup page (or asks Claude
Code, whose fix tools call the same routes).

FFmpeg and Node.js go into this Studio's own folder (tools/ffmpeg, tools/node): no password, no sudo, nothing in the
computer's system folders, the creator's own FFmpeg or Node.js left as they are. Every download is pinned to one
version and its SHA-256, and a file that doesn't match is refused before anything is unpacked. Only the programs
Studio runs are taken out of each archive (named, never a path from inside it), into a staging folder; they are run
once there to prove they work, and only then moved into place. use_studio_tools() puts those folders first on PATH
for Studio and every process it starts: workers, the engine's ffmpeg calls, yt-dlp's Node.

Claude Code is installed by Anthropic's own installer (which checks its download against Anthropic's signed release
manifest) into the creator's home folder: ~/.local/bin/claude, or %USERPROFILE%\\.local\\bin\\claude.exe on Windows.
cr_claude.find_executable already looks there, so Studio finds it without a restart. The installer doesn't add that
folder to PATH, so `claude` typed in a new Terminal often isn't found: open_sign_in() starts the sign-in by its full
path in a Terminal (PowerShell on Windows) window instead.

Python can't be fixed from here: the launcher needs it before Studio can start (the launchers say what to do)."""
from __future__ import annotations

import hashlib
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

HOME = Path(__file__).resolve().parent
WINDOWS = os.name == "nt"


# ── what is downloaded, pinned (researched and checked 2026-10-26) ─────────────────────────────────────────────
#
# FFmpeg 9.0.2, FFmpeg's release of 2026-09-18 (https://ffmpeg.org/download.html).
# - macOS, Apple silicon and Intel: Martin Riedl's static builds, https://ffmpeg.martin-riedl.de (release builds keep
#   their own folder). Signed "Developer ID Application: Martin Riedl (KU3N25YGLU)". Built with libass (the
#   subtitles and ass filters), libx264, libmp3lame and FFmpeg's own AAC encoder; needs macOS 12 or newer and only
#   the system's own libraries. ffmpeg.org's own macOS link (evermeet.cx) builds for Intel only. Each SHA-256 is the
#   site's .sha256 beside the file, and our own downloads on 2026-10-26 hashed the same. Checked for real on a Mac
#   that day: the filters and encoders listed, and captions burned with fontsdir (arm64 natively, Intel through
#   Rosetta).
# - Windows (x64; Windows 11 on ARM runs it emulated): Gyan Doshi's "essentials" release build, which ffmpeg.org's
#   download page links (libass, libx264, libmp3lame among its libraries), fetched from its GitHub release mirror
#   (GyanD/codexffmpeg, tag 9.0.2) with gyan.dev's packages/ folder as the second address. gyan.dev's .sha256 and
#   GitHub's asset digest give the same SHA-256; the zip's listing (read 2026-10-26) has bin/ffmpeg.exe and
#   bin/ffprobe.exe under ffmpeg-9.0.2-essentials_build/.
FFMPEG_VERSION = "9.0.2"
FFMPEG_MACOS = (12, 0)
_RIEDL = "https://ffmpeg.martin-riedl.de/download/macos/"
_GYAN = "ffmpeg-9.0.2-essentials_build"
FFMPEG = {
    "darwin-arm64": [
        {"urls": [_RIEDL + "arm64/1789931890_9.0.2/ffmpeg.zip"], "size": 28395699,
         "sha256": "c8ed4c4e6978a03c485edbfe4e0a5dc2380f8a30bba5150531b31b094492d924", "take": {"ffmpeg": "ffmpeg"}},
        {"urls": [_RIEDL + "arm64/1789931890_9.0.2/ffprobe.zip"], "size": 28317701,
         "sha256": "fcbe839537485eaee7a7a8bc5cbc0f90d53617e80943e8a5b2e31cb851197ea6", "take": {"ffprobe": "ffprobe"}},
    ],
    "darwin-x64": [
        {"urls": [_RIEDL + "amd64/1789931006_9.0.2/ffmpeg.zip"], "size": 33816391,
         "sha256": "7c6b4125b191cbf773832dc51f424cf2b6bb7da43007d1e066f95909e47cacd4", "take": {"ffmpeg": "ffmpeg"}},
        {"urls": [_RIEDL + "amd64/1789931006_9.0.2/ffprobe.zip"], "size": 33719233,
         "sha256": "2322438ed2f6319a691291b247d09c69dcaa3a982460d1f269a7e1af335cfdfd", "take": {"ffprobe": "ffprobe"}},
    ],
    "windows-x64": [
        {"urls": ["https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/" + _GYAN + ".zip",
                  "https://www.gyan.dev/ffmpeg/builds/packages/" + _GYAN + ".zip"], "size": 114768076,
         "sha256": "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba",
         "take": {"ffmpeg.exe": _GYAN + "/bin/ffmpeg.exe", "ffprobe.exe": _GYAN + "/bin/ffprobe.exe"}},
    ],
}

# Node.js from nodejs.org (https://nodejs.org/dist/index.json, read 2026-10-26). 24.22.1 "Krypton" is the Active LTS
# (a security release of 2026-10-20); 22.23.4 "Jod", the Maintenance LTS of the same day, only for Macs older than
# macOS 13.5, which Node 24's Mac builds need (Node 22's run on macOS 11 and newer). yt-dlp's YouTube challenge solver
# needs Node 22 or newer. Each SHA-256 is the archive's line in that release's SHASUMS256.txt
# (https://nodejs.org/dist/vX/SHASUMS256.txt). The pinned hash is the guarantee; fix_node also reads that list again at
# install time and refuses if it disagrees (a second look from the same host, without its GPG signature). Only the node program is taken out (Studio never runs npm). Checked for real on
# 2026-10-26: both darwin-arm64 archives hashed as listed and their node ran (macOS minimums read from the binaries).
NODE_MINIMUM = 22
NODE_DIST = "https://nodejs.org/dist/"
NODE = {
    "24.22.1": {"macos": (13, 5), "builds": {
        "darwin-arm64": ("node-v24.22.1-darwin-arm64.tar.gz", 53062778,
                         "89c682593d76cd44233fa53b1563264e7de9cbb189cad169135c5b4fadfbc0a7"),
        "darwin-x64": ("node-v24.22.1-darwin-x64.tar.gz", 54382566,
                       "d132557e7ee2b85af08b0c8c34338e65c6430f2238d01f727728ef42dc83b035"),
        "windows-x64": ("node-v24.22.1-win-x64.zip", 37748064,
                        "d2046d28c2c3dcd211ac5145c0bf30e2047ef9dfb306693c8c6e2eccd8b95fa5")}},
    "22.23.4": {"macos": (11, 0), "builds": {
        "darwin-arm64": ("node-v22.23.4-darwin-arm64.tar.gz", 49996369,
                         "c9e1dbc748b33ed2d14f42445191ecaf14c56259c7a9a26d0042c49ec2b66428"),
        "darwin-x64": ("node-v22.23.4-darwin-x64.tar.gz", 51190214,
                       "efcc2b0db6f94bf53eddb038fe2b370277a042bd29e4f19b8f8e23598795b819")}},
}
NODE_LATEST = "24.22.1"

# Claude Code: Anthropic's official installers (https://code.claude.com/docs/en/setup, read 2026-10-26). They take no
# admin rights; Windows needs no Git (Claude Code uses PowerShell without it).
CLAUDE_INSTALL_SH = "https://claude.ai/install.sh"
CLAUDE_INSTALL_PS1 = "https://claude.ai/install.ps1"
CLAUDE_SECONDS = 15 * 60

# room on the disk while a fix runs: the download plus what is unpacked from it (FFmpeg for Windows is the largest)
NEEDS_MB = {"ffmpeg": 700, "node": 400}
LABEL = {"ffmpeg": "FFmpeg", "node": "Node.js", "claude": "Claude Code"}
BUSY = ("starting", "downloading", "checking", "installing")
FIXES = {name: {"state": "idle", "done": 0, "total": 0, "message": "", "tail": ""} for name in LABEL}
_locks = {name: threading.Lock() for name in LABEL}


class Stop(Exception):
    """A plain-language reason a fix stopped. Nothing was installed (an earlier install is left as it was)."""


# ── this computer ──────────────────────────────────────────────────────────────────────────────────────────────

def _rosetta() -> bool:
    """Is this Python an Intel one running through Rosetta on an Apple silicon Mac? (Then the arm64 builds run
    natively.)"""
    try:
        return subprocess.run(["/usr/sbin/sysctl", "-n", "sysctl.proc_translated"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=5).stdout.strip() == "1"
    except (OSError, subprocess.SubprocessError):
        return False


def platform_key(system: str | None = None, machine: str | None = None, translated: bool | None = None) -> str:
    """darwin-arm64, darwin-x64, windows-x64, or '' where Studio has nothing pinned (Linux, 32-bit Windows)."""
    system = system or sys.platform
    machine = (machine if machine is not None else platform.machine() or "").lower()
    if system == "darwin":
        if machine in ("arm64", "aarch64"):
            return "darwin-arm64"
        if machine in ("x86_64", "amd64", "i386"):
            return "darwin-arm64" if (_rosetta() if translated is None else translated) else "darwin-x64"
    if system in ("win32", "cygwin") and machine in ("amd64", "x86_64", "arm64", "aarch64"):
        return "windows-x64"                                   # Windows 11 on ARM runs x64 programs
    return ""


def mac_version(text: str | None = None) -> tuple:
    text = platform.mac_ver()[0] if text is None else text
    return tuple(int(p) for p in re.findall(r"\d+", text or "")[:2]) or (0,)


def _exe(name: str, windows: bool | None = None) -> str:
    return name + ".exe" if (WINDOWS if windows is None else windows) else name


def tool_dirs(home: Path = HOME, windows: bool | None = None) -> list:
    """Studio's own tool folders that hold an installed program."""
    found = []
    ffmpeg = Path(home) / "tools" / "ffmpeg"
    if all((ffmpeg / _exe(n, windows)).is_file() for n in ("ffmpeg", "ffprobe")):
        found.append(ffmpeg)
    node = Path(home) / "tools" / "node"
    if (node / _exe("node", windows)).is_file():
        found.append(node)
    return found


def use_studio_tools(home: Path = HOME, environ=None, windows: bool | None = None) -> list:
    """Put Studio's own FFmpeg and Node.js first on PATH, for this process and every one it starts. Idempotent: a
    folder already on PATH moves to the front instead of being added twice. Returns the folders added."""
    environ = os.environ if environ is None else environ
    sep = ";" if (WINDOWS if windows is None else windows) else os.pathsep
    ours = [str(d) for d in tool_dirs(home, windows)]
    if not ours:
        return []
    same = lambda p: os.path.normcase(os.path.normpath(p))
    mine = {same(p) for p in ours}
    rest = [p for p in str(environ.get("PATH", "")).split(sep) if p and same(p) not in mine]
    environ["PATH"] = sep.join(ours + rest)
    return ours


# ── checks (setup's rows) ──────────────────────────────────────────────────────────────────────────────────────

def runs(name: str, flag: str = "-version", *, which=None, run=None) -> bool:
    which, run = which or shutil.which, run or subprocess.run       # looked up now: tests replace them
    executable = which(name)
    if not executable:
        return False
    try:
        return run([executable, flag], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


VIDEO_NEEDS = (("-filters", {"subtitles": "burn captions", "ass": "burn captions"}),
               ("-encoders", {"libx264": "make H.264 video", "aac": "make AAC sound", "libmp3lame": "make MP3s"}))


def video_tools_problem(ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe", *, which=None, run=None) -> str:
    """'' when FFmpeg and ffprobe work and FFmpeg can burn captions (libass: the subtitles and ass filters) and make
    H.264, AAC and MP3; else what is wrong, in plain words."""
    which, run = which or shutil.which, run or subprocess.run
    if not runs(ffprobe, which=which, run=run):
        return "FFmpeg's ffprobe isn't installed or won't start."
    executable = which(ffmpeg)
    if not executable:
        return "FFmpeg isn't installed."
    try:
        missing = []
        for flag, wanted in VIDEO_NEEDS:
            # a minimal Homebrew build passes -version but can't burn captions
            result = run([executable, "-hide_banner", flag], capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=10)
            if result.returncode:
                return "FFmpeg is installed but won't start."
            names = {line.split()[1] for line in (result.stdout or "").splitlines() if len(line.split()) > 1}
            for name, why in wanted.items():
                if name not in names and why not in missing:
                    missing.append(why)
    except (OSError, subprocess.SubprocessError):
        return "FFmpeg is installed but won't start."
    return ("This computer's FFmpeg can't " + " or ".join(missing) + ".") if missing else ""


def version_of(executable: str, *, run=None) -> tuple | None:
    try:
        result = (run or subprocess.run)([executable, "--version"], capture_output=True, text=True, encoding="utf-8",
                                         errors="replace", timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"v?(\d+)\.(\d+)\.(\d+)", result.stdout or "")
    return tuple(int(x) for x in match.groups()) if result.returncode == 0 and match else None


def node_status(*, which=None, run=None) -> dict:
    """Node.js for the YouTube downloader (yt-dlp solves YouTube's challenge with it): ready only from Node 22."""
    found = (which or shutil.which)("node")
    if not found:
        return {"ready": False, "version": "", "reason": "Node.js isn't installed."}
    version = version_of(found, run=run)
    if version is None:
        return {"ready": False, "version": "", "reason": "Node.js is installed but won't start."}
    text = ".".join(map(str, version))
    if version[0] < NODE_MINIMUM:
        return {"ready": False, "version": text, "reason": f"Node.js {text} is too old for the YouTube downloader "
                f"(it needs {NODE_MINIMUM} or newer)."}
    return {"ready": True, "version": text, "reason": ""}


def can_fix(name: str, key: str | None = None) -> bool:
    """Is there a one-click fix for this tool on this computer?"""
    key = platform_key() if key is None else key
    if name == "ffmpeg":
        return key in FFMPEG
    if name == "node":
        return any(key in release["builds"] for release in NODE.values())
    if name == "claude":
        return sys.platform == "darwin" or WINDOWS or sys.platform.startswith("linux")
    return False


def download_mb(name: str, key: str | None = None) -> int:
    """How much the fix downloads on this computer, in MB."""
    key = platform_key() if key is None else key
    if name == "ffmpeg":
        return sum(i["size"] for i in FFMPEG.get(key, [])) >> 20
    version = node_release(key) if name == "node" else ""
    return NODE[version]["builds"][key][1] >> 20 if version else 0


def fix_hint(name: str) -> str:
    """What setup tells the creator to do about a tool that isn't ready."""
    if can_fix(name):
        size = f"a {download_mb(name)} MB download, " if download_mb(name) else ""
        return {"ffmpeg": f"Press Fix it next to Video tools: Studio puts a full FFmpeg in its own folder ({size}no "
                          "password needed).",
                "node": f"Press Fix it next to YouTube helper: Studio puts Node.js in its own folder ({size}no password "
                        "needed). Any other Node.js on this computer stays as it is."}[name]
    return {"ffmpeg": "Install FFmpeg with subtitles (libass), H.264, AAC and MP3 support, then reopen the launcher.",
            "node": f"Install Node.js {NODE_MINIMUM} or newer from https://nodejs.org, then reopen the launcher."}[name]


# ── running one fix at a time, in the background ───────────────────────────────────────────────────────────────

def status(name: str) -> dict:
    """The fix's state for setup's progress line: idle | starting | downloading | checking | installing | done |
    failed, with a plain message."""
    fix = dict(FIXES[name])
    if fix.get("state") == "downloading":
        done, total = fix.get("done", 0) >> 20, fix.get("total", 0) >> 20
        fix["message"] = (f"Downloading {fix.get('what') or LABEL[name]}: {done} of {total} MB…" if total else
                          f"Downloading {fix.get('what') or LABEL[name]}…")
    return fix


def _start(name: str, work, *, background: bool = True) -> dict:
    with _locks[name]:
        if FIXES[name].get("state") in BUSY:
            return status(name)
        # one assignment: a status read never sees half a reset
        FIXES[name] = {"state": "starting", "done": 0, "total": 0, "message": "Starting…", "tail": ""}

    def run():
        fix = FIXES[name]
        try:
            message = work(fix)
            fix.update(state="done", message=message)
        except Stop as stop:
            fix.update(state="failed", message=str(stop))
        except Exception as error:                             # noqa: BLE001 - said on the setup page, never raised
            fix.update(state="failed", tail=f"{type(error).__name__}: {error}"[:500],
                       message=f"Installing {LABEL[name]} stopped ({type(error).__name__}). Press Fix it to try "
                               "again; if it keeps stopping, press Copy diagnostics and paste it in Discord #help.")

    started = status(name)
    if background:
        threading.Thread(target=run, daemon=True, name="cr-fix-" + name).start()
    else:
        run()
    return started


# ── downloading and unpacking ──────────────────────────────────────────────────────────────────────────────────

def _get(url: str):
    import requests
    return requests.get(url, stream=True, timeout=(15, 60), allow_redirects=True,
                        headers={"User-Agent": "ChannelRecipe-Studio"})


def _host(url: str) -> str:
    return urlsplit(url).hostname or url


def _network_words(error: BaseException) -> str:
    text = f"{type(error).__name__}: {error}".lower()
    if "ssl" in text or "certificate" in text:
        return "the secure connection was refused (an antivirus or company network that inspects connections can do this)"
    if "timed out" in text or "timeout" in text:
        return "the connection timed out"
    if any(word in text for word in ("name resolution", "getaddrinfo", "nodename nor servname", "resolve")):
        return "this computer couldn't look up the address"
    return "the connection failed"


def _no_room(error: OSError) -> bool:
    return getattr(error, "errno", None) == 28 or "no space left" in str(error).lower()


def fetch(item: dict, folder: Path, fix: dict, *, get=None, what: str = "") -> Path:
    """Download one pinned file into folder, checking its size and SHA-256 as it arrives; each address in turn.
    A file that isn't the pinned one is deleted and refused."""
    get = get or _get
    urls, size, digest_wanted = item["urls"], int(item["size"]), item["sha256"].lower()
    name = PurePosixPath(urlsplit(urls[0]).path).name
    folder.mkdir(parents=True, exist_ok=True)
    part, target = folder / (name + ".part"), folder / name
    base, problems, mismatch = fix.get("done", 0), [], False
    for url in urls:
        fix["done"] = base
        digest, got = hashlib.sha256(), 0
        try:
            with get(url) as response:
                if response.status_code != 200:
                    problems.append(f"{_host(url)} answered {response.status_code}")
                    continue
                landed = str(getattr(response, "url", "") or url)
                if not landed.startswith("https://"):            # a redirect to plain http: not followed
                    problems.append(f"{_host(url)} sent Studio to an insecure address")
                    continue
                with open(part, "wb") as out:
                    for chunk in response.iter_content(1 << 20):
                        if not chunk:
                            continue
                        got += len(chunk)
                        if got > size:                         # longer than the pinned file: not it
                            break
                        digest.update(chunk)
                        out.write(chunk)
                        fix["done"] = base + got
        except OSError as error:
            part.unlink(missing_ok=True)
            if _no_room(error):
                raise Stop(f"The disk is full, so {what or name} couldn't download. Free up "
                           "about 1 GB (empty the Bin or Recycle Bin, delete old downloads), then press Fix it.") from None
            problems.append(f"{_host(url)}: {_network_words(error)}")
            continue
        except Exception as error:                             # noqa: BLE001 - requests' own errors
            part.unlink(missing_ok=True)
            problems.append(f"{_host(url)}: {_network_words(error)}")
            continue
        if got == size and digest.hexdigest() == digest_wanted:
            part.replace(target)
            return target
        part.unlink(missing_ok=True)
        mismatch = True
        problems.append(f"{_host(url)} sent a different file")
    if mismatch:
        raise Stop(f"The download of {what or name} wasn't the exact file Studio expects (its SHA-256 fingerprint "
                   "didn't match), so Studio refused it and installed nothing. Try again later; if it happens again, "
                   "press Copy diagnostics and paste it in Discord #help.")
    raise Stop(f"Studio couldn't download {what or name} ({'; '.join(problems) or 'no answer'}). Check this "
               "computer's internet connection: a firewall, antivirus or VPN can block downloads, and a phone hotspot "
               "works for a quick try. Then press Fix it again.")


def _executable(path: Path) -> None:
    if not WINDOWS:
        path.chmod(0o755)


def take(archive: Path, members: dict, into: Path) -> None:
    """Copy the named members of a .zip or .tar.gz archive into `into`, under Studio's own names (a member's own path
    is never used to write, so nothing can land outside the folder)."""
    into.mkdir(parents=True, exist_ok=True)
    try:
        if archive.name.endswith(".zip"):
            with zipfile.ZipFile(archive) as bundle:
                listed = set(bundle.namelist())
                for name, member in members.items():
                    if member not in listed:
                        raise KeyError(member)
                    with bundle.open(member) as source, open(into / name, "wb") as out:
                        shutil.copyfileobj(source, out, 1 << 20)
                    _executable(into / name)
        else:
            wanted = {member: name for name, member in members.items()}
            with tarfile.open(archive, "r:gz") as bundle:
                for entry in bundle:
                    name = wanted.pop(entry.name, None)
                    if name is None:
                        continue
                    if not entry.isfile():
                        raise Stop(f"The downloaded archive's {entry.name} isn't a plain file, so Studio didn't use it.")
                    with bundle.extractfile(entry) as source, open(into / name, "wb") as out:
                        shutil.copyfileobj(source, out, 1 << 20)
                    _executable(into / name)
                    if not wanted:
                        break
            if wanted:
                raise KeyError(next(iter(wanted)))
    except KeyError as missing:
        raise Stop(f"The downloaded archive doesn't hold {missing.args[0]}, so Studio didn't use it. Press Copy "
                   "diagnostics and paste it in Discord #help.") from None
    except OSError as error:
        if _no_room(error):
            raise Stop("The disk is full. Free up about 1 GB (empty the Bin or Recycle Bin, delete old downloads), then "
                       "press Fix it.") from None
        raise


def _room(home: Path, need_mb: int) -> None:
    try:
        free = shutil.disk_usage(home).free
    except OSError:
        return
    if free < need_mb << 20:
        raise Stop(f"The disk has {free >> 20} MB free and this needs about {need_mb} MB. Free up some space (empty "
                   "the Bin or Recycle Bin, delete old downloads), then press Fix it.")


SWAP_PATIENCE = 45.0          # seconds: Norton, McAfee and Avast can scan a fresh 100 MB ffmpeg.exe for half a minute


def _stage(home: Path, name: str) -> Path:
    """A fresh staging folder. A stopped fix's staging leftovers go; an earlier install (tools/.<name>-old-*, left
    when a swap and its undo both failed) is put back if nothing replaced it, and is never deleted before a new
    install is in place."""
    tools = Path(home) / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    for left in tools.glob(f".{name}-stage-*"):
        shutil.rmtree(left, ignore_errors=True)
    final = tools / name
    olds = sorted(tools.glob(f".{name}-old-*"))
    if olds and not final.exists():
        try:
            olds[-1].rename(final)
        except OSError:
            pass
    stage = tools / f".{name}-stage-{os.getpid()}-{int(time.time())}"
    stage.mkdir()
    return stage


def _rename(source: Path, target: Path, *, patience: float = SWAP_PATIENCE, sleep=None, waiting=None) -> None:
    """Windows refuses a rename while an antivirus still scans a program just written or run: try again, waiting
    a little longer each time (0.5, 1, 2, 4, then 8 s), for up to `patience` seconds."""
    sleep, waited, wait = sleep or time.sleep, 0.0, 0.5
    while True:
        try:
            source.rename(target)
            return
        except OSError:
            if waited >= patience:
                raise
            if waiting:
                waiting()
            sleep(wait)
            waited += wait
            wait = min(wait * 2, 8.0)


def _swap(stage: Path, final: Path, *, patience: float = SWAP_PATIENCE, sleep=None, fix: dict | None = None) -> None:
    """Move the checked install into place; the one before it goes only once the new one is there."""
    old = final.with_name(f".{final.name}-old-{int(time.time())}")
    note = (lambda: fix.update(message="Your antivirus is still checking the new files; waiting for it (up to a "
                                       "minute)…")) if fix is not None else None
    try:
        if final.exists():
            _rename(final, old, patience=patience, sleep=sleep, waiting=note)
        _rename(stage, final, patience=patience, sleep=sleep, waiting=note)
    except OSError:
        if old.exists() and not final.exists():
            try:
                _rename(old, final, patience=patience, sleep=sleep)   # the working install back where it was
            except OSError:
                pass                                           # kept as .<name>-old-*: _stage puts it back
        raise Stop(f"Studio couldn't replace its {final.name} folder: a program (or an antivirus scan) is still using "
                   "it. Try again in a few minutes, when no video is being made.") from None
    for left in final.parent.glob(f".{final.name}-old-*"):
        shutil.rmtree(left, ignore_errors=True)


# ── FFmpeg ────────────────────────────────────────────────────────────────────────────────────────────────────

VIDEO_RUNNING = ("A video is being made right now, and Studio never switches its {what} in the middle of one. Press "
                 "Fix it again when it's done.")


def fix_ffmpeg(*, home: Path = HOME, key: str | None = None, get=None, check=None, background: bool = True,
               macos: tuple | None = None, environ=None, busy=None) -> dict:
    """Install FFmpeg + ffprobe into tools/ffmpeg and put it first on PATH. check(ffmpeg, ffprobe) -> '' when they
    work (video_tools_problem). busy() -> True while a video is being made: nothing is switched then (the fix tools
    are free, so Claude Code can run them unasked)."""
    def work(fix):
        spec_key = platform_key() if key is None else key
        windows = spec_key.startswith("windows") if spec_key else WINDOWS
        own = Path(home) / "tools" / "ffmpeg"
        paths = (str(own / _exe("ffmpeg", windows)), str(own / _exe("ffprobe", windows)))
        if all(Path(p).is_file() for p in paths) and not (check or video_tools_problem)(*paths):
            use_studio_tools(home, environ, windows)
            return "FFmpeg is already installed in Studio's own folder and works. Nothing to download."
        if busy and busy():
            raise Stop(VIDEO_RUNNING.format(what="FFmpeg"))
        if spec_key not in FFMPEG:
            raise Stop("Studio has no FFmpeg of its own for this computer. " + fix_hint("ffmpeg"))
        if spec_key.startswith("darwin"):
            have = macos or mac_version()
            if have < FFMPEG_MACOS:
                raise Stop(f"Studio's FFmpeg needs macOS {FFMPEG_MACOS[0]} or newer, and this Mac has macOS "
                           f"{'.'.join(map(str, have))}. Update macOS, or install FFmpeg with Homebrew "
                           "(brew install ffmpeg-full), then reopen the launcher.")
        _room(home, NEEDS_MB["ffmpeg"])
        items = FFMPEG[spec_key]
        fix.update(state="downloading", done=0, total=sum(i["size"] for i in items), what=f"FFmpeg {FFMPEG_VERSION}")
        stage = _stage(home, "ffmpeg")
        try:
            archives = stage / "downloads"
            files = stage / "ffmpeg"
            for item in items:
                archive = fetch(item, archives, fix, get=get, what=f"FFmpeg {FFMPEG_VERSION}")
                take(archive, item["take"], files)
                archive.unlink(missing_ok=True)                # an archive is up to 115 MB: not kept
            fix.update(state="checking", message="Checking FFmpeg can burn captions and make H.264, AAC and MP3…")
            problem = (check or video_tools_problem)(str(files / _exe("ffmpeg", windows)),
                                                     str(files / _exe("ffprobe", windows)))
            if problem:
                raise Stop(f"The downloaded FFmpeg didn't pass Studio's check ({problem.rstrip('.')}), so it wasn't "
                           "installed. Press Copy diagnostics and paste it in Discord #help.")
            if busy and busy():
                raise Stop(VIDEO_RUNNING.format(what="FFmpeg"))
            _swap(files, own, fix=fix)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        use_studio_tools(home, environ, windows)
        return f"FFmpeg {FFMPEG_VERSION} is installed in Studio's own folder and works."
    return _start("ffmpeg", work, background=background)


# ── Node.js ───────────────────────────────────────────────────────────────────────────────────────────────────

def node_release(key: str, macos: tuple | None = None) -> str:
    """The Node.js version for this computer: the Active LTS, or Node 22 on a Mac too old for it."""
    for version in (NODE_LATEST, *[v for v in NODE if v != NODE_LATEST]):
        release = NODE[version]
        if key not in release["builds"]:
            continue
        if key.startswith("darwin") and (macos or mac_version()) < release["macos"]:
            continue
        return version
    return ""


def published_sha256(version: str, filename: str, *, get=None) -> str:
    """The SHA-256 nodejs.org lists for filename in that release's SHASUMS256.txt ('' when it lists none)."""
    get = get or _get
    url = f"{NODE_DIST}v{version}/SHASUMS256.txt"
    try:
        with get(url) as response:
            if response.status_code != 200:
                raise Stop(f"Studio couldn't read Node.js's checksum list (nodejs.org answered {response.status_code}). "
                           "Try again in a few minutes.")
            text = b"".join(response.iter_content(1 << 16))[:1 << 20].decode("utf-8", "replace")
    except Stop:
        raise
    except Exception as error:                                 # noqa: BLE001 - requests' own errors
        raise Stop(f"Studio couldn't reach nodejs.org ({_network_words(error)}). Check this computer's internet "
                   "connection (a firewall, antivirus or VPN can block it; a phone hotspot works for a quick try), "
                   "then press Fix it again.") from None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == filename:
            return parts[0].lower()
    return ""


def fix_node(*, home: Path = HOME, key: str | None = None, get=None, check=None, background: bool = True,
             macos: tuple | None = None, environ=None, busy=None) -> dict:
    """Install Node.js (the node program only) into tools/node and put it first on PATH. check(node) -> version
    tuple or None (version_of). busy() as for fix_ffmpeg."""
    def work(fix):
        spec_key = platform_key() if key is None else key
        windows = spec_key.startswith("windows") if spec_key else WINDOWS
        own = Path(home) / "tools" / "node"
        program = _exe("node", windows)
        if (own / program).is_file():
            have = (check or version_of)(str(own / program))
            if have and have[0] >= NODE_MINIMUM:
                use_studio_tools(home, environ, windows)
                return (f"Node.js {'.'.join(map(str, have))} is already installed in Studio's own folder and works. "
                        "Nothing to download.")
        if busy and busy():
            raise Stop(VIDEO_RUNNING.format(what="Node.js"))
        version = node_release(spec_key, macos)
        if not version:
            if spec_key.startswith("darwin"):
                raise Stop("This Mac's macOS is too old for Node.js 22 (it needs macOS 11 or newer). Update macOS, "
                           "then press Fix it again.")
            raise Stop("Studio has no Node.js of its own for this computer. " + fix_hint("node"))
        filename, size, sha = NODE[version]["builds"][spec_key]
        _room(home, NEEDS_MB["node"])
        fix.update(state="downloading", done=0, total=size, what=f"Node.js {version}")
        listed = published_sha256(version, filename, get=get)
        if listed != sha:
            raise Stop("Node.js's own checksum list doesn't match the file Studio expects, so Studio refused it and "
                       "installed nothing. Press Copy diagnostics and paste it in Discord #help.")
        stage = _stage(home, "node")
        try:
            item = {"urls": [f"{NODE_DIST}v{version}/{filename}"], "size": size, "sha256": sha}
            archive = fetch(item, stage / "downloads", fix, get=get, what=f"Node.js {version}")
            top = filename[:-len(".zip")] if filename.endswith(".zip") else filename[:-len(".tar.gz")]
            files = stage / "node"
            take(archive, {program: f"{top}/{program}" if windows else f"{top}/bin/node"}, files)
            archive.unlink(missing_ok=True)
            fix.update(state="checking", message=f"Checking Node.js {version} starts…")
            found = (check or version_of)(str(files / program))
            if not found or found[0] < NODE_MINIMUM:
                raise Stop(f"The downloaded Node.js {version} didn't start on this computer, so it wasn't installed. "
                           "Press Copy diagnostics and paste it in Discord #help.")
            if busy and busy():
                raise Stop(VIDEO_RUNNING.format(what="Node.js"))
            _swap(files, own, fix=fix)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        use_studio_tools(home, environ, windows)
        return f"Node.js {version} is installed in Studio's own folder and works."
    return _start("node", work, background=background)


# ── Claude Code ───────────────────────────────────────────────────────────────────────────────────────────────

def powershell() -> str:
    """Windows PowerShell by its full path (it ships with every Windows 10 and 11)."""
    system = Path(os.environ.get("SystemRoot") or r"C:\Windows")
    built_in = system / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(built_in) if built_in.is_file() else (shutil.which("powershell") or shutil.which("pwsh") or
                                                     "powershell.exe")


def claude_install_command(windows: bool | None = None) -> list:
    """Anthropic's documented installer for this computer. On a Mac (and Linux) the script is downloaded whole to a
    temporary file first and only then run: piped straight into bash, a script cut off mid-download could run half
    way. irm reads the whole script before iex runs it."""
    if WINDOWS if windows is None else windows:
        return [powershell(), "-NoProfile", "-NonInteractive", "-Command",
                f"$ProgressPreference = 'SilentlyContinue'; irm {CLAUDE_INSTALL_PS1} | iex"]
    return ["/bin/bash", "-c", 't=$(mktemp "${TMPDIR:-/tmp}/claude-install.XXXXXX") || exit 1; '
            "trap 'rm -f \"$t\"' EXIT; "
            f'curl -fsSL -o "$t" {CLAUDE_INSTALL_SH} && /bin/bash "$t"']


def run_installer(command: list, *, env: dict, timeout: float, popen=None, kill=None) -> tuple:
    """(exit code, output) of the installer. It runs in its own process group (Mac, Linux) or is a tree's root
    (Windows), and on a timeout all of it is stopped, so a second Install never runs beside a stuck first one."""
    popen = popen or subprocess.Popen
    extra = ({"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)} if WINDOWS else
             {"start_new_session": True})
    child = popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                  encoding="utf-8", errors="replace", env=env, **extra)
    try:
        output, _ = child.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        (kill or _kill_tree)(child)
        try:
            child.communicate(timeout=30)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass
        raise
    return child.returncode, output or ""


def _kill_tree(child) -> None:
    try:
        if WINDOWS:
            taskkill = Path(os.environ.get("SystemRoot") or r"C:\Windows") / "System32" / "taskkill.exe"
            subprocess.run([str(taskkill), "/T", "/F", "/PID", str(child.pid)], capture_output=True, timeout=30)
        else:
            import signal
            os.killpg(child.pid, signal.SIGKILL)               # its own session: the group is the installer's
    except (OSError, subprocess.SubprocessError):
        try:
            child.kill()
        except OSError:
            pass


def installer_problem(output: str) -> str:
    low = (output or "").lower()
    if "supported-countries" in low or "not available in your region" in low or " 403" in low:
        return ("Anthropic's download service refused this computer. Claude Code is available only in Anthropic's "
                "supported countries, and some VPNs and company networks are refused: try without the VPN or on "
                "another network, then press Install Claude Code again.")
    if "checksum verification failed" in low:
        return ("The Claude Code download didn't match Anthropic's checksum, so the installer refused it. Try again in "
                "a few minutes.")
    if "32-bit" in low:
        return "Claude Code needs 64-bit Windows."
    if any(w in low for w in ("could not resolve", "failed to connect", "timed out", "ssl", "unable to connect",
                              "name or service not known", "remote name could not be resolved")):
        return ("This computer couldn't reach claude.ai to download Claude Code. Check the internet connection (a "
                "firewall, antivirus or VPN can block it), then press Install Claude Code again.")
    last = next((line.strip() for line in reversed((output or "").splitlines()) if line.strip()), "")
    return "Claude Code's installer stopped" + (f" ({last[:200]})." if last else ".") + (
        " Press Copy diagnostics and paste it in Discord #help.")


def sign_in_command(executable: str | None = None, *, windows: bool | None = None) -> str:
    """The command the creator types to sign in, by full path (the installer doesn't put claude on PATH)."""
    windows = WINDOWS if windows is None else windows
    launcher = Path.home() / ".local" / "bin" / _exe("claude", windows)
    path = str(launcher) if launcher.is_file() else (executable or "claude")
    if windows:
        return "& '" + path.replace("'", "''") + "' auth login"
    return shlex.quote(path) + " auth login"


SIGNED_IN_NEXT = ("press Open the sign-in window (or run {command} in Terminal on a Mac, PowerShell on Windows), sign "
                  "in in your browser with the Claude account (Pro or Max) you want Studio to use, then press Check my "
                  "Claude Code login.")


def fix_claude(*, find=None, popen=None, kill=None, background: bool = True, windows: bool | None = None) -> dict:
    """Run Anthropic's official Claude Code installer (only when the creator asks for it), then find the program."""
    def work(fix):
        import cr_claude
        locate = find or cr_claude.find_executable
        found = locate()
        if found:
            return ("Claude Code is already installed. Last step, once: " +
                    SIGNED_IN_NEXT.format(command=sign_in_command(found, windows=windows)))
        fix.update(state="installing", message="Running Anthropic's official Claude Code installer (a minute or two; it "
                                               "downloads about 200 MB)…")
        try:
            _, output = run_installer(claude_install_command(windows), env=cr_claude.clean_environment(),
                                      timeout=CLAUDE_SECONDS, popen=popen, kill=kill)
        except subprocess.TimeoutExpired:
            raise Stop("Claude Code's installer didn't finish within 15 minutes. Check the internet connection, then "
                       "press Install Claude Code again.") from None
        except OSError as error:
            raise Stop(f"Studio couldn't start Claude Code's installer ({type(error).__name__}). Install it yourself "
                       "with the official guide (the link below), then press Check my Claude Code login.") from None
        fix["tail"] = output[-1500:]
        found = locate()
        if not found:
            raise Stop(installer_problem(output))
        return "Claude Code is installed. Last step, once: " + SIGNED_IN_NEXT.format(
            command=sign_in_command(found, windows=windows))
    return _start("claude", work, background=background)


def open_sign_in(*, find=None, popen=None, home: Path = HOME, windows: bool | None = None,
                 mac: bool | None = None) -> dict:
    """Open a Terminal (PowerShell on Windows) window that runs Claude Code's sign-in by its full path. The creator
    signs in in their browser; Studio never sees the password or the login."""
    import cr_claude
    windows = WINDOWS if windows is None else windows
    mac = (sys.platform == "darwin") if mac is None else mac
    executable = (find or cr_claude.find_executable)()
    if not executable:
        raise Stop("Install Claude Code first: press Install Claude Code.")
    command = sign_in_command(executable, windows=windows)
    popen = popen or subprocess.Popen
    if windows:
        quoted = "'" + executable.replace("'", "''") + "'"
        popen([powershell(), "-NoExit", "-NoProfile", "-Command",
               "Write-Host 'Sign in to Claude Code for ChannelRecipe Studio: your browser opens.'; & " + quoted +
               " auth login --claudeai"], creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10))
    elif mac:
        script = Path(home) / "tools" / "claude-sign-in.command"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("#!/bin/bash\n"
                          "echo 'Sign in to Claude Code for ChannelRecipe Studio.'\n"
                          "echo 'Your browser opens: sign in with the Claude account (Pro or Max) Studio should use.'\n"
                          "echo\n"
                          f"{shlex.quote(executable)} auth login --claudeai\n"
                          "echo\n"
                          "read -r -p 'Done? Close this window and press Check my Claude Code login in Studio. ' _\n",
                          encoding="utf-8")
        script.chmod(0o755)
        popen(["/usr/bin/open", "-a", "Terminal", str(script)])
    else:
        return {"opened": False, "command": command,
                "message": f"Open a terminal, run {command} and sign in in your browser."}
    return {"opened": True, "command": command,
            "message": "A sign-in window opened. Sign in in your browser with the Claude account (Pro or Max) Studio "
                       "should use, then come back here."}
