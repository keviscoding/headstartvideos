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
# Never written by an update, even if a download unexpectedly contained them.
PROTECTED_DIRS = {"output", ".venv", "presets", "samples", BACKUPS, ".cr-update-staging"}
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
        cfg = json.loads((home / ".cr-connection.json").read_text())
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


def download(base: str, key: str, target: Path, get=http_get, attempts: int = 4) -> None:
    """Download the release, resuming after drops and checking it arrived whole.

    The signed storage link supports byte ranges, so an interrupted transfer
    continues where it stopped. The finished file must match the size and the
    SHA-256 the release was uploaded with (when storage reports them).
    """
    auth = {"Authorization": "Bearer " + key}
    try:
        first = get(base + "/api/studio/download", auth)
        location = None
        if _status(first) in (301, 302, 303, 307, 308):
            # The signed storage link carries its own authorization.
            location = first.headers.get("Location", "")
            if urlsplit(location).scheme != "https":
                raise UpdateStopped("The download link wasn't secure. Nothing was changed.")
        size = expected_sha = None
        target.unlink(missing_ok=True)
        for attempt in range(attempts):
            have = target.stat().st_size if target.exists() else 0
            if location:
                response = get(location, {"Range": "bytes=%d-" % have} if have else {})
            else:
                response, have = (first if attempt == 0 else get(base + "/api/studio/download", auth)), 0
            if have and _status(response) != 206:
                have = 0  # the server ignored the range; start again
            if not have:
                size = int(response.headers.get("Content-Length") or 0) or None
                expected_sha = response.headers.get("x-amz-meta-sha256")
            with open(target, "ab" if have else "wb") as out:
                try:
                    shutil.copyfileobj(response, out, 1024 * 1024)
                except (OSError, http.client.HTTPException):
                    pass  # resume on the next attempt
            if size is None or target.stat().st_size >= size:
                break
        if size is not None and target.stat().st_size != size:
            raise UpdateStopped("The download kept getting interrupted. Nothing was changed; check your "
                                "connection and try again.")
        if expected_sha and _digest(target) != expected_sha.strip().lower():
            raise UpdateStopped("The download didn't match the published release. Nothing was changed; try again.")
    except UpdateStopped:
        raise
    except HTTPError as error:
        raise UpdateStopped("The download didn't start (error %s). Nothing was changed; try again shortly." % error.code) from None
    except (URLError, OSError, http.client.HTTPException) as error:
        if _certificate_problem(error):
            raise UpdateStopped(CERTIFICATE_HELP) from None
        raise UpdateStopped("The download was interrupted. Nothing was changed; check your connection and try again.") from None


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
        files = json.loads((home / "cr-files.json").read_text()).get("files", {})
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
        retired = json.loads((home / "cr-files.json").read_text()).get("retired", {})
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


def update(home: Path = HOME, *, get=http_get, running=studio_running, now=time.time) -> str:
    try:
        local = json.loads((home / "cr-release.json").read_text())["version"]
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
        download(base, key, archive, get)
        files = _stage(archive, staging, remote)
        if running():
            raise UpdateStopped("Studio was opened during the update. Nothing was changed; close it and try again.")
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now()))
        backup = home / BACKUPS / ("%s-%s" % (_build(local), stamp))
        changed, requirements_changed, kept = apply(home, staging, files, backup)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        archive.unlink(missing_ok=True)
    retire(home)
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


def main() -> int:
    try:
        print(update())
        return 0
    except UpdateStopped as stopped:
        print(stopped)
        return 1


if __name__ == "__main__":
    sys.exit(main())
