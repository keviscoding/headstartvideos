"""An approved-reference path through the original private style learner.

This adapter contains no creative prompts or source copies. Metadata preview is
free of model requests; study runs the pinned learner on real downloaded frames
inside a separate owned Claude Code session. Interrupted work is never rerun
automatically, and completion synchronization submits only saved receipts.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
import traceback
from types import FunctionType
from urllib.parse import parse_qs, quote, unquote, urlsplit

HOME = Path(__file__).resolve().parent
STUDY_GUIDANCE = HOME / "cr-style-study-brief.txt"
MAX_REFERENCES = 3
MAX_REFERENCE_S = 24 * 3600
PREVIEW_LIMIT = 8
PHASES = {
    "prepared": (0, "Your approved reference study is ready to start."),
    "design": (8, "Designing the style around your example title and brief."),
    "research": (18, "Using only the reference videos you approved."),
    "watch": (30, "Studying actual reference frames, pacing, graphics and sound."),
    "build": (58, "Writing the original learner's graphics kit."),
    "validate": (78, "Rendering and reviewing the learned graphics against the reference."),
    "art": (88, "Writing the picture style, cast and voice from what Studio saw."),
    "renderer": (90, "Claude is writing this channel's animation and checking its test shots against the references."),
    "publish": (94, "Saving your validated style in the studio's preset library."),
    "complete": (100, "Your learned style is saved and ready to use."),
}


class StudyStopped(BaseException):
    """Do not let the original learner turn a stopped study into a plain style."""


def _read(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, value):
    import cr_claude
    cr_claude._private_write(Path(path), value)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _folder(home):
    root = Path(home).resolve()
    folder = root / "output/cr-style-clone"
    if not folder.resolve().is_relative_to(root):
        raise RuntimeError("Keep style studies in this studio's output folder.")
    return folder


def _operation(home, operation_id):
    if not isinstance(operation_id, str) or not re.fullmatch(r"op_[a-f0-9]{24}", operation_id):
        raise RuntimeError("Choose a saved style-study operation.")
    folder = _folder(home) / operation_id
    if not folder.resolve().is_relative_to(_folder(home).resolve()):
        raise RuntimeError("Keep the study in this studio's output folder.")
    return folder


@contextmanager
def _lock(folder, name=".lock"):
    folder.mkdir(parents=True, exist_ok=True)
    try:
        folder.chmod(0o700)
    except OSError:
        pass
    descriptor = os.open(folder / name, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt
                # Windows locks byte 0 even past the end of the file. Never write it first: the descriptor has no
                # O_APPEND, so the write landed on a byte another study had locked, and the failed write came back on
                # close as a bare "Permission denied" instead of this message.
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("Another style-study request is already working. Wait for it to finish.") from None
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def normalize_url(value):
    """Accept only public YouTube video/channel URLs; no arbitrary fetch target."""
    if not isinstance(value, str) or len(value) > 1000 or any(ord(c) < 32 for c in value):
        raise ValueError("Paste a public YouTube video or channel link.")
    parsed = urlsplit(value.strip())
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port
            or parsed.hostname not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}):
        raise ValueError("Paste a public HTTPS YouTube video or channel link.")
    path = unquote(parsed.path).strip("/")
    parts = path.split("/")
    video = parts[0] if parsed.hostname == "youtu.be" else (
        (parse_qs(parsed.query).get("v") or [""])[0] if path == "watch" else
        parts[1] if len(parts) == 2 and parts[0] in {"shorts", "live", "embed"} else "")
    if video:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{11}", video):
            raise ValueError("This YouTube video link is incomplete.")
        return "https://www.youtube.com/watch?v=" + video
    if len(parts) > 1 and parts[-1] in {"videos", "featured", "shorts"}:
        parts = parts[:-1]
    if len(parts) == 1 and re.fullmatch(r"@[\w.\-]{3,80}", parts[0]):
        channel = quote(parts[0], safe="@._-")
    elif (len(parts) == 2 and parts[0] in {"channel", "c", "user"}
          and re.fullmatch(r"[a-zA-Z0-9_.-]{3,100}", parts[1])):
        channel = "/".join(parts)
    else:
        raise ValueError("Use a YouTube video link or a channel's public page.")
    return "https://www.youtube.com/" + channel + "/videos"


# Plain-language reasons for yt-dlp failures (matched on its error text).
_YT_FAILURES = (
    ("confirm you", "YouTube is asking this computer to confirm it isn't a bot. Wait a few minutes and try again, or try another network."),
    ("not a bot", "YouTube is asking this computer to confirm it isn't a bot. Wait a few minutes and try again, or try another network."),
    ("does not have a videos tab", "This channel has no long videos to learn from. Use a channel or video with videos over a minute long."),
    ("does not exist", "That YouTube channel or video doesn't exist. Check the link."),
    ("http error 404", "That YouTube page doesn't exist. Check the link."),
    ("private video", "That video is private. Use a public video or channel."),
    ("members-only", "That video is for channel members only. Use a public video."),
    ("video unavailable", "That video isn't available. Try another public video."),
    ("getaddrinfo", "Studio couldn't reach YouTube. Check your internet connection and try again."),
    ("unable to download", "YouTube didn't answer. Check your internet connection and try again."),
)
# Signs that YouTube changed and Studio's YouTube helper needs its update.
_YT_OUTDATED = ("unable to extract", "nsig", "http error 400", "http error 403", "precondition",
                "please report this issue", "no such option", "unrecognized arguments")
_YT_HELPER = {"updated": False}


def _ytdlp_command():
    """Studio's own yt-dlp (installed and updated with Studio) before an older copy on PATH.

    Only look it up, never import it here: Studio wraps subprocess.Popen for its
    render workers, and yt-dlp subclasses Popen at import time.
    """
    import importlib.util
    ipv4 = ["--force-ipv4"] if os.environ.get("YT_FORCE_IPV4", "") not in ("", "0", "no", "off", "false") else []
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"] + ipv4
    found = shutil.which("yt-dlp")
    return [found] + ipv4 if found else []


def list_videos(url, limit, timeout=90, *, run=subprocess.run):
    """Public videos of a channel or one video, as yt-dlp lists them. Says why when it can't."""
    command = _ytdlp_command()
    if not command:
        raise RuntimeError("Studio's YouTube helper is missing. Ask Claude Code to repair your Studio, then try again.")

    def attempt():
        try:
            done = run(command + ["--ignore-config", "--flat-playlist", "--no-warnings", "-j", "--playlist-end",
                                  str(limit), url], capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            raise RuntimeError("YouTube took too long to answer. Try again in a minute.") from None
        rows = []
        for line in (done.stdout or "").splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and item.get("id"):
                rows.append({"id": item["id"], "title": str(item.get("title") or ""),
                             "views": int(item.get("view_count") or 0), "duration": float(item.get("duration") or 0),
                             "channel": str(item.get("channel") or item.get("uploader") or item.get("playlist_channel") or "")})
        return rows, done.stderr or ""

    rows, error = attempt()
    if not rows and any(sign in error.lower() for sign in _YT_OUTDATED) and not _YT_HELPER["updated"]:
        # YouTube changes often; one quiet update of the helper usually fixes it.
        _YT_HELPER["updated"] = True
        upgrade = run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-U", "yt-dlp"],
                      capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240)
        if upgrade.returncode == 0:
            command = _ytdlp_command()
            rows, error = attempt()
    if rows:
        return rows
    lowered = error.lower()
    for sign, message in _YT_FAILURES:
        if sign in lowered:
            raise RuntimeError(message + " No model request was made.")
    detail = next((line.strip() for line in reversed(error.splitlines()) if line.strip()), "")
    if detail:
        raise RuntimeError("YouTube could not list this reference (" + detail.removeprefix("ERROR: ")[:180]
                           + "). Try another public link; no model request was made.")
    raise RuntimeError("YouTube didn't list any videos for this link. Try another public link; no model request was made.")


def preview(home, url, *, learner=None):
    """Bounded original yt-dlp metadata, without Claude or a video-analysis API."""
    canonical = normalize_url(url)
    direct_id = (parse_qs(urlsplit(canonical).query).get("v") or [None])[0]
    limit = 1 if direct_id else PREVIEW_LIMIT
    candidates = []
    rows = learner._flat(canonical, limit, 90) if learner is not None else list_videos(canonical, limit)
    for row in rows:
        identifier = row.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{11}", identifier):
            continue
        if direct_id and identifier != direct_id or any(r["id"] == identifier for r in candidates):
            continue
        duration = max(0.0, float(row.get("duration") or 0))
        candidates.append({"id": identifier, "url": "https://www.youtube.com/watch?v=" + identifier,
            "title": str(row.get("title") or "Untitled reference")[:300],
            "channel": str(row.get("channel") or "")[:160],
            "views": max(0, int(row.get("views") or 0)), "duration_seconds": duration,
            # a look samples a few minutes of each reference, so marathon recaps (manhwa channels post 5 to 12
            # hour videos) can be studied too
            "eligible": 60 <= duration <= MAX_REFERENCE_S,
            "eligibility_note": "" if 60 <= duration <= MAX_REFERENCE_S else "Choose a reference between one minute and 24 hours with a known duration."})
        if len(candidates) == PREVIEW_LIMIT:
            break
    if not candidates:
        raise RuntimeError("YouTube could not list this reference. Try another public link; no model request was made.")
    result = {"reference_url": canonical, "candidates": candidates, "no_model_requests": True}
    identifier = "pv_" + _hash(result)
    _write(_folder(home) / ".previews" / (identifier + ".json"), {**result, "created_at": time.time()})
    return {**result, "preview_id": identifier}


def _owner(bridge):
    cfg = bridge.load_settings()
    if not isinstance(cfg.get("key"), str) or not cfg["key"].startswith("cr_mcp_"):
        raise RuntimeError("Connect your ChannelRecipe account before starting this study.")
    return _hash({"account": cfg["key"], "url": cfg.get("url", "https://channelrecipe.com")})


