"""Optional single-account media routing for the original owner preview engine.

Only compatible capabilities are replaced. Unknown specialised operations stop
with setup guidance. Provider task IDs are saved before polling; uncertain paid
submissions are never repeated or routed to another wallet.
"""
from __future__ import annotations
import hashlib
from contextlib import contextmanager
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
import cr_relay

cr_relay.install()               # Kie's files through ChannelRecipe when this computer can't reach Kie's file host

HOME = Path(__file__).resolve().parent
CATALOG = HOME / "cr-providers.json"
if not CATALOG.is_file():  # Repository tests; the builder supplies the same catalog.
    CATALOG = HOME.parent / "webapp/frontier/providers.json"
PROVIDERS = json.loads(CATALOG.read_text(encoding="utf-8"))
TASKS = {"images", "narration", "motion", "avatars"}
# Tasks in flight per kind. Pictures run four at a time (a video needs dozens and Kie takes up to a minute for
# each); voice, motion and avatars stay at two.
_slots = {"images": threading.BoundedSemaphore(4)}
_other_slots = threading.BoundedSemaphore(2)
_lock = threading.RLock()
_references = {}
_native_images = None
_routing = threading.local()


class AccountStop(BaseException):
    # The original engine catches Exception and sometimes SystemExit to try
    # other paid services. Stop at its job boundary instead of changing wallets.
    pass


class TaskFailed(AccountStop):
    """A confirmed rejection or terminal task failure, safe to consider for fallback."""
    def __init__(self, provider, receipt, task):
        self.code = str(receipt.get("failure_code") or "")
        self.reason = receipt.get("failure_message") or ""
        self.task_id, self.rejected, self.credits = receipt.get("id"), bool(receipt.get("rejected")), receipt.get("credits")
        self.category = receipt.get("failure_category") or failure_category(self.code, self.reason)
        self.fallback_allowed = provider == "kie" and task == "images" and self.category in ("moderation", "model_failure")
        action = (" Review this image prompt or choose another image model in Setup & accounts." if self.category == "moderation" and task == "images"
                  else " Review this narration text in your provider log." if self.category == "moderation" and task == "narration"
                  else " Top up this account, then resume." if self.category == "balance"
                  else " Kie charged nothing for it. Wait a few minutes and press Resume saved project, or voice this video "
                       "another way (Gemini through Kie, your own ElevenLabs account, or your own recording)."
                  if fresh_attempt_ok(provider, task, receipt) and task == "narration"
                  else " Kie charged nothing for it. Wait a few minutes, then press Resume saved project to try again."
                  if fresh_attempt_ok(provider, task, receipt)
                  else " Wait a few minutes, then press Resume saved project to try again."
                  if fresh_attempt_ok(provider, task, receipt, asked=True)
                  else " Check its log; no other wallet was used.")
        prefix = f"{PROVIDERS[provider]['label']} " + (f"refused this {task} request." if receipt.get("rejected") else f"failed this {task} task.")
        if (provider == "kie" and task == "narration" and receipt.get("id") and not receipt.get("rejected")
                and self.code == "422" and self.reason.strip().casefold() == "generate playground failed, task id is blank"):
            prefix = "Kie.ai's narration service failed after accepting the request. CR saved a valid task ID; Kie's upstream task ID was missing."
            action = " No retry was submitted. Create a new attempt or select another narration model in Setup & accounts."
            if receipt.get("credits") == 0:
                action = " Kie reported 0 credits consumed." + action
        super().__init__(prefix +
                         (" " + self.reason if self.reason else "") + action)


class ModelsExhausted(AccountStop):
    """Every image model chosen in Setup failed this one picture, each for certain (a refusal or a failed task).
    free: every one of those attempts was refused up front or charged 0 credits."""
    free = False


class PictureFailed(RuntimeError):
    """One picture or clip that could not be made, for certain: the engine skips it, as it does on its own services
    (a story holds the picture before; more than a third missing still stops the video; an AI image becomes stock
    footage; a thumbnail is left out). Nothing is submitted again for it."""


# A failure that would stop every picture the same way stops the video; anything else stops only its own picture.
# A rate limit stops it too: a burst of 429s across the parallel picture slots would otherwise skip many pictures,
# and a failed picture's receipt is never submitted again on resume (review, 2026-10-26).
ACCOUNT_WIDE = ("balance", "access", "rate_limit")

# This run's pictures and clips whose failure was charged, or not known to be free: never bought again in this run.
# The engine gives a failed picture one "plainer" try on the same file, and the presenter's set-ups try other models
# under new file names with the same prompt, so a picture is known by its file and by its prompt (review, 2026-10-26).
_settled = set()
_settled_lock = threading.Lock()


def _free(failed) -> bool:
    """Certain that this failure cost nothing: Kie refused it up front, or reported 0 credits for every attempt."""
    if isinstance(failed, TaskFailed):
        credits = failed.credits
        return bool(failed.rejected) or (isinstance(credits, (int, float)) and not isinstance(credits, bool)
                                         and credits == 0)
    return bool(getattr(failed, "free", False))


def _picture_keys(args, kwargs, prompt_at, dest_at):
    dest = kwargs.get("dest", args[dest_at] if len(args) > dest_at else None)
    prompt = kwargs.get("prompt", args[prompt_at] if len(args) > prompt_at else None)
    keys = set()
    if dest:
        keys.add(("file", os.path.normcase(os.path.abspath(str(dest)))))
    if isinstance(prompt, str) and prompt.strip():
        keys.add(("prompt", " ".join(prompt.split())))
    return keys


