"""
Storyboard Pack — dialogue story → numbered stills + I2V prompts → zip.

Output layout (I2V-ready):
  {slug}_pack/
    README.md
    MANIFEST.csv
    ALL_PROMPTS.txt
    001_scene.jpg
    001_prompt.txt
    ...
"""

from __future__ import annotations

import csv
import json
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ProgressFn = Callable[[str], None]

MAX_FREE_MINUTES = 8
MAX_PAID_MINUTES = 30
SEC_PER_BEAT = 8.0  # target hold / I2V clip length

_BIBLE_PATH = Path(__file__).resolve().parent / "storyboard_family_english_bible.txt"
_BIBLE_CACHE: str | None = None


def load_style_bible() -> str:
    """Competitor-distilled A1–A2 family-story style guide (not a fine-tune)."""
    global _BIBLE_CACHE
    if _BIBLE_CACHE is not None:
        return _BIBLE_CACHE
    try:
        _BIBLE_CACHE = _BIBLE_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        _BIBLE_CACHE = ""
    return _BIBLE_CACHE


# Visual style presets — user picks one; Pixar-lite is default, not the only look.
VISUAL_STYLE_PRESETS: dict[str, dict[str, str]] = {
    "pixar_lite": {
        "label": "3D Pixar-lite",
        "lock": (
            "3D Pixar-lite animation style, soft global illumination, large expressive eyes, "
            "smooth skin, clean textures, shallow depth of field, 16:9 widescreen, "
            "no text, no subtitles, no watermark, no logos"
        ),
        "short": "3D Pixar-lite, soft light, expressive eyes, 16:9, no text/subtitles",
    },
    "anime_2d": {
        "label": "2D anime",
        "lock": (
            "2D anime animation style, clean line art, vibrant cel shading, expressive eyes, "
            "soft gradients, cinematic lighting, 16:9 widescreen, "
            "no text, no subtitles, no watermark, no logos"
        ),
        "short": "2D anime, clean lines, cel shading, 16:9, no text/subtitles",
    },
    "storybook_watercolor": {
        "label": "Storybook watercolor",
        "lock": (
            "soft storybook watercolor illustration style, gentle washes, warm paper texture, "
            "hand-painted feel, friendly characters, 16:9 widescreen, "
            "no text, no subtitles, no watermark, no logos"
        ),
        "short": "storybook watercolor, soft washes, 16:9, no text/subtitles",
    },
    "comic_cartoon": {
        "label": "Comic cartoon",
        "lock": (
            "bold comic cartoon style, thick outlines, flat vibrant colors, dynamic poses, "
            "expressive faces, 16:9 widescreen, no text, no subtitles, no watermark, no logos"
        ),
        "short": "comic cartoon, bold outlines, flat color, 16:9, no text/subtitles",
    },
    "semi_realistic": {
        "label": "Semi-realistic 3D",
        "lock": (
            "semi-realistic 3D animation style, detailed textures, natural proportions, "
            "cinematic lighting, shallow depth of field, 16:9 widescreen, "
            "no text, no subtitles, no watermark, no logos"
        ),
        "short": "semi-realistic 3D, cinematic light, 16:9, no text/subtitles",
    },
}

DEFAULT_VISUAL_STYLE = "pixar_lite"

# Backward-compat aliases (prefer resolve_visual_style)
STYLE_LOCK = VISUAL_STYLE_PRESETS[DEFAULT_VISUAL_STYLE]["lock"]
STYLE_SHORT = VISUAL_STYLE_PRESETS[DEFAULT_VISUAL_STYLE]["short"]

# Optional Easy English family template looks (only when user opts into the template)
FAMILY_TEMPLATE_LOOKS: dict[str, str] = {
    "max": "boy ~8 years old, messy black hair, large expressive eyes, blue polo shirt, grey pants",
    "mia": "girl ~7 years old, black hair in pigtails with pink ties, large expressive eyes, yellow sweater",
    "mom": "woman mid-30s, long wavy brown hair, warm smile, light cardigan over soft top",
    "dad": "man mid-30s, curly dark hair, short beard, blue shirt, kind eyes",
}

# Deprecated aliases — kept so older imports don't break
DEFAULT_LOOKS = FAMILY_TEMPLATE_LOOKS
DEFAULT_CAST = (
    "Max: boy messy black hair blue polo grey pants. "
    "Mia: girl black pigtails pink ties yellow sweater. "
    "Mom: long wavy brown hair light cardigan. "
    "Dad: curly dark hair short beard blue shirt."
)


def resolve_visual_style(visual_style: str = "") -> tuple[str, str, str]:
    """Return (style_id, style_lock, style_short). Unknown ids fall back to default."""
    sid = (visual_style or "").strip().lower() or DEFAULT_VISUAL_STYLE
    preset = VISUAL_STYLE_PRESETS.get(sid) or VISUAL_STYLE_PRESETS[DEFAULT_VISUAL_STYLE]
    if sid not in VISUAL_STYLE_PRESETS:
        sid = DEFAULT_VISUAL_STYLE
    return sid, preset["lock"], preset["short"]


def _cast_id_from_name(name: str, used: set[str] | None = None) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", (name or "character").strip().lower()).strip("_") or "character"
    base = base[:32]
    used = used if used is not None else set()
    cid = base
    n = 2
    while cid in used:
        cid = f"{base}_{n}"
        n += 1
    used.add(cid)
    return cid


def _empty_cast_member(
    *,
    cid: str,
    name: str = "",
    look_prompt: str = "",
    included: bool = True,
) -> dict[str, Any]:
    return {
        "id": cid,
        "name": (name or cid).strip() or cid,
        "included": bool(included),
        "look_prompt": (look_prompt or "").strip(),
        "portrait_url": "",
        "sheet_url": "",
        "portrait_path": "",
        "sheet_path": "",
    }


def family_template_cast() -> list[dict[str, Any]]:
    """Optional Easy English family starter cast — only when user picks that template."""
    names = {"max": "Max", "mia": "Mia", "mom": "Mom", "dad": "Dad"}
    return [
        _empty_cast_member(cid=cid, name=names[cid], look_prompt=FAMILY_TEMPLATE_LOOKS[cid])
        for cid in ("max", "mia", "mom", "dad")
    ]


def default_series_cast() -> list[dict[str, Any]]:
    """Blank cast — users bring their own characters (or extract from story/script)."""
    return []


