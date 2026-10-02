"""
Frontier Atlas Image Generation - gpt-image-2 stills with scene prompts.

Generates AI stills using Atlas Cloud's gpt-image-2 model, matching the
Whop Frontier visual style with:
1. Scene-based prompts (not quote-on-paper)
2. Photorealistic/cinematic style matching Frontier look
3. Word-locked timing from script segmentation
"""

from __future__ import annotations
import os
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.atlas_llm import generate_text as atlas_llm_text
from config import ATLASCLOUD_KEY


def generate_scene_prompts(
    script: str,
    scene_count: int = 10,
    style_suffix: str = "",
) -> list[dict]:
    """
    Generate AI image scene prompts from the script.
    
    Returns list of dicts with keys:
        - text: str - the line of narration this scene belongs to
        - prompt: str - complete image generation prompt
    """
    if not style_suffix:
        style_suffix = (
            " Cinematic still, natural light, shallow depth of field, filmic grain, "
            "muted colour, no text, no watermark, no logo."
        )
    
    prompt = f"""You are the art director for a faceless YouTube video.

Below is the full spoken SCRIPT. Design {scene_count} distinct background SCENES that carry this video visually.

RULES:
- Each scene illustrates a MOMENT the narrator actually reaches. Read the script and follow its order; scene 1 belongs at the start, the last at the end.
- Atmospheric imagery, never text on screen. No words, letters or numbers.
- No faces looking at camera unless the channel is built on a person.
- One subject per scene, described concretely: what is in frame, the light, the distance. "A hand resting on a closed book, low window light" — not "wisdom".
- Vary the distance across the set: wide, middle, close. A pool of fifteen medium shots edits like a slideshow.

Return ONLY a JSON array of {scene_count} objects:

{{"text": "<the line of narration this scene belongs to>",
 "prompt": "<a complete image-generation prompt>"}}

SCRIPT:
{script[:3500]}
"""
    
    try:
        response = atlas_llm_text(prompt, model="gpt-4o-mini", max_tokens=2000)
        text = response.strip()
        
        # Remove markdown fences
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(line for line in lines if not line.strip().startswith("```"))
        
        scenes = []
        items = []
        try:
            items = eval(text.strip())  # Try literal eval first
        except:
            import json
            items = json.loads(text.strip())
        
        for item in items:
            if isinstance(item, dict) and "prompt" in item:
                # Append style suffix
                full_prompt = item["prompt"].strip()
                if not full_prompt.endswith(style_suffix.strip()):
                    full_prompt += style_suffix
                
                scenes.append({
                    "text": item.get("text", "")[:200],
                    "prompt": full_prompt,
                })
        
        if len(scenes) >= scene_count // 2:
            return scenes[:scene_count]
    
    except Exception as e:
        print(f"[frontier_atlas] Scene prompt generation failed: {e}")
    
    # Fallback scenes
    fallback = [
        {"text": "Opening", "prompt": f"A misty landscape at dawn{style_suffix}"},
        {"text": "Journey begins", "prompt": f"Winding forest path through ancient trees{style_suffix}"},
        {"text": "Discovery", "prompt": f"Old book open on wooden desk under warm lamplight{style_suffix}"},
        {"text": "Reflection", "prompt": f"Still water reflecting sky at golden hour{style_suffix}"},
        {"text": "Challenge", "prompt": f"Dark storm clouds gathering over mountains{style_suffix}"},
        {"text": "Insight", "prompt": f"Single candle flame in darkness{style_suffix}"},
        {"text": "Transformation", "prompt": f"Butterfly emerging from chrysalis{style_suffix}"},
        {"text": "Resolution", "prompt": f"Peaceful sunset over calm ocean{style_suffix}"},
        {"text": "New beginning", "prompt": f"Morning light through window{style_suffix}"},
        {"text": "Closing", "prompt": f"Night sky filled with stars{style_suffix}"},
    ]
    return fallback[:scene_count]


def generate_frontier_stills(
    scene_prompts: list[dict],
    output_dir: str | Path,
    max_workers: int = 8,
    progress_callback=None,
) -> list[dict]:
    """
    Generate AI stills using Atlas gpt-image-2.
    
    Args:
        scene_prompts: List of dicts with 'prompt' and 'text' keys
        output_dir: Directory to save images
        max_workers: Parallel generation workers
        progress_callback: Optional callback(completed, total)
    
    Returns:
        List of dicts with keys:
            - success: bool
            - path: str - image path if successful
            - error: str - error message if failed
            - prompt: str - the prompt used
    """
    if not ATLASCLOUD_KEY:
        raise RuntimeError("ATLASCLOUD_KEY not set - required for Frontier")
    
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    results = []
    completed = 0
    total = len(scene_prompts)
    
    def _generate_one(index: int, scene: dict) -> dict:
        nonlocal completed
        
        output_path = output_dir / f"frontier_still_{index:03d}.png"
        
        # Skip if already exists
        if output_path.exists() and output_path.stat().st_size > 10000:
            completed += 1
            if progress_callback:
                progress_callback(completed, total)
            return {
                "success": True,
                "path": str(output_path),
                "error": "",
                "prompt": scene["prompt"],
                "index": index,
            }
        
        try:
            # Generate with Atlas gpt-image-2
            _generate_atlas_image(
                prompt=scene["prompt"],
                output_path=output_path,
                model="gpt-image-2",
            )
            
            completed += 1
            if progress_callback:
                progress_callback(completed, total)
            
            return {
                "success": True,
                "path": str(output_path),
                "error": "",
                "prompt": scene["prompt"],
                "index": index,
            }
        
        except Exception as e:
            error_msg = str(e)[:200]
            print(f"[frontier_atlas] Still {index} failed: {error_msg}")
            
            completed += 1
            if progress_callback:
                progress_callback(completed, total)
            
            return {
                "success": False,
                "path": "",
                "error": error_msg,
                "prompt": scene["prompt"],
                "index": index,
            }
    
    # Generate in parallel
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_generate_one, i, scene): i
            for i, scene in enumerate(scene_prompts)
        }
        
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
    
    # Sort by index to maintain order
    results.sort(key=lambda r: r["index"])
    
    return results


