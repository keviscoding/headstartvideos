"""
Storyboard Assemble — match numbered I2V clips to pack beats, stitch, burn dialogue captions.

Matching:
  1) Filename index (001_, scene_003, …)
  2) Frame-0 average hash vs pack stills (Pillow only)
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable

ProgressFn = Callable[[str], None]

_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".m4v"}
_INDEX_RE = re.compile(
    r"(?:^|[_\-\s])(?:scene[_\-\s]*)?0*(\d{1,4})(?:[_\-\s.]|$)",
    re.IGNORECASE,
)
_AHASH_SIZE = 8
_AHASH_MAX_DISTANCE = 18  # of 64 bits — loose enough for I2V drift


def _probe_duration_sec(path: str) -> float:
    try:
        from core.assembler import _probe_duration_sec as _p
        return float(_p(path) or 0)
    except Exception:
        return 0.0


def parse_clip_index(filename: str) -> int | None:
    """Extract scene index from a clip filename, or None."""
    name = Path(filename or "").name
    m = _INDEX_RE.search(name)
    if not m:
        # bare leading digits: 001.mp4 / 1.mp4
        m2 = re.match(r"^0*(\d{1,4})\b", name)
        if not m2:
            return None
        idx = int(m2.group(1))
    else:
        idx = int(m.group(1))
    return idx if idx >= 1 else None


def extract_clips_from_uploads(paths: list[str | Path], dest_dir: Path) -> list[Path]:
    """Copy video files and unpack zips of clips into dest_dir. Returns clip paths."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in _VIDEO_EXTS:
            dest = dest_dir / p.name
            if p.resolve() != dest.resolve():
                shutil.copy2(p, dest)
            out.append(dest)
        elif ext == ".zip":
            with zipfile.ZipFile(p, "r") as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    inner = Path(info.filename).name
                    if Path(inner).suffix.lower() not in _VIDEO_EXTS:
                        continue
                    target = dest_dir / inner
                    with zf.open(info) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    out.append(target)
    # de-dupe by name
    seen: set[str] = set()
    unique: list[Path] = []
    for c in out:
        key = c.name.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)
    return unique


def _average_hash(image_path: str | Path) -> int | None:
    try:
        from PIL import Image
        im = Image.open(image_path).convert("L").resize(
            (_AHASH_SIZE, _AHASH_SIZE), Image.Resampling.LANCZOS,
        )
        pixels = list(im.getdata())
        avg = sum(pixels) / max(1, len(pixels))
        bits = 0
        for i, px in enumerate(pixels):
            if px >= avg:
                bits |= 1 << i
        return bits
    except Exception as e:
        print(f"[sb-assemble] aHash failed ({image_path}): {e}")
        return None


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _extract_frame0(clip_path: Path, out_jpg: Path) -> bool:
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-ss", "0", "-i", str(clip_path),
        "-frames:v", "1", "-q:v", "3", str(out_jpg),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return r.returncode == 0 and out_jpg.is_file() and out_jpg.stat().st_size > 200
    except Exception as e:
        print(f"[sb-assemble] frame0 extract failed: {e}")
        return False


