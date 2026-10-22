"""Studio Cloud machines on Modal: one machine renders one Studio farm job, then switches off.

    modal deploy cloud/modal_app.py            # from the repo root; prints the control endpoint's URL

The server (webapp/cloud_jobs.py) starts, checks and stops machines through the control endpoint here, with a shared
key (the Modal secret `cr-cloud-control`, key CR_CLOUD_CONTROL_KEY; the same value is in the web app's env). Nobody
else can start a machine.

What a machine does with a job:
1. downloads the creator's Studio code bundle from the signed link in its env (FARM_CODE_URL), as the engine's own
   farm pods do, and installs anything that release needs beyond this image;
2. runs `python -m cr_cloud pod`: the engine's farm runner, with its storage pointed at ChannelRecipe's server (which
   signs one request at a time inside the creator's folder) and its Claude calls relayed to the creator's Studio;
3. stops at the job's time limit no matter what (the money reserved for it covers exactly that), and returns how
   long it ran so the server can charge per second.

The machine holds the creator's own render keys for the one job and a token that only reaches that job; no
ChannelRecipe key is ever in it.
"""
import os
import re
import shutil
import subprocess
import time
import urllib.request
import zipfile
from pathlib import Path

import modal

ROOT = Path("/channelrecipe")
HERE = Path(__file__).resolve().parent

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "curl", "unzip", "ca-certificates", "nodejs", "fonts-liberation", "fonts-dejavu-core",
                 # real WebGL on the GPU machine: Chromium reaches the NVIDIA L4 through ANGLE's Vulkan backend
                 "libvulkan1", "mesa-vulkan-drivers", "libegl1", "libgl1", "libgles2")
    .env({"PLAYWRIGHT_BROWSERS_PATH": "/opt/pw", "PYTHONUNBUFFERED": "1", "WHISPER_MODEL": "base",
          "DEBIAN_FRONTEND": "noninteractive"})
    .pip_install_from_requirements(str(HERE / "requirements-render.txt"))
    .pip_install("fastapi[standard]")
    .run_commands("python -m playwright install --with-deps chromium",
                  "python -c \"from faster_whisper import WhisperModel; "
                  "WhisperModel('base', device='cpu', compute_type='int8')\"")
)
app = modal.App("cr-studio-cloud", image=image)
control_secret = modal.Secret.from_name("cr-cloud-control")
# Official Studio releases, kept once per version: a Studio sends only its own files and the machine starts from these
releases = modal.Volume.from_name("cr-studio-releases", create_if_missing=True)
RELEASES = Path("/releases")

GPU_CHROMIUM = "--use-angle=vulkan --enable-features=Vulkan --disable-vulkan-surface --ignore-gpu-blocklist"
FOUR_HOURS = 4 * 3600 + 600


def _say(msg: str) -> None:
    print(f"=== studio cloud: {msg} ===", flush=True)


def _release_zip(version: str, url: str) -> Path:
    """The official release for this version, downloaded once into the shared volume."""
    if not re.fullmatch(r"\d+\.\d+-admin\.\d+", version or ""):
        raise RuntimeError("unknown Studio version")
    releases.reload()
    z = RELEASES / f"channelrecipe-{version}.zip"
    if z.is_file() and zipfile.is_zipfile(z):
        _say(f"Studio {version} from the release cache")
        return z
    part = RELEASES / f".{version}.{os.getpid()}.{int(time.time())}.part"
    with urllib.request.urlopen(url, timeout=900) as r, part.open("wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    if not zipfile.is_zipfile(part):
        part.unlink(missing_ok=True)
        raise RuntimeError("the Studio release download is not a zip (link expired?)")
    part.rename(z)
    releases.commit()
    _say(f"Studio {version} downloaded into the release cache ({z.stat().st_size / 1024 ** 2:.0f} MB)")
    return z


def _unpack_release(z: Path) -> None:
    """The release ZIP holds one top folder ("ChannelRecipe studio/"); its contents become the machine's Studio."""
    with zipfile.ZipFile(z) as zf:
        for info in zf.infolist():
            parts = Path(info.filename).parts
            if len(parts) < 2 or ".." in parts:
                continue
            dest = ROOT.joinpath(*parts[1:])
            if info.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)


