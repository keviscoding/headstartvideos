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
    first_still_min_hold_sec: float = 6.0,  # Kinetic hook: 6-10s first cut (Kevis prefers kinetic over 12s overhold)
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
        first_still_min_hold_sec: Minimum first still duration (kinetic hook, default 6s per Kevis;
                                  formula measured Whop @ 16.9s but Kevis rejects that overhold feel)
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
        min_hold = max(still_min_hold_sec, first_cut_min_sec) if i == 0 else still_min_hold_sec
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
    
    # Whop hook lock: first still owns [0, first_cut_target] (captions refresh inside)
    if still_spans:
        opener = still_spans[0]
        hold_end = min(total_duration_sec, max(first_cut_min_sec, first_cut_target_sec))
        # If next still starts before hold_end, push it (or absorb)
        opener.start_sec = 0.0
        opener.end_sec = hold_end
        # Drop / shift any stills that would hard-cut before hold_end
        kept = [opener]
        for s in still_spans[1:]:
            if s.start_sec < hold_end + 0.5:
                # merge spoken text into opener; skip as separate hard cut
                if s.text and not opener.text:
                    opener.text = s.text
                continue
            kept.append(s)
        still_spans = kept
        # Re-clamp later stills to min/max hold without invading neighbours
        for i, s in enumerate(still_spans):
            if i == 0:
                continue
            dur = s.end_sec - s.start_sec
            if dur < still_min_hold_sec:
                s.end_sec = min(total_duration_sec, s.start_sec + still_min_hold_sec)
            if i + 1 < len(still_spans):
                s.end_sec = min(s.end_sec, still_spans[i + 1].start_sec)
            s.end_sec = min(s.end_sec, s.start_sec + still_max_hold_sec, total_duration_sec)

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


# ── Whop / Dissect pacing locks (formula.md) ──────────────────────────────
WHOP_PACING = {
    "first_cut_min_sec": 12.0,
    "first_cut_target_sec": 14.5,
    "bed_min_sec": 5.0,
    "bed_target_sec": 7.0,
    "bed_max_sec": 12.0,
    "pexels_min_sec": 3.0,
    "pexels_max_sec": 7.0,
    "pic_per_min_lo": 15.0,
    "pic_per_min_hi": 22.0,
    "gfx_ratio_lo": 0.20,
    "gfx_ratio_hi": 0.25,
}


def coalesce_short_segments(
    segments: list[MotionSegment],
    min_bed_sec: float = 5.0,
    min_pexels_sec: float = 3.0,
) -> list[MotionSegment]:
    """Merge tiny bed crumbs into neighbours (fix rush / ~2s mean shots)."""
    if not segments:
        return []
    out: list[MotionSegment] = []
    for seg in segments:
        min_d = min_pexels_sec if seg.type.startswith("pexels") else min_bed_sec
        if seg.type == "motion_gfx":
            out.append(seg)
            continue
        if out and out[-1].type != "motion_gfx" and seg.duration < min_d:
            # extend previous to cover this crumb if same-ish role, else absorb forward
            prev = out[-1]
            if prev.type == seg.type or prev.type.startswith("ai_") or seg.duration < 1.2:
                prev.end_sec = seg.end_sec
                continue
        out.append(MotionSegment(seg.type, seg.path, seg.start_sec, seg.end_sec, seg.zoom, seg.text))
    # second pass: merge consecutive same-path
    merged: list[MotionSegment] = []
    for seg in out:
        if merged and merged[-1].path == seg.path and merged[-1].type == seg.type:
            merged[-1].end_sec = seg.end_sec
        else:
            merged.append(seg)
    return merged


def enforce_first_hard_cut(
    segments: list[MotionSegment],
    first_cut_min_sec: float = 12.0,
    first_cut_target_sec: float = 14.5,
) -> list[MotionSegment]:
    """Guarantee no path change before first_cut_min_sec (Whop hook breathe)."""
    if not segments:
        return segments
    target = max(first_cut_min_sec, first_cut_target_sec)
    opener = segments[0]
    # Prefer holding an ai_still opener; if first is short pexels, promote next still
    hold_path = opener.path
    hold_type = opener.type
    hold_zoom = opener.zoom
    hold_text = opener.text
    for seg in segments:
        if seg.type == "ai_still":
            hold_path, hold_type, hold_zoom = seg.path, seg.type, seg.zoom
            hold_text = seg.text or hold_text
            break
    rest = []
    for seg in segments:
        if seg.end_sec <= target + 1e-6:
            continue
        if seg.start_sec < target:
            rest.append(MotionSegment(
                seg.type, seg.path, target, seg.end_sec, seg.zoom, seg.text,
            ))
        else:
            rest.append(seg)
    opener_seg = MotionSegment(hold_type, hold_path, 0.0, target, hold_zoom, hold_text)
    return [opener_seg] + rest