def _normalize_clip(src: Path, dest: Path, *, width: int = 1920, height: int = 1080) -> bool:
    """Re-encode to H.264 16:9 for reliable concat."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24"
    )
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        "-shortest",
        str(dest),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode == 0 and dest.is_file() and dest.stat().st_size > 1000:
            return True
        # Retry video-only (clips with no/broken audio)
        cmd_an = [
            "ffmpeg", "-y", "-i", str(src),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-an",
            str(dest),
        ]
        r2 = subprocess.run(cmd_an, capture_output=True, text=True, timeout=300)
        return r2.returncode == 0 and dest.is_file() and dest.stat().st_size > 1000
    except Exception as e:
        print(f"[sb-assemble] normalize failed ({src.name}): {e}")
        return False


def load_pack_beats(pack_dir: Path | None = None, beats: list[dict] | None = None) -> list[dict[str, Any]]:
    """Normalize beat list from pack.json / MANIFEST / job result."""
    if isinstance(beats, list) and beats:
        out = []
        for b in beats:
            if not isinstance(b, dict):
                continue
            try:
                idx = int(b.get("index") or 0)
            except (TypeError, ValueError):
                continue
            if idx < 1:
                continue
            out.append({
                "index": idx,
                "dialogue": str(b.get("dialogue") or "").strip(),
                "filename": str(b.get("filename") or "").strip(),
                "image_path": str(b.get("image_path") or "").strip(),
                "image_url": str(b.get("image_url") or "").strip(),
                "target_sec": float(b.get("target_sec") or 8),
            })
        return sorted(out, key=lambda x: x["index"])

    if not pack_dir or not Path(pack_dir).is_dir():
        return []
    pack_dir = Path(pack_dir)
    pack_json = pack_dir / "pack.json"
    if pack_json.is_file():
        try:
            data = json.loads(pack_json.read_text(encoding="utf-8"))
            return load_pack_beats(beats=data.get("beats") if isinstance(data, dict) else None)
        except Exception:
            pass
    manifest = pack_dir / "MANIFEST.csv"
    if manifest.is_file():
        rows = []
        with manifest.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                try:
                    idx = int(str(row.get("index") or "").strip())
                except (TypeError, ValueError):
                    continue
                rows.append({
                    "index": idx,
                    "dialogue": str(row.get("dialogue") or "").strip(),
                    "filename": str(row.get("filename") or "").strip(),
                    "image_path": str(pack_dir / (row.get("filename") or "")),
                    "image_url": "",
                    "target_sec": float(row.get("target_sec") or 8),
                })
        return sorted(rows, key=lambda x: x["index"])
    return []


def _still_path_for_beat(beat: dict, pack_dir: Path | None, cache_dir: Path) -> str:
    local = (beat.get("image_path") or "").strip()
    if local and Path(local).is_file():
        return local
    fn = (beat.get("filename") or "").strip()
    if pack_dir and fn:
        cand = Path(pack_dir) / fn
        if cand.is_file():
            return str(cand)
        # common pattern
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            cand2 = Path(pack_dir) / f"{beat['index']:03d}_scene{ext}"
            if cand2.is_file():
                return str(cand2)
    url = (beat.get("image_url") or "").strip()
    if url.startswith("http"):
        try:
            from webapp.storage import fetch_to_local
            return fetch_to_local(url, cache_dir)
        except Exception as e:
            print(f"[sb-assemble] still fetch failed: {e}")
    return ""


def match_clips_to_beats(
    clips: list[Path],
    beats: list[dict[str, Any]],
    *,
    pack_dir: Path | None = None,
    work_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """
    Return ordered matches: [{index, clip, method, confidence, dialogue}, ...]
    One clip per beat index; extras ignored.
    """
    work = Path(work_dir or tempfile.mkdtemp(prefix="sb_match_"))
    work.mkdir(parents=True, exist_ok=True)
    still_cache = work / "stills"
    still_cache.mkdir(exist_ok=True)
    frame_cache = work / "frames"
    frame_cache.mkdir(exist_ok=True)

    by_index = {int(b["index"]): b for b in beats}
    assigned: dict[int, dict[str, Any]] = {}
    leftover: list[Path] = []

    for clip in clips:
        idx = parse_clip_index(clip.name)
        if idx is not None and idx in by_index and idx not in assigned:
            assigned[idx] = {
                "index": idx,
                "clip": str(clip),
                "method": "filename",
                "confidence": 1.0,
                "dialogue": by_index[idx].get("dialogue") or "",
            }
        else:
            leftover.append(clip)

    # Build still hashes for remaining beats
    need = [b for b in beats if int(b["index"]) not in assigned]
    still_hashes: dict[int, int] = {}
    for b in need:
        sp = _still_path_for_beat(b, pack_dir, still_cache)
        if not sp:
            continue
        h = _average_hash(sp)
        if h is not None:
            still_hashes[int(b["index"])] = h

    for clip in leftover:
        frame = frame_cache / f"{clip.stem}_f0.jpg"
        if not _extract_frame0(clip, frame):
            continue
        ch = _average_hash(frame)
        if ch is None:
            continue
        best_idx = None
        best_dist = 999
        for idx, sh in still_hashes.items():
            if idx in assigned:
                continue
            d = _hamming(ch, sh)
            if d < best_dist:
                best_dist = d
                best_idx = idx
        if best_idx is not None and best_dist <= _AHASH_MAX_DISTANCE:
            conf = max(0.0, 1.0 - (best_dist / 64.0))
            assigned[best_idx] = {
                "index": best_idx,
                "clip": str(clip),
                "method": "phash",
                "confidence": round(conf, 3),
                "dialogue": by_index[best_idx].get("dialogue") or "",
                "distance": best_dist,
            }

    return [assigned[i] for i in sorted(assigned)]


def build_caption_slots(matched: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build ASS caption slots from matched clips using actual durations."""
    slots: list[dict[str, Any]] = []
    t = 0.0
    for m in matched:
        clip = m.get("clip") or ""
        dur = _probe_duration_sec(clip) if clip else 0.0
        if dur <= 0.05:
            dur = float(m.get("target_sec") or 8.0)
        dialogue = (m.get("dialogue") or "").strip()
        # Strip "Name: " prefix noise lightly for on-screen length
        text = re.sub(r"\s+", " ", dialogue).strip()
        if text and text.lower() not in ("(no dialogue)", "no dialogue"):
            slots.append({
                "text": text[:220],
                "start_sec": t,
                "end_sec": t + max(0.4, dur - 0.05),
            })
        t += dur
    return slots


