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

STYLE_LOCK = (
    "3D Pixar-lite animation style, soft global illumination, large expressive eyes, "
    "smooth skin, clean textures, shallow depth of field, 16:9 widescreen, "
    "no text, no subtitles, no watermark, no logos"
)

STYLE_SHORT = (
    "3D Pixar-lite, soft light, expressive eyes, 16:9, no text/subtitles"
)

DEFAULT_CAST = (
    "Max: boy messy black hair blue polo grey pants. "
    "Mia: girl black pigtails pink ties yellow sweater. "
    "Mom: long wavy brown hair light cardigan. "
    "Dad: curly dark hair short beard blue shirt."
)


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


def _beat_count_for_minutes(minutes: float) -> int:
    total_sec = max(60.0, minutes * 60.0)
    n = int(round(total_sec / SEC_PER_BEAT))
    return max(8, min(n, 240))  # hard ceiling ~32 min @ 8s


def generate_beat_sheet(
    *,
    title: str,
    topic: str,
    script: str,
    target_minutes: float,
    progress: ProgressFn | None = None,
) -> tuple[str, list[Beat]]:
    """LLM → title + ordered beats."""
    from core.atlas_llm import generate_text

    log = progress or (lambda _m: None)
    n_beats = _beat_count_for_minutes(target_minutes)
    log(f"Planning ~{n_beats} scenes for {target_minutes:.0f} min…")

    system = (
        "You write A1–A2 easy-English family dialogue storyboards for YouTube. "
        "Output ONLY valid JSON. No markdown."
    )
    user_parts = [
        f"Target runtime: {target_minutes:.1f} minutes.",
        f"Produce exactly {n_beats} beats (scenes).",
        f"Each beat target_sec should be about {SEC_PER_BEAT:.0f} seconds (5–12 ok).",
        "Sum of target_sec should be close to the target runtime.",
        "Style: slow clear dialogue, moral/school life lesson, recurring cast Max/Mia/Mom/Dad.",
        f"Default cast look: {DEFAULT_CAST}",
        "",
        "JSON shape:",
        '{',
        '  "title": "string",',
        '  "beats": [',
        '    {',
        '      "index": 1,',
        '      "target_sec": 8,',
        '      "dialogue": "Speaker lines for this beat",',
        '      "location": "short place",',
        '      "characters": "Max, Mia",',
        '      "image_prompt": "Still frame description WITHOUT style words (cast + action + place)",',
        '      "i2v_prompt": "Short motion prompt: camera + character action for image-to-video"',
        '    }',
        '  ]',
        '}',
        "",
        "Rules:",
        "- image_prompt: who is in frame, expression, props, location. No 'Pixar' word.",
        "- i2v_prompt: subtle motion only (turn head, speak, walk slowly, camera push-in).",
        "- dialogue: CEFR A1–A2. Include speaker names when useful.",
        "- Keep Max/Mia/Mom/Dad visually consistent with the cast look.",
    ]
    if (title or "").strip():
        user_parts.append(f"Working title: {title.strip()}")
    if (topic or "").strip():
        user_parts.append(f"Story idea / topic: {topic.strip()}")
    if (script or "").strip():
        # Cap huge pastes
        body = script.strip()
        if len(body) > 12000:
            body = body[:12000] + "\n…[truncated]"
        user_parts.append("Use this script as the source of truth:\n" + body)
    else:
        user_parts.append("No full script provided — invent a complete dialogue story from the idea.")

    raw = generate_text(
        "\n".join(user_parts),
        system=system,
        max_tokens=16000,
        temperature=0.4,
    )
    data = _parse_json_obj(raw)
    if not data or not isinstance(data.get("beats"), list):
        raise RuntimeError("Storyboard LLM did not return a valid beat sheet JSON.")

    out_title = (data.get("title") or title or topic or "Storyboard Pack").strip()
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


def _compact_image_prompt(beat: Beat) -> str:
    """ERNIE ~500 char hard limit."""
    cast = (beat.characters or "Max Mia").strip()
    loc = (beat.location or "indoor").strip()
    scene = beat.image_prompt.strip()
    base = f"{STYLE_SHORT}. {cast} at {loc}. {scene}"
    if len(base) <= 480:
        return base
    return (STYLE_SHORT + ". " + scene)[:480]


def _full_image_prompt(beat: Beat) -> str:
    cast = (beat.characters or "").strip()
    loc = (beat.location or "").strip()
    parts = [STYLE_LOCK]
    if cast:
        parts.append(f"Characters: {cast}.")
    if loc:
        parts.append(f"Location: {loc}.")
    parts.append(beat.image_prompt.strip())
    parts.append(f"Cast look lock: {DEFAULT_CAST}")
    return " ".join(parts)[:1200]


