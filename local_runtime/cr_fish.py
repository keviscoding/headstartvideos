"""Fish Audio (fish.audio) narration on the creator's own Fish Audio API key.

A customer asked for it on Discord (Swapnil, 2026-10-29: "Please add Fish Audio integration"). It works like the
creator's own ElevenLabs key: the key is pasted once in Setup & accounts (never in a chat) and kept in Studio's .env as
FISH_AUDIO_API_KEY; Create -> Choose voice lists their Fish Audio voices (their own clones, the library voices they
picked here, a search of Fish Audio's public library, or a pasted fish.audio voice link), each with Fish Audio's own
sample when the voice has one; and the video's narration is read by Fish Audio's text-to-speech API, then timed on
this computer by Studio's speech-timing model like every other voice (cr_media._time_narration).

Fish Audio's API (official docs, read 2026-10-29: https://docs.fish.audio/api-reference/endpoint/openapi-v1/text-to-speech,
https://docs.fish.audio/api-reference/endpoint/model/list-models, https://docs.fish.audio/api-reference/openapi.json,
https://docs.fish.audio/developer-guide/models-pricing/pricing-and-rate-limits, .../api-reference/errors):
- POST https://api.fish.audio/v1/tts, "Authorization: Bearer <key>", the model in a "model" header (s2.1-pro by
  default; s2.1-pro-free, s2-pro, drama-3; s1 is deprecated and retires 2026-12-31). JSON body: text, reference_id (the
  voice), format mp3|wav|pcm|opus, mp3_bitrate 64|128|192, latency normal|balanced|low, normalize, prosody {speed
  0.5-2, volume}, language (BCP-47, only for spelling out numbers and dates). The answer is the audio itself, chunked.
- GET https://api.fish.audio/model (self=true: the account's own voices; title=, page_size<=100, page_number,
  sort_by score|task_count|created_at) and GET /model/{id}: each voice has _id, title, description, languages, state,
  type (tts), author, and samples [{title, text, audio}].
- GET https://api.fish.audio/wallet/self/api-credit: the API balance ("credit"), read for free: the key check.
- Errors: 401 key, 402 no API credit, 403 not permitted, 400/404 bad voice, 429 at the concurrency limit (Starter: 5
  at once), 5xx server. Retry 429 and 5xx with backoff; never other 4xx.
- Price by the size of the text in UTF-8 bytes: s2.1-pro / s2-pro $15 a million bytes (about 180,000 English words,
  ~12 hours of speech), drama-3 $50 ($10 until 2026-11-12 17:00 UTC), s2.1-pro-free $0 under fair-use limits.

Nothing here is paid twice. Each part of the narration keeps a receipt beside its audio (part-NNN.fish.json): the part
is read again only when its audio is missing and Fish Audio certainly made nothing for it (it answered with an error
before any audio, or the connection never opened). A part whose request may have reached Fish Audio without its
audio arriving (the connection dropped mid-answer, Studio closed while it was being read) stops the video with the
voice choices; "Try the same voice again" (or picking Fish Audio there) is the creator's yes to read that part again,
which can pay for it twice (a few cents at most). A Resume reuses every finished part.

Studio's voice speed (a style's voice.speed) is applied after the narration by the engine (_speed_voice in
cr_media.make_voiceover), exactly as for Kie and ElevenLabs, so Fish Audio reads at its natural pace (prosody.speed
is left at 1) and the speed is never applied twice.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

import cr_bridge
import cr_media

API = "https://api.fish.audio"
KEY_ENV = "FISH_AUDIO_API_KEY"
PROVIDER = "fish"
LABEL = "Fish Audio"
KEY_URL = "https://fish.audio/app/api-keys"
BILLING_URL = "https://fish.audio/app/developers/billing/"

# Fish Audio's TTS models (pricing page, 2026-10-29). usd: per million UTF-8 bytes of text.
MODELS = (
    {"id": "s2.1-pro", "label": "S2.1 Pro", "usd": 15.0,
     "note": "Fish Audio's recommended model: 83 languages, expressive [bracket] cues."},
    {"id": "s2.1-pro-free", "label": "S2.1 Pro Free", "usd": 0.0,
     "note": "The same model at $0 under Fish Audio's fair-use limits, without speed guarantees."},
    {"id": "s2-pro", "label": "S2 Pro", "usd": 15.0, "note": "Fish Audio's previous S2 model."},
    {"id": "drama-3", "label": "Drama 3", "usd": 50.0,
     "note": "Directed in plain language; Fish Audio's priciest voice model."},
)
DEFAULT_MODEL = "s2.1-pro"
DRAMA_PROMO = (datetime(2026, 11, 12, 17, 0, tzinfo=timezone.utc), 10.0)    # drama-3's launch price until then
BYTES_PER_MINUTE = 900          # ~150 spoken English words a minute at ~6 UTF-8 bytes each (words plus spaces)

VOICE_ID = re.compile(r"[0-9a-f]{32}\Z")
VOICE_IN = re.compile(r"(?<![0-9a-f])([0-9a-f]{32})(?![0-9a-f])", re.I)

PART_CHARS = 2000               # a part of the narration per request: ~2 minutes of speech, well inside a slot
FORMAT, MP3_BITRATE = "mp3", 192
LATENCY = "normal"              # Fish Audio: "most stable output"; this is long-form narration, not a live reply
TIMEOUT = (10, 600)             # (connect, read) seconds for one part
REFUSED_PAUSES = (30, 90)       # 429 (at the concurrency limit) and 503 (high load): nothing made, wait and ask again
UNSENT_PAUSES = (10,)           # the connection never opened: nothing reached Fish Audio
MIN_AUDIO_BYTES = 1000

SAVED = "fish_voices"           # settings: [{id, name, preview}] library voices the creator picked in Studio
PICKS = "fish_picks"            # settings: {style: {id, name}} the voice chosen for each recipe
PIN = "cr-fish-voice.json"      # a video's Fish Audio voice as first used: a Resume reads with the same voice
FAILED = "cr-voice-failed.json"  # cr_media.VOICE_FAILED: why this video's voice stopped (the voice choices read it)


class Stop(cr_media.AccountStop):
    """A Fish Audio narration that can't go on: an AccountStop, so the engine stops at its job boundary like every
    other paid-voice stop and the voice choices are offered."""
    def __init__(self, message, *, category="request", code="", uncertain=False, charged=None):
        super().__init__(message)
        self.category, self.code, self.uncertain, self.charged = category, str(code), uncertain, charged
        self.reason = message


# ── account ──────────────────────────────────────────────────────────────────────────────────────────────────────

def key() -> str:
    return os.getenv(KEY_ENV, "").strip()


def settings() -> dict:
    return cr_bridge.load_settings() or {}


def model_info(model_id=None) -> dict:
    wanted = str(model_id or DEFAULT_MODEL)
    return next((m for m in MODELS if m["id"] == wanted), MODELS[0])


def usd_per_million(model_id=None, now=None) -> float:
    info = model_info(model_id)
    if info["id"] == "drama-3":
        until, promo = DRAMA_PROMO
        if (now or datetime.now(timezone.utc)) < until:
            return promo
    return float(info["usd"])


def cost_usd(text: str, model_id=None, now=None) -> float:
    """What Fish Audio charges for reading these words: their size in UTF-8 bytes at the model's price."""
    return len(str(text or "").encode("utf-8")) / 1_000_000 * usd_per_million(model_id, now)


