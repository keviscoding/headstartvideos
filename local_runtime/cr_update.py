"""Update ChannelRecipe Studio in place, keeping projects, accounts and settings.

Close the Studio launcher first (finish any running video), then run with the
studio's own Python from this folder:

    Mac:      .venv/bin/python cr_update.py
    Windows:  .venv\\Scripts\\python cr_update.py

or ask Claude Code in this folder to "update ChannelRecipe Studio".

Only files that ship in the official download are replaced. Your output folder,
saved accounts and keys, learned styles and the Python environment are never
touched. Replaced files are backed up first, and a failure part-way through
restores them. Uses only the standard library so it runs before any install.
"""
from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
import re
import shutil
import socket
import ssl
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

HOME = Path(__file__).resolve().parent
TOP = "ChannelRecipe studio/"
STUDIO_PORT = 7860
BACKUPS = ".cr-update-backups"
KEEP_BACKUPS = 2
# Never written by an update, even if a download unexpectedly contained them. tools: the FFmpeg and Node.js setup's
# Fix it installed (cr_installs), never part of a download.
PROTECTED_DIRS = {"output", ".venv", "presets", "samples", "tools", BACKUPS, ".cr-update-staging"}
PROTECTED_FILES = {".env", ".mcp.json"}
# Shipped files creators are meant to customise. An edited one is kept and the
# new version saved beside it. Everything else (code, release metadata) is
# always replaced, with a backup, so the studio stays consistent.
EDITABLE_DIRS = {"styles"}


class UpdateStopped(Exception):
    """A plain-language reason the update did not run. Nothing was changed."""


def _build(version: str | None) -> int | None:
    match = re.search(r"(\d+)$", str(version or ""))
    return int(match.group(1)) if match else None


