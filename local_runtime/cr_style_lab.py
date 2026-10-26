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
- `plates/<set id>.jpg` (optional): a picture for a place in scene.json. A renderer with `plates: {{draw: true}}` composites
  its characters over it, as videos do over the picture Studio draws for each place (about $0.02 each). Without one
  the preview shows your stand-in and the check judges the characters, not the background.

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
           "study_bar": bar, "study_control": control, "bar": None, "control": None, "history": [],
           **({"sounds": spec["sounds"]} if isinstance(spec.get("sounds"), dict) else _heard(home, op_dir))}
    _write(lab_dir / "lab.json", lab)
    (lab_dir / "GUIDE.md").write_text(_guide(dims, data, lab, home), encoding="utf-8")
    return status(home, style)


def _heard(home, op_dir) -> dict:
    """{"sounds": ...}: what the study's look heard the channel play, for a style saved before its spec kept it."""
    if not op_dir or not Path(op_dir).is_dir():
        return {}
    import cr_animated2d as a2d
    import cr_style_look
    ospec = _read(Path(op_dir) / "operation.json").get("spec") or {}
    if not ospec.get("look_id"):
        return {}
    return a2d._sounds_of(_read(cr_style_look.folder(home, ospec["look_id"]) / "look.json").get("profile") or {})


def _mix(video: Path, marks: list, duration: float, home=None) -> bool:
    """The preview's own sounds and the kit's recordings, mixed at their marks into its video (Studio's shared cues
    are mixed only in videos). False when there was nothing to mix."""
    import subprocess
    root = Path(home or HERE) / "assets"
    rows = []
    for t, name, db in marks[:40]:
        if not str(name).startswith("file:"):
            continue
        path = Path(name[5:]) if Path(name[5:]).is_absolute() else root / name[5:]
        if path.is_file():
            rows.append((float(t), path, float(db)))
    if not rows:
        return False
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(video)]
    parts = []
    for i, (t, path, db) in enumerate(rows):
        cmd += ["-i", str(path)]
        ms = int(max(0.0, t) * 1000)
        parts.append(f"[{i + 1}:a]adelay={ms}|{ms},volume={db - 6:.1f}dB[a{i}]")
    graph = ";".join(parts) + ";" + "".join(f"[a{i}]" for i in range(len(rows))) + \
        f"amix=inputs={len(rows)}:normalize=0,apad,atrim=0:{duration:.3f}[mix]"
    tmp = video.with_name(video.stem + ".mixed.mp4")
    subprocess.run(cmd + ["-filter_complex", graph, "-map", "0:v", "-map", "[mix]", "-c:v", "copy", "-c:a", "aac",
                          "-shortest", str(tmp)], check=True, capture_output=True)
    tmp.replace(video)
    return True


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
    if not out["changed_since_saved"] and saved.get("from") == "style_lab":
        out["next"] = (f"This is the style's saved renderer (version {saved.get('version')}). Edit and preview to improve "
                       "it, or make a video in the style to see it with its voice and sound.")
    elif not preview:
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


def _plates(lab_dir: Path, scene: dict) -> dict:
    """{set id: picture}: the workspace's own pictures for its places (plates/<set id>.jpg or .png), put on the test
    scene so a renderer that composites over a picture per place is previewed and checked over real backdrops."""
    found = {}
    for ident, row in (scene.get("sets") or {}).items():
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            path = lab_dir / "plates" / f"{ident}{ext}"
            if isinstance(row, dict) and path.is_file() and path.stat().st_size < 12_000_000:
                row["image"] = str(path)
                found[ident] = str(path)
                break
    return found


def preview(home, style: str, *, seconds: float = PREVIEW_S) -> dict:
    """The renderer in the workspace, drawn and checked (free, on this computer): problems first, then its test shots
    and a short video. Recorded in lab.json against the code's hash."""
    lab_dir, lab = _lab(home, style)
    code = (lab_dir / "renderer.js").read_text(encoding="utf-8")
    entry = _draw(home, style, lab_dir, lab, code, _read(lab_dir / "scene.json"), lab_dir / "preview" / _sha(code)[:10],
                  seconds)
    _record(lab_dir, lab, entry)
    entry["next"] = ("Fix these problems and preview again." if entry["problems"] else
                     "Look at the shots (and the video) next to refs/shown_*.jpg; edit and preview again, or check."
                     + (" The warnings don't stop a save: fix them if the channel needs it." if entry.get("warnings")
                        else ""))
    return entry