def usd_per_minute(model_id=None, now=None) -> float:
    return BYTES_PER_MINUTE / 1_000_000 * usd_per_million(model_id, now)


def selected() -> bool:
    """Narration in Setup & accounts is Fish Audio, with its key on this computer."""
    return (settings().get("narration") or {}).get("provider") == PROVIDER and bool(key())


def narration() -> dict:
    """Setup's Fish Audio narration: {provider, model, voice, voice_name} ({} when Fish Audio isn't chosen)."""
    cfg = settings().get("narration") or {}
    if cfg.get("provider") != PROVIDER:
        return {}
    return {"provider": PROVIDER, "model": model_info(cfg.get("model"))["id"],
            "voice": str(cfg.get("voice") or "") if VOICE_ID.fullmatch(str(cfg.get("voice") or "")) else "",
            "voice_name": str(cfg.get("voice_name") or "")[:90]}


def voice_for_style(style: str = "") -> dict:
    """The Fish Audio voice for a recipe: the one chosen for it in Choose voice, else Setup's last chosen voice."""
    cfg = narration()
    if not cfg:
        return {}
    pick = (settings().get(PICKS) or {}).get(str(style or "")) or {}
    if VOICE_ID.fullmatch(str(pick.get("id") or "")):
        cfg = {**cfg, "voice": pick["id"], "voice_name": str(pick.get("name") or "")[:90]}
    return cfg