def _protected(relative: PurePosixPath) -> bool:
    parts = relative.parts
    return (not parts or parts[0] in PROTECTED_DIRS or relative.name in PROTECTED_FILES
            or relative.name.startswith(".cr-"))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def tls_context() -> ssl.SSLContext:
    """Verify HTTPS with certifi's bundle when present.

    python.org's macOS Python ships without CA certificates until its
    "Install Certificates" step runs; the studio's .venv always has certifi.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


CERTIFICATE_HELP = ("Python on this computer can't verify secure connections. Run the update with the studio's "
                    "own Python (Mac: .venv/bin/python cr_update.py, Windows: .venv\\Scripts\\python cr_update.py), "
                    "or on Mac open your Python folder in Applications and run \"Install Certificates.command\". "
                    "Nothing was changed.")


def _certificate_problem(error: BaseException) -> bool:
    reason = getattr(error, "reason", error)
    return isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(reason)


def http_get(url: str, headers: dict[str, str]):
    """GET without following redirects (the ChannelRecipe key must never follow one)."""
    opener = build_opener(_NoRedirect, HTTPSHandler(context=tls_context()))
    try:
        return opener.open(Request(url, headers=headers), timeout=60)
    except HTTPError as error:
        if error.code in (301, 302, 303, 307, 308):
            return error
        raise


def studio_running(port: int = STUDIO_PORT) -> bool:
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _settings(home: Path) -> tuple[str, str]:
    try:
        cfg = json.loads((home / ".cr-connection.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    key, base = str(cfg.get("key", "")), str(cfg.get("url", "https://channelrecipe.com")).rstrip("/")
    parsed = urlsplit(base)
    if not key.startswith("cr_mcp_"):
        raise UpdateStopped("Connect your ChannelRecipe account in Studio's setup page first, then run the update again.")
    if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1"):
        raise UpdateStopped("Your ChannelRecipe connection must use HTTPS. Reconnect it in Studio's setup page.")
    return base, key


def latest_version(base: str, key: str, get=http_get) -> str:
    try:
        response = get(base + "/api/studio/status", {"Authorization": "Bearer " + key})
        data = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        if error.code in (401, 403):
            raise UpdateStopped("ChannelRecipe didn't accept this studio's connection. Check your plan includes "
                                "Studio, or reconnect in the setup page.") from None
        raise UpdateStopped("ChannelRecipe couldn't answer right now. Try again in a few minutes.") from None
    except (URLError, OSError, ValueError) as error:
        if _certificate_problem(error):
            raise UpdateStopped(CERTIFICATE_HELP) from None
        raise UpdateStopped("Couldn't reach ChannelRecipe. Check your internet connection and try again.") from None
    version = data.get("version")
    if not isinstance(version, str) or _build(version) is None:
        raise UpdateStopped("ChannelRecipe didn't report a Studio version. Try again in a few minutes.")
    return version


def _status(response) -> int:
    return int(getattr(response, "status", None) or getattr(response, "code", None) or 200)


STORAGE_MISSES = 2        # failed connections to the storage link before the same file comes through ChannelRecipe


def download(base: str, key: str, target: Path, get=http_get, attempts: int = 6, path: str = "/api/studio/download",
             sleep=None, resume: bool = False, progress=None) -> None:
    """Download the release, resuming after drops and checking it arrived whole.

    The signed storage link supports byte ranges, so an interrupted transfer
    continues where it stopped. Each retry asks for a fresh link: on a slow
    connection the first one can expire before the release arrives (storage is in
    San Francisco; a creator in Europe saw "the update keeps getting interrupted").
    The finished file must match the size and the SHA-256 the release was
    uploaded with (when storage reports them).

    `attempts` counts tries in a row that brought nothing new, so a connection that
    keeps dropping still finishes while each try gets further. A computer that can't
    open the storage link at all (its internet provider can't look the name up, or
    resets or refuses the connection: a creator's Shorts pack failed six times in a
    second each) gets the same file streamed through ChannelRecipe. resume=True keeps
    a partial file from an earlier run and carries on from it. progress(done, total)
    hears how far a long download got.
    """
    auth = {"Authorization": "Bearer " + key}
    sleep = sleep or time.sleep
    site = base + path + "/file"                 # the same file streamed through ChannelRecipe itself
    try:
        first = get(base + path, auth)
        location = _signed_location(first)
        size = expected_sha = None
        newer = via_site = restarted = False
        tries = stalled = misses = 0
        if not resume:
            target.unlink(missing_ok=True)
        best = target.stat().st_size if target.exists() else 0
        while True:
            have = target.stat().st_size if target.exists() else 0
            if tries:
                sleep(min(20, 2 * max(1, stalled)))
                if location and not via_site:
                    location, newer = _fresh_link(lambda: _signed_location(get(base + path, auth)), location)
            tries += 1
            last = stalled + 1 >= attempts or tries >= attempts * 25
            response = None
            if location and not via_site:
                try:
                    response = get(location, {"Range": "bytes=%d-" % have} if have else {})
                except (HTTPError, URLError, OSError, http.client.HTTPException) as error:
                    if _certificate_problem(error):
                        raise
                    if isinstance(error, HTTPError) and error.code == 416 and have and not restarted:
                        target.unlink(missing_ok=True)    # a kept partial longer than the file: start it again
                        restarted = True
                        continue
                    if not isinstance(error, HTTPError):
                        misses += 1
                    if _host_unreachable(error) or misses >= STORAGE_MISSES:
                        via_site = True              # storage can't be reached from here: through ChannelRecipe
                    else:
                        if tries > 1 and newer and isinstance(error, HTTPError) and error.code in (400, 403):
                            raise UpdateStopped(NEWER_RELEASE) from None
                        if last:
                            raise
                        stalled += 1
                        continue                     # refused (an expired link) or dropped: a fresh link next time
            if via_site:
                try:
                    response = get(site, {**auth, "Range": "bytes=%d-" % have} if have else auth)
                except (HTTPError, URLError, OSError, http.client.HTTPException) as error:
                    if isinstance(error, HTTPError) and error.code == 416 and have and not restarted:
                        target.unlink(missing_ok=True)
                        restarted = True
                        continue
                    if (last or _certificate_problem(error)
                            or isinstance(error, HTTPError) and error.code in (401, 402, 403, 404)):
                        raise
                    stalled += 1
                    continue
            if response is not None:
                if have and size and _status(response) == 206 and _range_total(response) not in (None, size):
                    raise UpdateStopped(NEWER_RELEASE)
            else:
                response, have = (first if tries == 1 else get(base + path, auth)), 0
            if have and _status(response) != 206:
                have = 0  # the server ignored the range; start again
            if have and size is None:                # carrying on from an earlier run's partial file
                size = _range_total(response)
                expected_sha = response.headers.get("x-amz-meta-sha256")
            if not have:
                size = int(response.headers.get("Content-Length") or 0) or None
                expected_sha = response.headers.get("x-amz-meta-sha256")
            with open(target, "ab" if have else "wb") as out:
                _copy(response, out, have, size, progress)
            got = target.stat().st_size
            if size is None or got >= size:
                break
            stalled = 0 if got > best else stalled + 1
            best = max(best, got)
            if stalled >= attempts or tries >= attempts * 25:
                break
        if size is not None and target.stat().st_size != size:
            raise UpdateStopped("The download kept getting interrupted. Nothing was changed; check your "
                                "connection and try again.")
        if expected_sha and _digest(target) != expected_sha.strip().lower():
            target.unlink(missing_ok=True)           # never resumed from: the next try starts clean
            raise UpdateStopped("The download didn't match the published release. Nothing was changed; try again.")
    except UpdateStopped:
        raise
    except HTTPError as error:
        raise UpdateStopped("The download didn't start (error %s). Nothing was changed; try again shortly." % error.code) from None
    except (URLError, OSError, http.client.HTTPException) as error:
        if _certificate_problem(error):
            raise UpdateStopped(CERTIFICATE_HELP) from None
        raise UpdateStopped("The download was interrupted. Nothing was changed; check your connection and try again.") from None


def _copy(response, out, done: int, size: int | None, progress) -> None:
    """Copy one response into the file; a drop keeps what arrived (the next try resumes from it)."""
    step = max(32 << 20, (size or 0) // 10)
    mark = done + step
    try:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                return
            out.write(chunk)
            done += len(chunk)
            if progress and done >= mark:
                mark = done + step
                try:
                    progress(done, size)
                except Exception:                    # noqa: BLE001 - progress is only a courtesy
                    pass
    except (OSError, http.client.HTTPException):
        pass


def _host_unreachable(error) -> bool:
    """This computer can't reach the storage host at all (its internet provider can't look the name up, or refuses
    the connection), as a creator's browser showed: DNS_PROBE_FINISHED_NXDOMAIN for the signed link's host."""
    if isinstance(error, HTTPError):
        return False
    reason = getattr(error, "reason", error)
    return isinstance(reason, (socket.gaierror, ConnectionRefusedError))


NEWER_RELEASE = ("A newer Studio was published while this one downloaded. Nothing was changed; press Update again "
                 "to get the newest.")


def _fresh_link(mint, old):
    """A fresh signed link for the same release file, or the one in hand: the fresh-link request is best effort (a
    network blip or a redeploying server never ends a download whose link still works), and a link to a different
    file (a release published mid-update) is never mixed into this one. (link, newer_published)"""
    try:
        new = mint()
    except (HTTPError, URLError, OSError, http.client.HTTPException) as error:
        if _certificate_problem(error):
            raise
        return old, False
    if not new:
        return old, False
    if urlsplit(new).path != urlsplit(old).path:
        return old, True
    return new, False


def _range_total(response) -> int | None:
    total = (response.headers.get("Content-Range") or "").rpartition("/")[2]
    return int(total) if total.isdigit() else None


def _signed_location(response):
    """The signed storage link a download redirect points to, or None."""
    if _status(response) not in (301, 302, 303, 307, 308):
        return None
    # The signed storage link carries its own authorization.
    location = response.headers.get("Location", "")
    if urlsplit(location).scheme != "https":
        raise UpdateStopped("The download link wasn't secure. Nothing was changed.")
    return location


class _NoRanges(Exception):
    """Storage didn't serve byte ranges; use the whole download instead."""