def _proof(bridge, proof):
    cfg = bridge.load_settings()
    required = ("claude_installed", "claude_authenticated", "ffmpeg_ready", "browser_ready",
                "node_ready", "models_verified", "costs_accepted")
    if (cfg.get("brain_mode") != "claudecode" or cfg.get("costs_accepted") is not True
            or not isinstance(proof, dict) or proof.get("brain_mode") != "claudecode"
            or proof.get("purpose") != "creative" or proof.get("api_provider") != "firstParty"
            or any(proof.get(key) is not True for key in required)
            or any(proof.get(key) != cfg.get(key) for key in ("general_model", "visual_model"))):
        raise RuntimeError("Finish native Claude Code setup, model verification and cost acknowledgement before studying a style.")
    return dict(proof)


def _spec(home, supplied):
    if not isinstance(supplied, dict) or supplied.get("acknowledge_costs") is not True:
        raise ValueError("Approve this bounded study and your own Claude account usage before starting.")
    preview_id = supplied.get("preview_id")
    if not isinstance(preview_id, str) or not re.fullmatch(r"pv_[a-f0-9]{64}", preview_id):
        raise ValueError("Preview your YouTube references before starting the study.")
    saved = _read(_folder(home) / ".previews" / (preview_id + ".json"))
    if not saved or not 0 <= time.time() - saved.get("created_at", 0) <= 86400:
        raise ValueError("Preview the references again before approving this study.")
    selected = supplied.get("reference_ids")
    if (not isinstance(selected, list) or not 1 <= len(selected) <= MAX_REFERENCES
            or any(not isinstance(x, str) for x in selected) or len(set(selected)) != len(selected)):
        raise ValueError("Approve one to three distinct reference videos.")
    rows = {row["id"]: row for row in saved["candidates"]}
    if any(identifier not in rows or not rows[identifier].get("eligible") for identifier in selected):
        raise ValueError("Choose eligible videos from your saved preview; the learner will study only those references.")
    base = supplied.get("base_style", "documentary")
    if not isinstance(base, str) or not re.fullmatch(r"[a-z0-9_-]{1,80}", base):
        raise ValueError("Choose an existing original base preset.")
    base_path = Path(home) / "styles" / (base + ".json")
    if not base_path.is_file() or not base_path.resolve().is_relative_to(Path(home).resolve()):
        raise ValueError("The chosen base preset is not installed.")
    label, title = supplied.get("label"), supplied.get("title")
    if (not isinstance(label, str) or not 1 <= len(label.strip()) <= 80 or any(c in label for c in "\r\n")
            or not isinstance(title, str) or not 1 <= len(title.strip()) <= 280 or any(c in title for c in "\r\n")):
        raise ValueError("Give the learned style a name and an example video title.")
    requested_name = supplied.get("name", "")
    # up to 24: the style's id names its videos' folder (output/cr-clone-<name>/), and Windows stops at 260 characters
    if requested_name and (not isinstance(requested_name, str) or not re.fullmatch(r"[a-z0-9_-]{1,24}", requested_name)):
        raise ValueError("Use a short style ID (up to 24 lowercase letters, numbers and hyphens).")
    brief = supplied.get("brief", "")
    if not isinstance(brief, str) or len(brief) > 4000:
        raise ValueError("Keep the style brief under 4,000 characters.")
    visual_mode = supplied.get("visual_mode", "reference_only")
    if not isinstance(visual_mode, str) or visual_mode not in {"reference_only", "mixed"}:
        raise ValueError("Choose reference animation only or a mixed-media style.")
    look_id, answers = supplied.get("look_id"), supplied.get("answers") or {}
    if look_id is not None:
        import cr_style_look
        looked = cr_style_look.status(home, look_id)
        if looked.get("state") != "done" or sorted(r["id"] for r in looked["references"]) != sorted(selected):
            raise ValueError("Look at these references again before starting the study.")
        questions = {q["id"]: q.get("options") or [] for q in (looked.get("mapping") or {}).get("questions") or []}
        if (not isinstance(answers, dict) or len(answers) > 12
                or any(k not in questions or v not in questions[k] for k, v in answers.items())):
            raise ValueError("Answer the questions from what Studio saw.")
        # what the videos will be is said before anything is paid: a medium Studio can't make yet (3D characters
        # drawn as pictures) is accepted in so many words, never swapped in silently
        import cr_style_map
        planned = cr_style_map.plan_style(looked.get("profile") or {}, _installed_styles(home), base=base,
                                          answers=answers)
        gap = (planned.get("route") or {}).get("medium_gap") if visual_mode != "reference_only" else ""
        if gap and supplied.get("accept_medium") is not True:
            raise ValueError(gap + " Tick that you understand before starting, or wait for the update.")
        engine = (planned.get("route") or {}).get("engine")
        # "2d" / "3d": the study has the creator's Claude Code write the channel's renderer on that kit
        renderer = {"animated2d": "2d", "animated3d": "3d"}.get(engine, False) if visual_mode != "reference_only" else False
    elif answers:
        raise ValueError("Look at the references before answering questions about them.")
    else:
        renderer = False
    attempt_id = supplied.get("attempt_id", "")
    if attempt_id and (not isinstance(attempt_id, str) or not re.fullmatch(r"[a-f0-9]{32}", attempt_id)):
        raise ValueError("Start a new style study from its approval form.")
    budget, calls = supplied.get("budget_minutes", 22), supplied.get("max_calls", 40)
    minutes = supplied.get("minutes", 10)
    if (not isinstance(budget, int) or isinstance(budget, bool) or not 15 <= budget <= 30
            or not isinstance(calls, int) or isinstance(calls, bool) or not 1 <= calls <= 80):
        raise ValueError("Choose a 15–30 minute study budget and a ceiling of 1–80 Claude stages.")
    if not isinstance(minutes, int) or isinstance(minutes, bool) or not 1 <= minutes <= 60:
        raise ValueError("Choose an example film length between one and sixty minutes.")
    references = [{**rows[x], "duration": rows[x]["duration_seconds"], "named": True, "ratio": 0,
                   "median": 0, "rank": 0} for x in selected]
    return {"preview_id": preview_id, "reference_url": saved["reference_url"], "references": references,
        "base_style": base, "base_sha256": _file_hash(base_path), "name": requested_name,
        "label": label.strip(), "title": title.strip(), "brief": brief,
        "budget_minutes": budget, "max_calls": calls, "minutes": minutes,
        "visual_mode": visual_mode, "attempt_id": attempt_id, "accept_medium": supplied.get("accept_medium") is True,
        **({"renderer": renderer} if renderer else {}),
        **({"look_id": look_id, "answers": dict(answers)} if look_id else {})}


# The reference learner checks a new graphics kit in a real browser only when the Mac has room: a memory level of 20+
# and 3 GB free on the startup disk (reflearn.gate). Without room it falls back to a simulated browser and the study
# stops unsaved after its Claude work, so the room is checked before any of it, with a margin for the downloads.
CHECK_DISK_GB = 3.0
CHECK_MARGIN_GB = 0.5
CHECK_MEMORY_LEVEL = 20


def room_to_check(platform=None, free_gb=None, memory_level=None) -> str:
    """"" when this computer can check graphics in a real browser now, else what to do about it, in plain words."""
    if (platform or sys.platform) != "darwin":
        return ""
    need = float(os.environ.get("REFLEARN_MIN_DISK_GB") or CHECK_DISK_GB) + CHECK_MARGIN_GB
    free = shutil.disk_usage("/").free / 1e9 if free_gb is None else free_gb
    if free < need:
        return (f"Your Mac has {free:.1f} GB of free disk space, and a style study needs at least {need:g} GB to check "
                "your new graphics in a real browser. Free up some space (empty the Bin, delete old downloads or "
                "videos you no longer need), then start the study again. Nothing has been used yet.")
    if memory_level is None:
        try:
            memory_level = int(subprocess.run(["sysctl", "-n", "kern.memorystatus_level"], capture_output=True,
                                              text=True, encoding="utf-8", errors="replace",
                                              timeout=10).stdout.strip() or 100)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            memory_level = 100
    if memory_level < float(os.environ.get("REFLEARN_MIN_MEM_LEVEL") or CHECK_MEMORY_LEVEL):
        return ("Your Mac is short of memory right now, and a style study needs some free to check your new graphics "
                "in a real browser. Close other apps (browsers with many tabs, editors, games), then start the study "
                "again. Nothing has been used yet.")
    return ""


_room = room_to_check                       # the check prepare() runs (tests stand in for this computer's disk)


def _graphics_kit(folder, record, *, renders: bool) -> dict:
    """The learner's graphics kit as the study keeps it: one checked in a real browser, or, where the channel's own
    renderer draws the pictures (renders), also one checked only in a stand-in browser because the real one was busy or
    the Mac short of room (a Lucas the Spider study stopped there after 8 minutes of Claude work while another render
    held the browser). Anything else stops the study; no plain preset is ever swapped in."""
    if record and record.get("kit") and record.get("browser") is True:
        return record
    if renders and not (record and record.get("kit")):
        # the owner (2026-10-26): the renderer never depends on the motion-graphics learner; the style is saved without
        # a graphics kit and its renderer draws everything
        return {**(record or {}), "kit": None, "no_kit": "the graphics learner wrote no kit"}
    why = _no_browser_why(folder) if record and record.get("kit") else ""
    if why and renders:
        return {**record, "browser_skipped": why}
    if why:
        raise StudyStopped(f"Your new graphics couldn't be checked in a real browser: {why}. Your references "
                           "are saved; no plain preset was substituted.")
    raise StudyStopped("The original learner did not produce a graphics kit that passed real browser validation. Your study is saved; no plain preset was substituted.")


def _no_browser_why(folder) -> str:
    """Why the graphics check had no real browser, from the study's own log, in plain words; "" when unknown."""
    try:
        text = (Path(folder) / "study.log").read_text(encoding="utf-8")[-20000:]
    except OSError:
        return ""
    gate = re.findall(r"DOM mock \(Mac gate: memory level (\d+) \(needs ([\d.]+)\), (\d+) GB free \(needs ([\d.]+)\)\)",
                      text)
    if gate:
        level, need_level, free, need_free = gate[-1]
        if float(free) < float(need_free) or int(level) >= float(need_level):
            return (f"your Mac had less than the {float(need_free):g} GB of free disk space the check needs. Free up "
                    "some space, then start the study again")
        return "your Mac was short of memory at that moment. Close other apps, then start the study again"
    if re.search(r"DOM mock \(the render lock is held", text):
        return "another video was using the graphics browser at that moment. Start the study again when it has finished"
    return ""


