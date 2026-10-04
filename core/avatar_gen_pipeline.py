"""
AI Avatar Generator Pipeline for Channel Recipe.

Generates videos with speaking AI avatars following reference channel patterns.
- Avatar speaks the script
- B-roll shown as nouns/concepts
- Face returns based on channel's own tag patterns
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
    target_duration: float,
    avg_cut_sec: float = 4.0,
) -> list[dict[str, Any]]:
    """
    Break script into timed segments (~4 sec each for long-form list style).
    
    Returns list of {"text": str, "start_sec": float, "end_sec": float, "duration": float}
    """
    words = script.split()
    if not words:
        return []
    
    # Estimate speaking rate (150 wpm)
    wpm = 150
    total_words = len(words)
    speech_duration = (total_words / wpm) * 60.0
    
    # Number of segments based on cut rate
    num_segments = max(1, int(math.ceil(speech_duration / avg_cut_sec)))
    words_per_segment = max(1, int(math.ceil(total_words / num_segments)))
    
    segments = []
    current_time = 0.0
    
    for i in range(0, len(words), words_per_segment):
        chunk_words = words[i:i + words_per_segment]
        text = " ".join(chunk_words)
        
        # Calculate duration for this segment
        seg_duration = (len(chunk_words) / wpm) * 60.0
        
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
    # In production, would use LLM for better concept extraction
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
        # Default: avatar open, then leave (Frugal Japan style)
        return {
            "opening_avatar_sec": 15.0,
            "face_return_frequency": None,
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
        # No face detected in reference - unusual, use default
        return {
            "opening_avatar_sec": 15.0,
            "face_return_frequency": None,
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
        "opening_avatar_sec": min(opening_duration, 30.0),
        "face_return_frequency": face_return_frequency,
        "face_shot_duration": avg_face_duration,
        "use_title_cards": has_title_cards,
    }


def plan_avatar_video_shots(
    script: str,
    target_duration: float,
    avatar_pattern: dict,
    avg_cut_sec: float = 4.0,
) -> list[AvatarShot]:
    """
    Plan all shots for the video based on script and channel pattern.
    """
    segments = parse_script_to_segments(script, target_duration, avg_cut_sec)
    shots = []
    current_time = 0.0
    shot_index = 0
    
    opening_sec = avatar_pattern.get("opening_avatar_sec", 15.0)
    face_return_freq = avatar_pattern.get("face_return_frequency")
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
                (current_time - last_face_time) >= face_return_freq - 5.0
            )
            
            if should_return_face:
                # Insert face shot
                shots.append(AvatarShot(
                    index=shot_index,
                    start_sec=seg_start,
                    end_sec=min(seg_start + face_duration, seg_end),
                    duration=min(face_duration, seg["duration"]),
                    shot_type="avatar",
                    text=seg["text"][:50],  # Brief text
                    visual_prompt="",
                ))
                last_face_time = current_time
                shot_index += 1
                
                # Remainder goes to b-roll
                if seg["duration"] > face_duration + 0.5:
                    concepts = extract_visual_concepts(seg["text"])
                    visual_prompt = ", ".join(concepts) if concepts else "abstract background"
                    
                    shots.append(AvatarShot(
                        index=shot_index,
                        start_sec=seg_start + face_duration,
                        end_sec=seg_end,
                        duration=seg["duration"] - face_duration,
                        shot_type="broll_still",
                        text="",
                        visual_prompt=visual_prompt,
                    ))
            else:
                # B-roll shot
                concepts = extract_visual_concepts(seg["text"])
                visual_prompt = ", ".join(concepts) if concepts else "abstract background"
                
                shots.append(AvatarShot(
                    index=shot_index,
                    start_sec=seg_start,
                    end_sec=seg_end,
                    duration=seg["duration"],
                    shot_type="broll_still",
                    text="",
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
    """Generate b-roll still image using Atlas (black-forest-labs/flux-schnell)."""
    from core.atlas_llm import _atlas_key
    import httpx
    
    key = _atlas_key()
    if not key:
        return False
    
    model = "black-forest-labs/flux-schnell"
    body = {
        "model": model,
        "prompt": prompt,
        "aspect_ratio": "16:9",
        "resolution": "1k",
        "enable_sync_mode": False,
    }
    
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                "https://api.atlascloud.ai/api/v1/model/generateImage",
                headers=headers,
                json=body,
            )
            
            if resp.status_code >= 400:
                return False
            
            data = resp.json()
            pred_id = None
            if isinstance(data.get("data"), dict):
                pred_id = data["data"].get("id")
            pred_id = pred_id or data.get("id")
            
            if not pred_id:
                return False
            
            # Poll for completion
            max_wait = 90
            start_time = time.time()
            
            while time.time() - start_time < max_wait:
                time.sleep(1.5)
                
                poll_resp = client.get(
                    f"https://api.atlascloud.ai/api/v1/model/prediction/{pred_id}",
                    headers={"Authorization": f"Bearer {key}"},
                )
                
                inner = poll_resp.json().get("data", poll_resp.json())
                status = str(inner.get("status", "")).lower()
                
                if status in ("succeeded", "completed", "done"):
                    outputs = inner.get("outputs") or inner.get("output") or []
                    if isinstance(outputs, str):
                        img_url = outputs
                    elif isinstance(outputs, list) and outputs:
                        img_url = outputs[0]
                    else:
                        return False
                    
                    img_resp = client.get(img_url, follow_redirects=True, timeout=60)
                    img_resp.raise_for_status()
                    
                    output_path_obj = Path(output_path)
                    output_path_obj.parent.mkdir(parents=True, exist_ok=True)
                    output_path_obj.write_bytes(img_resp.content)
                    
                    return output_path_obj.stat().st_size > 1000
                
                elif status in ("failed", "error", "cancelled"):
                    return False
            
            return False
    
    except Exception:
        return False


def assemble_avatar_video(
    shots: list[AvatarShot],
    work_dir: Path,
    output_path: Path,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """
    Stitch all shots together into final video using ffmpeg.
    """
    if progress:
        progress("Assembling final video...")
    
    # Create concat file for ffmpeg
    concat_file = work_dir / "concat.txt"
    concat_lines = []
    
    for shot in shots:
        if shot.asset_path and Path(shot.asset_path).is_file():
            concat_lines.append(f"file '{shot.asset_path}'")
            concat_lines.append(f"duration {shot.duration}")
    
    if not concat_lines:
        raise RuntimeError("No shots to assemble")
    
    concat_file.write_text("\n".join(concat_lines))
    
    # Use ffmpeg to concatenate
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_path),
    ]
    
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600,
    )
    
    if result.returncode != 0 or not output_path.is_file():
        raise RuntimeError(f"FFmpeg assembly failed: {result.stderr}")
    
    if progress:
        progress("Video assembly complete!")
    
    return {
        "output_path": str(output_path),
        "shot_count": len(shots),
        "duration": sum(s.duration for s in shots),
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
    Main pipeline: generate AI avatar video following reference channel pattern.
    
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
    
    progress(f"Pattern detected: opening {avatar_pattern['opening_avatar_sec']:.0f}s, "
             f"returns: {avatar_pattern['face_return_frequency'] or 'no'}")
    
    # Plan all shots
    progress("Planning video shots...")
    shots = plan_avatar_video_shots(script, target_duration, avatar_pattern)
    
    progress(f"Planned {len(shots)} shots")
    
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
    
    # Generate TTS audio (using existing voiceover system)
    progress("Generating voiceover...")
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
    
    progress("Generating avatar and b-roll assets...")
    t0 = time.time()
    
    # Generate assets for each shot
    avatar_shots_generated = 0
    broll_shots_generated = 0
    
    for i, shot in enumerate(shots):
        if shot.shot_type == "avatar":
            # Generate speaking avatar video
            progress(f"Generating avatar shot {i+1}/{len(shots)}...")
            avatar_vid_path = work_dir / f"shot_{i:03d}_avatar.mp4"
            
            # Extract audio segment for this shot
            audio_segment = work_dir / f"audio_seg_{i:03d}.wav"
            subprocess.run([
                "ffmpeg", "-y",
                "-i", audio_path,
                "-ss", str(shot.start_sec),
                "-t", str(shot.duration),
                "-c", "copy",
                str(audio_segment),
            ], capture_output=True, timeout=30)
            
            ok = generate_avatar_video_atlas(
                avatar_img_path,
                audio_segment,
                avatar_vid_path,
                shot.duration,
                progress,
            )
            
            if ok:
                shot.asset_path = str(avatar_vid_path)
                shot.is_generated = True
                avatar_shots_generated += 1
            else:
                # Fallback: use static avatar image with audio
                progress(f"Avatar generation failed for shot {i+1}, using static fallback")
                fallback_path = work_dir / f"shot_{i:03d}_static.mp4"
                subprocess.run([
                    "ffmpeg", "-y",
                    "-loop", "1",
                    "-i", str(avatar_img_path),
                    "-i", str(audio_segment),
                    "-t", str(shot.duration),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac",
                    str(fallback_path),
                ], capture_output=True, timeout=60)
                shot.asset_path = str(fallback_path)
        
        elif shot.shot_type == "broll_still":
            # Generate b-roll still image
            progress(f"Generating b-roll {i+1}/{len(shots)}...")
            broll_img_path = work_dir / f"shot_{i:03d}_broll.jpg"
            
            ok = generate_broll_image_atlas(
                shot.visual_prompt,
                broll_img_path,
                progress,
            )
            
            if ok:
                # Convert still to video clip
                broll_vid_path = work_dir / f"shot_{i:03d}_broll.mp4"
                subprocess.run([
                    "ffmpeg", "-y",
                    "-loop", "1",
                    "-i", str(broll_img_path),
                    "-t", str(shot.duration),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(broll_vid_path),
                ], capture_output=True, timeout=60)
                shot.asset_path = str(broll_vid_path)
                shot.is_generated = True
                broll_shots_generated += 1
    
    timing["generation"] = time.time() - t0
    
    # Assemble final video
    progress("Assembling final video...")
    t0 = time.time()
    output_path = work_dir / "final_video.mp4"
    result = assemble_avatar_video(shots, work_dir, output_path, progress)
    timing["assembly"] = time.time() - t0
    
    timing["total"] = time.time() - t0_total
    
    return {
        "output_path": str(output_path),
        "shot_count": len(shots),
        "avatar_shots": avatar_shots_generated,
        "broll_shots": broll_shots_generated,
        "duration": sum(s.duration for s in shots),
        "timing": timing,
        "pattern": avatar_pattern,
    }
