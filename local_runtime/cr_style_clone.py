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
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from types import FunctionType
from urllib.parse import parse_qs, quote, unquote, urlsplit

HOME = Path(__file__).resolve().parent
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


def preview(home, url, *, learner=None):
    """Bounded original yt-dlp metadata, without Claude or a video-analysis API."""
    canonical = normalize_url(url)
    if learner is None:
        import reflearn as learner
    direct_id = (parse_qs(urlsplit(canonical).query).get("v") or [None])[0]
    candidates = []
    for row in learner._flat(canonical, 1 if direct_id else PREVIEW_LIMIT, 90):
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
        "budget_minutes": budget, "max_calls": calls, "minutes": minutes}


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


def status(home, operation_id, bridge):
    folder, saved = _saved(home, operation_id, bridge)
    state, phase = saved.get("state"), saved.get("phase", "prepared")
    percent, message = PHASES.get(phase, PHASES["prepared"])
    if state == "failed":
        message = "This study stopped. Review its saved Claude stages in setup before approving any further work. It will not run again automatically."
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
        seen = local_watch(reference, clock, work)
        sheets = seen.get("sheets") or []
        if not sheets or not any(Path(p).is_file() for p in sheets):
            return {}
        # YouTube's own captions are a free original source helper. Keep the
        # evidence private and report absence honestly; no speech API fallback.
        rows = transcript(reference["id"], Path(work), original=True)
        excerpt = " ".join(str(row[1]) for row in rows if isinstance(row, (list, tuple))
                           and len(row) == 2 and isinstance(row[0], (int, float)) and 0 <= row[0] < 180)[:1800]
        _write(Path(work) / "reference-evidence.json", {"id": reference["id"], "actual_frames": True,
            "captions_available": bool(rows), "opening_caption_excerpt": excerpt})
        seen["measures"] = {"reference_captions_are_source_data": {
            "available": bool(rows), "opening_excerpt": excerpt}, **(seen.get("measures") or {})}
        return seen
    # Validation's original _media helper can use a deterministic stand-in.
    # This operation approves Claude study, not extra portrait API generation.
    original_media = learner._media
    def validation_media(engine, analysis, job, work):
        return original_media(engine, analysis, job, work, fetch=False)
    learned = _bind(learner._learn, research=allowed, match=lambda *args, **kwargs: [],
                    watch=watch, _media=validation_media, _fallback=lambda *args, **kwargs: None)
    engine.set_extra(spec["brief"])
    progress("design")
    language = (engine.STYLE_INFO.get(spec["base_style"]) or {}).get("language", "English")
    design = designer.design(engine, spec["title"], language, spec["minutes"], channel=spec["base_style"], log=log)
    genome = design["genome"]
    work = folder / learner.WORK
    work.mkdir(parents=True, exist_ok=True)
    record = learned(engine, spec["title"], folder, genome, genome.get("analysis") or {},
                     clock, work, spec["base_style"], spec["minutes"])
    if not record or not record.get("kit") or record.get("browser") is not True:
        raise StudyStopped("The original learner did not produce a graphics kit that passed real browser validation. Your study is saved; no plain preset was substituted.")
    genome = designer.around(design, record, channel=spec["base_style"], minutes=spec["minutes"], title=spec["title"], log=log) or genome
    genome["learned"] = learner.summary(record)
    progress("publish")
    style = designer.build(genome, engine.STYLE_INFO, spec["base_style"], job=folder, title=spec["title"], log=log)
    designer.save(folder, style)
    if not designer.genkit(style, folder, log, invalidate=True):
        raise StudyStopped("The validated kit could not attach to this style. No default graphics were substituted.")
    # Original auto looks are hidden variants; make this an explicit selectable
    # style while retaining their actual look, prompts and durable kit manifest.
    style = copy.deepcopy(style)
    style.pop("variant_of", None)
    style.update(name=spec["style_id"], label=spec["label"], builtin=False, auto_style=False)
    style["cr_style_clone"] = {"operation_id": folder.name, "reference_ids": [r["id"] for r in spec["references"]],
        "learned_ref": record.get("ref"), "base_style": spec["base_style"]}
    _write(folder / "ready-style.json", style)
    _write(folder / "study-result.json", {"references": [r["id"] for r in spec["references"]],
        "kit": record["kit"], "reference_used": (record.get("reference") or {}).get("id", ""),
        "validated": True, "report": clock.report()})
    return style


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
        return original(command, *args, **{**kwargs, "timeout": min(kwargs.get("timeout") or remaining, remaining)})
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
                    study_original(engine, folder, saved["spec"], progress)
            else:
                execute(folder, saved["spec"], progress)
            saved["state"] = "validated"
            _write(folder / "operation.json", saved)
        except BaseException:
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
