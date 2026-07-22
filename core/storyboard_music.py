"""
Storyboard music beds — curated offline catalog (YouTube Audio Library style).

Pick one soft instrumental bed by mood, loop/trim to video length, mix under
dialogue at low volume. Never fails the cook if catalog is empty.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
MUSIC_DIR = ROOT / "assets" / "music"
CATALOG_PATH = MUSIC_DIR / "catalog.json"
TRACKS_DIR = MUSIC_DIR / "tracks"

MOODS = ("warm", "playful", "tension", "sad", "resolve")

# Soft bed relative to dialogue (matches explainer BGM ballpark)
_BGM_VOLUME = float(os.getenv("STORYBOARD_BGM_VOLUME", "0.10") or 0.10)

_MOOD_KEYWORDS: dict[str, tuple[str, ...]] = {
    "playful": (
        "fun", "funny", "laugh", "joke", "play", "happy", "silly", "party",
        "game", "adventure", "surprise party",
    ),
    "tension": (
        "worry", "scared", "afraid", "trouble", "mistake", "lost", "late",
        "angry", "fight", "problem", "danger", "secret", "passport", "miss",
    ),
    "sad": (
        "cry", "sad", "sorry", "lonely", "hurt", "miss you", "goodbye",
        "tears", "upset",
    ),
    "resolve": (
        "learn", "lesson", "together", "forgive", "fix", "hug", "apologize",
        "better", "promise", "end", "moral",
    ),
    "warm": (
        "family", "home", "love", "kind", "morning", "breakfast", "cozy",
        "gentle", "care",
    ),
}


def catalog_path() -> Path:
    return CATALOG_PATH


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    p = path or CATALOG_PATH
    if not p.is_file():
        return {"version": 1, "moods": list(MOODS), "tracks": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "moods": list(MOODS), "tracks": []}
    if not isinstance(data, dict):
        return {"version": 1, "moods": list(MOODS), "tracks": []}
    tracks = data.get("tracks")
    if not isinstance(tracks, list):
        data["tracks"] = []
    return data


def save_catalog(data: dict[str, Any], path: Path | None = None) -> None:
    p = path or CATALOG_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def resolve_track_path(entry: dict[str, Any]) -> Path | None:
    """Resolve catalog entry → local mp3 path."""
    rel = (entry.get("file") or entry.get("path") or "").strip()
    if not rel:
        return None
    cand = Path(rel)
    if not cand.is_absolute():
        cand = MUSIC_DIR / rel
    if cand.is_file() and cand.stat().st_size > 1000:
        return cand
    # Common layout: tracks/<filename>
    name = Path(rel).name
    alt = TRACKS_DIR / name
    if alt.is_file() and alt.stat().st_size > 1000:
        return alt
    return None


def infer_story_mood(
    title: str = "",
    beats: list[dict[str, Any]] | None = None,
    *,
    use_llm: bool = False,
) -> str:
    """
    Fast mood pick from title + beat dialogue/prompts.
    Defaults to warm (family-story baseline).
    """
    parts: list[str] = [title or ""]
    for b in beats or []:
        if not isinstance(b, dict):
            continue
        parts.append(str(b.get("dialogue") or ""))
        parts.append(str(b.get("image_prompt") or ""))
        parts.append(str(b.get("i2v_prompt") or ""))
    blob = " ".join(parts).lower()

    scores = {m: 0 for m in MOODS}
    for mood, words in _MOOD_KEYWORDS.items():
        for w in words:
            if w in blob:
                scores[mood] += 1 + blob.count(w)

    # Slight bias: early beats warm/playful, late beats resolve if present
    if beats and len(beats) >= 4:
        last = " ".join(
            str((b or {}).get("dialogue") or "") for b in beats[-max(2, len(beats) // 4):]
            if isinstance(b, dict)
        ).lower()
        if any(w in last for w in _MOOD_KEYWORDS["resolve"]):
            scores["resolve"] += 2

    best = max(scores, key=lambda m: scores[m])
    if scores[best] <= 0:
        best = "warm"

    if use_llm and scores[best] <= 1:
        try:
            from core.atlas_llm import generate_text
            sample = blob[:1200]
            raw = generate_text(
                f"Pick ONE mood for this kids/family story video music bed.\n"
                f"Options: {', '.join(MOODS)}\n"
                f"Reply with only the mood word.\n\nStory text:\n{sample}",
                system="You pick background music mood tags. Reply with one word only.",
                max_tokens=8,
                temperature=0.0,
            )
            word = re.sub(r"[^a-z]", "", (raw or "").strip().lower().split()[0])
            if word in MOODS:
                return word
        except Exception:
            pass
    return best


def pick_track(
    mood: str,
    *,
    catalog: dict[str, Any] | None = None,
    seed: str = "",
) -> tuple[Path | None, dict[str, Any] | None]:
    """
    Pick a track for mood. Falls back to warm, then any track.
    Uses seed (job_id) for stable variety across cooks.
    """
    data = catalog if catalog is not None else load_catalog()
    tracks = [t for t in (data.get("tracks") or []) if isinstance(t, dict)]
    if not tracks:
        return None, None

    mood = (mood or "warm").strip().lower()
    if mood not in MOODS:
        mood = "warm"

    def _pool(m: str) -> list[dict[str, Any]]:
        out = []
        for t in tracks:
            tags = t.get("moods") or t.get("mood") or []
            if isinstance(tags, str):
                tags = [tags]
            tags_l = {str(x).strip().lower() for x in tags}
            if m in tags_l or (not tags_l and m == "warm"):
                if resolve_track_path(t):
                    out.append(t)
        return out

    pool = _pool(mood) or _pool("warm") or [
        t for t in tracks if resolve_track_path(t)
    ]
    if not pool:
        return None, None

    if seed:
        h = int(hashlib.sha256(f"{seed}:{mood}".encode()).hexdigest()[:8], 16)
        entry = pool[h % len(pool)]
    else:
        entry = pool[0]
    path = resolve_track_path(entry)
    return path, entry


def mix_bgm_under_dialogue(
    video_in: str | Path,
    music_path: str | Path,
    video_out: str | Path,
    *,
    volume: float | None = None,
    timeout: int = 600,
) -> bool:
    """
    Loop/trim music to video length and mix under existing dialogue audio.
    Returns True on success.
    """
    vin = Path(video_in)
    music = Path(music_path)
    vout = Path(video_out)
    if not vin.is_file() or not music.is_file():
        return False
    vol = _BGM_VOLUME if volume is None else float(volume)
    vol = max(0.02, min(0.35, vol))
    vout.parent.mkdir(parents=True, exist_ok=True)

    # Dialogue = 0:a, music = 1:a (looped via -stream_loop). Mix under dialogue.
    filter_complex = (
        f"[1:a]volume={vol:.3f},aformat=sample_fmts=fltp:channel_layouts=stereo[bg];"
        f"[0:a]aformat=sample_fmts=fltp:channel_layouts=stereo[dlg];"
        f"[dlg][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(vin),
        "-stream_loop", "-1",
        "-i", str(music),
        "-filter_complex", filter_complex,
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(vout),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0 and vout.is_file() and vout.stat().st_size > 1000:
            return True
        # Retry with re-encode video if stream copy fails
        cmd_re = [
            "ffmpeg", "-y",
            "-i", str(vin),
            "-stream_loop", "-1",
            "-i", str(music),
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            str(vout),
        ]
        r2 = subprocess.run(cmd_re, capture_output=True, text=True, timeout=timeout)
        return r2.returncode == 0 and vout.is_file() and vout.stat().st_size > 1000
    except Exception as e:
        print(f"[sb-music] mix failed: {e}")
        return False


def apply_storyboard_music(
    video_path: str | Path,
    *,
    title: str = "",
    beats: list[dict[str, Any]] | None = None,
    seed: str = "",
    work_dir: str | Path | None = None,
    progress: Any = None,
) -> dict[str, Any]:
    """
    If catalog has tracks, mix a mood bed under dialogue.
    Returns meta; on skip/failure returns {applied: False} and leaves video unchanged.
    """
    log = progress if callable(progress) else (lambda _m: None)
    vin = Path(video_path)
    meta: dict[str, Any] = {"applied": False, "mood": "", "track": "", "file": ""}
    if not vin.is_file():
        return meta

    catalog = load_catalog()
    if not (catalog.get("tracks") or []):
        return meta

    mood = infer_story_mood(title, beats)
    path, entry = pick_track(mood, catalog=catalog, seed=seed or vin.stem)
    if not path or not entry:
        return meta

    log("Adding music…")
    work = Path(work_dir or vin.parent)
    work.mkdir(parents=True, exist_ok=True)
    mixed = work / f"{vin.stem}_with_music.mp4"
    ok = mix_bgm_under_dialogue(vin, path, mixed)
    if not ok:
        print(f"[sb-music] mix failed for {path.name}")
        return meta

    try:
        # Replace original in place
        tmp = vin.with_suffix(".mp4.tmp_music")
        shutil_move = __import__("shutil").move
        if vin.resolve() != mixed.resolve():
            shutil_move(str(mixed), str(tmp))
            vin.unlink(missing_ok=True)
            shutil_move(str(tmp), str(vin))
        meta = {
            "applied": True,
            "mood": mood,
            "track": entry.get("title") or path.stem,
            "file": entry.get("file") or path.name,
            "attribution": entry.get("attribution") or "",
        }
        print(f"[sb-music] mixed mood={mood} track={meta['track']}")
    except Exception as e:
        print(f"[sb-music] replace failed: {e}")
        if mixed.is_file():
            meta = {
                "applied": True,
                "mood": mood,
                "track": entry.get("title") or path.stem,
                "file": str(mixed),
                "output_path": str(mixed),
            }
    return meta
