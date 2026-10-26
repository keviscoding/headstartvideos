"""Why customers' Studio videos stop, counted for the team.

A Studio sends a short failure record with a stopped project (local_runtime/cr_failure.py): the step it stopped at,
the kind of problem, the service involved, one cleaned line of its message, the Studio's version, the computer's
family (windows, mac or linux) and the recipe. The latest record of each project is kept in frontier_failures for
MAX_DAYS days (a stop that ends no project, like Studio Cloud's preparing, gets its own row). GET /api/admin/failures
counts them.

Only counts and cleaned messages leave here: never an email, a user id, a project id or a title. The message is
cleaned again here (and the project's title taken out), so a Studio's mistake can't put anything private on record.
"""
from __future__ import annotations

import re
import secrets
import threading
import time
from collections import Counter

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, model_validator

from webapp import database as db

MAX_MESSAGE = 240
STEPS = {"setup", "script", "voice", "pictures", "footage", "motion", "presenter", "youtube", "clips", "music",
         "graphics", "threed", "render", "mux", "thumbnail", "upload", "study", "save", "cloud", "unknown"}
CATEGORIES = {"provider_error", "timeout", "disk_full", "out_of_memory", "crash", "blocked_network", "usage_limit",
              "balance", "auth", "moderation", "rate_limit", "missing_tool", "tool_error", "invalid_input",
              "studio_closed", "cancelled", "unknown"}
PROVIDERS = {"kie", "elevenlabs", "claude", "youtube", "channelrecipe", "other", "none"}
SOURCES = {"video", "short", "timeline", "style_study", "threed", "cloud"}
OS_FAMILIES = {"windows", "mac", "linux"}
REPORTS_PER_HOUR = 20                 # stops that end no project (Studio Cloud's preparing), per account
TOP_MESSAGES = 30
MAX_DAYS = 90
NO_STORE = {"Cache-Control": "private, no-store"}

_ready = set()
_ready_lock = threading.Lock()
_pruned: dict = {}                    # database -> when its rows older than MAX_DAYS were last taken out


# ── the message, cleaned again ────────────────────────────────────────────────────────────────────────────────────

MAX_INPUT = 4000
# where a path starts (a drive, a share, file://, ~, ./, a Mac's or Linux's places for people's files): from there
# every folder goes whatever its name holds, and of the file only its extension stays ("[path].mp4"). A Studio's own
# "…/ffmpeg" (a tool in the computer's own folders) stays as it is.
_ROOT = re.compile(r"(?i)(?<![\w.:/\\~…-])(?:file:/{2,3}(?:[a-z]:)?|[a-z]:(?=[\\/])|\\{2,}(?=[^\\/\s]+[\\/])"
                   r"|(?:~|\.{1,2})(?=[\\/])"
                   r"|(?=[\\/](?:users|home|volumes|private|tmp|var|root|mnt|media|srv)(?:[\\/]|$)))")
_EXT = re.compile(r"\.((?=[A-Za-z0-9]{0,4}[A-Za-z])[A-Za-z0-9]{1,5})(?!\w|\.\w)")
_SEG = r"[^\s\\/\"'<>|:!,;()\[\]{}]+"
# a label for the video's own words ("text: …", prompt '…', scene 4: …), and quoted words (three or more)
_LABEL = re.compile(r"(?i)(?<![\w\[-])['\"]?(?:text|prompt|negative[_ ]prompt|script|title|narration|captions?"
                    r"|description|transcript|lyrics|query|topic|subject|story|sentence"
                    r"|(?:scene|beat|chapter|shot|clip|slide|segment)(?:\s*#?\d+)?)['\"]?(?:\s*[:=]\s*|\s+(?=['\"“‘]))")
_QUOTED = re.compile(r"(?<![\w'\"‘’“”`])(?:'(?P<a>(?:[^'\n]|(?<=\w)'(?=\w))*)(?:'(?!\w)|$)|\"(?P<b>[^\"\n]*)(?:\"|$)"
                     r"|“(?P<c>[^”\n]*)(?:”|$)|‘(?P<d>(?:[^’\n]|(?<=\w)’(?=\w))*)(?:’(?!\w)|$)|`(?P<e>[^`\n]*)(?:`|$))")
