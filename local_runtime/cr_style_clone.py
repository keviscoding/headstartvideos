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
PREVIEW_LIMIT = 8
PHASES = {
    "prepared": (0, "Your approved reference study is ready to start."),
    "design": (8, "Designing the style around your example title and brief."),
    "research": (18, "Using only the reference videos you approved."),
    "watch": (30, "Studying actual reference frames, pacing, graphics and sound."),
    "build": (58, "Writing the original learner's graphics kit."),
    "validate": (78, "Rendering and reviewing the learned graphics against the reference."),
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
                handle.seek(0); handle.write(b"0"); handle.flush(); handle.seek(0)
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
    """Studio's own yt-dlp (installed and updated with Studio) before an older copy on PATH."""
    try:
        import yt_dlp  # noqa: F401
        return [sys.executable, "-m", "yt_dlp"]
    except ImportError:
        found = shutil.which("yt-dlp")
        return [found] if found else []


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
                      capture_output=True, text=True, timeout=240)
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
            "eligible": 60 <= duration <= 4 * 3600,
            "eligibility_note": "" if 60 <= duration <= 4 * 3600 else "Choose a reference between one minute and four hours with a known duration."})
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
    if requested_name and (not isinstance(requested_name, str) or not re.fullmatch(r"[a-z0-9_-]{1,50}", requested_name)):
        raise ValueError("Use a short style ID with lowercase letters, numbers and hyphens.")
    brief = supplied.get("brief", "")
    if not isinstance(brief, str) or len(brief) > 4000:
        raise ValueError("Keep the style brief under 4,000 characters.")
    visual_mode = supplied.get("visual_mode", "reference_only")
    if not isinstance(visual_mode, str) or visual_mode not in {"reference_only", "mixed"}:
        raise ValueError("Choose reference animation only or a mixed-media style.")
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
        "visual_mode": visual_mode, "attempt_id": attempt_id}


def prepare(home, supplied, proof, bridge):
    """Save an immutable, account-owned operation; no model work starts here."""
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
    if state == "failed":
        message = "This study stopped. Review its saved Claude stages in setup before approving any further work. It will not run again automatically."
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
    result["setup_review_required"] = state == "failed" and native_failure not in {"timeout", "usage_limit"} and failure_code != "metadata_handoff_failed"
    if state == "failed" and not result["metadata_recovery_available"]:
        result["retry_spec"] = {key: copy.deepcopy(saved["spec"][key]) for key in (
            "preview_id", "reference_url", "references", "base_style", "name", "label", "title", "brief",
            "budget_minutes", "max_calls", "minutes") if key in saved["spec"]}
        result["retry_spec"]["visual_mode"] = saved["spec"].get("visual_mode", "reference_only")
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
    return result


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
    _write(folder / "failure.json", {"schema": 1, "failure_code": saved["failure_code"],
        "exception_type": type(error).__name__, "phase": saved.get("phase"), "at": time.time(),
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
    local_watch = _bind(learner.watch, watch_remote=stop_remote)
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
    if not record or not record.get("kit") or record.get("browser") is not True:
        raise StudyStopped("The original learner did not produce a graphics kit that passed real browser validation. Your study is saved; no plain preset was substituted.")
    score = (record.get("critique") or {}).get("score")
    if spec.get("visual_mode") == "reference_only" and (isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score)):
        raise StudyStopped("The reference animation kit has not completed its visual comparison. It is saved, but it is not ready to use; no unreviewed preset was substituted.")
    _write(folder / "validated-checkpoint.json", {"schema": 1, "record": record,
        "spec_sha256": _hash(spec), "assets": _validated_assets(folder, record)})
    return finish_original(engine, folder, spec, design, record, progress,
        learner=learner, designer=designer, log=log, report=clock.report())


def _validated_assets(folder, record):
    """Bind a completed learner record to its actual installed browser kit."""
    folder = Path(folder).resolve()
    manifest = _read(folder / "genkit/kit.json")
    if (record.get("browser") is not True or not record.get("kit")
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
    if not designer.genkit(style, folder, log, invalidate=True):
        raise StudyStopped("The validated kit could not attach to this style. No default graphics were substituted.")
    # Original auto looks are hidden variants; make this an explicit selectable
    # style while retaining their actual look, prompts and durable kit manifest.
    style = copy.deepcopy(style)
    style.pop("variant_of", None)
    style.update(name=spec["style_id"], label=spec["label"], builtin=False, auto_style=False)
    style["cr_style_clone"] = {"operation_id": folder.name, "reference_ids": [r["id"] for r in spec["references"]],
        "learned_ref": record.get("ref"), "base_style": spec["base_style"],
        "visual_mode": spec.get("visual_mode", "mixed")}
    if style["cr_style_clone"]["visual_mode"] == "reference_only":
        import cr_style_delivery
        cr_style_delivery.configure_style(style)
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
        for name in [*assets, "reflearn/reference-evidence.json"]:
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
                with _creative_budget(saved["spec"]["budget_minutes"]):
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
