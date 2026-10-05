"""
AI Avatar Generator Pipeline for Channel Recipe.

Generates videos with speaking AI avatars following reference channel patterns.
- Avatar speaks the full script (talking head with lip sync)
- B-roll images shown intermixed throughout the video
- Atlas Cloud for all generation (images, video, speaking avatar)
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ProgressFn = Callable[[str], None]


def _verify_motion(video_path: Path) -> tuple[bool, str]:
    """
    Verify that a video clip has visible motion.
    Uses ffmpeg freezedetect to check for frozen segments.
    Returns (has_motion, reason).
    """
    cmd = [
        "ffmpeg",
        "-i", str(video_path),
        "-vf", "freezedetect=n=0.001:d=2",
        "-f", "null",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    stderr = result.stderr or ""
    
    # freezedetect logs when it finds frozen segments
    freeze_lines = [line for line in stderr.split('\n') if 'freezedetect' in line.lower()]
    
    if any('freeze_start' in line for line in freeze_lines):
        return False, "freezedetect found frozen segments"
    
    return True, "Motion verified"


@dataclass
class AvatarShot:
    """One shot in the final video."""
    index: int
    start_sec: float
    end_sec: float
    duration: float
    shot_type: str  # "avatar", "broll_still", "broll_motion", "title_card"
    text: str  # script segment
    visual_prompt: str  # for b-roll generation
    asset_path: str = ""
    is_generated: bool = False


def parse_script_to_segments(
    script: str,
    actual_audio_duration: float,
    avg_cut_sec: float = 3.0,
) -> list[dict[str, Any]]:
    """
    Break script into timed segments based on ACTUAL audio duration.
    For ~20s Shorts, targets 6-7 segments (~3s each) to enable 3-4 b-roll shots.
    
    Returns list of {"text": str, "start_sec": float, "end_sec": float, "duration": float}
    """
    words = script.split()
    if not words:
        return []
    
    # Use ACTUAL audio duration to ensure all segments fit within the voiceover
    # Calculate number of segments based on desired cut rate
    num_segments = max(1, int(math.ceil(actual_audio_duration / avg_cut_sec)))
    words_per_segment = max(1, int(math.ceil(len(words) / num_segments)))
    
    segments = []
    current_time = 0.0
    # Distribute time evenly across segments
    time_per_segment = actual_audio_duration / num_segments
    
    for i in range(0, len(words), words_per_segment):
        chunk_words = words[i:i + words_per_segment]
        text = " ".join(chunk_words)
        
        # Use proportional duration based on actual audio
        seg_duration = time_per_segment
        
        segments.append({
            "text": text,
            "start_sec": current_time,
            "end_sec": current_time + seg_duration,
            "duration": seg_duration,
        })
        current_time += seg_duration
    
    return segments


def extract_visual_concepts(text: str) -> list[str]:
    """Extract concrete nouns and concepts from text for b-roll."""
    # Simple extraction: look for capitalized words and common nouns
    words = text.split()
    concepts = []
    
    # Extract capitalized words (likely proper nouns/places)
    for word in words:
        clean = re.sub(r'[^\w\s]', '', word)
        if clean and clean[0].isupper() and len(clean) > 2:
            concepts.append(clean.lower())
    
    return concepts[:2]  # Max 2 concepts per segment


def detect_channel_avatar_pattern(reference_tags: list[dict]) -> dict[str, Any]:
    """
    Analyze reference channel tags to determine avatar usage pattern.
    
    Returns pattern dict with:
    - opening_avatar_sec: how long avatar appears at start
    - face_return_frequency: seconds between face returns (or None)
    - face_shot_duration: typical duration of face shots
    - use_title_cards: whether to show list numbers as title cards
    """
    if not reference_tags:
        # Default: short avatar intro (3-5s), then mix with b-roll throughout
        return {
            "opening_avatar_sec": 4.0,
            "face_return_frequency": 10.0,
            "face_shot_duration": 3.0,
            "use_title_cards": False,
        }
    
    # Analyze tags to find face/avatar shots
    face_shots = []
    total_shots = len(reference_tags)
    
    for i, tag in enumerate(reference_tags):
        shot_type = (tag.get("type") or "").lower()
        visual = (tag.get("visual") or "").lower()
        timestamp = float(tag.get("timestamp") or tag.get("start_sec") or 0)
        duration = float(tag.get("duration") or 4.0)
        
        # Detect face/avatar shots
        is_face = (
            "face" in shot_type or "avatar" in shot_type or
            "face" in visual or "speaking" in visual or
            "presenter" in visual
        )
        
        if is_face:
            face_shots.append({
                "index": i,
                "timestamp": timestamp,
                "duration": duration,
            })
    
    if not face_shots:
        # No face detected in reference - use default short intro
        return {
            "opening_avatar_sec": 4.0,
            "face_return_frequency": 10.0,
            "face_shot_duration": 3.0,
            "use_title_cards": False,
        }
    
    # Calculate opening avatar duration
    first_face = face_shots[0]
    opening_duration = first_face["duration"]
    
    # Find first non-face shot after opening to determine when avatar leaves
    opening_end = first_face["timestamp"] + first_face["duration"]
    for tag in reference_tags:
        ts = float(tag.get("timestamp") or 0)
        if ts > opening_end:
            opening_duration = ts - first_face["timestamp"]
            break
    
    # Cap opening at 4 seconds to ensure b-roll appears throughout
    # Even if reference shows longer, we don't want face to dominate a Short
    opening_duration = min(opening_duration, 4.0)
    
    # Check for face returns
    face_return_frequency = None
    if len(face_shots) > 1:
        # Calculate average time between face returns
        gaps = []
        for i in range(1, len(face_shots)):
            gap = face_shots[i]["timestamp"] - (
                face_shots[i-1]["timestamp"] + face_shots[i-1]["duration"]
            )
            if gap > 10:  # Only count meaningful gaps
                gaps.append(gap)
        
        if gaps:
            face_return_frequency = sum(gaps) / len(gaps)
    
    # Average face shot duration
    avg_face_duration = sum(s["duration"] for s in face_shots) / len(face_shots)
    
    # Detect title cards (numbers on screen)
    has_title_cards = any(
        "title" in (tag.get("type") or "").lower() or
        "number" in (tag.get("visual") or "").lower() or
        "card" in (tag.get("visual") or "").lower()
        for tag in reference_tags[:20]  # Check first 20 shots
    )
    
    return {
        "opening_avatar_sec": opening_duration,
        "face_return_frequency": face_return_frequency or 10.0,
        "face_shot_duration": avg_face_duration,
        "use_title_cards": has_title_cards,
    }


def _find_segment_for_time(segments: list[dict], time_sec: float) -> str:
    """Find the script text being spoken at the given time."""
    for seg in segments:
        if seg["start_sec"] <= time_sec < seg["end_sec"]:
            return seg["text"]
    # Fallback: return closest segment
    if segments:
        return segments[-1]["text"] if time_sec >= segments[-1]["end_sec"] else segments[0]["text"]
    return ""


def plan_avatar_video_shots(
    script: str,
    actual_audio_duration: float,
    avatar_pattern: dict,
    avg_cut_sec: float = 3.0,
) -> list[AvatarShot]:
    """
    Plan all shots for the video based on script and channel pattern.
    Mix avatar (talking head) with b-roll throughout.
    Uses ACTUAL audio duration to ensure shots cover the full voiceover.
    Always ends on the avatar for a strong finish.
    
    B-roll count scales with middle section length at ~2.5-4s per shot:
    - ~20s Short: 3-4 b-roll shots
    - ~60s video: 13-15 b-roll shots
    
    Each b-roll's text matches what's being spoken during that shot's time window.
    """
    segments = parse_script_to_segments(script, actual_audio_duration, avg_cut_sec)
    shots = []
    shot_index = 0
    
    opening_sec = avatar_pattern.get("opening_avatar_sec", 4.0)
    face_return_freq = avatar_pattern.get("face_return_frequency", 10.0)
    face_duration = avatar_pattern.get("face_shot_duration", 3.0)
    
    # Reserve last ~3 seconds for avatar ending
    ending_avatar_sec = 3.0
    broll_end_time = actual_audio_duration - ending_avatar_sec
    
    # Get text for opening from segments covering that time
    opening_midpoint = opening_sec / 2
    opening_text = _find_segment_for_time(segments, opening_midpoint)
    
    # Create opening avatar shot (single shot, exact duration)
    shots.append(AvatarShot(
        index=shot_index,
        start_sec=0.0,
        end_sec=opening_sec,
        duration=opening_sec,
        shot_type="avatar",
        text=opening_text,
        visual_prompt="",
    ))
    shot_index += 1
    last_face_time = opening_sec
    
    # Middle section: b-roll with optional face returns
    # Scale b-roll count with middle duration at ~2.5-4s per shot
    middle_duration = broll_end_time - opening_sec
    
    # Target 2.5-4s per b-roll shot (aim for ~3.2s average)
    target_broll_count = max(3, int(middle_duration / 3.2))
    ideal_broll_duration = middle_duration / target_broll_count
    
    current_time = opening_sec
    broll_added = 0
    
    while current_time < broll_end_time:
        # Calculate how much time is left in middle section
        remaining_time = broll_end_time - current_time
        
        # Check if it's time for face to return (based on frequency pattern)
        time_since_face = current_time - last_face_time
        should_return_face = (
            face_return_freq is not None and
            time_since_face >= face_return_freq - 1.0 and
            remaining_time > face_duration + ideal_broll_duration  # Need room for face + at least one more b-roll
        )
        
        if should_return_face:
            # Insert face return
            face_midpoint = current_time + face_duration / 2
            face_text = _find_segment_for_time(segments, face_midpoint)
            
            shots.append(AvatarShot(
                index=shot_index,
                start_sec=current_time,
                end_sec=current_time + face_duration,
                duration=face_duration,
                shot_type="avatar",
                text=face_text,
                visual_prompt="",
            ))
            current_time += face_duration
            last_face_time = current_time
            shot_index += 1
        else:
            # B-roll shot
            # Use calculated duration, but cap at remaining time for last shot
            this_duration = min(ideal_broll_duration, remaining_time)
            
            # Get text from segment covering this shot's midpoint
            broll_midpoint = current_time + this_duration / 2
            broll_text = _find_segment_for_time(segments, broll_midpoint)
            
            shots.append(AvatarShot(
                index=shot_index,
                start_sec=current_time,
                end_sec=current_time + this_duration,
                duration=this_duration,
                shot_type="broll_still",
                text=broll_text,
                visual_prompt=broll_text,
            ))
            current_time += this_duration
            shot_index += 1
            broll_added += 1
    
    # Get text for ending from segments covering that time
    ending_midpoint = broll_end_time + (actual_audio_duration - broll_end_time) / 2
    ending_text = _find_segment_for_time(segments, ending_midpoint)
    
    # Create ending avatar shot
    shots.append(AvatarShot(
        index=shot_index,
        start_sec=broll_end_time,
        end_sec=actual_audio_duration,
        duration=actual_audio_duration - broll_end_time,
        shot_type="avatar",
        text=ending_text,
        visual_prompt="",
    ))
    
    return shots


def generate_avatar_video_atlas(
    image_path: str | Path,
    audio_path: str | Path,
    output_path: str | Path,
    duration: float,
    progress: ProgressFn | None = None,
) -> bool:
    """
    Generate speaking avatar video using Atlas Cloud (kwaivgi/kling-v2.6-std/avatar).
    """
    from core.atlas_llm import _atlas_key, _image_payload_for_atlas
    import httpx
    
    key = _atlas_key()
    if not key:
        if progress:
            progress("Atlas Cloud key not configured")
        return False
    
    if progress:
        progress("Generating speaking avatar video...")
    
    try:
        image_val = _image_payload_for_atlas(image_path)
    except Exception as e:
        if progress:
            progress(f"Could not load avatar image: {e}")
        return False
    
    # Load audio as base64
    audio_path_obj = Path(audio_path)
    if not audio_path_obj.is_file():
        if progress:
            progress(f"Audio file not found: {audio_path}")
        return False
    
    import base64
    audio_bytes = audio_path_obj.read_bytes()
    audio_b64 = base64.b64encode(audio_bytes).decode('ascii')
    
    # Prepare request
    model = "kwaivgi/kling-v2.6-std/avatar"
    body = {
        "model": model,
        "image": image_val,
        "audio": f"data:audio/wav;base64,{audio_b64}",
        "duration": int(duration),
    }
    
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    
    timeout = httpx.Timeout(connect=30.0, read=120.0, write=120.0, pool=30.0)
    
    try:
        with httpx.Client(timeout=timeout) as client:
            # Create prediction
            resp = client.post(
                "https://api.atlascloud.ai/api/v1/model/generateVideo",
                headers=headers,
                json=body,
            )
            
            if resp.status_code >= 400:
                data = resp.json() if resp.content else {}
                # Check for fatal errors that should not retry
                from core.atlas_llm import _is_atlas_fatal_error
                is_fatal, fatal_reason = _is_atlas_fatal_error(data)
                if is_fatal:
                    if progress:
                        progress(fatal_reason)
                    return False
                if progress:
                    progress(f"Atlas avatar generation failed: HTTP {resp.status_code}")
                return False
            
            data = resp.json()
            pred_id = None
            if isinstance(data.get("data"), dict):
                pred_id = data["data"].get("id")
            pred_id = pred_id or data.get("id")
            
            if not pred_id:
                if progress:
                    progress("No prediction ID returned")
                return False
            
            # Poll for completion (avatar gen can take 2-5 minutes)
            max_wait = 600  # 10 minutes
            start_time = time.time()
            sleep_interval = 3.0
            
            while time.time() - start_time < max_wait:
                time.sleep(sleep_interval)
                sleep_interval = min(8.0, sleep_interval + 0.5)
                
                poll_resp = client.get(
                    f"https://api.atlascloud.ai/api/v1/model/prediction/{pred_id}",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=httpx.Timeout(connect=20.0, read=90.0, write=30.0, pool=20.0),
                )
                
                inner = poll_resp.json().get("data", poll_resp.json())
                if not isinstance(inner, dict):
                    continue
                
                status = str(inner.get("status", "")).lower()
                
                if status in ("succeeded", "completed", "done"):
                    outputs = inner.get("outputs") or inner.get("output") or []
                    if isinstance(outputs, str):
                        video_url = outputs
                    elif isinstance(outputs, list) and outputs:
                        video_url = outputs[0]
                    else:
                        if progress:
                            progress("No video output in completed response")
                        return False
                    
                    # Download video
                    vid_resp = client.get(
                        video_url,
                        follow_redirects=True,
                        timeout=httpx.Timeout(connect=30.0, read=180.0, write=30.0, pool=30.0),
                    )
                    vid_resp.raise_for_status()
                    
                    output_path_obj = Path(output_path)
                    output_path_obj.parent.mkdir(parents=True, exist_ok=True)
                    output_path_obj.write_bytes(vid_resp.content)
                    
                    if output_path_obj.stat().st_size < 1000:
                        if progress:
                            progress("Downloaded video file is too small")
                        return False
                    
                    if progress:
                        progress(f"Avatar video generated: {output_path_obj.name}")
                    return True
                
                elif status in ("failed", "error", "cancelled"):
                    error = inner.get("error") or inner.get("message") or "Generation failed"
                    # Check for fatal errors
                    from core.atlas_llm import _is_atlas_fatal_error
                    is_fatal, fatal_reason = _is_atlas_fatal_error(inner)
                    if is_fatal:
                        if progress:
                            progress(fatal_reason)
                        return False
                    if progress:
                        progress(f"Avatar generation failed: {error}")
                    return False
            
            if progress:
                progress(f"Avatar generation timed out after {max_wait}s")
            return False
    
    except Exception as e:
        if progress:
            progress(f"Atlas error: {e}")
        return False


def select_shot_framing(broll_index: int, segment_text: str, prev_framing: str | None, total_broll_shots: int) -> dict[str, str]:
    """
    Deterministically select shot framing type for b-roll variety.
    Uses the b-roll shot index (not overall shot index) for proper round-robin.
    
    Args:
        broll_index: Index among b-roll shots only (0, 1, 2, ...)
        segment_text: Script text for this segment
        prev_framing: Previous framing to avoid consecutive repeats
        total_broll_shots: Total number of b-roll shots expected
    
    Returns dict with:
    - framing: "close_up", "wide", "over_shoulder", "detail", "medium"
    - directive: prompt text specifying the framing
    - camera_move: camera movement for i2v motion prompt
    
    First 3 b-rolls always include at least one close-up/detail and one wide.
    """
    # Order ensures first 3 shots hit close-up, detail, and wide
    framings = [
        {
            "framing": "close_up",
            "directive": "Close-up shot focusing on hands, facial expressions, or key physical objects in sharp detail",
            "camera_move": "gentle camera drift closer",
        },
        {
            "framing": "detail",
            "directive": "Extreme macro detail shot of textures, surfaces, or edges - card corner, paper texture, fabric weave, hand gesture",
            "camera_move": "subtle camera push forward",
        },
        {
            "framing": "wide",
            "directive": "Wide establishing shot showing the full environment, people, and spatial context",
            "camera_move": "slow camera pan revealing scene",
        },
        {
            "framing": "over_shoulder",
            "directive": "Over-the-shoulder perspective showing hands interacting with objects, natural background",
            "camera_move": "handheld camera following action",
        },
        {
            "framing": "medium",
            "directive": "Medium shot from waist or chest level showing person and their immediate interaction space",
            "camera_move": "camera slowly pushes forward",
        },
    ]
    
    # Use round-robin based on b-roll index
    base_index = broll_index % len(framings)
    
    # Select framing, avoiding previous if possible
    selected = framings[base_index]
    if prev_framing and selected["framing"] == prev_framing:
        # Pick next framing to ensure variety
        selected = framings[(base_index + 1) % len(framings)]
    
    return selected


def build_motion_prompt(segment_text: str, camera_move: str) -> str:
    """
    Build deterministic motion prompt for Atlas i2v from segment text and camera move.
    
    Extracts key action/subject from text and combines with camera movement.
    No LLM call - uses simple text processing.
    
    Args:
        segment_text: Script text for this shot (e.g. "scan your card at the desk")
        camera_move: Camera movement from framing (e.g. "gentle camera drift closer")
    
    Returns:
        Motion prompt like "Person scanning card at desk, gentle camera drift closer, realistic"
    """
    # Clean and extract key phrases
    text = segment_text.lower().strip()
    
    # Remove common filler words but keep action words
    filler = ["the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with"]
    words = [w for w in text.split() if w not in filler]
    
    # Take first 5-8 words as the action/subject
    action_words = words[:min(8, len(words))]
    action_phrase = " ".join(action_words)
    
    # Build motion prompt: action + camera move + realistic
    motion_prompt = f"{action_phrase}, {camera_move}, realistic"
    
    # Cap at reasonable length
    if len(motion_prompt) > 200:
        motion_prompt = motion_prompt[:197] + "..."
    
    return motion_prompt


def _generate_broll_motion_parallel(
    broll_stills: list[dict],
    work_dir: Path,
    progress: ProgressFn | None = None,
) -> list[dict]:
    """
    Generate motion clips from b-roll stills in parallel using Atlas i2v.
    
    Args:
        broll_stills: List of dicts with still_path, motion_prompt, duration, shot
        work_dir: Working directory for output
        progress: Progress callback
    
    Returns:
        List of dicts with video_path, shot
        
    Raises:
        RuntimeError: If any i2v generation fails after retries
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from core.atlas_llm import generate_video_file
    import config
    
    if not broll_stills:
        return []
    
    max_workers = min(len(broll_stills), getattr(config, "ATLAS_I2V_CONCURRENCY", 5))
    
    def generate_one_clip(still_info: dict) -> dict:
        """Generate one motion clip with retries."""
        still_path = still_info["still_path"]
        motion_prompt = still_info["motion_prompt"]
        duration = int(max(4, min(12, still_info["duration"])))  # Atlas i2v duration 4-12s
        shot = still_info["shot"]
        
        # Output path for the raw i2v clip (before trimming)
        raw_video_path = work_dir / f"broll_{shot.index:03d}_i2v.mp4"
        
        # Generate motion clip with Atlas i2v (max 3 attempts with backoff)
        ok = generate_video_file(
            prompt=motion_prompt,
            image=still_path,
            output_path=raw_video_path,
            duration=duration,
            resolution="720p",
            generate_audio=False,
            camera_fixed=False,
            max_attempts=3,
            timeout_sec=600,  # 10 min per clip
        )
        
        if not ok or not raw_video_path.is_file():
            raise RuntimeError(
                f"Atlas i2v failed for shot {shot.index} after retries. "
                f"Prompt: '{motion_prompt[:100]}...'. "
                "No fallback to static - avatar recipe requires real motion."
            )
        
        # Trim to exact shot duration
        trimmed_path = work_dir / f"broll_{shot.index:03d}_motion.mp4"
        trim_cmd = [
            "ffmpeg", "-y",
            "-i", str(raw_video_path),
            "-t", f"{still_info['duration']:.2f}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-an",  # Remove audio (i2v audio not needed)
            str(trimmed_path),
        ]
        
        result = subprocess.run(trim_cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0 or not trimmed_path.is_file():
            raise RuntimeError(
                f"Failed to trim i2v clip for shot {shot.index}: {result.stderr[:200]}"
            )
        
        # Verify motion with freeze check
        has_motion, reason = _verify_motion(trimmed_path)
        if not has_motion:
            raise RuntimeError(
                f"Generated i2v clip {shot.index} failed freeze check: {reason}"
            )
        
        return {
            "shot": shot,
            "video_path": trimmed_path,
            "motion_prompt": motion_prompt,
        }
    
    # Generate all clips in parallel
    results = []
    completed = 0
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(generate_one_clip, still): still for still in broll_stills}
        
        for future in as_completed(futures):
            still = futures[future]
            try:
                result = future.result()
                results.append(result)
                completed += 1
                if progress:
                    progress(f"B-roll motion {completed}/{len(broll_stills)} complete")
            except Exception as e:
                # Fast fail on first error
                raise RuntimeError(
                    f"B-roll motion generation failed for shot {still['shot'].index}: {e}"
                ) from e
    
    return results