def build_paced_bed_timeline(
    still_paths: list[Path | str],
    pexels_videos: list[Path | str],
    pexels_photos: list[Path | str],
    total_duration_sec: float,
    first_cut_target_sec: float = 14.5,
    bed_target_sec: float = 7.0,
    seed: int = 42,
) -> list[MotionSegment]:
    """Build a Whop-paced bed (no GFX yet): long opener + 5–9s stills/bridges.

    Target ~6–10 hard cuts / min equivalent on short smokes via longer holds
    (pic/min from captions + later GFX, not from chopping beds).
    """
    rng = random.Random(seed)
    stills = [str(p) for p in still_paths]
    videos = [str(p) for p in pexels_videos]
    photos = [str(p) for p in pexels_photos]
    if not stills:
        raise ValueError("need at least one Atlas still for paced timeline")
    segs: list[MotionSegment] = []
    t = 0.0
    # Opener still
    cut1 = min(total_duration_sec, max(12.0, first_cut_target_sec))
    segs.append(MotionSegment("ai_still", stills[0], 0.0, cut1, "in", ""))
    t = cut1
    si = 1
    vi = 0
    pi = 0
    rng.shuffle(videos)
    rng.shuffle(photos)
    use_video_next = True
    while t < total_duration_sec - 0.35:
        remaining = total_duration_sec - t
        # Prefer stills for symbolic beds; sprinkle pexels bridges 3–7s
        if use_video_next and videos and remaining > 4.0:
            dur = min(remaining, rng.uniform(3.2, 5.8))
            path = videos[vi % len(videos)]
            vi += 1
            segs.append(MotionSegment("pexels_video", path, t, t + dur, "hold", ""))
            t += dur
            use_video_next = False
        elif stills:
            dur = min(remaining, rng.uniform(5.0, min(8.5, bed_target_sec + 1.5)))
            if dur < 3.0 and remaining < 3.5:
                # extend last
                segs[-1].end_sec = total_duration_sec
                break
            path = stills[si % len(stills)]
            zoom = "out" if si % 2 else "in"
            si += 1
            segs.append(MotionSegment("ai_still", path, t, t + dur, zoom, ""))
            t += dur
            use_video_next = True
        elif photos:
            dur = min(remaining, rng.uniform(4.0, 7.0))
            path = photos[pi % len(photos)]
            pi += 1
            segs.append(MotionSegment("pexels_photo", path, t, t + dur, "in", ""))
            t += dur
        else:
            segs[-1].end_sec = total_duration_sec
            break
    if segs:
        segs[-1].end_sec = total_duration_sec
    return coalesce_short_segments(segs)


def pacing_metrics(segments: list[MotionSegment]) -> dict:
    """Compute Dissect-style timing metrics for QA / FIXES docs."""
    if not segments:
        return {}
    dur = segments[-1].end_sec - segments[0].start_sec
    durs = [s.duration for s in segments]
    # hard cut = path change
    cuts = sum(
        1 for i in range(1, len(segments))
        if segments[i].path != segments[i - 1].path
    )
    first_hard = None
    for i in range(1, len(segments)):
        if segments[i].path != segments[i - 1].path:
            first_hard = segments[i].start_sec
            break
    gfx = [s for s in segments if s.type == "motion_gfx"]
    gfx_dur = sum(s.duration for s in gfx)
    return {
        "duration_sec": dur,
        "segment_count": len(segments),
        "hard_cuts": cuts,
        "cuts_per_min": cuts / (dur / 60) if dur else 0,
        "pic_changes": len(segments),
        "pic_per_min": len(segments) / (dur / 60) if dur else 0,
        "mean_shot_sec": sum(durs) / len(durs),
        "median_shot_sec": sorted(durs)[len(durs) // 2],
        "first_hard_cut_sec": first_hard,
        "gfx_count": len(gfx),
        "gfx_dur_sec": gfx_dur,
        "gfx_ratio": gfx_dur / dur if dur else 0,
    }
