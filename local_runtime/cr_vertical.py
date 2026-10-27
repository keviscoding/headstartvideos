"""Vertical Shorts from Studio's own engine: a style whose look.aspect is "9:16" and isn't written in code.

cr_video_cancel.video_size starts such a video's worker at 1080x1920 (VIDEO_W/VIDEO_H). The engine already draws its
pictures, moving pictures, footage and captions at that size, but parts of it were made for wide frames only: a
1920x1080 canvas cropped to its middle third, or words placed for a frame 1920 wide. Before this, a Shorts channel
cloned with Studio's ready-made pictures came out as a regular 16:9 video. In a tall frame:
- the pictures that fill the frame are drawn tall (cr_media.aspect);
- captions are sized and placed for a phone: bigger, short lines, above the Shorts title and buttons;
- a scene drawn wide (motion graphics, maps, headlines, 3D scenes, chapter cards) sits whole across the middle over a
  blurred copy of itself, never cropped to its middle;
- what is drawn only for wide frames is left out, said once in the log: subjects lifted off the pictures, punch cards,
  a learned look's footage devices and text effects, the footage treatments, and the name and place labels.
Every other video (VIDEO_H not over VIDEO_W) runs exactly as before.
"""
from __future__ import annotations

import functools
import inspect
import json
import os
import subprocess
import threading
from pathlib import Path

# Shorts: YouTube's title, channel name and buttons cover the bottom fifth and the right edge
SAFE_BOTTOM = 0.28          # captions sit at least this share of the height above the bottom edge
SIDE = 120                  # and keep this far from each side
GROW = 1.4                  # a caption sized for a wide frame, on a phone
SIZE_RANGE = (60, 92)
# how wide a character of the caption face is, as a share of its size (Inter ExtraBold, measured)
CHAR_W = {"upper": 0.68, "lower": 0.58}
VIDEO_SUFFIXES = (".mp4", ".mov", ".webm", ".mkv")

_locks: dict = {}
_locks_guard = threading.Lock()


def tall(engine) -> bool:
    try:
        return int(getattr(engine, "VIDEO_H", 1080)) > int(getattr(engine, "VIDEO_W", 1920))
    except (TypeError, ValueError):
        return False


def phone_captions(cap: dict, width: int, height: int, size: int, line_chars: int, margin_v: int) -> dict:
    """A channel's caption settings (look.captions) for a tall frame: its size grown for a phone, as many characters
    a line as fit between the sides, and above the Shorts title. size, line_chars and margin_v are the engine's own
    defaults for a caption that doesn't set them."""
    out = dict(cap or {})
    if out.get("off"):
        return out
    size = int(out.get("size") or size)
    out["size"] = max(size, max(SIZE_RANGE[0], min(SIZE_RANGE[1], round(size * GROW))))
    fit = max(10, int((width - 2 * SIDE) / (CHAR_W["upper" if out.get("upper") else "lower"] * out["size"])))
    out["line_chars"] = min(int(out.get("line_chars") or line_chars), fit)
    out["margin_v"] = max(int(out.get("margin_v") or margin_v), round(height * SAFE_BOTTOM))
    return out


def _size(src: Path) -> tuple:
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", str(src)], capture_output=True, text=True, timeout=60)
    w, h = (int(x) for x in probe.stdout.strip().split(",")[:2])
    return w, h


def fit(src: Path, width: int, height: int) -> Path:
    """A wide scene for a tall frame: the whole scene across the middle, over a darkened, blurred copy of itself
    filling the frame (made once, beside the source). A source as tall as the frame, or unreadable, is used as is."""
    src = Path(src)
    try:
        w, h = _size(src)
    except (OSError, ValueError, subprocess.SubprocessError):
        return src
    if w * height <= h * width:
        return src
    out = src.with_name(f"{src.stem}.tall{width}x{height}.mp4")
    with _locks_guard:
        lock = _locks.setdefault(str(out), threading.Lock())
    with lock:                                  # two slots may show the same scene: it is fitted once
        if out.exists() and out.stat().st_size > 1000 and out.stat().st_mtime >= src.stat().st_mtime:
            return out
        part = out.with_name(out.stem + ".part.mp4")
        graph = (f"[0:v]split=2[a][b];[a]scale={width}:{height}:force_original_aspect_ratio=increase,"
                 f"crop={width}:{height},gblur=sigma=36,eq=brightness=-0.12[bg];"
                 f"[b]scale={width}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[v]")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex", graph, "-map", "[v]",
                        "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "copy",
                        str(part)], check=True, capture_output=True, timeout=900)
        os.replace(part, out)
    return out


