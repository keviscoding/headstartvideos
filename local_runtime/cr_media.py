"""Optional single-account media routing for the original owner preview engine.

Only compatible capabilities are replaced. Unknown specialised operations stop
with setup guidance. Provider task IDs are saved before polling; uncertain paid
submissions are never repeated or routed to another wallet.
"""
from __future__ import annotations
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests
import cr_bridge

HOME = Path(__file__).resolve().parent
CATALOG = HOME / "cr-providers.json"
if not CATALOG.is_file():  # Repository tests; the builder supplies the same catalog.
    CATALOG = HOME.parent / "webapp/frontier/providers.json"
PROVIDERS = json.loads(CATALOG.read_text())
TASKS = {"images", "narration", "motion", "avatars"}
_slots = threading.BoundedSemaphore(2)
_lock = threading.RLock()
_references = {}


class AccountStop(BaseException):
    # The original engine catches Exception and sometimes SystemExit to try
    # other paid services. Stop at its job boundary instead of changing wallets.
    pass


def profile(task=None):
    settings = cr_bridge.load_settings()
    voice = settings.get("narration") or {}
    if task == "narration" and voice.get("provider") in ("kie", "atlas", "original"):
        return {**voice, "tasks": ["narration"]} if voice["provider"] != "original" else {}
    cfg = settings.get("media_hub") or {}
    return cfg if cfg.get("provider") in ("kie", "atlas") else {}


def active(task):
    return task in profile(task).get("tasks", [])


def account(task=None):
    cfg = profile(task)
    if not cfg or not cr_bridge.load_settings().get("costs_accepted"):
        raise AccountStop("Review your model account and spending agreement in Setup & accounts.")
    provider = cfg["provider"]
    key = os.getenv(PROVIDERS[provider]["key_env"], "").strip()
    if not key:
        raise AccountStop(f"Reconnect {PROVIDERS[provider]['label']} in Setup & accounts.")
    return provider, key


def narration_model(provider, model=None):
    info = PROVIDERS[provider]
    model = model or info["models"]["narration"]
    for choice in info.get("narration_models", []):
        if choice["id"] == model:
            return choice
    raise AccountStop("Choose a supported voice model in Setup & accounts.")


def narration_input(text, voice, model):
    if model == "google/gemini-3-8-flash-tts":
        return {"temperature": 1, "speakers": [{"speaker_id": "Speaker 1", "voice_name": voice}],
                "dialogue_turns": [{"speaker_id": "Speaker 1", "text": text}]}
    return {"text": text, "voice": voice}


def narration_audio(path):
    # FFmpeg is checked at setup. Pass PCM directly to Whisper so differing
    # PyAV decoder versions cannot break a successfully generated voice.
    import numpy as np
    try:
        decoded = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le",
                                  "-ac", "1", "-ar", "16000", "pipe:1"], check=True, capture_output=True)
        samples = np.frombuffer(decoded.stdout, dtype=np.float32)
        if not samples.size:
            raise AccountStop("Narration was saved, but no audio could be read for subtitle timing.")
        return samples
    except (OSError, subprocess.CalledProcessError, ValueError):
        raise AccountStop("Narration was saved, but subtitle timing could not read it. Check your video tools before resuming.")


def _data(res, provider):
    if res.status_code != 200:
        raise AccountStop(f"{PROVIDERS[provider]['label']} could not complete this step (HTTP {res.status_code}). Check its task log before retrying.")
    try:
        data = res.json()
        if not isinstance(data, dict):
            raise ValueError()
        # Kie uses HTTP 200 for errors; Atlas uses code 0/200 on success.
        if data.get("code") not in ((200,) if provider == "kie" else (None, 0, 200)):
            raise AccountStop(f"{PROVIDERS[provider]['label']} refused this request. Check its API access, balance and task log.")
        return data
    except (ValueError, TypeError):
        raise AccountStop("The model account returned an unreadable response. Check its task log.")


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.chmod(0o600)
    tmp.replace(path)


def _url(value):
    # Provider outputs/uploads are downloaded locally, without Authorization.
    parsed = urlsplit(str(value or ""))
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise AccountStop("The model account did not return a secure media URL.")
    return str(value)