def normalize_cast(cast: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Normalize an arbitrary N-character cast. Does NOT inject Max/Mia defaults."""
    ordered: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for row in cast or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        cid = str(row.get("id") or "").strip().lower()
        if not cid:
            if not name:
                continue
            cid = _cast_id_from_name(name, used_ids)
        elif cid in used_ids:
            cid = _cast_id_from_name(name or cid, used_ids)
        else:
            used_ids.add(cid)
        cur = _empty_cast_member(cid=cid, name=name or cid.title())
        for key in (
            "name", "included", "look_prompt",
            "portrait_url", "sheet_url", "portrait_path", "sheet_path",
        ):
            if key in row and row[key] is not None:
                cur[key] = row[key]
        cur["id"] = cid
        cur["included"] = bool(cur.get("included", True))
        if not (cur.get("name") or "").strip():
            cur["name"] = cid.replace("_", " ").title()
        if not (cur.get("look_prompt") or "").strip():
            # Soft fallback — describe the named character, not a fixed family look
            cur["look_prompt"] = f"consistent animated character named {cur['name']}"
        ordered.append(cur)
    return ordered


def extract_cast_from_text(
    *,
    story: str = "",
    script: str = "",
    max_characters: int = 8,
    visual_style: str = "",
) -> list[dict[str, Any]]:
    """LLM: propose recurring cast from the user's story and/or script."""
    from core.atlas_llm import generate_text

    body_parts: list[str] = []
    if (story or "").strip():
        body_parts.append("STORY:\n" + story.strip()[:4000])
    if (script or "").strip():
        body_parts.append("SCRIPT:\n" + script.strip()[:8000])
    if not body_parts:
        return []
    n = max(1, min(int(max_characters or 8), 12))
    style_id, _, _ = resolve_visual_style(visual_style)
    style_label = VISUAL_STYLE_PRESETS.get(style_id, {}).get("label", "3D Pixar-lite")
    if style_id == "semi_realistic":
        look_rules = (
            f"Write each look_prompt for {style_label} animation (cinematic 3D, still stylized — "
            "not a live-action photo). Include age, hair, outfit, distinctive features."
        )
    else:
        look_rules = (
            f"Write each look_prompt as a {style_label} ANIMATED character-design note "
            f"(stylized proportions, expressive features, clear silhouette). "
            f"Lead with '{style_label} animated character'. "
            "Never write photoreal / live-action / real-person descriptions. "
            "Include age, hair, outfit, distinctive features suited to that animation style."
        )
    raw = generate_text(
        (
            f"Extract up to {n} RECURRING characters from this animation story/script.\n"
            "Only include characters who appear more than once or drive the plot.\n"
            "Skip one-off extras, crowds, and unnamed background people.\n"
            f"{look_rules}\n"
            "Do NOT invent characters that are not in the text.\n"
            'Output ONLY JSON: {"characters": [{"name": "...", "look_prompt": "..."}]}\n\n'
            + "\n\n".join(body_parts)
        ),
        system=(
            f"You extract cast lists for {style_label} animation storyboards. "
            "Output ONLY valid JSON. No markdown."
        ),
        max_tokens=1200,
        temperature=0.3,
    )
    data = _parse_json_obj(raw) or {}
    rows = data.get("characters") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    proposed: list[dict[str, Any]] = []
    used: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        look = str(row.get("look_prompt") or "").strip()
        if not name:
            continue
        cid = _cast_id_from_name(name, used)
        proposed.append(_empty_cast_member(cid=cid, name=name, look_prompt=look))
        if len(proposed) >= n:
            break
    return normalize_cast(proposed)


@dataclass
class Beat:
    index: int
    target_sec: float
    dialogue: str
    image_prompt: str
    i2v_prompt: str
    location: str = ""
    characters: str = ""
    image_path: str = ""
    error: str = ""


@dataclass
class PackResult:
    pack_dir: str
    zip_path: str
    title: str
    beat_count: int
    target_minutes: float
    scene_files: list[str] = field(default_factory=list)
    manifest_path: str = ""


def _slug(text: str, max_len: int = 48) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (text or "story").strip()).strip("_").lower()
    return (s or "story")[:max_len]


def _parse_json_obj(text: str) -> dict | None:
    if not text:
        return None
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return None


def clamp_minutes(
    target_minutes: float,
    *,
    is_admin: bool = False,
    is_paid: bool = False,
) -> float:
    mins = float(target_minutes or 8)
    mins = max(1.0, mins)
    if is_admin:
        return min(mins, float(MAX_PAID_MINUTES))
    if is_paid:
        return min(mins, float(MAX_PAID_MINUTES))
    return min(mins, float(MAX_FREE_MINUTES))


def _beat_count_for_minutes(minutes: float, *, pack_mode: str = "full") -> int:
    mode = (pack_mode or "full").strip().lower()
    if mode == "preview":
        # First ~minute: ~8 scenes @ 8s — dopamine sample before full pack
        return 8
    total_sec = max(60.0, minutes * 60.0)
    n = int(round(total_sec / SEC_PER_BEAT))
    return max(8, min(n, 240))  # hard ceiling ~32 min @ 8s


def _format_cast_constraint(cast: list[dict[str, Any]] | None) -> str:
    """Human-readable cast line for the beat-sheet prompt."""
    rows = normalize_cast(cast)
    included: list[str] = []
    for row in rows:
        if not row.get("included", True):
            continue
        cid = str(row.get("id") or "").strip().lower()
        name = str(row.get("name") or cid or "Character").strip() or "Character"
        look = str(row.get("look_prompt") or "").strip()
        has_art = bool(row.get("portrait_url") or row.get("sheet_url") or row.get("portrait_path"))
        art = " (has reference portrait — match look exactly)" if has_art else ""
        included.append(f"{name} (id={cid or 'custom'}): {look}{art}")
    if not included:
        return (
            "Cast: extract and keep consistent any recurring characters clearly named "
            "in the user's story/script. Do not invent a fixed stock cast."
        )
    return "Cast (ONLY these characters, use their names and looks exactly):\n" + "\n".join(
        f"- {x}" for x in included
    )


def _cast_look_lock(cast: list[dict[str, Any]] | None) -> str:
    rows = [r for r in normalize_cast(cast) if r.get("included", True)]
    if not rows:
        return ""
    parts = []
    for r in rows:
        name = (r.get("name") or r.get("id") or "Character").strip()
        look = (r.get("look_prompt") or "").strip()
        parts.append(f"{name}: {look}" if look else name)
    return " ".join(parts)


def _style_exclusivity(visual_style: str = "") -> str:
    """Hard ban on common drift styles so one scene doesn't flip to anime/photo."""
    sid, _, _ = resolve_visual_style(visual_style)
    label = VISUAL_STYLE_PRESETS.get(sid, {}).get("label", "3D Pixar-lite")
    bans = [
        "photorealistic", "real photograph", "real people", "live-action",
    ]
    if sid != "anime_2d":
        bans += [
            "2D anime", "Japanese anime", "manga", "anime eyes", "cel-shaded anime",
        ]
    if sid != "comic_cartoon":
        bans.append("flat comic book ink")
    if sid != "storybook_watercolor":
        bans.append("watercolor illustration")
    ban_txt = ", ".join(bans)
    return (
        f"CRITICAL STYLE LOCK: render ONLY as {label}. "
        f"Do NOT switch styles. Forbidden: {ban_txt}."
    )


def _anti_photo_clause(style_id: str) -> str:
    if style_id == "semi_realistic":
        return "Stylized 3D animation — not live-action photography."
    return (
        "Fully stylized ANIMATION character — NOT photorealistic, NOT a real photograph, "
        "NOT a real person, NOT CGI that looks like a live human. Cartoon/animated look only."
    )


def _generate_cast_t2i(prompt: str, out_path: Path, *, aspect_ratio: str = "1:1") -> bool:
    """Nano-banana T2I for cast art — respects stylized prompts far better than ERNIE/GPT Image."""
    try:
        from core.thumbnail_gen import _T2I_MODELS, _submit_and_download
    except Exception as e:
        print(f"[storyboard] cast T2I helpers unavailable: {e}")
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    for model in _T2I_MODELS:
        payload = {
            "model": model,
            "prompt": (prompt or "")[:1400],
            "aspect_ratio": aspect_ratio,
            "resolution": "1k",
            "output_format": "png",
            "enable_base64_output": False,
        }
        try:
            _submit_and_download(payload, out_path, label=f"cast t2i/{model}")
            if out_path.is_file():
                return True
        except Exception as e:
            msg = str(e)
            print(f"[storyboard] cast T2I failed ({model}): {msg[:120]}")
            if "insufficient balance" in msg.lower():
                break
            continue
    return False


