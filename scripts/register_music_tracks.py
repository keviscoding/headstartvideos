#!/usr/bin/env python3
"""
Register YouTube Audio Library (or other cleared) MP3s into the storyboard music catalog.

Usage:
  python3 scripts/register_music_tracks.py
  python3 scripts/register_music_tracks.py --suggest-moods
  python3 scripts/register_music_tracks.py --inbox /path/to/mp3s --mood warm

Copies inbox/*.mp3 → assets/music/tracks/ and updates catalog.json.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.storyboard_music import (  # noqa: E402
    MOODS,
    MUSIC_DIR,
    TRACKS_DIR,
    load_catalog,
    save_catalog,
)

INBOX = MUSIC_DIR / "inbox"
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".ogg"}


def _slug(name: str) -> str:
    stem = Path(name).stem
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", stem).strip("_")
    return (s or "track")[:80]


def _guess_moods_from_title(title: str) -> list[str]:
    t = (title or "").lower()
    hits: list[str] = []
    rules = [
        ("playful", ("fun", "happy", "play", "upbeat", "kids", "bounce", "sunny")),
        ("tension", ("dark", "tense", "suspense", "drama", "worry", "chase")),
        ("sad", ("sad", "melanchol", "lonely", "tear", "soft piano")),
        ("resolve", ("hope", "inspire", "victory", "warm ending", "resolve")),
        ("warm", ("warm", "calm", "peaceful", "acoustic", "gentle", "cozy", "family", "home")),
    ]
    for mood, words in rules:
        if any(w in t for w in words):
            hits.append(mood)
    return hits or ["warm"]


def _llm_suggest_moods(title: str) -> list[str]:
    try:
        from core.atlas_llm import generate_text
        raw = generate_text(
            f"Title of an instrumental bed for kids/family YouTube stories: {title}\n"
            f"Pick 1–2 moods from: {', '.join(MOODS)}\n"
            "Reply with comma-separated moods only.",
            system="Music mood tagger. Reply moods only.",
            max_tokens=20,
            temperature=0.0,
        )
        found = []
        for part in re.split(r"[,/\s]+", (raw or "").lower()):
            w = re.sub(r"[^a-z]", "", part)
            if w in MOODS and w not in found:
                found.append(w)
        return found or _guess_moods_from_title(title)
    except Exception:
        return _guess_moods_from_title(title)


def main() -> int:
    ap = argparse.ArgumentParser(description="Register music beds into storyboard catalog")
    ap.add_argument("--inbox", type=Path, default=INBOX, help="Folder of new MP3s")
    ap.add_argument("--mood", action="append", dest="moods", default=[],
                    help="Force mood tag(s); repeatable. Default: guess from filename")
    ap.add_argument("--suggest-moods", action="store_true",
                    help="Ask Atlas LLM to suggest moods from titles")
    ap.add_argument("--attribution", default="", help="Credit string if required")
    args = ap.parse_args()

    inbox: Path = args.inbox
    inbox.mkdir(parents=True, exist_ok=True)
    TRACKS_DIR.mkdir(parents=True, exist_ok=True)

    files = sorted(
        p for p in inbox.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS
    )
    if not files:
        print(f"No audio files in {inbox}")
        print("Download from YouTube Audio Library and drop MP3s there, then re-run.")
        return 1

    catalog = load_catalog()
    existing_files = {
        (t.get("file") or "").strip()
        for t in catalog.get("tracks") or []
        if isinstance(t, dict)
    }
    existing_ids = {
        (t.get("id") or "").strip()
        for t in catalog.get("tracks") or []
        if isinstance(t, dict)
    }

    added = 0
    for src in files:
        slug = _slug(src.name)
        digest = hashlib.sha1(src.read_bytes()[:65536] + str(src.stat().st_size).encode()).hexdigest()[:10]
        track_id = f"{slug}_{digest}"
        dest_name = f"{slug}_{digest}{src.suffix.lower()}"
        dest = TRACKS_DIR / dest_name
        rel = f"tracks/{dest_name}"

        if rel in existing_files or track_id in existing_ids:
            print(f"skip (already registered): {src.name}")
            continue

        shutil.copy2(src, dest)
        title = Path(src.name).stem.replace("_", " ").replace("-", " ").strip()
        if args.moods:
            moods = [m for m in args.moods if m in MOODS] or ["warm"]
        elif args.suggest_moods:
            moods = _llm_suggest_moods(title)
            print(f"  moods for '{title}': {moods}")
        else:
            moods = _guess_moods_from_title(title)

        entry = {
            "id": track_id,
            "title": title,
            "file": rel,
            "moods": moods,
            "attribution": (args.attribution or "").strip(),
            "source": "youtube_audio_library",
        }
        catalog.setdefault("tracks", []).append(entry)
        added += 1
        print(f"added: {rel}  moods={moods}")

    save_catalog(catalog)
    print(f"Done — {added} new track(s). Catalog: {MUSIC_DIR / 'catalog.json'}")
    print(f"Total tracks: {len(catalog.get('tracks') or [])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
