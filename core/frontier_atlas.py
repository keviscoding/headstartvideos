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


def _generate_atlas_image(
    prompt: str,
    output_path: str | Path,
    model: str = "gpt-image-2",
    width: int = 1920,
    height: int = 1080,
    max_retries: int = 3,
):
    """
    Generate a single image using Atlas Cloud.
    
    Atlas API returns either:
    - data[0]["url"] → download PNG from URL
    - data[0]["b64_json"] → base64-encoded PNG data
    
    This implementation handles both response formats (1:1 with Whop Frontier).
    """
    import base64
    import requests
    
    if not ATLASCLOUD_KEY:
        raise RuntimeError("ATLASCLOUD_KEY not set")
    
    url = "https://api.atlascloud.ai/v1/images/generations"
    headers = {
        "Authorization": f"Bearer {ATLASCLOUD_KEY}",
        "Content-Type": "application/json",
    }
    
    payload = {
        "model": model,
        "prompt": prompt,
        "width": width,
        "height": height,
        "response_format": "url",  # Request URL, but accept b64_json fallback
    }
    
    last_error = None
    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=120)
            response.raise_for_status()
            data = response.json()
            
            if "data" not in data or len(data["data"]) == 0:
                raise RuntimeError(f"No data in Atlas response: {data}")
            
            item = data["data"][0]
            output_path = Path(output_path)
            
            # Handle URL response (preferred)
            if "url" in item and item["url"]:
                image_url = item["url"]
                img_response = requests.get(image_url, timeout=60)
                img_response.raise_for_status()
                output_path.write_bytes(img_response.content)
                return
            
            # Handle base64 response (fallback, matches Whop Frontier behavior)
            if "b64_json" in item and item["b64_json"]:
                b64_data = item["b64_json"]
                png_bytes = base64.b64decode(b64_data)
                output_path.write_bytes(png_bytes)
                return
            
            raise RuntimeError(f"No url or b64_json in Atlas response: {item.keys()}")
        
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
    
    raise RuntimeError(f"Atlas image generation failed after {max_retries} attempts: {last_error}")