def _headers(api_key: str, model: str = "") -> dict:
    out = {"Authorization": "Bearer " + api_key}
    if model:
        out["model"] = model
    return out


def _error_text(res) -> str:
    try:
        data = res.json()
        if isinstance(data, dict):
            return str(data.get("message") or data.get("reason") or "")[:200]
    except Exception:                                          # noqa: BLE001 - a plain-text or empty error body
        pass
    try:
        return str(res.text or "")[:200]
    except Exception:                                          # noqa: BLE001
        return ""


def category(code) -> str:
    code = str(code)
    return {"401": "access", "403": "access", "402": "balance", "429": "rate_limit", "400": "request",
            "404": "request", "422": "request"}.get(code, "model_failure" if code.startswith("5") else "request")


def check_key(api_key: str, *, get=None) -> dict:
    """Is this a working Fish Audio API key? Reads its API balance, which costs nothing. {"credit": float|None}."""
    get = get or requests.get
    try:
        r = get(API + "/wallet/self/api-credit", headers=_headers(api_key), timeout=(10, 20), allow_redirects=False)
    except requests.RequestException:
        raise Stop("Fish Audio didn't answer. Check your connection and try again.", category="network")
    if r.status_code == 401:
        raise Stop("Fish Audio didn't accept this key. Copy it again from Fish Audio → API Keys.", category="access",
                   code=401)
    if r.status_code == 403:
        raise Stop("This Fish Audio key isn't allowed to do that. Create a new API key in Fish Audio and paste it.",
                   category="access", code=403)
    if r.status_code != 200:
        raise Stop(f"Fish Audio couldn't check this key right now (HTTP {r.status_code}). Try again in a moment.",
                   code=r.status_code)
    try:
        credit = float((r.json() or {}).get("credit"))
    except (TypeError, ValueError, AttributeError):
        credit = None
    return {"credit": credit}


# ── voices ───────────────────────────────────────────────────────────────────────────────────────────────────────

def voice_id_in(text: str) -> str:
    """A Fish Audio voice ID from what the creator pasted: the ID itself or a fish.audio voice link."""
    found = VOICE_IN.search(str(text or ""))
    return found.group(1).lower() if found else ""


def _https(url) -> str:
    url = str(url or "")
    return url if url.startswith("https://") else ""


def voice_row(item: dict, mine: bool = False) -> dict:
    """One voice as Choose voice shows it (the same fields as the ElevenLabs library rows)."""
    samples = [s for s in item.get("samples") or [] if isinstance(s, dict)]
    preview = next((_https(s.get("audio")) for s in samples if _https(s.get("audio"))), "")
    langs = [str(l) for l in item.get("languages") or [] if l][:4]
    author = (item.get("author") or {}).get("nickname") if isinstance(item.get("author"), dict) else ""
    return {"id": str(item.get("_id") or ""), "name": str(item.get("title") or "Fish Audio voice")[:90],
            "gender": "", "accent": ", ".join(langs), "age": "",
            "description": " ".join(str(item.get("description") or "").split())[:160]
                           or (f"by {author}" if author and not mine else ""),
            "preview": preview, "mine": bool(mine)}


def _usable(item) -> bool:
    return (isinstance(item, dict) and VOICE_ID.fullmatch(str(item.get("_id") or "")) is not None
            and str(item.get("type") or "tts") == "tts" and str(item.get("state") or "trained") == "trained"
            and not item.get("dmca_taken_down"))


def _list_error(r):
    if r.status_code in (401, 403):
        raise Stop("Fish Audio didn't accept your key. Paste it again in Setup & accounts.", category="access",
                   code=r.status_code)
    raise Stop(f"Fish Audio couldn't list voices right now (HTTP {r.status_code}).", code=r.status_code)


def get_voice(api_key: str, voice_id: str, *, get=None) -> dict:
    get = get or requests.get
    if not VOICE_ID.fullmatch(voice_id or ""):
        raise Stop("That isn't a Fish Audio voice link or ID.")
    try:
        r = get(f"{API}/model/{voice_id}", headers=_headers(api_key), timeout=(10, 30))
    except requests.RequestException:
        raise Stop("Fish Audio didn't answer. Check your connection and try again.", category="network")
    if r.status_code == 404:
        raise Stop("Fish Audio has no voice with that ID. Copy the link from the voice's page on fish.audio.",
                   code=404)
    if r.status_code != 200:
        _list_error(r)
    item = r.json()
    if not _usable(item):
        raise Stop("That Fish Audio voice isn't ready to read text (still training, or not a voice model).")
    return item