def generate_broll_image_atlas(
    prompt: str,
    output_path: str | Path,
    progress: ProgressFn | None = None,
) -> bool:
    """
    Generate b-roll still image using Atlas.
    Uses the same proven model as avatar image generation (google/nano-banana-2-lite).
    """
    from core.atlas_llm import generate_image_file
    
    # Use the same model that already succeeded for avatar image
    # (google/nano-banana-2-lite/text-to-image-developer, not flux-schnell)
    try:
        ok = generate_image_file(
            prompt,
            str(output_path),
            progress=progress,
        )
        return ok
    except Exception as e:
        if progress:
            progress(f"B-roll generation failed: {e}")
        return False


def assemble_mixed_avatar_broll_video(
    avatar_video_path: Path,
    original_audio_path: Path,
    shots: list[AvatarShot],
    work_dir: Path,
    output_path: Path,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """
    Create final video by overlaying b-roll on specific segments of the avatar video.
    The avatar speaks continuously; b-roll appears on top at designated times.
    Uses ORIGINAL voiceover audio to ensure no words are cut off.
    Pads video if shorter than audio to prevent audio cutoff.
    """
    if progress:
        progress("Compositing avatar with b-roll...")
    
    # Get avatar video duration
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(avatar_video_path),
        ],
        capture_output=True, text=True, timeout=30,
    )
    avatar_video_duration = float((probe.stdout or "0").strip() or 0)
    
    # Get original audio duration
    audio_probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(original_audio_path),
        ],
        capture_output=True, text=True, timeout=30,
    )
    audio_duration = float((audio_probe.stdout or "0").strip() or 0)
    
    # Calculate padding needed (add small buffer so last word not clipped)
    padding_needed = max(0.0, audio_duration - avatar_video_duration + 0.2)
    
    # Build ffmpeg filter_complex to overlay b-roll at specific times
    # Base layer is the full avatar video
    filter_parts = []
    overlay_inputs = []
    last_output_label = "0:v"  # Start with avatar video
    
    # Use the already-generated Atlas i2v motion clips (no Ken Burns needed)
    broll_index = 1  # Input index (0 is avatar video)
    for shot in shots:
        if shot.shot_type == "broll_still" and shot.asset_path and Path(shot.asset_path).is_file():
            # The asset_path now points to the trimmed i2v motion clip
            motion_path = Path(shot.asset_path)
            
            if not motion_path.is_file():
                raise RuntimeError(
                    f"B-roll motion clip missing for shot {shot.index}: {motion_path}"
                )

            # Align the motion clip to the shot window on the main timeline
            overlay_inputs.append(("-itsoffset", f"{shot.start_sec:.3f}", "-i", str(motion_path)))

            scale_label = f"broll{shot.index}scaled"
            filter_parts.append(
                f"[{broll_index}:v]scale=1280:720:force_original_aspect_ratio=decrease,"
                f"pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p[{scale_label}]"
            )

            next_label = f"out{shot.index}"
            filter_parts.append(
                f"[{last_output_label}][{scale_label}]overlay=enable='between(t,{shot.start_sec:.2f},{shot.end_sec:.2f})'[{next_label}]"
            )
            last_output_label = next_label
            broll_index += 1
    
    if not filter_parts:
        # No b-roll to overlay - still need to use original audio and pad if needed
        if padding_needed > 0.1:
            # Pad video to match voiceover duration (hold last frame)
            cmd = [
                "ffmpeg", "-y",
                "-i", str(avatar_video_path),
                "-i", str(original_audio_path),
                "-filter_complex", f"[0:v]tpad=stop_mode=clone:stop_duration={padding_needed:.3f}[v]",
                "-map", "[v]",  # padded video
                "-map", "1:a",  # audio from original voiceover
                "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                str(output_path),
            ]
        else:
            # Video already long enough, just replace audio
            cmd = [
                "ffmpeg", "-y",
                "-i", str(avatar_video_path),
                "-i", str(original_audio_path),
                "-map", "0:v",  # video from avatar
                "-map", "1:a",  # audio from original voiceover
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                str(output_path),
            ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"Audio replacement failed: {result.stderr}")
        return {
            "output_path": str(output_path),
            "shot_count": len(shots),
        }
    
    # Build ffmpeg command with original audio as additional input
    cmd = ["ffmpeg", "-y", "-i", str(avatar_video_path)]
    
    # Add all b-roll inputs (may include -itsoffset before each -i)
    for inp in overlay_inputs:
        cmd.extend(list(inp))
    
    # Add original audio as final input
    cmd.extend(["-i", str(original_audio_path)])
    audio_input_index = len(overlay_inputs) + 1  # 0=avatar, 1..N=broll, N+1=audio
    
    # Add filter complex with precise video padding if needed
    filter_str = ";".join(filter_parts)
    if padding_needed > 0.1:
        # Pad the composited video to match audio duration
        filter_str += f";[{last_output_label}]tpad=stop_mode=clone:stop_duration={padding_needed:.3f}[vfinal]"
        video_output_label = "[vfinal]"
    else:
        # Video already long enough
        video_output_label = f"[{last_output_label}]"
    
    cmd.extend([
        "-filter_complex", filter_str,
        "-map", video_output_label,  # composited video (padded if needed)
        "-map", f"{audio_input_index}:a",  # ORIGINAL voiceover audio
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_path),
    ])
    
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600,
    )
    
    if result.returncode != 0 or not output_path.is_file():
        raise RuntimeError(f"FFmpeg composition failed: {result.stderr}")
    
    if progress:
        progress("Video composition complete!")
    
    return {
        "output_path": str(output_path),
        "shot_count": len(shots),
    }


