"""Deliver an approved learned kit over the entire film, without a base-media mix.

The original visuals-plugin seam keeps script, voice, metadata and publication
in Frontier. Only the reference-only visual plan and assembly are supplied here.
"""
import hashlib
import importlib
import json
import math
from pathlib import Path
import sys
import cr_claude

MODE = "reference_only"
MAX_BATCH = 18
PROMPT_FILE = Path(__file__).with_name("cr-style-delivery-prompt.txt")


def selected(style):
    return isinstance(style, dict) and (style.get("cr_style_clone") or {}).get("visual_mode") == MODE


def configure_style(style):
    """Preserve the learned grammar, narration and accounts; remove mixed media."""
    if not selected(style):
        return style
    old = style.get("features") or {}
    on = ["motion"] + (["sound"] if "sound" in (old.get("on") or []) else [])
    style["features"] = {"offered": ["motion", "sound"], "on": on}
    style["auto_style"] = False
    style.pop("variant_of", None)
    look = style.setdefault("look", {})
    look.pop("avatar", None)
    look.pop("threed", None)
    look["visuals"] = "cr_style_delivery"
    pace = style.setdefault("pacing", {})
    pace.update(mix=[0, 0, 100], scene_share_body=1.0, scene_share_hook=1.0)
    return style


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _prompt():
    if not PROMPT_FILE.is_file():
        raise RuntimeError("The private reference animation workflow is missing. Update this studio before starting.")
    template = PROMPT_FILE.read_text(encoding="utf-8")
    if any("[" + name + "]" not in template for name in
           ("KIT_CONTRACT", "CREATOR_INSTRUCTIONS", "PREVIOUS_SCENES", "TIMED_BEATS")):
        raise RuntimeError("The private reference animation workflow is incomplete. Update this studio before starting.")
    return template


def _kit(engine, style, kits):
    info = engine.STYLE_INFO.get(style) or {}
    if not selected(info):
        raise RuntimeError("This preset is not a reference-only animation preset.")
    name = kits.kit_for_style(engine, style)
    entry = kits.KITS.get(name) or {}
    source = Path(str(entry.get("js_path") or ""))
    if not name or not source.is_file() or not entry.get("types") or not entry.get("prompt"):
        raise RuntimeError("The studied animation kit is missing or incomplete. No other visual style was substituted.")
    grammar = {k: v for k, v in entry.items() if k != "js_path"}
    return name, entry, _digest({"grammar": grammar, "source": source.read_text(encoding="utf-8")})


def beats(entries, total, cut_s):
    """Contiguous narration windows; the clock comes from the actual recording."""
    if not math.isfinite(total) or total <= 0:
        raise RuntimeError("The narration has no usable duration.")
    cut_s = max(2.0, min(12.0, float(cut_s)))
    count = max(1, int(math.ceil(total / cut_s)))
    # Equal final windows avoid a final fraction-of-a-second flash.
    bounds = [round(total * i / count, 6) for i in range(count + 1)]
    result = []
    for i in range(count):
        start, end = bounds[i], bounds[i + 1]
        text = " ".join(str(row[2]) for row in entries if len(row) >= 3
                        and float(row[0]) < end and float(row[1]) > start).strip()
        result.append({"beat": i + 1, "start": start, "end": end, "narration": text})
    if not any(row["narration"] for row in result):
        raise RuntimeError("The narration timing is empty. No generic pictures were substituted.")
    return result


def _parse_response(text):
    text = str(text).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else text
    try:
        value = json.loads(text)
    except ValueError:
        raise RuntimeError("Claude's reference animation plan is incomplete. Its response is saved; no retry or visual fallback was submitted.") from None
    if not isinstance(value, list):
        raise RuntimeError("Claude's reference animation plan must contain a scene for every narration beat.")
    return value


def _validate(raw, windows, kit, kits):
    if len(raw) != len(windows):
        raise RuntimeError("The reference animation plan leaves narration uncovered. No mixed-media fallback was used.")
    scenes = []
    for value, window in zip(raw, windows):
        if not isinstance(value, dict) or value.get("beat") != window["beat"]:
            raise RuntimeError("The reference animation plan has a missing or reordered narration beat.")
        scene = kits._normalise(kit, value)
        if not scene or scene.get("type") == "none":
            raise RuntimeError("The studied kit cannot draw a requested narration beat. No alternate style was substituted.")
        # These media fields may only be produced by the user's learned JS;
        # external photographs, movie frames and generated pictures are excluded.
        forbidden = {"ground", "frame", "photo", "image", "image_url", "video", "url", "alpha"}
        if forbidden.intersection(scene):
            raise RuntimeError("The reference-only plan requested external media instead of the learned animation.")
        for key in ("beat", "start", "end", "narration"):
            scene.pop(key, None)
        scene.update(duration=round(window["end"] - window["start"], 6))
        scenes.append(scene)
    return scenes


