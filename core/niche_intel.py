"""
Admin Niche Intel packager — Shorts competitor data for LLM drag-and-drop.

Builds anonymized for_llm/ packs (Video 1..N) plus private/ title maps.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import config
from core.channel_data import (
    _extract_channel_id,
    _fetch_transcript,
    _get_uploads_playlist,
    _get_video_stats,
    _list_videos,
    _list_videos_via_search,
    _validate_yt_key,
)

ProgressFn = Callable[[str], None]

MAX_CHANNELS = 12
SHORTS_MAX_SEC = 90
DEFAULT_VIDEOS = 10
DEFAULT_FRAMES = 8
MAX_COMMENTS_PER_VIDEO = 500
LIST_POOL_MULTIPLIER = 4
LIST_POOL_MIN = 40


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (text or "").strip().lower()).strip("_")
    return (s or "niche")[:60]


def parse_iso8601_duration(iso: str) -> float:
    """Parse YouTube ISO-8601 duration (e.g. PT1M30S) → seconds."""
    if not iso:
        return 0.0
    m = re.fullmatch(
        r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)(?:\.\d+)?S)?",
        iso.strip().upper(),
    )
    if not m:
        return 0.0
    h = int(m.group(1) or 0)
    mins = int(m.group(2) or 0)
    secs = float(m.group(3) or 0)
    return h * 3600 + mins * 60 + secs


def fetch_comments(
    video_id: str,
    yt_api_key: str,
    *,
    max_comments: int = MAX_COMMENTS_PER_VIDEO,
) -> list[dict[str, Any]]:
    """Paginate commentThreads.list; return [{text, likes, published_at, author}]."""
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    youtube = build("youtube", "v3", developerKey=yt_api_key)
    out: list[dict[str, Any]] = []
    page_token = None
    try:
        while len(out) < max_comments:
            resp = youtube.commentThreads().list(
                part="snippet",
                videoId=video_id,
                maxResults=min(100, max_comments - len(out)),
                pageToken=page_token,
                textFormat="plainText",
                order="relevance",
            ).execute()
            for item in resp.get("items") or []:
                top = ((item.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}
                out.append({
                    "text": (top.get("textDisplay") or "").strip(),
                    "likes": int(top.get("likeCount") or 0),
                    "published_at": top.get("publishedAt") or "",
                    "author": top.get("authorDisplayName") or "",
                })
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    except HttpError as e:
        reason = ""
        try:
            reason = (e.error_details or [{}])[0].get("reason", "")
        except Exception:
            reason = str(e)
        if "commentsDisabled" in reason or "disabled" in str(e).lower():
            return []
        raise
    return out[:max_comments]


def download_short_video(video_id: str, out_path: Path) -> Path:
    """Download a single YouTube video (prefer mp4) via yt-dlp."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://www.youtube.com/watch?v={video_id}"
    tmpl = str(out_path.with_suffix("")) + ".%(ext)s"

    cookies = (getattr(config, "YOUTUBE_COOKIES_FILE", "") or "").strip()
    cookie_args: list[str] = []
    if cookies and Path(cookies).is_file():
        cookie_args = ["--cookies", cookies]

    client_attempts = ["android,ios,mweb", "android", "ios", "mweb", ""]
    last_err = ""
    for clients in client_attempts:
        for p in out_path.parent.glob(out_path.stem + ".*"):
            if p.suffix.lower() in (".mp4", ".webm", ".mkv", ".m4a"):
                p.unlink(missing_ok=True)
        cmd = [
            "yt-dlp",
            "-f", "best[height<=720][ext=mp4]/best[height<=720]/best",
            "--no-playlist",
            "--no-warnings",
            "-o", tmpl,
            *cookie_args,
        ]
        if clients:
            cmd.extend(["--extractor-args", f"youtube:player_client={clients}"])
        cmd.append(url)
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=240)
        except FileNotFoundError as e:
            raise RuntimeError("yt-dlp is not installed") from e
        except subprocess.CalledProcessError as e:
            last_err = ((e.stderr or e.stdout or "")[-300:]).strip()
            continue
        candidates = sorted(
            out_path.parent.glob(out_path.stem + ".*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for c in candidates:
            if c.suffix.lower() in (".mp4", ".webm", ".mkv") and c.stat().st_size > 1000:
                if c != out_path:
                    if out_path.exists():
                        out_path.unlink()
                    c.rename(out_path)
                return out_path
        last_err = "download produced no video file"
    raise RuntimeError(f"Could not download video {video_id}: {last_err[:200]}")


def extract_even_frames(video_path: Path, out_dir: Path, count: int = DEFAULT_FRAMES) -> list[Path]:
    """Extract `count` evenly spaced JPEG frames across the video."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("f_*.jpg"):
        old.unlink(missing_ok=True)

    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True, text=True, timeout=30,
    )
    try:
        duration = float((probe.stdout or "0").strip() or 0)
    except ValueError:
        duration = 0.0
    if duration <= 0:
        duration = 30.0

    n = max(1, min(int(count), 24))
    # Sample interior points so we avoid pure black first/last frames when possible
    frames: list[Path] = []
    for i in range(n):
        t = duration * (i + 0.5) / n
        dest = out_dir / f"f_{i + 1:02d}.jpg"
        subprocess.run(
            [
                "ffmpeg", "-y", "-ss", f"{t:.3f}",
                "-i", str(video_path),
                "-frames:v", "1", "-q:v", "3",
                str(dest),
            ],
            capture_output=True, check=False, timeout=60,
        )
        if dest.exists() and dest.stat().st_size > 500:
            frames.append(dest)
    return frames


def _pick_videos(
    channel_id: str,
    yt_api_key: str,
    *,
    max_videos: int,
    log: ProgressFn,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    playlist_id, metadata = _get_uploads_playlist(channel_id, yt_api_key)
    pool = max(max_videos * LIST_POOL_MULTIPLIER, LIST_POOL_MIN)
    try:
        listed = _list_videos(playlist_id, yt_api_key, pool)
    except ValueError:
        log("Uploads playlist unavailable — falling back to search…")
        listed = _list_videos_via_search(channel_id, yt_api_key, pool)
    if not listed:
        raise ValueError(f"No public videos for {metadata.get('channel_name') or channel_id}")

    stats = _get_video_stats([v["video_id"] for v in listed], yt_api_key)
    enriched = []
    for v in listed:
        s = stats.get(v["video_id"], {})
        dur = parse_iso8601_duration(s.get("duration") or "")
        enriched.append({
            **v,
            "views": s.get("views", 0),
            "likes": s.get("likes", 0),
            "comments": s.get("comments", 0),
            "duration": s.get("duration") or "",
            "duration_sec": dur,
            "is_short": 0 < dur <= SHORTS_MAX_SEC,
        })

    shorts = [v for v in enriched if v["is_short"]]
    rest = [v for v in enriched if not v["is_short"]]
    # Prefer recent Shorts (list order is newest-first); then fill
    picked = (shorts + rest)[:max_videos]
    log(f"Selected {len(picked)} videos ({sum(1 for v in picked if v['is_short'])} Shorts)")
    return metadata, picked


def _channel_display_name(metadata: dict[str, Any], fallback: str) -> str:
    name = (metadata.get("channel_name") or "").strip()
    return name or fallback


def _process_channel(
    *,
    channel_url: str,
    channel_index: int,
    yt_api_key: str,
    downsub_key: str,
    videos_per_channel: int,
    frames_per_video: int,
    pack_root: Path,
    log: ProgressFn,
) -> dict[str, Any]:
    tag = f"ch{channel_index:02d}"
    log(f"[{tag}] Resolving {channel_url}…")
    channel_id = _extract_channel_id(channel_url, yt_api_key)
    metadata, videos = _pick_videos(
        channel_id, yt_api_key, max_videos=videos_per_channel, log=lambda m: log(f"[{tag}] {m}"),
    )
    channel_name = _channel_display_name(metadata, f"Channel {channel_index}")
    channel_slug = _slug(channel_name)[:40]
    log(f"[{tag}] {channel_name}")

    # Downloads stay under _admin (NOT in the LLM zip)
    raw_dir = pack_root / "_admin" / "raw" / channel_slug
    raw_dir.mkdir(parents=True, exist_ok=True)
    frames_llm = pack_root / "llm" / "frames"
    frames_llm.mkdir(parents=True, exist_ok=True)

    # Video titles anonymized as Video 1..N (channel names stay real)
    video_entries = []
    for i, v in enumerate(videos, start=1):
        video_entries.append({
            "anon_id": f"Video {i}",
            "video_num": i,
            **v,
            "url": f"https://www.youtube.com/watch?v={v['video_id']}",
        })

    log(f"[{tag}] Fetching transcripts…")
    for i, entry in enumerate(video_entries, start=1):
        log(f"[{tag}] Transcript {i}/{len(video_entries)}…")
        text = _fetch_transcript(entry["video_id"], downsub_key) or ""
        entry["transcript"] = text.strip()
        time.sleep(0.25)

    by_views = sorted(video_entries, key=lambda e: e.get("views") or 0, reverse=True)
    comment_targets = by_views[:2]
    for entry in video_entries:
        entry["has_comments_pack"] = entry["video_id"] in {t["video_id"] for t in comment_targets}

    comments_by_num: dict[int, list[dict]] = {}
    for entry in comment_targets:
        log(f"[{tag}] Comments for {entry['anon_id']} ({entry['views']} views)…")
        try:
            comments = fetch_comments(entry["video_id"], yt_api_key)
        except Exception as e:
            log(f"[{tag}] Comments failed for {entry['anon_id']}: {e}")
            comments = []
        comments_by_num[entry["video_num"]] = comments
        (raw_dir / f"comments_video_{entry['video_num']:02d}.json").write_text(
            json.dumps(comments, indent=2), encoding="utf-8",
        )
        time.sleep(0.2)

    shorts_picked = [e for e in video_entries if e.get("is_short")]
    visual = (
        max(shorts_picked, key=lambda e: e.get("views") or 0)
        if shorts_picked
        else max(video_entries, key=lambda e: e.get("views") or 0)
    )
    for entry in video_entries:
        entry["is_visual_source"] = entry["video_id"] == visual["video_id"]

    frame_paths: list[str] = []
    video_file = raw_dir / f"video_{visual['video_num']:02d}.mp4"
    try:
        log(f"[{tag}] Downloading visual source {visual['anon_id']}…")
        download_short_video(visual["video_id"], video_file)
        log(f"[{tag}] Extracting {frames_per_video} frames…")
        frames = extract_even_frames(video_file, raw_dir / "frames", count=frames_per_video)
        for fp in frames:
            dest_name = f"{channel_slug}__Video{visual['video_num']:02d}__{fp.stem}.jpg"
            dest = frames_llm / dest_name
            shutil.copy2(fp, dest)
            frame_paths.append(dest_name)
    except Exception as e:
        log(f"[{tag}] Visual pack failed: {e}")

    return {
        "channel_name": channel_name,
        "channel_slug": channel_slug,
        "input_url": channel_url,
        "metadata": metadata,
        "videos": video_entries,
        "comments_by_num": comments_by_num,
        "visual_video_num": visual["video_num"],
        "frame_files": frame_paths,
    }


def _write_llm_folder(pack_root: Path, niche: str, channels: list[dict[str, Any]]) -> Path:
    """
    Write a single clean `llm/` folder meant to be uploaded/pasted as-is:
      llm/README.md   — one-line how-to
      llm/ALL.md      — overview + transcripts + comments (real channel names, anon videos)
      llm/frames/     — stills
    Admin-only title map goes under `_admin/` (excluded from zip).
    """
    llm = pack_root / "llm"
    admin = pack_root / "_admin"
    llm.mkdir(parents=True, exist_ok=True)
    (llm / "frames").mkdir(parents=True, exist_ok=True)
    admin.mkdir(parents=True, exist_ok=True)

    # Title map for you only (not in zip)
    video_map = []
    for ch in channels:
        for v in ch["videos"]:
            video_map.append({
                "channel_name": ch["channel_name"],
                "anon_id": v["anon_id"],
                "title": v["title"],
                "video_id": v["video_id"],
                "url": v["url"],
                "views": v.get("views"),
                "likes": v.get("likes"),
                "comments": v.get("comments"),
            })
    (admin / "video_titles.json").write_text(json.dumps(video_map, indent=2), encoding="utf-8")

    lines: list[str] = [
        f"# Niche Intel — {niche}",
        "",
        "Upload this **entire folder** to your LLM (or paste `ALL.md` and attach everything in `frames/`).",
        "",
        "- **Channel names are real.**",
        "- **Video titles are hidden** — use Video 1, Video 2, … only.",
        "",
        "---",
        "",
        "# Overview",
        "",
    ]

    for ch in channels:
        meta = ch["metadata"]
        name = ch["channel_name"]
        lines += [
            f"## {name}",
            f"- Subscribers: {meta.get('subscribers', 0):,}",
            f"- Channel total views: {meta.get('total_views', 0):,}",
            f"- Videos in this pack: {len(ch['videos'])}",
            f"- Frames from: Video {ch['visual_video_num']} ({len(ch.get('frame_files') or [])} stills)",
            "",
            "| Label | Views | Likes | Comment count | Duration (s) | Short? | Comments included | Has frames |",
            "|---|---:|---:|---:|---:|:---:|:---:|:---:|",
        ]
        for v in ch["videos"]:
            lines.append(
                f"| {v['anon_id']} | {v.get('views', 0):,} | {v.get('likes', 0):,} | "
                f"{v.get('comments', 0):,} | {int(v.get('duration_sec') or 0)} | "
                f"{'yes' if v.get('is_short') else ''} | "
                f"{'yes' if v.get('has_comments_pack') else ''} | "
                f"{'yes' if v.get('is_visual_source') else ''} |"
            )
        lines.append("")
        if ch.get("frame_files"):
            lines.append("Frame files:")
            for fn in ch["frame_files"]:
                lines.append(f"- `frames/{fn}`")
            lines.append("")

    lines += ["---", "", "# Transcripts", ""]
    for ch in channels:
        lines += [f"## {ch['channel_name']}", ""]
        for v in ch["videos"]:
            body = (v.get("transcript") or "").strip() or "(no transcript available)"
            lines += [f"### {v['anon_id']}", "", body, "", "---", ""]

    lines += ["# Comments", "", "Top 2 videos by views per channel.", ""]
    for ch in channels:
        lines += [f"## {ch['channel_name']}", ""]
        by_num = ch.get("comments_by_num") or {}
        if not by_num:
            lines += ["(no comments packed)", ""]
            continue
        for num in sorted(by_num.keys()):
            comments = by_num[num]
            lines += [f"### Video {num} — {len(comments)} comments", ""]
            for c in comments:
                likes = c.get("likes") or 0
                text = (c.get("text") or "").replace("\n", " ").strip()
                if text:
                    lines.append(f"- ({likes} likes) {text}")
            lines.append("")

    (llm / "ALL.md").write_text("\n".join(lines), encoding="utf-8")
    (llm / "README.md").write_text(
        f"# {niche} — LLM pack\n\n"
        "Upload **this whole folder** to your LLM.\n\n"
        "1. Paste or attach `ALL.md` (stats + transcripts + comments)\n"
        "2. Attach every image in `frames/`\n\n"
        "Channel names are real. Video titles are labeled Video 1, Video 2, … only.\n",
        encoding="utf-8",
    )
    return llm


def build_pack(
    *,
    niche: str,
    channel_urls: list[str],
    videos_per_channel: int = DEFAULT_VIDEOS,
    frames_per_video: int = DEFAULT_FRAMES,
    out_root: Path | None = None,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """
    Build a full niche intel pack. Returns {pack_dir, zip_path, channels_ok, errors}.
    """
    def log(msg: str) -> None:
        print(f"[niche_intel] {msg}", flush=True)
        if progress:
            progress(msg)

    yt_key = (getattr(config, "YOUTUBE_API_KEY", "") or "").strip()
    _validate_yt_key(yt_key)
    downsub = (getattr(config, "DOWNSUB_KEY", "") or "").strip()

    urls = []
    for raw in channel_urls:
        u = (raw or "").strip()
        if u and u not in urls:
            urls.append(u)
    if not urls:
        raise ValueError("Add at least one channel URL.")
    if len(urls) > MAX_CHANNELS:
        raise ValueError(f"Max {MAX_CHANNELS} channels per run (got {len(urls)}).")

    videos_per_channel = max(1, min(int(videos_per_channel or DEFAULT_VIDEOS), 30))
    frames_per_video = max(2, min(int(frames_per_video or DEFAULT_FRAMES), 24))

    niche_clean = (niche or "niche").strip() or "niche"
    root = Path(out_root or (Path(__file__).resolve().parents[1] / "output" / "niche_intel"))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    pack_dir = root / f"{_slug(niche_clean)}_{stamp}"
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "llm" / "frames").mkdir(parents=True, exist_ok=True)
    (pack_dir / "_admin").mkdir(parents=True, exist_ok=True)

    channels_out: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for i, url in enumerate(urls, start=1):
        try:
            ch = _process_channel(
                channel_url=url,
                channel_index=i,
                yt_api_key=yt_key,
                downsub_key=downsub,
                videos_per_channel=videos_per_channel,
                frames_per_video=frames_per_video,
                pack_root=pack_dir,
                log=log,
            )
            channels_out.append(ch)
        except Exception as e:
            log(f"[ch{i:02d}] FAILED: {e}")
            errors.append({"channel_url": url, "error": str(e)})

    if not channels_out:
        raise RuntimeError(
            "No channels succeeded. " + "; ".join(f"{e['channel_url']}: {e['error']}" for e in errors)
        )

    log("Writing LLM folder…")
    llm_dir = _write_llm_folder(pack_dir, niche_clean, channels_out)

    # Zip ONLY llm/ — one clean folder to upload (no _admin / raw mess)
    zip_name = f"{_slug(niche_clean)}_llm_{stamp}.zip"
    zip_path = root / zip_name
    folder_in_zip = f"{_slug(niche_clean)}_llm"
    log(f"Zipping → {zip_path.name}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in llm_dir.rglob("*"):
            if path.is_file():
                zf.write(path, arcname=str(Path(folder_in_zip) / path.relative_to(llm_dir)))

    log("Done.")
    return {
        "pack_dir": str(pack_dir),
        "llm_dir": str(llm_dir),
        "zip_path": str(zip_path),
        "channels_ok": len(channels_out),
        "errors": errors,
        "niche": niche_clean,
    }
