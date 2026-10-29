"""Studio Cloud on the creator's own Modal account ("bring your own cloud").

The owner (2026-10-29): "If their computer is too slow and they really want to render in the cloud, they need to add
their own API keys." So a creator pastes a Modal token (an ID and a secret) in Setup, and Studio renders on THEIR
Modal account, billed by Modal per second, instead of ChannelRecipe's machines and cloud balance (cr_cloud.CloudPods,
kept for ChannelRecipe's team and closed to everyone else).

Everything lives in the creator's own Modal workspace, so the token is the only thing they add:
- machines: the `channelrecipe-studio` app (cr_cloud_modal.py), deployed there by Studio once and again when it changes;
- storage: one Modal Volume (`VolumeBucket`) stands in for the farm's bucket: the job, the prepared voice and
  pictures, the code, the finished video;
- Claude: the machine has no Claude login. Its Claude calls (and its YouTube downloads, which YouTube refuses to
  datacentre machines) go into a Modal queue that this computer's Studio answers through the creator's own Claude
  Code (`Relay` here, `LocalRelay` on the machine), so Claude stays on their plan exactly as at home.

Nothing passes through channelrecipe.com except the official Studio download link the machine starts from (the same
signed link an update uses), so this costs ChannelRecipe nothing and needs no server change.

    python -m cr_cloud_byo pod      what one of the creator's Modal machines runs (cr_cloud_modal.py)
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import queue as _queue
import re
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOME = Path(__file__).resolve().parent
APP = "channelrecipe-studio"
VOLUME = "channelrecipe-studio-files"
QUEUE = "channelrecipe-studio-relay"
ANSWERS = "channelrecipe-studio-answers"
SCHEME = "modalvol://"
STATE = ".cr-cloud-byo.json"
APP_FILE = "cr_cloud_modal.py"
# Exact: checked 2026-10-29 in fresh venvs with Studio's own requirements.txt on Python 3.10 and 3.13. It only ADDS
# packages (grpclib, synchronicity, aiohttp, rich, toml, watchfiles…) and changes none Studio already has (protobuf,
# click, requests…); pip check stays clean and Studio's modules still import. install_library also pins everything
# already installed, so a later Modal can never move one of Studio's own libraries.
MODAL_REQUIREMENT = "modal==1.6.3"

# Modal's list prices (modal.com/pricing, 2026-10): $0.0000131 a physical core-second, $0.00000222 a GiB-second, an
# NVIDIA L4 $0.000222 a second. The CPU machine is 8 cores and 32 GiB; the GPU machine the same plus an L4.
PRICE_PER_HR = {"cpu": round((8 * 0.0000131 + 32 * 0.00000222) * 3600, 2),
                "gpu": round((8 * 0.0000131 + 32 * 0.00000222 + 0.000222) * 3600, 2)}
LABELS = {"cpu": "Your Modal machine (8 cores)", "gpu": "Your Modal machine with a graphics card (NVIDIA L4)"}
# The same planning numbers as ChannelRecipe's own machines (webapp/cloud_jobs.py): fixed work plus time per minute
STARTUP_MINUTES, PER_VIDEO_MINUTE, MAX_MINUTES = 12, {"cpu": 2.5, "gpu": 7.0}, 240
RELAY_WAIT_S = 30 * 60          # longer than a relayed Claude Code call may take here (cr_cloud.RELAYED_CC_TIMEOUT)
# The settings a machine is given: the engine's render keys (farm/config.py RENDER_KEYS) and the job's own. Never an
# Anthropic key: the machine's Claude is the creator's Claude Code, through the relay.
RENDER_KEYS = {
    "ALGROW_API_KEY", "WAVESPEED_API_KEY", "KIE_API_KEY", "GEMINI_API_KEY", "PEXELS_API_KEY", "AI33_API_KEY",
    "AI33_VOICE_ID", "ALGROW_VOICE_ID", "ALGROW_TTS_MODEL", "ALGROW_IMAGE_MODEL", "WAVESPEED_VOICE",
    "WAVESPEED_IMAGE_MODEL", "VOICE_PROVIDER", "IMAGE_PROVIDER", "CLAUDE_SCRIPT_MODEL", "CLAUDE_SCRIPT_BODY_MODEL",
    "CLAUDE_UTILITY_MODEL", "KIE_IMAGE_MODEL", "GEMINI_MODEL", "WHISPER_MODEL", "VIDEO_W", "VIDEO_H", "FPS",
    "NUM_AI_IMAGES", "FOOTAGE_GRADE", "YT_SLEEP", "YT_WORKERS", "YTDLP_PLAYER_CLIENT", "YTDLP_ARGS", "YTDLP_PROXY",
    "AI_IMG_WORKERS", "DL_WORKERS", "RENDER_WORKERS", "FFMPEG_THREADS", "SOURCE_LINKS", "SIRAY_API_KEY",
    "EPIDEMIC_API_KEY", "EPIDEMIC", "ELEVENLABS_API_KEY", "ELEVENLABS_MODEL",
    "FARM_JOB_KEY", "FARM_SLUG", "FARM_TITLE", "FARM_ASSETS_URL",
}

GUIDE = [
    "Make a free account at modal.com (Sign up, with Google or GitHub is fine).",
    "Only for 3D videos and 3D clones: add a card in Modal (Settings, Usage and billing). Modal's machines with a "
    "graphics card don't start without one. Other videos run on Modal's free monthly credits first.",
    "In Modal open Settings, API Tokens and press New Token. Modal shows a Token ID (starts with ak-) and a Token "
    "secret (starts with as-) once.",
    "Paste both here and press Check and save. Studio checks them and sets up its cloud storage on your Modal account.",
    "In Create, Where it renders, choose Studio Cloud. The first cloud video also sets up Studio's machines on your "
    "account (about 5 to 10 minutes, once).",
]


def _s3_error(message: str):
    try:
        from farm.s3 import S3Error
    except ImportError:                                     # pragma: no cover - the farm ships with every release
        S3Error = RuntimeError
    return S3Error(message)


# ── the creator's Modal token ─────────────────────────────────────────────────────────────────────────────────

def keys(home: Path = HOME) -> tuple[str, str]:
    """The Modal token from Setup (Studio's .env, loaded into this process), or ('', '')."""
    tid, secret = os.getenv("MODAL_TOKEN_ID", "").strip(), os.getenv("MODAL_TOKEN_SECRET", "").strip()
    if not (tid and secret):
        try:
            from dotenv import dotenv_values
            saved = dotenv_values(home / ".env")
            tid, secret = str(saved.get("MODAL_TOKEN_ID") or "").strip(), str(saved.get("MODAL_TOKEN_SECRET") or "").strip()
        except Exception:                                   # noqa: BLE001 - no .env yet
            return "", ""
    return (tid, secret) if valid_token(tid, secret) else ("", "")


def valid_token(tid: str, secret: str) -> bool:
    return bool(re.fullmatch(r"ak-[A-Za-z0-9]{8,80}", tid or "") and re.fullmatch(r"as-[A-Za-z0-9]{8,80}", secret or ""))


def configured(home: Path = HOME) -> bool:
    """Has this creator added their own Modal account? (Then Studio Cloud means their account.)"""
    return all(keys(home))


def on_machine() -> bool:
    """Inside one of the creator's Modal machines (cr_cloud_modal.py sets both): Modal signs it in by itself."""
    return bool(os.getenv("CR_CLOUD_BYO") == "1" and os.getenv("CR_CLOUD_JOB"))


def library_ready() -> bool:
    import importlib.util
    return importlib.util.find_spec("modal") is not None


def install_library(timeout: int = 600) -> str:
    """Modal's Python library in Studio's own Python (only creators who add a Modal account need it). '' when ready,
    else why not."""
    if library_ready():
        return ""
    import tempfile
    pins = installed_pins()
    with tempfile.TemporaryDirectory(prefix="cr-modal-") as tmp:
        constraints = Path(tmp) / "keep-studio.txt"
        constraints.write_text("\n".join(pins) + "\n", encoding="utf-8")
        try:
            p = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check",
                                "-c", str(constraints), MODAL_REQUIREMENT], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout)
        except (OSError, subprocess.SubprocessError) as e:
            return f"Studio couldn't install Modal's library ({type(e).__name__})."
    import importlib
    importlib.invalidate_caches()
    if p.returncode != 0 or not library_ready():
        text = (p.stderr or p.stdout or "").strip()
        if "conflict" in text.lower() or "resolutionimpossible" in text.lower():
            return ("Studio didn't install Modal's library: it needs a different version of one of Studio's own "
                    "libraries, and Studio never changes those. Nothing was changed. Copy diagnostics for support.")
        tail = text.splitlines()[-1:] or [""]
        return "Studio couldn't install Modal's library: " + tail[0][:200]
    return ""