_CLOSES = {"'": "'", '"': '"', "“": "”", "‘": "’", "`": "`"}
_LINKS = (
    (re.compile(r"(?i)\b(?:https?|wss?|ftp|file)://[^\s\"'<>)\]]+|\bwww\.[^\s\"'<>)\]]+"), "[link]"),
    (re.compile(r"(?<![\w.+%-])[\w.+%-]+(?:@|%40|%2540)[\w-]+(?:\.[\w-]+)+"), "[email]"),     # jane%40… too
    (re.compile(r"(?:\"[^\"\n]{1,60}\"\s*|(?<![\w'’.\-])(?:(?:[A-Z][\w'’.\-]*\s+){1,4}|(?:[^\W\d_][\w'’.\-]*\s+){1,2}))"
                r"<\s*\[email\]\s*>"), "[email]"),                                       # "Jane Doe <…>", "jane doe <…>"
    (re.compile(r"(?i)\b(bearer|basic)\s+[^\s,;\"']+"), r"\1 [hidden]"),
    (re.compile(r"(?i)\b([\w-]*(?:api[_-]?key|apikey|key|token|secret|password|passwd|authorization|cookie|signature"
                r"|credential)s?)(['\"]?\s*[=:]\s*['\"]?)[^\s,;\"'&}]+"), r"\1\2[hidden]"),
    (re.compile(r"\b(?:cr_mcp_|sk-|sk_|rk_|pk_|xi_|ghp_|gho_|github_pat_|xox[abposr]-|AIza|ya29\.|eyJ)[\w.\-]{6,}"),
     "[hidden]"),
)
_CLEAN = (
    # the computer's own folders (/opt/homebrew/bin/ffmpeg): folders dropped, the file's name stays
    (re.compile(r"(?<![\w:/\\~…\]])(?:[\\/]" + _SEG + r"(?: " + _SEG + r"){0,2})+[\\/]"), "…/"),
    (re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "[id]"),
    (re.compile(r"(?i)\b(?:crv_|op_|video_|b_|cc_)?[0-9a-f]{12,}\b"), "[id]"),
    (re.compile(r"(?i)\b(user|account|customer|member|subscriber)((?:[ _-]?id)?\s*[#:=]?\s*)\d{2,}\b"), r"\1\2[id]"),
    (re.compile(r"\b(?:DESKTOP|LAPTOP|WIN)-[A-Z0-9]{5,}\b"), "[computer]"),
    (re.compile(r"(?<![\w@.%/])@(?=[\w.\-]*[A-Za-z])[A-Za-z0-9_](?:[\w.\-]{0,28}\w)?(?![\w@/\-]|\.\w)"), "[handle]"),
    (re.compile(r"(?<![\w~…])(?=[A-Za-z0-9_\-+/=]*\d)(?=[A-Za-z0-9_\-+/=]*[A-Za-z])[A-Za-z0-9_\-+/=]{24,}"),
     "[hidden]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[ip]"),
    (re.compile(r"(?i)(?<![\w:.])(?:(?:[0-9a-f]{1,4}:){7}[0-9a-f]{1,4}|[0-9a-f]{1,4}(?::[0-9a-f]{1,4}){0,6}::"
                r"(?:[0-9a-f]{1,4}(?::[0-9a-f]{1,4}){0,6})?|::[0-9a-f]{1,4}(?::[0-9a-f]{1,4}){0,6})(?:%[\w.]+)?(?![\w:])"),
     "[ip]"),                                                                            # 2001:db8::1, not 12:30:45
)


def _path_end(text: str, i: int) -> int:
    """A path ends at a quote (not an apostrophe inside a word), a colon, semicolon or bar before a space, a bracket
    it didn't open, a space before another path or a link, or the end."""
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
        elif c == " " and (_ROOT.match(text, i + 1) or _LINKS[0][0].match(text, i + 1)):
            return i
        i += 1
    return n


def _paths(text: str) -> str:
    out, pos = [], 0
    while found := _ROOT.search(text, pos):
        start, end = found.start(), _path_end(text, found.end())
        cut = max(text.rfind("/", start, end), text.rfind("\\", start, end))
        base = text[cut + 1:end] if cut >= start else ""
        ext = _EXT.search(base)
        out.append(text[pos:start] + ("[path]." + ext.group(1) + base[ext.end():] if ext else "[path]"))
        pos = end
    return "".join(out) + text[pos:]