def generate_character_portrait(
    *,
    name: str,
    look_prompt: str,
    out_path: str | Path,
    visual_style: str = "",
) -> str:
    """Generate a hero portrait for Cast studio. Returns local path."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    sid, style_lock, style_short = resolve_visual_style(visual_style)
    label = VISUAL_STYLE_PRESETS.get(sid, {}).get("label", "3D Pixar-lite")
    look = (look_prompt or "").strip() or f"{label} animated character"
    nm = (name or "Character").strip()
    anti = _anti_photo_clause(sid)
    full = (
        f"{label} animation character portrait of {nm}. {style_lock}. {look}. "
        f"{anti} Centered upper-body portrait, soft warm lighting, plain soft background, "
        "expressive animated face, character design sheet quality, no text, no watermark."
    )
    # Prefer nano-banana — ERNIE/GPT Image 2 often ignore style and spit photoreal faces.
    if _generate_cast_t2i(full, out, aspect_ratio="1:1"):
        return str(out)

    from core.illustration_gen import generate_single_illustration

    short = f"{label}. {style_short}. Portrait of {nm}. {look}. {anti}"[:480]
    result = generate_single_illustration(full, str(out), short_prompt=short)
    if not result.success or not out.is_file():
        raise RuntimeError(result.error or "Character portrait failed")
    return str(out)


def generate_character_sheet(
    *,
    name: str,
    look_prompt: str,
    out_path: str | Path,
    portrait_path: str = "",
    visual_style: str = "",
) -> str:
    """Multi-angle character sheet (consistency aid for I2V)."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    sid, style_lock, style_short = resolve_visual_style(visual_style)
    label = VISUAL_STYLE_PRESETS.get(sid, {}).get("label", "3D Pixar-lite")
    look = (look_prompt or "").strip() or f"{label} animated character"
    nm = (name or "Character").strip()
    anti = _anti_photo_clause(sid)
    full = (
        f"{label} animation character design sheet for {nm}. {style_lock}. {look}. {anti} "
        "Same character shown in multiple panels on one image: front view, 3/4 view, "
        "side profile, close-up face. Consistent face, hair, outfit across all panels. "
        "Clean white/light grey background, no text labels, no watermark."
    )

    # If we have a portrait, edit from it so the sheet matches the approved look.
    if portrait_path and Path(portrait_path).is_file():
        try:
            from core.thumbnail_gen import _EDIT_MODELS, _submit_and_download, _upload_media
            ref_url = _upload_media(str(portrait_path))
            if ref_url and ref_url.startswith("http"):
                edit_prompt = (
                    f"Using the reference portrait, create a {label} character design sheet for {nm}. "
                    f"Keep face, hair, and outfit IDENTICAL to the reference. {look}. {anti} "
                    "Multiple panels: front, 3/4, side profile, close-up face. "
                    "Clean light background, no text, no watermark."
                )
                for model in _EDIT_MODELS:
                    payload = {
                        "model": model,
                        "prompt": edit_prompt[:1400],
                        "images": [ref_url],
                        "aspect_ratio": "16:9",
                        "resolution": "1k",
                        "output_format": "png",
                        "enable_base64_output": False,
                    }
                    try:
                        _submit_and_download(payload, out, label=f"cast sheet edit/{model}")
                        if out.is_file():
                            return str(out)
                    except Exception as e:
                        print(f"[storyboard] cast sheet edit failed ({model}): {e}")
                        if "insufficient balance" in str(e).lower():
                            break
        except Exception as e:
            print(f"[storyboard] cast sheet edit path failed: {e}")

    if _generate_cast_t2i(full, out, aspect_ratio="16:9"):
        return str(out)

    from core.illustration_gen import generate_single_illustration

    short = f"{label}. {style_short}. Character sheet {nm}. {look}. {anti}"[:480]
    result = generate_single_illustration(full, str(out), short_prompt=short)
    if not result.success or not out.is_file():
        raise RuntimeError(result.error or "Character sheet failed")
    return str(out)


def _format_mistake_constraint(mistake_by: str, cast: list[dict[str, Any]] | None) -> str:
    """Legacy helper — mistake framing is optional; kept for older callers."""
    raw = (mistake_by or "").strip()
    if not raw:
        return ""
    name_map: dict[str, str] = {}
    for row in cast or []:
        if isinstance(row, dict) and row.get("id"):
            name_map[str(row["id"]).strip().lower()] = str(row.get("name") or row["id"]).strip()
    key = raw.lower()
    who = name_map.get(key, raw)
    return f"Optional conflict focus (if it fits the story): {who}."


def suggest_morals_from_story(
    story: str,
    *,
    count: int = 4,
    template: str = "",
) -> list[str]:
    """Cheap LLM: optional one-line takeaways grounded in the user's story."""
    from core.atlas_llm import generate_text

    body = (story or "").strip()
    if not body:
        return []
    if len(body) > 4000:
        body = body[:4000] + "\n…[truncated]"
    n = max(2, min(int(count or 4), 6))
    tmpl = (template or "").strip().lower()
    if tmpl == "easy_english_family":
        framing = (
            f"Given this children's Easy English family story, suggest {n} short morals "
            "(one sentence each) the viewer could learn at the end.\n"
        )
        system = "You write clear A1–A2 kids morals. Output ONLY valid JSON. No markdown."
    else:
        framing = (
            f"Given this animation story, suggest {n} short optional takeaways "
            "(one sentence each) a viewer might feel or learn — only if they fit naturally. "
            "Do not invent a moral that fights the story.\n"
        )
        system = "You write concise story takeaways. Output ONLY valid JSON. No markdown."
    raw = generate_text(
        framing + 'Output ONLY JSON: {"morals": ["...", "..."]}\n\n'
        f"STORY:\n{body}",
        system=system,
        max_tokens=600,
        temperature=0.5,
    )
    data = _parse_json_obj(raw) or {}
    morals = data.get("morals") if isinstance(data, dict) else None
    if not isinstance(morals, list):
        return []
    out: list[str] = []
    for m in morals:
        s = str(m or "").strip()
        if s and s not in out:
            out.append(s)
        if len(out) >= n:
            break
    return out


