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
    avg_cut_sec: float = 4.0,
) -> list[dict[str, Any]]:
    """
    Break script into timed segments based on ACTUAL audio duration.
    
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


def plan_avatar_video_shots(
    script: str,
    actual_audio_duration: float,
    avatar_pattern: dict,
    avg_cut_sec: float = 4.0,
) -> list[AvatarShot]:
    """
    Plan all shots for the video based on script and channel pattern.
    Mix avatar (talking head) with b-roll throughout.
    Uses ACTUAL audio duration to ensure shots cover the full voiceover.
    """
    segments = parse_script_to_segments(script, actual_audio_duration, avg_cut_sec)
    shots = []
    current_time = 0.0
    shot_index = 0
    
    opening_sec = avatar_pattern.get("opening_avatar_sec", 4.0)
    face_return_freq = avatar_pattern.get("face_return_frequency", 10.0)
    face_duration = avatar_pattern.get("face_shot_duration", 3.0)
    last_face_time = 0.0
    
    for seg_idx, seg in enumerate(segments):
        seg_start = current_time
        seg_end = seg_start + seg["duration"]
        
        # First segment(s) are avatar opening
        if current_time < opening_sec:
            shots.append(AvatarShot(
                index=shot_index,
                start_sec=seg_start,
                end_sec=seg_end,
                duration=seg["duration"],
                shot_type="avatar",
                text=seg["text"],
                visual_prompt="",
            ))
            last_face_time = seg_end
        else:
            # Check if it's time for face to return
            should_return_face = (
                face_return_freq is not None and
                (current_time - last_face_time) >= face_return_freq - 2.0
            )
            
            if should_return_face and seg["duration"] > 2.0:
                # Insert face shot
                face_dur = min(face_duration, seg["duration"] * 0.6)
                shots.append(AvatarShot(
                    index=shot_index,
                    start_sec=seg_start,
                    end_sec=seg_start + face_dur,
                    duration=face_dur,
                    shot_type="avatar",
                    text=seg["text"][:50],
                    visual_prompt="",
                ))
                last_face_time = current_time
                shot_index += 1
                
                # Remainder goes to b-roll if significant time left
                remaining = seg["duration"] - face_dur
                if remaining > 1.0:
                    # Use script context for b-roll, not just keywords
                    visual_prompt = seg["text"]
                    
                    shots.append(AvatarShot(
                        index=shot_index,
                        start_sec=seg_start + face_dur,
                        end_sec=seg_end,
                        duration=remaining,
                        shot_type="broll_still",
                        text=seg["text"],
                        visual_prompt=visual_prompt,
                    ))
            else:
                # B-roll shot - use script context
                visual_prompt = seg["text"]
                
                shots.append(AvatarShot(
                    index=shot_index,
                    start_sec=seg_start,
                    end_sec=seg_end,
                    duration=seg["duration"],
                    shot_type="broll_still",
                    text=seg["text"],
                    visual_prompt=visual_prompt,
                ))
        
        current_time = seg_end
        shot_index += 1
    
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
    """
    if progress:
        progress("Compositing avatar with b-roll...")
    
    # Build ffmpeg filter_complex to overlay b-roll at specific times
    # Base layer is the full avatar video
    filter_parts = []
    overlay_inputs = []
    last_output_label = "0:v"  # Start with avatar video
    
    broll_index = 1  # Input index (0 is avatar video)
    for shot in shots:
        if shot.shot_type == "broll_still" and shot.asset_path and Path(shot.asset_path).is_file():
            # Add this b-roll image as an input
            overlay_inputs.append(("-i", shot.asset_path))
            
            # Scale b-roll to match video size
            scale_label = f"broll{shot.index}scaled"
            filter_parts.append(f"[{broll_index}:v]scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2[{scale_label}]")
            
            # Overlay on avatar video at the right time
            next_label = f"out{shot.index}"
            filter_parts.append(
                f"[{last_output_label}][{scale_label}]overlay=enable='between(t,{shot.start_sec:.2f},{shot.end_sec:.2f})'[{next_label}]"
            )
            last_output_label = next_label
            broll_index += 1
    
    if not filter_parts:
        # No b-roll to overlay - still need to use original audio
        # Avatar video audio might be cut short by Kling
        cmd = [
            "ffmpeg", "-y",
            "-i", str(avatar_video_path),
            "-i", str(original_audio_path),
            "-map", "0:v",  # video from avatar
            "-map", "1:a",  # audio from original voiceover (not avatar video)
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
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
    
    # Add all b-roll inputs
    for inp_flag, inp_path in overlay_inputs:
        cmd.extend([inp_flag, inp_path])
    
    # Add original audio as final input
    cmd.extend(["-i", str(original_audio_path)])
    audio_input_index = len(overlay_inputs) + 1  # 0=avatar, 1..N=broll, N+1=audio
    
    # Add filter complex
    filter_str = ";".join(filter_parts)
    cmd.extend([
        "-filter_complex", filter_str,
        "-map", f"[{last_output_label}]",  # composited video
        "-map", f"{audio_input_index}:a",  # ORIGINAL voiceover audio (not avatar video audio)
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
    
    # Generate b-roll images for designated segments
    progress(f"Generating b-roll assets...")
    t0 = time.time()
    broll_count = 0
    
    for i, shot in enumerate(shots):
        if shot.shot_type == "broll_still":
            broll_count_needed = sum(1 for s in shots if s.shot_type == "broll_still")
            progress(f"Generating b-roll {broll_count + 1}/{broll_count_needed}...")
            broll_img_path = work_dir / f"broll_{shot.index:03d}.jpg"
            
            # Create detailed, contextual prompt from segment text
            # Include script context to ensure on-topic generation
            segment_text = shot.text or shot.visual_prompt
            # Build explicit prompt emphasizing the script topic and segment content
            prompt_text = (
                f"Professional photograph, photorealistic, high quality: "
                f"{segment_text}. "
                f"Context: {title}. "
                f"Relevant visual showing specific objects or scenes mentioned. "
                f"16:9 aspect ratio, no text, no captions."
            )
            
            if len(prompt_text) > 10:
                ok = generate_broll_image_atlas(
                    prompt_text,
                    broll_img_path,
                    progress,
                )
                
                if ok:
                    shot.asset_path = str(broll_img_path)
                    shot.is_generated = True
                    broll_count += 1
                else:
                    # B-roll generation failed - this is fatal
                    raise RuntimeError(
                        f"B-roll image generation failed for segment {shot.index}. "
                        "The avatar recipe requires b-roll images throughout the video."
                    )
    
    timing["broll_generation"] = time.time() - t0
    
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