def skippable(make, *, prompt_at=0, dest_at=1):
    """`make` as the engine's own picture and clip calls meet it. AccountStop is a BaseException so the engine can't
    swap wallets behind it, but that also went past every "one picture failing must not abort the video" handler in
    the engine: one picture Kie couldn't make (a refusal on every model, or Kie's "422 task id is blank", 2 of 266
    of the owner's own pictures) stopped a whole video. A 6-minute stick-figure story draws about 140 pictures, so
    most long story videos stopped (cr-stick-stories 61% failed, sweep 2026-10-25). Certain, single-picture failures
    are now the engine's to skip; balance and access problems, and anything uncertain (a pending task, an
    interrupted submission or transfer), still stop the video with their own message and receipt."""
    def made(*args, **kwargs):
        keys = _picture_keys(args, kwargs, prompt_at, dest_at)
        with _settled_lock:
            if keys & _settled:
                raise PictureFailed("This picture already failed once in this video and may have been charged, so it "
                                    "is not bought again: the video goes on without it.")
        try:
            return make(*args, **kwargs)
        except TaskFailed as failed:
            if failed.category in ACCOUNT_WIDE:
                raise
            if not _free(failed):
                with _settled_lock:
                    _settled.update(keys)
            raise PictureFailed(str(failed)) from None
        except ModelsExhausted as stopped:
            if not _free(stopped):
                with _settled_lock:
                    _settled.update(keys)
            raise PictureFailed(str(stopped)) from None
    made._cr_skippable = True
    return made


TRANSIENT = re.compile(r"internal error|try again later|timed? ?out|temporarily|overloaded|busy|service unavailable", re.I)


def fresh_attempt_ok(provider, task, receipt, *, asked=False) -> bool:
    """A narration task Kie took, ran and then failed on its own side (a server error such as "Internal Error, please
    try again later") and charged nothing for: submitting the same narration again cannot pay twice. Rejections,
    moderation, balance and account problems, a missing task ID, and any failure with credits taken keep stopping
    the video, as do every image, motion and avatar task (images have their own model fallbacks).

    asked: the creator pressed Resume (a new run met the failed receipt). Kie's answer is then enough even when it
    didn't say what the failed task cost: it produced nothing, so a new task can't pay twice for the same voice.
    Studio's own automatic retries need Kie to report 0 credits."""
    if provider != "kie" or task != "narration" or not isinstance(receipt, dict):
        return False
    if receipt.get("status") != "failed" or receipt.get("rejected") or not receipt.get("id"):
        return False
    credits = receipt.get("credits")
    if isinstance(credits, bool) or (credits is not None and not isinstance(credits, (int, float))):
        return False
    if credits is None and not asked or credits is not None and credits != 0:
        return False
    code, reason = str(receipt.get("failure_code") or ""), str(receipt.get("failure_message") or "")
    if failure_category(code, reason) in ("moderation", "balance", "access", "rate_limit", "cancelled"):
        return False
    if code == "422" and "task id is blank" in reason.casefold():
        return False                                          # explained separately; a new attempt is the creator's call
    return code in ("500", "501", "502", "503", "504") or bool(TRANSIENT.search(reason))


def failure_category(code, reason):
    text = str(reason).lower()
    if str(code) == "cancelled":
        return "cancelled"
    if str(code) in ("401", "403") or any(w in text for w in ("api key", "unauthorized", "permission")):
        return "access"
    if str(code) == "402" or any(w in text for w in ("insufficient", "balance", "credits", "credit", "quota")):
        return "balance"
    if str(code) == "429" or "rate limit" in text:
        return "rate_limit"
    if any(w in text for w in ("sensitive", "moderation", "content policy", "safety", "nsfw")):
        return "moderation"
    if str(code) in ("500", "502", "503", "504"):
        return "model_failure"
    return "request"


def profile(task=None):
    if task == "motion" and getattr(_routing, "native_motion", False):
        return {"provider": "kie", "tasks": ["motion"]}
    if task == "narration" and getattr(_routing, "narration", None):
        return _routing.narration                    # this video's own voice choice (job_voice), while narrate runs
    settings = cr_bridge.load_settings()
    images = settings.get("images") or {}
    if task == "images" and images.get("provider") == "kie":
        return {**images, "tasks": ["images"]}
    voice = settings.get("narration") or {}
    if task == "narration" and voice.get("provider") in ("kie", "atlas", "original"):
        return {**voice, "tasks": ["narration"]} if voice["provider"] != "original" else {}
    cfg = settings.get("media_hub") or {}
    if task == "images" and "images" not in cfg.get("tasks", []):
        # Repair only a Kie image provider already selected by the native engine.
        # A Kie voice key alone must never replace another image provider.
        if _native_images and _native_images() == "kie":
            return {"provider": "kie", "tasks": ["images"], "model": os.getenv("KIE_IMAGE_MODEL", "google/nano-banana")}
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
            # The code says which refusal it was (kie: 455 unavailable, 500 server error, 501 generation failed...);
            # the provider's own wording stays out of the message.
            code = re.sub(r"[^0-9A-Za-z_-]", "", str(data.get("code")))[:12]
            raise AccountStop(f"{PROVIDERS[provider]['label']} refused this request (code {code}). Check its API access, "
                              "balance and task log.")
        return data
    except (ValueError, TypeError):
        raise AccountStop("The model account returned an unreadable response. Check its task log.")


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
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
                        raise AccountStop("The generated file is larger than Studio's 512 MB download limit.")
                    f.write(chunk)
            if not size:
                raise AccountStop("The model account returned an empty media file.")
        tmp.replace(dest)
    except requests.RequestException:
        raise AccountStop("The file transfer stopped. Resume to fetch the existing task; no new generation is needed.")
    finally:
        tmp.unlink(missing_ok=True)