def run_avatar_gen_pipeline(
    script: str,
    avatar_source: str | Path,  # path to avatar image or "prompt:description"
    reference_channel_tags: list[dict] | None = None,
    target_duration: float = 120.0,
    title: str = "",
    work_dir: Path | None = None,
    progress_callback: ProgressFn | None = None,
) -> dict[str, Any]:
    """
    Main pipeline: generate AI avatar video with b-roll following reference channel pattern.
    
    Flow:
    1. Generate avatar image (if needed)
    2. Generate full voiceover audio
    3. Generate ONE talking avatar video for the complete script
    4. Plan shots (when to show avatar vs b-roll)
    5. Generate b-roll images for designated segments
    6. Composite: overlay b-roll on avatar video at designated times
    
    The avatar speaks continuously; b-roll appears intermixed.
    
    Args:
        script: Full narration script
        avatar_source: Path to avatar image OR "prompt:..." to generate one
        reference_channel_tags: Tags from reference channel video(s) for pattern learning
        target_duration: Target video length in seconds
        title: Video title
        work_dir: Working directory for temp files
        progress_callback: Progress updates
    
    Returns:
        dict with output_path, shot_count, duration, timing, etc.
    """
    if not work_dir:
        work_dir = Path("output") / "avatar_gen" / str(int(time.time()))
    work_dir.mkdir(parents=True, exist_ok=True)
    
    timing = {}
    t0_total = time.time()
    
    def progress(msg: str):
        if progress_callback:
            progress_callback(msg)
    
    progress("Analyzing reference channel pattern...")
    
    # Detect pattern from reference tags
    avatar_pattern = detect_channel_avatar_pattern(reference_channel_tags or [])
    
    progress(f"Pattern: opening {avatar_pattern['opening_avatar_sec']:.0f}s, "
             f"b-roll returns every ~{avatar_pattern['face_return_frequency']:.0f}s")
    
    # Generate or load avatar image
    avatar_img_path = work_dir / "avatar.jpg"
    if str(avatar_source).startswith("prompt:"):
        # Generate avatar image from prompt
        progress("Generating avatar image...")
        prompt = str(avatar_source)[7:].strip()
        from core.atlas_llm import generate_image_file
        ok = generate_image_file(prompt, str(avatar_img_path), progress=progress)
        if not ok:
            raise RuntimeError(f"Failed to generate avatar image from prompt: {prompt[:100]}")
    else:
        # Copy provided image
        import shutil
        shutil.copy(avatar_source, avatar_img_path)
    
    # Generate TTS audio for full script
    progress("Generating voiceover for full script...")
    t0 = time.time()
    from core.voiceover_gen import generate_voiceover
    audio_path = generate_voiceover(
        script=script,
        voice="Kore",
        style_preset="Narrator",
        custom_notes="",
        output_dir=str(work_dir / "audio"),
    )
    timing["voiceover"] = time.time() - t0
    
    # Get actual audio duration
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", audio_path,
        ],
        capture_output=True, text=True, timeout=30,
    )
    audio_duration = float((probe.stdout or "0").strip() or 0)
    if audio_duration < 1.0:
        raise RuntimeError("Failed to get voiceover duration")
    
    progress(f"Voiceover ready: {audio_duration:.1f} seconds")
    
    # Generate ONE full-length talking avatar video with the complete audio
    progress(f"Generating talking avatar video ({audio_duration:.1f}s)...")
    t0 = time.time()
    avatar_video_path = work_dir / "avatar_full.mp4"
    
    ok = generate_avatar_video_atlas(
        avatar_img_path,
        audio_path,
        avatar_video_path,
        audio_duration,
        progress,
    )
    
    if not ok:
        # Atlas avatar generation failed - this is fatal, do not fall back to static
        raise RuntimeError(
            "Atlas talking avatar generation failed. The avatar recipe requires a "
            "speaking avatar with lip sync. A static image is not acceptable."
        )
    
    timing["avatar_generation"] = time.time() - t0
    
    # Plan all shots (when to show avatar face vs b-roll)
    progress("Planning b-roll segments...")
    shots = plan_avatar_video_shots(script, audio_duration, avatar_pattern)
    
    # Generate b-roll stills and animate them with Atlas i2v in parallel
    progress(f"Generating b-roll motion clips...")
    t0 = time.time()
    
    # Step 1: Generate all b-roll stills first (fast, sequential is fine)
    broll_shots = [s for s in shots if s.shot_type == "broll_still"]
    broll_count = len(broll_shots)
    prev_framing = None
    broll_stills = []
    
    for idx, shot in enumerate(broll_shots):
        progress(f"Generating b-roll still {idx + 1}/{broll_count}...")
        broll_img_path = work_dir / f"broll_{shot.index:03d}.jpg"
        
        # Create detailed, contextual prompt from segment text
        segment_text = shot.text or shot.visual_prompt
        
        # Select shot framing for variety using b-roll index
        framing = select_shot_framing(idx, segment_text, prev_framing, broll_count)
        prev_framing = framing["framing"]
        
        # Build explicit prompt emphasizing the script topic and segment content
        prompt_text = (
            f"Professional photograph, photorealistic, high quality. "
            f"{framing['directive']}. "
            f"Scene: {segment_text}. "
            f"Context: {title}. "
            f"Relevant visual showing specific objects or scenes mentioned. "
            f"16:9 aspect ratio. "
            f"No text, no captions, no logos, no brand marks, no watermarks, "
            f"no credit cards, no payment cards, no trademarks, no readable labels. "
            f"If people appear, keep them consistent with the script and the speaking "
            f"avatar (same gender and role as the on-camera host when that role is shown)."
        )
        
        ok = generate_broll_image_atlas(
            prompt_text,
            broll_img_path,
            progress,
        )
        
        if not ok:
            raise RuntimeError(
                f"B-roll still generation failed for shot {shot.index}. "
                "The avatar recipe requires b-roll images throughout the video."
            )
        
        # Build motion prompt from segment text and camera move
        motion_prompt = build_motion_prompt(segment_text, framing["camera_move"])
        
        broll_stills.append({
            "shot": shot,
            "still_path": broll_img_path,
            "motion_prompt": motion_prompt,
            "duration": shot.duration,
        })
    
    timing["broll_stills"] = time.time() - t0
    
    # Step 2: Animate all stills to motion clips in parallel using Atlas i2v
    progress(f"Animating {broll_count} b-roll clips with Atlas i2v...")
    t0 = time.time()
    
    broll_clips = _generate_broll_motion_parallel(
        broll_stills,
        work_dir,
        progress,
    )
    
    # Assign generated clips back to shots
    for clip_info in broll_clips:
        clip_info["shot"].asset_path = str(clip_info["video_path"])
        clip_info["shot"].is_generated = True
    
    timing["broll_motion"] = time.time() - t0
    
    # Avatar recipe requires b-roll throughout - fail if we couldn't generate any
    if broll_count == 0:
        raise RuntimeError(
            "No b-roll assets were generated. The avatar recipe requires b-roll images "
            "intermixed with the talking avatar throughout the video. A continuous locked "
            "shot of only the avatar is not acceptable."
        )
    
    progress(f"Compositing {broll_count} b-roll segments...")
    t0 = time.time()
    output_path = work_dir / "final_video.mp4"
    result = assemble_mixed_avatar_broll_video(
        avatar_video_path, audio_path, shots, work_dir, output_path, progress
    )
    timing["assembly"] = time.time() - t0
    
    # Verify final video duration matches or exceeds audio duration
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(output_path),
        ],
        capture_output=True, text=True, timeout=30,
    )
    final_duration = float((probe.stdout or "0").strip() or 0)
    
    # Video must be at least as long as audio (tolerance: 0.5s)
    if final_duration < audio_duration - 0.5:
        raise RuntimeError(
            f"Final video ({final_duration:.1f}s) is shorter than audio ({audio_duration:.1f}s). "
            f"The voiceover would be cut off mid-script. This is not acceptable."
        )
    
    # Verify audio track plays through completely
    audio_probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(output_path),
        ],
        capture_output=True, text=True, timeout=30,
    )
    audio_track_duration = float((audio_probe.stdout or "0").strip() or 0)
    
    if audio_track_duration < audio_duration - 0.5:
        raise RuntimeError(
            f"Audio track ({audio_track_duration:.1f}s) is shorter than expected ({audio_duration:.1f}s). "
            f"The script would be cut off before the final word."
        )
    
    timing["total"] = time.time() - t0_total
    
    return {
        "output_path": str(output_path),
        "shot_count": len(shots),
        "avatar_shots": sum(1 for s in shots if s.shot_type == "avatar"),
        "broll_shots": broll_count,
        "duration": final_duration,
        "timing": timing,
        "pattern": avatar_pattern,
        "slots": [],  # For compatibility with cook_runner
    }
