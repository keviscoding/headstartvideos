"""Why a video stopped, as a short record ChannelRecipe can count.

A stopped project's finish (cr_bridge.finish_project) carries `failure`: the step it stopped at, the kind of problem,
the service involved, one cleaned line of its message (at most MAX_MESSAGE characters), and this Studio's version, the
computer's family (windows, mac or linux) and the recipe. Never the video's content: web addresses, file paths (the
home folder becomes ~ and folder names are dropped), emails, keys, tokens and the video's title are taken out of the
message, and nothing of the script, voice or pictures is read.

Before these records, a server refused a finish carrying one; cr_bridge.finish_project then sends the same finish
again without it. A server that takes the record and answers nothing more changes nothing here.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

HOME = Path(__file__).resolve().parent
MAX_MESSAGE = 240

STEPS = ("setup", "script", "voice", "pictures", "footage", "motion", "presenter", "youtube", "clips", "music",
         "graphics", "threed", "render", "mux", "thumbnail", "upload", "study", "save", "cloud", "unknown")
CATEGORIES = ("provider_error", "timeout", "disk_full", "out_of_memory", "crash", "blocked_network", "usage_limit",
              "balance", "auth", "moderation", "rate_limit", "missing_tool", "tool_error", "invalid_input",
              "studio_closed", "cancelled", "unknown")
PROVIDERS = ("kie", "elevenlabs", "claude", "youtube", "channelrecipe", "other", "none")
SOURCES = ("video", "short", "timeline", "style_study", "threed", "cloud")


# ── the message, cleaned ──────────────────────────────────────────────────────────────────────────────────────────

_URL = re.compile(r"(?i)\b(?:https?|wss?|ftp|file)://[^\s\"'<>)\]]+|\bwww\.[^\s\"'<>)\]]+")
_HOST_PATH = re.compile(r"(?i)\b((?:[a-z0-9-]+\.)+[a-z]{2,})/[^\s\"'<>)\]]*")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[^\s,;\"']+")
_ASSIGNED = re.compile(r"(?i)\b([\w-]*(?:api[_-]?key|apikey|key|token|secret|password|passwd|authorization|cookie"
                       r"|signature|credential)s?)(['\"]?\s*[=:]\s*['\"]?)[^\s,;\"'&}]+")
_PREFIXED = re.compile(r"\b(?:cr_mcp_|sk-|sk_|rk_|pk_|xi_|ghp_|gho_|github_pat_|xox[abposr]-|AIza|ya29\.|eyJ)"
                       r"[\w.\-]{6,}")
_UUID = re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_HEX_ID = re.compile(r"(?i)\b(?:crv_|op_|video_|b_|cc_)?[0-9a-f]{12,}\b")
_LONG_TOKEN = re.compile(r"(?<![\w~…])(?=[A-Za-z0-9_\-+/=]*\d)(?=[A-Za-z0-9_\-+/=]*[A-Za-z])[A-Za-z0-9_\-+/=]{24,}")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_LONG_QUOTE = re.compile(r"(?<!\w)(?:\"[^\"\n]{48,}\"|'[^'\n]{48,}'|“[^”\n]{48,}”)(?!\w)")
# a home folder, the user's name with it (no space in a Mac or Linux name; a Windows one ends at its backslash)
_HOMES = (re.compile(r"(?i)\b[A-Z]:[\\/]+(?:Users|Documents and Settings)[\\/]+[^\\/\r\n\"']+?(?=[\\/]|$)"),
          re.compile(r"(?<![\w.])/(?:Users|home)/[^/\s\"']+"),
          re.compile(r"(?<![\w.])/root(?=/)"))
# what is left that looks like a path: from ~, a drive, … or a slash. A folder's name may have a space or two
# ("ChannelRecipe studio"); the file's name has none.
_SEG = r"[^\\/\s\"'<>|:!,;()\[\]{}]+"
_PATH = re.compile(r"(?<![\w:/\\~…])(?P<root>~|[A-Za-z]:|…|\.{1,2})?"
                   r"(?P<dirs>(?:[\\/]" + _SEG + r"(?: " + _SEG + r"){0,2}(?=[\\/]))*)"
                   r"[\\/](?P<base>[^\\/\s\"'<>|:,;()\[\]{}]+)?")
_BOILERPLATE = [re.compile(p, re.I) for p in (
    r"Saved work is kept[^.]*\.", r"Your (?:local |saved |generated )?(?:output|progress|work|study|scenes and voice)"
    r"[^.]{0,40}(?:saved|kept|intact)[^.]*\.", r"Check Video tools in Setup & accounts\.",
    r"\(Copy diagnostics[^)]*\)", r"(?:Press|press) Resume saved project[^.]*\.", r"It will not run again automatically\.",
    r"The details are in the project's[^.]*\.", r"Nothing was (?:spent|charged|changed)[^.]*\.",
    r"(?:,? then )?review (?:this saved stage|recovery|its status|stopped Claude work)[^.]*\.",
    r"Wait a few minutes[^.]*\.")]
_FIELDS = re.compile(r"(?i)['\"]?\b(code|msg|message|failmsg|failcode|error|errormessage|status|state|detail|reason|type)"
                     r"\b['\"]?\s*[:=]\s*(?:'([^'\n]{1,80})'|\"([^\"\n]{1,80})\"|([^,'\"{}\[\]\n]{1,80}))")


def _secrets():
    """This computer's own keys (the account file and the environment), to take out of a message word for word."""
    found = [v for k, v in os.environ.items() if isinstance(v, str) and len(v) >= 8
             and (k.upper().endswith(("_KEY", "_TOKEN", "_SECRET")) or "TOKEN" in k.upper() or "SECRET" in k.upper())]
    try:
        cfg = json.loads((HOME / ".cr-connection.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}

    def walk(value, name=""):
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, str(key))
        elif isinstance(value, list):
            for item in value:
                walk(item, name)
        elif (isinstance(value, str) and len(value) >= 8
              and re.search(r"(?i)(key|token|secret|password)$", name)):
            found.append(value)
    walk(cfg)
    return sorted(set(found), key=len, reverse=True)


def _bodies(text: str) -> str:
    """A provider's answer pasted into a message ({...}) keeps only its code, status and message fields: a request
    body may hold the prompt, the narration or a link."""
    out, i = [], 0
    while i < len(text):
        if text[i] != "{":
            out.append(text[i])
            i += 1
            continue
        depth, j = 0, i
        while j < len(text):
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            j += 1
            if depth == 0:
                break
        body = text[i:j]
        fields = [f"{k}: {(a or b or c).strip()}" for k, a, b, c in _FIELDS.findall(body) if (a or b or c).strip()][:4]
        out.append("(" + "; ".join(fields) + ")" if fields else "(…)")
        i = j
    return "".join(out)


def _title_forms(titles) -> list:
    forms = []
    for title in titles or ():
        title = " ".join(str(title or "").split())
        if len(title) < 3:
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        forms += [title, slug, slug.replace("-", "_"), slug.replace("-", " ")]
        if len(title) > 24:
            forms.append(title[:24])                              # a title cut short in a message
    return sorted({f for f in forms if len(f) >= 3}, key=len, reverse=True)


def _title_words(titles) -> set:
    return {w for t in titles or () for w in re.findall(r"[a-z0-9]{4,}", str(t or "").lower())}


def sanitize(text, *, titles=(), secrets=None, home=None, limit=MAX_MESSAGE) -> str:
    """One line of `text` that is safe to send: no URLs, file paths (home -> ~, folder names dropped), emails, keys,
    tokens, ids or titles, at most `limit` characters."""
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    for secret in (secrets if secrets is not None else _secrets()):
        if secret:
            text = text.replace(secret, "[hidden]")
    text = _URL.sub("[link]", text)
    text = _EMAIL.sub("[email]", text)
    text = _HOST_PATH.sub(r"\1/…", text)
    text = _BEARER.sub(r"\1 [hidden]", text)
    text = _ASSIGNED.sub(r"\1\2[hidden]", text)
    text = _PREFIXED.sub("[hidden]", text)
    text = _bodies(text)
    try:
        home = str(home if home is not None else Path.home()).rstrip("\\/")
    except (RuntimeError, OSError):
        home = ""
    studio = str(HOME).rstrip("\\/")
    inside = bool(home) and studio.lower().startswith(home.lower())
    for root, mark in sorted(((studio, "~/…" if inside else "…"), (home, "~")), key=lambda r: -len(r[0])):
        if len(root) < 3:
            continue
        for form in {root, root.replace("\\", "/"), root.replace("/", "\\")}:
            text = re.sub(re.escape(form) + r"(?=[\\/\s\"',;:)]|$)", lambda _m, mark=mark: mark, text, flags=re.I)
    for pattern in _HOMES:
        text = pattern.sub("~", text)
    words = _title_words(titles)
    forms = _title_forms(titles)

    def path(match):
        root, base = match.group("root") or "", match.group("base") or ""
        folders = [p for p in re.split(r"[\\/]+", match.group("dirs")) if p]
        if not base and not folders or not root and not folders:
            return match.group(0)                                  # "/" or "and/or": not a path
        if words and any(w in base.lower() for w in words) or any(f.lower() in base.lower() for f in forms):
            suffix = Path(base).suffix
            base = "[file]" + (suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,5}", suffix) else "")
        lead = "~/" if root == "~" else ""
        return lead + ("…/" + base if folders or root not in ("", "~") else base)
    text = _PATH.sub(path, text)
    for form in forms:
        text = re.sub(re.escape(form), "[title]", text, flags=re.I)
    text = _UUID.sub("[id]", text)
    text = _HEX_ID.sub("[id]", text)
    text = _LONG_TOKEN.sub("[hidden]", text)
    text = _IPV4.sub("[ip]", text)
    text = _LONG_QUOTE.sub('"…"', text)
    for pattern in _BOILERPLATE:
        text = pattern.sub("", text)
    text = " ".join(text.split()).strip(" -:;,")
    if limit and len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return text


# ── what kind of stop it was ──────────────────────────────────────────────────────────────────────────────────────

_PROVIDER_WORDS = (("elevenlabs", r"elevenlabs|eleven labs|eleven v\d|xi-api"),
                   ("kie", r"\bkie\b|kie\.ai|aiquickdraw|redpandaai"),
                   ("claude", r"\bclaude\b|anthropic"),
                   ("youtube", r"youtube|yt-dlp|yt_dlp|ytdlp|googlevideo"),
                   ("channelrecipe", r"channelrecipe|clip watch|watch_service|watch_failed|out_of_watches|studio cloud"),
                   ("other", r"pexels|heygen|algrow|atlas ?cloud|wavespeed|siray|runpod|hugging ?face|openai|gemini"))
_CATEGORY_WORDS = (
    ("cancelled", r"\bcancell?ed\b"),
    ("studio_closed", r"studio that started this video closed|studio closed"),
    ("disk_full", r"no space left|errno 28|disk (?:is )?full|disk_full|not enough (?:disk )?space|winerror 112"
                  r"|free disk space"),
    ("out_of_memory", r"memoryerror|out of memory|ran out of memory|cannot allocate memory|bad_alloc"),
    ("usage_limit", r"usage limit|usage_limit|session limit|weekly limit|hit your limit|limit reached|out of extra usage"
                    r"|out_of_watches|clip watches (?:are )?used"),
    ("balance", r"insufficient|balance|top up|not enough credits|\b402\b"),
    ("moderation", r"sensitive|moderation|content policy|nsfw|safety system|flagged"),
    ("rate_limit", r"\b429\b|rate.?limit|too many requests|slow down"),
    ("auth", r"unauthori[sz]ed|\b401\b|\b403\b|forbidden|api key|invalid key|not logged in|log ?in again|sign in again"
             r"|authenticat|claude_auth|auth_missing|reconnect your"),
    ("timeout", r"timed? ?out|timeout|time limit|took too long|deadline exceeded"),
    ("blocked_network", r"could not reach|couldn't reach|can't reach|unreachable|connection (?:refused|reset|aborted|error)"
                        r"|name resolution|getaddrinfo|nodename nor servname|failed to resolve|max retries exceeded"
                        r"|ssl|certificate|handshake|proxy|\bdns\b|winerror 100(?:5[34]|6[01])|errno 11001"
                        r"|network_interrupted|claude_network|econnreset|econnrefused|\bnetwork\b"),
    ("missing_tool", r"not installed|isn't installed|no module named|modulenotfounderror|dll load failed|cannot find"
                     r"|can't find|command not found|executable doesn't exist|is not recognized|_missing\b|\bmodules\b"
                     r"|visual c\+\+"),
    ("tool_error", r"ffmpeg|conversion failed|invalid data found|error while (?:de|en)coding|chromium|playwright"
                   r"|browser (?:has )?closed|target (?:page, context or browser )?(?:has been )?closed"),
    ("crash", r"segmentation fault|access violation|ended unexpectedly|exit code|traceback|crashed|omp: error"
              r"|illegal instruction|\b(?:key|type|attribute|index|value|name|zerodivision|assertion|runtime|os)error\b"
              r"|stopped unexpectedly"),
    ("invalid_input", r"can't be laid out|no narration text|choose a |\bboard_|invalid (?:title|option)"),
    ("provider_error", r"internal error|service unavailable|overloaded|\b50[0234]\b|failed this|refused|task failed"
                       r"|createtask|model_unavailable|model not found|cli_failed|failed after \d+ attempts"),
)
# most specific first; the earliest mention wins among the rest
_MUX = re.compile(r"(?i)final render failed|final mux|_final_mux")
_STEP_WORDS = (("thumbnail", r"thumbnail"),
               ("presenter", r"avatar|heygen|presenter|lip.?sync|talking head"),
               ("motion", r"\bmotion\b|video createtask|kling|\bveo\b|seedance|hailuo|image.to.video"),
               ("voice", r"narration|voice|\btts\b|speech|whisper|subtitle"),
               ("pictures", r"image|picture|gpt-image|nano.?banana|seedream|flux|illustration"),
               ("music", r"music|suno|soundtrack|sound effect|\bsfx\b"),
               ("clips", r"clip watch|watch service|watch_service|watch_failed|out_of_watches"),
               ("youtube", r"youtube|yt-dlp|yt_dlp|ytdlp|reference video|search_failed"),
               ("footage", r"pexels|b-roll|broll|footage|archive\.org"),
               ("threed", r"\b3d\b|three\.js|threed|webgl"),
               ("graphics", r"chromium|playwright|graphics|manim|headless|scene"),
               ("upload", r"upload"),
               ("render", r"ffmpeg|render|encod|assembl"),
               ("script", r"\bscript\b|research|description"))
_HEADERS = ((r"transcrib", "voice"), (r"^\d/\d\s+script|^script|description", "script"),
            (r"voiceover \+|in parallel|^running:", ""), (r"voiceover", "voice"), (r"graphics|scenes|mindmap", "graphics"),
            (r"assembl|final render", "render"), (r"thumbnail", "thumbnail"), (r"avatar", "presenter"),
            (r"b-roll", "footage"), (r"ai fotky|images", "pictures"), (r"offer|product", "graphics"),
            (r"^title:", "setup"))
_STAGE_CODES = {"usage_limit": "usage_limit", "timeout": "timeout", "auth_missing": "auth",
                "authentication_required": "auth", "network_interrupted": "blocked_network",
                "model_unavailable": "provider_error", "cli_failed": "provider_error", "invalid_result": "provider_error",
                "cancelled": "cancelled"}
_VOICE_CATEGORIES = {"moderation": "moderation", "balance": "balance", "access": "auth", "rate_limit": "rate_limit",
                     "cancelled": "cancelled", "model_failure": "provider_error", "request": "provider_error"}


def _earliest(text: str, table) -> str:
    best, where = "", len(text) + 1
    for name, pattern in table:
        found = re.search(pattern, text, re.I)
        if found and found.start() < where:
            best, where = name, found.start()
    return best


def provider_of(text: str) -> str:
    return _earliest(text or "", _PROVIDER_WORDS) or "none"


def category_of(text: str, provider: str = "none") -> str:
    for name, pattern in _CATEGORY_WORDS:
        if re.search(pattern, text or "", re.I):
            return name
    return "provider_error" if provider not in ("none", "") else "unknown"


def step_of(text: str, header: str = "", provider: str = "none") -> str:
    if _MUX.search(text or ""):
        return "mux"
    found = _earliest(text or "", _STEP_WORDS)
    if found:
        return found
    found = header_step(header)
    if found:
        return found
    return "script" if provider == "claude" else "unknown"


def header_step(header: str) -> str:
    header = (header or "").strip().lower()
    for pattern, step in _HEADERS:
        if re.search(pattern, header):
            return step
    return ""


def last_header(lines) -> str:
    """The step the engine announced last ("=== 3/4  Graphics (...) ===  [1:02]" -> "3/4  Graphics (...)")."""
    for line in reversed(list(lines or [])[-400:]):
        line = str(line).strip()
        if line.startswith("===") and not re.match(r"===\s*\S*\s*⚠", line):
            return re.sub(r"\s*===.*$", "", line[3:]).strip()
    return ""


def _neutral(text, titles=()) -> str:
    """The words a stop is classified by: its message without paths, titles or keys (a folder called "YouTube ideas"
    says nothing about YouTube), plus the services its links went to."""
    hosts = {m.group(1).lower() for m in re.finditer(r"(?i)\b(?:https?|wss?)://([^/\s:\"'<>]+)", str(text or ""))}
    return (sanitize(text, titles=titles, secrets=[], limit=0) + " " + " ".join(sorted(hosts))).strip()


def classify(text: str, *, header: str = "", code: str = "", titles=()) -> dict:
    """{failed_step, category, provider} for a stop's message (and a stop code, when its pipeline gave one)."""
    words = f"{code} {_neutral(text, titles)}".strip()
    provider = provider_of(words)
    return {"failed_step": step_of(words, header, provider), "category": category_of(words, provider),
            "provider": provider}


# ── this computer, this Studio ───────────────────────────────────────────────────────────────────────────────────

def os_family(platform: str | None = None) -> str:
    platform = platform or sys.platform
    return "windows" if platform.startswith(("win", "cygwin", "msys")) else "mac" if platform == "darwin" else "linux"


def studio_version(home: Path | None = None) -> str:
    try:
        version = json.loads((Path(home or HOME) / "cr-release.json").read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return ""
    return str(version)[:40] if isinstance(version, str) and re.fullmatch(r"[\w.\-]{1,40}", version) else ""


def recipe_id(style, home: Path | None = None) -> str:
    """The recipe as ChannelRecipe knows it. A creator's own cloned or designed style is "cloned_style" or
    "custom_style": its name is theirs."""
    style = str(style or "").strip()
    if not style:
        return ""
    if style.startswith("cr_clone_"):
        return "cloned_style"
    try:
        saved = json.loads((Path(home or HOME) / "styles" / (style + ".json")).read_text(encoding="utf-8"))
        if isinstance(saved, dict) and saved.get("builtin") is False:
            return "custom_style"
    except (OSError, ValueError):
        pass
    return style.lower()[:60] if re.fullmatch(r"[A-Za-z0-9_\-]{1,60}", style) else "custom_style"


def _choice(value, allowed, default):
    return value if value in allowed else default


def record(message, *, step: str = "", category: str = "", provider: str = "", header: str = "", code: str = "",
           recipe: str = "", titles=(), source: str = "video", home: Path | None = None) -> dict:
    """The failure record for one stop. Given step/category/provider win over what the message suggests."""
    guess = classify(str(message or ""), header=header, code=code, titles=titles)
    found = {"failed_step": _choice(step or guess["failed_step"], STEPS, "unknown"),
             "category": _choice(category or guess["category"], CATEGORIES, "unknown"),
             "provider": _choice(provider or guess["provider"], PROVIDERS, "none"),
             "message": sanitize(message, titles=titles),
             "version": studio_version(home), "os": os_family(), "recipe": recipe_id(recipe, home),
             "source": _choice(source, SOURCES, "video")}
    if found["category"] == "provider_error" and found["provider"] == "none":
        found["category"] = "unknown"
    return found


def from_exception(error: BaseException, **kw) -> dict:
    """The record for an exception that stopped work (a style study, a 3D operation)."""
    try:
        said = str(error).strip()
        message = f"{type(error).__name__}: {said}" if said else type(error).__name__
        if isinstance(error, (KeyboardInterrupt, SystemExit)) and not said:
            kw.setdefault("category", "cancelled")
        return record(message, **kw)
    except Exception:                                           # noqa: BLE001 - a record never stops a finish
        return {"failed_step": "unknown", "category": "unknown", "provider": "none", "message": "",
                "version": "", "os": os_family(), "recipe": "", "source": kw.get("source", "video")}


# ── a video job: its own evidence first, then its message ───────────────────────────────────────────────────────────

def _fresh(path: Path, since: float) -> bool:
    try:
        return path.is_file() and path.stat().st_mtime >= since - 5
    except OSError:
        return False


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _claude_stop(home: Path, project_id: str, since: float) -> str:
    """The error code of the newest Claude stage of this project that failed during this run, or ""."""
    if not re.fullmatch(r"crv_[a-f0-9]{24}", str(project_id or "")):
        return ""
    folder = Path(home) / "output" / "cr-claude-cache" / project_id
    try:
        stages = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:200]
    except OSError:
        return ""
    for path in stages:
        if not _fresh(path, since):
            break
        stage = _read(path)
        if stage.get("state") in ("failed", "failed_local"):
            return str(stage.get("error_code") or "cli_failed")
    return ""


def for_job(home, state: dict, spec: dict, job, project_id: str = "", error_code: str = "") -> dict:
    """The failure record for a video job that ended without its video: what the job itself left behind (a stop the
    worker explained, a Short's error.json, the voice's failure note, the final render's log, a failed Claude stage),
    then its message and the step it was on. Never raises."""
    state, spec = state or {}, spec or {}
    style = str(spec.get("style") or state.get("style") or "")
    titles = [t for t in (spec.get("title"), state.get("title")) if t]
    source = "timeline" if spec.get("timeline") else "short" if style.startswith("cr-shorts") else "video"
    base = {"recipe": style, "titles": titles, "source": source, "home": home}
    try:
        home, job = Path(home), Path(job) if job else None
        since = float(state.get("started") or 0) or (time.time() - 6 * 3600)
        message = str(state.get("error") or "").strip()
        header = last_header(state.get("lines"))
        hint = state.get("cr_failure") if isinstance(state.get("cr_failure"), dict) else {}
        if error_code == "cancelled" or state.get("state") == "cancelled":
            return record(hint.get("message") or message or "Cancelled on this computer.", category="cancelled",
                          step=header_step(header) or "unknown", header=header, **base)
        if hint:
            return record(hint.get("message") or message, category=hint.get("category", ""),
                          step=hint.get("step", ""), header=header, **base)
        if job is not None:
            short = _read(job / "short" / "error.json") if _fresh(job / "short" / "error.json", since) else {}
            if short:
                code = str(short.get("code") or "")
                found = classify(str(short.get("message") or message), code=code)
                if code.startswith("claude_") and found["failed_step"] == "unknown":
                    found["failed_step"] = "script"
                return record(short.get("message") or message, code=code, **{**base, "source": "short"},
                              step=found["failed_step"])
            voice = _read(job / "cr-voice-failed.json") if _fresh(job / "cr-voice-failed.json", since) else {}
            if voice and float(voice.get("at") or since) >= since - 5:
                said = classify(message)
                category = said["category"] if said["category"] in ("timeout", "blocked_network", "cancelled") else \
                    _VOICE_CATEGORIES.get(str(voice.get("category") or ""), "provider_error")
                provider = str(voice.get("provider") or "")
                return record(message or voice.get("reason"), step="voice", category=category,
                              provider=provider if provider in PROVIDERS else "other", **base)
            if _MUX.search(message) or _fresh(job / "cr-render-error.log", since):
                return record(message, step="mux", header=header, **base)
        stage = _claude_stop(home, project_id, since)
        if stage and (not message or re.search(r"(?i)claude", message) or classify(message)["provider"] == "none"):
            return record(message or "A Claude stage stopped.", provider="claude",
                          category=_STAGE_CODES.get(stage, "provider_error"),
                          step=step_of(re.sub(r"(?i)claude", "", message), header, "claude"), header=header, **base)
        return record(message or "The video stopped without a message.", header=header, **base)
    except Exception:                                           # noqa: BLE001 - a record never stops a finish
        return from_exception(RuntimeError(str((state or {}).get("error") or "")), **base)


def update_note(answer) -> str:
    """The server's one plain line about a newer Studio, when its answer to a stopped finish has one."""
    note = (answer or {}).get("update_note") if isinstance(answer, dict) else ""
    note = " ".join(str(note or "").split())
    return note[:300] if note and not re.search(r"https?://|<", note) else ""