def _brackets(text: str) -> str:
    """A pasted body ({...}) and a list of quoted or comma-separated items ([...]) go whole; "[Errno 28]" stays."""
    pairs, stacks = {}, {"{": [], "[": []}
    for n, c in enumerate(text):
        if c in stacks:
            stacks[c].append(n)
        elif c in "}]" and stacks["{" if c == "}" else "["]:
            pairs[stacks["{" if c == "}" else "["].pop()] = n + 1
    out, i = [], 0
    while i < len(text):
        c, j = text[i], pairs.get(i, 0)
        if c == "{" or c == "[" and j and re.search(r"['\"‘’“”`]|,", text[i + 1:j - 1]):
            out.append("(…)" if c == "{" else "[…]")
            i = j or len(text)
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _quote_end(text: str, i: int, close: str) -> int:
    while True:
        i = text.find(close, i)
        if i < 0 or close not in "'’" or not (text[i - 1:i].isalnum() and text[i + 1:i + 2].isalnum()):
            return i
        i += 1


def _labelled(text: str) -> str:
    """What a label for the video's own words is followed by becomes "…": a quoted value, or the rest of its clause
    (to a ";" or "|", a bracket it didn't open, or the end)."""
    out, pos = [], 0
    while found := _LABEL.search(text, pos):
        i = found.end()
        close = _CLOSES.get(text[i:i + 1])
        end = _quote_end(text, i + 1, close) if close else -1
        if end >= 0:
            out.append(text[pos:i + 1] + "…" + close)
            pos = end + 1
            continue
        depth, end = 0, len(text)
        for j in range(i, len(text)):
            if text[j] in ";|" and not depth or text[j] in ")]}" and not depth:
                end = j
                break
            depth += 1 if text[j] in "([{" else -1 if text[j] in ")]}" else 0
        out.append(text[pos:i] + "…")
        pos = end
    return "".join(out) + text[pos:]


def _quoted(match) -> str:
    whole = match.group(0)
    inner = next(g for g in match.groups() if g is not None)
    if len(inner.split()) < 3:
        return whole
    return whole[0] + "…" + (whole[-1] if len(whole) == len(inner) + 2 else "")


