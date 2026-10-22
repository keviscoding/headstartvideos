"""The likeness check: before a learned style is saved, test pictures are drawn the way its videos will draw them and
judged against the references' own frames.

A customer cloned @ifologyfilms (round black heads, cute faces, bright jackets, stick legs, bright low-poly sets) and
Studio saved a style that put faceless mannequins on a red steel frame, without a word: nothing ever compared what the
style makes with what the channel looks like. Now the study draws three pictures with the style's own picture model,
words and a reference frame, and ChannelRecipe's server (Gemini, /api/studio/style/compare) scores them against other
reference frames: medium, characters, sets, palette, light, camera and overall, with the deal-breakers and the
prompt fixes that would close the gap. Below the bar, the fixes are added to the style's picture words and three new
pictures are drawn and judged once more (both rounds are in the cost the creator approved for the study). The best
picture becomes the style's drawing (look.style_image), a recurring cast gets its character sheet drawn in it, and the
verdict is saved with the style and shown to the creator, plainly, whatever it is.

A held-out reference frame goes in with the candidates as a hidden control: when the channel's own frame doesn't
score well, the judge didn't understand the references, and the verdict says it can't be trusted.

Only styles that draw their pictures are checked (an image model and picture words), and since Studio 77 styles whose
designed cast the 3D engine builds (look.threed.preset "designed"): their three test shots are RENDERED by the engine
from the style's cast spec (no picture is paid for; one round, since the judge's prompt fixes can't change code). A
style that films its pictures, or whose videos are graphics only, is skipped with its reason. It runs after the style is saved, within a few
minutes, and nothing here undoes or stops a study: a missing account, a busy judge or a picture that never comes back
is noted, the rounds already paid for are kept, and the style stays as it was saved. Every number that decides the
verdict is the drawn pictures' own: the hidden control is only the judge's test. run() pays (pictures, the check);
apply_saved() only reads what run() saved, for the same picture words, so a recovery or an improved copy of the study
applies it again without paying twice.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import shutil
import time
from pathlib import Path

PASS = 7.0                       # overall: a viewer would believe the pictures are this channel's frames
CHARACTERS_PASS = 6.0            # and its people are drawn the same way, when it has people
CONTROL_MIN = 6.0                # the channel's own held-out frame scores at least this, or the judge is not trusted
ROUNDS = 2                       # the first drawing, and one with the judge's fixes
PICTURE_USD = 0.02               # a Kie picture (4 credits)
LIMIT_S = 480                    # the whole check, so a queued image account never holds the saved style for long
PICTURE_WAIT_S = 180             # one picture; a slower one is saved by its task ID and the check moves on
AREAS = ("medium", "characters", "sets", "palette", "light", "camera", "overall")
SUBJECTS = [
    "Two characters of this channel talking in a bright, simple room, both whole and fully in frame. No text.",
    "One character of this channel running across a wide outdoor scene, whole body in frame, seen from the side. "
    "No text.",
    "A close shot of one character of this channel reacting with surprise to something just out of frame. No text.",
]
NOTE = ("STYLE REFERENCE: the reference image shows exactly how this channel's pictures look and how its characters "
        "are designed. Draw a NEW scene in that same medium, rendering, palette and light, with characters designed "
        "the same way (head shape and colour, face, body, limbs, clothes). Do not copy its composition, its exact "
        "characters, any text or logos. ")
SHEET = ("A character sheet on a plain light background: the same character three times, front view, three-quarter "
         "view and full body, neutral pose, no text, no labels. The character: ")
CHECK = "likeness.json"


def _read(path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _jpeg(path, width=960) -> bytes:
    from PIL import Image
    img = Image.open(path).convert("RGB")
    img.thumbnail((width, width))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=82)
    return out.getvalue()


def _b64(path) -> str:
    return base64.b64encode(_jpeg(path)).decode()


def frames(home, look_id) -> list:
    """The look's reference frames, spread across its references (4 per video, in order)."""
    import cr_style_look
    if not look_id:
        return []
    return sorted(cr_style_look.folder(home, look_id).glob("frames/*.jpg"))


