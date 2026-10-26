"""Style Lab: the creator's Claude Code works on a style's renderer like on any code project, and the result plugs back
into ChannelRecipe as that style's renderer, reused by every video.

A renderer is the code that draws a style's videos (2D canvas or three.js, cr_animated2d's kit). The study writes a
first one; in the lab Claude edits it freely, previews it, checks it against the channel's own frames and saves it:

    <Studio>/style-lab/<style>/
        renderer.js     the renderer being worked on (the style's own, or the kit's example for a new one)
        scene.json      the test scene previews draw (edit it to show what is being worked on)
        refs/           the channel's frames: shown_*.jpg to study, held_*.jpg held out to set the bar
        GUIDE.md        how the lab works, the kit's guide and its example renderer
        preview/<sha>/  each preview's test shots (shot_*.jpg) and a 5 s video (preview.mp4)
        lab.json        the style, its size and kit, the bar, and every preview and check (history)

    start    free: the workspace, from the style as it is saved
    preview  free, on this computer: the contract checks, the test shots, a 5 s video and the stress sweep
    check    the study's likeness judge: the test shots against the channel's frames, scored against its bar
    save     the previewed renderer becomes the style's next version (renderer-vN.js); metadata only

Nothing here spends the creator's money or calls Claude: the creator's own Claude Code is the one writing the code.

    .venv/bin/python cr_style_lab.py start|status|preview|check|save STYLE      (the same from a terminal)
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAB = "style-lab"
SCHEMA = 1
PREVIEW_S = 5.0
HISTORY = 60                            # previews and checks kept in lab.json
FRAME_MS = 400                          # slower than this a frame and a 60 s Short takes over 12 minutes to draw
_STYLE = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}")
SAMPLE_WORDS = ("In this world everyone has a number on their chest and nobody knows what it counts until today")


class LabError(ValueError):
    """What the creator (or their Claude Code) should read and fix."""


# ── the workspace ─────────────────────────────────────────────────────────────────────────────────────────────────

def folder(home, style: str) -> Path:
    if not isinstance(style, str) or not _STYLE.fullmatch(style):
        raise LabError("Choose one of your styles by its id (from list_styles).")
    return Path(home) / LAB / style


def _read(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _style(home, style: str) -> dict:
    folder(home, style)                                     # a style id, never a path
    data = _read(Path(home) / "styles" / f"{style}.json")
    if not data:
        raise LabError(f"There is no style {style} in this Studio.")
    return data


def _spec(style: dict) -> dict:
    """The style's animation spec (look.animated2d): its kit (2D or 3D), size, captions, colours and renderer."""
    import cr_animated2d as a2d
    look = style.get("look") or {}
    if look.get("visuals") not in a2d.PLUGINS or not isinstance(look.get("animated2d"), dict):
        raise LabError("This style draws pictures, so it has no renderer to work on. Style Lab works on styles animated "
                       "in code: clone a channel and answer that it is animated (2D) or rendered in 3D.")
    return look["animated2d"]