def _burn_captions_video_only(
    video_path: str,
    subtitle_path: str,
    output_path: str,
) -> bool:
    """Burn ASS captions onto a silent (or existing-audio) video — no separate VO."""
    from core.assembler import _check_ass_filter

    if not os.path.isfile(video_path) or os.path.getsize(video_path) < 1000:
        return False
    if not subtitle_path or not os.path.isfile(subtitle_path):
        # Just copy through
        shutil.copy2(video_path, output_path)
        return True
    if not _check_ass_filter():
        print("[sb-assemble] ffmpeg ass filter missing — shipping without burn-in")
        shutil.copy2(video_path, output_path)
        return True

    tmp_sub = os.path.join(tempfile.gettempdir(), f"_sb_sub_{os.getpid()}.ass")
    try:
        shutil.copy2(subtitle_path, tmp_sub)
        safe = tmp_sub.replace("\\", "/").replace(":", "\\:").replace("'", r"\'")
        # Keep audio if present; re-encode video for burn-in
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", f"ass='{safe}'",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if r.returncode == 0 and os.path.isfile(output_path) and os.path.getsize(output_path) > 1000:
            return True
        # Video-only retry
        cmd_an = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", f"ass='{safe}'",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-an", "-movflags", "+faststart",
            output_path,
        ]
        r2 = subprocess.run(cmd_an, capture_output=True, text=True, timeout=900)
        if r2.returncode == 0 and os.path.isfile(output_path) and os.path.getsize(output_path) > 1000:
            return True
        print(f"[sb-assemble] caption burn failed: {(r.stderr or r2.stderr or '')[-300:]}")
        shutil.copy2(video_path, output_path)
        return True
    except Exception as e:
        print(f"[sb-assemble] caption burn exception: {e}")
        try:
            shutil.copy2(video_path, output_path)
            return True
        except Exception:
            return False
    finally:
        try:
            os.unlink(tmp_sub)
        except OSError:
            pass


def assemble_storyboard_video(
    *,
    matched: list[dict[str, Any]],
    output_path: str | Path,
    work_dir: str | Path | None = None,
    progress: ProgressFn | None = None,
    burn_captions: bool = True,
) -> dict[str, Any]:
    """
    Normalize matched clips → concat → optional dialogue caption burn-in.
    Returns {output_path, match_report, duration_sec, caption_count}.
    """
    from core.assembler import concatenate_clips, generate_ass_subtitles

    log = progress or (lambda _m: None)
    if not matched:
        raise RuntimeError("No clips matched to storyboard scenes.")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(work_dir or (out.parent / f"_assemble_work_{os.getpid()}"))
    work.mkdir(parents=True, exist_ok=True)
    norm_dir = work / "normalized"
    norm_dir.mkdir(exist_ok=True)

    normalized: list[str] = []
    report: list[dict[str, Any]] = []
    for i, m in enumerate(matched):
        src = Path(m["clip"])
        dest = norm_dir / f"{int(m['index']):03d}_clip.mp4"
        log(f"Normalizing scene {m['index']:03d} ({i + 1}/{len(matched)})…")
        if not _normalize_clip(src, dest):
            raise RuntimeError(f"Could not normalize clip for scene {m['index']}: {src.name}")
        normalized.append(str(dest))
        report.append({
            "index": m["index"],
            "clip": src.name,
            "method": m.get("method") or "",
            "confidence": m.get("confidence"),
            "dialogue": (m.get("dialogue") or "")[:160],
        })

    log("Stitching clips…")
    concat_path = str(work / "concat.mp4")
    if not concatenate_clips(normalized, concat_path):
        raise RuntimeError("Failed to concatenate clips.")

    final = str(out)
    caption_count = 0
    if burn_captions:
        slots = build_caption_slots([
            {**m, "clip": normalized[i]} for i, m in enumerate(matched)
        ])
        caption_count = len(slots)
        if slots:
            log(f"Burning {caption_count} dialogue captions…")
            ass_path = str(work / "dialogue.ass")
            generate_ass_subtitles(slots, ass_path)
            if not _burn_captions_video_only(concat_path, ass_path, final):
                raise RuntimeError("Caption burn / final mux failed.")
        else:
            shutil.copy2(concat_path, final)
    else:
        shutil.copy2(concat_path, final)

    duration = _probe_duration_sec(final)
    log(f"Assemble complete — {len(matched)} scenes, {duration:.1f}s")
    return {
        "output_path": final,
        "match_report": report,
        "duration_sec": duration,
        "caption_count": caption_count,
        "beat_count": len(matched),
    }