def _generate_still(beat: Beat, out_path: Path) -> Beat:
    from core.illustration_gen import generate_single_illustration

    short = _compact_image_prompt(beat)
    full = _full_image_prompt(beat)
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


def generate_stills(
    beats: list[Beat],
    images_dir: Path,
    *,
    progress: ProgressFn | None = None,
    max_workers: int = 4,
) -> list[Beat]:
    log = progress or (lambda _m: None)
    images_dir.mkdir(parents=True, exist_ok=True)
    total = len(beats)
    log(f"Generating {total} scene stills…")

    done = 0

    def _one(b: Beat) -> Beat:
        path = images_dir / f"{b.index:03d}_scene.jpg"
        # Prefer png from generator then convert? illustration_gen writes whatever path — use .png then rename
        png = images_dir / f"{b.index:03d}_scene.png"
        updated = _generate_still(b, png)
        if updated.image_path and Path(updated.image_path).suffix.lower() == ".png":
            # Convert to jpg for smaller packs if pillow available
            try:
                from PIL import Image
                im = Image.open(updated.image_path).convert("RGB")
                im.save(path, "JPEG", quality=88)
                Path(updated.image_path).unlink(missing_ok=True)
                updated.image_path = str(path)
            except Exception:
                # Keep png; rename to expected stem
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
            if done % 5 == 0 or done == total:
                ok = sum(1 for b in beats if b.image_path)
                log(f"Stills {done}/{total} ({ok} ok)…")

    failed = [b for b in beats if not b.image_path]
    if failed and len(failed) == len(beats):
        raise RuntimeError("All still generations failed. Check Atlas image keys.")
    if failed:
        log(f"Warning: {len(failed)} stills failed — pack will skip those indices.")
    return beats


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
    ]
    return "\n".join(lines) + "\n"


def write_pack_files(
    *,
    pack_dir: Path,
    title: str,
    beats: list[Beat],
    target_minutes: float,
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

    readme = f"""# {title}

Storyboard Pack for image-to-video batching.

## How to use
1. Open your image-to-video tool (Seedance, Wan, Kling, etc.).
2. For each scene in order (`001`, `002`, …):
   - Drop `NNN_scene.jpg` (or `.png`) as the image.
   - Paste the **Image-to-video prompt** from `NNN_prompt.txt` (or from `ALL_PROMPTS.txt`).
3. Keep download filenames starting with `001_`, `002_`, … if the tool allows — ChannelRecipe assemble will sort by that number.
4. Target length per clip is in `MANIFEST.csv` (`target_sec`). Clips may come back shorter/longer; that is OK.

## Files
- `MANIFEST.csv` — index, filenames, dialogue, prompts, timings
- `NNN_scene.*` — still frame
- `NNN_prompt.txt` — dialogue + I2V prompt for that scene
- `ALL_PROMPTS.txt` — all I2V prompts in one file

Generated {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}.
"""
    (pack_dir / "README.md").write_text(readme, encoding="utf-8")

    # metadata for future assemble
    meta = {
        "title": title,
        "target_minutes": target_minutes,
        "beat_count": len(ok_beats),
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
    out_root: str | Path | None = None,
    progress: ProgressFn | None = None,
    is_admin: bool = True,
    is_paid: bool = False,
    max_workers: int = 4,
) -> dict[str, Any]:
    """
    Full pack build. Returns paths + summary for cook_runner / API.
    """
    log = progress or (lambda _m: None)
    mins = clamp_minutes(target_minutes, is_admin=is_admin, is_paid=is_paid)
    root = Path(out_root or (Path(__file__).resolve().parents[1] / "output" / "storyboard_packs"))
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    work = root / f"_work_{stamp}_{_slug(title or topic or 'pack')}"
    work.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    out_title, beats = generate_beat_sheet(
        title=title,
        topic=topic,
        script=script,
        target_minutes=mins,
        progress=log,
    )
    images_dir = work / "raw_images"
    beats = generate_stills(beats, images_dir, progress=log, max_workers=max_workers)

    pack_name = f"{_slug(out_title)}_{stamp}_pack"
    pack_dir = root / pack_name
    log("Writing pack files…")
    write_pack_files(pack_dir=pack_dir, title=out_title, beats=beats, target_minutes=mins)

    zip_path = root / f"{pack_name}.zip"
    log("Zipping…")
    zip_pack(pack_dir, zip_path)

    ok = [b for b in beats if b.image_path]
    log(f"Done — {len(ok)} scenes in {time.time() - t0:.0f}s")

    # Cleanup work dir (best effort)
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
        "scene_files": [Path(b.image_path).name for b in ok],
        "manifest_path": str(pack_dir / "MANIFEST.csv"),
        "output_path": str(zip_path),  # alias for cook_runner upload path
    }