def list_voices(api_key: str, source: str = "mine", search: str = "", page: int = 1, *, get=None,
                saved=None) -> dict:
    """One page of voices: source "mine" (the account's own voices, then the library voices picked in Studio) or
    "shared" (Fish Audio's public library, best first). A pasted voice link or ID answers with that one voice."""
    get = get or requests.get
    search = str(search or "").strip()[:200]
    wanted = voice_id_in(search)
    if wanted:
        return {"voices": [voice_row(get_voice(api_key, wanted, get=get))], "more": False,
                "note": "The voice from your link. Press Use to read your videos with it."}
    size = 30
    params = {"page_size": size, "page_number": max(1, min(int(page or 1), 200)), "sort_by": "score"}
    if search:
        params["title"] = search
    if source == "mine":
        params.update(self="true", sort_by="created_at")
    try:
        r = get(API + "/model", headers=_headers(api_key), params=params, timeout=(10, 30))
    except requests.RequestException:
        raise Stop("Fish Audio didn't answer. Check your connection and try again.", category="network")
    if r.status_code != 200:
        _list_error(r)
    data = r.json() if isinstance(r.json(), dict) else {}
    rows = [voice_row(item, mine=source == "mine") for item in data.get("items") or [] if _usable(item)]
    more = bool(data.get("has_more")) if data.get("has_more") is not None else \
        params["page_number"] * size < int(data.get("total") or 0)
    note = ""
    if source == "mine":
        have = {row["id"] for row in rows}
        picked = [v for v in (settings().get(SAVED) if saved is None else saved) or []
                  if isinstance(v, dict) and VOICE_ID.fullmatch(str(v.get("id") or "")) and v["id"] not in have
                  and (not search or search.casefold() in str(v.get("name") or "").casefold())]
        rows += [{"id": v["id"], "name": str(v.get("name") or "Fish Audio voice")[:90], "gender": "", "accent": "",
                  "age": "", "description": "From the Fish Audio library", "preview": _https(v.get("preview")),
                  "mine": False} for v in picked]
        if not rows:
            note = ("No voices on your Fish Audio account yet. Try the Voice library, or paste a fish.audio voice "
                    "link in the search box.")
    return {"voices": rows, "more": more, "note": note}


def remember_voice(cfg: dict, row: dict) -> dict:
    """A library voice the creator picked joins "Your voices" (Fish Audio's API lists only a workspace's own voices;
    there is no list of saved or liked library voices to read back)."""
    if row.get("mine"):
        return cfg
    saved = [v for v in cfg.get(SAVED) or [] if isinstance(v, dict) and v.get("id") != row["id"]]
    cfg[SAVED] = ([{"id": row["id"], "name": row.get("name") or "", "preview": _https(row.get("preview"))}]
                  + saved)[:50]
    return cfg


# ── narration ────────────────────────────────────────────────────────────────────────────────────────────────────

def chunks(text: str, limit: int = PART_CHARS):
    """Parts of at most `limit` characters, cut after a sentence where one ends in the second half, else at a space."""
    remaining = str(text or "").strip()
    while remaining:
        end = min(len(remaining), limit)
        if end < len(remaining):
            window = remaining[:end]
            sentence = max(window.rfind(". "), window.rfind("! "), window.rfind("? "), window.rfind("\n"))
            cut = sentence + 1 if sentence >= limit // 2 else window.rfind(" ")
            if cut > 0:
                end = cut
        yield remaining[:end].strip()
        remaining = remaining[end:].lstrip()