# Uploads (reference pictures, clips) create nothing paid, so a dropped or timed-out one is simply sent again. A creator
# on a slow connection saw a large clip fail every time: one request, a fixed three minutes, no second try (2026-10-24).
UPLOAD_TRIES, UPLOAD_WAITS = 3, (10, 30)       # three tries; seconds before the second and the third
SLOW_UPLINK = 32 * 1024                        # bytes a second a slow home connection still sends


def upload_timeout(size):
    """(connect, read) seconds for an upload of `size` bytes: room for a slow connection to send it and the host to
    answer, never under three minutes, at most an hour."""
    return (15, min(3600, max(180, int(size / SLOW_UPLINK) + 120)))


def _upload_failed(path, size, provider, why):
    megabytes = size / (1024 * 1024)
    return AccountStop(f"The upload of {path.name} ({megabytes:.1f} MB) to {PROVIDERS[provider]['label']} didn't finish "
                       f"after {UPLOAD_TRIES} tries ({why}). Nothing was charged. A slow or unstable connection may not "
                       "send a file this size in time: press Resume saved project to try again, on a faster or wired "
                       "connection if you can.")


def receipt_name(name: str, fingerprint: str) -> str:
    """One digest of the file's name and its task (21 characters): a receipt for each file, and for each request."""
    return hashlib.sha256((name + "\0" + fingerprint).encode("utf-8")).hexdigest()[:16] + ".json"


def task_receipt(dest, fingerprint) -> Path:
    """Where a paid media task's receipt lives: beside its file, in .cr-tasks. Named by a digest of the file's name,
    not the name itself: beside a long picture name (a collage's) the receipt doubled the path, and Windows stops at
    260 characters (cr_paths). A receipt saved under the old name is still the one used, so nothing is made twice."""
    dest = Path(dest)
    legacy = dest.parent / ".cr-tasks" / (dest.name + "-" + fingerprint[:24] + ".json")
    return legacy if legacy.is_file() else dest.parent / ".cr-tasks" / receipt_name(dest.name, fingerprint)


