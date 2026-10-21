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

Only styles that draw their pictures are checked (an image model and picture words); a style that films or builds its
pictures is skipped with its reason. Nothing here stops a study: a missing account or an unanswered check is noted and
the style is saved as before. run() pays (pictures, the check); apply_saved() only reads what run() saved, so a study's
recovery applies it again without paying twice.
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


def _hash(style) -> str:
    look = style.get("look") or {}
    return hashlib.sha256(json.dumps([look.get("still_suffix"), look.get("image_model"), look.get("character")],
                                     sort_keys=True).encode()).hexdigest()[:20]


def wanted(style) -> str:
    """"" when this style draws its pictures and can be checked, else why it is skipped."""
    look = style.get("look") or {}
    if not str(look.get("still_suffix") or "").strip():
        return "this style has no picture words to draw with"
    if not look.get("image_model"):
        return "this style films or builds its pictures instead of drawing them"
    return ""


def _verdict(overall, characters, people) -> str:
    if overall >= PASS and (not people or characters >= CHARACTERS_PASS):
        return "match"
    return "close" if overall >= 5 else "far"


def run(folder, spec, style, log, *, home, generate=None, upload=None, call=None) -> dict:
    """Draw, judge, and once more with the judge's fixes when below the bar. Saved in <operation>/likeness.json."""
    folder = Path(folder)
    saved = _read(folder / CHECK)
    if saved.get("style_hash") == _hash(style) and saved.get("state") in ("done", "skipped"):
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
    import cr_media
    gen = generate or cr_media.image
    put = upload or (lambda path: cr_media.upload(path, task="images"))
    if call is None:
        import cr_bridge
        call = cr_bridge.call
    look = style.get("look") or {}
    draw_from = shots[0]                                      # drawn from; never judged against itself
    control = shots[-1] if len(shots) >= 5 else None          # the channel's own frame, hidden among the candidates
    judged = [s for s in shots[1:] if s != control][:6]
    people = _people(style)
    context = ("The candidates were drawn by an image model to match the references' look"
               + (", including how their characters are designed." if people else "."))
    ref_url = put(draw_from)
    suffix, extra, rounds, spent = str(look.get("still_suffix") or ""), "", [], 0.0
    out_dir = folder / "likeness"
    out_dir.mkdir(parents=True, exist_ok=True)
    for r in range(ROUNDS):
        pictures = []
        for k, subject in enumerate(SUBJECTS):
            dest = out_dir / f"r{r}_{k}.jpg"
            gen(NOTE + subject + " " + suffix + extra, dest, f"likeness picture {r + 1}.{k + 1}", ref_urls=[ref_url],
                model=look["image_model"])
            spent += PICTURE_USD
            if dest.is_file():
                pictures.append(dest)
        if not pictures:
            break
        got = call("style/compare", {"references": [_b64(p) for p in judged],
                                     "candidates": [_b64(p) for p in pictures] + ([_b64(control)] if control else []),
                                     "context": context})
        spent += float(got.get("cost_usd") or 0)
        scores = got.get("candidates") or []
        mine = [float((scores[i] or {}).get("overall") or 0) if i < len(scores) else 0.0 for i in range(len(pictures))]
        held = float((scores[len(pictures)] or {}).get("overall") or 0) if control and len(scores) > len(pictures) else None
        row = {"round": r + 1, "extra": extra, "pictures": [p.name for p in pictures], "picture_scores": mine,
               "control": held, **{k: float(got.get(k) or 0) for k in ("medium", "characters", "sets", "palette",
                                                                         "light", "camera", "overall")},
               "dealbreakers": got.get("dealbreakers") or [], "fixes": got.get("fixes") or []}
        rounds.append(row)
        log(f"  likeness round {r + 1}: {row['overall']:.0f}/10 overall, characters {row['characters']:.0f}/10"
            + (f" (the channel's own frame: {held:.0f}/10)" if held is not None else ""))
        if _verdict(row["overall"], row["characters"], people) == "match" or not row["fixes"]:
            break
        extra = " Must: " + "; ".join(str(f).rstrip(".") for f in row["fixes"][:5]) + "."
    if not rounds:
        result = {"state": "skipped", "why": "the test pictures could not be drawn", "style_hash": _hash(style)}
        _write(folder / CHECK, result)
        return result
    best = max(rounds, key=lambda x: x["overall"])
    pick = max(range(len(best["pictures"])), key=lambda i: best["picture_scores"][i])
    control_scores = [x["control"] for x in rounds if x["control"] is not None]
    reliable = not control_scores or min(control_scores) >= CONTROL_MIN
    result = {"state": "done", "style_hash": _hash(style), "checked_at": time.time(), "people": people,
              "verdict": _verdict(best["overall"], best["characters"], people), "reliable": reliable,
              "round": best["round"], "extra": best["extra"], "best": best["pictures"][pick],
              "scores": {k: best[k] for k in ("medium", "characters", "sets", "palette", "light", "camera", "overall")},
              "dealbreakers": best["dealbreakers"], "rounds": rounds, "cost_usd": round(spent, 3)}
    cast = str(look.get("character") or "").strip()
    if cast and not look.get("character_image"):              # the recurring cast drawn in the best picture's look
        first = re.split(r"\n|(?<=\.)\s+(?=[A-Z][\w' -]{1,40}(?: \([^)]*\))?:)",
                         re.sub(r"^THE CAST\s*[—-]\s*", "", cast))[0].strip() or cast
        sheet = out_dir / "character.png"
        try:
            gen(NOTE + SHEET + first[:1500], sheet, "character sheet", ref_urls=[put(out_dir / result["best"])],
                model=look["image_model"])
            result["cost_usd"] = round(result["cost_usd"] + PICTURE_USD, 3)
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
    clone = style.setdefault("cr_style_clone", {})
    if result.get("state") == "skipped":
        clone["likeness"] = {"state": "skipped", "why": result.get("why", "")}
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