def fingerprint(text: str, voice: str, model: str, language: str = "") -> str:
    raw = json.dumps([PROVIDER, model, voice, FORMAT, MP3_BITRATE, LATENCY, language, text], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def receipt_path(part: Path) -> Path:
    return Path(part).with_suffix(".fish.json")


def read_receipt(part: Path) -> dict:
    try:
        data = json.loads(receipt_path(part).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_receipt(part: Path, data: dict) -> None:
    path = receipt_path(part)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)


def uncertain_parts(job) -> list:
    """Parts of this video whose request may have reached Fish Audio without their audio arriving."""
    out = []
    for folder in Path(job).glob("*-fish-voice"):
        for receipt in sorted(folder.glob("part-*.fish.json")):
            part = receipt.with_name(receipt.name[:-len(".fish.json")] + "." + FORMAT)
            data = read_receipt(part)
            if data.get("state") == "sending" and not data.get("released") and not part.is_file():
                out.append(part)
    return out


def release_uncertain(job) -> int:
    """The creator's yes to read the uncertain parts again (Voice it another way: the same voice again, or Fish
    Audio): each may be paid for twice. Returns how many were released."""
    n = 0
    for part in uncertain_parts(job):
        write_receipt(part, {**read_receipt(part), "released": True, "released_at": int(time.time())})
        n += 1
    return n


def _language() -> str:
    try:
        import cr_language
        return cr_language.whisper_code() or ""
    except Exception:                                          # noqa: BLE001 - no language chosen: Fish Audio detects
        return ""


def synthesize(api_key: str, text: str, voice: str, model: str, part: Path, *, post=None, log=print,
               language: str = "", sleep=time.sleep) -> dict:
    """Read one part with Fish Audio into `part` (mp3), from its receipt when it was already made. Returns the
    receipt. Refusals that made nothing (429, 503, a connection that never opened) are waited out a few times."""
    post = post or requests.post
    part = Path(part)
    sign = fingerprint(text, voice, model, language)
    receipt = read_receipt(part)
    if part.is_file() and part.stat().st_size >= MIN_AUDIO_BYTES and receipt.get("fingerprint") == sign \
            and receipt.get("state") == "done":
        return receipt                                       # made before: reused, never paid again
    if receipt.get("fingerprint") == sign and receipt.get("state") == "sending" and not receipt.get("released"):
        usd = cost_usd(text, model)
        raise Stop(f"Fish Audio may already have read part {part.stem.split('-')[-1]} of this narration (Studio sent it, "
                   "but its audio never arrived). To be safe Studio didn't send it again. Choose \"Try the same voice "
                   f"again\" to read that part once more (it may be paid twice, about ${usd:.3f}), or voice this "
                   "video another way.", category="uncertain", uncertain=True)
    body = {"text": text, "reference_id": voice, "format": FORMAT, "mp3_bitrate": MP3_BITRATE, "latency": LATENCY,
            "normalize": True}
    if language:
        body["language"] = language
    headers = {**_headers(api_key, model), "Content-Type": "application/json"}
    refused = unsent = 0
    while True:
        write_receipt(part, {"fingerprint": sign, "state": "sending", "model": model, "voice": voice,
                             "bytes": len(text.encode("utf-8")), "usd": round(cost_usd(text, model), 5),
                             "at": int(time.time())})
        tmp = part.with_name(part.name + ".download")
        try:
            r = post(API + "/v1/tts", headers=headers, json=body, stream=True, timeout=TIMEOUT)
        except requests.RequestException as error:
            never = isinstance(error, requests.exceptions.ConnectTimeout) or any(
                s in repr(error) for s in ("NewConnectionError", "NameResolutionError",
                                           "Failed to establish a new connection"))
            if never:
                write_receipt(part, {"fingerprint": sign, "state": "unsent", "at": int(time.time())})
                if unsent < len(UNSENT_PAUSES):
                    log(f"Fish Audio couldn't be reached; nothing was sent. Trying again in {UNSENT_PAUSES[unsent]} s.")
                    sleep(UNSENT_PAUSES[unsent])
                    unsent += 1
                    continue
                raise Stop("Studio couldn't reach Fish Audio (api.fish.audio); nothing was sent or paid. Check this "
                           "computer's connection, then press Resume.", category="network")
            raise Stop("The connection to Fish Audio dropped while it was reading part "
                       f"{part.stem.split('-')[-1]} of this narration, so it may have been charged without its audio "
                       "arriving. Studio didn't send it again by itself. Choose \"Try the same voice again\" to read it "
                       "once more, or voice this video another way.", category="uncertain", uncertain=True)
        status = int(getattr(r, "status_code", 0) or 0)
        kind = str((getattr(r, "headers", {}) or {}).get("content-type", "")).lower()
        if status != 200 or kind.startswith("application/json") or kind.startswith("text/"):
            reason = _error_text(r)
            code = status if status != 200 else "200-error"
            cat = category(code)
            write_receipt(part, {"fingerprint": sign, "state": "refused", "code": str(code), "reason": reason,
                                 "at": int(time.time())})
            if status in (429, 503) and refused < len(REFUSED_PAUSES):
                pause = REFUSED_PAUSES[refused]
                refused += 1
                log(f"Fish Audio is busy (HTTP {status}); nothing was made or charged. Trying again in {pause} s "
                    f"(attempt {refused + 1} of {len(REFUSED_PAUSES) + 1}).")
                sleep(pause)
                continue
            raise Stop(_refusal(status, reason, model), category=cat, code=code, charged=0)
        try:
            with open(tmp, "wb") as out:
                for piece in r.iter_content(64 * 1024):
                    if piece:
                        out.write(piece)
        except (requests.RequestException, OSError):
            tmp.unlink(missing_ok=True)
            raise Stop("The connection to Fish Audio dropped while part "
                       f"{part.stem.split('-')[-1]} of this narration was arriving, so it may have been charged. "
                       "Studio didn't send it again by itself. Choose \"Try the same voice again\" to read it once "
                       "more, or voice this video another way.", category="uncertain", uncertain=True)
        size = tmp.stat().st_size if tmp.is_file() else 0
        if size < MIN_AUDIO_BYTES:
            tmp.unlink(missing_ok=True)
            raise Stop(f"Fish Audio answered part {part.stem.split('-')[-1]} with no usable audio ({size} bytes), so "
                       "it may have been charged. Choose \"Try the same voice again\" to read it once more, or voice "
                       "this video another way.", category="uncertain", uncertain=True)
        tmp.replace(part)
        done = {"fingerprint": sign, "state": "done", "model": model, "voice": voice,
                "bytes": len(text.encode("utf-8")), "usd": round(cost_usd(text, model), 5),
                "audio_bytes": size, "at": int(time.time())}
        write_receipt(part, done)
        return done


def _refusal(status, reason, model) -> str:
    said = f" Fish Audio said: {reason}" if reason else ""
    if status == 401:
        return "Fish Audio didn't accept your key. Paste it again in Setup & accounts, then press Resume." + said
    if status == 402:
        return ("Your Fish Audio API balance can't pay for this narration. Add API credit at "
                f"{BILLING_URL} (or choose S2.1 Pro Free in Setup & accounts), then press Resume.")
    if status == 403:
        return "Fish Audio didn't allow this key to read text. Check the key in Setup & accounts." + said
    if status in (400, 404, 422):
        return ("Fish Audio couldn't read with this voice (it may have been deleted or made private). Choose another "
                "voice in Create → Choose voice, then press Resume." + said)
    if status == 429:
        return ("Fish Audio is still at your account's limit of requests at once. Nothing was charged. Wait a few "
                "minutes, then press Resume.")
    if status >= 500:
        return (f"Fish Audio had a problem on its side (HTTP {status}) and sent no audio. Wait a few minutes, then "
                "press Resume." + said)
    return f"Fish Audio couldn't read this narration (HTTP {status})." + said


def pinned(job) -> dict:
    try:
        data = json.loads((Path(job) / PIN).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if isinstance(data, dict) and VOICE_ID.fullmatch(str(data.get("voice") or "")):
        return {"provider": PROVIDER, "model": model_info(data.get("model"))["id"], "voice": data["voice"],
                "voice_name": str(data.get("voice_name") or "")[:90]}
    return {}


def voice_for_job(job, chosen: dict = None, style: str = "") -> dict:
    """The Fish Audio voice this video reads with: its own choice (cr-voice.json), else the voice it started with
    (cr-fish-voice.json), else the recipe's voice in Setup & accounts. {} when this video doesn't use Fish Audio."""
    if chosen is not None and chosen.get("provider") == PROVIDER:
        cfg = {**(voice_for_style(style) or {"provider": PROVIDER, "model": DEFAULT_MODEL, "voice": "",
                                              "voice_name": ""}),
               **{k: v for k, v in chosen.items() if v}}
        return cfg
    if chosen:
        return {}
    return pinned(job) or (voice_for_style(style) if selected() else {})


def _failed(job, model, error) -> None:
    try:
        (Path(job) / FAILED).write_text(json.dumps({
            "provider": PROVIDER, "label": LABEL, "model": model, "category": getattr(error, "category", ""),
            "reason": str(error)[:300], "charged": getattr(error, "charged", None),
            "uncertain": bool(getattr(error, "uncertain", False)), "at": int(time.time())}), encoding="utf-8")
    except OSError:
        pass


def narrate(engine, text, mp3, srt, cfg: dict, *, time_narration=None, post=None, sleep=time.sleep):
    """The video's narration read by Fish Audio, part by part with receipts, joined into mp3 and timed into srt."""
    job, mp3 = Path(mp3).parent, Path(mp3)
    model, voice = model_info(cfg.get("model"))["id"], str(cfg.get("voice") or "")
    try:
        if not cr_bridge.load_settings().get("costs_accepted"):
            raise Stop("Review your model accounts and spending agreement in Setup & accounts.", category="access")
        api_key = key()
        if not api_key:
            raise Stop("Reconnect Fish Audio in Setup & accounts (paste your Fish Audio API key there).",
                       category="access")
        if not VOICE_ID.fullmatch(voice):
            raise Stop("Choose a Fish Audio voice in Create → Choose voice first.", category="request")
        if not (job / PIN).is_file():
            (job / PIN).write_text(json.dumps({"provider": PROVIDER, "model": model, "voice": voice,
                                               "voice_name": cfg.get("voice_name") or ""}), encoding="utf-8")
        pieces = list(chunks(text))
        if not pieces:
            raise Stop("There is no narration text to record.")
        name = cfg.get("voice_name") or voice[:8]
        total = sum(cost_usd(p, model) for p in pieces)
        engine.log(f"Narration: Fish Audio {model_info(model)['label']}, voice {name}: {len(pieces)} part"
                   f"{'s' if len(pieces) != 1 else ''}, about ${total:.3f} on your Fish Audio balance.")
        folder = job / (mp3.stem + "-fish-voice")
        folder.mkdir(parents=True, exist_ok=True)
        language = _language()
        parts = []
        for i, piece in enumerate(pieces):
            part = folder / f"part-{i:03d}.{FORMAT}"
            synthesize(api_key, piece, voice, model, part, post=post, log=engine.log, language=language, sleep=sleep)
            parts.append(part)
    except Stop as error:
        _failed(job, model, error)
        raise
    if len(parts) == 1:
        shutil.copyfile(parts[0], mp3)
    else:
        listing = folder / "parts.txt"
        listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                        "-c:a", "libmp3lame", "-b:a", "192k", str(mp3)], check=True)
    if time_narration is None:
        time_narration = cr_media._time_narration
    time_narration(engine, mp3, srt, text)