def prepare(home, supplied, proof, bridge):
    """Save an immutable, account-owned operation; no model work starts here."""
    room = _room()
    if room:
        raise RuntimeError(room)
    spec, proof = _spec(home, supplied), _proof(bridge, proof)
    owner = _owner(bridge)
    identity = _hash({"spec": spec, "owner": owner,
                      "general_model": proof["general_model"], "visual_model": proof["visual_model"]})
    with _lock(_folder(home), ".prepare.lock"):
        index = _folder(home) / ".requests" / (identity + ".json")
        existing = _read(index)
        if existing.get("operation_id"):
            return status(home, existing["operation_id"], bridge)
        bridge.prepare_runtime(proof)
        operation_id = "op_" + secrets.token_hex(12)
        folder = _operation(home, operation_id)
        spec["style_id"] = "cr_clone_" + (spec["name"] or operation_id[3:15])
        style = Path(home) / "styles" / (spec["style_id"] + ".json")
        if style.exists():
            raise ValueError("That learned style ID already exists. Choose a different name so its saved configuration is preserved.")
        saved = {"operation_id": operation_id, "owner": owner, "spec": spec, "proof": proof,
                 "identity": identity, "state": "prepared", "phase": "prepared",
                 "begin_id": "b_" + secrets.token_hex(16), "created_at": time.time()}
        _write(folder / "operation.json", saved)
        _write(index, {"operation_id": operation_id})
    return status(home, operation_id, bridge)


def _saved(home, operation_id, bridge):
    folder = _operation(home, operation_id)
    saved = _read(folder / "operation.json")
    if not saved or saved.get("operation_id") != operation_id or saved.get("owner") != _owner(bridge):
        raise RuntimeError("Reconnect the CR account that owns this style study.")
    return folder, saved