class _RangeReader(io.RawIOBase):
    """A read-only, seekable view of the remote release, fetched in byte ranges.

    zipfile reads the archive's index from the end, then only the members it is
    asked for, so an update downloads just the files that changed.
    """
    BLOCK = 1024 * 1024

    TRIES = 5

    def __init__(self, url, get, size, refresh=None, sleep=None, headers=None):
        super().__init__()
        self.url, self.get, self.size, self.refresh, self.headers = url, get, size, refresh, dict(headers or {})
        self.sleep = sleep or (lambda seconds: time.sleep(seconds))
        self.newer = False
        self.pos, self.start, self.block, self.fetched = 0, -1, b"", 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence]
        self.pos = max(0, base + offset)
        return self.pos

    def _fill(self, wanted):
        """One block of the release. A dropped or refused request (the signed link expired on a slow connection) is
        tried again with a fresh link, so a slow update finishes instead of starting the whole download over."""
        end = min(self.size, self.pos + max(wanted, self.BLOCK)) - 1
        for attempt in range(self.TRIES):
            try:
                response = self.get(self.url, {**self.headers, "Range": "bytes=%d-%d" % (self.pos, end)})
                if _status(response) != 206:
                    raise _NoRanges()                  # storage ignores ranges: the whole download instead
                if _range_total(response) not in (None, self.size):
                    raise UpdateStopped(NEWER_RELEASE)  # a different file: never mixed into this release
                data = response.read()
                if len(data) == end - self.pos + 1:
                    self.start, self.block = self.pos, data
                    self.fetched += len(data)
                    return
            except (HTTPError, URLError, OSError, http.client.HTTPException) as error:
                if _certificate_problem(error):
                    raise
            if attempt + 1 < self.TRIES:
                self.sleep(min(20, 2 ** attempt))
                if self.refresh:
                    self.url, newer = _fresh_link(self.refresh, self.url)
                    self.newer = self.newer or newer
        if self.newer:
            raise UpdateStopped(NEWER_RELEASE)
        raise UpdateStopped("The download kept getting interrupted. Nothing was changed; check your connection and "
                            "try again.")

    def read(self, size=-1):
        size = self.size - self.pos if size is None or size < 0 else min(size, self.size - self.pos)
        out = bytearray()
        while size > 0:
            if not self.start <= self.pos < self.start + len(self.block):
                self._fill(size)
            piece = self.block[self.pos - self.start:self.pos - self.start + size]
            out += piece
            self.pos += len(piece)
            size -= len(piece)
        return bytes(out)

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)


