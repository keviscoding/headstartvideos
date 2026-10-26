"""Why a video stopped, as a short record ChannelRecipe can count.

A stopped project's finish (cr_bridge.finish_project) carries `failure`: the step it stopped at, the kind of problem,
the service involved, one cleaned line of its message (at most MAX_MESSAGE characters), and this Studio's version, the
computer's family (windows, mac or linux) and the recipe. Never the video's content: web addresses, file paths (only
a file's extension stays: "[path].mp4"), emails, names, keys, tokens, the video's title and any quoted or labelled
words of its prompts or narration are taken out of the message, and nothing of the script, voice or pictures is read.

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

MAX_INPUT = 4000                      # a message is cut to this before any cleaning: a long traceback stays quick
MAX_TITLE = 300

_URL = re.compile(r"(?i)\b(?:https?|wss?|ftp|file)://[^\s\"'<>)\]]+|\bwww\.[^\s\"'<>)\]]+")
_HOST_PATH = re.compile(r"(?i)\b((?:[a-z0-9-]+\.)+[a-z]{2,})/[^\s\"'<>)\]]*")
_EMAIL = re.compile(r"(?<![\w.+%-])[\w.+%-]+(?:@|%40|%2540)[\w-]+(?:\.[\w-]+)+")     # jane@…, jane%40… in a link
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[^\s,;\"']+")
_ASSIGNED = re.compile(r"(?i)\b([\w-]*(?:api[_-]?key|apikey|key|token|secret|password|passwd|authorization|cookie"
                       r"|signature|credential)s?)(['\"]?\s*[=:]\s*['\"]?)[^\s,;\"'&}]+")
_PREFIXED = re.compile(r"\b(?:cr_mcp_|sk-|sk_|rk_|pk_|xi_|ghp_|gho_|github_pat_|xox[abposr]-|AIza|ya29\.|eyJ)"
                       r"[\w.\-]{6,}")
_UUID = re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_HEX_ID = re.compile(r"(?i)\b(?:crv_|op_|video_|b_|cc_)?[0-9a-f]{12,}\b")
_LONG_TOKEN = re.compile(r"(?<![\w~…])(?=[A-Za-z0-9_\-+/=]*\d)(?=[A-Za-z0-9_\-+/=]*[A-Za-z])[A-Za-z0-9_\-+/=]{24,}")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
# eight groups, or fewer around a "::" (a time like 12:30:45 or ffmpeg's 0:a:0 has neither; std::bad_alloc is a word's)
_IPV6 = re.compile(r"(?i)(?<![\w:.])(?:(?:[0-9a-f]{1,4}:){7}[0-9a-f]{1,4}|[0-9a-f]{1,4}(?::[0-9a-f]{1,4}){0,6}::"
                   r"(?:[0-9a-f]{1,4}(?::[0-9a-f]{1,4}){0,6})?|::[0-9a-f]{1,4}(?::[0-9a-f]{1,4}){0,6})(?:%[\w.]+)?(?![\w:])")
# "Jane Doe <…>", "jane doe <…>", "\"J. Doe\" <…>": a name before an email
_EMAIL_NAME = re.compile(r"(?:\"[^\"\n]{1,60}\"\s*|(?<![\w'’.\-])(?:(?:[A-Z][\w'’.\-]*\s+){1,4}"
                         r"|(?:[^\W\d_][\w'’.\-]*\s+){1,2}))<\s*\[email\]\s*>")
_HANDLE = re.compile(r"(?<![\w@.%/])@(?=[\w.\-]*[A-Za-z])[A-Za-z0-9_](?:[\w.\-]{0,28}\w)?(?![\w@/\-]|\.\w)")  # not @scope/pkg
_ACCOUNT_NO = re.compile(r"(?i)\b(user|account|customer|member|subscriber)((?:[ _-]?id)?\s*[#:=]?\s*)\d{2,}\b")
_PC_NAME = re.compile(r"\b(?:DESKTOP|LAPTOP|WIN)-[A-Z0-9]{5,}\b")
# Where a path starts: a drive (C:\ or C:/), a share (\\computer\share), file://, ~, ./ or ../, … or the places a Mac
# or Linux keeps people's files. From there a folder's name may hold anything (spaces, apostrophes, dashes), so all of
# it up to the last separator goes, and of the file only its extension stays: "[path].mp4".
_ROOT = re.compile(r"(?i)(?<![\w.:/\\~…-])(?:file:/{2,3}(?:[a-z]:)?|[a-z]:(?=[\\/])|\\{2,}(?=[^\\/\s]+[\\/])"
                   r"|(?:~|\.{1,2}|…)(?=[\\/])"
                   r"|(?=[\\/](?:users|home|volumes|private|tmp|var|root|mnt|media|srv)(?:[\\/]|$)))")
_EXT = re.compile(r"\.((?=[A-Za-z0-9]{0,4}[A-Za-z])[A-Za-z0-9]{1,5})(?!\w|\.\w)")
# what is left that looks like a path (the computer's own folders: /opt/homebrew/bin/ffmpeg): folders go, the file's
# name stays unless it holds the title
_SEG = r"[^\\/\s\"'<>|:!,;()\[\]{}]+"
_PATH = re.compile(r"(?<![\w:/\\~…\]])(?P<dirs>(?:[\\/]" + _SEG + r"(?: " + _SEG + r"){0,2}(?=[\\/]))*)"
                   r"[\\/](?P<base>[^\\/\s\"'<>|:,;()\[\]{}]+)?")
# A label for the video's own words ("text: …", 'prompt': '…', scene 4: …): what it labels is theirs, to the end of
# its clause.
_LABEL = re.compile(r"(?i)(?<![\w\[-])['\"]?(?:text|prompt|negative[_ ]prompt|script|title|narration|captions?"
                    r"|description|transcript|lyrics|query|topic|subject|story|sentence"
                    r"|(?:scene|beat|chapter|shot|clip|slide|segment)(?:\s*#?\d+)?)['\"]?(?:\s*[:=]\s*|\s+(?=['\"“‘]))")
# quoted words, three or more: a prompt, a line of narration, a scene's or a title's words. 'beats', 'api.kie.ai' and
# "you're" stay. A quote the message's end cut off counts too.
_QUOTED = re.compile(r"(?<![\w'\"‘’“”`])(?:'(?P<a>(?:[^'\n]|(?<=\w)'(?=\w))*)(?:'(?!\w)|$)|\"(?P<b>[^\"\n]*)(?:\"|$)"
                     r"|“(?P<c>[^”\n]*)(?:”|$)|‘(?P<d>(?:[^’\n]|(?<=\w)’(?=\w))*)(?:’(?!\w)|$)|`(?P<e>[^`\n]*)(?:`|$))")
_CLOSES = {"'": "'", '"': '"', "“": "”", "‘": "’", "`": "`"}
_BOILERPLATE = [re.compile(p, re.I) for p in (
    r"Wait a few minutes[^.]*(?:\.|$)", r"Saved work is kept[^.]*\.", r"Your (?:local |saved |generated )?(?:output|progress|work|study|scenes and voice)"
    r"[^.]{0,40}(?:saved|kept|intact)[^.]*\.", r"Check Video tools in Setup & accounts\.",
    r"\(Copy diagnostics[^)]*\)", r"(?:Press|press) Resume saved project[^.]*\.", r"It will not run again automatically\.",
    r"The details are in the project's[^.]*\.", r"Nothing was (?:spent|charged|changed)[^.]*\.",
    r"(?:,? then )?review (?:this saved stage|recovery|its status|stopped Claude work)[^.]*\.")]
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


_GENERIC_NAMES = {"user", "users", "admin", "administrator", "owner", "default", "public", "guest", "home", "runner",
                  "desktop", "laptop", "computer", "local", "localhost", "macbook", "imac", "studio", "video", "videos"}


def _names(home) -> list:
    """This computer's user name (its home folder's, the environment's) and its own name, with their parts ("Jane
    Doe" -> Jane, Doe), to take out of a message where no path holds them. Four letters at least: a shorter word
    ("max", "kev") is as likely an ordinary word of the message."""
    found = set()
    try:
        import socket
        host = socket.gethostname().split(".")[0]
    except (OSError, ValueError):
        host = ""
    folder = re.split(r"[\\/]+", str(home or "").rstrip("\\/"))[-1]     # either kind of path, on any computer
    values = [folder, os.environ.get("USER"), os.environ.get("USERNAME"), host, host.split("-")[0]]
    for value in values:
        value = " ".join(str(value or "").split())
        for part in [value] + re.split(r"[\s._\-]+", value):
            if len(part) >= 4 and part.lower() not in _GENERIC_NAMES and not part.isdigit():
                found.add(part)
    return sorted(found, key=len, reverse=True)


def _quote_end(text: str, i: int, close: str) -> int:
    """Where the quote opened just before `i` closes (an apostrophe inside a word, "Jane's", doesn't), or -1."""
    while True:
        i = text.find(close, i)
        if i < 0 or close not in "'’" or not (text[i - 1:i].isalnum() and text[i + 1:i + 2].isalnum()):
            return i
        i += 1


def _clause_end(text: str, i: int) -> int:
    """Where the clause from `i` ends: at a ";" or "|", a bracket it didn't open, or the end."""
    depth = 0
    for j in range(i, len(text)):
        c = text[j]
        if c in ";|" and not depth:
            return j
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if not depth:
                return j
            depth -= 1
    return len(text)


def _path_end(text: str, i: int) -> int:
    """Where the path whose root ends at `i` ends: at a quote (not an apostrophe inside a word: "Jane's Channel"), a
    colon, semicolon or bar before a space, a bracket it didn't open, a space before another path or a link, or the
    end."""
    depth, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"`“”" or c in "'‘’" and not (text[i - 1:i].isalnum() and text[i + 1:i + 2].isalnum()):
            return i
        if c in ":;|" and text[i + 1:i + 2] in (" ", ""):
            return i
        if c in "([{<":
            depth += 1
        elif c in ")]}>":
            if not depth:
                return i
            depth -= 1
        elif c == " " and (_ROOT.match(text, i + 1) or _URL.match(text, i + 1)):
            return i
        i += 1
    return n


def _paths(text: str) -> str:
    """Every path from a root (_ROOT) becomes "[path]" and its file's extension: "[path].mp4". The words after a file's
    extension stay ("[path].mp3 failed"); with no extension, the path runs to its end (_path_end)."""
    out, pos = [], 0
    while True:
        found = _ROOT.search(text, pos)
        if not found:
            break
        start, end = found.start(), _path_end(text, found.end())
        cut = max(text.rfind("/", start, end), text.rfind("\\", start, end))
        base = text[cut + 1:end] if cut >= start else ""
        ext = _EXT.search(base)
        out.append(text[pos:start] + ("[path]." + ext.group(1) + base[ext.end():] if ext else "[path]"))
        pos = end
    return "".join(out) + text[pos:]


def _bodies(text: str) -> str:
    """A provider's answer pasted into a message ({...}) keeps only its code, status and message fields: a request
    body may hold the prompt, the narration or a link. A list of quoted or comma-separated items ([...]) goes whole;
    a bracketed word like "[Errno 28]" or "[youtube]" stays."""
    pairs, stacks = {}, {"{": [], "[": []}                       # each bracket's closing one, found in one pass
    for n, c in enumerate(text):
        if c in stacks:
            stacks[c].append(n)
        elif c in "}]" and stacks["{" if c == "}" else "["]:
            pairs[stacks["{" if c == "}" else "["].pop()] = n + 1
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if c not in "{[":
            out.append(c)
            i += 1
            continue
        j = pairs.get(i, 0 if c == "[" else len(text))
        body = text[i:j]
        if c == "[":
            if not j or not re.search(r"['\"‘’“”`]|,", body[1:-1]):
                out.append(c)
                i += 1
            else:
                out.append("[…]")
                i = j
            continue
        fields = [f"{k}: {(a or b or c).strip()}" for k, a, b, c in _FIELDS.findall(body) if (a or b or c).strip()][:4]
        out.append("(" + "; ".join(fields) + ")" if fields else "(…)")
        i = j
    return "".join(out)


def _labelled(text: str) -> str:
    """What a label for the video's own words (_LABEL) is followed by becomes "…": a quoted value whatever its length,
    otherwise the rest of its clause."""
    out, pos = [], 0
    while True:
        found = _LABEL.search(text, pos)
        if not found:
            break
        i = found.end()
        close = _CLOSES.get(text[i:i + 1])
        end = _quote_end(text, i + 1, close) if close else -1
        if end >= 0:
            out.append(text[pos:i + 1] + "…" + close)
            pos = end + 1
        else:
            end = _clause_end(text, i)
            out.append(text[pos:i] + "…")
            pos = end
    return "".join(out) + text[pos:]


def _quoted(match) -> str:
    whole = match.group(0)
    inner = next(g for g in match.groups() if g is not None)
    if len(inner.split()) < 3:
        return whole
    return whole[0] + "…" + (whole[-1] if len(whole) == len(inner) + 2 else "")


_JOIN = r"[\W_]{1,3}"


def _title_pattern(titles):
    """One regular expression for the titles as a message may hold them: whole, as a folder name (slug), or cut short.
    A cut-short title is three of its words in a row, then any more of its words and the start of one before an
    ellipsis. Three short words only ("is not a") say nothing of a title and are common in error messages, so a run
    needs a word of four letters or more. At most MAX_TITLE characters of a title, so at most a hundred or so runs:
    one pattern, built once a message, keeps a long title quick. None when there is no title."""
    seeds, words = {}, set()
    for title in titles or ():
        title = " ".join(str(title or "").split())[:MAX_TITLE]
        found = [w.lower() for w in re.findall(r"[^\W_]+", title)]
        if len(title) < 3 or not found:
            continue
        words.update(found)
        seeds[_JOIN.join(map(re.escape, found))] = len(found)          # the whole title, however short its words
        for i in range(len(found) - 2):
            if any(len(w) >= 4 for w in found[i:i + 3]):
                seeds.setdefault(_JOIN.join(map(re.escape, found[i:i + 3])), 3)
    if not seeds:
        return None
    alternatives = "|".join(sorted(seeds, key=lambda s: (-seeds[s], -len(s))))
    more = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(r"(?<![^\W_])(?:" + alternatives + r")(?![^\W_])(?:" + _JOIN + r"(?:" + more + r")(?![^\W_]))*"
                      r"(?:" + _JOIN + r"[^\W_]{1,30}(?=…|\.\.\.))?", re.I)


def _title_words(titles) -> set:
    return {w for t in titles or () for w in re.findall(r"[a-z0-9]{4,}", str(t or "").lower()[:MAX_TITLE])}


def sanitize(text, *, titles=(), secrets=None, home=None, names=None, limit=MAX_MESSAGE) -> str:
    """One line of `text` that is safe to send: no URLs, file paths ("[path].mp4"), emails, names, handles, keys,
    tokens, ids, addresses, titles or the video's own words (quoted or labelled), at most `limit` characters. `names`
    defaults to this computer's (_names). Only the first MAX_INPUT characters are read."""
    text = " ".join(str(text or "")[:MAX_INPUT].split())
    if not text:
        return ""
    for secret in (secrets if secrets is not None else _secrets()):
        if secret:
            text = text.replace(secret, "[hidden]")
    try:
        home = str(home if home is not None else Path.home()).rstrip("\\/")
    except (RuntimeError, OSError):
        home = ""
    # this Studio's folder and the home folder become ~, wherever they are, so their paths go too
    for root in sorted({str(HOME).rstrip("\\/"), home}, key=len, reverse=True):
        if len(root) < 3:
            continue
        for form in {root, root.replace("\\", "/"), root.replace("/", "\\")}:
            text = re.sub(r"(?<![\w.:/\\~…-])" + re.escape(form) + r"(?=[\\/\s\"',;:)]|$)", "~", text, flags=re.I)
    text = _paths(text)
    text = _URL.sub("[link]", text)
    text = _EMAIL.sub("[email]", text)
    text = _EMAIL_NAME.sub("[email]", text)                         # "Jane Doe <jane@…>"
    text = _HOST_PATH.sub(r"\1/…", text)
    text = _BEARER.sub(r"\1 [hidden]", text)
    text = _ASSIGNED.sub(r"\1\2[hidden]", text)
    text = _PREFIXED.sub("[hidden]", text)
    text = _bodies(text)
    text = _labelled(text)
    text = _QUOTED.sub(_quoted, text)
    words, title = _title_words(titles), _title_pattern(titles)

    def path(match):
        base = match.group("base") or ""
        folders = [p for p in re.split(r"[\\/]+", match.group("dirs")) if p]
        if not folders:
            return match.group(0)                                  # "/" or "and/or": not a path
        if words and any(w in base.lower() for w in words) or title and title.search(base):
            suffix = Path(base).suffix
            base = "[file]" + (suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,5}", suffix) else "")
        return "…/" + base
    text = _PATH.sub(path, text)
    if title:
        text = title.sub("[title]", text)
    # after paths and titles, so a name in a folder or a title goes with it
    for name in (names if names is not None else _names(home)):
        text = re.sub(r"(?<![\w\[])" + re.escape(name) + r"(?![\w\]])", "[name]", text, flags=re.I)
    text = _HANDLE.sub("[handle]", text)
    text = _PC_NAME.sub("[computer]", text)
    text = _UUID.sub("[id]", text)
    text = _HEX_ID.sub("[id]", text)
    text = _ACCOUNT_NO.sub(r"\1\2[id]", text)
    text = _LONG_TOKEN.sub("[hidden]", text)
    text = _IPV4.sub("[ip]", text)
    text = _IPV6.sub("[ip]", text)
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
    hosts = {m.group(1).lower() for m in re.finditer(r"(?i)\b(?:https?|wss?)://([^/\s:\"'<>]+)",
                                                     str(text or "")[:MAX_INPUT])}
    return (sanitize(text, titles=titles, secrets=[], names=(), limit=0) + " " + " ".join(sorted(hosts))).strip()


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
           recipe: str = "", titles=(), source: str = "video", home: Path | None = None,
           default_step: str = "unknown") -> dict:
    """The failure record for one stop. Given step/category/provider win over what the message suggests."""
    guess = classify(str(message or ""), header=header, code=code, titles=titles)
    if guess["failed_step"] == "unknown":
        guess["failed_step"] = default_step
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
        if isinstance(error, (KeyboardInterrupt, SystemExit)) and not said and not kw.get("category"):
            kw["category"] = "cancelled"
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
                found = classify(str(short.get("message") or message), code=code, titles=titles)
                if code.startswith("claude_") and found["failed_step"] == "unknown":
                    found["failed_step"] = "script"
                return record(short.get("message") or message, code=code, **{**base, "source": "short"},
                              step=found["failed_step"])
            voice = _read(job / "cr-voice-failed.json") if _fresh(job / "cr-voice-failed.json", since) else {}
            if voice and float(voice.get("at") or since) >= since - 5:
                said = classify(message, titles=titles)
                category = said["category"] if said["category"] in ("timeout", "blocked_network", "cancelled") else \
                    _VOICE_CATEGORIES.get(str(voice.get("category") or ""), "provider_error")
                provider = str(voice.get("provider") or "")
                return record(message or voice.get("reason"), step="voice", category=category,
                              provider=provider if provider in PROVIDERS else "other", **base)
            if _MUX.search(message) or _fresh(job / "cr-render-error.log", since):
                return record(message, step="mux", header=header, **base)
        stage = _claude_stop(home, project_id, since)
        if stage and (not message or re.search(r"(?i)claude", message)
                      or classify(message, titles=titles)["provider"] == "none"):
            return record(message or "A Claude stage stopped.", provider="claude",
                          category=_STAGE_CODES.get(stage, "provider_error"),
                          step=step_of(re.sub(r"(?i)claude", "", message), header, "claude"), header=header, **base)
        return record(message or "The video stopped without a message.", header=header, **base)
    except Exception:                                           # noqa: BLE001 - a record never stops a finish
        return from_exception(RuntimeError(str((state or {}).get("error") or "")), **base)


def report_stop(error: BaseException, *, source: str, recipe: str = "", titles=(), default_step: str = "unknown",
                on_note=None) -> None:
    """A stop that ends no project of its own (Studio Cloud's preparing): its record goes to ChannelRecipe on its own, in
    the background. An older server has no address for it, and any failure to send is ignored. on_note(line) gets the
    server's line about a newer Studio, when it has one."""
    import threading

    def send():
        try:
            try:
                import cr_bridge
            except ImportError:                                    # pragma: no cover - imported as a package in tests
                from . import cr_bridge
            failure = from_exception(error, source=source, recipe=recipe, titles=titles, default_step=default_step)
            note = update_note(cr_bridge.call("failures", {"failure": failure}))
            if note and on_note is not None:
                on_note(note)
        except Exception:                                       # noqa: BLE001 - counting a stop never adds one
            pass
    threading.Thread(target=send, daemon=True, name="cr-failure-report").start()


def update_note(answer) -> str:
    """The server's one plain line about a newer Studio, when its answer to a stopped finish has one."""
    note = (answer or {}).get("update_note") if isinstance(answer, dict) else ""
    note = " ".join(str(note or "").split())
    return note[:300] if note and not re.search(r"https?://|<", note) else ""