def installed_pins() -> list:
    """Every library already in Studio's Python, pinned to its installed version: the constraints Modal's library is
    installed under, so it can add what it needs but never upgrade or downgrade one of Studio's own."""
    from importlib import metadata
    pins = {}
    for dist in metadata.distributions():
        name, version = (dist.metadata.get("Name") or "").strip(), (dist.version or "").strip()
        if name and version and re.fullmatch(r"[A-Za-z0-9._-]+", name) and re.fullmatch(r"[A-Za-z0-9.+!_-]+", version):
            pins[name.lower()] = f"{name}=={version}"
    pins.pop("modal", None)
    return sorted(pins.values())


_CLIENT = {"key": "", "client": None}
_CLIENT_LOCK = threading.Lock()


def client(home: Path = HOME):
    """A Modal client signed in with the creator's token (None on the machine: it is already inside their account)."""
    if on_machine():
        return None
    tid, secret = keys(home)
    if not tid:
        raise RuntimeError("Add your Modal account in Setup first.")
    fingerprint = hashlib.sha256(f"{tid}:{secret}".encode()).hexdigest()
    with _CLIENT_LOCK:
        if _CLIENT["key"] != fingerprint:
            _CLIENT.update(key=fingerprint, client=_modal().Client.from_credentials(tid, secret))
        return _CLIENT["client"]


_IMPORT_LOCK = threading.Lock()


def _modal():
    """Modal's library. Studio wraps subprocess.Popen in a function (cr_workers, for its media workers), and on Python
    3.13 and older Modal's own classes evaluate `subprocess.Popen | None` when they are defined, so `import modal` in
    a running Studio failed with "unsupported operand type(s) for |: 'function' and 'NoneType'" (found 2026-10-29 on
    a fresh 3.13 venv; 3.14 defers annotations, which hid it). The import sees the real Popen for that moment."""
    if "modal" in sys.modules:
        return sys.modules["modal"]
    import subprocess as sp
    with _IMPORT_LOCK:
        if "modal" in sys.modules:
            return sys.modules["modal"]
        current = sp.Popen
        real = getattr(sys.modules.get("cr_workers"), "_original_popen", None)
        swap = not isinstance(current, type) and isinstance(real, type)
        if swap:
            sp.Popen = real
        try:
            import modal
        finally:
            if swap:
                sp.Popen = current
    return modal


def volume(home: Path = HOME):
    return _modal().Volume.from_name(VOLUME, create_if_missing=True, client=client(home))