def _member_path(name: str) -> PurePosixPath | None:
    """A release member's path inside the studio, or None for folders; unsafe paths stop the update."""
    if not name.startswith(TOP):
        raise UpdateStopped("The download had an unexpected layout. Nothing was changed.")
    relative = PurePosixPath(name[len(TOP):])
    if not relative.parts:
        return None
    if relative.is_absolute() or ".." in relative.parts:
        raise UpdateStopped("The download had an unsafe file path. Nothing was changed.")
    return relative


def stage_changes(home: Path, base: str, key: str, staging: Path, expected: str, *, get=http_get,
                  progress=None) -> tuple[list[tuple[PurePosixPath, bool]], int]:
    """Stage only the files that differ from this install, reading the release in byte ranges.

    Returns the staged files and how many bytes were downloaded. Raises _NoRanges
    when storage can't serve ranges, so the caller can use the whole download.
    """
    auth = {"Authorization": "Bearer " + key}
    first = get(base + "/api/studio/download", auth)
    location = _signed_location(first)
    if not location:
        raise _NoRanges()
    via, probe = {}, None
    for miss in range(STORAGE_MISSES):
        try:
            probe = get(location, {"Range": "bytes=0-0"})
            break
        except (URLError, OSError) as error:
            if isinstance(error, HTTPError) or _certificate_problem(error):
                raise
            if _host_unreachable(error) or miss + 1 == STORAGE_MISSES:
                break
    if probe is None:
        location, via = base + "/api/studio/download/file", auth      # storage can't be reached: through ChannelRecipe
        probe = get(location, {**via, "Range": "bytes=0-0"})
    total = int((probe.headers.get("Content-Range") or "").rpartition("/")[2] or 0) if _status(probe) == 206 else 0
    if not total:
        raise _NoRanges()
    reader = _RangeReader(location, get, total, headers=via,
                          refresh=None if via else lambda: _signed_location(get(base + "/api/studio/download", auth)))
    with zipfile.ZipFile(reader) as bundle:
        try:
            release = json.loads(bundle.read(TOP + "cr-release.json"))
            listing = json.loads(bundle.read(TOP + "cr-files.json")).get("files", {})
        except (KeyError, ValueError):
            raise UpdateStopped("The download isn't a ChannelRecipe Studio release. Nothing was changed.") from None
        if release.get("version") != expected:
            raise UpdateStopped("The download didn't match the expected version. Nothing was changed; try again.")
        wanted = []
        for info in bundle.infolist():
            relative = _member_path(info.filename)
            if relative is None or info.is_dir() or _protected(relative):
                continue
            target = home.joinpath(*relative.parts)
            digest = listing.get(relative.as_posix())
            if digest and target.is_file() and not target.is_symlink() and _digest(target) == digest:
                continue
            wanted.append((info, relative))
        need = sum(info.compress_size for info, _ in wanted) or 1
        done, files = 0, []
        for info, relative in wanted:
            data = bundle.read(info)
            digest = listing.get(relative.as_posix())
            if digest and hashlib.sha256(data).hexdigest() != digest:
                raise UpdateStopped("The download was damaged. Nothing was changed; try again.")
            destination = staging.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            files.append((relative, bool(info.external_attr >> 16 & 0o111)))
            done += info.compress_size
            if progress:
                progress(min(done, need), need)
    return files, reader.fetched