def _people(style) -> bool:
    look = style.get("look") or {}
    words = " ".join(str(look.get(k) or "") for k in ("still_suffix", "character")).lower()
    return bool(str(look.get("character") or "").strip()) or bool(re.search(r"\bpeople:|\bcharacters?\b|\bperson\b",
                                                                              words))


def _designed(style) -> dict:
    """The cast spec of a style whose designed cast the 3D engine builds, else None."""
    lt = (style.get("look") or {}).get("threed")
    if isinstance(lt, dict) and lt.get("preset") == "designed":
        return lt.get("designed") if isinstance(lt.get("designed"), dict) else {}
    return None


def _hash(style) -> str:
    look = style.get("look") or {}
    return hashlib.sha256(json.dumps([look.get("still_suffix"), look.get("image_model"), look.get("character"),
                                      _designed(style)], sort_keys=True).encode()).hexdigest()[:20]


def wanted(style) -> str:
    """"" when this style draws its pictures and can be checked, else why it is skipped."""
    if (style.get("cr_style_clone") or {}).get("visual_mode") == "reference_only":
        return "this style's videos are all motion graphics, with no drawn pictures"
    look = style.get("look") or {}
    if _designed(style) is not None:
        return ""                                           # rendered by the 3D engine from the cast spec
    if not str(look.get("still_suffix") or "").strip():
        return "this style has no picture words to draw with"
    if not look.get("image_model"):
        return "this style films or builds its pictures instead of drawing them"
    return ""


def _verdict(overall, characters, people) -> str:
    if overall >= PASS and (not people or characters >= CHARACTERS_PASS):
        return "match"
    return "close" if overall >= 5 else "far"


def _wants_upload(spec) -> bool:
    """The creator answered that they will upload their own host or mascot: no drawn sheet takes their place."""
    return str((spec.get("answers") or {}).get("host") or "").startswith("Yes, I'll upload")


def _mean(values):
    values = [float(v) for v in values if isinstance(v, (int, float))]
    return sum(values) / len(values) if values else None


def _row(r, extra, pictures, got) -> dict:
    """One round's scores, from the drawn pictures alone: the judge scores each candidate in every area, and the
    hidden control (after the pictures) is only the test of the judge."""
    scores = [c if isinstance(c, dict) else {} for c in got.get("candidates") or []]
    mine = scores[:len(pictures)] + [{}] * (len(pictures) - len(scores[:len(pictures)]))
    held = scores[len(pictures)] if len(scores) > len(pictures) else None
    row = {"round": r + 1, "extra": extra, "pictures": [p.name for p in pictures],
           "picture_scores": [float(c.get("overall") or 0) for c in mine],
           "control": float(held.get("overall") or 0) if held is not None else None,
           "dealbreakers": got.get("dealbreakers") or [], "fixes": got.get("fixes") or []}
    for area in AREAS:
        own = _mean([c.get(area) for c in mine])
        row[area] = round(own if own is not None else float(got.get(area) or 0), 2)
    return row


def render_designed(home, style, out_dir, *, timeout_s=None, run=None) -> list:
    """The three test shots rendered by this Studio's 3D engine in the style's design (cr_designed.test_video): the
    preview frames of each scene, in SUBJECTS order. [] when the engine can't render them."""
    import subprocess
    import sys
    import cr_designed
    home, out_dir = Path(home), Path(out_dir)
    if not cr_designed.engine_ready(home):
        raise RuntimeError("this Studio's 3D engine has no designed-cast kit (update Studio)")
    video = cr_designed.test_video(_designed(style) or {})
    work = out_dir / "render"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    (work / "video.json").write_text(json.dumps(video, indent=1), encoding="utf-8")
    cmd = [sys.executable, str(home / "threed" / "render.py"), str(work / "video.json"), str(work), "--preview", "1.6",
           "--w", "1280", "--h", "720"]
    (run or subprocess.run)(cmd, cwd=str(home / "threed"), capture_output=True, text=True, timeout=timeout_s or LIMIT_S)
    pictures = []
    for k, scene in enumerate(video["scenes"]):
        got = sorted(work.glob(scene["id"] + "_*.jpg"))
        if got:
            dest = out_dir / f"r0_{k}.jpg"
            shutil.copyfile(got[0], dest)
            pictures.append(dest)
    return pictures