# ============================================================================
# PROVEN WHOP FRONTIER ATLAS PATH - DO NOT MODIFY
# Mac-tested: 8/8 stills succeeded with this exact code
# ============================================================================

ATLAS_BASE = "https://api.atlascloud.ai"
ATLAS_IMAGE_MODEL = os.environ.get("ATLAS_IMAGE_MODEL", "openai/gpt-image-2/text-to-image")
ATLAS_IMAGE_SIZE = os.environ.get("ATLAS_IMAGE_SIZE", "1536x864")
ATLAS_IMAGE_QUALITY = os.environ.get("ATLAS_IMAGE_QUALITY", "medium")


def _atlas_key() -> str:
    key = (ATLASCLOUD_KEY or os.environ.get("ATLASCLOUD_KEY") or os.environ.get("ATLASCLOUD_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("ATLASCLOUD_KEY not set")
    return key


def _atlas_headers() -> dict:
    return {"Authorization": f"Bearer {_atlas_key()}", "Content-Type": "application/json"}


def _atlas_create(prompt: str) -> str:
    """Create Atlas prediction. Returns prediction ID."""
    import requests
    
    body = {
        "model": ATLAS_IMAGE_MODEL,  # MUST be openai/gpt-image-2/text-to-image — NOT bare gpt-image-2
        "prompt": prompt[:5000],
        "size": ATLAS_IMAGE_SIZE,    # NOT width/height
        "quality": ATLAS_IMAGE_QUALITY,
        "output_format": "jpeg",
        "enable_sync_mode": False,
        "enable_base64_output": False,
    }
    r = requests.post(f"{ATLAS_BASE}/api/v1/model/generateImage",
                      headers=_atlas_headers(), json=body, timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"atlas create HTTP {r.status_code}: {r.text[:200]}")
    d = r.json()
    code = d.get("code")
    if code is not None and code != 200:
        raise RuntimeError(f"atlas create error: {str(d)[:200]}")
    data = d.get("data") if isinstance(d.get("data"), dict) else {}
    pid = data.get("id")  # nested under data — NOT top-level id
    if not pid:
        raise RuntimeError(f"atlas create missing prediction id: {str(d)[:200]}")
    return pid


def _atlas_poll(prediction_id: str, timeout_s: int = 600) -> str:
    """Poll prediction until complete. Returns first output IMAGE URL (not base64)."""
    import requests
    
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(5)
        try:
            # singular prediction — NOT /predictions/
            r = requests.get(f"{ATLAS_BASE}/api/v1/model/prediction/{prediction_id}",
                             headers=_atlas_headers(), timeout=45)
        except requests.exceptions.RequestException:
            continue
        if r.status_code != 200:
            continue
        try:
            payload = r.json()
        except ValueError:
            continue
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        st = str(data.get("status") or payload.get("status") or "").lower()
        if st in ("completed", "succeeded", "success"):
            outputs = data.get("outputs") or payload.get("outputs") or []
            if not outputs:
                raise RuntimeError(f"atlas: done but no outputs: {str(payload)[:200]}")
            url = outputs[0]
            if not url or not isinstance(url, str):
                raise RuntimeError(f"atlas: bad output url: {str(outputs[0])[:160]}")
            return url
        if st in ("failed", "error", "cancelled", "canceled", "failure"):
            why = data.get("error") or data.get("failMsg") or data.get("message") or payload
            raise RuntimeError(f"atlas job {st}: {str(why)[:160]}")
    raise TimeoutError(f"atlas: prediction {prediction_id} did not finish within {timeout_s}s")


def _download(url: str, dest: Path) -> None:
    """Download image from URL to dest path."""
    import requests
    
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    if dest.stat().st_size < 1000:
        raise RuntimeError(f"atlas download too small: {dest.stat().st_size} bytes")


def _generate_atlas_image(
    prompt: str,
    output_path: str | Path,
    model: str = "openai/gpt-image-2/text-to-image",  # ignored; ATLAS_IMAGE_MODEL wins
    width: int = 1920,
    height: int = 1080,
    max_retries: int = 4,
):
    """Whop create → poll URL → download. Mac-proven 8/8 stills."""
    last = None
    dest = Path(output_path)
    for attempt in range(1, max_retries + 1):
        try:
            _download(_atlas_poll(_atlas_create(prompt)), dest)
            return
        except Exception as e:
            last = e
            if attempt < max_retries:
                time.sleep(min(60, 12 * attempt))
    raise RuntimeError(f"Atlas image generation failed after {max_retries} attempts: {last}")