def _stage(archive: Path, staging: Path, expected: str) -> list[tuple[PurePosixPath, bool]]:
    """Extract and validate the whole download before touching the studio."""
    try:
        bundle = zipfile.ZipFile(archive)
    except zipfile.BadZipFile:
        raise UpdateStopped("The download was incomplete. Nothing was changed; try again.") from None
    with bundle:
        if bundle.testzip() is not None:
            raise UpdateStopped("The download was damaged. Nothing was changed; try again.")
        try:
            release = json.loads(bundle.read(TOP + "cr-release.json"))
        except (KeyError, ValueError):
            raise UpdateStopped("The download isn't a ChannelRecipe Studio release. Nothing was changed.") from None
        if release.get("version") != expected:
            raise UpdateStopped("The download didn't match the expected version. Nothing was changed; try again.")
        files = []
        for info in bundle.infolist():
            name = info.filename
            if not name.startswith(TOP):
                raise UpdateStopped("The download had an unexpected layout. Nothing was changed.")
            relative = PurePosixPath(name[len(TOP):])
            if info.is_dir() or not relative.parts:
                continue
            if relative.is_absolute() or ".." in relative.parts:
                raise UpdateStopped("The download had an unsafe file path. Nothing was changed.")
            if _protected(relative):
                continue
            destination = staging.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(bundle.read(info))
            files.append((relative, bool(info.external_attr >> 16 & 0o111)))
    return files


def _same(path: Path, other: Path) -> bool:
    return path.is_file() and path.stat().st_size == other.stat().st_size and path.read_bytes() == other.read_bytes()


def _installed_hashes(home: Path) -> dict[str, str]:
    """What this install originally shipped (from release 44 on), to spot creator edits."""
    try:
        files = json.loads((home / "cr-files.json").read_text(encoding="utf-8")).get("files", {})
        return files if isinstance(files, dict) else {}
    except (OSError, ValueError):
        return {}