def _native_failure(home, saved):
    """Read only normalized local stage evidence, never expose provider text."""
    project_id = saved.get("project_id", "")
    if not isinstance(project_id, str) or not re.fullmatch(r"crv_[a-f0-9]{24}", project_id):
        return ""
    folder = Path(home) / "output/cr-claude-cache" / project_id
    for path in sorted(folder.glob("cc_*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        stage = _read(path)
        if stage.get("state") in {"failed", "failed_local"}:
            code = stage.get("error_code")
            return code if code in {"timeout", "usage_limit", "cancelled", "authentication_required"} else ""
    return ""


def status(home, operation_id, bridge):
    folder, saved = _saved(home, operation_id, bridge)
    state, phase = saved.get("state"), saved.get("phase", "prepared")
    percent, message = PHASES.get(phase, PHASES["prepared"])
    failure_code = saved.get("failure_code", "")
    native_failure = _native_failure(home, saved) if state == "failed" else ""
    said, stop_type = "", ""
    if state == "failed":
        failure = _read(folder / "failure.json")
        said, stop_type = str(failure.get("message") or "").strip(), str(failure.get("exception_type") or "")
        message = "This study stopped. Review its saved Claude stages in setup before approving any further work. It will not run again automatically."
        if said:
            message = said + " Your study is saved; start it again here when you're ready."
        if native_failure == "timeout":
            message = "Claude timed out while " + PHASES.get(phase, PHASES["prepared"])[1].rstrip(".").lower() + ". Your saved study is intact. Review your references and approve a new study here; setup review is not required to start a separate attempt."
        elif native_failure == "usage_limit":
            message = "Claude reported a usage limit during this study. Your saved work is intact. Once your Claude account has available usage, review and approve a new study here."
        if failure_code == "metadata_handoff_failed":
            message = "Reference study and graphics validation finished, but saving the style stopped. Save the completed study without repeating Claude, downloads or rendering."
    elif state == "complete_local":
        message = "Your validated style is saved locally. Reconnect CR to record completion; no generation needs to repeat."
    elif state == "running":
        message = PHASES.get(phase, PHASES["prepared"])[1]
    result = {"operation_id": operation_id, "state": {"complete": "done", "complete_local": "pending"}.get(state, state),
        "phase": phase, "percent": percent, "message": message,
        "elapsed_seconds": max(0, round(saved.get("completed_at", time.time()) - saved.get("started_at", time.time()), 1)),
        "operation_project_id": saved.get("project_id"), "reconciliation_pending": bool(saved.get("finish_receipt")),
        "needs_manual_recovery": state == "failed", "reference_url": saved["spec"]["reference_url"],
        "reference_ids": [r["id"] for r in saved["spec"]["references"]],
        "budget_minutes": saved["spec"]["budget_minutes"], "max_calls": saved["spec"]["max_calls"],
        "style_ready": state in {"complete", "complete_local"} and _published(home, saved)}
    result["metadata_recovery_available"] = state == "failed" and (
        _recovery_checkpoint_ready(folder, saved) or _legacy_recovery_possible(home, folder, saved))
    result["failure_code"] = native_failure or failure_code
    # setup can help only with Claude itself (sign-in, model access); a stop the study explains is retried here
    result["setup_review_required"] = (state == "failed" and native_failure not in {"timeout", "usage_limit"}
                                       and failure_code != "metadata_handoff_failed"
                                       and not (said and stop_type == "StudyStopped"))
    result["stop_reason"] = said
    if state == "failed" and not result["metadata_recovery_available"]:
        result["retry_spec"] = {key: copy.deepcopy(saved["spec"][key]) for key in (
            "preview_id", "reference_url", "references", "base_style", "name", "label", "title", "brief",
            "budget_minutes", "max_calls", "minutes") if key in saved["spec"]}
        result["retry_spec"]["visual_mode"] = saved["spec"].get("visual_mode", "reference_only")
        # the look Studio saw and the creator's answers: a new study made without them would be the plain recipe
        for key in ("look_id", "answers"):
            if saved["spec"].get(key):
                result["retry_spec"][key] = copy.deepcopy(saved["spec"][key])
    result["metadata_only"] = saved.get("metadata_only") is True
    result["recovery_of"] = saved.get("recovery_of")
    result["refinement_only"] = saved.get("refinement_only") is True
    result["refinement_of"] = saved.get("refinement_of")
    if result["style_ready"]:
        result.update(style=saved["spec"]["style_id"], label=saved["spec"]["label"],
                      board_available=(folder / "reflearn/board.jpg").is_file())
        learned = _read(folder / "study-result.json")
        result["reference_used"] = learned.get("reference_used", "")
        result["captions_available"] = _read(folder / "reflearn/reference-evidence.json").get("captions_available") is True
        style = _read(Path(home) / "styles" / (saved["spec"]["style_id"] + ".json"))
        likeness = (style.get("cr_style_clone") or {}).get("likeness") or {}
        import cr_style_likeness
        check = _read(folder / cr_style_likeness.CHECK)
        if not likeness and check.get("state") == "running" and \
                time.time() - float(check.get("started_at") or 0) < cr_style_likeness.LIMIT_S + 300:
            result["likeness_running"] = True
            result["message"] = "Your style is saved. Now drawing test pictures in it and comparing them with your references."
        if likeness:
            result["likeness"] = {k: likeness.get(k) for k in ("state", "verdict", "reliable", "scores", "dealbreakers",
                                                                "pictures", "why", "people", "cost_usd")}
            route = ((style.get("cr_style_clone") or {}).get("learned_v2") or {}).get("route") or {}
            result["route"] = {k: route.get(k) for k in ("engine", "why", "medium_gap")}
            if route.get("engine") == "drawn" and not route.get("medium_gap"):     # a style saved before Studio 75
                import cr_style_map
                result["route"]["medium_gap"] = cr_style_map.MEDIUM_GAP
        renderer = renderer_verdict(style)
        if renderer:
            result["renderer"] = renderer
            if renderer.get("say"):
                result["message"] = renderer["say"]
    return result


def renderer_verdict(style) -> dict:
    """What the creator is told about an animated style's renderer, plainly: how it scored against the channel's own
    bar, and the pictures route offered when it fell short or never worked. {} for any other style."""
    clone = style.get("cr_style_clone") or {}
    if clone.get("pictures_instead"):
        return {"state": "pictures", "say": "This style draws pictures in the channel's look (you chose pictures "
                "instead of Claude's animation)."}
    look = style.get("look") or {}
    if look.get("visuals") not in ("cr_animated2d", "cr_animated3d"):
        return {}
    r = (look.get("animated2d") or {}).get("renderer")
    if not isinstance(r, dict):
        return {}
    out = {k: r.get(k) for k in ("state", "overall", "bar", "control", "version", "why") if r.get(k) is not None}
    if r.get("state") == "passed":
        out["say"] = (f"Your style is saved. Claude wrote this channel's animation, and its test shots scored "
                      f"{r.get('overall')}/10 against the channel's own frames (the bar for this channel: {r.get('bar')}). "
                      "Every video is animated with it.")
    elif r.get("state") == "below_bar":
        out["say"] = (f"Your style is saved, but Claude's animation for this channel reached {r.get('overall')}/10 "
                      f"against its own frames, under this channel's bar of {r.get('bar')}. Your videos will be animated "
                      "with it as it is. If you'd rather have pictures in the channel's look, choose Use pictures "
                      "instead.")
        out["offer_pictures"] = True
    else:
        out["say"] = ("Your style is saved, but Claude couldn't write a working animation for this channel ("
                      + str(r.get("why") or "no draft drew its test shots") + "). Videos in it won't start until you "
                      "choose Use pictures instead, or study the channel again.")
        out["offer_pictures"] = True
    return out


def use_pictures(home, operation_id, bridge):
    """The creator's choice after an animation fell short: the saved style draws pictures in the channel's look
    instead (the plan for the "pictures" answer), said in the style, never done without being asked."""
    folder, saved = _saved(home, operation_id, bridge)
    if saved.get("state") not in ("complete", "complete_local", "validated") or not _published(home, saved):
        raise RuntimeError("Wait for the style to be saved first.")
    import cr_style_look
    import cr_style_map
    spec = saved["spec"]
    target = Path(home) / "styles" / (spec["style_id"] + ".json")
    style = _read(target)
    look = style.get("look") or {}
    if look.get("visuals") not in ("cr_animated2d", "cr_animated3d"):
        return status(home, operation_id, bridge)
    looked = _read(cr_style_look.folder(home, spec.get("look_id") or "") / "look.json") if spec.get("look_id") else {}
    # the "pictures" answer, for a 2D channel's animation and a 3D channel's medium alike
    answers = {**(spec.get("answers") or {}), "made": cr_style_map.MADE_OTHER, "animation": cr_style_map.ANIMATED_PICTURES,
               **({"medium": cr_style_map.MEDIUM_PICTURES} if look.get("visuals") == "cr_animated3d" else {})}
    mapping = cr_style_map.plan_style(looked.get("profile") or {}, _installed_styles(home), base=spec["base_style"],
                                      answers=answers)
    renderer = (look.get("animated2d") or {}).get("renderer") or {}
    for key in ("visuals", "animated2d", "aspect"):
        look.pop(key, None)
    style.pop("cost", None)
    style = cr_style_map.apply(style, mapping)
    clone = style.setdefault("cr_style_clone", {})
    clone["pictures_instead"] = {"at": time.time(), "renderer": {k: renderer.get(k) for k in ("state", "overall", "bar")}}
    if isinstance(clone.get("learned_v2"), dict):
        clone["learned_v2"]["route"] = mapping.get("route") or {}
    republish(folder, saved, style)
    return status(home, operation_id, bridge)


def republish(folder, saved, style):
    """A saved style changed by the creator's own action (pictures instead, a renderer saved from Style Lab), written
    with its study's receipt, so the study still knows the style as its own."""
    target = Path(_studio_home(folder)) / "styles" / (saved["spec"]["style_id"] + ".json")
    _write(target, style)
    _write(Path(folder) / "ready-style.json", style)
    saved["style_sha256"] = _file_hash(target)
    if saved.get("finish_receipt"):
        saved["finish_receipt"]["artifacts"][0]["sha256"] = saved["style_sha256"]
    _write(Path(folder) / "operation.json", saved)


def _published(home, saved):
    path = Path(home) / "styles" / (saved["spec"]["style_id"] + ".json")
    return path.is_file() and saved.get("style_sha256") == _file_hash(path)


def _metadata_checkpoint_ready(folder, saved):
    design, validated = _read(folder / "design-checkpoint.json"), _read(folder / "validated-checkpoint.json")
    expected = _hash(saved["spec"])
    payload = design.get("design")
    if (design.get("schema") != 1 or validated.get("schema") != 1
            or design.get("spec_sha256") != expected or validated.get("spec_sha256") != expected
            or not isinstance(payload, dict) or not isinstance(payload.get("genome"), dict)):
        return False
    try:
        return _validated_assets(folder, validated.get("record") or {}) == validated.get("assets")
    except (OSError, RuntimeError):
        return False


def _recovery_checkpoint_ready(folder, saved):
    if saved.get("refinement_only"):
        import cr_style_refine
        if not cr_style_refine.validation_completed(folder, saved):
            return False
    return _metadata_checkpoint_ready(folder, saved)


def _record_failure(folder, saved, error):
    """Persist useful private frames, without provider text or secrets."""
    saved["failure_code"] = ("metadata_handoff_failed" if _recovery_checkpoint_ready(folder, saved)
                             else "study_failed")
    # a stop Studio itself explains (its own wording, never provider text) is kept, so the page can say why
    said = str(error)[:400] if type(error).__name__ in ("StudyStopped", "ClaudeStageStopped") else ""
    _write(folder / "failure.json", {"schema": 1, "failure_code": saved["failure_code"],
        "exception_type": type(error).__name__, "message": said, "phase": saved.get("phase"), "at": time.time(),
        "frames": [{"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
                   for frame in traceback.extract_tb(error.__traceback__)]})


def _legacy_recovery_possible(home, folder, saved):
    """A display hint only. Recovery independently verifies all owned receipts."""
    if saved.get("phase") != "validate":
        return False
    evidence = _read(folder / "reflearn/reference-evidence.json")
    if evidence.get("actual_frames") is not True or evidence.get("id") not in {
            reference["id"] for reference in saved["spec"]["references"]}:
        return False
    try:
        assets = _validated_assets(folder, {"kit": _read(folder / "genkit/kit.json").get("name"), "browser": True})
        for path in (Path(home) / "learned").glob("*/meta.json"):
            if not path.resolve().is_relative_to(Path(home).resolve()):
                continue
            meta = _read(path)
            valid = meta.get("valid") or {}
            if (meta.get("video") == evidence["id"] and meta.get("first_title") == saved["spec"]["title"]
                    and valid.get("browser") is True and len(valid.get("kept") or []) >= 3
                    and all((path.parent / "kit" / Path(name).name).is_file()
                            and _file_hash(path.parent / "kit" / Path(name).name) == digest
                            for name, digest in assets.items() if name.startswith("genkit/"))):
                return True
    except (OSError, RuntimeError):
        pass
    return False


def metadata_recovery_available(home, operation_id, bridge):
    """Owned, local-only readiness hint for the studio's recovery control."""
    folder, saved = _saved(home, operation_id, bridge)
    return saved.get("state") == "failed" and (
        _recovery_checkpoint_ready(folder, saved) or _legacy_recovery_possible(home, folder, saved))


def _sync(folder, saved, bridge):
    import cr_claude
    cr_claude.sync_receipts(bridge)
    if saved.get("finish_receipt"):
        bridge.project_progress(saved["project_id"])
        bridge.finish_project(saved["finish_receipt"])
        saved.pop("finish_receipt")
        if saved["state"] == "complete_local":
            saved["state"] = "complete"
        _write(folder / "operation.json", saved)


def sync(home, operation_id, bridge):
    """Reconcile only saved metadata. Never call a model, downloader or renderer."""
    folder, saved = _saved(home, operation_id, bridge)
    with _lock(folder):
        if saved.get("state") == "validated":
            _publish(home, folder, saved)
        _sync(folder, saved, bridge)
    return status(home, operation_id, bridge)


def _bind(function, **overrides):
    bound = FunctionType(function.__code__, {**function.__globals__, **overrides},
                         function.__name__, function.__defaults__, function.__closure__)
    bound.__kwdefaults__ = function.__kwdefaults__
    return bound


def study_original(engine, folder, spec, progress, *, learner=None, designer=None, transcript=None):
    """Call original creative functions; pin reference selection, not the look."""
    if learner is None:
        import reflearn as learner
    if designer is None:
        import autostyle as designer
    if transcript is None:
        import youtube
        transcript = youtube.transcript
    folder = Path(folder)
    def log(value):
        path = folder / "study.log"
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(str(value) + "\n")
    clock = learner.Clock(spec["budget_minutes"], log)
    original_step = clock.step
    def step(name):
        original_step(name)
        progress(name)
    clock.step = step
    def allowed(*args):
        return {"pick": spec["references"][0], "backup": spec["references"][1] if len(spec["references"]) > 1 else None,
                "rows": list(spec["references"]), "queries": [], "why": "The creator approved these exact references."}
    def stop_remote(*args, **kwargs):
        log("Reference download did not provide actual frames. No video-analysis API or alternate brain was used.")
        return {}
    looked = _look_material(folder, spec)
    def local_watch(row, clock, location):
        # A study after a look reuses the sections the look downloaded (the same windows Gemini watched).
        if looked and row["id"] in looked["media"]:
            spans = [(a, b) for a, b, _ in looked["media"][row["id"]]]
            cached = {(a, b): path for a, b, path in looked["media"][row["id"]]}
            def download(vid, a, b, dest, timeout=300):
                source = cached.get((a, b))
                if vid == row["id"] and source and Path(source).is_file():
                    shutil.copyfile(source, dest)
                    return Path(dest)
                return learner._download(vid, a, b, dest, timeout=timeout)
            bound = _bind(learner.watch, watch_remote=stop_remote, _sections=lambda dur: list(spans),
                          _download=download)
        else:
            bound = _bind(learner.watch, watch_remote=stop_remote)
        return bound(row, clock, location)
    def watch(reference, clock, work):
        strict = spec.get("visual_mode") == "reference_only"
        references = spec["references"] if strict else [reference]
        observed = []
        for row in references:
            location = Path(work) / "references" / row["id"] if strict else Path(work)
            location.mkdir(parents=True, exist_ok=True)
            seen = local_watch(row, clock, location)
            sheets = [str(p) for p in (seen.get("sheets") or []) if Path(p).is_file()]
            if not sheets:
                if strict:
                    raise StudyStopped("An approved reference could not be inspected. No other reference or default look was substituted.")
                return {}
            rows = transcript(row["id"], location, original=True)
            excerpt = " ".join(str(item[1]) for item in rows if isinstance(item, (list, tuple))
                               and len(item) == 2 and isinstance(item[0], (int, float)) and 0 <= item[0] < 180)[:1800]
            observed.append({"id": row["id"], "seen": seen, "sheets": sheets,
                             "captions_available": bool(rows), "opening_excerpt": excerpt})
        first = observed[0]
        seen = copy.deepcopy(first["seen"])
        if len(observed) > 1:
            # The original vision learner accepts three sheets. Give each
            # approved video its own unscaled opening sheet, rather than let
            # the first reference occupy all three available image slots.
            seen["sheets"] = [row["sheets"][0] for row in observed]
            seen["frames"] = [frame for row in observed for frame in row["seen"].get("frames", [])]
        # YouTube's own captions are a free original source helper. Keep the
        # evidence private and report absence honestly; no speech API fallback.
        evidence = [{"id": row["id"], "actual_frames": True,
                     "captions_available": row["captions_available"],
                     "opening_excerpt": row["opening_excerpt"],
                     "measures": row["seen"].get("measures") or {}} for row in observed]
        _write(Path(work) / "reference-evidence.json", {"id": first["id"], "actual_frames": True,
            "captions_available": first["captions_available"], "opening_caption_excerpt": first["opening_excerpt"],
            "references_studied": evidence})
        seen["measures"] = {"reference_captions_are_source_data": {
            "available": first["captions_available"], "opening_excerpt": first["opening_excerpt"]},
            **(seen.get("measures") or {}), "approved_reference_evidence": evidence}
        return seen
    # Validation's original _media helper can use a deterministic stand-in.
    # This operation approves Claude study, not extra portrait API generation.
    original_media = learner._media
    def validation_media(engine, analysis, job, work):
        return original_media(engine, analysis, job, work, fetch=False)
    learned = _bind(learner._learn, research=allowed, match=lambda *args, **kwargs: [],
                    watch=watch, _media=validation_media, _fallback=lambda *args, **kwargs: None)
    brief = spec["brief"]
    if spec.get("visual_mode") == "reference_only":
        if not STUDY_GUIDANCE.is_file():
            raise StudyStopped("The full reference animation workflow is missing. Update the studio before starting a new study.")
        brief += "\n\n" + STUDY_GUIDANCE.read_text(encoding="utf-8")
    engine.set_extra(brief)
    progress("design")
    language = (engine.STYLE_INFO.get(spec["base_style"]) or {}).get("language", "English")
    design = designer.design(engine, spec["title"], language, spec["minutes"], channel=spec["base_style"], log=log)
    # A completed creative design must survive a later library/metadata error.
    # Neither checkpoint is a licence to repeat an interrupted model request.
    _write(folder / "design-checkpoint.json", {"schema": 1, "design": design,
        "spec_sha256": _hash(spec)})
    genome = design["genome"]
    work = folder / learner.WORK
    work.mkdir(parents=True, exist_ok=True)
    record = learned(engine, spec["title"], folder, genome, genome.get("analysis") or {},
                     clock, work, spec["base_style"], spec["minutes"])
    record = _graphics_kit(folder, record, renders=bool(spec.get("renderer")) and bool(looked))
    if record.get("browser_skipped") or record.get("no_kit"):
        log(f"  graphics: {record.get('no_kit') or 'checked in a stand-in browser (' + record['browser_skipped'] + ')'}; "
            "this style's own renderer draws its pictures, so the study carries on")
    score = (record.get("critique") or {}).get("score")
    if spec.get("visual_mode") == "reference_only" and (isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score)):
        raise StudyStopped("The reference animation kit has not completed its visual comparison. It is saved, but it is not ready to use; no unreviewed preset was substituted.")
    _write(folder / "validated-checkpoint.json", {"schema": 1, "record": record,
        "spec_sha256": _hash(spec), "assets": _validated_assets(folder, record)})
    if looked:
        progress("art")
        _art_director(folder, spec, looked, lambda prompt, images: learner._see(engine, clock, "art", prompt, images),
                      log)
        if spec.get("renderer"):
            progress("renderer")
            _renderer(folder, spec, looked, lambda prompt, images: learner._see(engine, clock, "renderer", prompt,
                                                                                 images, max_tokens=32000), log)
    return finish_original(engine, folder, spec, design, record, progress,
        learner=learner, designer=designer, log=log, report=clock.report())


def _portable(value, home):
    """The style with every path inside this Studio written relative to it (its kit's manifest, the study board), so
    the style keeps working when the Studio folder moves; the engine reads them from the Studio folder."""
    # normcase: Windows paths use backslashes and any case ("C:\Users\..."), so a plain "/" prefix never matched there
    # and a Windows creator's learned styles kept absolute paths
    root = os.path.normcase(str(Path(home).resolve())) + os.sep
    if isinstance(value, dict):
        return {k: _portable(v, home) for k, v in value.items()}
    if isinstance(value, list):
        return [_portable(v, home) for v in value]
    if isinstance(value, str) and os.path.normcase(value).startswith(root):
        return Path(value[len(root):]).as_posix()
    return value


def _studio_home(folder):
    """The studio folder an operation lives in (<home>/output/cr-style-clone/op_...)."""
    return Path(folder).resolve().parents[2]


def _look_material(folder, spec):
    """What the look before this study saw: cached sections, frames, captions and the profile. {} without a look."""
    if not spec.get("look_id"):
        return {}
    import cr_style_look
    home = _studio_home(folder)
    look_folder = cr_style_look.folder(home, spec["look_id"])
    looked = _read(look_folder / "look.json")
    if looked.get("state") != "done":
        raise StudyStopped("The look at these references is missing. Look at them again before studying.")
    media = {}
    for path in sorted((look_folder / "work" / "media").glob("*_*_*.mp4")):
        match = re.fullmatch(r"([A-Za-z0-9_-]{11})_(\d+)_(\d+)", path.stem)
        if match:
            media.setdefault(match.group(1), []).append((int(match.group(2)), int(match.group(3)), str(path)))
    sheets = [str(look_folder / "sheets" / (row["id"] + ".jpg")) for row in looked.get("sheets", [])
              if (look_folder / "sheets" / (row["id"] + ".jpg")).is_file()]
    return {"profile": looked.get("profile") or {}, "media": media, "sheets": sheets,
            "excerpts": looked.get("excerpts") or {},
            "channels": sorted({r.get("channel", "") for r in looked.get("references", []) if r.get("channel")})}


def _installed_styles(home):
    return {path.stem: _read(path) for path in sorted((Path(home) / "styles").glob("*.json"))
            if not path.stem.startswith(("_", "cr_clone_"))}


def _art_director(folder, spec, looked, see, log):
    """One Claude vision call, saved so a recovery never repeats it."""
    import cr_style_art
    import cr_style_map
    checkpoint = folder / "art-checkpoint.json"
    saved = _read(checkpoint)
    if saved.get("spec_sha256") == _hash(spec):
        return saved.get("art") or {}
    home = _studio_home(folder)
    profile = cr_style_map.answer(looked["profile"], spec.get("answers") or {})
    # planned from the measured profile with the answers, as the learned style is: a removed mascot never changes route
    mapping = cr_style_map.plan_style(looked["profile"], _installed_styles(home), base=spec["base_style"],
                                      answers=spec.get("answers") or {})
    excerpt = " ".join(looked["excerpts"].get(r["id"], "") for r in spec["references"])[:1500]
    banned = looked["channels"] + [r.get("channel", "") for r in spec["references"]]
    try:
        art = cr_style_art.direct(see, profile, mapping, title=spec["title"], brief=spec.get("brief", ""),
                                  base=spec["base_style"], excerpt=excerpt, sheets=looked["sheets"][:3],
                                  banned=banned, log=log)
    except Exception as error:                              # noqa: BLE001 - the learned settings stand without it
        log(f"  art director: stopped ({type(error).__name__}); the learned settings are kept without it")
        art = {}
    _write(checkpoint, {"schema": 1, "art": art, "spec_sha256": _hash(spec)})
    return art


RENDERER_CONTEXT = ("The candidates were drawn in code by an animation renderer written for this channel, on a plain "
                    "stand-in background where no background picture was drawn. Judge how the characters are built "
                    "and drawn, their poses and effects, the palette and the framing as this channel's; do not judge "
                    "the background.")
RENDERER_CONTEXT_3D = ("The candidates were rendered in real 3D by a three.js renderer written for this channel. Judge how "
                       "the characters are built (proportions, heads, faces, clothes, materials), the sets, the light, "
                       "the palette and the framing as this channel's.")


def _by_video(frames: list) -> list:
    """The channel's frames a video at a time in turn (<video>_<k>.jpg), so the frames Claude sees and the ones held
    out to set the bar span its videos rather than the first one."""
    groups = {}
    for p in frames:
        groups.setdefault(Path(p).stem.rsplit("_", 1)[0], []).append(p)
    return [g[k] for k in range(max((len(g) for g in groups.values()), default=0)) for g in groups.values() if k < len(g)]


def renderer_frames(folder, spec) -> list:
    """The channel's frames a renderer is built and checked against (at most 9, spread over its videos; the last one or
    two are held out to set the bar): the look's drawn frames, filled from the study's own when the look has few."""
    import cr_animated2d
    import cr_style_likeness
    frames = cr_style_likeness.frames(_studio_home(folder), spec["look_id"])
    drawn = [p for p in frames if not cr_style_likeness.flat_graphic(p)]
    frames = _by_video(drawn if len(drawn) >= 4 else frames)
    if len(frames) < 9:
        # one reference gives the look only 4 frames: the study's own frames of it (smaller) fill the frames Claude
        # sees and the ones held out to set the bar, so the bar is still the channel's own
        watched = [p for p in sorted((Path(folder) / "reflearn" / "ref_frames").glob("k*.jpg"))
                   if not cr_animated2d._blank(p, 8)]
        need = min(9 - len(frames), len(watched))
        frames += [watched[i * len(watched) // need] for i in range(need)] if need else []
    return frames[:9]


def renderer_names(spec, looked) -> list:
    """The channel's title and handle: never drawn by its renderer, though its watermark is in the frames studied."""
    handle = re.search(r"youtube\.com/@([\w.\-]+)", str(spec.get("reference_url") or ""))
    names = list(dict.fromkeys([str(r.get("channel") or "") for r in looked.get("references") or []]
                               + ([handle.group(1)] if handle else [])))
    return [n for n in names if n]


def renderer_dims(spec, looked, home) -> int:
    """2 or 3: which kit the study's renderer is written on. A study started before the spec said "2d"/"3d" stored
    True; its planned route says which (a 3D channel once got the 2D kit, and its drafts were flat canvas code)."""
    said = spec.get("renderer")
    if said is not True:
        return 3 if said == "3d" else 2
    import cr_style_map
    planned = cr_style_map.plan_style(looked.get("profile") or {}, _installed_styles(home), base=spec.get("base_style"),
                                      answers=spec.get("answers") or {})
    return 3 if (planned.get("route") or {}).get("engine") == "animated3d" else 2


def _renderer(folder, spec, looked, see, log):
    """Claude writes the channel's animation renderer; its test shots are judged against the channel's own frames and
    it is revised within a budget (cr_animated2d.build). Saved in <operation>/renderer/, so a recovery never repeats a
    Claude call; a renderer below the channel's bar is saved and said, never swapped for something else."""
    import cr_animated2d
    import cr_bridge
    import cr_style_likeness
    import cr_style_map
    home = _studio_home(folder)
    profile = cr_style_map.answer(looked["profile"], spec.get("answers") or {})
    channel = cr_animated2d.from_profile(profile)
    three = renderer_dims(spec, looked, home) == 3
    tall = cr_style_map.vertical(looked["profile"])
    size = {}
    if three:
        size = {"dims": 3, "width": 1080 if tall else 1920, "height": 1920 if tall else 1080}
        channel.update(medium="real 3D (three.js)", format=("vertical 1080x1920 Shorts with captions" if tall else
                                                            "16:9 1920x1080 video"))
    elif tall:                                              # a 2D Shorts channel is drawn vertical, as its videos are
        size = {"width": 1080, "height": 1920}
        channel.update(format="vertical 1080x1920 Shorts with captions")
    art = ((_read(folder / "art-checkpoint.json").get("art") or {}).get("overrides") or {}).get("look") or {}
    if art.get("character"):
        channel["cast_in_words"] = str(art["character"])[:1500]
    frames = renderer_frames(folder, spec)
    names = renderer_names(spec, looked)

    def judge(references, candidates):
        return cr_bridge.call("style/compare", {"references": [cr_style_likeness._b64(p) for p in references],
                                                "candidates": [cr_style_likeness._b64(p) for p in candidates],
                                                "context": RENDERER_CONTEXT_3D if three else RENDERER_CONTEXT})
    result = cr_animated2d.build(folder, {**{k: channel.get(k) for k in ("sky", "floor", "blur")}, **size}, channel, see=see,
                                 judge=judge, references=frames, home=home, log=log, names=names)
    log(f"  renderer: {result.get('state')}"
        + (f", {result.get('overall')}/10 against a bar of {result.get('bar')}" if result.get("overall") is not None
           else f" ({result.get('why', '')})"))
    return result


def _apply_learned(folder, spec, style, log):
    """The look's settings and the art director's words over the built style. Metadata only: no model call."""
    import cr_style_look
    import cr_style_map
    home = _studio_home(folder)
    looked = _read(cr_style_look.folder(home, spec["look_id"]) / "look.json")
    if looked.get("state") != "done":
        return style
    mapping = cr_style_map.plan_style(looked.get("profile") or {}, _installed_styles(home), base=spec["base_style"],
                                      answers=spec.get("answers") or {})
    learned = cr_style_map.apply(style, mapping)
    if mapping["base"] in ("stories", "avatar-premium"):
        # The engine's automatic documentary look adds documentary script and edit guidance; a picture-led channel
        # has its own voice from the art director instead.
        learned.pop("auto", None)
    art = (_read(folder / "art-checkpoint.json").get("art") or {})
    import cr_style_prompts
    # a recipe built around one niche (money stories, dark psychology) writes neutrally for the learned channel
    cr_style_map._merge(learned, cr_style_prompts.overrides(
        mapping["base"], (art.get("art") or {}).get("craft", ""),
        (art.get("overrides") or {}).get("description") or learned.get("description", "")))
    if art.get("overrides"):
        cr_style_map._merge(learned, art["overrides"])
    cr_style_prompts.with_original_sound(learned)      # real clips are found only where the script marks them
    if (mapping.get("route") or {}).get("engine") in ("animated2d", "animated3d"):
        # the characters are animated in code: no writer's words change how the video is made
        look = learned.setdefault("look", {})
        look.update(visuals=mapping["overrides"]["look"]["visuals"], animated2d=mapping["overrides"]["look"]["animated2d"])
        if mapping["overrides"]["look"].get("aspect"):
            look["aspect"] = mapping["overrides"]["look"]["aspect"]
        import cr_animated2d
        if spec.get("renderer"):
            cr_animated2d.attach(learned, folder, home)
        learned["cost"] = cr_animated2d.cost_note(learned)
    learned.setdefault("cr_style_clone", {}).update(
        learned_v2={"look_id": spec["look_id"], "base": mapping["base"], "base_why": mapping["base_why"],
                    "route": mapping.get("route") or {},
                    "needs": mapping["needs"], "cost": mapping["cost"], "art_director": bool(art.get("overrides")),
                    "settings": [{k: r.get(k) for k in ("area", "label", "value", "why")} for r in mapping["settings"]
                                 if r.get("path") != "features.offered"]})
    log(f"  learned settings applied: {len(mapping['settings'])} settings over {mapping['base']}"
        + (", art director's picture style and cast" if art.get("overrides") else ""))
    return learned


def _validated_assets(folder, record):
    """Bind a completed learner record to its actual installed browser kit ({} for a renderer style saved without one)."""
    if record.get("no_kit") and not record.get("kit"):
        return {}
    folder = Path(folder).resolve()
    manifest = _read(folder / "genkit/kit.json")
    if ((record.get("browser") is not True and not record.get("browser_skipped")) or not record.get("kit")
            or manifest.get("name") != record.get("kit")
            or not isinstance(manifest.get("types"), dict) or len(manifest["types"]) < 3):
        raise RuntimeError("The completed study's validated kit is missing. Review the saved study.")
    assets = {}
    for name in ("genkit/kit.json", "genkit/kit.js", "genkit/look_overrides.json", "reflearn/board.jpg"):
        path = folder / name
        if path.is_file():
            if not path.resolve().is_relative_to(folder):
                raise RuntimeError("Keep validated study files inside their saved operation.")
            assets[name] = _file_hash(path)
    if "genkit/kit.js" not in assets:
        raise RuntimeError("The completed study's graphics code is missing. Review the saved study.")
    return assets


def finish_original(engine, folder, spec, design, record, progress, *, learner=None,
                    designer=None, log=None, report=None):
    """Pure metadata handoff after original browser validation; no creative call."""
    if learner is None:
        import reflearn as learner
    if designer is None:
        import autostyle as designer
    folder = Path(folder)
    _validated_assets(folder, record)
    genome = learner.around(design, record, channel=spec["base_style"], minutes=spec["minutes"], title=spec["title"], log=log) or design["genome"]
    genome["learned"] = learner.summary(record)
    progress("publish")
    style = designer.build(genome, engine.STYLE_INFO, spec["base_style"], job=folder, title=spec["title"], log=log)
    saved_style = designer.save(folder, style)
    if saved_style is not None:
        Path(saved_style).chmod(0o600)
    if record.get("no_kit"):
        (log or print)("  graphics: no graphics kit; the style's own renderer draws its pictures")
    elif not designer.genkit(style, folder, log, invalidate=True):
        raise StudyStopped("The validated kit could not attach to this style. No default graphics were substituted.")
    # Original auto looks are hidden variants; make this an explicit selectable
    # style while retaining their actual look, prompts and durable kit manifest.
    style = copy.deepcopy(style)
    style.pop("variant_of", None)
    style.update(name=spec["style_id"], label=spec["label"], builtin=False, auto_style=False)
    style["cr_style_clone"] = {"operation_id": folder.name, "reference_ids": [r["id"] for r in spec["references"]],
        "learned_ref": record.get("ref"), "base_style": spec["base_style"],
        "visual_mode": spec.get("visual_mode", "mixed")}
    if spec.get("look_id"):
        style = _apply_learned(folder, spec, style, log or print)
    if style["cr_style_clone"]["visual_mode"] == "reference_only":
        import cr_style_delivery
        cr_style_delivery.configure_style(style)
    style = _portable(style, _studio_home(folder))
    import cr_style_likeness                                    # a check already paid for, applied again (no model)
    style = cr_style_likeness.apply_saved(folder, style, _studio_home(folder))
    _write(folder / "ready-style.json", style)
    _write(folder / "study-result.json", {"references": [r["id"] for r in spec["references"]],
        "kit": record["kit"], "reference_used": (record.get("reference") or {}).get("id", ""),
        "validated": True, "report": report or record.get("report") or {}})
    return style


def _legacy_completed_metadata(home, folder, saved, engine, learner, designer):
    """Recover admin.18's missing checkpoint from COMPLETE local evidence only.

    This invokes original deterministic validators and registry readers. It does
    not rerun design(), _learn(), Claude, yt-dlp, browser capture or rendering.
    Ambiguous/incomplete evidence is deliberately refused.
    """
    spec = saved["spec"]
    if saved.get("phase") != "validate":
        raise RuntimeError("Only a fully validated study can be saved without repeating creative work.")
    project_id = saved.get("project_id", "")
    if not re.fullmatch(r"crv_[a-f0-9]{24}", project_id):
        raise RuntimeError("The saved study has no owned Claude project.")
    cache = Path(home) / "output/cr-claude-cache" / project_id
    if not cache.resolve().is_relative_to(Path(home).resolve()):
        raise RuntimeError("Keep completed Claude evidence inside this studio.")
    outputs, receipts = {}, []
    files = sorted(cache.glob("cc_*.json"))
    if not files:
        raise RuntimeError("The completed Claude evidence is missing; this study cannot be recovered automatically.")
    for path in files:
        data = _read(path)
        text = data.get("stdout")
        if (not path.resolve().is_relative_to(cache.resolve()) or data.get("state") != "complete"
                or not isinstance(text, str)
                or hashlib.sha256(text.encode()).hexdigest() != data.get("result_fingerprint")):
            raise RuntimeError("A Claude stage is incomplete or uncertain. Review it before any further work.")
        receipts.append(data["result_fingerprint"])
        if data.get("origin") != "make_video:claude":
            continue
        raw = designer._json_obj(text)
        kind = ("analysis" if "genre" in raw and "brief_reading" in raw else
                "concepts" if isinstance(raw.get("concepts"), list) or {"name", "world", "metaphor"}.issubset(raw) else
                "genome" if "overrides" in raw and "director_notes" in raw else None)
        if not kind or kind in outputs:
            raise RuntimeError("The original design evidence is ambiguous. Preserve this study for review.")
        outputs[kind] = raw
    if set(outputs) != {"analysis", "concepts", "genome"}:
        raise RuntimeError("The complete original design evidence is missing. Preserve this study for review.")
    language = (engine.STYLE_INFO.get(spec["base_style"]) or {}).get("language", "English")
    analysis = designer.validate_analysis(outputs["analysis"], spec["title"], language)
    if spec["brief"]:
        analysis["brief"] = designer.read_brief(spec["brief"], outputs["analysis"].get("brief_reading"))
    analysis["job_3d"] = bool(designer.job_3d(engine, spec["base_style"]))
    recent = designer.recent(spec["base_style"], exclude_slug=designer.title_slug(spec["title"]), n=designer.HISTORY_KEEP)
    concepts = _bind(designer.concepts, _ask=lambda *a, **k: outputs["concepts"])(
        engine, spec["title"], analysis, recent)
    chosen, _, _ = designer.pick_concept(analysis, concepts, recent, spec["minutes"])
    genome = designer.validate(analysis, concepts[chosen], outputs["genome"], recent, spec["minutes"], spec["title"])
    design = {"genome": genome, "concepts": concepts, "chosen": chosen, "raw_c": outputs["genome"]}
    evidence = _read(folder / "reflearn/reference-evidence.json")
    refs = {row["id"]: row for row in spec["references"]}
    if evidence.get("actual_frames") is not True or evidence.get("id") not in refs:
        raise RuntimeError("Actual approved reference-frame evidence is missing. Preserve the saved study.")
    assets = _validated_assets(folder, {"kit": _read(folder / "genkit/kit.json").get("name"), "browser": True})
    candidates = []
    library_root = (Path(home) / "learned").resolve()
    for meta_path in library_root.glob("*/meta.json"):
        library = meta_path.parent
        if not library.resolve().is_relative_to(library_root):
            continue
        meta = _read(meta_path)
        valid = meta.get("valid") or {}
        if (meta.get("video") != evidence["id"] or meta.get("first_title") != spec["title"]
                or valid.get("browser") is not True or len(valid.get("kept") or []) < 3):
            continue
        if any(not (library / "kit" / Path(name).name).is_file()
               or _file_hash(library / "kit" / Path(name).name) != digest
               for name, digest in assets.items() if name.startswith("genkit/")):
            continue
        board = library / "sheets/board.jpg"
        if ("reflearn/board.jpg" not in assets or not board.is_file()
                or _file_hash(board) != assets["reflearn/board.jpg"]):
            continue
        candidates.append((library, meta))
    if len(candidates) != 1:
        raise RuntimeError("A unique completed browser validation could not be confirmed. Preserve the saved study.")
    library, meta = candidates[0]
    learned_spec = _read(library / "spec.json")
    kit = _read(folder / "genkit/kit.json")
    if not set(meta["valid"]["kept"]).issubset(kit.get("types") or {}):
        raise RuntimeError("The installed kit differs from its saved browser validation. Preserve the study.")
    record = {"ref": meta["id"], "from": "research", "style_name": meta.get("style_name"),
        "kit": kit["name"], "kit_label": kit.get("label"), "types": list(kit["types"]),
        "reference": {k: refs[evidence["id"]].get(k) for k in ("id", "title", "views", "ratio")},
        "wants": learned_spec.get("genome_wants") or {},
        "cut_s": (learned_spec.get("pacing") or {}).get("median_shot_s"),
        "with_brief": bool(spec["brief"]), "board": str(folder / "reflearn/board.jpg"),
        "library": str(library), "browser": True, "critique": meta["valid"].get("critique"),
        "report": {"total_min": meta.get("minutes"), "total_usd": meta.get("usd")}}
    return design, record, receipts


def recover_metadata(home, operation_id, bridge, *, readiness, engine=None, learner=None, designer=None):
    """Create an owned publication operation, preserving the FAILED study.

    A failed paid operation remains immutable. This new operation may only use
    verified completed assets and original deterministic metadata functions.
    """
    source, original = _saved(home, operation_id, bridge)
    with _lock(_folder(home), ".runner.lock"), _lock(source):
        if original.get("state") != "failed":
            raise RuntimeError("Only a stopped, completed reference study needs metadata recovery.")
        if original.get("refinement_only") and not _recovery_checkpoint_ready(source, original):
            raise RuntimeError("This graphics refinement stopped before its new validation completed. Review its saved Claude work; the earlier kit cannot stand in for the unfinished refinement.")
        index = _folder(home) / ".recoveries" / (operation_id + ".json")
        previous = _read(index)
        if previous.get("operation_id"):
            folder, saved = _saved(home, previous["operation_id"], bridge)
            if saved.get("metadata_only") is not True or saved.get("recovery_of") != operation_id:
                raise RuntimeError("The saved metadata recovery does not belong to this study.")
            with _lock(folder):
                return _finish_metadata_recovery(home, folder, saved, bridge, readiness,
                    engine=engine, learner=learner, designer=designer)
        proof = _proof(bridge, readiness())
        if any(proof[key] != original["proof"][key] for key in ("general_model", "visual_model")):
            raise RuntimeError("Keep the completed study's original Claude model profile.")
        if _file_hash(Path(home) / "styles" / (original["spec"]["base_style"] + ".json")) != original["spec"]["base_sha256"]:
            raise RuntimeError("The original base preset changed. Preserve the completed study for review.")
        cloud = bridge.project_progress(original["project_id"])
        if (not isinstance(cloud, dict) or (cloud.get("project") or {}).get("status") != "failed"
                or any(stage.get("status") != "complete" for stage in cloud.get("stages", []))):
            raise RuntimeError("Reconcile this failed study's completed Claude stages before saving it. An active or uncertain stage cannot be repeated.")
        if designer is None:
            import autostyle as designer
        if learner is None:
            import reflearn as learner
        if engine is None:
            engine = designer._engine()
        if _metadata_checkpoint_ready(source, original):
            design = _read(source / "design-checkpoint.json")["design"]
            record = _read(source / "validated-checkpoint.json")["record"]
        else:
            design, record, receipts = _legacy_completed_metadata(home, source, original, engine, learner, designer)
            registered = {stage.get("result_fingerprint") for stage in cloud.get("stages", [])}
            if len(receipts) != len(cloud.get("stages", [])) or set(receipts) != registered:
                raise RuntimeError("The local completed stages do not match CR's saved receipts. Reconcile the study before saving it.")
        assets = _validated_assets(source, record)
        new_id = "op_" + secrets.token_hex(12)
        folder = _operation(home, new_id)
        folder.mkdir(parents=True, mode=0o700)
        for name in [*assets, "reflearn/reference-evidence.json", *_carried(source)]:
            path = source / name
            if path.is_file():
                target = folder / name
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(path, target)
                target.chmod(0o600)
        record = copy.deepcopy(record)
        record["board"] = str(folder / "reflearn/board.jpg")
        spec = copy.deepcopy(original["spec"])
        saved = {"operation_id": new_id, "owner": original["owner"], "spec": spec, "proof": proof,
            "state": "prepared", "phase": "publish", "recovery_of": operation_id,
            "metadata_only": True, "begin_id": "b_" + secrets.token_hex(16), "created_at": time.time()}
        _write(folder / "design-checkpoint.json", {"schema": 1, "design": design, "spec_sha256": _hash(spec)})
        _write(folder / "validated-checkpoint.json", {"schema": 1, "record": record,
            "spec_sha256": _hash(spec), "assets": _validated_assets(folder, record)})
        _write(folder / "operation.json", saved)
        # The durable begin ID survives a lost response. A repeat of this
        # function only reconciles metadata; it never enters the creative study.
        _write(index, {"operation_id": new_id})
        return _finish_metadata_recovery(home, folder, saved, bridge, readiness,
            engine=engine, learner=learner, designer=designer)


def _finish_metadata_recovery(home, folder, saved, bridge, readiness, *, engine=None, learner=None, designer=None):
    """Idempotent metadata-only completion, including a lost begin response."""
    if saved["state"] in {"complete", "complete_local", "validated"}:
        if saved["state"] == "validated":
            _publish(home, folder, saved)
        _sync(folder, saved, bridge)
        return status(home, folder.name, bridge)
    if saved["state"] == "failed":
        # A new adapter failure is visible and stays stopped; never blindly
        # iterate another publication attempt after a known exception.
        return status(home, folder.name, bridge)
    if saved.get("metadata_only") is not True or not _metadata_checkpoint_ready(folder, saved):
        raise RuntimeError("The completed metadata recovery evidence changed. Preserve the saved study.")
    proof = _proof(bridge, readiness())
    if any(proof[key] != saved["proof"][key] for key in ("general_model", "visual_model")):
        raise RuntimeError("Keep the completed study's original Claude model profile.")
    if saved["state"] == "prepared":
        bridge.prepare_runtime(proof)
        response = bridge._mcp_tool("local_studio_begin", {"begin_id": saved["begin_id"],
            "title": "Save completed style: " + saved["spec"]["title"], "style": saved["spec"]["base_style"],
            "minutes": 1, "features": {}, "options": {}, "setup": proof,
            "max_calls": 1, "platform": "windows" if os.name == "nt" else "mac"})
        saved.update(project_id=response["project"]["id"], state="running", started_at=time.time(), pid=os.getpid())
        _write(folder / "operation.json", saved)
    if designer is None:
        import autostyle as designer
    if learner is None:
        import reflearn as learner
    if engine is None:
        engine = designer._engine()
    design = _read(folder / "design-checkpoint.json")["design"]
    record = _read(folder / "validated-checkpoint.json")["record"]
    try:
        finish_original(engine, folder, saved["spec"], design, record, lambda phase: None,
            learner=learner, designer=designer)
        saved["state"] = "validated"
        _write(folder / "operation.json", saved)
    except BaseException as error:
        _record_failure(folder, saved, error)
        saved.update(state="failed", completed_at=time.time(), finish_receipt={"project_id": saved["project_id"],
            "status": "failed", "artifacts": [], "error_code": "runtime_failed"})
        _write(folder / "operation.json", saved)
        try:
            _sync(folder, saved, bridge)
        except Exception:
            pass
        raise RuntimeError("Saving the completed style stopped. Its validated assets and private diagnostics are preserved; no creative work was repeated.") from None
    _publish(home, folder, saved)
    try:
        _sync(folder, saved, bridge)
    except Exception:
        pass
    return status(home, folder.name, bridge)


@contextmanager
def _creative_budget(minutes):
    """Bound native subprocess time inside this isolated study process only."""
    import cr_claude
    runtime = cr_claude._runtime
    if runtime is None:
        raise RuntimeError("Install the native Claude adapter before starting a reference study.")
    original, deadline = runtime.real_run, time.monotonic() + minutes * 60
    def bounded(command, *args, **kwargs):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, minutes * 60)
        # Original helpers inherit a 900-second CLI cap. An approved study
        # must use its actual remaining budget for creative calls rather than
        # aborting a large kit early. Login/version probes retain their short
        # timeouts; no prompt, model, output cap or retry policy changes.
        timeout = remaining if kwargs.get("input") is not None else min(kwargs.get("timeout") or remaining, remaining)
        return original(command, *args, **{**kwargs, "timeout": timeout})
    runtime.real_run = bounded
    try:
        yield
    finally:
        runtime.real_run = original


def _publish(home, folder, saved):
    style = _read(folder / "ready-style.json")
    if style.get("name") != saved["spec"]["style_id"] or (style.get("cr_style_clone") or {}).get("operation_id") != folder.name:
        raise RuntimeError("The validated style record is missing. Review this saved study before any further work.")
    target = Path(home) / "styles" / (style["name"] + ".json")
    existing = _read(target)
    if existing and (existing.get("cr_style_clone") or {}).get("operation_id") != folder.name:
        raise RuntimeError("A different style uses this name. Its configuration was preserved.")
    _write(target, style)
    report = folder / "completion-report.json"
    _write(report, {"operation_id": folder.name, "style": style["name"], "validated": True,
                    "references": [r["id"] for r in saved["spec"]["references"]]})
    saved.update(state="complete_local", phase="complete", completed_at=time.time(), style_sha256=_file_hash(target))
    saved["finish_receipt"] = {"project_id": saved["project_id"], "status": "done", "error_code": "",
        "artifacts": [{"name": "learned-style.json", "sha256": saved["style_sha256"], "media_type": "application/json"},
                      {"name": "style-study-report.json", "sha256": _file_hash(report), "media_type": "application/json"}]}
    board = folder / "reflearn/board.jpg"
    if board.is_file():
        saved["finish_receipt"]["artifacts"].append({"name": "style-board.jpg", "sha256": _file_hash(board), "media_type": "image/jpeg"})
    _write(folder / "operation.json", saved)


def _carried(source) -> dict:
    """Saved work a new copy of a study keeps without paying again: the art director's words and the likeness check
    (its verdict, test pictures and character sheet), {name: (path, sha256)}."""
    source = Path(source)
    paths = [source / "art-checkpoint.json", source / "likeness.json",
             *sorted((source / "likeness").glob("*.jpg")), *sorted((source / "likeness").glob("*.png"))]
    return {path.relative_to(source).as_posix(): (path, _file_hash(path)) for path in paths if path.is_file()}


def _likeness(home, folder, saved, log=print):
    """After the style is saved: test pictures drawn the way its videos will draw them, judged against the references
    (the ifology clone saved mannequins without a word). The saved style is updated with the verdict, the best picture
    as its drawing and the judge's fixes, and the study keeps owning it (style_sha256). Never undoes or stops a study."""
    spec = saved["spec"]
    if not spec.get("look_id") or spec.get("likeness") is False or saved.get("refinement_only") \
            or saved.get("metadata_only"):
        return
    import cr_style_likeness
    target = Path(home) / "styles" / (spec["style_id"] + ".json")
    try:
        style = _read(target)
        if not _published(home, saved) or (style.get("cr_style_clone") or {}).get("likeness"):
            return
        cr_style_likeness.run(folder, spec, style, log, home=home)
        style = cr_style_likeness.apply_saved(folder, style, home)
        if not (style.get("cr_style_clone") or {}).get("likeness") or not _published(home, saved):
            return
        _write(target, style)
        _write(folder / "ready-style.json", style)
        saved["style_sha256"] = _file_hash(target)
        if saved.get("finish_receipt"):
            saved["finish_receipt"]["artifacts"][0]["sha256"] = saved["style_sha256"]
        _write(folder / "operation.json", saved)
    except Exception as error:                                 # noqa: BLE001 - the saved style stands as it was
        log(f"  likeness check stopped: {str(error)[:160]}")
        check = _read(folder / cr_style_likeness.CHECK)
        if check.get("state") == "running":
            _write(folder / cr_style_likeness.CHECK, {**check, "state": "failed", "why": str(error)[:160]})


def run(home, operation_id, bridge, *, readiness, execute=None):
    """Execute once. Already completed studies only synchronize saved receipts."""
    folder, saved = _saved(home, operation_id, bridge)
    with _lock(_folder(home), ".runner.lock"), _lock(folder):
        if saved["state"] in {"complete", "complete_local"}:
            _sync(folder, saved, bridge)
            return status(home, operation_id, bridge)
        if saved["state"] == "validated":
            _publish(home, folder, saved)
            _sync(folder, saved, bridge)
            return status(home, operation_id, bridge)
        if saved.get("metadata_only"):
            raise RuntimeError("Use Save completed style to finish this metadata operation. No creative study will repeat.")
        if saved["state"] != "prepared":
            raise RuntimeError("This style study already started or stopped. Review its saved Claude stages; it will not execute again automatically.")
        proof = _proof(bridge, readiness())
        if any(proof[key] != saved["proof"][key] for key in ("general_model", "visual_model")):
            raise RuntimeError("Use this style study's pinned Claude model profile.")
        if _file_hash(Path(home) / "styles" / (saved["spec"]["base_style"] + ".json")) != saved["spec"]["base_sha256"]:
            raise RuntimeError("The base preset changed after approval. Preview and approve a new study.")
        bridge.prepare_runtime(proof)
        response = bridge._mcp_tool("local_studio_begin", {"begin_id": saved["begin_id"],
            "title": "Style study: " + saved["spec"]["title"], "style": saved["spec"]["base_style"],
            "minutes": 1, "features": {}, "options": {}, "setup": proof,
            "max_calls": saved["spec"]["max_calls"], "platform": "windows" if os.name == "nt" else "mac"})
        project = response["project"]
        bridge._project = project
        saved.update(state="running", project_id=project["id"], started_at=time.time(), pid=os.getpid())
        _write(folder / "operation.json", saved)
        def progress(phase):
            if phase in PHASES:
                saved["phase"] = phase
                _write(folder / "operation.json", saved)
        try:
            if execute is None:
                import make_video as engine
                import cr_claude
                cr_claude.install(engine, bridge)
                extra = 0
                if saved["spec"].get("renderer"):              # the channel's renderer is written within the study
                    import cr_animated2d
                    extra = cr_animated2d.BUILD_MINUTES
                with _creative_budget(saved["spec"]["budget_minutes"] + extra):
                    if saved.get("refinement_only"):
                        import cr_style_refine
                        cr_style_refine.run_original(engine, folder, saved, progress)
                    else:
                        study_original(engine, folder, saved["spec"], progress)
            else:
                execute(folder, saved["spec"], progress)
            saved["state"] = "validated"
            _write(folder / "operation.json", saved)
        except BaseException as error:
            _record_failure(folder, saved, error)
            saved.update(state="failed", completed_at=time.time(), finish_receipt={"project_id": project["id"],
                "status": "failed", "artifacts": [], "error_code": "runtime_failed"})
            _write(folder / "operation.json", saved)
            try:
                _sync(folder, saved, bridge)
            except Exception:
                pass
            raise
        # From here on recovery only publishes the already validated style or
        # records completion. It cannot repeat a creative call.
        _publish(home, folder, saved)
        _likeness(home, folder, saved)
        try:
            _sync(folder, saved, bridge)
        except Exception:
            pass
        return status(home, operation_id, bridge)


def _readiness(home, bridge):
    import cr_studio
    import cr_readiness
    cr_studio.configure_video_tools()
    checks = {**cr_studio.graphics_ready(), "python": sys.version_info >= (3, 10),
        "ffmpeg": cr_studio.video_tools_ready(), "node": cr_studio.tool_ready("node", "--version") or cr_studio.tool_ready("deno", "--version"),
        "account": bridge.call("status"), "local_version": _read(Path(home) / "cr-release.json").get("version")}
    result = cr_readiness.basic(checks)
    if not result["ready"]:
        raise RuntimeError("Finish setup before studying a style: " + " ".join(result["blockers"]))
    if result.get("brain_mode") != "claudecode":
        raise RuntimeError("Choose and verify Claude Code before studying a style.")
    checks["claude_code"] = result["claude_code"]
    return cr_readiness.proof(checks, purpose="creative")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run an approved, owned reference study through ChannelRecipe.")
    parser.add_argument("--operation", required=True)
    args = parser.parse_args(argv)
    import cr_bridge
    import cr_media
    import cr_render
    # Set native mode before importing the private engine; no API brain route
    # or inherited key helper is selected by this worker.
    if cr_bridge.load_settings().get("brain_mode") != "claudecode":
        raise RuntimeError("Choose and verify Claude Code before starting this study.")
    os.environ["CLAUDE_PROVIDER"] = os.environ["SCRIPT_PROVIDER"] = "claudecode"
    os.environ["CLAUDE_HANDOFF_DIR"] = ""
    import autostyle
    engine = autostyle._engine()
    cr_bridge.install(engine)
    cr_media.install(engine)
    cr_render.install(engine)
    result = run(HOME, args.operation, cr_bridge, readiness=lambda: _readiness(HOME, cr_bridge))
    print(result["message"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        # Raw source/model diagnostics remain in private study logs and caches.
        print("This style study stopped. Review its saved operation and Claude stages in ChannelRecipe setup.", file=sys.stderr)
        raise SystemExit(1) from None
