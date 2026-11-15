"""
Kie.ai for the website's own AI calls: text, voices, pictures and clips.

Atlas Cloud ran out of credit on 2026-11-13 and every Classic step that used it failed (titles, scripts, niche
analysis, voiceover, thumbnails, storyboard pictures). The owner, 2026-11-15: "use Kie.AI instead of atlas". The Atlas
code paths stay as they were: each asks active() first and hands its request here. A customer's own Atlas key
(BYOK, core.atlas_runtime.use_atlas_key) stays on Atlas; SITE_AI_PROVIDER=atlas switches the site back.

Kie's API (as Studio uses it, local_runtime/cr_media.py): media are tasks (POST /api/v1/jobs/createTask, then
/api/v1/jobs/recordInfo until state is success); Gemini text has OpenAI-compatible routes per model; files to use as
references are uploaded first. Kie answers its own errors with HTTP 200 and a "code" in the body.
"""
from __future__ import annotations

import json
import mimetypes
import time
import uuid
from pathlib import Path

import httpx

import config

KIE = "https://api.kie.ai"
UPLOAD_URL = "https://kieai.redpandaai.co/api/file-stream-upload"
TTS_MODEL = "google/gemini-3-8-flash-tts"
VIDEO_MODEL = "bytedance/v1-pro-fast-image-to-video"
POLL_S = 2.0
OUT_OF_CREDIT = "Kie.ai credits are insufficient for this request (provider balance). Please try again later."

# The site's voices were Atlas's xAI voices; Kie reads with Gemini voices. Each keeps its kind and its tone
# (Google's own words for the Gemini voice in brackets).
GEMINI_VOICES = {
    "leo": "Charon",             # authoritative narrator (informative)
    "rex": "Orus",               # professional (firm)
    "sal": "Schedar",            # neutral, versatile (even)
    "ara": "Sulafat",            # warm conversational (warm)
    "eve": "Laomedeia",          # energetic, upbeat (upbeat)
    "78a495fdbb39": "Puck",      # James: young, energetic (upbeat)
    "96819d0bd28d": "Gacrux",    # Daniel: mature (mature)
    "f8cf5c2c78d4": "Erinome",   # Grace: clear (clear)
    "79f3a8b96d43": "Kore",      # Claire: steady (firm)
}
GEMINI_NAMES = frozenset(GEMINI_VOICES.values()) | {
    "Zephyr", "Aoede", "Fenrir", "Leda", "Rasalgethi", "Achird", "Iapetus", "Algieba", "Despina", "Enceladus",
    "Sadaltager", "Vindemiatrix", "Zubenelgenubi", "Alnilam", "Algenib", "Autonoe", "Callirrhoe", "Umbriel",
    "Achernar", "Pulcherrima", "Sadachbia",
}
DEFAULT_VOICE = "Charon"

# Atlas image model families -> Kie: (text-to-image model, edit model, the references' field, the shape's field, fixed
# options). Timed on Kie 2026-11-15, one 16:9 thumbnail each: Nano Banana 2 104 s, 2.1 30 s (and the best looking),
# Pro 36 s, GPT Image 2 54 s, the base Nano Banana 15 s ($0.02). Atlas's Nano Banana 2 / lite become 2.1 (Thumbnail
# Studio has 75 s); "fast" stands in for Atlas's free ERNIE stills (many per video).
IMAGE_MODELS = {
    "nano-banana-2": ("nano-banana-2-1", "nano-banana-2-1", "image_input", "aspect_ratio",
                      {"resolution": "1K", "output_format": "png"}),
    "nano-banana-pro": ("nano-banana-pro", "nano-banana-pro", "image_input", "aspect_ratio",
                        {"resolution": "1K", "output_format": "png"}),
    "gpt-image-2": ("gpt-image-2-text-to-image", "gpt-image-2-image-to-image", "input_urls", "aspect_ratio",
                    {"resolution": "1K", "background": "opaque"}),
    "fast": ("google/nano-banana", "google/nano-banana-edit", "image_urls", "image_size", {"output_format": "png"}),
}


class KieError(RuntimeError):
    """Kie refused or failed a request; the message never carries the key."""


def key() -> str:
    return (getattr(config, "KIE_API_KEY", "") or "").strip()


def active() -> bool:
    """Whether the site's AI calls go to Kie now: a Kie key, not switched back, and no customer's own Atlas key."""
    from core.atlas_runtime import _atlas_override
    if (_atlas_override.get() or "").strip():
        return False
    return bool(key()) and (getattr(config, "SITE_AI_PROVIDER", "kie") or "kie") != "atlas"


def _headers() -> dict:
    return {"Authorization": "Bearer " + key()}