def _digest(path: Path) -> str:
    # Python 3.10 compatible (hashlib.file_digest needs 3.11).
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def apply(home: Path, staging: Path, files, backup: Path) -> tuple[int, bool, list[str]]:
    """Move staged files into the studio, backing up and rolling back on failure.

    A shipped file the creator has edited since installing is kept as it is;
    the new version is saved beside it as <name>.new.
    """
    shipped = _installed_hashes(home)
    replaced: list[tuple[Path, Path | None]] = []
    kept: list[str] = []
    requirements_changed = False
    try:
        for relative, executable in files:
            source = staging.joinpath(*relative.parts)
            target = home.joinpath(*relative.parts)
            if _same(target, source):
                continue
            original = shipped.get(relative.as_posix())
            if (original and relative.parts[0] in EDITABLE_DIRS and target.is_file()
                    and _digest(target) != original):
                beside = target.with_name(target.name + ".new")
                os.replace(source, beside)
                kept.append(relative.as_posix())
                continue
            saved = None
            if target.is_file():
                saved = backup.joinpath(*relative.parts)
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
            replaced.append((target, saved))
            if executable and os.name != "nt":
                target.chmod(0o755)
            if relative.as_posix() == "requirements.txt":
                requirements_changed = True
    except OSError:
        for target, saved in reversed(replaced):
            try:
                if saved is not None:
                    shutil.copy2(saved, target)
                else:
                    target.unlink(missing_ok=True)
            except OSError:
                pass
        raise UpdateStopped("A file couldn't be replaced (is Studio still open?). Your previous version was "
                            "restored. Close Studio completely and try again.") from None
    return len(replaced), requirements_changed, kept


def retire(home: Path = HOME) -> list[str]:
    """Remove files an earlier release shipped that this release no longer uses.

    cr-files.json lists each retired path with the hashes it shipped under. A
    file is removed only while it still has one of those hashes, so anything a
    creator edited or added is never touched. Safe to run on every start.
    """
    try:
        retired = json.loads((home / "cr-files.json").read_text(encoding="utf-8")).get("retired", {})
    except (OSError, ValueError):
        return []
    removed = []
    for name, hashes in (retired.items() if isinstance(retired, dict) else ()):
        relative = PurePosixPath(str(name))
        if (relative.is_absolute() or ".." in relative.parts or _protected(relative)
                or not isinstance(hashes, list)):
            continue
        target = home.joinpath(*relative.parts)
        try:
            if target.is_symlink() or not target.is_file() or _digest(target) not in hashes:
                continue
            target.unlink()
            removed.append(relative.as_posix())
            parent = target.parent
            while parent != home and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
        except OSError:
            continue
    return removed


def _fetch(home, base, key, staging, archive, remote, *, get=http_get, progress=None):
    """Stage the release: only the changed files when possible, else the whole download."""
    try:
        files, _ = stage_changes(home, base, key, staging, remote, get=get, progress=progress)
        return files
    except UpdateStopped:
        raise
    except (_NoRanges, zipfile.BadZipFile, URLError, OSError, http.client.HTTPException, ValueError):
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
    download(base, key, archive, get)
    return _stage(archive, staging, remote)