# ── Studio routes: Setup's Fish Audio card and Choose voice ─────────────────────────────────────────────────────

def setup_row() -> dict:
    """What Setup and Create show for narration on Fish Audio (no key ever leaves this computer)."""
    cfg = narration()
    model = model_info(cfg.get("model"))
    return {"selection": PROVIDER, "provider": PROVIDER, "label": LABEL, "direct": PROVIDER, "connected": bool(key()),
            "model": model["id"], "model_label": f"Fish Audio {model['label']}",
            "voice": cfg.get("voice_name") or cfg.get("voice") or "", "voice_id": cfg.get("voice") or "",
            "usd_per_million_bytes": usd_per_million(model["id"]),
            "note": "" if cfg.get("voice") else "Choose your Fish Audio voice in Create → Choose voice."}


def install(studio, activity, write_provider):
    """Setup's Fish Audio card (/cr-voice/fish) and Fish Audio in Choose voice (wrapping /cr-voices, its select and
    library routes, which answer as before when Fish Audio isn't the narration)."""
    from flask import jsonify, request
    app = studio.app
    listing = app.view_functions.get("voices_list")
    selecting = app.view_functions.get("voices_select")
    browsing = app.view_functions.get("voices_library")
    style_re = re.compile(r"[\w.-]{1,120}\Z")

    def recipe(data=None):
        style = str(request.args.get("style") or (data or {}).get("style") or "").strip()
        styles = getattr(getattr(studio, "styles", None), "STYLES", None)
        if not style_re.fullmatch(style) or (isinstance(styles, dict) and style not in styles):
            return ""
        return style

    @app.post("/cr-voice/fish")
    def use_fish():
        data = request.get_json(silent=True) or {}
        cfg = cr_bridge.load_settings()
        if not cfg.get("key") or not cfg.get("costs_accepted"):
            return jsonify(error="Connect CR and review the spending agreement first."), 409
        if activity.busy():
            return jsonify(error="Finish your current video before changing its accounts."), 409
        supplied = str(data.get("key", "")).strip()
        api_key = supplied or key()
        if not 16 <= len(api_key) <= 512 or any(c.isspace() for c in api_key):
            return jsonify(error="Paste your complete Fish Audio API key."), 400
        model = str(data.get("model") or (cfg.get("narration") or {}).get("model") or DEFAULT_MODEL)
        if model not in {m["id"] for m in MODELS}:
            return jsonify(error="Choose one of the Fish Audio models listed."), 400
        try:
            balance = check_key(api_key)
        except Stop as error:
            return jsonify(error=str(error)), 400
        if supplied:
            write_provider(KEY_ENV, api_key)
        old = cfg.get("narration") or {}
        cfg["narration"] = {"provider": PROVIDER, "model": model,
                            **({"voice": old["voice"], "voice_name": old.get("voice_name", "")}
                               if old.get("provider") == PROVIDER and VOICE_ID.fullmatch(str(old.get("voice") or ""))
                               else {})}
        cfg.pop("voice_notice", None)
        cr_bridge.save_settings(cfg)
        credit = balance.get("credit")
        price = usd_per_million(model)
        money = (f" Your Fish Audio API balance: ${credit:.2f}." if isinstance(credit, float) else "")
        if isinstance(credit, float) and credit <= 0 and price > 0:
            money += (f" Add API credit at {BILLING_URL} before making a video, or choose S2.1 Pro Free.")
        return jsonify(saved=True, model=model, credit=credit,
                       message=f"Fish Audio connected: narration now uses {model_info(model)['label']} from your Fish "
                               f"Audio account (about ${usd_per_minute(model):.3f} a minute of narration)." + money +
                               " Pick your voice in Create → Choose voice.")

    def voices_list():
        if not selected():
            return listing() if listing else jsonify(connected=False, voices=[], previews=[])
        style = recipe()
        cfg = voice_for_style(style)
        model = model_info(cfg.get("model"))
        return jsonify(connected=True, library=True, provider=PROVIDER, provider_label=LABEL, model=model["id"],
                       model_label=f"Fish Audio {model['label']}", style=style, voice=cfg.get("voice_name") or "",
                       voice_id=cfg.get("voice") or "", voices=[], previews=[],
                       note="Your Fish Audio voices first. Paste a fish.audio voice link in the search box to use "
                            "any voice from Fish Audio's library.")

    def voices_select():
        data = request.get_json(silent=True) or {}
        if not selected():
            return selecting() if selecting else (jsonify(error="Connect a voice account in Setup & accounts first."),
                                                  409)
        if activity.busy():
            return jsonify(error="Finish the video that's running, then change the voice for your next one."), 409
        voice = str(data.get("voice_id") or "").strip().lower()
        if not VOICE_ID.fullmatch(voice):
            return jsonify(error="Choose a voice from the list."), 400
        try:
            item = get_voice(key(), voice)
        except Stop as error:
            return jsonify(error=str(error)), 400
        row = voice_row(item, mine=bool(data.get("mine")))
        name = row["name"]
        cfg = cr_bridge.load_settings()
        cfg["narration"] = {**(cfg.get("narration") or {}), "provider": PROVIDER, "voice": voice, "voice_name": name}
        style = recipe(data)
        if style:
            cfg[PICKS] = {**(cfg.get(PICKS) or {}), style: {"id": voice, "name": name}}
        remember_voice(cfg, row)
        cfg.pop("voice_notice", None)
        cr_bridge.save_settings(cfg)
        label = ((getattr(getattr(studio, "styles", None), "STYLES", {}) or {}).get(style) or {}).get("label")
        return jsonify(saved=True, voice=name, voice_id=voice,
                       message=f"Your next {label + ' ' if label else ''}videos use {name} from Fish Audio.")

    def voices_library():
        if not selected():
            if browsing:
                return browsing()
            return jsonify(error="Connect ElevenLabs or Fish Audio in Setup & accounts to browse voices."), 409
        a = request.args
        try:
            page = max(1, min(200, int(a.get("page") or 1)))
        except (TypeError, ValueError):
            page = 1
        source = "mine" if a.get("source") == "mine" else "shared"
        try:
            got = list_voices(key(), source, str(a.get("search") or "")[:200], page)
        except Stop as error:
            return jsonify(error=str(error), voices=[], more=False), 502
        except Exception:                                      # noqa: BLE001 - the dialog says what went wrong
            return jsonify(error="Fish Audio didn't answer. Try again in a moment.", voices=[], more=False), 502
        return jsonify(voices=got["voices"], more=got["more"], note=got.get("note") or "", source=source)

    if listing is not None:
        app.view_functions["voices_list"] = voices_list
    if selecting is not None:
        app.view_functions["voices_select"] = voices_select
    if browsing is not None:
        app.view_functions["voices_library"] = voices_library
    else:
        app.add_url_rule("/cr-voices/library", "voices_library", voices_library)