def install(engine):
    if getattr(engine, "_cr_vertical", False):
        return
    engine._cr_vertical = True
    told = set()

    def say(what):
        if what not in told:
            told.add(what)
            getattr(engine, "log", print)(f"vertical Short: {what}")

    def wrap(name, replacement):
        original = getattr(engine, name, None)
        if callable(original):
            setattr(engine, name, functools.wraps(original)(replacement(original)))

    def arguments(original, args, kwargs) -> dict:
        try:
            bound = inspect.signature(original).bind(*args, **kwargs)
        except (TypeError, ValueError):
            return {}
        bound.apply_defaults()
        return bound.arguments

    def burn(original):
        def build(*args, **kwargs):
            got = arguments(original, args, kwargs)
            table = getattr(engine, "STYLE_CAPTIONS", None)
            if not tall(engine) or not got or got.get("center") or not isinstance(table, dict):
                return original(*args, **kwargs)
            style = got.get("style") or ""
            had, cap = style in table, table.get(style) or {}
            kit = str(cap.get("mode") or "").lower() not in ("", "scripture")      # the kit modes' own defaults
            table[style] = phone_captions(cap, int(engine.VIDEO_W), int(engine.VIDEO_H),
                                          56 if kit else int(getattr(engine, "SUB_SIZE_PX", 73)),
                                          int(getattr(engine, "SUB_LINE_CHARS", 44)), 120 if kit else 96)
            try:
                return original(*args, **kwargs)
            finally:
                if had:
                    table[style] = cap
                else:
                    table.pop(style, None)
        return build
    wrap("_build_burn_ass", burn)

    def fitted(src):
        out = fit(Path(src), int(engine.VIDEO_W), int(engine.VIDEO_H))
        if out != Path(src):
            say("graphics and scenes drawn wide sit whole across the middle of the frame")
        return out

    def scene(original):
        # drift=False is the engine's own mark for a scene (it has its own camera move); footage keeps filling the frame
        def render(src, *args, **kwargs):
            if tall(engine) and arguments(original, (src,) + args, kwargs).get("drift") is False:
                src = fitted(src)
            return original(src, *args, **kwargs)
        return render
    wrap("_render_video_segment", scene)

    def held(original):
        def render(src, *args, **kwargs):
            return original(fitted(src) if tall(engine) else src, *args, **kwargs)
        return render
    wrap("_render_held_scene", held)

    def depth(original):
        def slots(*args, **kwargs):
            if tall(engine):
                if "depth" not in (getattr(engine, "_DLC_OFF", None) or ()):
                    say("subjects lifted off the pictures are left out (drawn for wide frames)")
                return None, set()
            return original(*args, **kwargs)
        return slots
    wrap("_depth_dlc", depth)

    def punch(original):
        # a punch card sets its words on a 1920x1080 page: in a tall frame the slot shows its picture or clip
        def render(src, dur, out, *args, **kwargs):
            if not tall(engine):
                return original(src, dur, out, *args, **kwargs)
            say("punch cards are left out (drawn for wide frames); their words stay in the captions")
            if Path(str(src)).suffix.lower() in VIDEO_SUFFIXES:
                return engine._render_video_segment(Path(src), float(dur), out)
            return engine._render_photo_segment(Path(src), float(dur), out)
        return render
    for name in ("_render_cardtext_segment", "_render_slit_segment", "_render_highlight_segment"):
        wrap(name, punch)

    def v2(original):
        # a learned look's footage devices (devices.py) and text effects (textfx.py) are laid out on 1920x1080
        def look(*args, **kwargs):
            got = original(*args, **kwargs)
            if tall(engine) and isinstance(got, dict) and (got.get("fx") or got.get("textfx")):
                say("the look's footage devices and text effects are left out (drawn for wide frames)")
                return {k: v for k, v in got.items() if k not in ("fx", "textfx")}
            return got
        return look
    wrap("_v2_look", v2)

    def labels(original):
        def ass(job, *args, **kwargs):
            if not tall(engine):
                return original(job, *args, **kwargs)
            try:
                planned = json.loads((Path(job) / "overlays.json").read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError):
                planned = []
            if planned:
                say("name and place labels are left out (placed for wide frames)")
            return [], []
        return ass
    wrap("_overlay_ass_labels", labels)

    try:
        import cinema                          # its footage treatments (frames, old TV, pops) draw at 1920x1080
    except Exception:                          # noqa: BLE001 - a Studio without it has nothing to treat
        return
    treat = getattr(cinema, "_treat", None)
    if callable(treat) and not getattr(treat, "_cr_vertical", False):
        def plain(mv, plan, segs, *args, **kwargs):
            if tall(mv):
                say("the footage treatments are left out (drawn for wide frames)")
                return segs
            return treat(mv, plan, segs, *args, **kwargs)
        plain = functools.wraps(treat)(plain)
        plain._cr_vertical = True
        cinema._treat = plain