def download(url, dest):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".cr-download")
    try:
        with requests.get(_url(url), stream=True, timeout=(15, 180)) as res:
            if res.status_code != 200:
                raise AccountStop("The generated file could not be downloaded. Resume to fetch the existing task; do not submit it again.")
            size = 0
            with tmp.open("wb") as f:
                for chunk in res.iter_content(1024 * 1024):
                    size += len(chunk)
                    if size > 512 * 1024 * 1024:
                        raise AccountStop("The generated file exceeds this preview's download limit.")
                    f.write(chunk)
            if not size:
                raise AccountStop("The model account returned an empty media file.")
        tmp.replace(dest)
    except requests.RequestException:
        raise AccountStop("The file transfer stopped. Resume to fetch the existing task; no new generation is needed.")
    finally:
        tmp.unlink(missing_ok=True)


def upload(path, cache_file=None):
    provider, key = account()
    path = Path(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    marker = path.parent / ".cr-tasks" / f"upload-{provider}-{digest}.json"
    # Uploads expire. Reuse for at most 12 hours, not indefinitely.
    with _lock:
        if marker.is_file():
            previous = json.loads(marker.read_text())
            if previous.get("created_at", 0) > time.time() - 43200:
                _references[previous["url"]] = {"upload_sha256": digest}
                return _url(previous["url"])
        headers = {"Authorization": "Bearer " + key}
        url = ("https://kieai.redpandaai.co/api/file-stream-upload" if provider == "kie"
               else "https://api.atlascloud.ai/api/v1/model/uploadMedia")
        try:
            with path.open("rb") as f:
                res = requests.post(url, headers=headers, files={"file": (digest + path.suffix, f, mimetypes.guess_type(path.name)[0])},
                                    data={"uploadPath": "channelrecipe", "fileName": digest + path.suffix} if provider == "kie" else None,
                                    timeout=(15, 180), allow_redirects=False)
            data = _data(res, provider)
            value = ((data.get("data") or {}).get("downloadUrl") if provider == "kie" else
                     data.get("url") or (data.get("data") or {}).get("url"))
            value = _url(value)
            _write(marker, {"url": value, "created_at": time.time()})
            _references[value] = {"upload_sha256": digest}
            return value
        except requests.RequestException:
            raise AccountStop("The reference upload stopped. Check your model account connection.")


def run(task, inputs, dest, *, model=None, log=print, timeout_s=1800):
    provider, key = account(task)
    dest = Path(dest)
    model = model or PROVIDERS[provider]["models"][task]
    payload = {"model": model, "input": inputs} if provider == "kie" else {"model": model, **inputs}
    # Scope to wallet as well as provider, without recording the key itself.
    def logical(value):
        if isinstance(value, dict):
            return {k: logical(v) for k, v in value.items()}
        if isinstance(value, list):
            return [logical(v) for v in value]
        return _references.get(value, value) if isinstance(value, str) else value
    # Temporary upload URLs may change on resume. Identify our references by
    # content/task identity, so refreshing one does not create another paid job.
    identity = {"provider": provider, "wallet": hashlib.sha256(key.encode()).hexdigest(), "payload": logical(payload)}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    marker = dest.parent / ".cr-tasks" / (dest.name + "-" + fingerprint[:24] + ".json")
    headers = {"Authorization": "Bearer " + key}
    base = "https://api.kie.ai/api/v1/jobs" if provider == "kie" else "https://api.atlascloud.ai/api/v1/model"
    with _slots:
        with _lock:
            previous = json.loads(marker.read_text()) if marker.is_file() else {}
            if previous.get("status") == "complete" and dest.is_file() and previous.get("file_sha256") == hashlib.sha256(dest.read_bytes()).hexdigest():
                log(f"cached model task: {dest.name}")
                _references[previous["url"]] = {"task_fingerprint": fingerprint}
                return previous["url"]
            if previous and not previous.get("id"):
                raise AccountStop("This media submission has an uncertain result. Check your provider task log and the .cr-tasks receipt before deliberately retrying.")
            if previous.get("status") == "failed":
                raise AccountStop("This media task failed. Check the provider log and saved receipt before deliberately retrying.")
            if not previous:
                previous = {"provider": provider, "model": model, "status": "submitting", "created_at": time.time()}
                _write(marker, previous)  # Crash before ID is recorded is uncertain, never auto-resubmit.
                suffix = "/createTask" if provider == "kie" else {"images": "/generateImage", "narration": "/generateAudio", "motion": "/generateVideo", "avatars": "/generateVideo"}[task]
                try:
                    res = requests.post(base + suffix, headers=headers, json=payload, timeout=(15, 90), allow_redirects=False)
                    data = _data(res, provider)
                    detail = data.get("data") or data
                    task_id = detail.get("taskId" if provider == "kie" else "id")
                    if not task_id or not re.fullmatch(r"[A-Za-z0-9_\-]+", str(task_id)):
                        raise AccountStop("No task ID was returned. Check the provider log before retrying this submission.")
                    previous.update(id=str(task_id), status="pending")
                    _write(marker, previous)
                except requests.RequestException:
                    raise AccountStop("The media submission was interrupted. Check the provider task log before retrying; it may already be running.")
            task_id = previous["id"]
        log(f"{PROVIDERS[provider]['label']}: {model} · task {task_id}")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                url = base + ("/recordInfo" if provider == "kie" else "/prediction/" + task_id)
                res = requests.get(url, headers=headers, params={"taskId": task_id} if provider == "kie" else None,
                                   timeout=(10, 45), allow_redirects=False)
                if res.status_code in (429, 500, 502, 503, 504):
                    time.sleep(5)
                    continue  # Polling an existing ID is safe; creating a new job is not.
                parsed = _data(res, provider)
                detail = parsed.get("data") or parsed
                state = detail.get("state" if provider == "kie" else "status")
                if state in ("success", "completed", "succeeded"):
                    outputs = json.loads(detail.get("resultJson") or "{}").get("resultUrls", []) if provider == "kie" else detail.get("outputs", [])
                    if not outputs:
                        raise AccountStop("The completed task returned no media. Check the provider task log.")
                    output = _url(outputs[0])
                    previous.update(status="generated", url=output, completed_at=time.time(),
                                    credits=detail.get("creditsConsumed"), cost=detail.get("cost"))
                    _write(marker, previous)
                    download(output, dest)
                    previous.update(status="complete", file_sha256=hashlib.sha256(dest.read_bytes()).hexdigest())
                    _write(marker, previous)
                    _references[output] = {"task_fingerprint": fingerprint}
                    return output
                if state in ("fail", "failed", "cancelled", "canceled"):
                    previous["status"] = "failed"
                    reason = re.sub(r"\s+", " ", str(detail.get("failMsg") or "")).replace(key, "[redacted]")[:200]
                    previous.update(failure_message=reason, failure_code=str(detail.get("failCode") or "")[:40],
                                    credits=detail.get("creditsConsumed"))
                    _write(marker, previous)
                    raise AccountStop(f"{PROVIDERS[provider]['label']} failed this task." + (" " + reason if reason else "") +
                                      " Check its log; no other wallet was used.")
            except requests.RequestException:
                pass  # Read-only task poll; keep the original ID.
            except (ValueError, TypeError, AttributeError, KeyError):
                raise AccountStop("The task status could not be read. Resume to check the same provider task.")
            time.sleep(5)
        raise AccountStop("The media task is still pending. Its ID is saved; resume this job to check it without submitting again.")


def image(prompt, dest, label="image", ref_urls=None, **kwargs):
    cfg = profile()
    provider = cfg["provider"]
    inputs = {"prompt": prompt[:5000], "aspect_ratio": "16:9", "output_format": "png"}
    refs = list(ref_urls or [])
    model = PROVIDERS[provider]["models"]["image_edit" if refs else "images"]
    if refs:
        inputs["image_urls" if provider == "kie" else "images"] = refs[:8]
    return run("images", inputs, dest, model=model)


def motion(image_url, prompt, dest, label="motion", retries=1, **kwargs):
    provider = profile()["provider"]
    inputs = {"prompt": prompt[:2000], "aspect_ratio": "16:9", "resolution": "720p", "duration": 8}
    if provider == "kie":
        inputs.update(input_urls=[image_url], generate_audio=False)
    else:
        inputs["image"] = image_url
    return run("motion", inputs, dest)


def avatar_clip(provider, image_url, audio_url, dest, log=print):
    if provider not in ("kling-standard", "kling-pro"):
        raise AccountStop("This presenter model needs its original service. Choose Kling Standard/Pro for the model account, or turn off its presenter replacement in setup.")
    hub = profile()["provider"]
    inputs = {"image_url": image_url, "audio_url": audio_url} if hub == "kie" else {"image": image_url, "audio": audio_url}
    model = PROVIDERS[hub]["models"]["avatar_pro" if provider == "kling-pro" else "avatars"]
    run("avatars", inputs, dest, model=model, log=log)
    return Path(dest)


def _chunks(text, limit=2400):
    # Hard bounds also handle a sentence longer than the model's text limit.
    remaining = text.strip()
    while remaining:
        end = min(len(remaining), limit)
        if end < len(remaining):
            cut = remaining.rfind(" ", 0, end)
            if cut > 0:
                end = cut
        yield remaining[:end]
        remaining = remaining[end:].lstrip()


def narrate(engine, text, mp3, srt, voice_id="", style=""):
    cfg = profile("narration")
    provider = cfg["provider"]
    model = narration_model(provider, cfg.get("model"))
    voice = cfg.get("voice") or model["default_voice"]
    if model.get("voice_names") and voice not in model["voice_names"]:
        raise AccountStop("Choose a voice name supported by your narration model in Setup & accounts.")
    folder = Path(mp3).parent / (Path(mp3).stem + "-hub-voice")
    folder.mkdir(parents=True, exist_ok=True)
    parts = []
    for i, piece in enumerate(_chunks(text)):
        part = folder / f"part-{i:03d}.{model['format']}"
        run("narration", narration_input(piece, voice, model["id"]), part, model=model["id"], log=engine.log)
        parts.append(part)
    if not parts:
        raise AccountStop("There is no narration text to record.")
    if len(parts) == 1 and model["format"] == "mp3":
        shutil.copyfile(parts[0], mp3)
    elif len(parts) == 1:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(parts[0]),
                        "-c:a", "libmp3lame", "-b:a", "192k", str(mp3)], check=True)
    else:
        # Relative names avoid escaping user-controlled folder names in ffconcat.
        listing = folder / "parts.txt"
        listing.write_text("".join(f"file '{p.name}'\n" for p in parts))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", str(listing), "-c:a", "libmp3lame", "-b:a", "192k", str(mp3)], check=True)
    # Both hubs return audio. Preserve timing with the engine's local Whisper,
    # then restore script spelling instead of inventing caption timestamps.
    engine.log("Timing narration on this computer…")
    segments, _ = engine._get_whisper().transcribe(narration_audio(mp3), beam_size=1, word_timestamps=True, vad_filter=True)
    cues, current = [], []
    for seg in segments:
        for word in seg.words or []:
            if not word.word.strip():
                continue
            current.append((word.word.strip(), float(word.start), float(word.end)))
            if len(current) >= 6 or re.search(r"[.!?]$", word.word.strip()):
                cues.append(current)
                current = []
    if current:
        cues.append(current)
    if not cues:
        raise AccountStop("Narration was saved, but subtitle timing failed. Check the audio before resuming.")
    Path(srt).write_text("\n".join(f"{i+1}\n{engine._srt_ts(c[0][1])} --> {engine._srt_ts(c[-1][2])}\n{' '.join(w[0] for w in c)}\n" for i, c in enumerate(cues)))
    engine._script_words_into_srt(Path(srt), text)


