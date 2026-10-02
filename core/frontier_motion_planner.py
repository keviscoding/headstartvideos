"""
Frontier Motion Planner - word-locked still timing + Pexels gap filling.

This module plans which visual (AI still, Pexels video, Pexels photo) owns each
moment of the timeline, matching the Whop Frontier behavior:

1. AI stills own specific SPOKEN SPANS (word/SRT locked)
2. Between stills, Pexels video or photo b-roll fills the gaps
3. Stills get Ken Burns zooms (in/out/hold)
4. Pexels clips are scaled to fill, photos get slow zooms
5. No back-to-back repeat of the same Pexels asset

The motion plan output is a timeline of segments, each specifying:
- Type: "ai_still", "pexels_video", "pexels_photo"
- Path: file path to the asset
- Start/end timestamps
- Zoom direction for stills/photos
"""

from __future__ import annotations
import random
from pathlib import Path
from typing import Literal


class MotionSegment:
    """One visual segment in the timeline."""
    
    def __init__(
        self,
        type: Literal["ai_still", "pexels_video", "pexels_photo", "motion_gfx"],
        path: str | Path,
        start_sec: float,
        end_sec: float,
        zoom: Literal["in", "out", "hold"] = "in",
        text: str = "",
    ):
        self.type = type
        self.path = str(path)
        self.start_sec = float(start_sec)
        self.end_sec = float(end_sec)
        self.zoom = zoom
        self.text = text
    
    @property
    def duration(self) -> float:
        return self.end_sec - self.start_sec
    
    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "path": self.path,
            "start_sec": self.start_sec,
            "end_sec": self.end_sec,
            "duration": self.duration,
            "zoom": self.zoom,
            "text": self.text,
        }


class StillOwnership:
    """Represents an AI still that owns a specific spoken span."""
    
    def __init__(
        self,
        still_path: str | Path,
        start_sec: float,
        end_sec: float,
        text: str = "",
        zoom: Literal["in", "out", "hold"] = "in",
    ):
        self.still_path = str(still_path)
        self.start_sec = float(start_sec)
        self.end_sec = float(end_sec)
        self.text = text
        self.zoom = zoom
    
    @property
    def duration(self) -> float:
        return self.end_sec - self.start_sec


def plan_motion_timeline(
    ai_stills: list[dict],
    pexels_videos: list[str | Path],
    pexels_photos: list[str | Path],
    total_duration_sec: float,
    zoom_strategy: Literal["in", "out", "alternate"] = "alternate",
    still_pad_sec: float = 0.8,
    still_min_hold_sec: float = 3.5,
    still_max_hold_sec: float = 10.0,
    first_still_min_hold_sec: float = 12.0,  # Hook lock: first cut ≥12s (formula)
    seed: int = 42,
) -> list[MotionSegment]:
    """
    Build the full motion timeline with AI stills owning spoken spans and
    Pexels b-roll filling gaps.
    
    Args:
        ai_stills: List of dicts with keys:
            - path: str - still image path
            - start_sec: float - when this still's narration starts
            - end_sec: float - when this still's narration ends
            - text: str - the spoken text for this still
        pexels_videos: List of Pexels video clip paths
        pexels_photos: List of Pexels photo paths
        total_duration_sec: Total video duration
        zoom_strategy: "in", "out", or "alternate" for stills/photos
        still_pad_sec: Hold still this many seconds past last spoken word
        still_min_hold_sec: Minimum still duration
        still_max_hold_sec: Maximum still duration
        first_still_min_hold_sec: Minimum first still duration (hook lock, default 12s per formula)
        seed: Random seed for Pexels selection
    
    Returns:
        List of MotionSegment objects in timeline order
    """
    rng = random.Random(seed)
    segments: list[MotionSegment] = []
    
    # Sort stills by start time
    stills_sorted = sorted(ai_stills, key=lambda s: s["start_sec"])
    
    # Determine zoom for each still
    zoom_directions = []
    for i, still in enumerate(stills_sorted):
        if zoom_strategy == "in":
            zoom = "in"
        elif zoom_strategy == "out":
            zoom = "out"
        else:  # alternate
            zoom = "in" if i % 2 == 0 else "out"
        zoom_directions.append(zoom)
    
    # Build still ownership spans (clamp durations)
    still_spans: list[StillOwnership] = []
    for i, still in enumerate(stills_sorted):
        raw_end = still["end_sec"] + still_pad_sec
        duration = raw_end - still["start_sec"]
        
        # Clamp duration (hook lock for first still: ≥12s per formula)
        min_hold = first_still_min_hold_sec if i == 0 else still_min_hold_sec
        if duration < min_hold:
            duration = min_hold
        elif duration > still_max_hold_sec:
            duration = still_max_hold_sec
        
        end_sec = still["start_sec"] + duration
        
        # Don't extend past total duration
        end_sec = min(end_sec, total_duration_sec)
        
        # Don't overlap with next still's start
        if i + 1 < len(stills_sorted):
            next_start = stills_sorted[i + 1]["start_sec"]
            end_sec = min(end_sec, next_start)
        
        still_spans.append(
            StillOwnership(
                still_path=still["path"],
                start_sec=still["start_sec"],
                end_sec=end_sec,
                text=still.get("text", ""),
                zoom=zoom_directions[i],
            )
        )
    
    # Prepare Pexels bag (no back-to-back repeats)
    pexels_bag = _PexelsBag(pexels_videos, pexels_photos, rng)
    
    # Build timeline by filling gaps between stills
    current_time = 0.0
    still_index = 0
    
    while current_time < total_duration_sec:
        # Check if we're at a still's start time
        if still_index < len(still_spans):
            still = still_spans[still_index]
            
            # Fill gap before this still (if any)
            if current_time < still.start_sec:
                gap_duration = still.start_sec - current_time
                pexels_seg = pexels_bag.fill_gap(
                    start_sec=current_time,
                    end_sec=still.start_sec,
                    prefer_video=gap_duration >= 3.0,
                )
                if pexels_seg:
                    # Determine zoom for photo
                    if pexels_seg.type == "pexels_photo":
                        # Alternate zoom for photos, offset from stills
                        photo_zoom = "out" if still.zoom == "in" else "in"
                        pexels_seg.zoom = photo_zoom
                    segments.append(pexels_seg)
                current_time = still.start_sec
            
            # Add the still
            segments.append(
                MotionSegment(
                    type="ai_still",
                    path=still.still_path,
                    start_sec=still.start_sec,
                    end_sec=still.end_sec,
                    zoom=still.zoom,
                    text=still.text,
                )
            )
            current_time = still.end_sec
            still_index += 1
        else:
            # No more stills - fill remainder with Pexels
            gap_duration = total_duration_sec - current_time
            if gap_duration > 0.1:
                pexels_seg = pexels_bag.fill_gap(
                    start_sec=current_time,
                    end_sec=total_duration_sec,
                    prefer_video=gap_duration >= 3.0,
                )
                if pexels_seg:
                    segments.append(pexels_seg)
            break
    
    return segments


