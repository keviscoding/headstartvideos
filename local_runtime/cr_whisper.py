"""The speech-timing model (faster-whisper 'base', CPU int8), ready during setup instead of in the middle of a video.

The engine times every narration with Whisper and loaded the model the first time a video needed it: a first video
downloaded about 140 MB from huggingface.co halfway through, and a customer's worker died while the model loaded,
mid-video, with nothing said in setup. Now Studio downloads the model when it starts (with a progress line in setup)
and loads it once in a separate process, so a model that can't load on this computer shows up in setup with a Fix
it button, not as a stopped video. A crash while loading ends that small process, never Studio.

Once the model is on this computer the engine loads it from its folder (cr_media wraps engine._get_whisper), so a
video never needs huggingface.co, even on a network that cuts it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

HOME = Path(__file__).resolve().parent
MARKER = HOME / ".cr-speech-model.json"          # the last load check: which files, and whether they loaded
REPOS = {"base": "Systran/faster-whisper-base", "base.en": "Systran/faster-whisper-base.en",
         "tiny": "Systran/faster-whisper-tiny", "small": "Systran/faster-whisper-small"}
FILES = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]   # as faster-whisper
NEEDED = ("model.bin", "config.json", "tokenizer.json")
SIZES = {"base": 147_900_000}                   # bytes the 'base' download writes, for the progress line
LOAD_SECONDS, DOWNLOAD_SECONDS = 300, 1800

FIX = {"state": "idle", "done": 0, "total": 0, "tail": ""}   # idle | downloading | checking | done | failed
_lock = threading.Lock()

DOWNLOAD = r"""
import sys
try:                                    # this computer's certificates, as the browser trusts them (cr_trust)
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass
from huggingface_hub import snapshot_download
path = snapshot_download(sys.argv[1], allow_patterns=sys.argv[3].split("|"), force_download=sys.argv[2] == "1")
print("CR-MODEL-PATH " + path, flush=True)
"""
LOAD = r"""
import sys
from faster_whisper import WhisperModel
WhisperModel(sys.argv[1], device="cpu", compute_type="int8")
print("CR-MODEL-LOADED", flush=True)
"""


def point_engine(engine) -> None:
    """Before the engine first loads its model: point it at the folder setup downloaded. Loading by name asks
    huggingface.co for the newest copy first, which fails mid-video on a network that cuts it (older Hugging Face
    libraries don't fall back to the saved copy when the secure connection is cut)."""
    if getattr(engine, "_WHISPER", None) is not None:
        return
    name = str(getattr(engine, "WHISPER_MODEL", "") or "base")
    if os.path.isdir(name):
        return
    folder = local_path(name)
    if folder:
        engine.WHISPER_MODEL = folder


def model_name(home: Path = HOME) -> str:
    """The model the engine loads: WHISPER_MODEL (environment, then .env), else 'base'."""
    name = os.environ.get("WHISPER_MODEL", "").strip()
    if not name:
        try:
            from dotenv import dotenv_values
            name = str(dotenv_values(home / ".env").get("WHISPER_MODEL") or "").strip()
        except Exception:                                      # noqa: BLE001
            name = ""
    return name or "base"


def repo_for(name: str) -> str:
    return name if "/" in name else REPOS.get(name, "Systran/faster-whisper-" + name)


def _complete(folder) -> bool:
    return bool(folder) and all((Path(folder) / f).is_file() for f in NEEDED)


def local_path(name: str | None = None) -> str:
    """The folder of this model on this computer ("" when it isn't downloaded). Never touches the network."""
    name = name or model_name()
    if os.path.isdir(name):
        return name if _complete(name) else ""
    try:
        from huggingface_hub import snapshot_download
        folder = snapshot_download(repo_for(name), allow_patterns=FILES, local_files_only=True)
    except Exception:                                          # noqa: BLE001 - not downloaded, or no hub library
        return ""
    return folder if _complete(folder) else ""


def _identity(folder: str) -> dict:
    model = Path(folder) / "model.bin"
    stat = model.stat()
    return {"path": str(folder), "size": stat.st_size, "mtime": int(stat.st_mtime)}


def _marker() -> dict:
    try:
        return json.loads(MARKER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _child_env() -> dict:
    # as the video worker runs (cr_video_cancel): two OpenMP copies abort a process on Windows instead of running
    return {**os.environ, "KMP_DUPLICATE_LIB_OK": "TRUE", "PYTHONUTF8": "1", "HF_HUB_DISABLE_PROGRESS_BARS": "1"}


def _crash(output: str, code) -> str:
    try:
        import cr_video_cancel
        why = cr_video_cancel.crash_reason(output, code)
    except Exception:                                          # noqa: BLE001
        why = f"it ended unexpectedly (exit code {code})"
    last = next((line.strip() for line in reversed(output.splitlines()) if line.strip()), "")
    return why + (f". Last message: {last[:200]}" if last and code not in (0, None) else "")


def load_check(folder: str, *, run=None) -> dict:
    """Load the model the way the engine does (CPU, int8), in its own process, and remember the answer for these
    exact files."""
    run = run or subprocess.run
    try:
        done = run([sys.executable, "-I", "-c", LOAD, folder], capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=LOAD_SECONDS, env=_child_env(), cwd=str(HOME))
        output = (done.stdout or "") + (done.stderr or "")
        ok = done.returncode == 0 and "CR-MODEL-LOADED" in output
        error = "" if ok else _crash(output, done.returncode)
    except subprocess.TimeoutExpired:
        ok, error = False, f"it didn't finish loading within {LOAD_SECONDS // 60} minutes"
    except OSError as problem:
        ok, error = False, f"Studio's Python couldn't start ({type(problem).__name__})"
    record = {**_identity(folder), "ok": ok, "error": error, "checked_at": time.time()}
    try:
        tmp = MARKER.with_suffix(".tmp")
        tmp.write_text(json.dumps(record), encoding="utf-8")
        tmp.replace(MARKER)
    except OSError:
        pass
    return record


def _blob_bytes(name: str, since: float = 0) -> int:
    """How much of this download is on disk (finished and partial files written since it started), for the progress
    line."""
    try:
        from huggingface_hub import constants
        folder = Path(constants.HF_HUB_CACHE) / ("models--" + repo_for(name).replace("/", "--")) / "blobs"
        return sum(s.st_size for s in (p.stat() for p in folder.iterdir() if p.is_file()) if s.st_mtime >= since)
    except Exception:                                          # noqa: BLE001
        return 0


def _download(name: str, force: bool, *, popen=None) -> tuple[str, str]:
    """(folder, "") once downloaded, else ("", why)."""
    popen = popen or subprocess.Popen
    since = time.time() - 2
    FIX.update(total=SIZES.get(name, 0), done=0)
    try:
        child = popen([sys.executable, "-I", "-c", DOWNLOAD, repo_for(name), "1" if force else "0", "|".join(FILES)],
                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                      # plain downloads (not Hugging Face's xet transfer) write a growing file setup can measure
                      errors="replace", env={**_child_env(), "HF_HUB_DISABLE_XET": "1"}, cwd=str(HOME))
    except OSError as problem:
        return "", f"Studio's Python couldn't start ({type(problem).__name__})"
    lines: list = []
    reader = threading.Thread(target=lambda: lines.extend(child.stdout), daemon=True)
    reader.start()
    started = time.monotonic()
    while child.poll() is None:
        if time.monotonic() - started > DOWNLOAD_SECONDS:
            child.kill()
            return "", f"the download didn't finish within {DOWNLOAD_SECONDS // 60} minutes"
        FIX["done"] = max(FIX["done"], min(_blob_bytes(name, since), FIX["total"] or 1 << 40))
        time.sleep(1)
    reader.join(5)
    output = "".join(lines)
    folder = next((line.split(" ", 1)[1].strip() for line in output.splitlines()
                   if line.startswith("CR-MODEL-PATH ")), "")
    if child.returncode == 0 and _complete(folder):
        FIX["done"] = FIX["total"] or FIX["done"]
        return folder, ""
    return "", download_problem(output)


def download_problem(output: str) -> str:
    low = output.lower()
    if any(sign in low for sign in ("connecterror", "connectionerror", "ssl", "timed out", "timeout",
                                    "name resolution", "getaddrinfo", "nodename nor servname", "max retries")):
        return ("this computer can't reach huggingface.co, where the model is downloaded from. Allow huggingface.co "
                "in your firewall, antivirus or VPN, or use another network (a phone hotspot works) just for this "
                "download, then press Fix it")
    if "no space left" in low or "errno 28" in low:
        return "the disk is full. Free up about 200 MB, then press Fix it"
    if "no module named" in low:
        return "Studio's speech libraries aren't installed. Close Studio and open the launcher again, then press Fix it"
    last = next((line.strip() for line in reversed(output.splitlines()) if line.strip()), "")
    return "the download stopped" + (f" ({last[:200]})" if last else "")


def ensure(*, force: bool = False, background: bool = True) -> dict:
    """Download the model when it's missing (or again with force) and load it once to be sure. In the background:
    setup shows the progress. One at a time."""
    with _lock:
        if FIX["state"] in ("downloading", "checking"):
            return dict(FIX)
        FIX.update(state="checking", done=0, total=0, tail="")

    def work():
        name = model_name()
        try:
            folder = "" if force else local_path(name)
            if not folder:
                FIX["state"] = "downloading"
                folder, why = _download(name, force)
                if not folder:
                    FIX.update(state="failed", tail=f"The speech-timing model couldn't download: {why}.")
                    return
            FIX["state"] = "checking"
            record = load_check(folder)
            FIX.update(state="done" if record["ok"] else "failed",
                       tail="" if record["ok"] else load_problem(record["error"]))
        except Exception as error:                             # noqa: BLE001 - shown in setup, never raised
            FIX.update(state="failed", tail=f"The speech-timing model check stopped ({type(error).__name__}).")

    started = dict(FIX)
    if background:
        threading.Thread(target=work, daemon=True, name="cr-speech-model").start()
    else:
        work()
    return started


def load_problem(error: str) -> str:
    return (f"The speech-timing model is on this computer but couldn't load: {error}. Press Fix it to download it "
            "again; if it still fails, press Copy diagnostics and paste it in Discord #help.")


def start() -> None:
    """At Studio start: fetch the model if it's missing, or load-check files that haven't been checked yet."""
    folder = local_path()
    if folder:
        record = _marker()
        if record.get("ok") and record.get("path") == folder and _same_files(record, folder):
            return
    ensure()


def _same_files(record: dict, folder: str) -> bool:
    try:
        now = _identity(folder)
    except OSError:
        return False
    return all(record.get(k) == now[k] for k in ("path", "size", "mtime"))


def status() -> dict:
    """{ready, state, reason, done, total} for setup. Ready means downloaded and loaded once on this computer."""
    fix = dict(FIX)
    if fix["state"] == "downloading":
        done, total = fix.get("done", 0) >> 20, fix.get("total", 0) >> 20
        progress = f"{done} of {total} MB" if total else (f"{done} MB so far" if done else "starting")
        return {"ready": False, "state": "downloading", "done": fix.get("done", 0), "total": fix.get("total", 0),
                "reason": f"Downloading the speech-timing model: {progress}. Setup finishes it by itself."}
    if fix["state"] == "checking":
        return {"ready": False, "state": "checking", "reason": "Checking the speech-timing model loads (a few seconds)…"}
    folder = local_path()
    if not folder:
        why = fix["tail"] if fix["state"] == "failed" else (
            "The speech-timing model isn't on this computer yet (about 140 MB). Press Fix it to download it.")
        return {"ready": False, "state": "missing", "reason": why}
    record = _marker()
    if record.get("path") == folder and _same_files(record, folder):
        if record.get("ok"):
            return {"ready": True, "state": "ready", "reason": "", "path": folder}
        return {"ready": False, "state": "broken", "reason": load_problem(record.get("error") or "unknown")}
    if fix["state"] == "failed" and fix["tail"]:
        return {"ready": False, "state": "broken", "reason": fix["tail"]}
    return {"ready": False, "state": "unchecked",
            "reason": "The speech-timing model is downloaded but hasn't been checked yet. Press Fix it to check it."}
