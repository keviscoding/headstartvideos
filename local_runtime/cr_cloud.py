"""Studio Cloud: the render farm off this computer, on the creator's own Modal account or ChannelRecipe's machines.

Since 2026-10-29 Studio Cloud for creators is their OWN cloud account (cr_cloud_byo.py: a Modal token pasted in
Setup; machines, storage and the Claude relay all in their Modal workspace, billed to them by Modal). When a Modal
account is saved, it is always used. Without one, the older route below (ChannelRecipe's machines paid from a cloud
balance) stays for ChannelRecipe's team only (the server keeps it closed to everyone else).

The engine's farm (README-RENDER-FARM.md) already does the waiting half of a video here and rents a machine for the
computing half. On its own it needs a RunPod account, a storage bucket and an Anthropic key. With a ChannelRecipe
account connected, this module swaps those three for ChannelRecipe's own:

- machines: `CloudPods` stands in for RunPod and asks channelrecipe.com to start one machine per video;
- storage: `BrokerBucket` stands in for the S3 bucket; the server signs one upload or download at a time inside this
  account's folder, so no storage key is ever on this computer or the machine;
- Claude: the machine's Claude calls are queued at channelrecipe.com and answered here by `Relay`, through the
  Claude Code signed in on this computer, so they cost what they cost at home.

    python -m cr_cloud pod        what a Studio Cloud machine runs (cloud/modal_app.py in the web repo)
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests

HOME = Path(__file__).resolve().parent
SENTINEL = "studio-cloud"            # what the farm's config holds instead of a RunPod key or bucket secret
_STATUS = {"at": 0.0, "data": None}
_STATUS_LOCK = threading.Lock()
_INSTALLED = []


def _s3_error(message: str):
    try:
        from farm.s3 import S3Error
    except ImportError:                                     # pragma: no cover - the farm ships with every release
        S3Error = RuntimeError
    return S3Error(message)


def _detail(r) -> str:
    try:
        return str(r.json().get("detail") or r.text)[:300]
    except ValueError:
        return (r.text or "")[:300]


class BrokerBucket:
    """The farm's bucket (farm/s3.py), with every request signed by channelrecipe.com."""

    def __init__(self, base: str, authorization: str, http=None):
        self.base = base.rstrip("/")
        self.headers = {"Authorization": authorization}
        self.http = http or requests.Session()
        self.bucket = "Studio Cloud"

    def _op(self, op: str, key: str = "", **kw) -> dict:
        try:
            r = self.http.post(self.base + "/storage", json={"op": op, "key": key, **kw}, headers=self.headers,
                               timeout=60)
        except requests.RequestException as e:
            raise _s3_error(f"{op} {key}: channelrecipe.com unreachable ({type(e).__name__})")
        if r.status_code >= 400:
            raise _s3_error(f"{op} {key} -> HTTP {r.status_code}: {_detail(r)}")
        return r.json()

    def presign(self, key: str, seconds: int = 6 * 3600, method: str = "GET", content_type: str = "") -> str:
        return self._op("presign", key, method=method, seconds=int(seconds), content_type=content_type)["url"]

    def _send(self, method: str, key: str, url: str, **kw):
        """One request to storage; a network failure is the farm's own storage error, which it is built to survive."""
        try:
            r = self.http.put(url, **kw) if method == "PUT" else self.http.get(url, **kw)
        except requests.RequestException as e:
            raise _s3_error(f"{method} {key}: storage unreachable ({type(e).__name__})")
        return self._check(r, method, key)

    def _check(self, r, method: str, key: str):
        if r.status_code >= 400:
            raise _s3_error(f"{method} {key} -> HTTP {r.status_code}: {(r.text or '')[:200]}")
        return r

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        url = self.presign(key, 3600, "PUT", content_type)
        self._send("PUT", key, url, data=data, headers={"Content-Type": content_type}, timeout=300)

    def put_file(self, key: str, path, content_type: str | None = None, timeout: int = 1800) -> int:
        path = Path(path)
        size = path.stat().st_size
        ctype = content_type or _guess_type(path.name)
        url = self.presign(key, 6 * 3600, "PUT", ctype)
        with path.open("rb") as fh:
            self._send("PUT", key, url, data=fh, headers={"Content-Type": ctype, "Content-Length": str(size)},
                       timeout=timeout)
        return size

    def get_bytes(self, key: str) -> bytes:
        return self._send("GET", key, self.presign(key, 600), timeout=300).content

    def get_file(self, key: str, path, timeout: int = 1800) -> int:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        got = 0
        r = self._send("GET", key, self.presign(key, 6 * 3600), stream=True, timeout=timeout)
        try:
            with tmp.open("wb") as fh:
                for block in r.iter_content(1 << 20):
                    fh.write(block)
                    got += len(block)
        except requests.RequestException as e:
            tmp.unlink(missing_ok=True)
            raise _s3_error(f"GET {key}: the download broke off ({type(e).__name__})")
        tmp.replace(path)
        return got

    def exists(self, key: str) -> bool:
        try:
            return bool(self._op("exists", key).get("exists"))
        except Exception:                                   # noqa: BLE001 - the farm treats "unknown" as absent
            return False

    def delete(self, key: str) -> None:
        self._op("delete", key)

    def list(self, prefix: str = "") -> list:
        out = []
        for o in self._op("list", prefix).get("items") or []:
            stamp = datetime.fromtimestamp(float(o.get("modified") or 0), timezone.utc)
            out.append({"key": o["key"], "size": int(o.get("size") or 0),
                        "modified": stamp.strftime("%Y-%m-%dT%H:%M:%S.000Z")})
        return out


_TYPES = {".mp4": "video/mp4", ".mov": "video/quicktime", ".png": "image/png", ".jpg": "image/jpeg",
          ".jpeg": "image/jpeg", ".json": "application/json", ".txt": "text/plain; charset=utf-8",
          ".srt": "text/plain; charset=utf-8", ".md": "text/markdown; charset=utf-8",
          ".log": "text/plain; charset=utf-8", ".zip": "application/zip"}


def _guess_type(name: str) -> str:
    return _TYPES.get(os.path.splitext(name)[1].lower(), "application/octet-stream")


# ── this computer's side ──────────────────────────────────────────────────────────────────────────────────────