def _installed(home: Path) -> str:
    try:
        return json.loads((home / "cr-release.json").read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        raise UpdateStopped("This folder isn't a ChannelRecipe Studio install. Run the update from your Studio folder.") from None


STAGED = ".cr-update-staging"


def prepare(home: Path = HOME, *, get=http_get, progress=None) -> dict:
    """Download and stage the latest release while Studio keeps running. Nothing is replaced yet."""
    local = _installed(home)
    base, key = _settings(home)
    remote = latest_version(base, key, get)
    if _build(remote) is None or _build(remote) <= (_build(local) or 0):
        return {"state": "current", "version": local}
    staging = home / STAGED
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    archive = home / ".cr-update-download.zip"
    try:
        files = _fetch(home, base, key, staging, archive, remote, get=get, progress=progress)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        archive.unlink(missing_ok=True)
    (staging / ".cr-staged.json").write_text(json.dumps({
        "version": remote, "from": local, "files": [[p.as_posix(), x] for p, x in files]}), encoding="utf-8")
    return {"state": "staged", "version": remote, "files": len(files)}


def apply_staged(home: Path = HOME, *, keep=frozenset(), now=time.time) -> dict:
    """Install a staged release. Files named in keep are left as they are (in use by the launcher)."""
    staging = home / STAGED
    try:
        saved = json.loads((staging / ".cr-staged.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise UpdateStopped("No update is ready to install. Download it again.") from None
    local = _installed(home)
    if saved.get("from") != local:
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateStopped("Studio changed since this update was downloaded. Download it again.")
    files = [(PurePosixPath(path), bool(executable)) for path, executable in saved.get("files", [])
             if path not in keep]
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now()))
    backup = home / BACKUPS / ("%s-%s" % (_build(local), stamp))
    try:
        changed, requirements_changed, kept = apply(home, staging, files, backup)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    retire(home)
    configure_claude_code(home)
    if requirements_changed:
        # The launcher installs libraries once; make it check them again.
        (home / ".venv" / ".cr-installed").unlink(missing_ok=True)
    backups = sorted((p for p in (home / BACKUPS).glob("*") if p.is_dir()), key=lambda p: p.stat().st_mtime)
    for old in backups[:-KEEP_BACKUPS]:
        shutil.rmtree(old, ignore_errors=True)
    return {"version": saved["version"], "changed": changed, "requirements_changed": requirements_changed,
            "kept": kept}


def update(home: Path = HOME, *, get=http_get, running=studio_running, now=time.time) -> str:
    try:
        local = json.loads((home / "cr-release.json").read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        raise UpdateStopped("This folder isn't a ChannelRecipe Studio install. Run the update from your Studio folder.") from None
    if running():
        raise UpdateStopped("Studio is still open. Let any running video finish, close the Studio launcher window, "
                            "then run the update again.")
    base, key = _settings(home)
    remote = latest_version(base, key, get)
    if _build(remote) is None or _build(remote) <= (_build(local) or 0):
        return "You already have the latest ChannelRecipe Studio (version %s)." % _build(local)
    staging = home / ".cr-update-staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    archive = home / ".cr-update-download.zip"
    try:
        files = _fetch(home, base, key, staging, archive, remote, get=get)
        if running():
            raise UpdateStopped("Studio was opened during the update. Nothing was changed; close it and try again.")
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now()))
        backup = home / BACKUPS / ("%s-%s" % (_build(local), stamp))
        changed, requirements_changed, kept = apply(home, staging, files, backup)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        archive.unlink(missing_ok=True)
    retire(home)
    configure_claude_code(home)
    if requirements_changed:
        # The launcher installs libraries once; make it check them again.
        (home / ".venv" / ".cr-installed").unlink(missing_ok=True)
    backups = sorted((p for p in (home / BACKUPS).glob("*") if p.is_dir()), key=lambda p: p.stat().st_mtime)
    for old in backups[:-KEEP_BACKUPS]:
        shutil.rmtree(old, ignore_errors=True)
    message = ("Updated ChannelRecipe Studio from version %s to %s (%d files). Your projects, accounts and styles "
               "are unchanged. Open the Studio launcher again to continue." % (_build(local), _build(remote), changed))
    if kept:
        message += (" You had edited %d file(s), so they were kept; the new versions are saved beside them with "
                    ".new at the end: %s" % (len(kept), ", ".join(kept[:10])))
    return message


def configure_claude_code(home: Path = HOME) -> None:
    """Claude Code in this folder: the new release's studio tools and their permissions (cr_claude_code.configure).
    Loaded from the folder, so an update run by an older updater still uses the new code. Best effort."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("cr_claude_code_update", home / "cr_claude_code.py")
        if spec is None or spec.loader is None:
            return
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.configure(home)
    except Exception:                                       # noqa: BLE001 - Studio configures it again on start
        pass


def main() -> int:
    try:
        print(update())
        return 0
    except UpdateStopped as stopped:
        print(stopped)
        return 1


if __name__ == "__main__":
    sys.exit(main())