def _refused(data, what: str) -> KieError:
    """The plain error for a Kie answer with a failing code; out of credit says "provider balance" (a 503 + alert)."""
    code = str((data or {}).get("code") if isinstance(data, dict) else "")
    msg = str((data or {}).get("msg") or (data or {}).get("message") or "") if isinstance(data, dict) else str(data)
    if code == "402" or any(w in msg.lower() for w in ("insufficient", "not enough credit", "balance")):
        return KieError(OUT_OF_CREDIT)
    return KieError(f"Kie.ai {what} failed (code {code or '?'}): {msg[:200]}".replace(key() or "\0", "[key]"))


def _json(resp: httpx.Response, what: str) -> dict:
    try:
        data = resp.json()
    except ValueError:
        raise KieError(f"Kie.ai {what}: unreadable answer (HTTP {resp.status_code})") from None
    if not isinstance(data, dict):
        raise KieError(f"Kie.ai {what}: unreadable answer (HTTP {resp.status_code})")
    return data


def chat(prompt: str, *, system: str | None = None, max_tokens: int = 8192, temperature: float | None = None,
         timeout: float = 180) -> str:
    """One text answer from Kie's Gemini (KIE_TEXT_MODEL), the way core.atlas_llm.generate_text answers."""
    model = (getattr(config, "KIE_TEXT_MODEL", "") or "gemini-3.8-flash").strip()
    url = f"{KIE}/{model.replace('.', '-')}-openai/v1/chat/completions"
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body: dict = {"messages": messages, "stream": False, "max_tokens": max(256, int(max_tokens)),
                  "include_thoughts": False, "reasoning_effort": "low"}
    if temperature is not None:
        body["temperature"] = temperature
    last: Exception = KieError("Kie.ai text came back empty")
    for attempt in range(4):
        if attempt:
            time.sleep(min(10, 2 * attempt))
        try:
            resp = httpx.post(url, headers=_headers(), json=body, timeout=timeout)
        except httpx.HTTPError as e:
            last = KieError(f"Kie.ai text: {type(e).__name__}")
            continue
        if resp.status_code in (429, 500, 502, 503, 504):
            last = KieError(f"Kie.ai text: HTTP {resp.status_code}")
            continue
        data = _json(resp, "text")
        if not data.get("choices"):
            err = _refused(data, "text")
            if str(data.get("code")) in ("429", "500", "502", "503"):
                last = err
                continue
            raise err
        choice = data["choices"][0] or {}
        content = (choice.get("message") or {}).get("content") or ""
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if str(content).strip():
            return str(content)
        last = KieError(f"Kie.ai text came back empty (finish_reason={choice.get('finish_reason')})")
        if choice.get("finish_reason") == "length":        # hidden thinking used the budget: once more with room
            body["max_tokens"] = min(32768, body["max_tokens"] * 2)
    raise last


def openai_target(url: str, headers: dict, body: dict) -> tuple[str, dict, dict]:
    """An OpenAI-shaped chat call meant for Atlas (url, headers, body), sent to Kie's Gemini instead when the site uses
    Kie; unchanged otherwise. The answer has the same shape (choices[0].message), images as data URIs included."""
    if not active():
        return url, headers, body
    model = (getattr(config, "KIE_TEXT_MODEL", "") or "gemini-3.8-flash").strip()
    kie_body = {k: v for k, v in body.items() if k != "model"}
    kie_body.update(stream=False, include_thoughts=False, reasoning_effort="low")
    return (f"{KIE}/{model.replace('.', '-')}-openai/v1/chat/completions",
            {**_headers(), "Content-Type": "application/json"}, kie_body)


def upload(path: str | Path, *, timeout: float = 60) -> str:
    """A local file on Kie's file host; its URL (kept there a few days)."""
    path = Path(path)
    name = f"{uuid.uuid4().hex}{path.suffix.lower()}"
    with path.open("rb") as f:
        resp = httpx.post(UPLOAD_URL, headers=_headers(), timeout=timeout,
                          files={"file": (name, f, mimetypes.guess_type(path.name)[0] or "application/octet-stream")},
                          data={"uploadPath": "channelrecipe-site", "fileName": name})
    data = _json(resp, "upload")
    url = (data.get("data") or {}).get("downloadUrl") if str(data.get("code")) == "200" else ""
    if not url:
        raise _refused(data, "upload")
    return url