def _fetch_code(url: str, release: Path | None = None) -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    ROOT.mkdir(parents=True)
    if release is not None:
        _unpack_release(release)
    blob = Path("/tmp/channelrecipe.zip")
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=600) as r, blob.open("wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
            if blob.stat().st_size < 22 or not zipfile.is_zipfile(blob):
                raise RuntimeError("the code download is not a zip (link expired?)")
            with zipfile.ZipFile(blob) as z:
                if any(".." in Path(n).parts or n.startswith("/") for n in z.namelist()):
                    raise RuntimeError("the code bundle has a path outside its folder")
                z.extractall(ROOT)
            _say(f"{'your own files' if release is not None else 'code'} {blob.stat().st_size / 1024 ** 2:.1f} MB")
            return
        except Exception as e:                          # noqa: BLE001 - retried, then reported
            last = e
            time.sleep(5 * (attempt + 1))
        finally:
            blob.unlink(missing_ok=True)
    raise RuntimeError(f"could not fetch the Studio code: {type(last).__name__}")


def _run(job_id: str, env: dict, max_seconds: int, gpu: bool) -> dict:
    t0 = time.time()
    try:
        release = None
        if env.get("FARM_RELEASE_URL"):
            release = _release_zip(env.get("CR_STUDIO_VERSION", ""), env["FARM_RELEASE_URL"])
        _fetch_code(env["FARM_CODE_URL"], release)
        req = ROOT / "requirements.txt"
        if req.is_file():
            subprocess.run(["python", "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", str(req)],
                           cwd=ROOT, timeout=900, check=False)
        if not (ROOT / "cr_cloud.py").is_file():
            raise RuntimeError("this Studio release can't render on Studio Cloud; update Studio first")
    except Exception as e:                              # noqa: BLE001
        return {"ok": False, "seconds": round(time.time() - t0, 1), "error": f"boot: {e}"[:400]}

    run_env = {**os.environ, **env, "CHANNELRECIPE_HOME": str(ROOT), "PYTHONUNBUFFERED": "1"}
    if gpu:
        run_env.setdefault("F3D_CHROMIUM_ARGS", GPU_CHROMIUM)
        run_env.setdefault("NVIDIA_DRIVER_CAPABILITIES", "all")
    left = max(60, int(max_seconds - (time.time() - t0)))
    _say(f"job {job_id}: rendering, limit {left // 60} min")
    try:
        p = subprocess.run(["python", "-u", "-m", "cr_cloud", "pod"], cwd=ROOT, env=run_env, timeout=left)
        rc = p.returncode
        error = "" if rc == 0 else f"the render exited with {rc}"
    except subprocess.TimeoutExpired:
        rc, error = -9, "stopped at its time limit"
    seconds = round(time.time() - t0, 1)
    _say(f"job {job_id}: {'done' if rc == 0 else error} after {seconds / 60:.1f} min")
    return {"ok": rc == 0, "seconds": seconds, "error": error}


@app.function(cpu=8, memory=32768, timeout=FOUR_HOURS, max_containers=20, volumes={"/releases": releases})
def render_cpu(job_id: str, env: dict, max_seconds: int) -> dict:
    return _run(job_id, env, max_seconds, gpu=False)


@app.function(gpu="L4", cpu=4, memory=16384, timeout=FOUR_HOURS, max_containers=10, volumes={"/releases": releases})
def render_gpu(job_id: str, env: dict, max_seconds: int) -> dict:
    return _run(job_id, env, max_seconds, gpu=True)


@app.function(secrets=[control_secret], min_containers=0)
@modal.asgi_app(label="cr-studio-cloud-control")
def control():
    import hmac

    from fastapi import FastAPI, Header, HTTPException
    from pydantic import BaseModel

    api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def _check(authorization: str):
        want = "Bearer " + os.environ["CR_CLOUD_CONTROL_KEY"]
        if not hmac.compare_digest((authorization or "").encode(), want.encode()):
            raise HTTPException(401, "no")

    class Spawn(BaseModel):
        machine: str
        job_id: str
        env: dict
        max_seconds: int

    class Call(BaseModel):
        call_id: str

    @api.post("/spawn")
    def spawn(req: Spawn, authorization: str = Header("")):
        _check(authorization)
        fn = render_gpu if req.machine == "gpu" else render_cpu
        call = fn.spawn(req.job_id, req.env, min(int(req.max_seconds), FOUR_HOURS - 600))
        return {"call_id": call.object_id}

    @api.post("/status")
    def status(req: Call, authorization: str = Header("")):
        _check(authorization)
        call = modal.FunctionCall.from_id(req.call_id)
        try:
            result = call.get(timeout=0)
        except modal.exception.FunctionTimeoutError:    # a subclass of Modal's TimeoutError: check it first
            return {"state": "failed", "error": "stopped at its time limit"}
        except modal.exception.OutputExpiredError:
            return {"state": "failed", "error": "its result expired before anyone collected it"}
        except (TimeoutError, modal.exception.TimeoutError):
            return {"state": "running"}
        except modal.exception.InputCancellation:
            return {"state": "cancelled"}
        except Exception as e:                          # noqa: BLE001 - a crashed machine is a failed job
            return {"state": "failed", "error": f"{type(e).__name__}: {str(e)[:300]}"}
        return {"state": "done", "result": result}

    @api.post("/cancel")
    def cancel(req: Call, authorization: str = Header("")):
        _check(authorization)
        modal.FunctionCall.from_id(req.call_id).cancel(terminate_containers=True)
        return {"ok": True}

    return api