def _draw(home, style: str, lab_dir: Path, lab: dict, code: str, scene: dict, out: Path, seconds: float) -> dict:
    """A renderer drawn and checked into out (its test shots, a short video with its sound, the contract, text and
    sound checks): the preview entry, not recorded. Used by Style Lab's preview and by "Improve this style"."""
    import cr_animated2d as a2d
    sha = _sha(code)
    size = {"dims": lab["dims"], "width": lab["width"], "height": lab["height"]}
    spec = {**{k: v for k, v in (_spec(_style(home, style))).items() if k in ("sky", "floor", "blur")}, **size}
    if lab["dims"] != 3:
        spec.pop("dims")
    out = Path(out)
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    entry = {"kind": "preview", "at": round(time.time()), "sha256": sha, "ok": False, "problems": []}
    problems = a2d.check_code(code, lab.get("names") or [])
    warnings = []                       # said, never a reason it can't be saved: the creator and Claude decide
    if not scene:
        problems.append("scene.json is missing or not a JSON object")
    found, info = {}, {}
    if not problems:
        test = a2d._test_scene(scene, spec)
        test.setdefault("captions", lab.get("captions"))
        shown_plates = _plates(lab_dir, test)
        entry["plates"] = sorted(shown_plates)
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
                warnings.append(f"a frame takes {info['frame_ms']} ms to draw: keep it under {FRAME_MS} ms (build meshes, "
                                "materials and textures once in prepare, only move them in frame)")
            own, made = a2d.make_sounds(code, out / "sounds", home=home, dims=lab["dims"])
            problems += made
            clip = {**test, "duration": max(1.0, min(float(seconds), 8.0, test["duration"]))}
            marks = a2d.render(clip, out / "preview.mp4", code, workers=2, home=home, log=lambda m: None,
                               own={k: "file:" + str(v) for k, v in own.items()})
            entry["video"] = str(out / "preview.mp4")
            entry["sounds"] = [[t, Path(name).stem, db] for t, name, db in marks][:40]
            entry["own_sounds"] = sorted(own)
            entry["heard"] = _mix(out / "preview.mp4", marks, clip["duration"], home)
            stress, words, played = a2d._stress_scene(scene, spec), {}, []
            runs = a2d.blank_runs(a2d.sweep(stress, code, home=home, text=words, marks=played), stress["duration"])
            unknown = sorted({str(m[1]) for m in played} - set(own) - {str(m[1]) for m in played if a2d.sound_file(str(m[1]))})
            if unknown:
                problems.append("it plays sounds that are neither its own (sounds: {...}) nor Studio's: " + ", ".join(unknown[:8]))
            warnings += a2d.generic_sounds({str(m[1]) for m in played} - set(own), {"sounds": lab.get("sounds")})
            entry["smallest_text"] = words or None
            if a2d.small_text(words, stress):
                warnings.append(a2d.small_text(words, stress))
            for a, b in runs[:3]:
                problems.append(f"in the stress scene the picture went blank from {a:g} s to {b:g} s (after "
                                f"{a2d._before(stress, a)}): every effect must end with the picture back")
        except a2d.AnimationStopped as error:
            problems.append(str(error))
        except Exception as error:                          # noqa: BLE001 - the browser itself failed
            problems.append(f"the preview could not be drawn ({type(error).__name__}: {str(error)[:300]})")
    entry.update(ok=not problems, problems=problems[:10], warnings=warnings[:10], frame_ms=info.get("frame_ms"),
                 pure=info.get("drift"),
                 manifest={k: found.get(k) for k in ("name", "version", "shots") if found.get(k) is not None}
                 | {"actions": sorted((found.get("actions") or {}))[:40]} if found else {})
    return entry


def _lab_lock(lab_dir: Path):
    """lab.json is written by Style Lab in Claude Code and by Improve this style in a worker: one at a time."""
    import contextlib
    import cr_style_clone

    @contextlib.contextmanager
    def held():
        for _ in range(100):
            try:
                with cr_style_clone._lock(lab_dir, ".lab.lock"):
                    yield
                    return
            except RuntimeError:
                time.sleep(0.1)
        raise LabError("This style's lab is busy writing. Try again in a moment.")
    return held()


