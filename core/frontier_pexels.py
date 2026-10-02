"""
Frontier Pexels Integration - fetch stock video/photo b-roll.

Fetches Pexels stock footage matching the script content, with:
1. Claude generates search keywords from script
2. Fetch videos and photos for atmospheric b-roll
3. Download and cache assets
4. Match to ~15s windows across the timeline
"""

from __future__ import annotations
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Literal
import requests

from config import PEXELS_KEY


def generate_pexels_keywords(
    script: str,
    count: int = 14,
    niche_hint: str = "",
) -> list[str]:
    """
    Generate Pexels search keywords from the script using Atlas/Gemini LLM.
    
    Returns list of 1-2 word search terms for atmospheric b-roll.
    """
    from core.atlas_llm import generate_text as atlas_llm_text
    
    prompt = f"""You pick stock-footage search terms for background b-roll of a faceless YouTube video.

TITLE/NICHE: {niche_hint or "General faceless video"}

Below is the script. Produce {count} short search queries (1-2 words each) for Pexels stock VIDEO and PHOTO b-roll that visually match this video's mood and subject.

Favor cinematic, atmospheric, calm footage that loops well: night sky, stars, ocean waves, candle flame, rain on window, misty forest, golden light, clouds, city at night, mountains, fog, etc.

Avoid anything with readable text, brands, or recognizable people. Each query must be something Pexels has lots of footage for (keep them common and visual).

Return ONLY a JSON array of {count} strings, no markdown, no commentary.

SCRIPT:
---
{script[:3000]}
---"""
    
    try:
        response = atlas_llm_text(prompt, model="gpt-4o-mini", max_tokens=300)
        # Extract JSON array
        text = response.strip()
        if text.startswith("```"):
            # Remove markdown code fences
            lines = text.split("\n")
            text = "\n".join(line for line in lines if not line.strip().startswith("```"))
        
        keywords = json.loads(text.strip())
        if isinstance(keywords, list) and len(keywords) >= count // 2:
            return [str(k).strip() for k in keywords if k][:count]
    except Exception as e:
        print(f"[pexels] keyword generation failed: {e}")
    
    # Fallback keywords
    return [
        "night sky stars",
        "ocean waves",
        "clouds timelapse",
        "forest mist",
        "golden hour",
        "candle flame",
        "rain window",
        "mountains aerial",
        "sunrise",
        "calm water",
        "city lights",
        "moon",
        "fire embers",
        "fog morning",
    ][:count]