def _sha(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _operation(style: dict) -> str:
    op = str((style.get("cr_style_clone") or {}).get("operation_id") or "")
    return op if re.fullmatch(r"op_[a-f0-9]{24}", op) else ""


def _unbox(src: Path, dest: Path):
    """A frame without its black bars: the channel's Shorts often reach the study pillarboxed inside a 16:9 frame,
    and the judge compares pictures, not borders."""
    from PIL import Image
    im = Image.open(src).convert("RGB")
    grey = im.convert("L")
    w, h = grey.size
    flat = lambda im: list(im.get_flattened_data() if hasattr(im, "get_flattened_data") else im.getdata())
    cols, rows = flat(grey.resize((w, 1), Image.BOX)), flat(grey.resize((1, h), Image.BOX))
    lit_c = [i for i, v in enumerate(cols) if v > 10]
    lit_r = [i for i, v in enumerate(rows) if v > 10]
    box = [0, 0, w, h]
    if lit_c and lit_c[-1] - lit_c[0] + 1 < 0.9 * w:
        box[0], box[2] = lit_c[0], lit_c[-1] + 1
    if lit_r and lit_r[-1] - lit_r[0] + 1 < 0.9 * h:
        box[1], box[3] = lit_r[0], lit_r[-1] + 1
    if box[2] - box[0] < 0.2 * w or box[3] - box[1] < 0.2 * h:
        box = [0, 0, w, h]                              # mostly dark: a night frame, not bars
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.crop(tuple(box)).save(dest, quality=92)


def _starter_scene(spec: dict) -> dict:
    """A small test scene from what the study saw (the cast, colours and pace), when the study left none."""
    cast = {}
    for k, c in enumerate(spec.get("cast") or [{"name": "hero"}]):
        ident = re.sub(r"[^a-z0-9_]", "", str(c.get("name") or f"c{k}").lower().replace(" ", "_"))[:24] or f"c{k}"
        cast[ident] = {"name": str(c.get("name") or ident), **({"color": c["color"]} if c.get("color") else {})}
    ids = list(cast) or ["hero"]
    shot = max(0.8, min(4.0, float(spec.get("shot_s") or 1.5)))
    duration = 6.0
    framings = ["wide", "medium:" + ids[0], "close:" + ids[0]]
    shots, t, k = [], 0.0, 0
    while t < duration:
        shots.append({"start": round(t, 2), "end": round(min(duration, t + shot), 2), "set": "place",
                      "framing": framings[k % 3], "move": "push", "cast": ids})
        t, k = t + shot, k + 1
    words = [{"w": w, "t": round(0.3 + i * 0.32, 2)} for i, w in enumerate(SAMPLE_WORDS.split()) if 0.3 + i * 0.32 < duration]
    return {"duration": duration, "captions": bool(spec.get("captions")), "cast": cast,
            "sets": {"place": {"prompt": "a place from the channel's own videos", "sky": spec.get("sky") or ["#8fc6ee", "#cfe6f7"],
                               "floor_colors": spec.get("floor") or ["#6a8a4a"]}},
            "shots": shots, "actions": [{"at": 0, "who": c, "move": "place", "x": (i - (len(ids) - 1) / 2) * 1.2, "z": 0}
                                        for i, c in enumerate(ids)],
            "fx": [], "words": words, "stills": [1.0, 3.0, 5.0]}


def _guide(dims: int, style: dict, lab: dict, home) -> str:
    import cr_animated2d as a2d
    if dims == 3:
        authoring, example = a2d._prompt3d("AUTHORING.md", home), a2d._prompt3d("example3d.js", home)
    else:
        authoring, example = a2d._prompt("AUTHORING.md", home), a2d._prompt(a2d.EXAMPLE_OWN, home)
    names = ", ".join(lab.get("names") or []) or "the channel's own name"
    return f"""# Style Lab: {style.get('label') or lab['style']} ({lab['style']})

You are working on this style's renderer: the code that draws every video made in this style
({"real 3D with three.js" if dims == 3 else "2D canvas"}, {lab['width']}x{lab['height']}). Change anything: the
characters, places, light, camera, moves, effects, captions. Studio supplies the rest of every video (script, voice,
word timings, the director's scene for each video, sound mixing and the final file).

## Files
- `renderer.js`: the renderer. Edit it.
- `scene.json`: the test scene a preview draws (at most 8 s; `stills` = the seconds the test shots are taken at;
  an optional `stress` scene, up to 30 s, is swept frame by frame for blank stretches). Write it in the renderer's own
  vocabulary, the way a video's director will, and change it to show what you are working on.
- `refs/shown_*.jpg`: the channel's own frames. Study them closely (proportions, faces, materials, light, lens, sets,
  captions). `refs/held_*.jpg` are held out: the judge scores them once to set this channel's bar.
- `preview/<version>/`: each preview's test shots (`shot_*.jpg`) and its 5 s video (`preview.mp4`).

## The loop
1. `style_lab` action `preview`: the contract checks (it runs, its frames are a pure function of time, its speed,
   its manifest, its sound names), the test shots, a 5 s video and the stress sweep. Free, on this computer.
2. Look at the shots next to `refs/shown_*.jpg`. Fix what differs most first. Preview again.
3. `style_lab` action `check`: the likeness judge scores the shots against the channel's frames (the bar here:
   {lab.get('bar') or 'set on the first check'}). Its notes say what still differs.
4. `style_lab` action `save`: the last previewed renderer becomes the style's next version. Every new video uses it.

Never draw {names} (its name, handle, watermark or logo), even where its frames show them.

---

{authoring}

---

## The kit's example renderer (the contract, never a look to copy)

```js
{example}
```
"""


def start(home, style: str, *, fresh: bool = False) -> dict:
    """The workspace for a style's renderer (free). An existing workspace is kept unless fresh=True."""
    import cr_animated2d as a2d
    data = _style(home, style)
    spec = _spec(data)
    lab_dir = folder(home, style)
    if (lab_dir / "lab.json").is_file() and not fresh:
        return status(home, style)
    dims = a2d.dims_of(spec)
    if not (a2d.available3d(home) if dims == 3 else a2d.available(home)):
        raise LabError("This Studio doesn't have the animation kit this style is drawn with. Update Studio.")
    if fresh and lab_dir.exists():
        aside = lab_dir.with_name(f"{style}.old-{int(time.time())}")
        lab_dir.rename(aside)
    lab_dir.mkdir(parents=True, exist_ok=True)
    op = _operation(data)
    op_dir = Path(home) / "output" / "cr-style-clone" / op if op else None
    # the renderer: the style's own (unchanged, checked by its hash), or the kit's example for a style without one
    saved = spec.get("renderer") if isinstance(spec.get("renderer"), dict) else {}
    code, version = "", 0
    if saved.get("file"):
        path = Path(home) / "assets" / str(saved["file"])
        if path.is_file() and _sha(path.read_text(encoding="utf-8")) == saved.get("sha256"):
            code, version = path.read_text(encoding="utf-8"), int(saved.get("version") or 0)
    if not code:
        code = a2d._prompt3d("example3d.js", home) if dims == 3 else a2d._prompt(a2d.EXAMPLE_OWN, home)
    (lab_dir / "renderer.js").write_text(code, encoding="utf-8")
    scene = _read(op_dir / "renderer" / "test-scene.json") if op_dir else {}
    _write(lab_dir / "scene.json", scene or _starter_scene(spec))
    # the channel's frames: the same ones the study built and judged against, without black bars
    shown, held, names, bar, control = [], [], [], None, None
    if op_dir and op_dir.is_dir():
        import cr_style_clone
        import cr_style_look
        ospec = _read(op_dir / "operation.json").get("spec") or {}
        if ospec.get("look_id"):
            looked = _read(cr_style_look.folder(home, ospec["look_id"]) / "look.json")
            refs = [Path(p) for p in cr_style_clone.renderer_frames(op_dir, ospec) if Path(p).is_file()]
            shown, held = a2d.split_refs(refs)
            names = cr_style_clone.renderer_names(ospec, looked)
        built = _read(op_dir / "renderer" / "build.json")
        bar, control = built.get("bar"), built.get("control")
    refs_dir = lab_dir / "refs"
    for kind, rows in (("shown", shown), ("held", held)):
        for k, p in enumerate(rows):
            _unbox(p, refs_dir / f"{kind}_{k}.jpg")
    lab = {"schema": SCHEMA, "style": style, "dims": dims, "width": int(spec.get("width") or 1920),
           "height": int(spec.get("height") or 1080), "captions": bool(spec.get("captions")), "operation": op,
           "names": names, "started_from": {"version": version, "sha256": _sha(code)},
           # the study's bar was set on boxed frames; the lab sets its own on the first check, on these frames
           "study_bar": bar, "study_control": control, "bar": None, "control": None, "history": []}
    _write(lab_dir / "lab.json", lab)
    (lab_dir / "GUIDE.md").write_text(_guide(dims, data, lab, home), encoding="utf-8")
    return status(home, style)


def _lab(home, style: str) -> tuple:
    lab_dir = folder(home, style)
    lab = _read(lab_dir / "lab.json")
    if not lab:
        raise LabError(f"Start a Style Lab for {style} first (style_lab action start).")
    return lab_dir, lab


def _last(lab: dict, kind: str, sha: str = None) -> dict:
    return next((h for h in reversed(lab.get("history") or []) if h.get("kind") == kind
                 and (sha is None or h.get("sha256") == sha)), {})


def status(home, style: str) -> dict:
    lab_dir, lab = _lab(home, style)
    code = (lab_dir / "renderer.js").read_text(encoding="utf-8") if (lab_dir / "renderer.js").is_file() else ""
    sha = _sha(code)
    spec = (( _style(home, style).get("look") or {}).get("animated2d") or {})
    saved = spec.get("renderer") if isinstance(spec.get("renderer"), dict) else {}
    preview, check = _last(lab, "preview", sha), _last(lab, "check", sha)
    out = {"style": style, "workspace": str(lab_dir), "guide": str(lab_dir / "GUIDE.md"),
           "renderer": str(lab_dir / "renderer.js"), "scene": str(lab_dir / "scene.json"),
           "refs": sorted(str(p) for p in (lab_dir / "refs").glob("*.jpg")),
           "kit": "3D (three.js)" if lab.get("dims") == 3 else "2D canvas", "size": f"{lab['width']}x{lab['height']}",
           "bar": lab.get("bar") or lab.get("study_bar"), "saved_version": saved.get("version"),
           "saved_state": saved.get("state"), "changed_since_saved": sha != saved.get("sha256"),
           "this_code": {"previewed": bool(preview), "preview_ok": bool(preview.get("ok")),
                         "checked": check.get("overall"), "passed": check.get("passed")}}
    if not preview:
        out["next"] = "Read GUIDE.md and the shown frames, edit renderer.js, then preview."
    elif not preview.get("ok"):
        out["next"] = "Fix the preview's problems, then preview again."
    elif not check:
        out["next"] = "Look at the test shots next to the channel's frames; check when they look close."
    else:
        out["next"] = "Save it, or keep improving what the check said still differs."
    return out


# ── preview: the contract checks, the shots, 5 seconds of video, the stress sweep ───────────────────────────────

def _diff(a: bytes, b: bytes) -> float:
    """The share (%) of pixels that differ visibly between two screenshots of the same frame: a pure renderer draws
    it identically (0), however small the part that would have changed."""
    import io
    from PIL import Image, ImageChops
    x, y = (Image.open(io.BytesIO(v)).convert("L") for v in (a, b))
    hist = ImageChops.difference(x, y).histogram()
    return round(100.0 * sum(hist[17:]) / max(1, sum(hist)), 3)


def _probe(spec: dict, code: str, home, times: list) -> dict:
    """In one sandbox: the sound marks it made, whether a frame is a pure function of t (t1, t2, then t1 again: the
    same picture) and how long a frame takes to draw and capture."""
    import cr_animated2d as a2d
    W, H = int(spec["width"]), int(spec["height"])
    box = a2d._Sandbox(a2d.page(spec, code, home), W, H, gl=a2d.dims_of(spec) == 3, home=home)
    try:
        err = box.error()
        if err:
            raise a2d.AnimationStopped("The renderer could not start: " + err[:600])
        clip = {"x": 0, "y": 0, "width": W, "height": H}
        marks = box.page.evaluate("window.__marks") or []

        def shot(t):
            box.draw(t)
            return box.page.screenshot(type="png", clip=clip)
        t1, t2 = times[0], times[-1]
        first, _, again = shot(t1), shot(t2), shot(t1)
        drift = _diff(first, again)
        ms = []
        for t in times:
            began = time.perf_counter()
            box.draw(t)
            box.page.screenshot(type="jpeg", quality=93, clip=clip)
            ms.append((time.perf_counter() - began) * 1000)
        return {"marks": marks, "drift": round(drift, 2), "frame_ms": round(statistics.median(ms))}
    finally:
        box.close()


def preview(home, style: str, *, seconds: float = PREVIEW_S) -> dict:
    """The renderer in the workspace, drawn and checked (free, on this computer): problems first, then its test shots
    and a short video. Recorded in lab.json against the code's hash."""
    import cr_animated2d as a2d
    lab_dir, lab = _lab(home, style)
    code = (lab_dir / "renderer.js").read_text(encoding="utf-8")
    scene = _read(lab_dir / "scene.json")
    sha = _sha(code)
    size = {"dims": lab["dims"], "width": lab["width"], "height": lab["height"]}
    spec = {**{k: v for k, v in (_spec(_style(home, style))).items() if k in ("sky", "floor", "blur")}, **size}
    if lab["dims"] != 3:
        spec.pop("dims")
    out = lab_dir / "preview" / sha[:10]
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    entry = {"kind": "preview", "at": round(time.time()), "sha256": sha, "ok": False, "problems": []}
    problems = a2d.check_code(code, lab.get("names") or [])
    if not scene:
        problems.append("scene.json is missing or not a JSON object")
    found, info = {}, {}
    if not problems:
        test = a2d._test_scene(scene, spec)
        test.setdefault("captions", lab.get("captions"))
        try:
            found = a2d.manifest(code, home, lab["dims"])
            if not (found.get("actions") or {}):
                problems.append("the manifest names no actions: the director could ask for nothing")
            stills = a2d.render_stills(test, out, code, home=home, prefix="shot_")
            entry["stills"] = [str(p) for p in stills]
            if stills and all(a2d._blank(p) for p in stills):
                problems.append("every test shot came out as one flat colour: nothing was drawn")
            info = _probe(test, code, home, [min(1.0, test["duration"] * 0.2), test["duration"] * 0.5,
                                            min(3.0, test["duration"] * 0.8)])
            if info["drift"] > 0.02:
                problems.append(f"a frame looked different when drawn again after a later frame ({info['drift']}% of "
                                "its pixels changed): frame(t) must be a pure function of t, setting everything "
                                "from t alone and keeping no state between frames (several pages draw frames in any order)")
            if info["frame_ms"] > FRAME_MS:
                problems.append(f"a frame takes {info['frame_ms']} ms to draw: keep it under {FRAME_MS} ms (build meshes, "
                                "materials and textures once in prepare, only move them in frame)")
            bad = sorted({str(m[1]) for m in info["marks"] if not a2d.sound_file(str(m[1]))})
            if bad:
                problems.append("unknown sound names " + ", ".join(bad[:8]) + ": use the names in the guide")
            clip = {**test, "duration": max(1.0, min(float(seconds), 8.0, test["duration"]))}
            marks = a2d.render(clip, out / "preview.mp4", code, workers=2, home=home, log=lambda m: None)
            entry["video"] = str(out / "preview.mp4")
            entry["sounds"] = [[t, Path(name).stem, db] for t, name, db in marks][:40]
            stress = a2d._stress_scene(scene, spec)
            runs = a2d.blank_runs(a2d.sweep(stress, code, home=home), stress["duration"])
            for a, b in runs[:3]:
                problems.append(f"in the stress scene the picture went blank from {a:g} s to {b:g} s (after "
                                f"{a2d._before(stress, a)}): every effect must end with the picture back")
        except a2d.AnimationStopped as error:
            problems.append(str(error))
        except Exception as error:                          # noqa: BLE001 - the browser itself failed
            problems.append(f"the preview could not be drawn ({type(error).__name__}: {str(error)[:300]})")
    entry.update(ok=not problems, problems=problems[:10], frame_ms=info.get("frame_ms"), pure=info.get("drift"),
                 manifest={k: found.get(k) for k in ("name", "version", "shots") if found.get(k) is not None}
                 | {"actions": sorted((found.get("actions") or {}))[:40]} if found else {})
    _record(lab_dir, lab, entry)
    entry["next"] = ("Fix these problems and preview again." if problems else
                     "Look at the shots (and the video) next to refs/shown_*.jpg; edit and preview again, or check.")
    return entry


def _record(lab_dir: Path, lab: dict, entry: dict):
    lab["history"] = (list(lab.get("history") or []) + [entry])[-HISTORY:]
    _write(lab_dir / "lab.json", lab)


# ── check: the study's likeness judge ───────────────────────────────────────────────────────────────────────────

def _judge(dims: int):
    import cr_bridge
    import cr_style_clone
    import cr_style_likeness
    context = cr_style_clone.RENDERER_CONTEXT_3D if dims == 3 else cr_style_clone.RENDERER_CONTEXT

    def judge(references, candidates):
        return cr_bridge.call("style/compare", {"references": [cr_style_likeness._b64(p) for p in references],
                                                "candidates": [cr_style_likeness._b64(p) for p in candidates],
                                                "context": context})
    return judge


def check(home, style: str, *, judge=None) -> dict:
    """The last preview's test shots against the channel's shown frames, by the study's judge, scored against this
    channel's bar (its held-out frames judged once on their own, set on the first check)."""
    import cr_animated2d as a2d
    lab_dir, lab = _lab(home, style)
    code = (lab_dir / "renderer.js").read_text(encoding="utf-8")
    sha = _sha(code)
    done = _last(lab, "preview", sha)
    if not done.get("ok"):
        raise LabError("Preview this renderer first (style_lab action preview); a check judges its test shots.")
    stills = [p for p in done.get("stills") or [] if Path(p).is_file()]
    shown = sorted((lab_dir / "refs").glob("shown_*.jpg"))
    held = sorted((lab_dir / "refs").glob("held_*.jpg"))
    if not stills or len(shown) < 2:
        raise LabError("This lab has no frames of the channel to judge against (its study left none).")
    judge = judge or _judge(lab["dims"])
    if lab.get("bar") is None:
        scores = []
        if held:
            try:
                got = judge([str(p) for p in shown], [str(p) for p in held])
                scores = [round(float((c if isinstance(c, dict) else {}).get("overall") or 0), 2)
                          for c in got.get("candidates") or []][:len(held)]
            except Exception:                               # noqa: BLE001 - the study's bar stands
                scores = []
        lab["control"] = max(scores) if [s for s in scores if s > 0] else lab.get("study_control")
        lab["bar"] = a2d.bar_for(lab["control"]) if lab["control"] else (lab.get("study_bar") or a2d.bar_for(None))
    got = judge([str(p) for p in shown], [str(p) for p in stills])
    mine = [c if isinstance(c, dict) else {} for c in got.get("candidates") or []][:len(stills)]
    if not mine:
        raise LabError("The judge gave no scores this time. Nothing was saved; check again in a minute.")
    overall = round(sum(float(c.get("overall") or 0) for c in mine) / len(mine), 2)
    characters = round(sum(float(c.get("characters") or 0) for c in mine) / len(mine), 2)
    entry = {"kind": "check", "at": round(time.time()), "sha256": sha, "overall": overall, "characters": characters,
             "bar": lab["bar"], "control": lab["control"], "passed": overall >= lab["bar"],
             "per_shot": [round(float(c.get("overall") or 0), 2) for c in mine],
             "dealbreakers": [str(x) for x in (got.get("dealbreakers") or [])][:6],
             "fixes": [str(x) for x in (got.get("fixes") or [])][:6]}
    _record(lab_dir, lab, entry)
    entry["next"] = ("It clears the bar: save it, or keep improving." if entry["passed"] else
                     "Under the bar: work on the dealbreakers first, preview, check again.")
    return entry


# ── save: the style's next renderer version ─────────────────────────────────────────────────────────────────────

def save(home, style: str) -> dict:
    """The workspace's renderer, previewed without problems, saved as the style's next version (renderer-vN.js) with
    its hash, manifest and last check: metadata only, the style's other settings untouched."""
    import cr_animated2d as a2d
    lab_dir, lab = _lab(home, style)
    code = (lab_dir / "renderer.js").read_text(encoding="utf-8")
    sha = _sha(code)
    done = _last(lab, "preview", sha)
    if not done.get("ok"):
        raise LabError("Preview this exact renderer without problems before saving it (style_lab action preview).")
    data = _style(home, style)
    spec = _spec(data)
    found = a2d.manifest(code, home, lab["dims"])
    checked = _last(lab, "check", sha)
    target = Path(home) / "assets" / "styles" / "learned" / style
    target.mkdir(parents=True, exist_ok=True)
    versions = sorted(target.glob("renderer-v*.js"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    same = next((p for p in versions if _sha(p.read_text(encoding="utf-8")) == sha), None)
    if same is None:
        number = (int(re.sub(r"\D", "", versions[-1].stem)) + 1) if versions else 1
        same = target / f"renderer-v{number}.js"
        same.write_text(code, encoding="utf-8")
    spec["renderer"] = {"file": same.relative_to(Path(home) / "assets").as_posix(), "sha256": sha,
                        "version": int(re.sub(r"\D", "", same.stem)), "api": a2d.KIT_API, "dims": lab["dims"],
                        "state": ("passed" if checked.get("passed") else "below_bar") if checked else "unchecked",
                        "overall": checked.get("overall"), "bar": checked.get("bar") or lab.get("bar"),
                        "control": checked.get("control") or lab.get("control"), "manifest": found,
                        "from": "style_lab"}
    data["cost"] = a2d.cost_note(data)
    data.setdefault("cr_style_clone", {})["style_lab"] = {"at": round(time.time()), "version": spec["renderer"]["version"],
                                                          "sha256": sha}
    op = _operation(data)
    saved_op = _read(Path(home) / "output" / "cr-style-clone" / op / "operation.json") if op else {}
    if saved_op.get("spec", {}).get("style_id") == style:
        import cr_style_clone
        cr_style_clone.republish(Path(home) / "output" / "cr-style-clone" / op, saved_op, data)
    else:
        _write(Path(home) / "styles" / f"{style}.json", data)
    _record(lab_dir, lab, {"kind": "save", "at": round(time.time()), "sha256": sha,
                           "version": spec["renderer"]["version"]})
    return {"style": style, "version": spec["renderer"]["version"], "file": str(same),
            "state": spec["renderer"]["state"], "overall": spec["renderer"]["overall"], "bar": spec["renderer"]["bar"],
            "cost": data["cost"], "next": "Every new video in this style uses this renderer. Make a short one to see it "
                                          "with the voice and sound."}


# ── routes (Studio's own, for the studio tools) ─────────────────────────────────────────────────────────────────

ACTIONS = {"start": start, "status": status, "preview": preview, "check": check, "save": save}


def run(home, action: str, style: str, **options) -> dict:
    if action not in ACTIONS:
        raise LabError("Choose start, status, preview, check or save.")
    if action == "start":
        return start(home, style, fresh=options.get("fresh") is True)
    if action == "preview":
        seconds = options.get("seconds")
        return preview(home, style, seconds=float(seconds) if isinstance(seconds, (int, float)) else PREVIEW_S)
    return ACTIONS[action](home, style)


def install(studio, token, home):
    """POST /cr-style-lab/<style>/<action> (start, preview, check, save) and GET /cr-style-lab/<style>; preview files
    (shots, video) at GET /cr-style-lab/<style>/file/<path>."""
    import secrets
    import threading
    from flask import jsonify, request, send_file
    app = studio.app
    locks = {}

    def local(mutation=False):
        if request.host not in {"127.0.0.1:7860", "localhost:7860"}:
            raise PermissionError("Open the studio on localhost.")
        if request.headers.get("Origin") not in {None, "http://127.0.0.1:7860", "http://localhost:7860"}:
            raise PermissionError("Open the studio on localhost.")
        if mutation and not secrets.compare_digest(request.headers.get("X-CR-Local-Token", ""), token):
            raise PermissionError("Refresh your studio page and try again.")

    def answer(fn):
        try:
            return jsonify(fn())
        except PermissionError as error:
            return jsonify(error=str(error)), 403
        except (LabError, ValueError) as error:
            return jsonify(error=str(error)), 400
        except RuntimeError as error:
            return jsonify(error=str(error)), 409

    @app.get("/cr-style-lab/<style>")
    def cr_style_lab_status(style):
        return answer(lambda: (local(), status(home, style))[1])

    @app.post("/cr-style-lab/<style>/<action>")
    def cr_style_lab_action(style, action):
        def go():
            local(True)
            body = request.get_json(silent=True) or {}
            lock = locks.setdefault(style, threading.Lock())
            if not lock.acquire(blocking=False):
                raise RuntimeError("This style's lab is already previewing or checking. Wait for it to finish.")
            try:
                result = run(home, action, style, **(body if isinstance(body, dict) else {}))
            finally:
                lock.release()
            if action == "save":
                studio.styles.load_all()
            return result
        return answer(go)

    @app.get("/cr-style-lab/<style>/file/<path:rel>")
    def cr_style_lab_file(style, rel):
        try:
            local()
            root = folder(home, style).resolve()
            path = (root / rel).resolve()
            if root not in path.parents or path.suffix.lower() not in (".jpg", ".mp4") or not path.is_file():
                return jsonify(error="No such preview file."), 404
            return send_file(path, conditional=False)
        except Exception as error:                          # noqa: BLE001
            return jsonify(error=str(error)[:200]), 400


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2 or argv[0] not in ACTIONS:
        print(__doc__.split("\n\n")[-1].strip())
        return 2
    sys.path.insert(0, str(HERE))
    home = Path(__import__("os").environ.get("CR_HOME") or HERE)
    try:
        result = run(home, argv[0], argv[1], fresh="--fresh" in argv,
                     **({"seconds": float(argv[argv.index("--seconds") + 1])} if "--seconds" in argv else {}))
    except (LabError, ValueError) as error:
        print(json.dumps({"error": str(error)}, indent=1))
        return 1
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