def _record(lab_dir: Path, lab: dict, entry: dict):
    """One entry added to lab.json as it is on disk now (another writer's entries are kept), and the bar once set."""
    with _lab_lock(lab_dir):
        now = _read(lab_dir / "lab.json") or dict(lab)
        for key in ("bar", "control"):
            if now.get(key) is None and lab.get(key) is not None:
                now[key] = lab[key]
        now["history"] = (list(now.get("history") or []) + [entry])[-HISTORY:]
        _write(lab_dir / "lab.json", now)
    lab.clear()
    lab.update(now)


# ── check: the study's likeness judge ───────────────────────────────────────────────────────────────────────────

def _judge(dims: int):
    import cr_bridge
    import cr_style_clone
    import cr_style_likeness

    def judge(references, candidates, plates=False):
        context = (cr_style_clone.RENDERER_CONTEXT_PLATES if plates else
                   cr_style_clone.RENDERER_CONTEXT_3D if dims == 3 else cr_style_clone.RENDERER_CONTEXT)
        return cr_bridge.call("style/compare", {"references": [cr_style_likeness._b64(p) for p in references],
                                                "candidates": [cr_style_likeness._b64(p) for p in candidates],
                                                "context": context})
    return judge


def check(home, style: str, *, judge=None) -> dict:
    """The last preview's test shots against the channel's shown frames, by the study's judge, scored against this
    channel's bar (its held-out frames judged once on their own, set on the first check)."""
    lab_dir, lab = _lab(home, style)
    code = (lab_dir / "renderer.js").read_text(encoding="utf-8")
    done = _last(lab, "preview", _sha(code))
    if not done.get("ok"):
        raise LabError("Preview this renderer first (style_lab action preview); a check judges its test shots.")
    entry = _judge_preview(lab_dir, lab, code, done, judge)
    _record(lab_dir, lab, entry)
    entry["next"] = ("It clears the bar: save it, or keep improving." if entry["passed"] else
                     "Under the bar: work on the dealbreakers first, preview, check again.")
    return entry


def _judge_preview(lab_dir: Path, lab: dict, code: str, done: dict, judge=None) -> dict:
    """A preview's test shots judged against the channel's shown frames and its bar (set once, from the held-out
    frames, kept in lab): the check entry, not recorded."""
    import cr_animated2d as a2d
    sha = _sha(code)
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
    stand_in = a2d.uses_plates(code) and not done.get("plates")     # composited over pictures it wasn't shown
    got = judge([str(p) for p in shown], [str(p) for p in stills], **({"plates": True} if stand_in else {}))
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


# ── "Improve this style" from the Studio dashboard: one change in the creator's words ───────────────────────────────

IMPROVE_PROMPT = """You are improving a YouTube channel's style in ChannelRecipe Studio. Its renderer is the code that
draws every video in this style; the creator wants one change and will compare your version with the current one
before keeping it.

[ASKED]

THE GUIDE (the renderer's contract, sandbox and helpers):
[GUIDE]

[HEADER]
```javascript
[CODE]
```

ITS TEST SCENE:
```json
[SCENE]
```

[IMAGES]
[FIX]
[TASK] Keep the contract:
Kit.renderer({...}) with the manifest's actions, shots, fx and cast traits (add new ones if the change needs them, never
remove one: the style's videos already use them). Every frame stays a pure function of t; every word on screen stays
readable at video size; sounds stay the channel's own.

Reply with exactly two fenced blocks and nothing else:
```javascript
// the whole renderer: Kit.renderer({...})
```
```json
{"duration": 6, "cast": {}, "sets": {}, "shots": [], "actions": [], "fx": [], "stills": [1.2, 3.0, 5.1]}
```
(the test scene, changed to show what you changed)"""


def _improve_prompt(lab_dir: Path, request: str, code: str, scene: dict, images: str, fix: str = "") -> str:
    """The change's prompt. With fix (the problems of Claude's own version): that version is the code shown, the
    change is already in it, and only the problems are to be fixed (never the change made twice)."""
    guide = (lab_dir / "GUIDE.md").read_text(encoding="utf-8") if (lab_dir / "GUIDE.md").is_file() else ""
    if fix:
        asked = "THE CREATOR ASKED (your version below already makes this change): " + request
        header = ("YOUR VERSION (it has the creator's change, but couldn't be used yet: fix only the problems below, "
                  "keep the change and everything else):")
        task = "Fix only these problems, keeping the creator's change exactly as you made it."
    else:
        asked = "THE CREATOR ASKS: " + request
        header = "THE CURRENT RENDERER (it works: keep everything the creator didn't ask to change):"
        task = "Make the change the creator asked for, as fully as the code allows, in the channel's own look."
    return (IMPROVE_PROMPT.replace("[GUIDE]", guide[:60000]).replace("[CODE]", code)
            .replace("[SCENE]", json.dumps(scene, ensure_ascii=False, indent=1)[:20000])
            .replace("[IMAGES]", images).replace("[FIX]", fix).replace("[HEADER]", header).replace("[TASK]", task)
            .replace("[ASKED]", asked))


