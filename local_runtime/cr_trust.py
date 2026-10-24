"""Studio's Python trusts this computer's own certificates, the way the browser does.

An antivirus that scans secure connections (the Avast, AVG, Kaspersky, Bitdefender and ESET web shields) or a company
firewall that inspects HTTPS re-signs every site with its own root certificate, and adds that root to the computer's
certificate store. The browser trusts that store, so websites keep working. Studio's Python trusted only certifi's
list, so every secure connection Studio made failed its certificate check (CERTIFICATE_VERIFY_FAILED) while the
creator's browser was fine, and the only fix was changing the antivirus.

install() makes Python's ssl module check certificates with the operating system (truststore: the Windows certificate
store, the macOS keychain, Linux's system bundle), with certifi's list still trusted on top, once per process: Studio,
its video workers, the style studies, the Shorts pipelines (cr_relay.install runs in each), the speech-model download
and Studio's own yt-dlp (cr_ytdlp, a process of its own). requests, urllib3, httpx and urllib build every secure
connection from ssl.SSLContext, so all of Studio's downloads and uploads go through it unchanged. Without truststore
(libraries not reinstalled yet) everything stays as it was.
"""
from __future__ import annotations

import importlib.util
import os
import ssl
import sys
import threading
from pathlib import Path

HOME = Path(__file__).resolve().parent
YTDLP = HOME / "cr_ytdlp.py"
_lock = threading.Lock()
_state: dict = {}


def available() -> bool:
    """Is truststore installed with this Python (found, not imported)?"""
    try:
        return importlib.util.find_spec("truststore") is not None
    except (ImportError, ValueError):
        return False


def install() -> bool:
    """From now on this process checks certificates with this computer's store (done once). True when it does."""
    with _lock:
        if "on" not in _state:
            try:
                import truststore
                truststore.inject_into_ssl()
                _state["on"] = True
            except Exception:                                  # noqa: BLE001 - no truststore: certifi, as before
                _state["on"] = False
        return _state["on"]


def active() -> bool:
    return bool(_state.get("on"))


def _certifi() -> str:
    try:
        import certifi
        return certifi.where()
    except Exception:                                          # noqa: BLE001
        return ""


def context() -> ssl.SSLContext:
    """A client context that checks certificates the way Studio's own connections do: this computer's store and
    certifi's list (certifi's alone without truststore), whether or not this process has installed it."""
    cafile = _certifi()
    if _state.get("on") is not False:
        try:
            import truststore
            trusted = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            if cafile:
                trusted.load_verify_locations(cafile=cafile)
            return trusted
        except Exception:                                      # noqa: BLE001
            pass
    return ssl.create_default_context(cafile=cafile or None)


def describe() -> str:
    """Which certificates this process checks against, for diagnostics."""
    return "this computer's and certifi's" if active() else "certifi's only"


def _same(path, *others) -> bool:
    mine = os.path.normcase(os.path.abspath(str(path)))
    return any(mine == os.path.normcase(os.path.abspath(str(other))) for other in others)


def ytdlp_command(cmd: list) -> list:
    """Studio's own yt-dlp run through cr_ytdlp, so it trusts this computer's certificates too (it runs as a process
    of its own). A yt-dlp the creator chose elsewhere (YTDLP_BIN, a standalone download) is left as it is."""
    cmd = list(cmd or [])
    if not cmd or not YTDLP.is_file() or not available():
        return cmd
    python = sys.executable
    if cmd[:3] == [python, "-m", "yt_dlp"]:
        rest = cmd[3:]
    elif _same(cmd[0], *(Path(python).with_name(name) for name in ("yt-dlp.exe", "yt-dlp"))):
        rest = cmd[1:]
    else:
        return cmd
    return [python, str(YTDLP), *rest]
