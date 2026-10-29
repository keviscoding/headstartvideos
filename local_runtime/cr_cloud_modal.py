"""Studio Cloud on the creator's own Modal account: the machines Studio starts there.

Studio deploys this file into the creator's Modal workspace once (and again when it changes) with the Modal token
they pasted in Setup (cr_cloud_byo.ensure_deployed). Nothing here runs on ChannelRecipe's machines and no
ChannelRecipe key is ever in it: the machine reads the job from the creator's own Modal storage (a Modal Volume),
asks its Claude questions through a Modal queue that the creator's Studio answers with their Claude Code, and is
billed by Modal to the creator's account, per second.

What a machine does with a job:
1. starts from the official Studio release of the creator's version (kept once in their volume) plus the Studio's
   own changed files, or from the Studio's whole code bundle when there's no release to start from;
2. runs `python -m cr_cloud_byo pod`: the engine's farm runner, with its storage on the volume;
3. stops at the job's time limit no matter what, and returns how long it ran.
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

APP_NAME = "channelrecipe-studio"
VOLUME = "channelrecipe-studio-files"
ROOT = Path("/channelrecipe")
GPU_CHROMIUM = "--use-angle=vulkan --enable-features=Vulkan --disable-vulkan-surface --ignore-gpu-blocklist"
FOUR_HOURS = 4 * 3600 + 600

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "curl", "unzip", "ca-certificates", "nodejs", "fonts-liberation", "fonts-dejavu-core",
                 # real WebGL on the GPU machine: Chromium reaches the NVIDIA L4 through ANGLE's Vulkan backend
                 "libvulkan1", "mesa-vulkan-drivers", "libegl1", "libgl1", "libgles2")
    .env({"PLAYWRIGHT_BROWSERS_PATH": "/opt/pw", "PYTHONUNBUFFERED": "1", "WHISPER_MODEL": "base",
          "DEBIAN_FRONTEND": "noninteractive"})
    # what Studio installs at home (requirements.txt), so a job only adds what its release needs on top
    .pip_install("requests>=2.31", "python-dotenv>=1.0", "flask>=3.0", "playwright>=1.40", "faster-whisper>=1.0",
                 "Pillow>=10.0", "numpy>=1.24", "opencv-python-headless>=4.8", "yt-dlp[default,curl-cffi]>=2026.8.19",
                 "rapidocr-onnxruntime==1.2.3", "pypdf>=4.0")
    .run_commands("python -m playwright install --with-deps chromium",
                  "python -c \"from faster_whisper import WhisperModel; "
                  "WhisperModel('base', device='cpu', compute_type='int8')\"")
)
app = modal.App(APP_NAME, image=image)


def _say(msg: str) -> None:
    print(f"=== studio cloud: {msg} ===", flush=True)


def _volume():
    return modal.Volume.from_name(VOLUME, create_if_missing=True)


def _read(vol, key: str, dest: Path) -> bool:
    try:
        with dest.open("wb") as fh:
            vol.read_file_into_fileobj(key, fh)
        return True
    except Exception:                                   # noqa: BLE001 - not there (yet)
        dest.unlink(missing_ok=True)
        return False


def _release(vol, version: str, url: str) -> Path:
    """The official release for this version: from the volume, or downloaded once and kept there."""
    if not re.fullmatch(r"\d+\.\d+-admin\.\d+", version or ""):
        raise RuntimeError("unknown Studio version")
    z = Path(f"/tmp/channelrecipe-{version}.zip")
    key = f"releases/channelrecipe-{version}.zip"
    if _read(vol, key, z) and zipfile.is_zipfile(z):
        _say(f"Studio {version} from your cloud storage")
        return z
    if not url:
        raise RuntimeError("no Studio release to start from; update Studio, then try again")
    with urllib.request.urlopen(url, timeout=900) as r, z.open("wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    if not zipfile.is_zipfile(z):
        raise RuntimeError("the Studio release download is not a zip (link expired?)")
    with vol.batch_upload(force=True) as batch:
        batch.put_file(str(z), "/" + key)
    _say(f"Studio {version} downloaded and kept in your cloud storage ({z.stat().st_size / 1024 ** 2:.0f} MB)")
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


def _code(vol, key: str, release: Path | None) -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    ROOT.mkdir(parents=True)
    if release is not None:
        _unpack_release(release)
    blob = Path("/tmp/channelrecipe-code.zip")
    if not key or not _read(vol, key, blob) or not zipfile.is_zipfile(blob):
        raise RuntimeError("the Studio code isn't in your cloud storage")
    with zipfile.ZipFile(blob) as z:
        if any(".." in Path(n).parts or n.startswith("/") for n in z.namelist()):
            raise RuntimeError("the code bundle has a path outside its folder")
        z.extractall(ROOT)
    _say(f"{'your own files' if release is not None else 'code'} {blob.stat().st_size / 1024 ** 2:.1f} MB")
    blob.unlink(missing_ok=True)


def _first_start(vol, job_id: str, now: float) -> float:
    """When this job's machine first started. Modal starts a job again from the beginning when it takes the machine
    back (preemption); the job's time limit counts from its first start, so a restarted job never runs (and bills
    the creator) past the most Studio showed."""
    if not re.fullmatch(r"byo_[0-9a-f]{16}", job_id or ""):
        return now
    key, mark = f"runs/{job_id}.txt", Path(f"/tmp/cr-run-{job_id}.txt")
    if _read(vol, key, mark):
        try:
            return min(now, float(mark.read_text(encoding="utf-8").strip()))
        except ValueError:
            return now
        finally:
            mark.unlink(missing_ok=True)
    try:
        mark.write_text(str(now), encoding="utf-8")
        with vol.batch_upload(force=True) as batch:
            batch.put_file(str(mark), "/" + key)
    except Exception:                                   # noqa: BLE001 - the limit then counts from this start
        pass
    finally:
        mark.unlink(missing_ok=True)
    return now


def _gpu_chromium() -> str:
    """Chromium as the 3D engine should launch it here: on the NVIDIA L4 through ANGLE's Vulkan backend."""
    found = sorted(Path("/opt/pw").glob("chromium-*/chrome-linux*/chrome"))
    if not found:
        return ""
    wrapper = Path("/tmp/cr-chromium-gpu")
    wrapper.write_text(f"#!/bin/sh\nexec {found[-1]} {GPU_CHROMIUM} \"$@\"\n", encoding="utf-8")
    wrapper.chmod(0o755)
    return str(wrapper)