def suggest_storyboard_thumbnail_brief(
    *,
    title: str = "",
    story: str = "",
    script: str = "",
    cast: list[dict[str, Any]] | None = None,
    visual_style: str = "",
    moral: str = "",
) -> dict[str, Any]:
    """Cheap LLM: one thumbnail concept from a short story gist + main cast (not full script)."""
    from core.atlas_llm import generate_text

    sid, style_lock, _ = resolve_visual_style(visual_style)
    label = VISUAL_STYLE_PRESETS.get(sid, {}).get("label", "3D Pixar-lite")
    gist_src = (story or "").strip() or (script or "").strip()
    if len(gist_src) > 800:
        gist_src = gist_src[:800] + "…"
    cast_lines: list[str] = []
    for row in normalize_cast(cast):
        if not row.get("included", True):
            continue
        name = str(row.get("name") or row.get("id") or "").strip()
        look = str(row.get("look_prompt") or "").strip()
        if not name:
            continue
        cast_lines.append(f"- {name}: {look[:120]}" if look else f"- {name}")
        if len(cast_lines) >= 6:
            break
    cast_block = "\n".join(cast_lines) if cast_lines else "(no cast listed)"
    moral_line = (moral or "").strip()
    raw = generate_text(
        (
            f"Title: {(title or 'Untitled').strip()}\n"
            f"Visual style: {label}\n"
            f"{'Optional takeaway: ' + moral_line + chr(10) if moral_line else ''}"
            f"Story gist (truncated):\n{gist_src or '(title only)'}\n\n"
            f"Cast:\n{cast_block}\n\n"
            "Propose ONE YouTube thumbnail concept for this animated story. "
            "Pick 1–2 main characters only. Keep it clickable and emotional.\n"
            "Output ONLY JSON:\n"
            '{"hook":"short hook line for the image mood",'
            '"emotion":"one emotion word",'
            '"cast_focus":["Name"],'
            '"composition":"one sentence describing the thumbnail frame"}\n'
        ),
        system=(
            f"You design {label} YouTube thumbnails for animation channels. "
            "Output ONLY valid JSON. No markdown."
        ),
        max_tokens=400,
        temperature=0.55,
    )
    data = _parse_json_obj(raw) or {}
    hook = str(data.get("hook") or "").strip() or "Emotional character close-up"
    emotion = str(data.get("emotion") or "").strip() or "dramatic"
    composition = str(data.get("composition") or "").strip() or (
        "Bold character faces filling the frame, cinematic lighting"
    )
    focus_raw = data.get("cast_focus") if isinstance(data, dict) else None
    focus: list[str] = []
    if isinstance(focus_raw, list):
        for n in focus_raw:
            s = str(n or "").strip()
            if s and s not in focus:
                focus.append(s)
            if len(focus) >= 2:
                break
    if not focus and cast_lines:
        # Fallback: first 1–2 included cast names
        for row in normalize_cast(cast):
            if not row.get("included", True):
                continue
            nm = str(row.get("name") or "").strip()
            if nm:
                focus.append(nm)
            if len(focus) >= 2:
                break
    style_prompt = (
        f"{label} animated YouTube thumbnail. {style_lock}. "
        f"Hook: {hook}. Emotion: {emotion}. Composition: {composition}. "
        f"Feature: {', '.join(focus) if focus else 'main characters'}. "
        "Large expressive faces, high click appeal, clean 16:9, "
        "bold readable title text if appropriate, no watermark, no logos."
    )
    return {
        "hook": hook,
        "emotion": emotion,
        "cast_focus": focus,
        "composition": composition,
        "visual_style": sid,
        "style_label": label,
        "style_prompt": style_prompt,
    }


def generate_beat_sheet(
    *,
    title: str,
    topic: str = "",
    script: str = "",
    target_minutes: float,
    story: str = "",
    moral: str = "",
    cast: list[dict[str, Any]] | None = None,
    mistake_by: str = "",
    dialogue_mode: str = "generate",
    pack_mode: str = "full",
    visual_style: str = "",
    template: str = "",
    progress: ProgressFn | None = None,
) -> tuple[str, list[Beat]]:
    """LLM → title + ordered beats. User craft fields are hard constraints."""
    from core.atlas_llm import generate_text

    log = progress or (lambda _m: None)
    pmode = (pack_mode or "full").strip().lower()
    if pmode not in ("preview", "full"):
        pmode = "full"
    tmpl = (template or "").strip().lower()
    family_mode = tmpl == "easy_english_family"
    n_beats = _beat_count_for_minutes(target_minutes, pack_mode=pmode)
    if pmode == "preview":
        log(f"Planning first-minute preview — {n_beats} opening scenes…")
    else:
        log(f"Planning ~{n_beats} scenes for {target_minutes:.0f} min…")

    story_text = (story or topic or "").strip()
    moral_text = (moral or "").strip()
    mode = (dialogue_mode or "generate").strip().lower()
    if mode not in ("generate", "paste"):
        mode = "paste" if (script or "").strip() else "generate"

    if family_mode:
        system = (
            "You write A1–A2 easy-English family dialogue storyboards for YouTube. "
            "Match the STYLE BIBLE voice, structure, and dialogue craft exactly. "
            "NEVER invent a different plot than the user's story. "
            "Never copy competitor stories. "
            "Output ONLY valid JSON. No markdown."
        )
    else:
        system = (
            "You write animation dialogue storyboards for YouTube / long-form image-to-video. "
            "Follow the user's story or script exactly — never invent a different plot. "
            "Keep characters visually consistent across beats. "
            "Output ONLY valid JSON. No markdown."
        )

    style_id, _, _ = resolve_visual_style(visual_style)
    user_parts = [
        f"Target runtime: {target_minutes:.1f} minutes.",
        f"Produce exactly {n_beats} beats (scenes).",
        f"Each beat target_sec should be about {SEC_PER_BEAT:.0f} seconds (5–12 ok).",
        "Sum of target_sec should be close to the target runtime.",
        f"Visual style id (for consistency notes only; do not put style brand words in image_prompt): {style_id}.",
        _format_cast_constraint(cast),
        "",
    ]
    if family_mode:
        bible = load_style_bible()
        if bible:
            user_parts += [
                "=== STYLE BIBLE (follow this; Easy English family template) ===",
                bible[:4500],
                "=== END STYLE BIBLE ===",
                "",
            ]
    user_parts += [
        "JSON shape:",
        '{',
        '  "title": "string",',
        '  "beats": [',
        '    {',
        '      "index": 1,',
        '      "target_sec": 8,',
        '      "dialogue": "Speaker lines for this beat",',
        '      "location": "short place",',
        '      "characters": "CharacterA, CharacterB",',
        '      "image_prompt": "Still frame description WITHOUT style brand words (cast + action + place)",',
        '      "i2v_prompt": "Short motion prompt: camera + character action for image-to-video"',
        '    }',
        '  ]',
        '}',
        "",
        "HARD CONSTRAINTS (do not violate):",
        "- Follow the user's story / script. Do NOT invent a different plot, conflict, or ending.",
        "- image_prompt: who is in frame, expression, props, location. No brand style words (Pixar, anime studio names, etc.).",
        "- i2v_prompt: subtle motion only (turn head, speak, walk slowly, camera push-in).",
        "- Keep included cast visually consistent; omit characters the user turned off.",
        "- Prefer the user's title when given.",
    ]
    if family_mode:
        user_parts.append(
            "- dialogue: CEFR A1–A2 per style bible. Short turns. Explicit feelings."
        )
        user_parts.append(
            "- Title may follow bible title patterns (question / mistake / hook) unless user title is given."
        )
    else:
        user_parts.append(
            "- dialogue: match the tone of the user's story/script (any genre). Natural spoken lines."
        )

    if pmode == "preview":
        user_parts += [
            "",
            "PACK MODE: FIRST-MINUTE PREVIEW ONLY.",
            f"- Produce exactly {n_beats} beats covering ONLY the opening (~1 minute).",
            "- Setup + first turn of the story. Do NOT rush to the ending yet.",
            "- Leave room for the rest of the episode later.",
        ]
        if moral_text:
            user_parts.append("- Do NOT state the optional takeaway/moral yet in this preview.")
    elif moral_text:
        user_parts.append(
            "- If a takeaway/moral is provided, land it clearly by the end (in dialogue or action)."
        )

    if (title or "").strip():
        user_parts.append(f"Working title: {title.strip()}")
    if story_text:
        user_parts.append(f"USER STORY (plot they chose — source of truth for what happens):\n{story_text}")
    if moral_text:
        user_parts.append(f"OPTIONAL TAKEAWAY (include if it fits):\n{moral_text}")
    # Legacy mistake_by — only if caller still sends it
    mistake_line = _format_mistake_constraint(mistake_by, cast)
    if mistake_line and family_mode:
        user_parts.append(mistake_line)
    if mode == "paste" and (script or "").strip():
        body = script.strip()
        if len(body) > 12000:
            body = body[:12000] + "\n…[truncated]"
        user_parts.append(
            "DIALOGUE MODE: paste. Use this script as the source of truth for dialogue. "
            "Split it into beats; story/cast guide scene splits and image prompts:\n"
            + body
        )
    elif (script or "").strip() and not story_text:
        body = script.strip()
        if len(body) > 12000:
            body = body[:12000] + "\n…[truncated]"
        user_parts.append("Use this script as the source of truth:\n" + body)
    else:
        if family_mode:
            user_parts.append(
                "DIALOGUE MODE: generate. Write A1–A2 dialogue FROM the user's story + cast. "
                "Do not change the plot. Expand into the requested beat count with natural scene pacing."
            )
        else:
            user_parts.append(
                "DIALOGUE MODE: generate. Write dialogue FROM the user's story + cast. "
                "Do not change the plot. Expand into the requested beat count with natural scene pacing."
            )

    raw = generate_text(
        "\n".join(user_parts),
        system=system,
        max_tokens=16000,
        temperature=0.4,
    )
    data = _parse_json_obj(raw)
    if not data or not isinstance(data.get("beats"), list):
        raise RuntimeError("Storyboard LLM did not return a valid beat sheet JSON.")

    out_title = (data.get("title") or title or story_text or topic or "Storyboard Pack").strip()
    beats: list[Beat] = []
    for i, row in enumerate(data["beats"], start=1):
        if not isinstance(row, dict):
            continue
        dialogue = str(row.get("dialogue") or "").strip()
        image_prompt = str(row.get("image_prompt") or "").strip()
        i2v_prompt = str(row.get("i2v_prompt") or "").strip()
        if not image_prompt:
            continue
        try:
            tsec = float(row.get("target_sec") or SEC_PER_BEAT)
        except (TypeError, ValueError):
            tsec = SEC_PER_BEAT
        tsec = max(4.0, min(tsec, 15.0))
        beats.append(
            Beat(
                index=int(row.get("index") or i),
                target_sec=tsec,
                dialogue=dialogue,
                image_prompt=image_prompt,
                i2v_prompt=i2v_prompt or "Subtle natural motion, gentle camera push-in, characters breathe and blink",
                location=str(row.get("location") or "").strip(),
                characters=str(row.get("characters") or "").strip(),
            )
        )

    if len(beats) < 4:
        raise RuntimeError(f"Beat sheet too short ({len(beats)} scenes). Try again.")

    # Re-index sequentially
    for i, b in enumerate(beats, start=1):
        b.index = i

    log(f"Beat sheet ready: {len(beats)} scenes — {out_title}")
    return out_title, beats