def plan(engine, job, srt, style, total, *, kits=None):
    kits = kits or importlib.import_module("kits")
    name, entry, kit_hash = _kit(engine, style, kits)
    job = Path(job)
    folder = job / ".cr-reference2d"
    folder.mkdir(exist_ok=True)
    info = engine.STYLE_INFO[style]
    template = _prompt()
    cut = float((info.get("pacing") or {}).get("cut_s") or 5.0)
    entries = engine._parse_srt_full(Path(srt).read_text(encoding="utf-8"))
    windows = beats(entries, total, cut)
    key = _digest({"kit": kit_hash, "style": info, "clock": windows, "workflow": _digest(template),
                   "brief": engine._extra_block(), "schema": 1})
    request = folder / "request.json"
    if request.is_file() and _read(request).get("key") != key:
        raise RuntimeError("This saved animation work belongs to different inputs. Create a new attempt for changed inputs.")
    if not request.is_file():
        _write(request, {"schema": 1, "key": key})
    checkpoint = folder / "plan.json"
    if checkpoint.is_file():
        saved = _read(checkpoint)
        if saved.get("key") != key:
            raise RuntimeError("This saved animation plan belongs to different inputs. Create a new attempt for changed inputs.")
        scenes = _validate(saved.get("raw") or [], windows, name, kits)
        return name, scenes, windows, key
    raw = []
    for offset in range(0, len(windows), MAX_BATCH):
        batch = windows[offset:offset + MAX_BATCH]
        reply = folder / ("response-%03d.txt" % offset)
        if reply.is_file():
            response = reply.read_text(encoding="utf-8")
        else:
            engine.step("Reference animation: designing scenes %s–%s of %s" %
                        (batch[0]["beat"], batch[-1]["beat"], len(windows)))
            prompt = template.replace("[KIT_CONTRACT]", kits.prompt_block(name)).replace(
                "[CREATOR_INSTRUCTIONS]", engine._extra_block()).replace(
                "[PREVIOUS_SCENES]", json.dumps(raw[-3:], ensure_ascii=False)).replace(
                "[TIMED_BEATS]", json.dumps(batch, ensure_ascii=False))
            response = engine.claude(prompt, cr_claude.VISUAL_MODEL,
                                     max_tokens=max(4000, len(batch) * 600), provider="claudecode")
            reply.write_text(str(response), encoding="utf-8")
            reply.chmod(0o600)
        parsed = _parse_response(response)
        _validate(parsed, batch, name, kits)
        raw.extend(parsed)
    scenes = _validate(raw, windows, name, kits)
    _write(checkpoint, {"schema": 1, "key": key, "raw": raw})
    return name, scenes, windows, key


def prepare(engine, script, job, style, force):
    # Validate before recording; actual visual timing follows the completed voice.
    _kit(engine, style, importlib.import_module("kits"))
    _prompt()


def assemble(engine, job, mp3, srt, style, force, burn_subs, *, kits=None):
    kits = kits or importlib.import_module("kits")
    job = Path(job)
    engine._word_clock(job, srt)
    total = engine._audio_dur(mp3)
    name, scenes, windows, key = plan(engine, job, srt, style, total, kits=kits)
    folder = job / "motion"
    folder.mkdir(exist_ok=True)
    outputs = [folder / ("kit_reference_%03d.mp4" % i) for i in range(len(scenes))]
    render_jobs = [(scene, output) for scene, output in zip(scenes, outputs)
                   if not output.is_file() or output.stat().st_size < 10000]
    if render_jobs:
        engine.step("Reference animation: rendering %s scene(s) in the studied style" % len(render_jobs))
        kits.render(name, render_jobs, workers=max(1, min(2, int(getattr(engine, "MOTION_WORKERS", 2)))),
                    look=(engine.STYLE_INFO[style].get("look") or {}))
    if any(not output.is_file() or output.stat().st_size < 10000 for output in outputs):
        raise RuntimeError("A required reference animation scene did not render. Saved scenes are retained; no fallback was substituted.")
    if callable(getattr(kits, "sound_marks", None)):
        for scene, output in zip(scenes, outputs):
            _write(output.with_suffix(".sfx.json"), kits.sound_marks(name, scene))
    segments = [(window["start"], str(output), scene["duration"])
                for window, output, scene in zip(windows, outputs, scenes)]
    timeline = [("motion", str(output), False, scene["duration"])
                for output, scene in zip(outputs, scenes)]
    _write(job / ".cr-reference2d/delivery.json", {"schema": 1, "key": key,
        "mode": MODE, "coverage_seconds": sum(row[3] for row in timeline),
        "narration_seconds": total, "scenes": len(timeline), "kit": name})
    engine._save_motion_plan(job, timeline, segments)
    engine.step("Reference animation: assembling the complete animated film")
    rendered = engine._render_astro_segments(timeline, job, force, white_fade=0.0, style=style)
    engine._concat_segments(rendered, job / "_visual.mp4")
    engine._final_mux(job, engine._with_sound_design(job, mp3, timeline, style), srt, burn_subs,
                      add_qr=False, vignette=False, no_dust_ranges=[(0.0, total)],
                      sub_center=False, no_sub_ranges=[])
    engine._keep_clean_visual(job, style)
    return job / "video.mp4"


def install(engine):
    """The original plugin branch owns every ordinary/non-clone video unchanged."""
    original = getattr(engine, "_visuals_plugin", None)
    if not callable(original):
        return
    if getattr(original, "_cr_style_delivery", False):
        return
    def plugin(style):
        if selected(engine.STYLE_INFO.get(style)):
            _kit(engine, style, importlib.import_module("kits"))
            return sys.modules[__name__]
        return original(style)
    plugin._cr_style_delivery = True
    engine._visuals_plugin = plugin
    # A submitted stale form cannot attach an unrelated presenter to this mode.
    original_avatar = getattr(engine, "_maybe_avatar", None)
    if not callable(original_avatar):
        return
    def avatar(final, job, *args, **kwargs):
        style_path = Path(job) / "style.txt"
        style = style_path.read_text(encoding="utf-8").strip() if style_path.is_file() else ""
        return final if selected(engine.STYLE_INFO.get(style)) else original_avatar(final, job, *args, **kwargs)
    engine._maybe_avatar = avatar