def improvement_folder(home, style: str, operation_id: str) -> Path:
    if not re.fullmatch(r"op_[a-f0-9]{24}", str(operation_id or "")):
        raise LabError("Choose a saved improvement.")
    return folder(home, style) / "improve" / operation_id


def candidate(home, style: str, operation_id: str) -> dict:
    """The improvement's candidate as saved (candidate.json), {} before it exists."""
    return _read(improvement_folder(home, style, operation_id) / "candidate.json")


FIX_CALL_MIN_S = 300                     # a fix call starts only with this long left (and 1.25x the first call's time)


def run_improvement(home, style: str, operation_id: str, request: str, see, *, judge=None, log=print,
                    left=None) -> dict:
    """One change in the creator's words: Claude rewrites the style's saved renderer (see(prompt, images), one vision
    call, and one more to fix what the preview finds), then the candidate is drawn and checked like any Style Lab
    preview and judged against the channel. Held aside in style-lab/<style>/improve/<operation>/ until the creator
    saves it or keeps the old one; the style itself is never changed here."""
    import cr_animated2d as a2d
    if not (folder(home, style) / "lab.json").is_file():
        start(home, style)
    lab_dir, lab = _lab(home, style)
    record = (_spec(_style(home, style)).get("renderer") or {})
    path = Path(home) / "assets" / str(record.get("file") or "")
    if not record.get("file") or not path.is_file() or _sha(path.read_text(encoding="utf-8")) != record.get("sha256"):
        raise LabError("This style's saved renderer is missing or changed on disk. Nothing was changed.")
    code = path.read_text(encoding="utf-8")
    scene = _read(lab_dir / "scene.json") or _starter_scene(_spec(_style(home, style)))
    out = improvement_folder(home, style, operation_id)
    out.mkdir(parents=True, exist_ok=True)
    current = _last(lab, "preview", record["sha256"])
    if not current.get("ok") or not all(Path(p).is_file() for p in current.get("stills") or [None]):
        log("  improve: drawing the current version for comparison")
        current = _draw(home, style, lab_dir, lab, code, scene, lab_dir / "preview" / record["sha256"][:10], PREVIEW_S)
        _record(lab_dir, lab, current)
    shown = sorted((lab_dir / "refs").glob("shown_*.jpg"))[:6]
    stills = [p for p in current.get("stills") or [] if Path(p).is_file()][:4]
    words = (f"The first {len(shown)} pictures are the channel's own frames" if shown else "")
    words += (("; " if words else "The pictures are ") + f"the last {len(stills)} are the current renderer's test shots."
              if stills else ".")
    images = [str(p) for p in shown] + [str(p) for p in stills]
    entry, new, new_scene, fix, short, took = {}, "", scene, "", "", 0.0
    for attempt in range(2):
        if attempt and left is not None and left() < max(FIX_CALL_MIN_S, 1.25 * took):
            short = "there wasn't time left in this change's budget for a second try"
            log("  improve: no time for a fix call; stopping with what Claude wrote")
            break
        log(f"  improve: Claude writes the change (call {attempt + 1})")
        began = time.monotonic()
        text = see(_improve_prompt(lab_dir, request, new or code, new_scene, words, fix), images)
        took = time.monotonic() - began
        got, got_scene = a2d._blocks(text or "")
        if not got:
            fix = "\nYOUR LAST ANSWER HAD NO RENDERER: reply with the two fenced blocks.\n"
            continue
        new, new_scene = got, (got_scene or new_scene)
        entry = _draw(home, style, lab_dir, lab, new, new_scene, out / "preview", PREVIEW_S)
        if entry.get("ok"):
            break
        fix = ("\nYOUR VERSION COULDN'T BE USED YET. Fix these and keep the change:\n- "
               + "\n- ".join(entry.get("problems") or ["it drew nothing"]) + "\n")
    result = {"schema": 1, "request": request, "operation_id": operation_id, "at": round(time.time()),
              "current": {"sha256": record["sha256"], "version": record.get("version"), "stills": stills,
                          "video": current.get("video"), "overall": record.get("overall"), "bar": record.get("bar")},
              # background pictures cost about $0.02 each on the creator's Kie account: said before Save when the new
              # version draws them and the current one doesn't
              "plates": {"current": a2d.uses_plates(code), "new": bool(new) and a2d.uses_plates(new)}}
    if new:                                                 # Claude's code is kept, used or not (Style Lab can open it)
        (out / "renderer.js").write_text(new, encoding="utf-8")
        _write(out / "scene.json", new_scene)
    if not new or not entry.get("ok"):
        result.update(state="failed", problems=((entry.get("problems") or ["Claude wrote no renderer"])[:5]
                                                + ([short] if short else [])), code_kept=bool(new))
        _write(out / "candidate.json", result)
        import cr_style_clone
        raise cr_style_clone.StudyStopped("Claude's change couldn't be drawn yet ("
                                          + str(result["problems"][0])[:300] + ")" + (f", and {short}" if short else "")
                                          + ". Your style is unchanged"
                                          + ("; Claude's code is kept in Style Lab." if new else "."))
    _record(lab_dir, lab, entry)                            # Style Lab knows this exact code was previewed
    verdict = {}
    try:
        verdict = _judge_preview(lab_dir, lab, new, entry, judge)
        _record(lab_dir, lab, verdict)
    except Exception as error:                              # noqa: BLE001 - the preview still shows
        log(f"  improve: the likeness check could not run ({str(error)[:120]})")
    result.update(preview=entry, checked=verdict or None)   # recorded again at Save if lab.json lost them
    result.update(state="ready", sha256=_sha(new), stills=entry.get("stills") or [], video=entry.get("video"),
                  smallest_text=entry.get("smallest_text"), own_sounds=entry.get("own_sounds") or [],
                  warnings=entry.get("warnings") or [],
                  check={k: verdict.get(k) for k in ("overall", "bar", "passed", "dealbreakers")} if verdict else None)
    _write(out / "candidate.json", result)
    return result