def run_task(model: str, inputs: dict, *, timeout: float = 120, out_path: str | Path | None = None) -> str:
    """One Kie media task, start to finish within timeout seconds: its first result URL (saved to out_path if given)."""
    deadline = time.monotonic() + timeout
    resp = httpx.post(f"{KIE}/api/v1/jobs/createTask", headers=_headers(), json={"model": model, "input": inputs},
                      timeout=max(5.0, min(60.0, deadline - time.monotonic())))
    data = _json(resp, model)
    task = (data.get("data") or {}).get("taskId") if str(data.get("code")) == "200" else None
    if not task:
        raise _refused(data, model)
    while time.monotonic() < deadline:
        time.sleep(POLL_S)
        try:
            poll = httpx.get(f"{KIE}/api/v1/jobs/recordInfo", headers=_headers(), params={"taskId": task},
                             timeout=max(5.0, min(30.0, deadline - time.monotonic())))
        except httpx.HTTPError:
            continue                                        # asking about the same task again pays nothing
        if poll.status_code == 429 or poll.status_code >= 500:
            continue
        detail = _json(poll, model).get("data") or {}
        state = str(detail.get("state") or "")
        if state == "success":
            urls = json.loads(detail.get("resultJson") or "{}").get("resultUrls") or []
            if not urls:
                raise KieError(f"Kie.ai {model} finished without a file (task {task})")
            if out_path:
                got = httpx.get(urls[0], timeout=max(5.0, min(60.0, deadline - time.monotonic() + 30)),
                                follow_redirects=True)
                got.raise_for_status()
                Path(out_path).parent.mkdir(parents=True, exist_ok=True)
                Path(out_path).write_bytes(got.content)
            return urls[0]
        if state in ("fail", "failed"):
            raise _refused({"code": detail.get("failCode"), "msg": detail.get("failMsg") or "generation failed"},
                           model)
    raise TimeoutError(f"Kie.ai {model} took longer than {timeout:.0f} s (task {task})")


def gemini_voice(voice: str) -> str:
    """The Gemini voice that reads for one of the site's voices (an xAI id or name, or a Gemini name)."""
    v = (voice or "").split(" -- ")[0].strip()
    if v in GEMINI_NAMES:
        return v
    from core.voiceover_gen import _ATLAS_VOICE_MAP
    xai = _ATLAS_VOICE_MAP.get(v, _ATLAS_VOICE_MAP.get(v.lower(), ""))
    return GEMINI_VOICES.get(xai, GEMINI_VOICES.get(v.lower(), DEFAULT_VOICE))


def tts(text: str, voice: str, *, timeout: float = 240) -> str:
    """Kie's Gemini voice reading text: the audio's URL."""
    inputs = {"temperature": 1, "speakers": [{"speaker_id": "Speaker 1", "voice_name": gemini_voice(voice)}],
              "dialogue_turns": [{"speaker_id": "Speaker 1", "text": text}]}
    return run_task(TTS_MODEL, inputs, timeout=timeout)


def image_family(atlas_model: str) -> str:
    m = (atlas_model or "").lower()
    if "gpt-image" in m:
        return "gpt-image-2"
    if "nano-banana-pro" in m:
        return "nano-banana-pro"
    return "nano-banana-2"


def image(prompt: str, out_path: str | Path, *, refs=(), aspect_ratio: str = "16:9", family: str = "nano-banana-2",
          timeout: float = 120) -> str:
    """One picture on Kie into out_path (references: URLs, or local files uploaded first); the result URL."""
    generate, edit, field, shape, options = IMAGE_MODELS.get(family, IMAGE_MODELS["nano-banana-2"])
    urls = [r if str(r).startswith("http") else upload(r) for r in list(refs or [])[:8]]
    inputs = {"prompt": (prompt or "")[:5000], shape: aspect_ratio or "16:9", **options}
    if urls:
        inputs[field] = urls
    return run_task(edit if urls else generate, inputs, timeout=timeout, out_path=out_path)


def image_from_atlas_payload(payload: dict, out_path: str | Path, *, timeout: float = 120) -> str:
    """An Atlas generateImage payload ({model, prompt, images|image, aspect_ratio, ...}) made on Kie instead."""
    refs = payload.get("images") or ([payload["image"]] if payload.get("image") else [])
    return image(str(payload.get("prompt") or ""), out_path, refs=refs, family=image_family(payload.get("model", "")),
                 aspect_ratio=str(payload.get("aspect_ratio") or "16:9"), timeout=timeout)


def video(prompt: str, image_ref: str | Path, out_path: str | Path, *, duration: int = 5,
          timeout: float = 720) -> str:
    """An image-to-video clip on Kie (Seedance V1 Pro Fast, 720p, 5 or 10 s) into out_path."""
    url = str(image_ref) if str(image_ref).startswith("http") else upload(image_ref)
    inputs = {"prompt": (prompt or "")[:10000], "image_url": url, "resolution": "720p",
              "duration": "10" if int(duration or 5) > 7 else "5"}
    return run_task(VIDEO_MODEL, inputs, timeout=timeout, out_path=out_path)