class _PexelsBag:
    """Manages Pexels assets with no back-to-back repeats."""
    
    def __init__(
        self,
        videos: list[str | Path],
        photos: list[str | Path],
        rng: random.Random,
    ):
        self.videos = [str(v) for v in videos]
        self.photos = [str(p) for p in photos]
        self.rng = rng
        
        # Shuffle both bags
        self.rng.shuffle(self.videos)
        self.rng.shuffle(self.photos)
        
        self.video_index = 0
        self.photo_index = 0
        self.last_used = None
    
    def fill_gap(
        self,
        start_sec: float,
        end_sec: float,
        prefer_video: bool = True,
    ) -> MotionSegment | None:
        """Fill a gap with a Pexels asset (video or photo)."""
        duration = end_sec - start_sec
        
        # Decide video vs photo
        use_video = prefer_video and len(self.videos) > 0
        
        if use_video:
            # Take next video (wrap around if needed)
            if self.video_index >= len(self.videos):
                self.video_index = 0
                self.rng.shuffle(self.videos)
            
            video_path = self.videos[self.video_index]
            self.video_index += 1
            
            # Avoid back-to-back repeat
            if video_path == self.last_used and len(self.videos) > 1:
                # Swap with next
                if self.video_index >= len(self.videos):
                    self.video_index = 0
                video_path = self.videos[self.video_index]
                self.video_index += 1
            
            self.last_used = video_path
            
            return MotionSegment(
                type="pexels_video",
                path=video_path,
                start_sec=start_sec,
                end_sec=end_sec,
                zoom="hold",  # Videos don't zoom
            )
        elif len(self.photos) > 0:
            # Take next photo
            if self.photo_index >= len(self.photos):
                self.photo_index = 0
                self.rng.shuffle(self.photos)
            
            photo_path = self.photos[self.photo_index]
            self.photo_index += 1
            
            # Avoid back-to-back repeat
            if photo_path == self.last_used and len(self.photos) > 1:
                if self.photo_index >= len(self.photos):
                    self.photo_index = 0
                photo_path = self.photos[self.photo_index]
                self.photo_index += 1
            
            self.last_used = photo_path
            
            return MotionSegment(
                type="pexels_photo",
                path=photo_path,
                start_sec=start_sec,
                end_sec=end_sec,
                zoom="in",  # Will be set by caller
            )
        else:
            # No assets available
            return None


def export_motion_plan_json(segments: list[MotionSegment]) -> list[dict]:
    """Export motion plan to JSON-serializable format."""
    return [seg.to_dict() for seg in segments]