def generate_story_ideas(
    *,
    seed: str = "",
    count: int = 8,
    template: str = "",
) -> list[dict[str, str]]:
    """Cheap ideation for Storyboard Pack — title + premise (+ optional moral)."""
    from core.atlas_llm import generate_text
    from core.script_gen import _extract_json_array

    n = max(3, min(int(count or 8), 12))
    tmpl = (template or "").strip().lower()
    if tmpl == "easy_english_family":
        bible = load_style_bible()
        parts = [
            f"Generate exactly {n} NEW Easy English family dialogue story ideas for YouTube.",
            "Each idea needs: title (bible title patterns), premise (1–2 sentences), moral (one short line).",
            "Invent fresh kid/parent casts — do not require Max/Mia.",
            "Do not copy known competitor plots.",
            'Return ONLY a JSON array of objects: {"title","premise","moral"}.',
        ]
        if bible:
            parts += ["", "STYLE BIBLE:", bible[:3500], ""]
        system = "You invent viral-ready A1–A2 family story hooks. Output JSON array only."
    else:
        parts = [
            f"Generate exactly {n} NEW animation story ideas suitable for long image-to-video packs.",
            "Any genre is fine (adventure, comedy, fantasy, slice-of-life, sci-fi, etc.).",
            "Each idea needs: title, premise (1–2 sentences), optional moral (one short line or empty).",
            "Invent original characters — do not assume a fixed stock cast.",
            'Return ONLY a JSON array of objects: {"title","premise","moral"}.',
        ]
        system = "You invent original animation story hooks. Output JSON array only."
    if (seed or "").strip():
        parts.append(f"Optional creative seed from the user: {seed.strip()[:500]}")

    raw = generate_text(
        "\n".join(parts),
        system=system,
        max_tokens=2500,
        temperature=0.7,
    )
    arr = _extract_json_array(raw) or []
    out: list[dict[str, str]] = []
    for row in arr:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        premise = str(row.get("premise") or "").strip()
        moral = str(row.get("moral") or "").strip()
        if title and premise:
            out.append({"title": title, "premise": premise, "moral": moral})
    return out[:n]


def _compact_image_prompt(beat: Beat, cast_lock: str = "", style_short: str = "") -> str:
    """ERNIE ~500 char hard limit."""
    short = style_short or STYLE_SHORT
    cast = (beat.characters or "characters").strip()
    loc = (beat.location or "indoor").strip()
    scene = beat.image_prompt.strip()
    lock = (cast_lock or "")[:120]
    base = f"{short}. {cast} at {loc}. {scene}"
    if lock:
        base = f"{base} Looks: {lock}"
    if len(base) <= 480:
        return base
    return (short + ". " + scene)[:480]


def _full_image_prompt(beat: Beat, cast_lock: str = "", style_lock: str = "") -> str:
    lock = style_lock or STYLE_LOCK
    cast = (beat.characters or "").strip()
    loc = (beat.location or "").strip()
    parts = [lock]
    if cast:
        parts.append(f"Characters: {cast}.")
    if loc:
        parts.append(f"Location: {loc}.")
    parts.append(beat.image_prompt.strip())
    if cast_lock:
        parts.append(f"Cast look lock: {cast_lock}")
    return " ".join(parts)[:1200]


def _generate_still(
    beat: Beat,
    out_path: Path,
    *,
    cast_lock: str = "",
    visual_style: str = "",
) -> Beat:
    from core.illustration_gen import generate_single_illustration

    sid, style_lock, style_short = resolve_visual_style(visual_style)
    label = VISUAL_STYLE_PRESETS.get(sid, {}).get("label", "animation")
    short = _compact_image_prompt(beat, cast_lock, style_short=style_short)
    full = _full_image_prompt(beat, cast_lock, style_lock=style_lock)
    if sid != "semi_realistic":
        neg = (
            "Fully stylized animation — NOT photorealistic, NOT a real photograph, NOT real people."
        )
        full = f"{label} animation still. {full} {neg}"
        short = f"{label}. {short}"[:480]
    result = generate_single_illustration(
        full,
        str(out_path),
        short_prompt=short,
    )
    if result.success and out_path.is_file():
        beat.image_path = str(out_path)
        beat.error = ""
    else:
        beat.error = result.error or "image generation failed"
    return beat


StillReadyFn = Callable[[Beat], None]


def _style_label(visual_style: str = "") -> str:
    sid, _, _ = resolve_visual_style(visual_style)
    return VISUAL_STYLE_PRESETS.get(sid, {}).get("label", "3D Pixar-lite")


