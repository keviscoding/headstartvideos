"""Studio Cloud: the render farm on ChannelRecipe's machines, paid from the account's cloud balance.

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
import json
import os
import sys
import tempfile
import threading
import time
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

    def _check(self, r, method: str, key: str):
        if r.status_code >= 400:
            raise _s3_error(f"{method} {key} -> HTTP {r.status_code}: {(r.text or '')[:200]}")
        return r

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        url = self.presign(key, 3600, "PUT", content_type)
        self._check(self.http.put(url, data=data, headers={"Content-Type": content_type}, timeout=300), "PUT", key)

    def put_file(self, key: str, path, content_type: str | None = None, timeout: int = 1800) -> int:
        path = Path(path)
        size = path.stat().st_size
        ctype = content_type or _guess_type(path.name)
        url = self.presign(key, 6 * 3600, "PUT", ctype)
        with path.open("rb") as fh:
            self._check(self.http.put(url, data=fh, headers={"Content-Type": ctype, "Content-Length": str(size)},
                                      timeout=timeout), "PUT", key)
        return size

    def get_bytes(self, key: str) -> bytes:
        return self._check(self.http.get(self.presign(key, 600), timeout=300), "GET", key).content

    def get_file(self, key: str, path, timeout: int = 1800) -> int:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        got = 0
        r = self._check(self.http.get(self.presign(key, 6 * 3600), stream=True, timeout=timeout), "GET", key)
        with tmp.open("wb") as fh:
            for block in r.iter_content(1 << 20):
                fh.write(block)
                got += len(block)
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
        cfg = json.loads((home / ".cr-connection.json").read_text())
    except (OSError, ValueError):
        return "", ""
    key = str(cfg.get("key") or "")
    base = str(cfg.get("url") or "https://channelrecipe.com").rstrip("/")
    if not key.startswith("cr_mcp_") or not (base.startswith("https://") or "://127.0.0.1" in base
                                             or "://localhost" in base):
        return "", ""
    return base, key


def status(force: bool = False, home: Path = HOME) -> dict:
    """Is Studio Cloud open to this account, and what is its balance? Asked at most once a minute."""
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
    with _STATUS_LOCK:
        _STATUS.update(at=time.time(), data=data)
    return data


def active(home: Path = HOME) -> bool:
    return bool(status(home=home).get("available"))


def studio_bucket(home: Path = HOME) -> BrokerBucket:
    base, key = _connection(home)
    return BrokerBucket(base + "/api/studio/cloud", "Bearer " + key)


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
        job = self._call("POST", "/jobs", {"machine": "cpu", "minutes": float(spec.get("minutes") or 10),
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
                return self._call("DELETE", f"/jobs/{pod_id}").get("status") != "running"
            except (RuntimeError, requests.RequestException):
                time.sleep(2)
        return False

    def gpus(self) -> list:
        return []

    def cpus(self) -> list:
        return []


class Relay:
    """Answers the Claude questions a Studio Cloud machine asks, with this computer's Claude Code."""
    _thread = None
    _lock = threading.Lock()
    WORKERS = 3

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
        while time.time() - idle_since < 20 * 60:         # it stops itself once no machine has asked for a while
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


def answer(request: dict) -> str:
    import make_video as mv
    with tempfile.TemporaryDirectory(prefix="cr-cloud-") as tmp:
        prompt, images = _prompt_parts(request, Path(tmp))
        if images:
            return mv._claude_code_vision(prompt, images)
        model = str(request.get("model") or getattr(mv, "SCRIPT_MODEL", ""))
        return mv.claude(prompt, model, int(request.get("max_tokens") or 4000), provider="claudecode")


# ── wiring the farm to Studio Cloud ───────────────────────────────────────────────────────────────────────────

def checklist(original) -> list:
    st = status()
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
        return checklist(original_checklist(self)) if getattr(self, "studio_cloud", False) else original_checklist(self)

    def missing(self):
        if not getattr(self, "studio_cloud", False):
            return original_missing(self)
        return [c["label"] for c in checklist(original_checklist(self))[:2] if not c["ok"]]

    def bucket_from(cfg):
        return studio_bucket(home) if getattr(cfg, "studio_cloud", False) else original_bucket(cfg)

    def runpod(api_key: str = "", session=None):
        return CloudPods(api_key, session, home) if api_key == SENTINEL else original_runpod(api_key, session)

    fc.Config.__init__, fc.Config.checklist, fc.Config.missing_for_mac = init, check, missing
    fc.bucket_from = ff.bucket_from = bucket_from
    frp.RunPod = runpod
    _INSTALLED.append(True)
    return True


# ── the machine's side ────────────────────────────────────────────────────────────────────────────────────────

def pod() -> int:
    """Run the farm's own runner with its storage pointed at the job's signed broker."""
    sys.path.insert(0, str(HOME))
    broker, token = os.environ.get("FARM_BROKER_URL", ""), os.environ.get("FARM_JOB_TOKEN", "")
    if not broker or not token:
        print("FARM_BROKER_URL / FARM_JOB_TOKEN are not set: this is not a Studio Cloud machine", file=sys.stderr)
        return 2
    import farm.config as fc
    fc.bucket_from = lambda cfg: BrokerBucket(broker, "Bearer " + token)
    from farm import runner
    return int(runner.main() or 0)


if __name__ == "__main__":
    if sys.argv[1:2] == ["pod"]:
        raise SystemExit(pod())
    print(__doc__)