def run(folder, spec, style, log, *, home, generate=None, upload=None, call=None, render=None) -> dict:
    """Draw, judge, and once more with the judge's fixes when below the bar. Saved in <operation>/likeness.json."""
    folder = Path(folder)
    saved = _read(folder / CHECK)
    if saved.get("style_hash") == _hash(style) and saved.get("state") in ("done", "skipped", "failed"):
        return saved
    why = wanted(style)
    if why:
        result = {"state": "skipped", "why": why, "style_hash": _hash(style)}
        _write(folder / CHECK, result)
        log(f"  likeness check skipped: {why}")
        return result
    shots = frames(home, spec.get("look_id"))
    if len(shots) < 3:
        result = {"state": "skipped", "why": "too few reference frames to compare with", "style_hash": _hash(style)}
        _write(folder / CHECK, result)
        return result
    designed = _designed(style) is not None
    if not designed:
        import cr_media
        gen = generate or cr_media.image
        put = upload or (lambda path: cr_media.upload(path, task="images"))
    if call is None:
        import cr_bridge
        call = cr_bridge.call
    _write(folder / CHECK, {"state": "running", "started_at": time.time(), "style_hash": _hash(style)})
    deadline = time.monotonic() + LIMIT_S
    look = style.get("look") or {}
    draw_from = shots[0]                                      # drawn from; never judged against itself
    control = shots[-1] if len(shots) >= 5 else None          # the channel's own frame, hidden among the candidates
    judged = [s for s in shots[1:] if s != control][:6]
    people = _people(style) or designed
    context = ("The candidates were rendered by a 3D engine in the references' design, including how their characters "
               "are built." if designed else "The candidates were drawn by an image model to match the references' look"
               + (", including how their characters are designed." if people else "."))
    suffix, extra, rounds, spent, stopped = str(look.get("still_suffix") or ""), "", [], 0.0, ""
    out_dir = folder / "likeness"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        ref_url = None if designed else put(draw_from)
        for r in range(1 if designed else ROUNDS):
            pictures = []
            if designed:                                      # rendered, not drawn: nothing paid but the judge
                pictures = (render or render_designed)(home, style, out_dir, timeout_s=max(60, deadline - time.monotonic()))
            for k, subject in enumerate([] if designed else SUBJECTS):
                if time.monotonic() > deadline:
                    raise TimeoutError("the image account took too long")
                dest = out_dir / f"r{r}_{k}.jpg"
                spent += PICTURE_USD
                gen(NOTE + subject + " " + suffix + extra, dest, f"likeness picture {r + 1}.{k + 1}",
                    ref_urls=[ref_url], model=look["image_model"], timeout_s=PICTURE_WAIT_S)
                if dest.is_file():
                    pictures.append(dest)
            if not pictures:
                break
            got = call("style/compare", {"references": [_b64(p) for p in judged],
                                         "candidates": [_b64(p) for p in pictures] + ([_b64(control)] if control else []),
                                         "context": context})
            spent += float(got.get("cost_usd") or 0)
            row = _row(r, extra, pictures, got)
            rounds.append(row)
            log(f"  likeness round {r + 1}: {row['overall']:.0f}/10 overall, characters {row['characters']:.0f}/10"
                + (f" (the channel's own frame: {row['control']:.0f}/10)" if row["control"] is not None else ""))
            if _verdict(row["overall"], row["characters"], people) == "match" or not row["fixes"]:
                break
            extra = " Must: " + "; ".join(str(f).rstrip(".") for f in row["fixes"][:5]) + "."
    except BaseException as error:                            # noqa: BLE001 - paid rounds are kept; the style stands
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        stopped = str(error)[:160] or type(error).__name__
        log(f"  likeness check stopped: {stopped}")
    if not rounds:
        result = {"state": "failed" if stopped else "skipped", "style_hash": _hash(style), "cost_usd": round(spent, 3),
                  "why": (f"the check could not finish ({stopped})" if stopped
                          else "the test pictures could not be drawn")}
        _write(folder / CHECK, result)
        return result
    best = max(rounds, key=lambda x: x["overall"])
    pick = max(range(len(best["pictures"])), key=lambda i: best["picture_scores"][i])
    control_scores = [x["control"] for x in rounds if x["control"] is not None]
    reliable = not control_scores or min(control_scores) >= CONTROL_MIN
    result = {"state": "done", "style_hash": _hash(style), "checked_at": time.time(), "people": people,
              "verdict": _verdict(best["overall"], best["characters"], people), "reliable": reliable,
              "round": best["round"], "extra": best["extra"], "best": best["pictures"][pick],
              "scores": {k: best[k] for k in AREAS}, "dealbreakers": best["dealbreakers"], "rounds": rounds,
              "cost_usd": round(spent, 3)}
    if stopped:
        result["stopped"] = stopped
    cast = "" if designed else str(look.get("character") or "").strip()
    if cast and not _wants_upload(spec) and not look.get("character_image") and time.monotonic() < deadline:
        first = re.split(r"\n|(?<=\.)\s+(?=[A-Z][\w' -]{1,40}(?: \([^)]*\))?:)",
                         re.sub(r"^THE CAST\s*[—-]\s*", "", cast))[0].strip() or cast
        sheet = out_dir / "character.png"
        try:                                                  # the recurring cast drawn in the best picture's look
            result["cost_usd"] = round(result["cost_usd"] + PICTURE_USD, 3)
            gen(NOTE + SHEET + first[:1500], sheet, "character sheet", ref_urls=[put(out_dir / result["best"])],
                model=look["image_model"], timeout_s=PICTURE_WAIT_S)
            if sheet.is_file():
                result["character"] = sheet.name
        except Exception as e:                                # noqa: BLE001 - the style keeps its words for the cast
            log(f"  character sheet not drawn: {str(e)[:120]}")
    _write(folder / CHECK, result)
    return result