def _run(job_id: str, env: dict, max_seconds: int, gpu: bool) -> dict:
    t0 = time.time()
    first = t0
    try:
        vol = _volume()
        first = _first_start(vol, job_id, t0)
        if max_seconds - (t0 - first) < 120:
            return {"ok": False, "seconds": 0.0, "error": "stopped at its time limit (Modal restarted the machine)"}
        release = None
        if env.get("CR_CODE_KIND") == "overlay":
            release = _release(vol, env.get("CR_STUDIO_VERSION", ""), env.pop("FARM_RELEASE_URL", ""))
        _code(vol, env.get("CR_CODE_KEY", ""), release)
        req = ROOT / "requirements.txt"
        if req.is_file():
            subprocess.run(["python", "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", str(req)],
                           cwd=ROOT, timeout=900, check=False)
        if not (ROOT / "cr_cloud_byo.py").is_file():
            raise RuntimeError("this Studio can't render on your Modal account; update Studio first")
    except Exception as e:                              # noqa: BLE001
        return {"ok": False, "seconds": round(time.time() - t0, 1), "error": f"boot: {e}"[:400]}

    run_env = {**os.environ, **env, "CHANNELRECIPE_HOME": str(ROOT), "PYTHONUNBUFFERED": "1", "CR_CLOUD_JOB": job_id}
    if gpu:
        run_env.setdefault("F3D_CHROMIUM_ARGS", GPU_CHROMIUM)
        run_env.setdefault("NVIDIA_DRIVER_CAPABILITIES", "all")
        chromium = _gpu_chromium()
        if chromium:
            run_env["F3D_CHROMIUM"] = chromium      # the 3D engine's render.py launches this Chromium
    left = max(60, int(max_seconds - (time.time() - first)))
    _say(f"job {job_id}: rendering, limit {left // 60} min")
    try:
        p = subprocess.run(["python", "-u", "-m", "cr_cloud_byo", "pod"], cwd=ROOT, env=run_env, timeout=left)
        rc = p.returncode
        error = "" if rc == 0 else f"the render exited with {rc}"
    except subprocess.TimeoutExpired:
        rc, error = -9, "stopped at its time limit"
    seconds = round(time.time() - t0, 1)
    _say(f"job {job_id}: {'done' if rc == 0 else error} after {seconds / 60:.1f} min")
    return {"ok": rc == 0, "seconds": seconds, "error": error}


@app.function(cpu=8, memory=32768, timeout=FOUR_HOURS, max_containers=5)
def render_cpu(job_id: str, env: dict, max_seconds: int) -> dict:
    return _run(job_id, env, max_seconds, gpu=False)


@app.function(gpu="L4", cpu=8, memory=32768, timeout=FOUR_HOURS, max_containers=3)
def render_gpu(job_id: str, env: dict, max_seconds: int) -> dict:
    return _run(job_id, env, max_seconds, gpu=True)