def install(engine):
    """Keep native providers when a task is unticked; resolve settings per call."""
    def hook(name, task, replacement):
        original = getattr(engine, name)
        setattr(engine, name, lambda *a, **k: replacement(*a, **k) if active(task) else original(*a, **k))
    hook("image_gen", "images", image)
    hook("_image_provider", "images", lambda: "channelrecipe-hub")
    hook("_kie_still_with_url", "images", lambda prompt, dest, label, retries=4: image(prompt, dest, label))
    hook("_kie_video_gen", "motion", motion)
    hook("_tts_one", "narration", lambda *a, **k: narrate(engine, *a, **k))
    original_voice = engine.generate_voiceover
    def voiceover(script, job, force=False, avatar_min=0, style=""):
        if not active("narration"):
            return original_voice(script, job, force, avatar_min, style)
        job = Path(job)
        mp3, srt = job / "audio.mp3", job / "subs.srt"
        if (job / "voice.own").exists() or (mp3.exists() and srt.exists() and not force):
            return mp3, srt
        # Preserve the engine's intro split and join contract for presenter jobs.
        pieces = engine._split_script_by_minutes(script, avatar_min)
        if len(pieces) > 1:
            audio_parts, subtitle_parts, offset = [], [], 0.0
            for i, piece in enumerate(pieces, 1):
                pm, ps = job / f"audio_p{i}.mp3", job / f"subs_p{i}.srt"
                narrate(engine, piece, pm, ps, style=style)
                audio_parts.append(pm)
                subtitle_parts.append((ps, offset))
                offset += engine._audio_dur(pm)
            engine._concat_mp3(audio_parts, mp3)
            engine._merge_srt(subtitle_parts, srt)
        else:
            narrate(engine, script, mp3, srt, style=style)
        (job / "words_heard.json").unlink(missing_ok=True)
        engine._speed_voice(mp3, srt, style)
        engine._regroup_word_srt(srt)
        return engine._denoise_voice(mp3, style), srt
    engine.generate_voiceover = voiceover
    # These modules have independent voice entry points and media uploads.
    import wavespeed
    import vox
    import avatar
    for module in (wavespeed, vox):
        original = module.voiceover
        def routed_voice(eng, script, job, *args, _original=original, **kwargs):
            if active("narration"):
                force = kwargs.get("force", args[-1] if args and isinstance(args[-1], bool) else False)
                style = args[0] if args and isinstance(args[0], str) else ""
                return voiceover(script, job, force=force, style=style)
            return _original(eng, script, job, *args, **kwargs)
        module.voiceover = routed_voice
    original_upload = wavespeed.upload
    wavespeed.upload = lambda *a, **k: upload(*a, **k) if any(active(t) for t in ("images", "motion", "avatars")) else original_upload(*a, **k)
    original_run = wavespeed.run
    def routed_run(model, payload, dest, *args, **kwargs):
        image_model = any(x in model for x in ("nano-banana", "gpt-image", "seedream", "flux", "qwen-image"))
        if image_model and active("images"):
            refs = payload.get("images") or ([payload["image"]] if payload.get("image") else [])
            image(payload.get("prompt", ""), dest, ref_urls=refs)
            return Path(dest)
        if "image-to-video" in model and active("motion"):
            source = payload.get("image") or payload.get("image_url")
            if not source:
                raise AccountStop("This motion model needs a source image. Review the scene before retrying.")
            motion(source, payload.get("prompt", ""), dest)
            return Path(dest)
        if (active("images") and image_model) or (active("motion") and ("video" in model or "wan" in model)):
            raise AccountStop("This specialised model needs its original account. Turn off its task replacement in Setup & accounts.")
        return original_run(model, payload, dest, *args, **kwargs)
    wavespeed.run = routed_run
    original_host = engine.host_image
    engine.host_image = lambda path: upload(path) if active("images") else original_host(path)
    original_kie_upload = engine._kie_upload_image
    engine._kie_upload_image = lambda path: upload(path) if any(active(t) for t in ("images", "motion", "avatars")) else original_kie_upload(path)
    original_avatar_host = avatar._host
    avatar._host = lambda path, cache, log=print: upload(path, cache) if active("avatars") else original_avatar_host(path, cache, log)
    for name in ("_kie", "_wavespeed", "_wavespeed_avatar"):
        original = getattr(avatar, name)
        def routed_avatar(provider, image_url, audio_url, dest, log=print, _original=original, **kwargs):
            return avatar_clip(provider, image_url, audio_url, dest, log) if active("avatars") else _original(provider, image_url, audio_url, dest, log, **kwargs)
        setattr(avatar, name, routed_avatar)