def _cast_ref_local_paths(cast: list[dict[str, Any]] | None) -> dict[str, str]:
    """Map cast id → best local reference image (sheet preferred, else portrait)."""
    refs: dict[str, str] = {}
    for row in normalize_cast(cast):
        if not row.get("included", True):
            continue
        cid = str(row.get("id") or "").strip().lower()
        for key in ("sheet_path", "portrait_path"):
            p = (row.get(key) or "").strip()
            if p and Path(p).is_file():
                refs[cid] = p
                break
    return refs


def _http_url(value: str) -> str:
    u = (value or "").strip()
    return u if u.startswith("http://") or u.startswith("https://") else ""


def _upload_cast_refs(cast: list[dict[str, Any]] | None) -> dict[str, str]:
    """Resolve cast references → {cid: remote_url}.

    Prefer existing public portrait/sheet URLs (regen on web has these).
    Otherwise upload local sheet/portrait paths (cook machines).
    """
    out: dict[str, str] = {}
    for row in normalize_cast(cast):
        if not row.get("included", True):
            continue
        cid = str(row.get("id") or "").strip().lower()
        if not cid:
            continue
        for key in ("sheet_url", "portrait_url"):
            url = _http_url(str(row.get(key) or ""))
            if url:
                out[cid] = url
                break
        if cid in out:
            continue
        for key in ("sheet_path", "portrait_path"):
            p = (row.get(key) or "").strip()
            if p and Path(p).is_file():
                try:
                    from core.thumbnail_gen import _upload_media
                    url = _upload_media(p)
                    if url and url.startswith("http"):
                        out[cid] = url
                except Exception as e:
                    print(f"[storyboard] cast ref upload failed ({cid}): {e}")
                break
    return out


def _beat_ref_urls(beat: Beat, cast: list[dict[str, Any]] | None, ref_urls: dict[str, str]) -> list[str]:
    """Pick reference URLs for the characters present in this beat (max 3)."""
    if not ref_urls:
        return []
    chars = (beat.characters or "").lower()
    picked: list[str] = []
    name_to_cid = {}
    for row in normalize_cast(cast):
        cid = str(row.get("id") or "").strip().lower()
        name = str(row.get("name") or "").strip().lower()
        if name:
            name_to_cid[name] = cid
    # Match by name mentioned in beat.characters
    for name, cid in name_to_cid.items():
        if name and name in chars and cid in ref_urls and ref_urls[cid] not in picked:
            picked.append(ref_urls[cid])
    # Fallback: if nothing matched, use all refs (cap 3)
    if not picked:
        picked = list(ref_urls.values())
    return picked[:3]


def _scene_edit_prompt(beat: Beat, style_label: str, visual_style: str) -> str:
    """Prompt for the reference-conditioned edit model — locks style + identity."""
    sid, _, _ = resolve_visual_style(visual_style)
    parts = [
        f"Create a single {style_label} animation still (16:9).",
        _style_exclusivity(visual_style),
        "The provided images include CHARACTER REFERENCES and may include STYLE ANCHOR stills "
        "from the same episode — match that exact look.",
        "Keep every character's face, hair, body, and outfit IDENTICAL to their character reference.",
    ]
    if sid != "semi_realistic":
        parts.append(
            "Render fully in the animated style — NOT photorealistic, NOT a real photograph, "
            "NOT real people. Stylized animation only."
        )
    if (beat.characters or "").strip():
        parts.append(f"Characters in frame: {beat.characters.strip()}.")
    if (beat.location or "").strip():
        parts.append(f"Location: {beat.location.strip()}.")
    parts.append(f"Scene: {beat.image_prompt.strip()}")
    parts.append("No text, no subtitles, no watermark, no logos.")
    return " ".join(parts)[:1400]


def _generate_still_edit(
    beat: Beat,
    out_path: Path,
    ref_urls: list[str],
    *,
    visual_style: str = "",
) -> bool:
    """Reference-conditioned scene generation via nano-banana edit. Returns success."""
    if not ref_urls:
        return False
    try:
        from core.thumbnail_gen import _EDIT_MODELS, _submit_and_download
    except Exception as e:
        print(f"[storyboard] edit helpers unavailable: {e}")
        return False
    style_label = _style_label(visual_style)
    prompt = _scene_edit_prompt(beat, style_label, visual_style)
    jpg = out_path if out_path.suffix.lower() in (".jpg", ".jpeg") else out_path.with_suffix(".jpg")
    for model in _EDIT_MODELS:
        payload = {
            "model": model,
            "prompt": prompt,
            "images": ref_urls[:4],
            "aspect_ratio": "16:9",
            "resolution": "1k",
            "output_format": "jpeg",
            "enable_base64_output": False,
        }
        try:
            _submit_and_download(payload, jpg, label=f"scene {beat.index:03d} edit/{model}")
            if jpg.is_file():
                beat.image_path = str(jpg)
                beat.error = ""
                return True
        except Exception as e:
            msg = str(e)
            print(f"[storyboard] scene {beat.index:03d} edit failed ({model}): {msg[:120]}")
            if "insufficient balance" in msg.lower():
                break
            continue
    return False


def _generate_still_t2i(
    beat: Beat,
    out_path: Path,
    *,
    cast_lock: str = "",
    visual_style: str = "",
) -> bool:
    """Nano-banana T2I fallback that keeps the chosen style (avoids ERNIE anime drift)."""
    sid, style_lock, _ = resolve_visual_style(visual_style)
    label = VISUAL_STYLE_PRESETS.get(sid, {}).get("label", "3D Pixar-lite")
    parts = [
        f"{label} animation still, 16:9.",
        style_lock,
        _style_exclusivity(visual_style),
    ]
    if (beat.characters or "").strip():
        parts.append(f"Characters: {beat.characters.strip()}.")
    if (beat.location or "").strip():
        parts.append(f"Location: {beat.location.strip()}.")
    parts.append((beat.image_prompt or "").strip())
    if cast_lock:
        parts.append(f"Cast look lock: {cast_lock}")
    prompt = " ".join(p for p in parts if p)[:1400]
    jpg = out_path if out_path.suffix.lower() in (".jpg", ".jpeg") else out_path.with_suffix(".jpg")
    try:
        from core.thumbnail_gen import _T2I_MODELS, _submit_and_download
    except Exception as e:
        print(f"[storyboard] still T2I helpers unavailable: {e}")
        return False
    jpg.parent.mkdir(parents=True, exist_ok=True)
    for model in _T2I_MODELS:
        payload = {
            "model": model,
            "prompt": prompt,
            "aspect_ratio": "16:9",
            "resolution": "1k",
            "output_format": "jpeg",
            "enable_base64_output": False,
        }
        try:
            _submit_and_download(payload, jpg, label=f"scene {beat.index:03d} t2i/{model}")
            if jpg.is_file():
                beat.image_path = str(jpg)
                beat.error = ""
                return True
        except Exception as e:
            print(f"[storyboard] scene {beat.index:03d} T2I failed ({model}): {e}")
            if "insufficient balance" in str(e).lower():
                break
            continue
    return False


