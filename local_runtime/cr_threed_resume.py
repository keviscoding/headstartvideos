"""A 3D video that stopped part-way carries on where it stopped, and never pays twice for what was finished.

Swapnil's 18-minute 3D reconstruction video (Claude Pro, 2026-10-29) stopped at Claude's usage limit in the 3D
review, four times over two days, and each "Try again and carry on" started the review again at round 1 ("3D previews,
round 1 of 3, 138 scenes, 690 stills"): the engine saves its reviewed board only after the last round, deletes a
round's previews before making them again, and a finished review call was paid again whenever its re-rendered
pictures came out a byte different (the Claude step's identity includes its pictures). The final 3D clips went the
same way: an interrupted final render left no stamp, and the next run deleted every finished clip.

Studio-side, around the engine (the engine itself is unchanged):
- previews: each review round's previews (the render report and its stills, hard-linked) are kept in
  threed/review_keep/previews/<digest of the shots, render settings and the renderer's own files>, only when the
  round is whole (every shot asked for, none an error). The same shots in a later try are put back instead of
  rendered again: no GPU work, the same pictures, so the same engine findings. Gone when the review or video ends.
- answers: each section's review answer is kept in threed/review_keep/answers/<key>. The key is everything the
  review is asked except what is read off the pictures: the round, the section's shots, the whole board, the words
  under the shots, the title, the review settings, the model, its effort and the engine's own files (an in-place
  update never reuses an older engine's answer). A later try that asks the same reuses the answer with no Claude
  call; a changed board, round, setting, model or engine never matches. Together the rounds replay to where
  the last try stopped (the round checkpoint) and only the rest is paid, even when a stop came mid-round.
- progress: threed/review_keep/progress.json (the round, the sections answered, the shots per round) for the cards'
  "about M review calls left" (cr_claude_use).
- final clips: each finished final clip is remembered with a digest of its own scene spec and the cast
  (threed/cr-final-clips.json; a clip an interrupted render finished is found from that render's video.json). A later
  final render keeps every clip whose spec is unchanged and renders only the rest.
- names: each 3D Claude step is labelled ("3D picture review, round 1") for the stopped card (cr_claude.labelled).
A creator's forced remake (force) reuses nothing.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import re
import shutil
import threading
import time
from pathlib import Path

KEEP = "review_keep"
CLIPS = "cr-final-clips.json"          # in <job>/threed: {"clips": {scene id: {"spec": digest, "mtime": float}}}
SIDE = "final_kept"                    # <job>/threed/final_kept: finished clips while the rest are rendered
JSON_AGAIN = "\n\nYour previous reply was not valid JSON. Output ONLY the JSON object."   # the engine's repair ask
# the review settings an answer depends on (look.threed and Create's choices; the model is the call's own)
SETTINGS = ("review_rounds", "preview_frac", "pass_score", "sheet_scenes", "lean_review", "lean_call_scenes",
            "preview_w", "preview_h", "preset", "model", "verify_last", "section_s")
# what is read off the pictures: never part of an answer's key (a re-render may differ in a pixel or a QA number)
FROM_PICTURES = ("sheet_rows", "issues", "checks")
LABELS = {"director": "3D shot planning", "review": "3D picture review"}

_lock = threading.RLock()
_ctx: dict = {}                        # the review being made in this engine process (one video per process)
_asked: dict = {}                      # digest of a review prompt -> {"key", "round", "job"}


# ── small helpers ─────────────────────────────────────────────────────────────────────────────────────────────────

def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
                          .encode("utf-8")).hexdigest()


def _read(path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, data) -> None:
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        for attempt in range(8):                           # Windows: a reader may hold the file a moment
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except OSError:
        pass                                               # bookkeeping never stops a video
    finally:
        try:
            tmp.unlink(missing_ok=True)                    # never left behind (Windows: a failed replace)
        except OSError:
            pass


def _link(src, dst) -> None:
    """dst as a hard link to src (no second copy of a still on disk), else a copy."""
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


# ── the engine's own files: an answer or a preview from an older engine is never reused ──────────────────────────

_versions: dict = {}


def _files_digest(paths) -> str:
    h = hashlib.sha256()
    for path in sorted({Path(p) for p in paths if p}, key=str):
        try:
            if path.is_file():
                h.update(str(path.name).encode("utf-8"))
                h.update(hashlib.sha256(path.read_bytes()).digest())
        except OSError:
            h.update(b"?")
    return h.hexdigest()


def engine_version(threed, what) -> str:
    """A digest of the engine files `what` depends on: "review" (threed.py, the director's storyboard.py and its
    prompts and rules: review.md, QUALITY.md...) or "previews" (the renderer, the scene engine and the catalog).
    Computed once per engine process; an in-place Studio update changes it, and nothing older is reused."""
    cache = (what, str(getattr(threed, "__file__", "") or id(threed)))
    with _lock:
        if cache in _versions:
            return _versions[cache]
    files = []
    try:
        here = Path(getattr(threed, "__file__", "") or ".").resolve().parent
        root = Path(getattr(threed, "ROOT", here / "threed"))
        sb = getattr(getattr(threed, "SB", None), "__file__", None)
        if what == "review":
            files = [getattr(threed, "__file__", None), root / "catalog.json"]
            if sb:
                files += [p for p in Path(sb).resolve().parent.iterdir() if p.is_file() and p.suffix != ".pyc"]
        else:
            files = [getattr(threed, "RENDER", root / "render.py"), root / "catalog.json"]
            engine_dir = root / "engine"
            if engine_dir.is_dir():
                files += [p for p in engine_dir.rglob("*") if p.is_file()]
    except Exception:                                      # noqa: BLE001 - an unknown engine: nothing matches
        files = []
    value = _files_digest(files) if files else "unknown-" + str(time.time())
    with _lock:
        _versions[cache] = value
    return value


def _effort(model) -> str:
    """The effort Claude Code is asked for on this call (cr_claude's choice), part of an answer's identity."""
    try:
        import cr_claude
        scoped = cr_claude._call_effort.get()
        if scoped == "default":
            return ""
        if scoped in cr_claude.EFFORT_LEVELS:
            return scoped
        return cr_claude.stage_effort("visual" if "opus" in str(model).lower() else "general")
    except Exception:                                      # noqa: BLE001
        return ""


def _bound(original, args, kwargs):
    """The call's arguments by name, or None when they don't fit the engine's own signature (then: no wrapping)."""
    try:
        return dict(inspect.signature(original).bind(*args, **kwargs).arguments)
    except (TypeError, ValueError):
        return None


def _log(engine, msg) -> None:
    try:
        (getattr(engine, "log", None) or print)(msg)
    except Exception:                                      # noqa: BLE001 - a progress line only
        pass


def _job_of(path):
    """The job folder of a path inside <job>/threed/, else None."""
    p = Path(path).resolve()
    return next((q.parent for q in [p, *p.parents] if q.name == "threed"), None)


def keep_dir(job) -> Path:
    return Path(job) / "threed" / KEEP


def _labelled(label):
    try:
        import cr_claude
        return cr_claude.labelled(label)
    except Exception:                                      # noqa: BLE001 - outside Studio: no names
        import contextlib
        return contextlib.nullcontext()


def _current_label() -> str:
    try:
        import cr_claude
        return cr_claude.stage_label()
    except Exception:                                      # noqa: BLE001
        return ""


# ── review progress (for "about M review calls left") ────────────────────────────────────────────────────────────

def _progress(job, change) -> None:
    with _lock:
        path = keep_dir(job) / "progress.json"
        data = _read(path)
        change(data)
        data["at"] = time.time()
        _write(path, data)


def calls_left(progress: dict) -> dict:
    """{"round", "rounds", "left", "finished"} from progress.json: the calls of this round not yet answered, plus each
    later round at most this round's shots (lean rounds pack `per` shots a call). An upper estimate: rounds end early
    when every shot passes."""
    rounds = int(progress.get("rounds") or 0)
    if not rounds or progress.get("finished"):
        return {"round": int(progress.get("round") or 0), "rounds": rounds, "left": 0,
                "finished": bool(progress.get("finished"))}
    cur = max(1, int(progress.get("round") or 1))
    per = max(1, int(progress.get("per") or 24))
    scenes = progress.get("scenes") or {}
    sections = progress.get("sections") or {}
    answered = progress.get("answered") or {}
    known = int(scenes.get(str(cur)) or scenes.get("1") or 0)

    def calls(r):
        if str(r) in sections:
            return int(sections[str(r)] or 0)
        n = int(scenes.get(str(r)) or known)
        return math.ceil(n / per) if n else 0
    left = max(0, calls(cur) - len(answered.get(str(cur)) or []))
    later = math.ceil(known / per) if known else 0
    left += later * max(0, rounds - cur)
    return {"round": cur, "rounds": rounds, "left": left, "finished": False}


# ── the review ────────────────────────────────────────────────────────────────────────────────────────────────────

def _wrap_review(threed, original):
    def review(engine, job, style, title, board, sents, context, cfg, catalog, force, *args, **kwargs):
        job = Path(job)
        settings = {k: cfg.get(k) for k in SETTINGS if k in cfg}
        rounds = int(cfg.get("review_rounds") or 0)
        board_key = digest(["board", board, settings])     # before the engine changes the board in place
        try:
            first = len(threed.sections([m for m in board["moments"]], cfg))
        except Exception:                                  # noqa: BLE001 - the estimate only
            first = 0
        shots = sum(len(m.get("scenes") or []) for m in board.get("moments") or [])

        def start(data):
            if data.get("board_key") != board_key:
                data.clear()
            data.update(board_key=board_key, rounds=rounds, finished=False,
                        per=max(6, int(cfg.get("lean_call_scenes", 24) or 24)),
                        lean=bool(cfg.get("lean_review", True)))
            data.setdefault("round", 0)
            data.setdefault("sections", {})["1"] = first
            data.setdefault("scenes", {}).setdefault("1", shots)
            data.setdefault("answered", {})
        if rounds > 0:
            _progress(job, start)
        with _lock:
            _ctx.clear()
            _ctx.update(job=str(job), settings=settings, force=bool(force), catalogs={},
                        engine=engine_version(threed, "review"), threed=threed)
            _asked.clear()
        try:
            out = original(engine, job, style, title, board, sents, context, cfg, catalog, force, *args, **kwargs)
        finally:
            with _lock:
                _ctx.clear()
                _asked.clear()
        if rounds > 0:
            _progress(job, lambda data: data.update(finished=True))
        # the reviewed board is saved: its previews are no longer needed (the answers are small and stay)
        shutil.rmtree(keep_dir(job) / "previews", ignore_errors=True)
        return out
    review._cr_resume = True
    return review


def _catalog_digest(catalog) -> str:
    with _lock:
        known = _ctx.get("catalogs")
        if known is not None and id(catalog) in known:
            return known[id(catalog)]
    value = digest(catalog) if catalog is not None else ""
    with _lock:
        if known is not None:
            known[id(catalog)] = value
    return value


def answer_key(arguments: dict, catalog_digest: str, settings: dict, engine: str = "") -> str:
    """The identity of one section's review answer: what the review is asked, without what is read off the pictures,
    with the review settings and the engine's own files (an in-place update never reuses an older engine's answer)."""
    asked = {k: v for k, v in arguments.items() if k not in FROM_PICTURES and k != "catalog"}
    return digest(["review-answer-v2", asked, catalog_digest, settings, engine])


def _wrap_prompt(original):
    try:
        signature = inspect.signature(original)
    except (TypeError, ValueError):
        return original

    def review_prompt(*args, **kwargs):
        prompt = original(*args, **kwargs)
        try:
            with _lock:
                ctx = dict(_ctx)
            if ctx and not ctx.get("force"):
                arguments = dict(signature.bind(*args, **kwargs).arguments)
                key = answer_key(arguments, _catalog_digest(arguments.get("catalog")), ctx["settings"],
                                 ctx.get("engine", ""))
                rn = arguments.get("round_no")
                with _lock:
                    _asked[digest(prompt)] = {"key": key, "round": int(rn) if isinstance(rn, int) else 0,
                                              "job": ctx["job"]}
        except Exception:                                  # noqa: BLE001 - no reuse, the review goes on as before
            pass
        return prompt
    review_prompt._cr_resume = True
    return review_prompt


def _parses(SB, raw) -> bool:
    try:
        return isinstance(SB._json_object(raw), dict)
    except Exception:                                      # noqa: BLE001
        return False


def _answered(job, rn, key) -> None:
    def note(data):
        done = data.setdefault("answered", {}).setdefault(str(rn), [])
        if key[:16] not in done:
            done.append(key[:16])
    _progress(job, note)


def _wrap_look(SB, original):
    def _look(*args, **kwargs):
        named = _bound(original, args, kwargs) or {}
        prompt, model, engine = named.get("prompt"), named.get("model"), named.get("engine")
        repair = isinstance(prompt, str) and prompt.endswith(JSON_AGAIN)
        base = prompt[:-len(JSON_AGAIN)] if repair else prompt
        with _lock:
            asked = _asked.get(digest(base)) if isinstance(base, str) else None
            force = bool(_ctx.get("force"))
        if not asked or force:
            return original(*args, **kwargs)
        rn, job = asked["round"], Path(asked["job"])
        key = digest([asked["key"], str(model), _effort(model)])
        path = keep_dir(job) / "answers" / f"{key[:40]}.json"
        saved = _read(path)
        if not repair and saved.get("key") == key and isinstance(saved.get("raw"), str) and _parses(SB, saved["raw"]):
            _log(engine, f"  3D review: round {rn}: a section Claude already reviewed in an earlier try is reused "
                         "(no new Claude call)")
            _answered(job, rn, key)
            return saved["raw"]
        with _labelled(f"3D picture review, round {rn}" if rn else "3D picture review"):
            raw = original(*args, **kwargs)
        if _parses(SB, raw):
            _write(path, {"key": key, "round": rn, "raw": raw, "at": time.time()})
            _answered(job, rn, key)
        return raw
    _look._cr_resume = True
    return _look


def _wrap_cc(original):
    """Every other 3D Claude step is named too (the director: "3D shot planning")."""
    def _cc(*args, **kwargs):
        what = (_bound(original, args, kwargs) or {}).get("what", "call")
        if _current_label():
            return original(*args, **kwargs)
        with _labelled(LABELS.get(str(what), "3D " + str(what))):
            return original(*args, **kwargs)
    _cc._cr_resume = True
    return _cc


# ── a round's previews ────────────────────────────────────────────────────────────────────────────────────────────

def complete_round(rep, video) -> bool:
    """A round worth keeping: one entry per shot asked for, none of them an error (a renderer that died mid-round
    leaves a report of the shots it reached; a GPU hiccup leaves "error" shots), and every still on disk."""
    if not isinstance(rep, dict) or not isinstance(video, dict):
        return False
    asked = [str(s.get("id")) for s in video.get("scenes") or [] if isinstance(s, dict)]
    got = [str(s.get("id")) for s in rep.get("scenes") or [] if isinstance(s, dict)]
    if not asked or sorted(asked) != sorted(got):
        return False
    for scene in rep["scenes"]:
        if scene.get("status") == "error" or scene.get("error"):
            return False
        for still in scene.get("stills") or []:
            if not Path(str(still.get("file") or "")).is_file():
                return False
    return True


def _restore(keep: Path, outdir: Path):
    """The kept previews put back into outdir, as the engine's report; None when anything is missing."""
    rep = _read(keep / "report.json")
    if not rep.get("scenes"):
        return None
    for scene in rep["scenes"]:
        for still in scene.get("stills") or []:
            if not (keep / str(still.get("name") or "")).is_file():
                return None
    outdir.mkdir(parents=True, exist_ok=True)
    for scene in rep["scenes"]:
        for still in scene.get("stills") or []:
            name = still.pop("name")
            target = outdir / name
            target.unlink(missing_ok=True)
            _link(keep / name, target)
            still["file"] = str(target)
    rep["_s"] = 0.0
    (outdir / "report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
    return rep


def _save_previews(rep: dict, keep: Path) -> None:
    tmp = keep.with_name(keep.name + f".{os.getpid()}.tmp")
    try:
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        saved = json.loads(json.dumps(rep, default=str))
        for scene in saved.get("scenes") or []:
            for still in scene.get("stills") or []:
                src = Path(str(still.get("file") or ""))
                still["name"] = src.name
                _link(src, tmp / src.name)                 # a hard link: the round's stills aren't stored twice
        (tmp / "report.json").write_text(json.dumps(saved, indent=1, ensure_ascii=False), encoding="utf-8")
        shutil.rmtree(keep, ignore_errors=True)
        os.replace(tmp, keep)
    except Exception:                                      # noqa: BLE001 - not kept: a later try renders again
        pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _wrap_render(threed, original):
    def _render(*args, **kwargs):
        named = _bound(original, args, kwargs) or {}
        what, outdir, video, flags = named.get("what"), named.get("outdir"), named.get("video"), named.get("args")
        engine = named.get("engine")
        found = re.match(r"previews round (\d+)", str(what or ""))
        with _lock:
            ctx = dict(_ctx)
        job = _job_of(outdir) if found and outdir is not None else None
        if (not found or job is None or not ctx or ctx.get("force") or not isinstance(video, dict)
                or "--preview-frac" not in [str(a) for a in flags or []]):
            return original(*args, **kwargs)
        rn = int(found.group(1))
        count = len(video.get("scenes") or [])

        def at_round(data):
            data["round"] = rn
            data.setdefault("scenes", {})[str(rn)] = count
            data.setdefault("answered", {}).setdefault(str(rn), [])
        _progress(job, at_round)
        keep = keep_dir(job) / "previews" / digest(["previews-v2", video, [str(a) for a in flags],
                                                    engine_version(threed, "previews")])[:32]
        try:
            rep = _restore(keep, Path(outdir).resolve())
        except Exception as error:                         # noqa: BLE001 - a full disk, a long Windows path...
            _log(engine, f"3D: previews round {rn}: the earlier try's pictures couldn't be put back "
                         f"({type(error).__name__}); rendering them again")
            rep = None
        if rep is not None:
            _log(engine, f"3D: previews round {rn}: the {count} shots' pictures from an earlier try are kept "
                         "(nothing rendered again)")
            return rep
        rep = original(*args, **kwargs)
        if complete_round(rep, video):
            _save_previews(rep, keep)
        return rep
    _render._cr_resume = True
    return _render


# ── the final clips ───────────────────────────────────────────────────────────────────────────────────────────────

def stamp_key(video) -> str:
    """The engine's own final-render stamp key for a board's video.json (threed.final_render's formula)."""
    return hashlib.sha1(json.dumps([video], sort_keys=True).encode()).hexdigest()[:12]


def _stamp_matches(out: Path, video) -> bool:
    """The engine's stamp is for this very board: its own cache keeps everything (and re-tries only the scenes that
    couldn't be built), so nothing here is touched."""
    stamp = _read(out / "stamp.json")
    return bool(stamp.get("key")) and stamp.get("key") == stamp_key(video)


def clip_specs(threed, board, cfg, video=None) -> dict:
    """{scene id: digest of its own render spec and the cast}, as the engine renders it."""
    video = video if video is not None else threed._video(board, cfg["sub"], preset=cfg.get("preset"))
    cast = video.get("cast") or []
    return {str(s["id"]): digest(["clip-v1", s, cast]) for s in video.get("scenes") or []}


def _mtime(path) -> float:
    try:
        return round(Path(path).stat().st_mtime, 3)
    except OSError:
        return 0.0


def _clips(job) -> dict:
    return dict(_read(Path(job) / "threed" / CLIPS).get("clips") or {})


def _harvest(out: Path, clips: dict) -> dict:
    """The clips an interrupted render finished: each scene of its video.json whose clip was written after it (the
    renderer writes a clip whole or not at all)."""
    vj = out / "video.json"
    video = _read(vj)
    started = _mtime(vj)
    cast = video.get("cast") or []
    for s in video.get("scenes") or []:
        if not isinstance(s, dict) or "id" not in s:
            continue
        sid = str(s["id"])
        at = _mtime(out / f"{sid}.mp4")
        if at and at >= started - 2 and (clips.get(sid) or {}).get("mtime") != at:
            clips[sid] = {"spec": digest(["clip-v1", s, cast]), "mtime": at}
    return clips


def remember(job, specs: dict, mp4: dict) -> None:
    """Each finished clip in <job>/threed/final with the spec it was rendered from."""
    out = Path(job) / "threed" / "final"
    clips = _clips(job)
    for sid, path in (mp4 or {}).items():
        sid = str(sid)
        f = out / f"{sid}.mp4"
        if sid in specs and f.is_file() and Path(path).resolve() == f.resolve():
            clips[sid] = {"spec": specs[sid], "mtime": _mtime(f)}
    _write(Path(job) / "threed" / CLIPS, {"clips": clips})


def _put_back(side: Path, out: Path) -> None:
    """Clips set aside by a render that stopped half-way go back first."""
    if not side.is_dir():
        return
    out.mkdir(parents=True, exist_ok=True)
    for f in side.glob("*.mp4"):
        try:
            if (out / f.name).exists():
                f.unlink()
            else:
                os.replace(f, out / f.name)
        except OSError:
            pass
    try:
        side.rmdir()
    except OSError:
        pass


def _wrap_final(threed, original):
    def final_render(engine, job, board, cfg, force, *args, **kwargs):
        job = Path(job)
        try:
            video = threed._video(board, cfg["sub"], preset=cfg.get("preset"))
            specs = clip_specs(threed, board, cfg, video)
        except Exception:                                  # noqa: BLE001 - the engine's own way
            return original(engine, job, board, cfg, force, *args, **kwargs)
        root = job / "threed"
        out, side = root / "final", root / SIDE
        _put_back(side, out)
        if force or _stamp_matches(out, video) or (not (root / CLIPS).exists() and (out / "stamp.json").exists()):
            # a forced remake, the engine's stamp for this very board (a finished render, its repairs and
            # _before_repair untouched), or clips from before Studio 112: the engine's own stamp decides, as before
            got = original(engine, job, board, cfg, force, *args, **kwargs)
            remember(job, specs, got)
            return got
        clips = _harvest(out, _clips(job))
        kept = {sid: str(out / f"{sid}.mp4") for sid, spec in specs.items()
                if (clips.get(sid) or {}).get("spec") == spec and (out / f"{sid}.mp4").is_file()
                and (clips.get(sid) or {}).get("mtime") == _mtime(out / f"{sid}.mp4")}
        if not kept:
            got = original(engine, job, board, cfg, force, *args, **kwargs)
            remember(job, specs, got)
            return got
        todo = [s for m in board.get("moments") or [] for s in m.get("scenes") or [] if str(s.get("id")) not in kept]
        if not todo:
            _log(engine, f"3D: final render: all {len(kept)} scenes were finished in an earlier try (kept)")
            remember(job, specs, kept)
            return kept
        _log(engine, f"3D: final render: keeping {len(kept)} finished scenes from an earlier try; rendering the "
                     f"other {len(todo)}")
        side.mkdir(parents=True, exist_ok=True)
        for sid in kept:                                   # the engine clears the folder before a new render
            os.replace(out / f"{sid}.mp4", side / f"{sid}.mp4")
        try:
            got = original(engine, job, dict(board, moments=[{"id": 0, "scenes": todo}]), cfg, force,
                           *args, **kwargs)
        finally:
            _put_back(side, out)
        merged = dict(kept)
        merged.update(got or {})
        remember(job, specs, merged)
        return merged
    final_render._cr_resume = True
    return final_render


def _wrap_repair(threed, original):
    """A repaired clip is remembered with its repaired spec, so a later try keeps it too."""
    def repair_pass(engine, job, board, mp4, cfg, *args, **kwargs):
        result = original(engine, job, board, mp4, cfg, *args, **kwargs)
        try:
            new_board, clips = result[0], result[1]
            remember(job, clip_specs(threed, new_board, cfg), clips)
        except Exception:                                  # noqa: BLE001 - bookkeeping only
            pass
        return result
    repair_pass._cr_resume = True
    return repair_pass


def install(threed) -> None:
    """Wrap the engine's review, previews, review calls and final render (each once; a missing one is left alone)."""
    SB = getattr(threed, "SB", None)
    plan = [("review", lambda f: _wrap_review(threed, f)), ("_render", lambda f: _wrap_render(threed, f)),
            ("final_render", lambda f: _wrap_final(threed, f)), ("repair_pass", lambda f: _wrap_repair(threed, f)),
            ("_cc", _wrap_cc)]
    if SB is not None and hasattr(SB, "_json_object"):
        plan.append(("_look", lambda f: _wrap_look(SB, f)))
    for name, wrap in plan:
        fn = getattr(threed, name, None)
        if callable(fn) and not getattr(fn, "_cr_resume", False):
            setattr(threed, name, wrap(fn))
    fn = getattr(SB, "review_prompt", None) if SB is not None else None
    if callable(fn) and not getattr(fn, "_cr_resume", False):
        SB.review_prompt = _wrap_prompt(fn)