def keep_candidate(home, style: str, operation_id: str, *, use_new: bool) -> dict:
    """The creator's choice: save the candidate as the style's next renderer version (Style Lab's save: metadata
    only) or keep the old one."""
    out = improvement_folder(home, style, operation_id)
    cand = _read(out / "candidate.json")
    if cand.get("state") not in ({"ready", "kept_old"} if use_new else {"ready"}):    # kept the old one: may still save
        raise LabError("This change has no ready version to keep: " + str(cand.get("state") or "not drawn yet") + ".")
    if use_new:
        lab_dir, lab = _lab(home, style)
        code = (out / "renderer.js").read_text(encoding="utf-8") if (out / "renderer.js").is_file() else ""
        if _sha(code) != cand.get("sha256"):
            raise LabError("The new version changed since it was drawn. Nothing was saved.")
        now = _spec(_style(home, style)).get("renderer") or {}
        if now.get("sha256") != (cand.get("current") or {}).get("sha256"):
            # a newer version was saved since this change was made from an older one: saving it would undo that one
            raise LabError(f"This change was made to version {(cand.get('current') or {}).get('version')}, and the "
                           f"style has had version {now.get('version')} saved since. Nothing was saved; ask for the "
                           "change again on the version it has now.")
        if not _last(lab, "preview", cand["sha256"]).get("ok") and (cand.get("preview") or {}).get("ok"):
            _record(lab_dir, lab, {**cand["preview"], "at": round(time.time())})     # pushed out of lab.json
            if cand.get("checked"):
                _record(lab_dir, lab, {**cand["checked"], "at": round(time.time())})
        for name in ("renderer.js", "scene.json"):           # Claude Code's own work in the workspace, kept
            mine = lab_dir / name
            if mine.is_file() and (out / name).is_file() and mine.read_bytes() != (out / name).read_bytes():
                shutil.copyfile(mine, lab_dir / f"{mine.stem}.before-{operation_id}{mine.suffix}")
        mine = lab_dir / "renderer.js"
        mine.write_text(code, encoding="utf-8")
        if (out / "scene.json").is_file():
            shutil.copyfile(out / "scene.json", lab_dir / "scene.json")
        saved = save(home, style)
        cand.update(state="saved", version=saved["version"], saved_at=round(time.time()))
    else:
        cand.update(state="kept_old", decided_at=round(time.time()))
    _write(out / "candidate.json", cand)
    return cand


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