def clean_message(text, titles=()) -> str:
    """One line with no links, paths ("[path].mp4"), emails (nor the name before one), handles, keys, ids, addresses
    or computer names, no quoted or labelled words of the video's own and no title; MAX_MESSAGE at most."""
    text = _paths(" ".join(str(text or "")[:MAX_INPUT].split()))
    for pattern, replacement in _LINKS:
        text = pattern.sub(replacement, text)
    text = _QUOTED.sub(_quoted, _labelled(_brackets(text)))
    for pattern, replacement in _CLEAN:
        text = pattern.sub(replacement, text)
    forms = set()
    for title in titles or ():
        title = " ".join(str(title or "").split())[:300]
        if len(title) >= 3:
            slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            forms.update(form for form in (title, slug, slug.replace("-", "_")) if len(form) >= 3)
    if forms:                                                   # one pattern, longest form first
        text = re.sub("|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True)), "[title]", text, flags=re.I)
    text = " ".join(text.split())
    return text if len(text) <= MAX_MESSAGE else text[:MAX_MESSAGE - 1].rstrip() + "…"


class FailureRecord(BaseModel):
    """What a Studio says about a stop. Lenient on purpose: an unknown or newer value becomes "other" rather than
    refusing the finish it comes with."""
    model_config = ConfigDict(extra="ignore")
    failed_step: str = "unknown"
    category: str = "unknown"
    provider: str = "none"
    message: str = ""
    version: str = ""
    os: str = ""
    recipe: str = ""
    source: str = "video"

    @model_validator(mode="before")
    @classmethod
    def _lenient(cls, data):
        data = data if isinstance(data, dict) else {}

        def word(name, allowed, default):
            value = str(data.get(name) or "").strip().lower()[:40]
            return default if not value else value if value in allowed else "other"
        version = str(data.get("version") or "").strip()
        recipe = str(data.get("recipe") or "").strip().lower()
        return {"failed_step": word("failed_step", STEPS, "unknown"), "category": word("category", CATEGORIES, "unknown"),
                "provider": word("provider", PROVIDERS, "none"), "message": clean_message(data.get("message")),
                "version": version if re.fullmatch(r"[\w.\-]{1,40}", version) else "",
                "os": word("os", OS_FAMILIES, ""),
                "recipe": recipe if re.fullmatch(r"[a-z0-9_\-]{1,60}", recipe) else "other" if recipe else "",
                "source": word("source", SOURCES, "video")}


# ── kept ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def _key():
    return (db.IS_PG, db.DATABASE_URL if db.IS_PG else str(db.DB_PATH))


def initialize():
    """The table, made once (the server's startup calls this, so two web workers never race to make it)."""
    key = _key()
    if key in _ready:
        return
    with _ready_lock:
        if key in _ready:
            return
        with db._conn() as conn:
            if db.IS_PG:
                conn.execute("SELECT pg_advisory_xact_lock(726346123)")    # one web process at a time
            # one row per stopped project (its latest stop) or per stop that ends no project; user_id only limits
            # how many of those an account may send, and never leaves this table
            conn.execute("""CREATE TABLE IF NOT EXISTS frontier_failures (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL DEFAULT '', user_id INTEGER NOT NULL,
                source TEXT NOT NULL DEFAULT 'video', error_code TEXT NOT NULL DEFAULT '',
                failed_step TEXT NOT NULL DEFAULT 'unknown', category TEXT NOT NULL DEFAULT 'unknown',
                provider TEXT NOT NULL DEFAULT 'none', message TEXT NOT NULL DEFAULT '',
                version TEXT NOT NULL DEFAULT '', os TEXT NOT NULL DEFAULT '', recipe TEXT NOT NULL DEFAULT '',
                stops INTEGER NOT NULL DEFAULT 1, created_at DOUBLE PRECISION NOT NULL,
                updated_at DOUBLE PRECISION NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_frontier_failures_updated ON frontier_failures(updated_at)")
            _prune(conn, key)
        _ready.add(key)


def _prune(conn, key, now: float | None = None):
    """Rows older than the longest report (MAX_DAYS) can show nothing: they go, at most once an hour per database."""
    now = time.time() if now is None else now
    if now - _pruned.get(key, 0.0) < 3600:
        return
    _pruned[key] = now
    conn.execute(db._q("DELETE FROM frontier_failures WHERE updated_at<?"), (now - MAX_DAYS * 86400,))


def _save(row_id: str, project_id: str, user_id: int, error_code: str, failure: FailureRecord, titles=()):
    initialize()
    now = time.time()
    message = clean_message(failure.message, titles)
    with db._conn() as conn:
        _prune(conn, _key(), now)
        conn.execute(db._q("""INSERT INTO frontier_failures (id,project_id,user_id,source,error_code,failed_step,category,
            provider,message,version,os,recipe,stops,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)
            ON CONFLICT(id) DO UPDATE SET source=excluded.source,error_code=excluded.error_code,
            failed_step=excluded.failed_step,category=excluded.category,provider=excluded.provider,
            message=excluded.message,version=excluded.version,os=excluded.os,recipe=excluded.recipe,
            stops=frontier_failures.stops+1,updated_at=excluded.updated_at"""),
            (row_id, project_id, int(user_id), failure.source, str(error_code or "")[:40], failure.failed_step,
             failure.category, failure.provider, message, failure.version, failure.os, failure.recipe, now, now))


def record_project(user_id: int, project_id: str, error_code: str, failure: FailureRecord, title: str = "") -> bool:
    """Keep a stopped project's record (its latest stop). Never raises: a record must not cost the finish."""
    try:
        _save(project_id, project_id, user_id, error_code, failure, [title])
        return True
    except Exception as error:                                  # noqa: BLE001
        print(f"[studio-failures] not saved: {type(error).__name__}")
        return False


def record_report(user_id: int, failure: FailureRecord) -> bool:
    """Keep a stop that ends no project, up to REPORTS_PER_HOUR an hour per account."""
    initialize()
    with db._conn() as conn:
        recent = conn.execute(db._q("""SELECT COUNT(*) AS n FROM frontier_failures
            WHERE user_id=? AND project_id='' AND created_at>?"""), (int(user_id), time.time() - 3600)).fetchone()["n"]
    if recent >= REPORTS_PER_HOUR:
        return False
    _save("rep_" + secrets.token_hex(12), "", user_id, "", failure)
    return True


# ── counted ───────────────────────────────────────────────────────────────────────────────────────────────────────

def _counts(values) -> dict:
    return dict(Counter(v or "unknown" for v in values).most_common())


def _message_key(message: str) -> str:
    """Messages that differ only in their numbers count together."""
    return re.sub(r"\d+", "#", message or "").strip()


def _cloud_category(error: str) -> str:
    low = (error or "").lower()
    for category, words in (("cancelled", ("cancel",)), ("balance", ("balance",)),
                            ("timeout", ("time limit", "timed out", "timeout")),
                            ("blocked_network", ("unreachable", "connection", "network")),
                            ("usage_limit", ("usage limit",))):
        if any(w in low for w in words):
            return category
    return "unknown" if not low or low == "stopped" else "provider_error"


def summary(days: int = 14, now: float | None = None) -> dict:
    """Counts of stopped projects' records in the last `days` days, by step, kind, service, recipe, version, computer
    and source; the TOP_MESSAGES commonest cleaned messages; failed projects that sent no record (Studios from before
    the records); and Studio Cloud's own render failures. Counts only: no ids, emails or titles."""
    from webapp import frontier_studio
    initialize()
    frontier_studio.initialize()
    days = max(1, min(MAX_DAYS, int(days or 14)))
    now = time.time() if now is None else now
    since = now - days * 86400
    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute(db._q("""SELECT f.source,f.error_code,f.failed_step,f.category,f.provider,
            f.message,f.version,f.os,f.recipe,f.stops,p.status AS project_status FROM frontier_failures f
            LEFT JOIN frontier_projects p ON p.id=f.project_id AND f.project_id<>''
            WHERE f.updated_at>=? ORDER BY f.updated_at DESC LIMIT 50000"""), (since,)).fetchall()]
        silent = [dict(r) for r in conn.execute(db._q("""SELECT s.error_code FROM frontier_projects p
            JOIN frontier_native_sessions s ON s.project_id=p.id
            WHERE p.status='failed' AND p.updated_at>=?
            AND NOT EXISTS (SELECT 1 FROM frontier_failures f WHERE f.id=p.id)"""), (since,)).fetchall()]
    messages: dict = {}
    for row in rows:
        key = _message_key(row["message"])
        if not key:
            continue
        entry = messages.setdefault(key, {"message": row["message"], "count": 0, "steps": Counter(),
                                          "categories": Counter(), "providers": Counter()})
        entry["count"] += 1
        entry["steps"][row["failed_step"]] += 1
        entry["categories"][row["category"]] += 1
        entry["providers"][row["provider"]] += 1
    top = sorted(messages.values(), key=lambda e: (-e["count"], e["message"]))[:TOP_MESSAGES]
    not_cancelled = [r for r in rows if r["category"] != "cancelled"]
    return {"days": days, "since": since,
            "stopped_projects": len(rows), "stopped_not_cancelled": len(not_cancelled),
            "stops_in_total": sum(int(r["stops"] or 1) for r in rows),
            "resumed_and_finished_later": sum(r["project_status"] == "done" for r in rows),
            "by_step": _counts(r["failed_step"] for r in rows),
            "by_category": _counts(r["category"] for r in rows),
            "by_provider": _counts(r["provider"] for r in rows),
            "by_recipe": _counts(r["recipe"] for r in rows),
            "by_version": _counts(r["version"] for r in rows),
            "by_os": _counts(r["os"] for r in rows),
            "by_source": _counts(r["source"] for r in rows),
            "by_error_code": _counts(r["error_code"] for r in rows),
            "step_and_category": _counts(f"{r['failed_step']}/{r['category']}" for r in not_cancelled),
            "top_messages": [{"message": e["message"], "count": e["count"],
                              "step": e["steps"].most_common(1)[0][0], "category": e["categories"].most_common(1)[0][0],
                              "provider": e["providers"].most_common(1)[0][0]} for e in top],
            "failed_without_reason": {"projects": len(silent), "by_error_code": _counts(r["error_code"] for r in silent),
                                      "note": "Failed projects from Studios that send no failure record yet."},
            "cloud": _cloud(since)}


def _cloud(since: float) -> dict:
    """Studio Cloud's render failures, from its own job records (the machine's error, cleaned)."""
    try:
        from webapp import cloud_jobs
        cloud_jobs.initialize()
        with db._conn() as conn:
            rows = [dict(r) for r in conn.execute(db._q("""SELECT error,title,machine FROM cloud_jobs
                WHERE status='failed' AND created_at>=? LIMIT 20000"""), (since,)).fetchall()]
    except Exception as error:                                  # noqa: BLE001 - the rest of the report still stands
        return {"available": False, "why": type(error).__name__}
    cleaned = [clean_message(r["error"], [r["title"]]) for r in rows]
    return {"available": True, "failed": len(rows), "by_machine": _counts(r["machine"] for r in rows),
            "by_category": _counts(_cloud_category(r["error"]) for r in rows),
            "top_messages": [{"message": m, "count": n} for m, n in Counter(cleaned).most_common(10) if m]}


def make_router(require_admin) -> APIRouter:
    router = APIRouter(prefix="/api/admin/failures", tags=["admin"])

    @router.get("")
    def get_failures(days: int = 14, admin=Depends(require_admin)):
        return JSONResponse(summary(days), headers=NO_STORE)

    return router