def upload(path, cache_file=None, *, task=None):
    provider, key = account(task)
    path = Path(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    marker = path.parent / ".cr-tasks" / f"up-{provider}-{digest[:16]}.json"     # short: Windows stops at 260
    # Uploads expire. Reuse for at most 12 hours, not indefinitely.
    with _lock:
        if marker.is_file():
            previous = json.loads(marker.read_text(encoding="utf-8"))
            if previous.get("created_at", 0) > time.time() - 43200:
                _references[previous["url"]] = {"upload_sha256": digest}
                return _url(previous["url"])
    # The upload itself runs outside the lock: a slow one must not hold up the pictures being made beside it.
    headers = {"Authorization": "Bearer " + key}
    url = ("https://kieai.redpandaai.co/api/file-stream-upload" if provider == "kie"
           else "https://api.atlascloud.ai/api/v1/model/uploadMedia")
    size = path.stat().st_size
    why = ""
    for attempt in range(UPLOAD_TRIES):
        if attempt:
            wait = UPLOAD_WAITS[min(attempt, len(UPLOAD_WAITS)) - 1]
            print(f"The upload of {path.name} stopped ({why}); trying again in {wait} s "
                  f"(try {attempt + 1} of {UPLOAD_TRIES}).", flush=True)
            time.sleep(wait)
        try:
            with path.open("rb") as f:
                res = requests.post(url, headers=headers, files={"file": (digest + path.suffix, f, mimetypes.guess_type(path.name)[0])},
                                    data={"uploadPath": "channelrecipe", "fileName": digest + path.suffix} if provider == "kie" else None,
                                    timeout=upload_timeout(size), allow_redirects=False)
        except requests.ConnectTimeout:
            why = "the upload host didn't answer"
            continue
        except requests.Timeout:
            why = "the connection was too slow to finish in time"
            continue
        except requests.ConnectionError as error:      # a slow send that timed out arrives as "write operation timed out"
            why = ("the connection was too slow to finish in time" if "timed out" in str(error).lower()
                   else "the connection dropped")
            continue
        except requests.RequestException:
            raise AccountStop("The reference upload stopped. Check your model account connection.")
        if res.status_code == 429 or res.status_code >= 500:
            why = f"the upload host was busy (HTTP {res.status_code})"
            continue
        data = _data(res, provider)
        value = ((data.get("data") or {}).get("downloadUrl") if provider == "kie" else
                 data.get("url") or (data.get("data") or {}).get("url"))
        value = _url(value)
        with _lock:
            _write(marker, {"url": value, "created_at": time.time()})
            _references[value] = {"upload_sha256": digest}
        return value
    raise _upload_failed(path, size, provider, why)


def _never_sent(error) -> bool:
    """Whether a failed request certainly never reached the provider: the connection was not opened."""
    if isinstance(error, requests.exceptions.ConnectTimeout):
        return True
    text = repr(error)
    return any(sign in text for sign in ("NewConnectionError", "NameResolutionError", "Failed to establish a new connection"))


def run(task, inputs, dest, *, model=None, log=print, timeout_s=1800):
    """One media task, from its receipt when it was already made. A narration Kie ran and then failed on its own side,
    charging nothing (fresh_attempt_ok: "Internal Error", "generate task timeout"), is submitted again after
    NARRATION_RETRY_PAUSES for every caller. Only the long-video voice used to ride these out; the Shorts call run()
    directly, so one of them stopped the whole Short ("The Short stopped unexpectedly (cr_media.TaskFailed: Kie.ai failed
    this narration task. generate task timeout. Kie charged nothing for it ...)", Photo Comedy Shorts, sweep
    2026-10-26)."""
    pauses = NARRATION_RETRY_PAUSES if task == "narration" else ()
    for attempt, pause in enumerate(pauses + (None,)):
        try:
            return _run_once(task, inputs, dest, model=model, log=log, timeout_s=timeout_s)
        except TaskFailed as failed:
            receipt = {"status": "failed", "id": getattr(failed, "task_id", None), "rejected": failed.rejected,
                       "credits": failed.credits, "failure_code": failed.code, "failure_message": failed.reason}
            provider = account(task)[0]
            if pause is None or not fresh_attempt_ok(provider, task, receipt):
                raise
            log(f"{PROVIDERS[provider]['label']} {task} hit a passing error ({failed.reason or failed.code}) and charged "
                f"nothing; trying again in {pause} s (attempt {attempt + 2} of {len(pauses) + 1}).")
            time.sleep(pause)


def _run_once(task, inputs, dest, *, model=None, log=print, timeout_s=1800):
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
    marker = task_receipt(dest, fingerprint)
    headers = {"Authorization": "Bearer " + key}
    base = "https://api.kie.ai/api/v1/jobs" if provider == "kie" else "https://api.atlascloud.ai/api/v1/model"
    with _slots.get(task, _other_slots):
        with _lock:
            previous = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else {}
            if previous.get("status") == "complete" and dest.is_file() and previous.get("file_sha256") == hashlib.sha256(dest.read_bytes()).hexdigest():
                log(f"cached model task: {dest.name}")
                _references[previous["url"]] = {"task_fingerprint": fingerprint}
                return previous["url"]
            if previous.get("status") == "failed":
                # A run that meets a failed receipt was started by the creator (Resume, Carry on): never by Studio.
                if not fresh_attempt_ok(provider, task, previous, asked=True):
                    raise TaskFailed(provider, previous, task)
                kept = marker.with_name(marker.stem + f".failed-{int(time.time() * 1000)}.json")
                marker.replace(kept)                         # the failed receipt stays, under its own name
                log(f"{PROVIDERS[provider]['label']} failed task {previous.get('id')} charged nothing; trying again.")
                previous = {}
            if previous and not previous.get("id"):
                raise AccountStop("This media submission has an uncertain result. Check your provider task log and the .cr-tasks receipt before deliberately retrying.")
            if not previous:
                previous = {"provider": provider, "model": model, "status": "submitting", "created_at": time.time()}
                _write(marker, previous)  # Crash before ID is recorded is uncertain, never auto-resubmit.
                suffix = "/createTask" if provider == "kie" else {"images": "/generateImage", "narration": "/generateAudio", "motion": "/generateVideo", "avatars": "/generateVideo"}[task]
                try:
                    for attempt in (1, 2, 3):
                        try:
                            res = requests.post(base + suffix, headers=headers, json=payload, timeout=(15, 90),
                                                allow_redirects=False)
                            break
                        except requests.ConnectionError as error:
                            # A connection that never opened sent nothing, so nothing was created: a network blip
                            # at that moment no longer stops the video. Anything after sending stays uncertain.
                            previous["submit_error"] = type(error).__name__
                            if attempt == 3 or not _never_sent(error):
                                _write(marker, previous)
                                raise
                            time.sleep(5 * attempt)
                    # Only an explicit client rejection is known not to have
                    # created a job. A 5xx/timeout without an ID stays uncertain.
                    if provider == "kie":
                        try:
                            rejected = res.json()
                            code = str(rejected.get("code") or res.status_code)
                            if code in ("400", "401", "402", "403", "422", "429") and not (rejected.get("data") or {}).get("taskId"):
                                reason = re.sub(r"\s+", " ", str(rejected.get("msg") or rejected.get("message") or "")).replace(key, "[redacted]")[:200]
                                category = failure_category(code, reason)
                                previous.update(status="failed", rejected=True, failure_code=code,
                                                failure_category=category,
                                                failure_message=reason if category == "moderation" else "", credits=0)
                                _write(marker, previous)
                                raise TaskFailed(provider, previous, task)
                        except (ValueError, TypeError, AttributeError):
                            pass
                    try:
                        data = _data(res, provider)
                    except AccountStop as stop:
                        previous["submit_error"] = str(stop)[:160]      # which refusal, kept with the receipt
                        _write(marker, previous)
                        raise
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
                    previous.update(failure_message=reason, failure_code=("cancelled" if state in ("cancelled", "canceled") else str(detail.get("failCode") or "")[:40]),
                                    credits=detail.get("creditsConsumed"))
                    _write(marker, previous)
                    raise TaskFailed(provider, previous, task)
            except requests.RequestException:
                pass  # Read-only task poll; keep the original ID.
            except (ValueError, TypeError, AttributeError, KeyError):
                raise AccountStop("The task status could not be read. Resume to check the same provider task.")
            time.sleep(5)
        raise AccountStop("The media task is still pending. Its ID is saved; resume this job to check it without submitting again.")


def image_model(model):
    for choice in PROVIDERS["kie"]["image_models"]:
        if model in (choice["id"], choice["generate"], choice["edit"]):
            return choice
    raise AccountStop("Choose a supported Kie image model in Setup & accounts.")


def picture_range(style: dict):
    """(floor, default, ceiling) pictures a minute a story recipe allows, or None for other recipes. The floor
    keeps quality: never fewer than half the recipe's own pace, nor under four a minute."""
    if (((style or {}).get("look") or {}).get("visuals")) == "cr_animated2d":
        return None                                   # animated in code: no pictures a minute to choose
    try:
        every = float(((style or {}).get("pacing") or {}).get("photo_every_s") or 0)
    except (TypeError, ValueError):
        return None
    if every <= 0:
        return None
    default = round(60.0 / every, 1)
    return max(4.0, round(default * 0.5, 1)), default, min(30.0, round(default * 1.5, 1))


def pictures_per_min(job) -> float:
    try:
        spec = json.loads((Path(job) / "run_spec.json").read_text(encoding="utf-8"))
        return float(spec.get("cr_pictures_per_min") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return 0.0


# The picture model the current job's style asks for (look.image_model). Jobs run one per worker process.
_style_model = {"id": ""}


def image_plan(cfg, prefer=None):
    """The image models to try, in order. A style's own model (prefer) goes first while the creator keeps the
    default model in Setup; a model the creator chose there always wins."""
    default = image_model(PROVIDERS["kie"]["models"]["images"])["id"]
    primary = image_model(cfg.get("model") or default)["id"]
    fallbacks = cfg.get("fallbacks", PROVIDERS["kie"]["image_fallbacks"])
    if not isinstance(fallbacks, list) or len(fallbacks) > 4:
        raise AccountStop("Choose at most four image fallbacks in Setup & accounts.")
    plan = [primary] + [image_model(m)["id"] for m in fallbacks]
    if prefer and primary == default:
        try:
            plan.insert(0, image_model(prefer)["id"])
        except AccountStop:
            pass                                   # a model this Studio doesn't offer: keep the creator's plan
    return list(dict.fromkeys(plan))


def image_input(choice, prompt, refs):
    inputs = {"prompt": prompt[:5000], "aspect_ratio": "16:9", **choice["options"]}
    if refs:
        inputs[choice["reference_field"]] = [_url(url) for url in refs[:8]]
    return choice["edit" if refs else "generate"], inputs


def image(prompt, dest, label="image", ref_urls=None, model=None, **kwargs):
    cfg = profile("images")
    provider = cfg["provider"]
    refs = list(ref_urls or [])
    wait = {"timeout_s": kwargs["timeout_s"]} if kwargs.get("timeout_s") else {}   # a caller's own limit on waiting
    if provider != "kie":
        inputs = {"prompt": prompt[:5000], "aspect_ratio": "16:9", "output_format": "png"}
        model = PROVIDERS[provider]["models"]["image_edit" if refs else "images"]
        if refs:
            inputs["images"] = refs[:8]
        return run("images", inputs, dest, model=model, **wait)
    last, free = None, True
    plan = image_plan(cfg, prefer=model or _style_model["id"])
    for index, model_id in enumerate(plan):
        choice = image_model(model_id)
        model, inputs = image_input(choice, prompt, refs)
        try:
            # Each model/prompt/wallet has its own durable receipt. Resuming
            # skips confirmed failures and reuses the successful fallback.
            return run("images", inputs, dest, model=model, **wait)
        except TaskFailed as error:
            if not error.fallback_allowed:
                raise
            last, free = error, free and _free(error)
            if index + 1 < len(plan):
                print(f"  {label}: {choice['label']} failed ({error.category}); trying {image_model(plan[index+1])['label']} on the same Kie account.")
    exhausted = ModelsExhausted(f"Image generation stopped after {len(plan)} selected models. {last} Saved attempts will not be submitted again on resume.")
    exhausted.free = free
    raise exhausted


def motion(image_url, prompt, dest, label="motion", retries=1, **kwargs):
    provider = profile("motion")["provider"]
    if provider == "kie":
        # V1 Pro Fast has a different schema from Seedance 1.5. Keep the
        # five-second cost choice fixed even for a longer timeline slot.
        inputs = {"prompt": prompt[:10000], "image_url": image_url,
                  "resolution": "720p", "duration": "5"}
    else:
        inputs = {"prompt": prompt[:2000], "image": image_url, "aspect_ratio": "16:9",
                  "resolution": "720p", "duration": 8}
    return run("motion", inputs, dest, log=kwargs.get("log", print))


@contextmanager
def native_motion_account():
    """A native Kie call selects Kie unless motion was explicitly replaced.

    Scope this to the calling thread: having a Kie voice key must not switch
    unrelated WaveSpeed jobs or uploads to Kie.
    """
    previous = getattr(_routing, "native_motion", False)
    if not active("motion"):
        _routing.native_motion = True
    try:
        yield
    finally:
        _routing.native_motion = previous


def native_motion(*args, **kwargs):
    with native_motion_account():
        return motion(*args, **kwargs)


# The engine's own words for how a presenter moves (avatar.MOTION_PROMPT), used when the engine can't be read.
PRESENTER_MOTION = ("The person speaks naturally to camera, subtle head movement, natural blinking, relaxed shoulders, "
                    "small hand gesture, static camera.")


def presenter_prompt(motion=""):
    """What Kie's Kling avatar models are told about the presenter's movement. Kie requires it: without a prompt
    every presenter was refused ({"code": 500, "msg": "prompt is required"}), so every video with an AI presenter on
    a Kie account stopped at its intro (sweep 2026-10-25). The engine's own Kling call always sent one."""
    try:
        import avatar
        base = str(getattr(avatar, "MOTION_PROMPT", "") or PRESENTER_MOTION)
    except Exception:                                   # noqa: BLE001 - the engine's words are a nicety, not a need
        base = PRESENTER_MOTION
    motion = str(motion or "").strip()
    return (((motion + " ") if motion else "") + base)[:5000]


def avatar_clip(provider, image_url, audio_url, dest, log=print, motion=""):
    if provider not in ("kling-standard", "kling-pro"):
        # Setup's "Kling Standard / Pro presenters": every presenter on this account is Kling. A recipe written for
        # another presenter model (Pruna on WaveSpeed) speaks through Kling Standard, as the engine itself does when
        # that service can't render it, instead of stopping the video.
        log(f"    avatar: {provider} presenter shots run as Kling Standard on your model account")
        provider = "kling-standard"
    hub = profile()["provider"]
    inputs = ({"image_url": image_url, "audio_url": audio_url, "prompt": presenter_prompt(motion)} if hub == "kie"
              else {"image": image_url, "audio": audio_url})
    model = PROVIDERS[hub]["models"]["avatar_pro" if provider == "kling-pro" else "avatars"]
    run("avatars", inputs, dest, model=model, log=log)
    return Path(dest)


def route_avatar(original):
    """Forward native positional options when presenter replacement is off."""
    def routed(provider, image_url, audio_url, dest, log=print, *args, **kwargs):
        if active("avatars"):
            # the engine's presenter calls pass a shot's movement as their next argument (motion=)
            motion = kwargs.get("motion", args[0] if args and isinstance(args[0], str) else "")
            return avatar_clip(provider, image_url, audio_url, dest, log, motion=motion)
        return original(provider, image_url, audio_url, dest, log, *args, **kwargs)
    return routed


# A narration task Kie failed on its own side and charged nothing for is tried twice more, after these pauses (s).
NARRATION_RETRY_PAUSES = (30, 90)


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


# A video whose voice failed can be voiced another way, for that video only: the choice sits in its job folder and
# Resume reads it here (the saved project's own settings live on ChannelRecipe and stay as they are).
VOICE_CHOICE = "cr-voice.json"           # {"provider": "kie", "model", "voice"} | {"provider": "original"} | {"provider": "own", "file"}
VOICE_FAILED = "cr-voice-failed.json"    # why the last voice attempt stopped (offered choices read it)


def job_voice(job) -> dict:
    """This video's own voice choice, or {} for the voice in Setup & accounts."""
    try:
        choice = json.loads((Path(job) / VOICE_CHOICE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(choice, dict):
        return {}
    if choice.get("provider") == "kie":
        try:
            model = narration_model("kie", choice.get("model"))
        except AccountStop:
            return {}
        voice = str(choice.get("voice") or model["default_voice"])
        if model.get("voice_names") and voice not in model["voice_names"]:
            voice = model["default_voice"]
        return {"provider": "kie", "model": model["id"], "voice": voice, "tasks": ["narration"]}
    if choice.get("provider") == "original":
        return {"provider": "original"}
    if choice.get("provider") == "own" and str(choice.get("file") or "").strip():
        return {"provider": "own", "file": str(choice["file"])}
    return {}


def _voice_failed(job, provider, model, failed) -> None:
    try:
        (Path(job) / VOICE_FAILED).write_text(json.dumps({
            "provider": provider, "label": PROVIDERS.get(provider, {}).get("label", provider), "model": model,
            "category": getattr(failed, "category", ""), "reason": str(getattr(failed, "reason", "") or failed)[:300],
            "charged": getattr(failed, "credits", None), "at": int(time.time())}), encoding="utf-8")
    except OSError:
        pass


def narrate(engine, text, mp3, srt, voice_id="", style=""):
    chosen = job_voice(Path(mp3).parent)
    chosen = chosen if chosen.get("provider") == "kie" else None
    cfg = chosen or profile("narration")
    provider = cfg["provider"]
    model = narration_model(provider, cfg.get("model"))
    voice = cfg.get("voice") or model["default_voice"]
    if model.get("voice_names") and voice not in model["voice_names"]:
        raise AccountStop("Choose a voice name supported by your narration model in Setup & accounts.")
    folder = Path(mp3).parent / (Path(mp3).stem + "-hub-voice")
    folder.mkdir(parents=True, exist_ok=True)
    parts = []
    _routing.narration = chosen                      # run() pays with the chosen account, not Setup's narration one
    try:
        for i, piece in enumerate(_chunks(text)):
            part = folder / f"part-{i:03d}.{model['format']}"
            # run() rides out a passing Kie error that charged nothing (NARRATION_RETRY_PAUSES)
            run("narration", narration_input(piece, voice, model["id"]), part, model=model["id"], log=engine.log)
            parts.append(part)
    except (AccountStop, Exception) as failed:              # what stopped this voice, for the choices offered
        if getattr(failed, "category", "") != "cancelled":
            _voice_failed(Path(mp3).parent, provider, model["id"], failed)
        raise
    finally:
        _routing.narration = None
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
        listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")   # ffmpeg reads UTF-8
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", str(listing), "-c:a", "libmp3lame", "-b:a", "192k", str(mp3)], check=True)
    _time_narration(engine, mp3, srt, text)


def _time_narration(engine, mp3, srt, text):
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
    # UTF-8, as the engine and ffmpeg read it: on Windows the cp1252 default would stop a video at a "café" in its words
    Path(srt).write_text("\n".join(f"{i+1}\n{engine._srt_ts(c[0][1])} --> {engine._srt_ts(c[-1][2])}\n{' '.join(w[0] for w in c)}\n"
                                   for i, c in enumerate(cues)), encoding="utf-8")
    engine._script_words_into_srt(Path(srt), text)


def make_voiceover(engine, original_voice):
    """The engine's voiceover step: this video's own voice choice (VOICE_CHOICE) first, then the narration
    account in Setup & accounts, else the engine's own voices."""
    def voiceover(script, job, force=False, avatar_min=0, style=""):
        chosen = job_voice(job)
        if chosen.get("provider") == "own":
            return own_recording(engine, script, Path(job), chosen["file"], force)
        if chosen.get("provider") == "original" or not (chosen or active("narration")):
            out = original_voice(script, job, force, avatar_min, style)
            (Path(job) / VOICE_FAILED).unlink(missing_ok=True)
            return out
        job = Path(job)
        mp3, srt = job / "audio.mp3", job / "subs.srt"
        if (job / "voice.own").exists() or (mp3.exists() and srt.exists() and not force):
            (job / VOICE_FAILED).unlink(missing_ok=True)
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
        (job / VOICE_FAILED).unlink(missing_ok=True)
        engine._speed_voice(mp3, srt, style)
        engine._regroup_word_srt(srt)
        return engine._denoise_voice(mp3, style), srt
    return voiceover


def own_recording(engine, script, job: Path, file: str, force=False):
    """The creator's own recording as this video's voice, chosen after its AI voice stopped: it becomes the video's
    audio.mp3, timed on this computer and spelled as the script they read. Nothing is paid for it."""
    import voices
    src = voices.resolve(file)
    if src is None:
        raise AccountStop("Your recording for this video isn't there any more. Upload it again, or pick another voice.")
    mp3, srt, mark = job / "audio.mp3", job / "subs.srt", job / "cr-voice-own.txt"
    sig = f"{src.name}:{src.stat().st_size}:{int(src.stat().st_mtime)}"
    if (mp3.exists() and srt.exists() and not force and mark.is_file()
            and mark.read_text(encoding="utf-8").strip() == sig):
        return mp3, srt
    engine.log(f"Your own recording is this video's voice: {src.name}")
    if src.suffix.lower() == ".mp3":
        shutil.copyfile(src, mp3)
    else:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-c:a", "libmp3lame", "-b:a", "192k",
                        str(mp3)], check=True)
    (job / "words_heard.json").unlink(missing_ok=True)
    _time_narration(engine, mp3, srt, script)
    engine._regroup_word_srt(srt)
    mark.write_text(sig + "\n", encoding="utf-8")
    (job / VOICE_FAILED).unlink(missing_ok=True)
    return mp3, srt


def wrap_whisper(engine):
    """The engine's speech-timing model reads narration as PCM, and loads the copy setup downloaded (cr_whisper), so a
    video never asks huggingface.co for it mid-run."""
    original_whisper=getattr(engine,"_get_whisper",None)
    if original_whisper and not getattr(original_whisper,"_cr_pcm_io",False):
        class WhisperIO:
            def __init__(self,model):self.model=model
            def __getattr__(self,name):return getattr(self.model,name)
            def transcribe(self,audio,*args,**kwargs):
                if isinstance(audio,(str,Path)):
                    audio=narration_audio(audio)
                return self.model.transcribe(audio,*args,**kwargs)
        def get_whisper():
            try:
                import cr_whisper
                cr_whisper.point_engine(engine)
            except Exception:                          # noqa: BLE001 - the engine loads it as before
                pass
            return WhisperIO(original_whisper())
        get_whisper._cr_pcm_io=True
        engine._get_whisper=get_whisper


def install(engine):
    """Keep native providers when a task is unticked; resolve settings per call."""
    import cr_watch
    cr_watch.install()
    try:
        import cr_score                     # a learned style's own score, on the creator's Kie account
        import cr_sfx_library               # and ChannelRecipe's recorded effects
        import cr_ambience                  # and the sound of each place under a story's pictures
        import cr_storyboard                # the sound storyboard a scored video follows
        cr_score.install(engine)
        cr_sfx_library.install(engine)
        cr_ambience.install(engine)
        cr_storyboard.install(engine)
    except ImportError:
        pass
    wrap_whisper(engine)
    global _native_images
    _native_images = engine._image_provider
    def hook(name, task, replacement):
        original = getattr(engine, name)
        setattr(engine, name, lambda *a, **k: replacement(*a, **k) if active(task) else original(*a, **k))
    hook("image_gen", "images", skippable(image))
    hook("_image_provider", "images", lambda: "channelrecipe-hub")
    # The first frame of a moving picture is drawn against the channel's style drawing too (look.style_image),
    # like every still, so an animated picture keeps the same look. Jobs run one per worker process.
    current = {"style": ""}
    original_visuals = getattr(engine, "generate_astrology_visuals", None)
    if original_visuals and not getattr(original_visuals, "_cr_style", False):
        def visuals(script, job, force, style="", *args, **kwargs):
            current["style"] = style
            look = ((getattr(engine, "STYLE_INFO", {}) or {}).get(style) or {}).get("look") or {}
            _style_model["id"] = str(look.get("image_model") or "") if isinstance(look, dict) else ""
            # the creator's pictures-per-minute for a story recipe (create page slider), this job only
            every = getattr(engine, "STYLE_PHOTO_EVERY", {})
            ppm = pictures_per_min(job)
            if ppm and style in every:
                before = every[style]
                every[style] = round(60.0 / ppm, 2)
                try:
                    return original_visuals(script, job, force, style, *args, **kwargs)
                finally:
                    every[style] = before
            return original_visuals(script, job, force, style, *args, **kwargs)
        visuals._cr_style = True
        engine.generate_astrology_visuals = visuals

    def still_with_url(prompt, dest, label, retries=4):
        try:
            refs = engine._style_refs(current["style"]) if current["style"] else []
        except Exception:
            refs = []
        note = getattr(engine, "STYLE_REF_NOTE", "") if refs else ""
        return skippable(image)(note + prompt, dest, label, ref_urls=refs)
    hook("_kie_still_with_url", "images", still_with_url)
    try:
        import cr_panels
        import cr_cta
        cr_panels.install(engine)
        import cr_sound_qc
        cr_sound_qc.install(engine)                 # the sound check on every finished video (measured)
        cr_cta.install(engine)                      # your call to action: in the script, on screen, in the description
        import cr_presenters
        cr_presenters.install()                     # your own presenters, listed first
        import cr_drawon
        cr_drawon.install(engine)                   # whiteboard styles: each picture draws itself on
        import cr_threed_look
        cr_threed_look.install(engine)              # the 3D look chosen in Create for one video
        import cr_character
        cr_character.install(engine)                # a new character for one video replaces the recipe's picture
        import cr_style_prompts
        cr_style_prompts.install(engine)            # learned styles draw with today's storyboard prompt
    except ImportError:
        pass
    # Native Kie motion and the history kit previously bypassed the hub.
    # Both must use the same cheap model and the same durable receipts.
    engine._kie_video_gen = skippable(native_motion, prompt_at=1, dest_at=2)   # a clip that can't be made shows its first frame instead
    engine.KIE_VIDEO_MODEL = PROVIDERS["kie"]["models"]["motion"]
    engine.KIE_VIDEO_RES, engine.KIE_VIDEO_DUR = "720p", "5"
    import history_kit
    import cr_history_io
    cr_history_io.install(history_kit)
    hook("_tts_one", "narration", lambda *a, **k: narrate(engine, *a, **k))
    voiceover = make_voiceover(engine, engine.generate_voiceover)
    engine.generate_voiceover = voiceover
    # These modules have independent voice entry points and media uploads.
    import wavespeed
    import vox
    import avatar
    for module in (wavespeed, vox):
        original = module.voiceover
        def routed_voice(eng, script, job, *args, _original=original, **kwargs):
            if active("narration") or job_voice(job):       # Setup's narration account, or this video's own choice
                force = kwargs.get("force", args[-1] if args and isinstance(args[-1], bool) else False)
                style = args[0] if args and isinstance(args[0], str) else ""
                return voiceover(script, job, force=force, style=style)
            return _original(eng, script, job, *args, **kwargs)
        module.voiceover = routed_voice
    original_upload = wavespeed.upload
    def upload_task():
        return next((t for t in ("images", "motion", "avatars") if active(t)), None)
    wavespeed.upload = lambda *a, **k: upload(*a, task=upload_task(), **k) if upload_task() else original_upload(*a, **k)
    original_run = wavespeed.run
    def routed_run(model, payload, dest, *args, **kwargs):
        image_model = any(x in model for x in ("nano-banana", "gpt-image", "seedream", "flux", "qwen-image"))
        if image_model and active("images"):
            refs = payload.get("images") or ([payload["image"]] if payload.get("image") else [])
            skippable(image)(payload.get("prompt", ""), dest, ref_urls=refs)
            return Path(dest)
        if "image-to-video" in model and active("motion"):
            source = payload.get("image") or payload.get("image_url")
            if not source:
                raise AccountStop("This motion model needs a source image. Review the scene before retrying.")
            skippable(motion, prompt_at=1, dest_at=2)(source, payload.get("prompt", ""), dest)
            return Path(dest)
        if (active("images") and image_model) or (active("motion") and ("video" in model or "wan" in model)):
            raise AccountStop("This specialised model needs its original account. Turn off its task replacement in Setup & accounts.")
        return original_run(model, payload, dest, *args, **kwargs)
    wavespeed.run = routed_run
    original_host = engine.host_image
    engine.host_image = lambda path: upload(path, task="images") if active("images") else original_host(path)
    original_kie_upload = engine._kie_upload_image
    engine._kie_upload_image = lambda path: upload(path, task=upload_task()) if upload_task() else original_kie_upload(path)
    original_avatar_host = avatar._host
    avatar._host = lambda path, cache, log=print: upload(path, cache, task="avatars") if active("avatars") else original_avatar_host(path, cache, log)
    for name in ("_kie", "_wavespeed", "_wavespeed_avatar"):
        original = getattr(avatar, name)
        setattr(avatar, name, route_avatar(original))