def fetch_pexels_assets(
    keywords: list[str],
    output_dir: str | Path,
    video_count: int = 24,
    photo_count: int = 12,
    per_query: Literal[1, 2, 3] = 2,
    orientation: Literal["landscape", "portrait", "square"] = "landscape",
    size: Literal["small", "medium", "large"] = "large",
) -> tuple[list[Path], list[Path]]:
    """
    Fetch Pexels stock videos and photos based on keywords.
    
    Args:
        keywords: List of search terms
        output_dir: Where to save downloaded assets
        video_count: Target number of videos to fetch
        photo_count: Target number of photos to fetch
        per_query: How many results to fetch per keyword
        orientation: Filter by orientation
        size: Photo size to download
    
    Returns:
        (video_paths, photo_paths) - lists of Path objects to downloaded files
    """
    if not PEXELS_KEY:
        print("[pexels] WARNING: PEXELS_KEY not set, returning empty")
        return [], []
    
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    video_dir = output_dir / "videos"
    photo_dir = output_dir / "photos"
    video_dir.mkdir(exist_ok=True)
    photo_dir.mkdir(exist_ok=True)
    
    # Manifest tracks what we've downloaded
    manifest_path = output_dir / "pexels_manifest.json"
    manifest = _load_manifest(manifest_path)
    
    headers = {"Authorization": PEXELS_KEY}
    
    videos: list[Path] = []
    photos: list[Path] = []
    
    # Fetch videos
    print(f"[pexels] Fetching up to {video_count} videos...")
    for i, keyword in enumerate(keywords):
        if len(videos) >= video_count:
            break
        
        # Check manifest first
        cached_videos = [
            v for v in manifest.get("videos", [])
            if v.get("keyword") == keyword and Path(v.get("path", "")).exists()
        ]
        
        if cached_videos:
            for v in cached_videos[:per_query]:
                videos.append(Path(v["path"]))
                if len(videos) >= video_count:
                    break
            continue
        
        # Fetch from Pexels
        try:
            url = "https://api.pexels.com/videos/search"
            params = {
                "query": keyword,
                "per_page": per_query,
                "orientation": orientation,
            }
            
            response = requests.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            for video in data.get("videos", [])[:per_query]:
                if len(videos) >= video_count:
                    break
                
                # Get HD video file
                files = video.get("video_files", [])
                # Prefer 1920x1080 HD
                hd_file = next(
                    (f for f in files if f.get("width") == 1920 and f.get("height") == 1080),
                    None,
                )
                if not hd_file and files:
                    # Fallback to largest available
                    hd_file = max(files, key=lambda f: f.get("width", 0) * f.get("height", 0))
                
                if hd_file and hd_file.get("link"):
                    video_url = hd_file["link"]
                    video_id = str(video.get("id", hashlib.md5(video_url.encode()).hexdigest()[:8]))
                    video_path = video_dir / f"pexels_video_{video_id}.mp4"
                    
                    if not video_path.exists():
                        _download_file(video_url, video_path)
                        time.sleep(0.2)  # Rate limit courtesy
                    
                    if video_path.exists():
                        videos.append(video_path)
                        manifest.setdefault("videos", []).append({
                            "keyword": keyword,
                            "path": str(video_path),
                            "id": video_id,
                            "url": video_url,
                        })
            
            time.sleep(0.3)  # Rate limit
            
        except Exception as e:
            print(f"[pexels] Video search '{keyword}' failed: {e}")
    
    # Fetch photos
    print(f"[pexels] Fetching up to {photo_count} photos...")
    for i, keyword in enumerate(keywords):
        if len(photos) >= photo_count:
            break
        
        # Check manifest
        cached_photos = [
            p for p in manifest.get("photos", [])
            if p.get("keyword") == keyword and Path(p.get("path", "")).exists()
        ]
        
        if cached_photos:
            for p in cached_photos[:per_query]:
                photos.append(Path(p["path"]))
                if len(photos) >= photo_count:
                    break
            continue
        
        # Fetch from Pexels
        try:
            url = "https://api.pexels.com/v1/search"
            params = {
                "query": keyword,
                "per_page": per_query,
                "orientation": orientation,
            }
            
            response = requests.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            for photo in data.get("photos", [])[:per_query]:
                if len(photos) >= photo_count:
                    break
                
                # Get image URL
                src = photo.get("src", {})
                photo_url = src.get(size, src.get("large", src.get("original", "")))
                
                if photo_url:
                    photo_id = str(photo.get("id", hashlib.md5(photo_url.encode()).hexdigest()[:8]))
                    photo_path = photo_dir / f"pexels_photo_{photo_id}.jpg"
                    
                    if not photo_path.exists():
                        _download_file(photo_url, photo_path)
                        time.sleep(0.2)
                    
                    if photo_path.exists():
                        photos.append(photo_path)
                        manifest.setdefault("photos", []).append({
                            "keyword": keyword,
                            "path": str(photo_path),
                            "id": photo_id,
                            "url": photo_url,
                        })
            
            time.sleep(0.3)  # Rate limit
            
        except Exception as e:
            print(f"[pexels] Photo search '{keyword}' failed: {e}")
    
    # Save manifest
    _save_manifest(manifest_path, manifest)
    
    print(f"[pexels] Fetched {len(videos)} videos, {len(photos)} photos")
    return videos, photos


def _load_manifest(path: Path) -> dict:
    """Load Pexels download manifest."""
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_manifest(path: Path, manifest: dict):
    """Save Pexels download manifest."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
    except Exception as e:
        print(f"[pexels] Failed to save manifest: {e}")


def _download_file(url: str, dest: Path, timeout: int = 60):
    """Download a file from URL to destination."""
    try:
        response = requests.get(url, timeout=timeout, stream=True)
        response.raise_for_status()
        
        with open(dest, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
    except Exception as e:
        print(f"[pexels] Download failed: {e}")
        if dest.exists():
            dest.unlink()
        raise