def apply_saved(folder, style, home) -> dict:
    """The saved check on the style, with no model call: the best picture as its drawing, the judge's fixes in its
    picture words (when they won), the character sheet, and the verdict for the creator."""
    result = _read(Path(folder) / CHECK)
    if not result or result.get("style_hash") != _hash(style):
        return style                                          # no check, or one made for other picture words
    clone = style.setdefault("cr_style_clone", {})
    if result.get("state") in ("skipped", "failed"):
        clone["likeness"] = {"state": result["state"], "why": result.get("why", ""), "cost_usd": result.get("cost_usd")}
        return style
    if result.get("state") != "done":
        return style
    look = style.setdefault("look", {})
    extra = str(result.get("extra") or "")
    if extra and extra not in str(look.get("still_suffix") or ""):
        look["still_suffix"] = str(look.get("still_suffix") or "") + extra
    name = re.sub(r"[^a-z0-9_-]", "", str(style.get("name") or ""))
    assets = Path(home) / "assets"
    target = assets / "styles" / "learned" / name
    best = Path(folder) / "likeness" / str(result.get("best") or "")
    if name and best.is_file() and look.get("image_model"):
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(best, target / "style.jpg")
        look["style_image"] = (target / "style.jpg").relative_to(assets).as_posix()
    sheet = Path(folder) / "likeness" / str(result.get("character") or "")
    if name and result.get("character") and sheet.is_file():
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sheet, target / "character.png")
        look["character_image"] = (target / "character.png").relative_to(assets).as_posix()
    clone["likeness"] = {k: result.get(k) for k in ("state", "verdict", "reliable", "scores", "dealbreakers", "round",
                                                     "people", "best", "cost_usd", "checked_at")}
    rounds = result.get("rounds") or [{}]
    shown = next((r for r in rounds if r.get("round") == result.get("round")), rounds[-1])
    clone["likeness"]["pictures"] = shown.get("pictures") or []
    return style