def check_token(tid: str, secret: str) -> str:
    """'' when Modal accepts this token (and Studio's storage exists on the account), else why not, in plain words."""
    if not valid_token(tid, secret):
        return "Paste the Token ID (starts with ak-) and the Token secret (starts with as-) from Modal's API Tokens page."
    modal = _modal()
    try:
        c = modal.Client.from_credentials(tid, secret)
        modal.Volume.from_name(VOLUME, create_if_missing=True, client=c).hydrate()
    except Exception as e:                                  # noqa: BLE001 - Modal says why in its own words
        text = f"{type(e).__name__}: {e}".lower()
        if "auth" in text or "token" in text or "unauthenticated" in text or "permission" in text:
            return "Modal didn't accept this token. Make a new one in Modal (Settings, API Tokens) and paste both parts."
        return f"Modal didn't answer ({type(e).__name__}). Check your connection and try again."
    return ""


# ── storage: the creator's Modal Volume as the farm's bucket ──────────────────────────────────────────────────

class VolumeBucket:
    """The farm's bucket (farm/s3.py) on a Modal Volume in the creator's account. A "link" is the file's name in the
    volume (modalvol://…): the machine reads it from the same volume."""

    def __init__(self, vol):
        self.vol = vol
        self.bucket = "your Modal storage"

    def presign(self, key: str, seconds: int = 0, method: str = "GET", content_type: str = "") -> str:
        return SCHEME + key.lstrip("/")

    def _upload(self, key: str, source) -> None:
        try:
            with self.vol.batch_upload(force=True) as batch:
                batch.put_file(source, "/" + key.lstrip("/"))
        except Exception as e:                              # noqa: BLE001 - the farm's own storage error
            raise _s3_error(f"PUT {key}: {type(e).__name__}: {str(e)[:160]}")

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self._upload(key, io.BytesIO(data))

    def put_file(self, key: str, path, content_type: str | None = None, timeout: int = 1800) -> int:
        path = Path(path)
        self._upload(key, str(path))
        return path.stat().st_size

    def get_bytes(self, key: str) -> bytes:
        buf = io.BytesIO()
        try:
            self.vol.read_file_into_fileobj(key.lstrip("/"), buf)
        except Exception as e:                              # noqa: BLE001
            raise _s3_error(f"GET {key}: {type(e).__name__}")
        return buf.getvalue()

    def get_file(self, key: str, path, timeout: int = 1800) -> int:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        try:
            with tmp.open("wb") as fh:
                got = self.vol.read_file_into_fileobj(key.lstrip("/"), fh)
        except Exception as e:                              # noqa: BLE001
            tmp.unlink(missing_ok=True)
            raise _s3_error(f"GET {key}: {type(e).__name__}")
        tmp.replace(path)
        return int(got or path.stat().st_size)

    def _entries(self, folder: str) -> list:
        try:
            return list(self.vol.listdir(folder or "/", recursive=True))
        except Exception:                                   # noqa: BLE001 - a folder not made yet
            return []

    def exists(self, key: str) -> bool:
        key = key.lstrip("/")
        folder = key.rsplit("/", 1)[0] if "/" in key else "/"
        try:
            return any(e.path.lstrip("/") == key for e in self.vol.listdir(folder))
        except Exception:                                   # noqa: BLE001 - the farm treats "unknown" as absent
            return False

    def delete(self, key: str) -> None:
        try:
            self.vol.remove_file(key.lstrip("/"), recursive=key.endswith("/"))
        except Exception as e:                              # noqa: BLE001
            if "not found" not in str(e).lower() and "NotFound" not in type(e).__name__:
                raise _s3_error(f"DELETE {key}: {type(e).__name__}")

    def list(self, prefix: str = "") -> list:
        prefix = prefix.lstrip("/")
        folder = prefix if prefix.endswith("/") or not prefix else prefix.rsplit("/", 1)[0] if "/" in prefix else ""
        out = []
        for e in self._entries(folder):
            path = e.path.lstrip("/")
            kind = getattr(getattr(e, "type", None), "name", "FILE")     # modal.volume.FileEntryType (FILE = 1)
            if str(kind).upper() != "FILE" or not path.startswith(prefix):
                continue
            stamp = time.gmtime(float(getattr(e, "mtime", 0) or 0))
            out.append({"key": path, "size": int(getattr(e, "size", 0) or 0),
                        "modified": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", stamp)})
        return out


def bucket(home: Path = HOME) -> VolumeBucket:
    return VolumeBucket(volume(home))


def tidy(b: VolumeBucket, keep_code: str = "", keep_version: str = "") -> int:
    """Old leftovers in the creator's storage: earlier code bundles and releases, prepared files of jobs a few days
    old, YouTube relay files. Finished videos stay until the creator deletes them. Best effort."""
    import calendar
    gone, now = 0, time.time()

    def age(row) -> float:
        try:
            return now - calendar.timegm(time.strptime(row["modified"][:19], "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            return 0.0
    for prefix, older, keep in (("code/", 86400, keep_code), ("releases/", 0, keep_version),
                                ("jobs/", 3 * 86400, ""), ("relay/", 86400, ""),
                                ("runs/", 86400, "")):
        for row in b.list(prefix):
            if (keep and keep in row["key"]) or age(row) < older:
                continue
            if prefix == "jobs/" and not row["key"].endswith("-assets.zip"):
                continue
            try:
                b.delete(row["key"])
                gone += 1
            except Exception:                               # noqa: BLE001
                pass
    return gone


# ── machines ──────────────────────────────────────────────────────────────────────────────────────────────────

def estimate(minutes: float, machine: str) -> dict:
    """What one video should cost on the creator's Modal account, and the most it can (its machine's time limit)."""
    machine = "gpu" if machine == "gpu" else "cpu"
    minutes = max(0.5, min(float(minutes or 10), 120.0))
    expected = STARTUP_MINUTES + PER_VIDEO_MINUTE[machine] * minutes
    most = min(MAX_MINUTES, math.ceil(expected * 2 + 10))
    rate = PRICE_PER_HR[machine]
    return {"machine": machine, "label": LABELS[machine], "price_per_hr": rate,
            "cents_per_min": round(rate * 100 / 60, 2), "expected_minutes": round(expected), "max_minutes": most,
            "expected_cents": max(1, math.ceil(expected * rate * 100 / 60)),
            "max_cents": max(1, math.ceil(most * rate * 100 / 60))}


def _state(home: Path) -> dict:
    try:
        data = json.loads((home / STATE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(home: Path, **fields) -> dict:
    data = {**_state(home), **fields}
    try:
        (home / STATE).write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        pass
    return data


def partition(home: Path = HOME) -> str:
    """This Studio's own lane in the relay queue (a creator with Studio on two computers gets each its own)."""
    st = _state(home)
    if not re.fullmatch(r"[a-f0-9]{16}", str(st.get("studio") or "")):
        st = _save_state(home, studio=secrets.token_hex(8))
    return st["studio"]


def app_digest(home: Path = HOME) -> str:
    try:
        return hashlib.sha256((home / APP_FILE).read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def ensure_deployed(home: Path = HOME, sink=None) -> None:
    """Studio's machines on the creator's account: deployed the first time, and again when cr_cloud_modal.py
    changed (a Studio update). The first deploy builds the machine image there, which takes a few minutes."""
    say = sink or (lambda line: None)
    tid, secret = keys(home)
    want = app_digest(home)
    account = hashlib.sha256(tid.encode()).hexdigest()[:12]
    st = _state(home)
    if st.get("deployed") == want and st.get("account") == account:
        try:
            _modal().Function.from_name(APP, "render_cpu", client=client(home)).hydrate()
            return
        except Exception:                                   # noqa: BLE001 - deleted on Modal's side: deploy again
            pass
    say("Setting up Studio's machines on your Modal account (the first time takes about 5 to 10 minutes)…")
    env = {**os.environ, "MODAL_TOKEN_ID": tid, "MODAL_TOKEN_SECRET": secret, "PYTHONIOENCODING": "utf-8"}
    try:
        p = subprocess.run([sys.executable, "-m", "modal", "deploy", APP_FILE], cwd=home, env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=1800)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Setting up Studio's machines on your Modal account took too long. Try again.")
    if p.returncode != 0:
        lines = [x for x in (p.stderr or p.stdout or "").strip().splitlines() if x.strip()]
        last = _scrub(lines[-1][:240] if lines else f"exit {p.returncode}", tid, secret)
        if re.search(r"(?i)unauthenticated|invalid token|token .*(invalid|revoked|not found)|authentication", last):
            raise RuntimeError("Modal didn't accept your token (it may have been deleted in Modal). Make a new one "
                               "in Modal (Settings, API Tokens) and paste both parts in Setup, Render in the cloud.")
        raise RuntimeError("Modal couldn't set up Studio's machines on your account: " + last)
    _save_state(home, deployed=want, account=account, deployed_at=time.time())


def _scrub(text: str, *secrets_: str) -> str:
    """A message from Modal, without the creator's token in it."""
    for s in secrets_:
        if s:
            text = text.replace(s, "…")
    return re.sub(r"\ba[ks]-[A-Za-z0-9]{8,}", "…", text)


def _connection(home: Path) -> tuple[str, str]:
    import cr_cloud
    return cr_cloud._connection(home)


def release_link(home: Path = HOME, http=None) -> str:
    """The official Studio download for this Studio's version (the signed link an update uses), when this Studio is
    on the published version; '' otherwise (the machine then gets this Studio's whole code instead)."""
    import cr_cloud
    import requests
    version = cr_cloud.release_version(home)
    base, key = _connection(home)
    if not (version and base):
        return ""
    http = http or requests
    try:
        r = http.get(base + "/api/studio/status", headers={"Authorization": "Bearer " + key}, timeout=20)
        if not r.ok or (r.json() or {}).get("version") != version:
            return ""
        r = http.get(base + "/api/studio/download", headers={"Authorization": "Bearer " + key}, timeout=30,
                     allow_redirects=False)
    except (requests.RequestException, ValueError):
        return ""
    location = r.headers.get("Location", "") if r.status_code in (301, 302, 303, 307, 308) else ""
    return location if location.startswith("https://") else ""


def release_cached(b: VolumeBucket, version: str) -> bool:
    return bool(version) and b.exists(f"releases/channelrecipe-{version}.zip")


def machine_env(env: dict, spec: dict, home: Path = HOME, release_url: str = "") -> dict:
    """The machine's environment: the creator's render settings that are allowed, where its job and code are in
    their storage, and where its Claude questions go."""
    import cr_cloud
    out = {k: v for k, v in (env or {}).items() if k in RENDER_KEYS and isinstance(v, str) and len(v) < 20000}
    code = str(env.get("FARM_CODE_URL") or "")
    if not code.startswith(SCHEME) or not out.get("FARM_JOB_KEY"):
        raise RuntimeError("The job isn't in your Modal storage.")
    out["CR_CODE_KEY"] = code[len(SCHEME):]
    if "/code/overlay-" in "/" + out["CR_CODE_KEY"]:
        out.update(CR_CODE_KIND="overlay", CR_STUDIO_VERSION=cr_cloud.release_version(home))
        if release_url:
            out["FARM_RELEASE_URL"] = release_url
    settings = cr_cloud.studio_settings(home)
    if settings:
        out["CR_STUDIO_SETTINGS"] = json.dumps(settings)
    out.update(cr_cloud.video_size(str(spec.get("style") or "")))
    out.update({"CR_CLOUD_BYO": "1", "CR_RELAY_PARTITION": partition(home), "CLAUDE_PROVIDER": "anthropic",
                "SCRIPT_PROVIDER": "anthropic", "CLAUDE_FALLBACK": "", "PYTHONUNBUFFERED": "1",
                "FARM_MAX_MINUTES": str(estimate(float(spec.get("minutes") or 10),
                                                 "gpu" if cr_cloud.wants_gpu(spec) else "cpu")["max_minutes"])})
    out.setdefault("AI_IMG_WORKERS", "4")
    return out


WATCH_EVERY_S = 20
STUCK_GRACE_S = 30 * 60      # past its own limit (a graphics-card machine can wait unbilled in Modal's queue first)
_KEEP = ("id", "call_id", "slug", "title", "machine", "status", "price_per_hr", "started_at", "max_minutes")


def running_job(job_id: str) -> bool:
    """Is this one of this Studio's cloud jobs, still running? (The relay answers only those.)"""
    job = ModalPods.JOBS.get(str(job_id or ""))
    return bool(job and job.get("status") == "running")


class ModalPods:
    """RunPod's API (farm/runpod.py) as far as the farm uses it, answered by the creator's own Modal account."""
    JOBS: dict = {}
    _lock = threading.Lock()

    def __init__(self, api_key: str = "", session=None, home: Path = HOME):
        self.home = home
        self._restore()

    def _restore(self) -> None:
        """Machines started before Studio was restarted: still listed, so the farm's stop button reaches them."""
        saved = _state(self.home).get("jobs")
        if not isinstance(saved, dict):
            return
        with self._lock:
            for job_id, row in saved.items():
                if (isinstance(row, dict) and job_id not in self.JOBS and row.get("status") == "running"
                        and row.get("call_id") and time.time() - float(row.get("started_at") or 0)
                        < float(row.get("max_minutes") or 0) * 60 + STUCK_GRACE_S):
                    self.JOBS[job_id] = {**row, "seconds": None, "error": ""}

    def _remember(self) -> None:
        with self._lock:
            live = {k: {f: j.get(f) for f in _KEEP} for k, j in self.JOBS.items() if j.get("status") == "running"}
        _save_state(self.home, jobs=live)

    def _watch(self, job_id: str) -> None:
        """The farm only reads the job's status file, which a machine that never booted (no card for a graphics
        card, Modal credits used up, a revoked token, a boot error) never writes: the Studio would wait hours on
        "rendering". Asking Modal how the call went writes the farm's last status for it, with Modal's reason."""
        while True:
            time.sleep(WATCH_EVERY_S)
            job = self.JOBS.get(job_id)
            if job is None or job["status"] != "running":
                return
            try:
                self._refresh(job)
            except Exception:                               # noqa: BLE001 - Modal unreachable for a moment
                pass

    @staticmethod
    def _pod(job: dict) -> dict:
        return {"id": job["id"], "name": "channelrecipe-" + (job.get("slug") or job["id"]),
                "desiredStatus": "RUNNING" if job.get("status") == "running" else "EXITED",
                "costPerHr": job.get("price_per_hr") or 0, "status": job.get("status"), "job": job}

    def create(self, *, name: str, image: str = "", env: dict, args: str = "", **_machine) -> dict:
        import cr_cloud
        b = bucket(self.home)
        try:
            spec = json.loads(b.get_bytes(env["FARM_JOB_KEY"]).decode("utf-8"))
        except Exception:                                   # noqa: BLE001 - the estimate then assumes ten minutes
            spec = {}
        machine = "gpu" if cr_cloud.wants_gpu(spec) else "cpu"
        est = estimate(float(spec.get("minutes") or 10), machine)
        version = cr_cloud.release_version(self.home)
        link = "" if release_cached(b, version) else release_link(self.home)
        job_env = machine_env(env, spec, self.home, link)
        job_id = "byo_" + secrets.token_hex(8)
        fn = _modal().Function.from_name(APP, "render_gpu" if machine == "gpu" else "render_cpu",
                                         client=client(self.home))
        call = fn.spawn(job_id, job_env, est["max_minutes"] * 60)
        job = {"id": job_id, "call_id": call.object_id, "slug": env.get("FARM_SLUG", ""),
               "title": env.get("FARM_TITLE", ""), "machine": machine, "status": "running",
               "price_per_hr": est["price_per_hr"], "started_at": time.time(), "max_minutes": est["max_minutes"],
               "seconds": None, "error": ""}
        with self._lock:
            self.JOBS[job_id] = job
        _save_state(self.home, last_job={k: job[k] for k in ("id", "call_id", "slug", "machine", "started_at")})
        self._remember()
        Relay.ensure(self.home)
        threading.Thread(target=self._watch, args=(job_id,), daemon=True, name="cr-cloud-byo-watch").start()
        return self._pod(job)

    def _refresh(self, job: dict) -> dict:
        if job["status"] != "running":
            return job
        modal = _modal()
        try:
            result = modal.FunctionCall.from_id(job["call_id"], client=client(self.home)).get(timeout=0)
        except modal.exception.FunctionTimeoutError:
            job.update(status="failed", error="stopped at its time limit")
        except modal.exception.OutputExpiredError:
            job.update(status="failed", error="its result expired")
        except (TimeoutError, modal.exception.TimeoutError):
            if time.time() - job["started_at"] > job["max_minutes"] * 60 + STUCK_GRACE_S:
                job["error"] = job.get("error") or "it didn't finish within its time limit on Modal"
                self.terminate(job["id"])
            return job
        except modal.exception.InputCancellation:
            job.update(status="cancelled")
        except Exception as e:                              # noqa: BLE001 - a crashed machine is a failed job
            text = f"{type(e).__name__}: {str(e)[:300]}"
            low = text.lower()
            if any(w in low for w in ("payment", "billing", "credit", "card on file", "spend limit", "insufficient funds")):
                text = ("Modal didn't start the machine: your Modal account needs a card on file (machines with a "
                        "graphics card always do) or has used its credits or spending limit. See Modal, Settings, "
                        "Usage and billing. (" + _scrub(text[:160]) + ")")
            elif any(w in low for w in ("unauthenticated", "invalid token", "permission denied")):
                text = "Modal didn't accept your token any more. Make a new one and paste it in Setup, Render in the cloud."
            job.update(status="failed", error=text)
        else:
            job.update(status="done" if (result or {}).get("ok") else "failed",
                       seconds=(result or {}).get("seconds"), error=str((result or {}).get("error") or ""))
        if job["status"] != "running":
            job["ended_at"] = time.time()
            if job["status"] in ("failed", "cancelled") and job.get("slug"):
                _final_status(self.home, job)
            self._remember()
        return job

    def get(self, pod_id: str) -> dict:
        job = self.JOBS.get(pod_id)
        if job is None:
            raise RuntimeError("No such cloud job on this Studio.")
        return self._pod(self._refresh(job))

    def list(self) -> list:
        return [self._pod(j) for j in list(self.JOBS.values()) if self._refresh(j)["status"] == "running"]

    def terminate(self, pod_id: str, tries: int = 3) -> bool:
        job = self.JOBS.get(pod_id)
        if job is None:
            return True
        if job["status"] != "running":
            return True
        # the farm also "terminates" a machine whose video is done: it is only uploading its last files and returning
        # how long it ran, so give it a moment to finish by itself before stopping it
        for _ in range(12 if self._done_already(job) else 0):
            if self._refresh(job)["status"] != "running":
                return True
            time.sleep(5)
        for _ in range(max(1, tries)):
            try:
                _modal().FunctionCall.from_id(job["call_id"], client=client(self.home)).cancel(terminate_containers=True)
            except Exception:                               # noqa: BLE001
                time.sleep(2)
                continue
            job.update(status="cancelled", ended_at=time.time(), error=job.get("error") or "stopped")
            if job.get("slug"):
                _final_status(self.home, job)
            self._remember()
            return True
        return False

    def _done_already(self, job: dict) -> bool:
        try:
            return json.loads(bucket(self.home).get_bytes(f"status/{job['slug']}.json").decode("utf-8")).get(
                "state") == "done"
        except Exception:                                   # noqa: BLE001
            return False

    def gpus(self) -> list:
        return []

    def cpus(self) -> list:
        return []


def _final_status(home: Path, job: dict) -> None:
    """A machine that was stopped never writes its last status, and the farm's loop waits for one. Write it, as the
    machine would have, unless the machine already did."""
    try:
        b = bucket(home)
        key = f"status/{job['slug']}.json"
        try:
            now = json.loads(b.get_bytes(key).decode("utf-8"))
            if now.get("state") in ("done", "failed", "timeout"):
                return
        except Exception:                                   # noqa: BLE001
            pass
        b.put_bytes(key, json.dumps({"slug": job["slug"], "title": job.get("title") or "", "state": "failed",
                                     "error": job.get("error") or "stopped", "updated": time.time()}).encode(),
                    "application/json")
    except Exception:                                       # noqa: BLE001 - the farm's own timeout still ends it
        pass


# ── the Claude relay, through a queue in the creator's account ───────────────────────────────────────────────

def _queues(home: Path = HOME):
    modal = _modal()
    c = client(home)
    return (modal.Queue.from_name(QUEUE, create_if_missing=True, client=c),
            modal.Dict.from_name(ANSWERS, create_if_missing=True, client=c))


class Relay:
    """Answers the Claude questions (and YouTube downloads) the creator's Modal machines ask, here, with this
    computer's Claude Code and connection."""
    _thread = None
    _lock = threading.Lock()
    WORKERS = 4

    @classmethod
    def ensure(cls, home: Path = HOME):
        with cls._lock:
            if cls._thread is None or not cls._thread.is_alive():
                cls._thread = threading.Thread(target=cls._loop, args=(home,), daemon=True, name="cr-cloud-byo-relay")
                cls._thread.start()

    @classmethod
    def _loop(cls, home: Path):
        import cr_cloud
        idle_since = time.time()
        busy = threading.Semaphore(cls.WORKERS)
        lane = partition(home)
        q = answers = None
        while time.time() - idle_since < 20 * 60 or cr_cloud._farm_running():   # while a machine may still ask
            if not configured(home):
                return
            busy.acquire()
            item = None
            try:
                if q is None:
                    q, answers = _queues(home)
                item = q.get(timeout=20, partition=lane)
            except _queue.Empty:
                pass
            except Exception:                               # noqa: BLE001 - Modal unreachable for a moment
                q = None
                time.sleep(5)
            try:
                fresh = (isinstance(item, dict) and isinstance(item.get("id"), str)
                         and float(item.get("deadline") or 0) >= time.time())
            except (TypeError, ValueError):
                fresh = False
            if not fresh:
                busy.release()
                continue
            idle_since = time.time()

            def work(item=item, answers=answers):
                text, error = "", ""
                try:
                    try:
                        raw = answers.pop("q:" + item["id"], None)
                        if raw is None:
                            # never silently: the machine would wait half an hour for an answer (2026-10-29: a
                            # 20 MB question came back from Modal's Dict as nothing)
                            error = "the question didn't arrive at your Studio"
                        elif not running_job(item.get("job")):
                            # only this Studio's own running jobs get its Claude Code and connection: never a
                            # question someone else with access to the Modal workspace put in the queue
                            error = "not a cloud video this Studio is running"
                        else:
                            text = cr_cloud.answer(_unstow(raw, bucket(home)))
                    except BaseException as e:              # noqa: BLE001 - the engine exits on Claude errors
                        text, error = "", f"{type(e).__name__}: {str(e)[:200]}"
                    if error:
                        print(f"Studio Cloud: a question from your Modal machine failed here: {error}", flush=True)
                    for _ in range(3):
                        try:
                            _stow(answers, "a:" + item["id"], {"text": text, "error": error}, bucket(home))
                            break
                        except Exception:                   # noqa: BLE001
                            time.sleep(3)
                finally:
                    busy.release()
            threading.Thread(target=work, daemon=True).start()


def ask(request: dict, wait: float = RELAY_WAIT_S) -> dict | None:
    """On the machine: one question for the creator's Studio; its answer ({text, error}) or None when nobody answered."""
    modal = _modal()
    q = modal.Queue.from_name(QUEUE, create_if_missing=True)
    answers = modal.Dict.from_name(ANSWERS, create_if_missing=True)
    store = VolumeBucket(modal.Volume.from_name(VOLUME, create_if_missing=True))
    rid = secrets.token_hex(10)
    deadline = time.time() + wait
    _stow(answers, "q:" + rid, request, store)
    q.put({"id": rid, "deadline": deadline, "job": os.getenv("CR_CLOUD_JOB", "")},
          partition=os.getenv("CR_RELAY_PARTITION") or "studio")
    while time.time() < deadline:
        got = answers.pop("a:" + rid, None)
        if got is not None:
            try:
                return _unstow(got, store)
            except Exception as e:                          # noqa: BLE001 - an answer that can't be read
                return {"text": "", "error": f"the answer couldn't be read ({type(e).__name__})"}
        time.sleep(1.0)
    answers.pop("q:" + rid, None)
    return None


# Modal's Dict carries a relay question or answer up to here; a bigger one (a vision question with a dozen full-size
# stills is 20-60 MB) goes through the creator's volume, and the Dict carries only where it is. Measured 2026-10-29:
# Dict values of 20 and 80 MB came back from pop() as nothing or cut short, so a depth check's question was lost and
# the machine waited on it.
INLINE_MAX = 1_000_000
_RELAY_FILE = re.compile(r"relay/[aq]-[0-9a-f]{20}\.json")


def _stow(answers, key: str, value, store) -> None:
    raw = json.dumps(value)
    if len(raw) <= INLINE_MAX:
        answers.put(key, value)
        return
    path = f"relay/{key.replace(':', '-')}.json"
    store.put_bytes(path, raw.encode("utf-8"), "application/json")
    answers.put(key, {"_relay_file": path})


def _unstow(value, store):
    if isinstance(value, dict) and set(value) == {"_relay_file"}:
        path = str(value["_relay_file"])
        if not _RELAY_FILE.fullmatch(path):
            raise ValueError("not a relay file")
        data = json.loads(store.get_bytes(path).decode("utf-8"))
        try:
            store.delete(path)
        except Exception:                                   # noqa: BLE001 - tidy() removes it after a day
            pass
        return data
    return value


def _sse(event: dict) -> bytes:
    return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()


def _message(text: str, model: str) -> dict:
    return {"id": "msg_" + secrets.token_hex(8), "type": "message", "role": "assistant", "model": model,
            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}}


class _Handler(BaseHTTPRequestHandler):
    """The machine's own Anthropic Messages endpoint (and YouTube relay), answered through `ask`."""
    protocol_version = "HTTP/1.1"
    asker = staticmethod(ask)

    def log_message(self, *args):                           # the render log has enough
        pass

    def _body(self):
        try:
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return None

    def _send(self, code: int, data: bytes, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _wait(self, request: dict, beat):
        """The answer, calling beat() every 10 s while waiting (keep-alive for a long Claude step)."""
        box, done = {}, threading.Event()

        def run():
            box["res"] = self.asker(request)
            done.set()
        threading.Thread(target=run, daemon=True).start()
        while not done.wait(10):
            beat()
        return box.get("res")

    def do_POST(self):
        body = self._body()
        if not isinstance(body, dict):
            return self._send(400, b'{"error":"send JSON"}')
        if self.path.rstrip("/").endswith("/relay/ytdlp"):
            res = self._wait({"kind": "ytdlp", "argv": [str(a)[:2000] for a in body.get("argv") or []][:60],
                              "inputs": [str(x)[:500] for x in body.get("inputs") or []][:10],
                              "prefix": str(body.get("prefix") or "")}, lambda: None)
            if res and not res.get("error"):
                return self._send(200, (res.get("text") or "{}").encode())
            why = (res or {}).get("error") or "your Studio didn't answer"
            return self._send(200, json.dumps({"rc": 1, "stdout": "", "files": [],
                                               "stderr": "Studio Cloud: " + why}).encode())
        if not self.path.rstrip("/").endswith("/v1/messages") or not body.get("messages"):
            return self._send(404, b'{"error":"not here"}')
        model = str(body.get("model") or "claude")
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            self.wfile.write(_sse({"type": "message_start", "message": {**_message("", model), "content": []}}))
            self.wfile.write(_sse({"type": "ping"}))
            self.wfile.flush()
            res = self._wait(body, lambda: (self.wfile.write(_sse({"type": "ping"})), self.wfile.flush()))
            if not res or res.get("error"):
                why = (res or {}).get("error") or "timed out waiting for your Studio"
                self.wfile.write(_sse({"type": "error", "error": {"type": "overloaded_error",
                                                                  "message": f"Studio relay: {why}; try again"}}))
                return
            for event in ({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                          {"type": "content_block_delta", "index": 0,
                           "delta": {"type": "text_delta", "text": res.get("text") or ""}},
                          {"type": "content_block_stop", "index": 0},
                          {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": {"output_tokens": 0}},
                          {"type": "message_stop"}):
                self.wfile.write(_sse(event))
            self.wfile.flush()
            return
        # a plain request: the headers go out at once and whitespace (which JSON allows) keeps it alive until the
        # answer, so no read timeout on the engine's side ends a long Claude step
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        res = self._wait(body, lambda: (self.wfile.write(b" "), self.wfile.flush()))
        if res and not res.get("error"):
            out = _message(res.get("text") or "", model)
        else:
            out = {"type": "error", "content": [], "error": {
                "type": "overloaded_error", "message": (res or {}).get("error") or "timed out waiting for your Studio"}}
        self.wfile.write(json.dumps(out).encode())
        self.wfile.flush()


def start_local_relay() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True, name="cr-local-relay").start()
    return f"http://127.0.0.1:{server.server_address[1]}"


# ── the machine's side ────────────────────────────────────────────────────────────────────────────────────────

def fetch_assets(b: VolumeBucket, job: Path) -> int:
    """The farm runner's fetch_assets, reading the prepared half of this video from the creator's storage."""
    from farm import runner
    url = os.environ.get("FARM_ASSETS_URL", "")
    if not url.startswith(SCHEME):
        return runner._cr_fetch_assets(job)
    blob = Path("/tmp/channelrecipe-assets.zip")
    try:
        b.get_file(url[len(SCHEME):], blob)
        from farm import assets
        n = assets.unpack(blob, job)
        runner.log(f"unpacked {n} prepared file(s) ({blob.stat().st_size / 1024 ** 2:.1f} MB) — "
                   "the script, voice, stills and edit plan are already done")
        return n
    except Exception as e:                                  # noqa: BLE001 - the pipeline can redo it
        runner.log(f"could not use the prepared assets ({str(e)[:140]}) — making them here instead")
        return 0
    finally:
        blob.unlink(missing_ok=True)


def pod() -> int:
    """Run the farm's own runner with its storage on the creator's Modal Volume and Claude through the relay."""
    sys.path.insert(0, str(HOME))
    if not on_machine():
        print("this is not one of your Studio Cloud machines on Modal", file=sys.stderr)
        return 2
    import cr_cloud
    b = VolumeBucket(_modal().Volume.from_name(VOLUME, create_if_missing=True))
    local = start_local_relay()
    os.environ.update(OPENLUX_BASE_URL=local, OPENLUX_API_KEY="studio-cloud", CLAUDE_RELAY="openlux",
                      CR_LOCAL_RELAY=local)
    cr_cloud._write_settings()
    cr_cloud._relay_claude()
    cr_cloud._relay_youtube()
    import farm.config as fc
    fc.bucket_from = lambda cfg: b
    from farm import runner
    runner._cr_fetch_assets = runner.fetch_assets
    runner.fetch_assets = lambda job: fetch_assets(b, job)
    return int(runner.main() or 0)


# ── what Studio shows ─────────────────────────────────────────────────────────────────────────────────────────

def status(home: Path = HOME) -> dict:
    """Studio Cloud's status when it runs on the creator's own Modal account."""
    out = {"available": False, "byo": "modal", "provider": "Modal", "balance_usd": None, "own_account": True,
           "machines": {k: {"label": LABELS[k], "price_per_hr": PRICE_PER_HR[k]} for k in LABELS},
           "setup_url": "/cr-setup#cloud-render", "why": ""}
    if not configured(home):
        return {**out, "why": "Add your own Modal account in Setup (Render in the cloud) to render off this computer."}
    if not library_ready():
        return {**out, "why": "Studio is still installing Modal's library. Open Setup, Render in the cloud, and press "
                              "Check and save again."}
    return {**out, "available": True, "deployed": bool(_state(home).get("deployed"))}


def summary(home: Path = HOME) -> dict:
    """For Setup's card: is a Modal account saved (never the token itself)?"""
    tid, _secret = keys(home)
    st = _state(home)
    return {"connected": bool(tid), "token_hint": (tid[:5] + "…" + tid[-3:]) if tid else "",
            "library": library_ready(), "deployed": bool(st.get("deployed")), "guide": GUIDE,
            "prices": PRICE_PER_HR}


def quote(p: dict, home: Path = HOME) -> dict:
    """Create's cloud choice on the creator's own account: what these videos should cost on their Modal bill."""
    import cr_cloud
    info = status(home)
    if not info["available"]:
        return {**info}
    why = cr_cloud.blocked(p)
    if why:
        return {**info, "available": False, "why": why, "blocked": True}
    titles = max(1, len([t for t in str(p.get("title") or "").split("\n") if t.strip()]) or int(p.get("titles") or 1))
    minutes = max(1, min(int(float(p.get("minutes") or 10)), 60))
    machine = "gpu" if cr_cloud.wants_gpu({"style": p.get("style"), "features": p.get("features")}) else "cpu"
    each = estimate(minutes, machine)
    return {**info, **each, "titles": titles, "minutes": minutes,
            "expected_usd": round(each["expected_cents"] * titles / 100, 2),
            "most_reserved_usd": round(each["max_cents"] * titles / 100, 2), "short_by_usd": 0, "topup_usd": 0}


if __name__ == "__main__":
    if sys.argv[1:2] == ["pod"]:
        raise SystemExit(pod())
    print(__doc__)