def _connection(home: Path = HOME) -> tuple[str, str]:
    try:
        cfg = json.loads((home / ".cr-connection.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", ""
    key = str(cfg.get("key") or "")
    base = str(cfg.get("url") or "https://channelrecipe.com").rstrip("/")
    if not key.startswith("cr_mcp_") or not (base.startswith("https://") or "://127.0.0.1" in base
                                             or "://localhost" in base):
        return "", ""
    return base, key


def byo(home: Path = HOME) -> bool:
    """Has the creator added their own Modal account? Then Studio Cloud renders there."""
    try:
        import cr_cloud_byo
        return cr_cloud_byo.configured(home)
    except Exception:                                       # noqa: BLE001 - an incomplete install: the old route
        return False


def status(force: bool = False, home: Path = HOME) -> dict:
    """Is Studio Cloud open to this account, and what is its balance? Asked at most once a minute."""
    if byo(home):
        import cr_cloud_byo
        return cr_cloud_byo.status(home)
    with _STATUS_LOCK:
        if not force and _STATUS["data"] is not None and time.time() - _STATUS["at"] < 60:
            return _STATUS["data"]
    base, key = _connection(home)
    data = {"available": False, "balance_usd": 0.0, "why": "Connect your ChannelRecipe account in Setup."}
    if base:
        try:
            r = requests.get(base + "/api/studio/cloud/status", headers={"Authorization": "Bearer " + key},
                             timeout=15)
            data = r.json() if r.ok else {**data, "why": "Studio Cloud isn't available on this account."}
        except (requests.RequestException, ValueError):
            data = {**data, "why": "channelrecipe.com didn't answer."}
    if not data.get("available"):
        # Studio Cloud for creators is their own Modal account: say where to add it (ChannelRecipe's own machines
        # stay with ChannelRecipe's team)
        data = {**data, "byo": "modal", "own_account": True, "setup_url": "/cr-setup#cloud-render",
                "why": "Add your own Modal account in Setup (Render in the cloud) to render off this computer."}
    with _STATUS_LOCK:
        _STATUS.update(at=time.time(), data=data)
    return data


def active(home: Path = HOME) -> bool:
    return bool(status(home=home).get("available"))


def studio_bucket(home: Path = HOME):
    if byo(home):
        import cr_cloud_byo
        return cr_cloud_byo.bucket(home)
    base, key = _connection(home)
    return BrokerBucket(base + "/api/studio/cloud", "Bearer " + key)


_THREED = []          # the engine's own "does this job make 3D scenes" (app._threed_on), saved before it is wrapped
_LOCAL = threading.local()


def _look(style: str) -> dict:
    styles = getattr(getattr(_STUDIO[0], "styles", None), "STYLES", None) if _STUDIO else None
    info = styles.get(style) if isinstance(styles, dict) else None
    look = (info or {}).get("look") if isinstance(info, dict) else None
    return look if isinstance(look, dict) else {}


def wants_gpu(spec: dict) -> bool:
    """A job that renders 3D scenes needs the machine with a graphics card (three.js in Chromium): the 3D engine's
    scenes, or a channel's own 3D renderer written in code (cr_animated3d)."""
    try:
        look = _look(str(spec.get("style") or ""))
        if look.get("visuals") == "cr_animated3d" or ((look.get("animated2d") or {}).get("dims") == 3):
            return True
        return bool(_THREED and _THREED[0](str(spec.get("style") or ""), spec.get("features")))
    except Exception:                                       # noqa: BLE001 - when in doubt, the CPU machine
        return False


_SECRET_NAME = re.compile(r"key|token|secret|password|proof", re.I)


def studio_settings(home: Path = HOME) -> dict:
    """This Studio's provider choices (voice, pictures, motion, media hub), for the machine to make the video the way
    this Studio would. Never the account key or anything named like a secret: the machine's keys come only from the
    creator's render settings."""
    try:
        cfg = json.loads((home / ".cr-connection.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if not _SECRET_NAME.search(str(k))}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    keep = ("media_hub", "images", "narration", "motion", "costs_accepted", "brain_mode", "general_model", "visual_model")
    out = {k: clean(cfg[k]) for k in keep if k in cfg}
    text = json.dumps(out)
    return out if len(text) < 15000 and "cr_mcp_" not in text and "sk-" not in text else {}


class CloudPods:
    """RunPod's API (farm/runpod.py) as far as the farm uses it, answered by channelrecipe.com."""

    def __init__(self, api_key: str = "", session=None, home: Path = HOME):
        base, key = _connection(home)
        self.base = base + "/api/studio/cloud"
        self.headers = {"Authorization": "Bearer " + key}
        self.http = session or requests.Session()
        self.home = home

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        r = self.http.request(method, self.base + path, json=body, headers=self.headers, timeout=120)
        if r.status_code >= 400:
            raise RuntimeError(_detail(r))
        return r.json()

    @staticmethod
    def _pod(job: dict) -> dict:
        return {"id": job["id"], "name": "channelrecipe-" + (job.get("slug") or job["id"]),
                "desiredStatus": "RUNNING" if job.get("status") == "running" else "EXITED",
                "costPerHr": job.get("price_per_hr") or 0, "status": job.get("status"), "job": job}

    def create(self, *, name: str, image: str = "", env: dict, args: str = "", **_machine) -> dict:
        spec = {}
        try:
            spec = json.loads(studio_bucket(self.home).get_bytes(env["FARM_JOB_KEY"]).decode("utf-8"))
        except Exception:                                   # noqa: BLE001 - the quote then assumes ten minutes
            pass
        version = release_version(self.home)
        if version and "/code/overlay-" in env.get("FARM_CODE_URL", ""):
            env = {**env, "CR_STUDIO_VERSION": version, "CR_CODE_KIND": "overlay"}
        settings = studio_settings(self.home)
        if settings:
            env = {**env, "CR_STUDIO_SETTINGS": json.dumps(settings)}
        env = {**env, **video_size(str(spec.get("style") or ""))}
        machine = "gpu" if wants_gpu(spec) else "cpu"
        job = self._call("POST", "/jobs", {"machine": machine, "minutes": float(spec.get("minutes") or 10),
                                           "slug": env.get("FARM_SLUG", ""), "title": env.get("FARM_TITLE", ""),
                                           "env": env})
        Relay.ensure(self.home)
        status(force=True, home=self.home)
        return self._pod(job)

    def get(self, pod_id: str) -> dict:
        return self._pod(self._call("GET", f"/jobs/{pod_id}"))

    def list(self) -> list:
        return [self._pod(j) for j in self._call("GET", "/jobs").get("jobs", []) if j.get("status") == "running"]

    def terminate(self, pod_id: str, tries: int = 3) -> bool:
        for _ in range(max(1, tries)):
            try:
                job = self._call("DELETE", f"/jobs/{pod_id}")
            except (RuntimeError, requests.RequestException):
                time.sleep(2)
                continue
            if job.get("status") in ("cancelled", "failed") and job.get("slug"):
                self._final_status(job)
            return job.get("status") != "running"
        return False

    def _final_status(self, job: dict) -> None:
        """A machine that was stopped never writes its last status, and the farm's loop waits for one (up to its
        four-hour watchdog, holding the Studio busy). Write it, as the machine would have."""
        try:
            studio_bucket(self.home).put_bytes(
                f"status/{job['slug']}.json",
                json.dumps({"slug": job["slug"], "title": job.get("title") or "", "state": "failed",
                            "error": job.get("error") or "stopped", "updated": time.time()}).encode(),
                "application/json")
        except Exception:                                   # noqa: BLE001 - the farm's own timeout still ends it
            pass

    def gpus(self) -> list:
        return []

    def cpus(self) -> list:
        return []


_STUDIO = []


def video_size(style: str) -> dict:
    """VIDEO_W/VIDEO_H for a style that makes vertical Shorts, as this computer's worker gets them
    (cr_video_cancel.video_size): without them the machine rendered a 9:16 style at the engine's 1920x1080."""
    return {"VIDEO_W": "1080", "VIDEO_H": "1920"} if _look(style).get("aspect") == "9:16" else {}


def _farm_running() -> bool:
    farm = getattr(_STUDIO[0], "FARM", {}) if _STUDIO else {}
    thread = farm.get("thread") if isinstance(farm, dict) else None
    return bool(thread is not None and thread.is_alive())


class Relay:
    """Answers the Claude questions a Studio Cloud machine asks, with this computer's Claude Code."""
    _thread = None
    _lock = threading.Lock()
    WORKERS = 4

    @classmethod
    def ensure(cls, home: Path = HOME):
        with cls._lock:
            if cls._thread is None or not cls._thread.is_alive():
                cls._thread = threading.Thread(target=cls._loop, args=(home,), daemon=True, name="cr-cloud-relay")
                cls._thread.start()

    @classmethod
    def _loop(cls, home: Path):
        idle_since = time.time()
        busy = threading.Semaphore(cls.WORKERS)
        while time.time() - idle_since < 20 * 60 or _farm_running():   # while a machine may still ask
            base, key = _connection(home)
            if not base:
                return
            busy.acquire()
            try:
                r = requests.get(base + "/api/studio/cloud/relay/next", params={"wait": 20},
                                 headers={"Authorization": "Bearer " + key}, timeout=40)
                q = r.json().get("question") if r.ok else None
            except (requests.RequestException, ValueError):
                q = None
                time.sleep(5)
            if not q:
                busy.release()
                continue
            idle_since = time.time()

            def work(q=q):
                try:
                    try:
                        text, error = answer(q["request"]), ""
                    except BaseException as e:              # noqa: BLE001 - the engine exits on Claude errors
                        text, error = "", f"{type(e).__name__}: {str(e)[:200]}"
                    for _ in range(3):
                        try:
                            requests.post(f"{base}/api/studio/cloud/relay/{q['id']}/answer",
                                          json={"text": text, "error": error},
                                          headers={"Authorization": "Bearer " + key}, timeout=60)
                            break
                        except requests.RequestException:
                            time.sleep(3)
                finally:
                    busy.release()
            threading.Thread(target=work, daemon=True).start()


def _prompt_parts(request: dict, folder: Path) -> tuple[str, list]:
    """An Anthropic Messages request as one prompt and its pictures, for Claude Code."""
    texts, images = [], []
    system = request.get("system")
    if isinstance(system, list):
        system = "\n\n".join(b.get("text", "") for b in system if isinstance(b, dict))
    if system:
        texts.append(str(system))
    for m in request.get("messages") or []:
        content = m.get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                texts.append(b.get("text", "") if m.get("role") != "assistant"
                             else "(your earlier reply)\n" + b.get("text", ""))
            elif b.get("type") == "image" and (b.get("source") or {}).get("type") == "base64":
                src = b["source"]
                ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
                       "image/webp": ".webp"}.get(src.get("media_type"), ".png")
                p = folder / f"image-{len(images) + 1}{ext}"
                p.write_bytes(base64.b64decode(src.get("data") or ""))
                images.append(str(p))
    return "\n\n".join(t for t in texts if t), images


RELAYED_CC_TIMEOUT = 1500        # seconds: what the 3D engine allows its own Claude Code calls (threed._cc, 900 + 600)


def answer(request: dict) -> str:
    if request.get("kind") == "ytdlp":
        return json.dumps(run_ytdlp(request))
    import make_video as mv
    # The machine's 3D director and reviews reach this computer as plain requests, so they ran under the engine's
    # 15-minute Claude Code limit instead of the 25 minutes threed gives its own calls at home: a 1-minute 3D video's
    # Opus director timed out at 900 s on the first own-account GPU test (2026-10-29), and the failed stage stopped
    # every Claude step after it. Relayed calls get the 3D engine's own limit.
    if int(getattr(mv, "CC_TIMEOUT", 900) or 900) < RELAYED_CC_TIMEOUT:
        mv.CC_TIMEOUT = RELAYED_CC_TIMEOUT
    with tempfile.TemporaryDirectory(prefix="cr-cloud-") as tmp:
        prompt, images = _prompt_parts(request, Path(tmp))
        if images:
            return mv._claude_code_vision(prompt, images)
        model = str(request.get("model") or getattr(mv, "SCRIPT_MODEL", ""))
        return mv.claude(prompt, model, int(request.get("max_tokens") or 4000), provider="claudecode")


# ── wiring the farm to Studio Cloud ───────────────────────────────────────────────────────────────────────────

OVERLAY_SKIP_TOP = {"shorts", "models"}      # the Shorts pack and downloaded models: fetched again where needed
LEARNED = ("assets/styles/learned/", "learned/")
VIDEO_FILES = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi")


def ship_learned(skip):
    """The farm's skip with learned styles' own files let through. farm._skip leaves out every folder called
    "learned" (meant for the style copier's reference videos), which also left behind each learned style's files: a
    coded clone's renderer (renderer-vN.js), a picture clone's style and character pictures, a studied graphics kit.
    The machine then stopped on "renderer is missing" after the voice and pictures were paid for, or drew a clone
    without its look. Those go now; videos never do, and the farm's other rules still apply to them."""
    def keep(rel: str) -> bool:
        if rel.startswith(LEARNED) and not rel.lower().endswith(VIDEO_FILES):
            return skip("/".join("styles-learned" if part == "learned" else part for part in rel.split("/")))
        return skip(rel)
    return keep


def release_version(home: Path = HOME) -> str:
    try:
        return str(json.loads((home / "cr-release.json").read_text(encoding="utf-8")).get("version") or "")
    except (OSError, ValueError):
        return ""


def overlay_files(home: Path, skip) -> list:
    """This Studio's own files: everything the farm would send that is not exactly as the release shipped it."""
    try:
        shipped = json.loads((home / "cr-files.json").read_text(encoding="utf-8")).get("files") or {}
    except (OSError, ValueError):
        shipped = {}
    out = []
    for p in sorted(home.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(home).as_posix()
        top = rel.split("/")[0]
        if skip(rel) or top.startswith(".") or top in OVERLAY_SKIP_TOP:
            continue
        want = shipped.get(rel)
        if want and want == hashlib.sha256(p.read_bytes()).hexdigest():
            continue
        out.append((rel, p))
    return out


def overlay_bundle(bucket, home: Path = HOME, skip=None, sink=None) -> str:
    """The farm's code bundle for Studio Cloud: only this Studio's own files. The machine starts from the official
    release of the same version, which ChannelRecipe already has, so nothing it already has is uploaded."""
    say = sink or print
    files = overlay_files(home, skip or (lambda rel: False))
    digest = hashlib.sha256()
    for rel, p in files:
        digest.update(rel.encode())
        digest.update(hashlib.sha256(p.read_bytes()).digest())
    key = f"code/overlay-{release_version(home) or 'unknown'}-{digest.hexdigest()[:12]}.zip"
    if bucket.exists(key):
        say(f"  code: Studio {release_version(home)} plus your own files, already uploaded")
        return key
    tmp = home / ".farm-overlay.zip"
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for rel, p in files:
                z.write(p, rel)
        say(f"  code: Studio {release_version(home)} comes from ChannelRecipe; uploading only your own "
            f"{len(files)} file(s) ({tmp.stat().st_size / 1024 ** 2:.1f} MB)")
        bucket.put_file(key, tmp, "application/zip")
    finally:
        tmp.unlink(missing_ok=True)
    return key


def private_file(rel: str) -> bool:
    """This Studio's own account and settings files at its top (.cr-connection.json holds the ChannelRecipe account
    key, .mcp.json its MCP key, .env every provider key and the Modal token): never in a code bundle. The farm's
    own rule leaves out only .env."""
    return rel.split("/")[0].startswith(".")


def whole_bundle(ff, original_bundle, bucket, learned, quiet: bool = False, sink=None) -> str:
    """The farm's whole-code bundle for the creator's own Modal storage (a Studio that isn't on the published
    version), without this Studio's account files and with learned styles' own files (ship_learned)."""
    original_skip = ff._skip
    ff._skip = lambda rel: private_file(rel) or learned(rel)
    try:
        return original_bundle(bucket, quiet=quiet, sink=sink)
    finally:
        ff._skip = original_skip


def checklist(original, home: Path = HOME) -> list:
    st = status(home=home)
    voice = next((c for c in original if c.get("key") == "render"), None)
    if st.get("byo"):
        return [{"key": "cloud", "label": "Your own Modal account (Studio Cloud)", "ok": bool(st.get("available")),
                 "env": "", "how": st.get("why") or "Billed by Modal to your account."},
                {"key": "balance", "label": "Billed by Modal to your account", "ok": True, "env": "",
                 "how": "No ChannelRecipe balance is used."}] + ([voice] if voice else [])
    balance = float(st.get("balance_usd") or 0)
    voice = next((c for c in original if c.get("key") == "render"), None)
    items = [
        {"key": "cloud", "label": "Studio Cloud on your ChannelRecipe account", "ok": bool(st.get("available")),
         "env": "", "how": st.get("why") or "Included with Studio."},
        {"key": "balance", "label": f"Studio Cloud balance (${balance:.2f})", "ok": balance >= 0.5, "env": "",
         "how": "Top up at channelrecipe.com, Billing, in $5 to $50 chunks. A render reserves its most before it "
                "starts and you pay only the machine time it used."},
    ]
    return items + ([voice] if voice else [])


def install(home: Path = HOME) -> bool:
    """Point the farm at Studio Cloud whenever this account has it. Safe to call more than once."""
    if _INSTALLED:
        return True
    try:
        import farm.config as fc
        import farm.runpod as frp
        import farm.farm as ff
    except Exception:                                       # noqa: BLE001 - a release without the farm
        return False
    original_init, original_checklist = fc.Config.__init__, fc.Config.checklist
    original_missing, original_bucket, original_runpod = fc.Config.missing_for_mac, fc.bucket_from, frp.RunPod

    def init(self):
        original_init(self)
        self.studio_cloud = not self.runpod_key and active(home)
        if self.studio_cloud:
            self.runpod_key = self.bucket_key_id = self.bucket_secret = SENTINEL
            self.bucket_endpoint, self.bucket = "https://channelrecipe.com", "Studio Cloud"
            self.gpu, self.cloud, self.max_pods, self.budget_usd = "Studio Cloud", "ChannelRecipe", 3, 10000.0

    def check(self):
        return checklist(original_checklist(self), home) if getattr(self, "studio_cloud", False) \
            else original_checklist(self)

    def missing(self):
        if not getattr(self, "studio_cloud", False):
            return original_missing(self)
        return [c["label"] for c in checklist(original_checklist(self), home)[:2] if not c["ok"]]

    def bucket_from(cfg):
        return studio_bucket(home) if getattr(cfg, "studio_cloud", False) else original_bucket(cfg)

    def runpod(api_key: str = "", session=None):
        if api_key != SENTINEL:
            return original_runpod(api_key, session)
        if byo(home):
            import cr_cloud_byo
            return cr_cloud_byo.ModalPods(api_key, session, home)
        return CloudPods(api_key, session, home)

    original_bundle = getattr(ff, "bundle", None)

    def bundle(bucket, quiet: bool = False, sink=None):
        learned = ship_learned(getattr(ff, "_skip", None) or (lambda rel: False))
        if isinstance(bucket, BrokerBucket) and release_version(home) and (home / "cr-files.json").is_file():
            return overlay_bundle(bucket, home, learned, None if quiet else sink)
        if type(bucket).__name__ == "VolumeBucket":
            # the creator's own Modal storage: the official release (kept there once per version) plus only this
            # Studio's own files, when this Studio is on the published version; else this Studio's whole code
            import cr_cloud_byo
            version = release_version(home)
            if version and (home / "cr-files.json").is_file() and (
                    cr_cloud_byo.release_cached(bucket, version) or cr_cloud_byo.release_link(home)):
                key = overlay_bundle(bucket, home, learned, None if quiet else sink)
            else:
                key = whole_bundle(ff, original_bundle, bucket, learned, quiet=quiet, sink=sink)
            threading.Thread(target=cr_cloud_byo.tidy, args=(bucket, key, version), daemon=True).start()
            return key
        return original_bundle(bucket, quiet=quiet, sink=sink)

    fc.Config.__init__, fc.Config.checklist, fc.Config.missing_for_mac = init, check, missing
    fc.bucket_from = ff.bucket_from = bucket_from
    frp.RunPod = runpod
    if original_bundle is not None:
        ff.bundle = bundle
    _INSTALLED.append(True)
    return True


# ── the workspace's "Studio Cloud" choice ─────────────────────────────────────────────────────────────────────

TASK = {"state": "idle", "message": "", "title": "", "error": "", "at": 0.0}
MAX_TITLES = 5                  # prepared here one after another, then rendered side by side (the farm's max_pods is 3)
FARM_FIELDS = ("title", "style", "minutes", "script", "extra", "burn_subs", "force", "thumb_count", "features",
               "character", "avatar_id", "avatar_s", "avatar_count", "auto_style",
               "cr_captions")    # sent along; the farm's own spec builder keeps only the fields it knows (Create says so)


def blocked(p: dict) -> str:
    """Why this video can't render on Studio Cloud yet ('' when it can). The engine's farm checks again."""
    style, features = str(p.get("style") or ""), p.get("features") or {}
    # Cloned styles and ChannelRecipe's recipes render on the machine (tested 2026-10-22: a drawn clone and Stick
    # Figure Stories came out as they do here). Shorts need the Shorts pack and their own pipeline; Manga Recaps and
    # panels use files uploaded on this computer.
    if style.startswith("cr-shorts"):
        return "Shorts render on this computer for now."
    if style == "cr-manga-recaps" or p.get("panels"):
        return "Recaps made from your own panels render on this computer."
    if style == "folio":
        return "FOLIO renders on this computer: every shot is drawn by your Claude Code."
    if p.get("byo_audio"):
        return "Your own recording renders on this computer."
    if p.get("character_image"):
        # the machine's queue route drops a picked picture; words alone (and a sheet drawn from them) prepare here
        return "A character picture of your own renders on this computer for now."
    if (p.get("render_backend") or "original") != "original":
        return "Fast mode renders on this computer."
    if p.get("language"):
        # the machine's queue route makes the video in its style's own language: another one is made here for now
        import cr_language
        row = cr_language.find(p.get("language"))
        try:
            info = json.loads((HOME / "styles" / f"{style}.json").read_text(encoding="utf-8")) \
                if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", style) else {}
        except (OSError, ValueError):
            info = {}
        if row and row["id"] != cr_language.style_language(info):
            return f"A video in {row['name']} renders on this computer for now."
    return ""


CHUNKS_USD = (5, 10, 25, 50)    # the server's top-up chunks (webapp/cloud_wallet.py), when its status doesn't list them


def quote(p: dict, home: Path = HOME, http=None) -> dict:
    """What these videos would cost on Studio Cloud, against this account's balance, before anything starts.

    The owner (2026-10-28): a creator tops up before their first cloud video, and a cloud choice never fails half-way.
    Each video reserves its most when its machine starts and settles at most that, so a balance that covers every
    title's most covers the whole batch, whatever order the machines start and end in. `short_by_usd` is what's
    missing, and `topup_usd` the smallest top-up chunk that covers it."""
    if byo(home):
        import cr_cloud_byo
        return cr_cloud_byo.quote(p, home)
    info = status(force=True, home=home)
    out = {"available": bool(info.get("available")), "why": info.get("why") or "",
           "balance_usd": round(float(info.get("balance_usd") or 0), 2), "topup_url": info.get("topup_url") or "",
           "chunks_usd": list(info.get("chunks_usd") or CHUNKS_USD)}
    if not out["available"]:
        out["why"] = out["why"] or "Studio Cloud isn't available on this account."
        return out
    why = blocked(p)
    if why:
        return {**out, "available": False, "why": why, "blocked": True}
    titles = max(1, len([t for t in str(p.get("title") or "").split("\n") if t.strip()]) or int(p.get("titles") or 1))
    minutes = max(1, min(int(float(p.get("minutes") or 10)), 60))
    machine = "gpu" if wants_gpu({"style": p.get("style"), "features": p.get("features")}) else "cpu"
    base, key = _connection(home)
    try:
        r = (http or requests).post(base + "/api/studio/cloud/quote", json={"minutes": minutes, "machine": machine},
                                    headers={"Authorization": "Bearer " + key}, timeout=20)
        each = r.json() if r.ok else None
    except (requests.RequestException, ValueError):
        each = None
    if not isinstance(each, dict) or "max_cents" not in each:
        return {**out, "available": False, "why": "channelrecipe.com couldn't price this right now. Try again shortly."}
    most = round(each["max_cents"] * titles / 100, 2)
    short = round(max(0.0, most - out["balance_usd"]), 2)
    chunk = next((c for c in sorted(out["chunks_usd"]) if c >= short), max(out["chunks_usd"])) if short else 0
    return {**out, **each, "titles": titles, "minutes": minutes,
            "expected_usd": round(each["expected_cents"] * titles / 100, 2), "most_reserved_usd": most,
            "short_by_usd": short, "topup_usd": chunk}


def short_message(q: dict) -> str:
    """The words for a batch the balance can't cover (nothing was started or spent)."""
    videos = f"{q['titles']} videos reserve" if q.get("titles", 1) > 1 else "This video reserves"
    return (f"{videos} up to ${q['most_reserved_usd']:.2f} of machine time (usually about ${q['expected_usd']:.2f}), "
            f"and your Studio Cloud balance is ${q['balance_usd']:.2f}. Top up first (${q['topup_usd']} covers it); "
            "nothing has started and nothing was spent.")


def topup_link(usd: int, home: Path = HOME, http=None) -> str:
    """A Stripe Checkout page for one top-up chunk of this account's cloud balance, made by channelrecipe.com with
    this Studio's account connection, so the creator needn't sign in on the website first."""
    base, key = _connection(home)
    if not base:
        raise RuntimeError("Connect your ChannelRecipe account in Setup first.")
    try:
        r = (http or requests).post(base + "/api/studio/cloud/topup", json={"usd": int(usd)},
                                    headers={"Authorization": "Bearer " + key}, timeout=30)
    except requests.RequestException:
        raise RuntimeError("channelrecipe.com didn't answer. Try again shortly.")
    if not r.ok:
        raise RuntimeError(_detail(r) or "The top-up page couldn't be opened.")
    url = str((r.json() or {}).get("url") or "")
    if not url.startswith("https://"):
        raise RuntimeError("The top-up page couldn't be opened.")
    with _STATUS_LOCK:
        _STATUS.update(at=0.0)          # the balance is read again on the next look
    return url


RUN_SPEC = "cr_run_spec"         # farm_payload's key for this video's run_spec choices (written beside the job, not queued)


def run_spec_choices(p: dict) -> dict:
    """This video's choices that Studio's adjustments read from its run_spec.json, as cr_submission keeps them for a
    video made here: call to action, pictures a minute, the 3D look and the quicker 3D review, captions on or off,
    the frame check, the YouTube description and the product to sell. The farm's spec has no place for them, so a cloud
    video quietly lost every one. Raises ValueError for a choice Create would refuse."""
    import cr_cta
    import cr_threed_look
    out = {k: p[k] for k in ("style", "minutes", "features", "burn_subs", "character", "extra") if k in p}
    cta, look = cr_cta.clean(p.get("cta")), cr_threed_look.clean(p.get("threed_look"))
    if cta:
        out["cr_cta"] = cta
    if look:
        out["cr_threed_look"] = look
    ppm = p.get("pictures_per_min")
    if ppm not in (None, "", 0):
        import cr_media
        allowed = cr_media.picture_range((getattr(getattr(_STUDIO[0], "styles", None), "STYLES", {}) or {})
                                         .get(p.get("style")) or {}) if _STUDIO else None
        try:
            ppm = float(ppm)
        except (TypeError, ValueError):
            ppm = None
        if not allowed or ppm is None or not allowed[0] <= ppm <= allowed[2]:
            raise ValueError(f"Choose between {allowed[0]:g} and {allowed[2]:g} pictures a minute." if allowed
                             else "This recipe sets its own pictures.")
        out["cr_pictures_per_min"] = ppm
    if p.get("burn_subs") is False:
        out["cr_captions"] = "off"
    elif p.get("cr_captions") == "on":
        out["cr_captions"] = "on"
    if p.get("threed_quick") is True:
        out["cr_threed_quick"] = True
    if isinstance(p.get("frame_check"), bool):
        out["cr_frame_check"] = "on" if p["frame_check"] else "off"
    if p.get("desc"):
        def count(key, low, high):
            try:
                return max(low, min(high, int(p.get(key) or 0)))
            except (TypeError, ValueError):
                return low
        out["meta"] = {"chapters": count("chapters", 0, 20), "sources": count("sources", 0, 10)}
    if p.get("offer"):
        out["offer"], out["offer_plugs"] = str(p["offer"])[:60], p.get("offer_plugs") or 0
    return out


def farm_payload(p: dict) -> dict:
    out = {k: p[k] for k in FARM_FIELDS if k in p}
    out["thumb"] = "thumbnail" in (p.get("steps") or [])
    out[RUN_SPEC] = run_spec_choices(p)
    return out


def write_run_specs(engine, body: dict) -> None:
    """Each title's run_spec.json in its job folder before the farm prepares it: read here while the script, voice and
    pictures are made, and packed with them for the machine (farm/assets.py sends the job folder's small files)."""
    choices = body.get(RUN_SPEC)
    if not isinstance(choices, dict) or engine is None:
        return
    for title in [t.strip() for t in str(body.get("title") or "").splitlines() if t.strip()]:
        try:
            folder = Path(engine.job_dir(title, str(body.get("style") or "")))
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "run_spec.json").write_text(json.dumps(choices, indent=1, default=str), encoding="utf-8")
        except Exception:                                   # noqa: BLE001 - the video still renders, as before
            pass


BUSY = "Finish the current studio request"


def _post(client, path: str, body: dict, base: dict, tries: int = 20):
    """One of the studio's own routes. Its work lock is also taken for a moment by the page's own refreshes, so a
    "busy" answer is waited out for up to a minute instead of failing the video."""
    for attempt in range(tries):
        r = client.post(path, json=body, **base)
        data = r.get_json(silent=True) or {}
        if r.status_code == 409 and BUSY in str(data.get("error") or "") and attempt < tries - 1:
            time.sleep(3)
            continue
        return r, data
    return r, data


def start(client, token: str, p: dict) -> None:
    """Prepare the video here (the farm's queue: script, voice, stills, plan) and then start its machine, in the
    background, through the studio's own routes so every check and receipt applies."""
    headers = {"X-CR-Local-Token": token}
    base = {"base_url": "http://127.0.0.1:7860", "headers": headers}
    titles = [x.strip() for x in str(p.get("title") or "").split("\n") if x.strip()]
    title = (titles[0][:120] if titles else "") + (f" and {len(titles) - 1} more" if len(titles) > 1 else "")
    own = byo()
    where = "your Modal machine" if own else "a ChannelRecipe machine"
    TASK.update(state="preparing", title=title, error="", at=time.time(),
                message="Writing the script and making the voice and pictures here with your Claude Code…")
    try:
        if own:                                             # before anything is paid for: Modal must be ready
            import cr_cloud_byo
            cr_cloud_byo.ensure_deployed(HOME, sink=lambda line: TASK.update(message=line))
            TASK.update(message="Writing the script and making the voice and pictures here with your Claude Code…")
        r, data = _post(client, "/api/farm/queue", farm_payload(p), base)
        if r.status_code >= 400 or not data.get("queued"):
            raise RuntimeError(data.get("error") or "; ".join(data.get("problems") or []) or "It couldn't be prepared.")
        TASK.update(state="starting", message=f"Prepared here. Starting {where}…")
        r, data = _post(client, "/api/farm/run", {}, base)
        if r.status_code >= 400:
            raise RuntimeError(data.get("error") or "The cloud machine couldn't start.")
        TASK.update(state="rendering", message=f"Rendering on {where}. Keep Studio open: it answers the "
                                               "machine's Claude questions with your Claude Code.")
    except BaseException as e:                              # noqa: BLE001 - the engine exits on a failed Claude step
        why = str(e) if not isinstance(e, SystemExit) else f"a step stopped ({e})" if str(e) else "a step stopped"
        TASK.update(state="failed", error=f"Preparing it here stopped: {why}"[:400], message="")
        import cr_failure                                   # why, privacy-safe, for ChannelRecipe's counts

        def note(line):
            if TASK.get("state") == "failed" and TASK.get("title") == title:
                TASK["error"] = (str(TASK.get("error") or "") + " " + line).strip()
        cr_failure.report_stop(e, source="cloud", recipe=str(p.get("style") or ""), titles=titles, default_step="cloud",
                               on_note=note)


def install_routes(studio, token: str, home: Path = HOME) -> None:
    from flask import jsonify, request

    app = studio.app
    _STUDIO[:] = [studio]
    original_threed = getattr(studio, "_threed_on", None)
    if original_threed is not None and not _THREED:
        _THREED.append(original_threed)

        def threed_on(style, asked=None):
            # inside a Studio Cloud farm queue the job renders on the GPU machine: not a reason to refuse it
            if getattr(_LOCAL, "cloud_queue", False):
                return False
            return original_threed(style, asked)
        studio._threed_on = threed_on
        queue_view = app.view_functions.get(next((r.endpoint for r in app.url_map.iter_rules()
                                                  if r.rule == "/api/farm/queue"), ""), None)
        if queue_view is not None:
            endpoint = next(r.endpoint for r in app.url_map.iter_rules() if r.rule == "/api/farm/queue")

            def farm_queue(*args, **kwargs):
                _LOCAL.cloud_queue = active(home)
                engine = getattr(studio, "mv", None)
                body = request.get_json(silent=True) or {}
                choices = body.get(RUN_SPEC) if _LOCAL.cloud_queue else None
                offer = isinstance(choices, dict) and choices.get("offer") and hasattr(engine, "set_offer")
                try:
                    if _LOCAL.cloud_queue:
                        write_run_specs(engine, body)
                    if offer:                               # the script is written here: it sells this product
                        engine.set_offer(choices["offer"], choices.get("offer_plugs", 0))
                    return queue_view(*args, **kwargs)
                finally:
                    _LOCAL.cloud_queue = False
                    if offer:
                        engine.set_offer("", 0)
            app.view_functions[endpoint] = farm_queue

    @app.post("/cr-cloud/start")
    def cr_cloud_start():
        if request.headers.get("X-CR-Local-Token") != token:
            return jsonify(error="Refresh your studio page and try again."), 403
        if not active(home):
            return jsonify(error=status(home=home).get("why") or "Studio Cloud isn't available."), 409
        if TASK["state"] in ("preparing", "starting"):
            return jsonify(error="A cloud video is still being prepared. Start the next one when it's on its way."), 409
        p = request.get_json(silent=True) or {}
        why = blocked(p)
        if why:
            return jsonify(error=why), 400
        try:
            run_spec_choices(p)                             # a choice Create would refuse stops here, before any work
        except ValueError as e:
            return jsonify(error=str(e)), 400
        titles = [x for x in str(p.get("title") or "").split("\n") if x.strip()]
        if len(titles) > MAX_TITLES:
            return jsonify(error=f"Studio Cloud takes up to {MAX_TITLES} titles at once; they render side by side."), 400
        # Top up first: nothing is written, voiced or drawn here for a batch whose machines the balance can't start.
        q = quote(p, home)
        if not q.get("available"):
            return jsonify(error=q.get("why") or "Studio Cloud isn't available.", quote=q), 409
        if q.get("short_by_usd"):
            return jsonify(error=short_message(q), quote=q, top_up=True), 402
        threading.Thread(target=start, args=(app.test_client(), token, p), daemon=True, name="cr-cloud-start").start()
        return jsonify(started=True), 202

    @app.post("/cr-cloud/quote")
    def cr_cloud_quote():
        """Create's cloud choice: these videos' expected cost and most, against the balance, before anything starts."""
        if request.headers.get("X-CR-Local-Token") != token:
            return jsonify(error="Refresh your studio page and try again."), 403
        return jsonify(quote(request.get_json(silent=True) or {}, home))

    @app.post("/cr-cloud/topup")
    def cr_cloud_topup():
        """A top-up chunk's checkout page, for the page to open in the creator's browser."""
        if request.headers.get("X-CR-Local-Token") != token:
            return jsonify(error="Refresh your studio page and try again."), 403
        try:
            usd = int((request.get_json(silent=True) or {}).get("usd") or 0)
        except (TypeError, ValueError):
            usd = 0
        if byo(home):
            return jsonify(error="Studio Cloud runs on your own Modal account: Modal bills it, nothing to top up."), 400
        chunks = status(home=home).get("chunks_usd") or CHUNKS_USD
        if usd not in chunks:
            return jsonify(error=f"Choose a top-up of ${', $'.join(str(c) for c in chunks)}."), 400
        try:
            return jsonify(url=topup_link(usd, home))
        except RuntimeError as e:
            return jsonify(error=str(e)), 502

    @app.get("/cr-cloud/progress")
    def cr_cloud_progress():
        out = {**TASK, "balance_usd": status(home=home).get("balance_usd"), "lines": [], "farm": "", "videos": []}
        client = app.test_client()
        try:
            farm = client.get("/api/farm", base_url="http://127.0.0.1:7860").get_json(silent=True) or {}
            out["lines"], out["farm"] = (farm.get("lines") or [])[-30:], farm.get("state") or ""
            if TASK["state"] == "rendering" and farm.get("state") in ("done", "failed", "idle"):
                TASK.update(state="done" if farm.get("state") == "done" else "failed",
                            message="" if farm.get("state") == "done" else "The cloud render stopped. See the log.")
            lib = client.get("/api/farm/library", base_url="http://127.0.0.1:7860").get_json(silent=True) or {}
            for row in lib.get("videos") or []:
                files = [f.get("name") for f in row.get("files") or []]
                video = next((n for n in files if str(n).lower().endswith((".mp4", ".mov"))), "")
                slug = str(row.get("slug") or "")
                out["videos"].append({"slug": slug, "title": row.get("title") or slug.replace("-", " ").capitalize(),
                                      "video": video, "bytes": row.get("bytes")})
        except Exception:                                   # noqa: BLE001 - progress is best-effort
            pass
        return jsonify(out)


# ── the machine's side ────────────────────────────────────────────────────────────────────────────────────────

def _relay_claude() -> None:
    """Claude through the job's relay, switched on inside the engine only. Left in the environment, the relay's
    OpenLux settings would also make the engine pick OpenLux as its video watcher, which the relay is not."""
    url, key = os.environ.pop("OPENLUX_BASE_URL", ""), os.environ.pop("OPENLUX_API_KEY", "")
    os.environ.pop("CLAUDE_RELAY", None)
    import importlib.abc
    import importlib.util

    class Hook(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path, target=None):
            if name != "make_video":
                return None
            sys.meta_path.remove(self)
            spec = importlib.util.find_spec(name)
            if spec is None or spec.loader is None:
                return spec
            run = spec.loader.exec_module

            def exec_module(module):
                run(module)
                _studio_adjustments(module)
                _from_run_spec(module)
                original = module._claude_endpoint

                def endpoint(provider):
                    if url and key and provider in ("anthropic", "openlux"):
                        return (url.rstrip("/") + "/v1/messages",
                                {"x-api-key": key, "anthropic-version": "2023-06-01",
                                 "content-type": "application/json"})
                    return original(provider)
                module._claude_endpoint = endpoint
            spec.loader.exec_module = exec_module
            return spec
    sys.meta_path.insert(0, Hook())


ENGINE_ADJUSTMENTS = ("cr_media", "cr_render", "cr_style_delivery", "cr_shorts")


def _from_run_spec(engine) -> None:
    """On the machine: what this computer's Studio passes the engine from a video's run_spec (app._run_spec) and the farm's
    runner doesn't: the YouTube description with its chapters and sources, and the product this video sells."""
    original = getattr(engine, "run_custom", None)
    if original is None:
        return

    def run_custom(*args, **kwargs):
        title = kwargs.get("title") if "title" in kwargs else (args[0] if args else "")
        try:
            spec = json.loads((Path(engine.job_dir(title, kwargs.get("style") or "")) / "run_spec.json")
                              .read_text(encoding="utf-8"))
        except Exception:                                   # noqa: BLE001 - no run_spec: the farm's spec as it is
            spec = {}
        if isinstance(spec, dict):
            if kwargs.get("meta") is None and isinstance(spec.get("meta"), dict):
                kwargs["meta"] = spec["meta"]
            if spec.get("offer") and hasattr(engine, "set_offer"):
                engine.set_offer(spec["offer"], spec.get("offer_plugs", 0))
        return original(*args, **kwargs)
    engine.run_custom = run_custom


def _studio_adjustments(engine) -> None:
    """What Studio does to the engine when it starts (cr_studio.py), on the machine too: Kie's current video
    watcher, the media hub's picture and voice routing, the sound library, the render contracts. Claude is the one
    exception: it is relayed, never the Studio's own Claude Code."""
    for name in ENGINE_ADJUSTMENTS:
        try:
            __import__(name).install(engine)
        except Exception as e:                              # noqa: BLE001 - the plain engine still renders
            print(f"=== studio cloud: {name} not applied ({type(e).__name__}: {str(e)[:120]}) ===", flush=True)


def _write_settings() -> None:
    """The Studio's provider choices, where its adjustments read them (no account key is ever in them)."""
    raw = os.environ.pop("CR_STUDIO_SETTINGS", "")
    try:
        settings = json.loads(raw) if raw else {}
    except ValueError:
        settings = {}
    if isinstance(settings, dict) and settings and not (HOME / ".cr-connection.json").exists():
        (HOME / ".cr-connection.json").write_text(json.dumps(settings), encoding="utf-8")


def _relay_youtube() -> None:
    """YouTube answers datacentre machines with "confirm you're not a bot", so the engine's yt-dlp is pointed at
    `python -m cr_cloud ytdlp`, which hands YouTube downloads to the creator's Studio (their own connection)."""
    shim = Path(tempfile.gettempdir()) / "cr-cloud-ytdlp"
    shim.write_text(f"#!/bin/sh\nPYTHONPATH=\"{HOME}${{PYTHONPATH:+:$PYTHONPATH}}\" exec \"{sys.executable}\" "
                    f"-m cr_cloud ytdlp \"$@\"\n", encoding="utf-8")
    shim.chmod(0o755)
    os.environ["YTDLP_BIN"] = str(shim)


def pod() -> int:
    """Run the farm's own runner with its storage pointed at the job's signed broker."""
    sys.path.insert(0, str(HOME))
    broker, token = os.environ.get("FARM_BROKER_URL", ""), os.environ.get("FARM_JOB_TOKEN", "")
    if not broker or not token:
        print("FARM_BROKER_URL / FARM_JOB_TOKEN are not set: this is not a Studio Cloud machine", file=sys.stderr)
        return 2
    _write_settings()
    _relay_claude()
    _relay_youtube()
    import farm.config as fc
    fc.bucket_from = lambda cfg: BrokerBucket(broker, "Bearer " + token)
    from farm import runner
    return int(runner.main() or 0)


# ── YouTube through the creator's connection ──────────────────────────────────────────────────────────────────

ROOT_MARK = "@@ROOT@@"
_YT_TARGET = re.compile(r"^(https://(www\.|m\.)?(youtube\.com|youtu\.be)/\S+|ytsearch\d{0,3}:.{1,300})$", re.S)
_FLAGS = {"-q", "--no-warnings", "--force-keyframes-at-cuts", "--skip-download", "--write-info-json",
          "--flat-playlist", "-j", "-J", "--force-ipv4"}
_VALUE_FLAGS = {"-f", "--download-sections", "--merge-output-format", "-o", "--load-info-json", "--playlist-end",
                "--print", "--js-runtimes", "--sleep-requests", "--extractor-args"}


def _youtube_job(argv: list, root: Path) -> bool:
    """Does this yt-dlp run need YouTube (search, a YouTube link, or a YouTube video's saved info file)?"""
    for i, a in enumerate(argv):
        if a.startswith("ytsearch") or "youtube.com/" in a or "youtu.be/" in a:
            return True
        if a == "--load-info-json" and i + 1 < len(argv):
            try:
                info = json.loads(Path(argv[i + 1]).read_text(encoding="utf-8"))
                return "youtube" in str(info.get("extractor") or info.get("webpage_url") or "").lower()
            except (OSError, ValueError):
                return False
    return False


def ytdlp_main(argv: list) -> int:
    """What a Studio Cloud machine runs as yt-dlp. Archive.org and other links download here; YouTube goes to
    the creator's Studio with the machine's paths marked, and what it downloaded comes back into the same places."""
    root = Path(os.environ.get("CHANNELRECIPE_HOME") or os.getcwd()).resolve()
    if not _youtube_job(argv, root):
        return subprocess.call([sys.executable, "-m", "yt_dlp"] + argv)
    broker, token = os.environ.get("FARM_BROKER_URL", ""), os.environ.get("FARM_JOB_TOKEN", "")
    local = os.environ.get("CR_LOCAL_RELAY", "")
    if local:                                               # one of the creator's own Modal machines (cr_cloud_byo)
        import cr_cloud_byo
        bucket = cr_cloud_byo.VolumeBucket(cr_cloud_byo.volume())
        relay_url = local + "/relay/ytdlp"
    else:
        bucket = BrokerBucket(broker, "Bearer " + token)
        relay_url = broker.rsplit("/pod", 1)[0] + "/relay/ytdlp"
    prefix = "relay/" + secrets.token_hex(8)
    mapped, inputs = [], []
    for a in argv:
        if a.startswith(str(root) + "/"):
            rel = Path(a).resolve().relative_to(root).as_posix() if Path(a).exists() else a[len(str(root)) + 1:]
            if Path(a).is_file():
                bucket.put_file(f"{prefix}/in/{rel}", Path(a))
                inputs.append(rel)
            mapped.append(f"{ROOT_MARK}/{rel}")
        else:
            mapped.append(a)
    try:
        r = requests.post(relay_url, json={"argv": mapped, "inputs": inputs, "prefix": prefix},
                          headers={"x-api-key": token}, timeout=1900)
        out = json.loads(r.text) if r.ok else {"rc": 1, "stderr": f"Studio Cloud relay: HTTP {r.status_code}"}
    except (requests.RequestException, ValueError) as e:
        out = {"rc": 1, "stderr": f"Studio Cloud relay: {type(e).__name__}"}
    for rel in out.get("files") or []:
        if ".." in rel.split("/"):
            continue
        try:
            bucket.get_file(f"{prefix}/out/{rel}", root / rel)
        except Exception as e:                              # noqa: BLE001 - yt-dlp's caller checks for the file
            out["stderr"] = (out.get("stderr") or "") + f"\nStudio Cloud: could not bring back {rel}: {e}"
    sys.stdout.write(out.get("stdout") or "")
    sys.stderr.write(out.get("stderr") or "")
    return int(out.get("rc") or 0)


def _safe_ytdlp_args(argv: list) -> list:
    """The pod's yt-dlp arguments, if every one is something the engine itself asks for; else ValueError."""
    out, i = [], 0
    while i < len(argv):
        a = str(argv[i])
        if a in _FLAGS:
            out.append(a)
        elif a in _VALUE_FLAGS and i + 1 < len(argv):
            v = str(argv[i + 1])
            if a in ("-o", "--load-info-json"):
                if not v.startswith(ROOT_MARK + "/") or ".." in re.split(r"[\\/]", v):
                    raise ValueError(f"{a} must stay in the job folder")
            elif a == "--js-runtimes" and v not in ("node", "bun", "deno"):
                raise ValueError("unknown JavaScript runtime")
            elif a == "--extractor-args" and not re.fullmatch(r"youtube:player_client=[a-z_,]+", v):
                raise ValueError("unexpected extractor arguments")
            out += [a, v]
            i += 1
        elif _YT_TARGET.match(a):
            out.append(a)
        else:
            raise ValueError(f"not an option the engine uses: {a[:40]}")
        i += 1
    return out


def _inside(folder: Path, rel: str) -> bool:
    """Is this relative path (from the machine) inside the folder on this computer? Never an absolute path, a drive
    or a way out with .. (either slash: Windows takes both)."""
    if not rel or rel.startswith(("/", "\\")) or ":" in rel or ".." in re.split(r"[\\/]", rel):
        return False
    try:
        return (folder / rel).resolve().is_relative_to(folder.resolve())
    except (OSError, ValueError):
        return False


def run_ytdlp(request: dict, home: Path = HOME) -> dict:
    """A Studio Cloud machine's YouTube download, run here on this computer's connection. Only the options the
    engine uses are accepted (never --exec or a path outside the job's temporary folder)."""
    try:
        args = _safe_ytdlp_args(list(request.get("argv") or []))
    except ValueError as e:
        return {"rc": 2, "stdout": "", "stderr": f"Studio Cloud refused this download: {e}", "files": []}
    prefix = str(request.get("prefix") or "")
    if not re.fullmatch(r"relay/[0-9a-f]{16}", prefix):
        return {"rc": 2, "stdout": "", "stderr": "Studio Cloud refused this download: bad folder", "files": []}
    bucket = studio_bucket(home)
    with tempfile.TemporaryDirectory(prefix="cr-cloud-yt-") as tmp:
        work = Path(tmp)
        for rel in request.get("inputs") or []:
            if _inside(work, str(rel)):
                bucket.get_file(f"{prefix}/in/{rel}", work / rel)
        before = {p: p.stat().st_mtime for p in work.rglob("*") if p.is_file()}
        args = [a.replace(ROOT_MARK, str(work)) for a in args]
        for i, a in enumerate(args[:-1]):
            if a == "-o":
                Path(args[i + 1]).parent.mkdir(parents=True, exist_ok=True)
        try:
            import youtube
            prefix_cmd = youtube.ytdlp() or [sys.executable, "-m", "yt_dlp"]
        except Exception:                                   # noqa: BLE001
            prefix_cmd = [sys.executable, "-m", "yt_dlp"]
        try:
            p = subprocess.run(prefix_cmd + args, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=900, cwd=work)
            rc, stdout, stderr = p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired:
            rc, stdout, stderr = 1, "", "Studio Cloud: the download took too long on this computer"
        files = []
        for f in sorted(work.rglob("*")):
            if f.is_file() and before.get(f) != f.stat().st_mtime and not f.name.endswith((".part", ".ytdl")):
                rel = f.relative_to(work).as_posix()
                bucket.put_file(f"{prefix}/out/{rel}", f)
                files.append(rel)
    return {"rc": rc, "stdout": stdout[:2_000_000], "stderr": stderr[-20000:], "files": files}


if __name__ == "__main__":
    if sys.argv[1:2] == ["pod"]:
        raise SystemExit(pod())
    if sys.argv[1:2] == ["ytdlp"]:
        raise SystemExit(ytdlp_main(sys.argv[2:]))
    print(__doc__)