def generate_stills(
    beats: list[Beat],
    images_dir: Path,
    *,
    progress: ProgressFn | None = None,
    max_workers: int = 4,
    cast: list[dict[str, Any]] | None = None,
    on_still: StillReadyFn | None = None,
    visual_style: str = "",
) -> list[Beat]:
    log = progress or (lambda _m: None)
    images_dir.mkdir(parents=True, exist_ok=True)
    total = len(beats)
    cast_lock = _cast_look_lock(cast)

    # Upload cast references once so every scene can lock to the same faces/style.
    ref_urls = _upload_cast_refs(cast)
    if ref_urls:
        log(f"Locked {len(ref_urls)} character reference(s) — scenes will match them…")
    else:
        log("No character references uploaded — falling back to text-only stills.")
    log(f"Generating {total} scene stills…")

    done = 0

    def _one(b: Beat) -> Beat:
        path = images_dir / f"{b.index:03d}_scene.jpg"
        # 1) Preferred: reference-conditioned edit (locks style + identity)
        beat_refs = _beat_ref_urls(b, cast, ref_urls)
        if beat_refs and _generate_still_edit(b, path, beat_refs, visual_style=visual_style):
            return b
        # 2) Style-locked nano-banana T2I (avoids ERNIE anime/photo drift)
        if _generate_still_t2i(b, path, cast_lock=cast_lock, visual_style=visual_style):
            return b
        # 3) Last resort: ERNIE / GPT Image text-only
        png = images_dir / f"{b.index:03d}_scene.png"
        updated = _generate_still(b, png, cast_lock=cast_lock, visual_style=visual_style)
        if updated.image_path and Path(updated.image_path).suffix.lower() == ".png":
            try:
                from PIL import Image
                im = Image.open(updated.image_path).convert("RGB")
                im.save(path, "JPEG", quality=88)
                Path(updated.image_path).unlink(missing_ok=True)
                updated.image_path = str(path)
            except Exception:
                if png.is_file() and not path.is_file():
                    png.rename(path.with_suffix(".png"))
                    updated.image_path = str(path.with_suffix(".png"))
        return updated

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_one, b): b.index for b in beats}
        for fut in as_completed(futs):
            updated = fut.result()
            idx = updated.index
            for i, b in enumerate(beats):
                if b.index == idx:
                    beats[i] = updated
                    break
            done += 1
            if on_still and updated.image_path:
                try:
                    on_still(updated)
                except Exception as cb_err:
                    print(f"[storyboard] on_still callback failed: {cb_err}")
            if done % 5 == 0 or done == total or (on_still and updated.image_path):
                ok = sum(1 for b in beats if b.image_path)
                log(f"Stills {done}/{total} ({ok} ok)…")

    failed = [b for b in beats if not b.image_path]
    if failed and len(failed) == len(beats):
        raise RuntimeError("All still generations failed. Check Atlas image keys.")
    if failed:
        log(f"Warning: {len(failed)} stills failed — pack will skip those indices.")
    return beats


def regenerate_beat_still(
    beat: Beat,
    out_path: Path,
    *,
    cast: list[dict[str, Any]] | None = None,
    note: str = "",
    visual_style: str = "",
    style_anchor_urls: list[str] | None = None,
) -> Beat:
    """Regenerate a single scene still (UI: fix one weak frame)."""
    direction = (note or "").strip()
    if direction:
        beat.image_prompt = (
            f"{(beat.image_prompt or '').strip()} "
            f"REVISION REQUEST (follow this): {direction}"
        ).strip()
    cast_lock = _cast_look_lock(cast)
    jpg = out_path if out_path.suffix.lower() in (".jpg", ".jpeg") else out_path.with_suffix(".jpg")

    # Character refs (public URLs or uploaded locals) + neighbor stills as style anchors.
    ref_urls = _upload_cast_refs(cast)
    beat_refs = _beat_ref_urls(beat, cast, ref_urls)
    anchors: list[str] = []
    for u in style_anchor_urls or []:
        hu = _http_url(str(u))
        if hu and hu not in beat_refs and hu not in anchors:
            anchors.append(hu)
        if len(anchors) >= 2:
            break
    edit_refs = (beat_refs + anchors)[:4]
    if edit_refs and _generate_still_edit(beat, jpg, edit_refs, visual_style=visual_style):
        return beat

    # Style-locked nano-banana T2I — do NOT fall straight to ERNIE (anime drift).
    if _generate_still_t2i(beat, jpg, cast_lock=cast_lock, visual_style=visual_style):
        return beat

    png = out_path if out_path.suffix.lower() == ".png" else out_path.with_suffix(".png")
    updated = _generate_still(beat, png, cast_lock=cast_lock, visual_style=visual_style)
    if updated.image_path and Path(updated.image_path).suffix.lower() == ".png":
        try:
            from PIL import Image
            im = Image.open(updated.image_path).convert("RGB")
            im.save(jpg, "JPEG", quality=88)
            Path(updated.image_path).unlink(missing_ok=True)
            updated.image_path = str(jpg)
        except Exception:
            pass
    return updated


def _prompt_txt(beat: Beat) -> str:
    lines = [
        f"# Scene {beat.index:03d}",
        f"target_sec: {beat.target_sec:.1f}",
        f"location: {beat.location}",
        f"characters: {beat.characters}",
        "",
        "## Dialogue (for assembly / captions)",
        beat.dialogue or "(no dialogue)",
        "",
        "## Image-to-video prompt (paste with the image)",
        beat.i2v_prompt,
        "",
        "## Still description (reference)",
        beat.image_prompt,
        "",
        "## Continuity tip (Kling start/end frame)",
        "Use this still as the start frame. For a seamless take into the next scene, "
        "use the next scene's still as the end frame (or extract the last frame of this clip "
        "as the next clip's start frame).",
    ]
    return "\n".join(lines) + "\n"


