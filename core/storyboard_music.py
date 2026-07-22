"""
Storyboard music beds — curated offline catalog (YouTube Audio Library style).

Pick a soft instrumental bed by mood, loop/trim to video length, mix under
dialogue at low volume. When clip durations are available, switch beds on
mood changes with short crossfades, returning to the story's main mood.
Never fails the cook if catalog is empty.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
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
_CROSSFADE_SEC = float(os.getenv("STORYBOARD_BGM_CROSSFADE", "1.4") or 1.4)
# Ignore brief mood digressions (stay on main bed) unless this long
_MIN_ALT_SEC = float(os.getenv("STORYBOARD_BGM_MIN_ALT_SEC", "8.0") or 8.0)

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


def _text_mood_scores(blob: str) -> dict[str, int]:
    blob = (blob or "").lower()
    scores = {m: 0 for m in MOODS}
    for mood, words in _MOOD_KEYWORDS.items():
        for w in words:
            if w in blob:
                scores[mood] += 1 + blob.count(w)
    return scores


def _beat_text(beat: dict[str, Any]) -> str:
    return " ".join([
        str(beat.get("dialogue") or ""),
        str(beat.get("image_prompt") or ""),
        str(beat.get("i2v_prompt") or ""),
        str(beat.get("narration") or ""),
    ])


def score_story_moods(
    title: str = "",
    beats: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    parts: list[str] = [title or ""]
    for b in beats or []:
        if isinstance(b, dict):
            parts.append(_beat_text(b))
    scores = _text_mood_scores(" ".join(parts))
    if beats and len(beats) >= 4:
        last = " ".join(
            _beat_text(b) for b in beats[-max(2, len(beats) // 4):]
            if isinstance(b, dict)
        )
        last_scores = _text_mood_scores(last)
        if last_scores.get("resolve", 0) > 0:
            scores["resolve"] += 2
    return scores


def infer_beat_mood(beat: dict[str, Any] | None, *, fallback: str = "warm") -> tuple[str, int]:
    """Return (mood, score) for a single beat. Low score → treat as fallback/main."""
    if not isinstance(beat, dict):
        return fallback, 0
    scores = _text_mood_scores(_beat_text(beat))
    best = max(scores, key=lambda m: scores[m])
    if scores[best] <= 0:
        return fallback, 0
    return best, scores[best]


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
    scores = score_story_moods(title, beats)
    # Main bed = story body mood. Resolve is an ending accent unless nothing else scored.
    body = {m: scores[m] for m in ("warm", "playful", "tension", "sad")}
    body_best = max(body, key=lambda m: body[m])
    if body[body_best] > 0:
        best = body_best
    elif scores.get("resolve", 0) > 0:
        best = "resolve"
    else:
        best = "warm"

    if use_llm and scores[best] <= 1:
        try:
            from core.atlas_llm import generate_text
            sample = " ".join(
                [title or ""] + [_beat_text(b) for b in (beats or []) if isinstance(b, dict)]
            )[:1200]
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


def plan_mood_segments(
    title: str,
    beats: list[dict[str, Any]],
    durations: list[float],
    *,
    min_alt_sec: float | None = None,
) -> list[dict[str, Any]]:
    """
    Build a timeline of mood segments aligned to clip durations.

    Uses the story's main mood as the default bed. Strong local mood signals
    switch to an alternate bed; short digressions stay on the main bed so we
    don't thrash. Consecutive same-mood beats collapse into one segment.
    """
    main = infer_story_mood(title, beats)
    min_alt = _MIN_ALT_SEC if min_alt_sec is None else float(min_alt_sec)
    n = min(len(beats), len(durations))
    if n <= 0:
        return [{"mood": main, "duration": max(0.1, sum(max(0.1, float(d or 0)) for d in durations)), "main": True}]

    raw: list[dict[str, Any]] = []
    for i in range(n):
        dur = max(0.1, float(durations[i] or 0.1))
        beat = beats[i] if isinstance(beats[i], dict) else {}
        local, score = infer_beat_mood(beat, fallback=main)
        # Need a clear signal to leave the main bed
        mood = local if (score >= 1 and local != main) else main
        raw.append({"mood": mood, "duration": dur, "main": mood == main, "score": score})

    # Absorb short non-main runs back into main (unless long enough to hear)
    i = 0
    while i < len(raw):
        if raw[i]["mood"] == main:
            i += 1
            continue
        j = i
        total = 0.0
        while j < len(raw) and raw[j]["mood"] == raw[i]["mood"]:
            total += raw[j]["duration"]
            j += 1
        if total < min_alt:
            for k in range(i, j):
                raw[k]["mood"] = main
                raw[k]["main"] = True
        i = j

    segments: list[dict[str, Any]] = []
    for row in raw:
        if segments and segments[-1]["mood"] == row["mood"]:
            segments[-1]["duration"] += row["duration"]
        else:
            segments.append({
                "mood": row["mood"],
                "duration": row["duration"],
                "main": row["mood"] == main,
            })
    if not segments:
        segments = [{"mood": main, "duration": sum(max(0.1, float(d or 0)) for d in durations[:n]), "main": True}]
    return segments


def _render_looped_segment(
    music_path: Path,
    duration: float,
    out_wav: Path,
    *,
    timeout: int = 120,
) -> bool:
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    dur = max(0.2, float(duration))
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",
        "-i", str(music_path),
        "-t", f"{dur:.3f}",
        "-ac", "2", "-ar", "44100",
        "-c:a", "pcm_s16le",
        str(out_wav),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0 and out_wav.is_file() and out_wav.stat().st_size > 1000
    except Exception as e:
        print(f"[sb-music] segment render failed: {e}")
        return False


def _acrossfade_parts(parts: list[Path], out_wav: Path, *, fade: float, timeout: int = 300) -> bool:
    if not parts:
        return False
    if len(parts) == 1:
        try:
            shutil.copy2(parts[0], out_wav)
            return True
        except Exception:
            return False
    fade = max(0.2, min(3.0, float(fade)))
    # Chain: out = acrossfade(acrossfade(p0,p1), p2)...
    work = out_wav.parent
    cur = parts[0]
    for i, nxt in enumerate(parts[1:], start=1):
        step = work / f"_xfade_{i}.wav"
        filter_complex = f"[0:a][1:a]acrossfade=d={fade:.2f}:c1=tri:c2=tri[a]"
        cmd = [
            "ffmpeg", "-y",
            "-i", str(cur),
            "-i", str(nxt),
            "-filter_complex", filter_complex,
            "-map", "[a]",
            "-ac", "2", "-ar", "44100",
            "-c:a", "pcm_s16le",
            str(step),
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if r.returncode != 0 or not step.is_file():
                print(f"[sb-music] acrossfade failed: {(r.stderr or '')[-400:]}")
                return False
            cur = step
        except Exception as e:
            print(f"[sb-music] acrossfade error: {e}")
            return False
    try:
        shutil.copy2(cur, out_wav)
        return out_wav.is_file()
    except Exception:
        return False


def build_mood_bed(
    segments: list[dict[str, Any]],
    *,
    catalog: dict[str, Any] | None = None,
    seed: str = "",
    work_dir: str | Path,
) -> tuple[Path | None, list[dict[str, Any]]]:
    """
    Render a single bed audio file from mood segments (with crossfades).
    Returns (bed_path, segment_meta).
    """
    data = catalog if catalog is not None else load_catalog()
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    fade = _CROSSFADE_SEC
    parts: list[Path] = []
    meta_rows: list[dict[str, Any]] = []
    # Prefer one stable track per mood for this cook (seeded)
    track_cache: dict[str, tuple[Path, dict[str, Any]]] = {}

    for i, seg in enumerate(segments):
        mood = (seg.get("mood") or "warm").strip().lower()
        dur = float(seg.get("duration") or 0.1)
        if mood not in track_cache:
            path, entry = pick_track(mood, catalog=data, seed=f"{seed}:{mood}")
            if not path or not entry:
                path, entry = pick_track("warm", catalog=data, seed=seed)
            if not path or not entry:
                return None, []
            track_cache[mood] = (path, entry)
        path, entry = track_cache[mood]
        # Pad a little so acrossfade has room (except last)
        pad = fade if i < len(segments) - 1 else 0.0
        part = work / f"seg_{i:02d}_{mood}.wav"
        if not _render_looped_segment(path, dur + pad, part):
            return None, []
        parts.append(part)
        meta_rows.append({
            "mood": mood,
            "duration": round(dur, 2),
            "main": bool(seg.get("main")),
            "track": entry.get("title") or path.stem,
            "file": entry.get("file") or path.name,
        })

    bed = work / "mood_bed.wav"
    if not _acrossfade_parts(parts, bed, fade=fade):
        return None, []
    return bed, meta_rows


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
    clip_durations: list[float] | None = None,
    seed: str = "",
    work_dir: str | Path | None = None,
    progress: Any = None,
) -> dict[str, Any]:
    """
    If catalog has tracks, mix a mood bed under dialogue.
    With clip_durations, switches beds on mood changes (crossfade) and returns
    to the story main mood between digressions.
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

    beat_rows = [b for b in (beats or []) if isinstance(b, dict)]
    main_mood = infer_story_mood(title, beat_rows)
    seed_s = seed or vin.stem
    work = Path(work_dir or vin.parent)
    work.mkdir(parents=True, exist_ok=True)

    music_src: Path | None = None
    entry: dict[str, Any] | None = None
    segments_meta: list[dict[str, Any]] = []

    durs = [float(d) for d in (clip_durations or []) if d is not None]
    if beat_rows and durs and len(durs) >= 1:
        segments = plan_mood_segments(title or "", beat_rows, durs)
        bed, segments_meta = build_mood_bed(
            segments, catalog=catalog, seed=seed_s, work_dir=work / "bed_parts",
        )
        if bed:
            music_src = bed
            # Primary display = main mood track
            path_main, entry = pick_track(main_mood, catalog=catalog, seed=seed_s)
            if not entry and segments_meta:
                entry = {
                    "title": segments_meta[0].get("track") or "",
                    "file": segments_meta[0].get("file") or "",
                    "attribution": "",
                }
            elif not entry and path_main:
                entry = {"title": path_main.stem, "file": path_main.name, "attribution": ""}

    if music_src is None:
        path, entry = pick_track(main_mood, catalog=catalog, seed=seed_s)
        if not path or not entry:
            return meta
        music_src = path
        segments_meta = [{"mood": main_mood, "duration": None, "main": True,
                          "track": entry.get("title") or path.stem,
                          "file": entry.get("file") or path.name}]

    log("Adding music…")
    mixed = work / f"{vin.stem}_with_music.mp4"
    ok = mix_bgm_under_dialogue(vin, music_src, mixed)
    if not ok:
        print(f"[sb-music] mix failed for {music_src.name}")
        return meta

    try:
        tmp = vin.with_suffix(".mp4.tmp_music")
        if vin.resolve() != mixed.resolve():
            shutil.move(str(mixed), str(tmp))
            vin.unlink(missing_ok=True)
            shutil.move(str(tmp), str(vin))
        switches = sum(1 for s in segments_meta if not s.get("main"))
        meta = {
            "applied": True,
            "mood": main_mood,
            "track": (entry or {}).get("title") or music_src.stem,
            "file": (entry or {}).get("file") or music_src.name,
            "attribution": (entry or {}).get("attribution") or "",
            "segments": segments_meta,
            "mood_switches": switches,
        }
        print(
            f"[sb-music] mixed main={main_mood} track={meta['track']} "
            f"segments={len(segments_meta)} switches={switches}"
        )
    except Exception as e:
        print(f"[sb-music] replace failed: {e}")
        if mixed.is_file():
            meta = {
                "applied": True,
                "mood": main_mood,
                "track": (entry or {}).get("title") or music_src.stem,
                "file": str(mixed),
                "output_path": str(mixed),
                "segments": segments_meta,
            }
    return meta