def write_pack_files(
    *,
    pack_dir: Path,
    title: str,
    beats: list[Beat],
    target_minutes: float,
    thumbnail_path: str = "",
    cast: list[dict] | None = None,
) -> None:
    pack_dir.mkdir(parents=True, exist_ok=True)
    ok_beats = [b for b in beats if b.image_path and Path(b.image_path).is_file()]

    # Copy/move images into pack root with stable names
    for b in ok_beats:
        src = Path(b.image_path)
        ext = src.suffix.lower() if src.suffix else ".jpg"
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            ext = ".jpg"
        dest = pack_dir / f"{b.index:03d}_scene{ext}"
        if src.resolve() != dest.resolve():
            dest.write_bytes(src.read_bytes())
            b.image_path = str(dest)
        (pack_dir / f"{b.index:03d}_prompt.txt").write_text(_prompt_txt(b), encoding="utf-8")

    thumb_name = ""
    if thumbnail_path and Path(thumbnail_path).is_file():
        src = Path(thumbnail_path)
        ext = src.suffix.lower() if src.suffix else ".png"
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            ext = ".png"
        dest = pack_dir / f"thumbnail{ext}"
        dest.write_bytes(src.read_bytes())
        thumb_name = dest.name

    cast_sheet_names: list[str] = []
    sheets_dir = pack_dir / "cast"
    for row in normalize_cast(cast):
        if not row.get("included", True):
            continue
        cid = str(row.get("id") or "char").strip().lower() or "char"
        name = str(row.get("name") or cid).strip() or cid
        for kind, key in (("portrait", "portrait_path"), ("sheet", "sheet_path")):
            src_s = (row.get(key) or "").strip()
            if not src_s or not Path(src_s).is_file():
                # try fetching later is cook_runner's job; skip missing local
                continue
            sheets_dir.mkdir(parents=True, exist_ok=True)
            src = Path(src_s)
            ext = src.suffix.lower() if src.suffix else ".png"
            if ext not in (".jpg", ".jpeg", ".png", ".webp"):
                ext = ".png"
            dest = sheets_dir / f"{cid}_{kind}{ext}"
            dest.write_bytes(src.read_bytes())
            cast_sheet_names.append(f"cast/{dest.name}")

    # MANIFEST.csv
    manifest = pack_dir / "MANIFEST.csv"
    with manifest.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([
            "index", "filename", "prompt_file", "target_sec",
            "dialogue", "i2v_prompt", "location", "characters",
        ])
        for b in ok_beats:
            img_name = Path(b.image_path).name
            w.writerow([
                f"{b.index:03d}",
                img_name,
                f"{b.index:03d}_prompt.txt",
                f"{b.target_sec:.1f}",
                (b.dialogue or "").replace("\n", " ").strip(),
                (b.i2v_prompt or "").replace("\n", " ").strip(),
                b.location,
                b.characters,
            ])

    # ALL_PROMPTS.txt
    all_lines = [f"# {title}", f"# {len(ok_beats)} scenes · target ~{target_minutes:.0f} min", ""]
    for b in ok_beats:
        all_lines += [
            f"===== {b.index:03d} ({b.target_sec:.0f}s) =====",
            b.i2v_prompt,
            "",
            f"[dialogue] {b.dialogue}",
            "",
        ]
    (pack_dir / "ALL_PROMPTS.txt").write_text("\n".join(all_lines), encoding="utf-8")

    thumb_line = f"- `{thumb_name}` — YouTube thumbnail\n" if thumb_name else ""
    cast_line = ""
    if cast_sheet_names:
        cast_line = "- `cast/` — character portraits & sheets for I2V consistency\n"
        for n in cast_sheet_names:
            cast_line += f"- `{n}`\n"
    readme = f"""# {title}

Storyboard Pack for image-to-video batching.

## How to use
1. Open your image-to-video tool (Seedance, Wan, Kling, etc.).
2. For each scene in order (`001`, `002`, …):
   - Drop `NNN_scene.jpg` (or `.png`) as the image.
   - Paste the **Image-to-video prompt** from `NNN_prompt.txt` (or from `ALL_PROMPTS.txt`).
3. Keep download filenames starting with `001_`, `002_`, … if the tool allows — ChannelRecipe assemble will sort by that number.
4. Target length per clip is in `MANIFEST.csv` (`target_sec`). Clips may come back shorter/longer; that is OK.
5. Upload `{thumb_name or "thumbnail.png"}` as the YouTube thumbnail (if included).

## Files
- `MANIFEST.csv` — index, filenames, dialogue, prompts, timings
- `NNN_scene.*` — still frame
- `NNN_prompt.txt` — dialogue + I2V prompt for that scene
- `ALL_PROMPTS.txt` — all I2V prompts in one file
{thumb_line}{cast_line}
Generated {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}.
"""
    (pack_dir / "README.md").write_text(readme, encoding="utf-8")

    # metadata for future assemble
    meta = {
        "title": title,
        "target_minutes": target_minutes,
        "beat_count": len(ok_beats),
        "thumbnail": thumb_name,
        "cast_sheets": cast_sheet_names,
        "beats": [
            {
                "index": b.index,
                "target_sec": b.target_sec,
                "dialogue": b.dialogue,
                "i2v_prompt": b.i2v_prompt,
                "image_prompt": b.image_prompt,
                "location": b.location,
                "characters": b.characters,
                "filename": Path(b.image_path).name,
            }
            for b in ok_beats
        ],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (pack_dir / "pack.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def zip_pack(pack_dir: Path, zip_path: Path) -> Path:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    folder_name = pack_dir.name
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(pack_dir.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=str(Path(folder_name) / path.relative_to(pack_dir)))
    return zip_path


def build_storyboard_pack(
    *,
    title: str = "",
    topic: str = "",
    script: str = "",
    target_minutes: float = 8,
    story: str = "",
    moral: str = "",
    cast: list[dict[str, Any]] | None = None,
    mistake_by: str = "",
    dialogue_mode: str = "generate",
    thumbnail_path: str = "",
    pack_mode: str = "full",
    visual_style: str = "",
    template: str = "",
    out_root: str | Path | None = None,
    progress: ProgressFn | None = None,
    on_still: StillReadyFn | None = None,
    is_admin: bool = True,
    is_paid: bool = False,
    max_workers: int = 4,
) -> dict[str, Any]:
    """
    Full pack build. Returns paths + summary for cook_runner / API.
    """
    log = progress or (lambda _m: None)
    pmode = (pack_mode or "full").strip().lower()
    if pmode not in ("preview", "full"):
        pmode = "full"
    style_id, _, _ = resolve_visual_style(visual_style)
    tmpl = (template or "").strip().lower()
    # Preview always targets ~1 minute of scenes regardless of slider
    mins_in = 1.0 if pmode == "preview" else target_minutes
    mins = clamp_minutes(mins_in, is_admin=is_admin, is_paid=is_paid)
    cast_norm = normalize_cast(cast)
    root = Path(out_root or (Path(__file__).resolve().parents[1] / "output" / "storyboard_packs"))
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    work = root / f"_work_{stamp}_{_slug(title or story or topic or 'pack')}"
    work.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    out_title, beats = generate_beat_sheet(
        title=title,
        topic=topic,
        script=script,
        target_minutes=mins,
        story=story or topic,
        moral=moral,
        cast=cast_norm,
        mistake_by=mistake_by,
        dialogue_mode=dialogue_mode,
        pack_mode=pmode,
        visual_style=style_id,
        template=tmpl,
        progress=log,
    )
    images_dir = work / "raw_images"
    beats = generate_stills(
        beats,
        images_dir,
        progress=log,
        max_workers=max_workers,
        cast=cast_norm,
        on_still=on_still,
        visual_style=style_id,
    )

    pack_name = f"{_slug(out_title)}_{stamp}_pack"
    pack_dir = root / pack_name
    log("Writing pack files…")
    write_pack_files(
        pack_dir=pack_dir,
        title=out_title,
        beats=beats,
        target_minutes=mins,
        thumbnail_path=thumbnail_path,
        cast=cast_norm,
    )

    zip_path = root / f"{pack_name}.zip"
    log("Zipping…")
    zip_pack(pack_dir, zip_path)

    ok = [b for b in beats if b.image_path]
    log(f"Done — {len(ok)} scenes in {time.time() - t0:.0f}s")

    # Cleanup work dir (best effort) — keep beat files that were copied into pack_dir
    try:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    except Exception:
        pass

    return {
        "title": out_title,
        "pack_dir": str(pack_dir),
        "zip_path": str(zip_path),
        "beat_count": len(ok),
        "target_minutes": mins,
        "pack_mode": pmode,
        "visual_style": style_id,
        "template": tmpl,
        "scene_files": [Path(b.image_path).name for b in ok],
        "manifest_path": str(pack_dir / "MANIFEST.csv"),
        "output_path": str(zip_path),
        "beats": [
            {
                "index": b.index,
                "target_sec": b.target_sec,
                "dialogue": b.dialogue,
                "i2v_prompt": b.i2v_prompt,
                "image_prompt": b.image_prompt,
                "location": b.location,
                "characters": b.characters,
                "filename": Path(b.image_path).name if b.image_path else "",
                "image_path": b.image_path,
            }
            for b in ok
        ],
        "cast": cast_norm,
    }
