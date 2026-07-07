"""
Video Factory — Complete Web App

Pipeline + Tools + Settings + History.
Run:  python -m webapp.server
"""

from __future__ import annotations
import asyncio
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

import config

WEBAPP_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEBAPP_DIR / "static"
NICHES_DIR = WEBAPP_DIR / "niches"
OUTPUT_DIR = ROOT / "output"
UPLOAD_DIR = OUTPUT_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Video Factory", docs_url="/docs")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_jobs: dict[str, dict[str, Any]] = {}

CURATED_VOICES = [
    {"id": "Charon", "name": "Charon", "tag": "Informative", "desc": "Clear, authoritative narrator — best for documentaries", "default": True},
    {"id": "Kore", "name": "Kore", "tag": "Firm", "desc": "Strong, confident delivery with gravitas"},
    {"id": "Gacrux", "name": "Gacrux", "tag": "Mature", "desc": "Deep, seasoned voice with natural warmth"},
    {"id": "Schedar", "name": "Schedar", "tag": "Even", "desc": "Calm, steady pacing — great for explainers"},
    {"id": "Puck", "name": "Puck", "tag": "Upbeat", "desc": "Energetic, engaging — ideal for listicles"},
    {"id": "Sulafat", "name": "Sulafat", "tag": "Warm", "desc": "Gentle, approachable storytelling tone"},
]


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class TitleRequest(BaseModel):
    niche: str
    topic: str = ""

class ScriptRequest(BaseModel):
    title: str
    niche: str
    target_minutes: int = 8

class VoiceoverRequest(BaseModel):
    script: str
    voice: str = "Charon"

class VoicePreviewRequest(BaseModel):
    voice: str
    text: str = "Welcome to this episode. Today we uncover one of history's greatest untold stories."

class ThumbnailRequest(BaseModel):
    title: str
    niche_style: str = ""
    count: int = 2

class BuildRequest(BaseModel):
    script: str
    voiceover_path: str
    title: str = ""
    niche: str = "animated_explainer"
    recipe: str = "animated_explainer"
    thumbnail_path: str = ""

class UploadKitRequest(BaseModel):
    title: str
    script: str
    niche: str = ""

class ChannelFetchRequest(BaseModel):
    channel_url: str
    max_videos: int = 20

class ChannelAnalyzeRequest(BaseModel):
    channel_data: dict | None = None

class IdeasRequest(BaseModel):
    channel_data: dict | None = None
    num_ideas: int = 7
    analysis: str = ""

class ClaudeTitlesRequest(BaseModel):
    video_idea: str
    channel_data: dict | None = None

class ClaudeScriptRequest(BaseModel):
    title: str
    video_idea: str = ""
    channel_data: dict | None = None
    target_minutes: int = 8

class VoiceoverStudioRequest(BaseModel):
    script: str
    voice: str = "Charon"
    style_preset: str = "Narrator"
    custom_notes: str = ""

class NicheAnalyzeRequest(BaseModel):
    youtube_url: str
    minutes: int = 5

class KeyTestRequest(BaseModel):
    key_name: str
    key_value: str = ""


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


# ---------------------------------------------------------------------------
# Niches
# ---------------------------------------------------------------------------
@app.get("/api/niches")
async def get_niches():
    niches = []
    for f in sorted(NICHES_DIR.glob("*.json")):
        with open(f) as fh:
            niches.append(json.load(fh))
    return niches


# ---------------------------------------------------------------------------
# Voices
# ---------------------------------------------------------------------------
@app.get("/api/voices")
async def get_voices():
    return CURATED_VOICES


@app.get("/api/voices/all")
async def get_all_voices():
    from core.voiceover_gen import VOICES
    return [{"id": name, "name": name, "tag": desc} for name, desc in VOICES.items()]


# ---------------------------------------------------------------------------
# Titles (Gemini-based)
# ---------------------------------------------------------------------------
@app.post("/api/titles")
async def generate_titles(req: TitleRequest):
    from google import genai

    if not config.GEMINI_KEY:
        raise HTTPException(500, "GEMINI_KEY not configured on backend")

    client = genai.Client(api_key=config.GEMINI_KEY)
    niche_data = _load_niche(req.niche)
    niche_name = niche_data.get("name", req.niche) if niche_data else req.niche
    topic_hint = f"\nTopic hint from user: {req.topic}" if req.topic else ""

    prompt = (
        f"Generate exactly 3 viral YouTube video titles for the '{niche_name}' niche. "
        f"These should be compelling, curiosity-driven titles that get clicks. "
        f"Each title should be a different angle on a fascinating topic. "
        f"Return ONLY a JSON array of 3 strings, nothing else.{topic_hint}"
    )

    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
        )
        raw = resp.text.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        titles = json.loads(raw)
        if not isinstance(titles, list) or len(titles) < 1:
            raise ValueError("Expected list of titles")
        return {"titles": titles[:3]}
    except Exception as e:
        raise HTTPException(500, f"Title generation failed: {e}")


# ---------------------------------------------------------------------------
# Script (Gemini-based)
# ---------------------------------------------------------------------------
@app.post("/api/script")
async def generate_script(req: ScriptRequest):
    from google import genai

    if not config.GEMINI_KEY:
        raise HTTPException(500, "GEMINI_KEY not configured on backend")

    client = genai.Client(api_key=config.GEMINI_KEY)
    niche_data = _load_niche(req.niche)
    style_hint = ""
    if niche_data:
        style_hint = f"\nVideo style: {niche_data.get('description', '')}"

    word_target = req.target_minutes * 150

    prompt = (
        f"Write a YouTube video script for this title: \"{req.title}\"\n\n"
        f"Target length: approximately {word_target} words ({req.target_minutes} minutes when narrated).{style_hint}\n\n"
        f"Rules:\n"
        f"- Write ONLY the narration script — no stage directions, no [brackets], no scene descriptions\n"
        f"- Open with a strong hook in the first 2 sentences\n"
        f"- Use short, punchy sentences for pacing\n"
        f"- Include specific facts, names, dates, numbers — not vague statements\n"
        f"- End with a thought-provoking conclusion\n"
        f"- Do NOT include any intro/outro channel plugs\n\n"
        f"Return ONLY the script text, nothing else."
    )

    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
        )
        script = resp.text.strip()
        return {"script": script, "word_count": len(script.split())}
    except Exception as e:
        raise HTTPException(500, f"Script generation failed: {e}")


# ---------------------------------------------------------------------------
# Voiceover
# ---------------------------------------------------------------------------
@app.post("/api/voiceover")
async def generate_voiceover(req: VoiceoverRequest):
    from core.voiceover_gen import generate_voiceover as gen_vo

    out_dir = str(OUTPUT_DIR / "voiceovers" / str(int(time.time())))
    try:
        wav_path = gen_vo(script=req.script, voice=req.voice, style_preset="Narrator", output_dir=out_dir)
        rel = os.path.relpath(wav_path, str(ROOT))
        return {"path": wav_path, "url": f"/api/files/{rel}"}
    except Exception as e:
        raise HTTPException(500, f"Voiceover generation failed: {e}")


@app.post("/api/voiceover/preview")
async def voice_preview(req: VoicePreviewRequest):
    from core.voiceover_gen import generate_voiceover as gen_vo

    out_dir = str(OUTPUT_DIR / "voice_previews")
    cache_path = Path(out_dir) / f"{req.voice.lower()}_preview.wav"

    if cache_path.exists():
        rel = os.path.relpath(str(cache_path), str(ROOT))
        return {"url": f"/api/files/{rel}"}

    try:
        wav_path = gen_vo(script=req.text, voice=req.voice, style_preset="Narrator", output_dir=out_dir)
        if Path(wav_path).exists() and not cache_path.exists():
            Path(wav_path).rename(cache_path)
            wav_path = str(cache_path)
        rel = os.path.relpath(wav_path, str(ROOT))
        return {"url": f"/api/files/{rel}"}
    except Exception as e:
        raise HTTPException(500, f"Voice preview failed: {e}")


@app.post("/api/voiceover/studio")
async def voiceover_studio(req: VoiceoverStudioRequest):
    from core.voiceover_gen import generate_voiceover as gen_vo

    out_dir = str(OUTPUT_DIR / "voiceovers" / str(int(time.time())))
    try:
        wav_path = gen_vo(
            script=req.script,
            voice=req.voice,
            style_preset=req.style_preset,
            custom_notes=req.custom_notes,
            output_dir=out_dir,
        )
        rel = os.path.relpath(wav_path, str(ROOT))
        return {"path": wav_path, "url": f"/api/files/{rel}"}
    except Exception as e:
        raise HTTPException(500, f"Voiceover generation failed: {e}")


# ---------------------------------------------------------------------------
# Thumbnails
# ---------------------------------------------------------------------------
@app.post("/api/thumbnail")
async def generate_thumbnail(req: ThumbnailRequest):
    from core.thumbnail_gen import generate_thumbnail_no_refs

    out_dir = str(OUTPUT_DIR / "thumbnails" / str(int(time.time())))
    try:
        paths = generate_thumbnail_no_refs(
            title=req.title,
            style_description=req.niche_style or "Bold, eye-catching YouTube thumbnail with dramatic lighting",
            output_dir=out_dir,
        )
        if not paths:
            raise ValueError("No thumbnails generated")
        urls = [f"/api/files/{os.path.relpath(p, str(ROOT))}" for p in paths[:req.count]]
        return {"thumbnails": urls, "paths": paths[:req.count]}
    except Exception as e:
        raise HTTPException(500, f"Thumbnail generation failed: {e}")


@app.post("/api/thumbnail/with-refs")
async def generate_thumbnail_with_refs(
    title: str = Form(...),
    style: str = Form(""),
    count: int = Form(2),
    refs: list[UploadFile] = File(default=[]),
):
    from core.thumbnail_gen import generate_thumbnails

    ref_paths = []
    for ref in refs:
        dest = UPLOAD_DIR / f"ref_{int(time.time())}_{ref.filename}"
        with open(dest, "wb") as f:
            content = await ref.read()
            f.write(content)
        ref_paths.append(str(dest))

    out_dir = str(OUTPUT_DIR / "thumbnails" / str(int(time.time())))
    try:
        paths = generate_thumbnails(
            title=title,
            reference_image_paths=ref_paths,
            style_prompt=style,
            num_images=count,
            output_dir=out_dir,
        )
        if not paths:
            raise ValueError("No thumbnails generated")
        urls = [f"/api/files/{os.path.relpath(p, str(ROOT))}" for p in paths]
        return {"thumbnails": urls, "paths": paths}
    except Exception as e:
        raise HTTPException(500, f"Thumbnail generation failed: {e}")


# ---------------------------------------------------------------------------
# Build (recipe-aware + SSE progress)
# ---------------------------------------------------------------------------
@app.post("/api/build")
async def start_build(req: BuildRequest):
    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {
        "status": "queued",
        "progress": [],
        "result": None,
        "request": req.model_dump(),
    }

    import threading
    t = threading.Thread(target=_run_build, args=(job_id, req), daemon=True)
    t.start()
    return {"job_id": job_id}


def _run_build(job_id: str, req: BuildRequest):
    job = _jobs[job_id]
    job["status"] = "running"

    def on_progress(msg: str):
        job["progress"].append({"time": time.time(), "message": msg})

    recipe = req.recipe or "animated_explainer"

    try:
        if recipe == "animated_explainer":
            from core.explainer_pipeline import run_explainer_pipeline
            result = run_explainer_pipeline(
                script=req.script,
                voiceover_path=req.voiceover_path,
                output_name="pipeline_video.mp4",
                style_preset="default",
                progress_callback=on_progress,
            )
        elif recipe == "broll_only":
            from core.pipeline import run_pipeline
            result = run_pipeline(
                script=req.script,
                voiceover_path=req.voiceover_path,
                output_name="pipeline_video.mp4",
                progress_callback=on_progress,
            )
        elif recipe == "broll_cinematic":
            from core.pipeline import run_cinematic_pipeline
            result = run_cinematic_pipeline(
                script=req.script,
                voiceover_path=req.voiceover_path,
                output_name="pipeline_video.mp4",
                progress_callback=on_progress,
            )
        elif recipe == "avatar_plus_broll":
            from core.avatar_pipeline import run_avatar_pipeline
            result = run_avatar_pipeline(
                script=req.script,
                voiceover_path=req.voiceover_path if req.voiceover_path else None,
                output_name="pipeline_video.mp4",
                progress_callback=on_progress,
            )
        else:
            raise ValueError(f"Unknown recipe: {recipe}")

        job["status"] = "complete"
        job["result"] = {
            "output_path": result["output_path"],
            "output_url": f"/api/files/{os.path.relpath(result['output_path'], str(ROOT))}",
            "job_dir": result.get("job_dir", ""),
            "concepts": len(result.get("slots", [])),
            "timing": result.get("timing", {}),
        }
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


@app.get("/api/build/{job_id}/progress")
async def build_progress(job_id: str, request: Request):
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")

    async def stream():
        seen = 0
        while True:
            if await request.is_disconnected():
                break
            job = _jobs[job_id]
            for msg in job["progress"][seen:]:
                yield {"event": "progress", "data": json.dumps(msg)}
                seen += 1
            if job["status"] == "complete":
                yield {"event": "complete", "data": json.dumps(job["result"])}
                break
            elif job["status"] == "error":
                yield {"event": "error", "data": json.dumps({"error": job.get("error", "Unknown")})}
                break
            await asyncio.sleep(1)

    return EventSourceResponse(stream())


@app.get("/api/build/{job_id}/result")
async def build_result(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")
    job = _jobs[job_id]
    if job["status"] != "complete":
        return {"status": job["status"], "progress": len(job["progress"])}
    return job["result"]


# ---------------------------------------------------------------------------
# Upload Kit
# ---------------------------------------------------------------------------
@app.post("/api/upload-kit")
async def generate_upload_kit(req: UploadKitRequest):
    from google import genai

    if not config.GEMINI_KEY:
        return {"description": f"Check out this video: {req.title}", "tags": ["youtube", "video"]}

    client = genai.Client(api_key=config.GEMINI_KEY)
    prompt = (
        f"Generate YouTube upload metadata for this video:\n"
        f"Title: \"{req.title}\"\nScript excerpt: \"{req.script[:500]}\"\n\n"
        f"Return a JSON object with:\n"
        f"- \"description\": a 150-200 word YouTube description with relevant keywords, 3 paragraph breaks, and a call to action\n"
        f"- \"tags\": array of 15-20 relevant YouTube tags for SEO\n"
        f"- \"hashtags\": array of 3 hashtags\n\nReturn ONLY valid JSON."
    )
    try:
        resp = client.models.generate_content(model="gemini-2.5-flash", contents=[{"role": "user", "parts": [{"text": prompt}]}])
        raw = resp.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(raw)
    except Exception:
        return {"description": f"Check out this video: {req.title}", "tags": ["youtube", "video"], "hashtags": []}


# ---------------------------------------------------------------------------
# Channel Data + Analysis (Script Studio)
# ---------------------------------------------------------------------------
@app.post("/api/channel/fetch")
async def fetch_channel(req: ChannelFetchRequest):
    from core.channel_data import fetch_channel_data

    if not config.YOUTUBE_API_KEY:
        raise HTTPException(400, "YouTube API key not configured. Add it in Settings.")

    try:
        data = fetch_channel_data(
            channel_url=req.channel_url,
            yt_api_key=config.YOUTUBE_API_KEY,
            downsub_key=config.DOWNSUB_KEY,
            max_videos=req.max_videos,
        )
        return data
    except Exception as e:
        raise HTTPException(500, f"Channel fetch failed: {e}")


@app.post("/api/channel/analyze")
async def analyze_channel(req: ChannelAnalyzeRequest):
    if not config.ANTHROPIC_KEY:
        return {"analysis": "Claude API key not configured. Add it in Settings to enable channel analysis."}

    try:
        from core.script_gen import analyze_channel as _analyze
        result = _analyze(channel_data=req.channel_data, api_key=config.ANTHROPIC_KEY)
        return {"analysis": result}
    except Exception as e:
        raise HTTPException(500, f"Channel analysis failed: {e}")


@app.post("/api/ideas")
async def generate_ideas(req: IdeasRequest):
    if not config.ANTHROPIC_KEY:
        raise HTTPException(400, "Claude API key not configured. Add it in Settings.")

    try:
        from core.script_gen import generate_ideas as _gen
        result = _gen(
            channel_data=req.channel_data,
            api_key=config.ANTHROPIC_KEY,
            num_ideas=req.num_ideas,
            analysis=req.analysis,
        )
        ideas = [line.strip() for line in result.split("\n") if line.strip()]
        return {"ideas": ideas, "raw": result}
    except Exception as e:
        raise HTTPException(500, f"Idea generation failed: {e}")


@app.post("/api/titles/claude")
async def generate_titles_claude(req: ClaudeTitlesRequest):
    if not config.ANTHROPIC_KEY:
        raise HTTPException(400, "Claude API key not configured. Add it in Settings.")

    try:
        from core.script_gen import generate_titles as _gen
        result = _gen(
            video_idea=req.video_idea,
            channel_data=req.channel_data,
            api_key=config.ANTHROPIC_KEY,
        )
        titles = [line.strip().lstrip("0123456789.-) ") for line in result.split("\n") if line.strip()]
        return {"titles": titles[:5], "raw": result}
    except Exception as e:
        raise HTTPException(500, f"Title generation failed: {e}")


@app.post("/api/script/claude")
async def generate_script_claude(req: ClaudeScriptRequest):
    if not config.ANTHROPIC_KEY:
        raise HTTPException(400, "Claude API key not configured. Add it in Settings.")

    try:
        from core.script_gen import generate_script as _gen
        result = _gen(
            title=req.title,
            video_idea=req.video_idea,
            channel_data=req.channel_data,
            api_key=config.ANTHROPIC_KEY,
            target_length_min=req.target_minutes,
        )
        return {"script": result, "word_count": len(result.split())}
    except Exception as e:
        raise HTTPException(500, f"Script generation failed: {e}")


# ---------------------------------------------------------------------------
# Niche Screener
# ---------------------------------------------------------------------------
@app.post("/api/niche/analyze")
async def analyze_niche(req: NicheAnalyzeRequest):
    try:
        from core.video_analyzer import analyze_video
        profile = analyze_video(req.youtube_url, analyze_minutes=req.minutes)
        profile_dict = {
            "niche_name": profile.niche_name,
            "recipe": profile.recipe,
            "broll_type": profile.broll_type,
            "default_swap_rate": profile.default_swap_rate,
            "visual_style": profile.visual_style,
            "avatar_config": profile.avatar_config,
            "automatable_pct": profile.automatable_pct,
            "sample_queries": profile.sample_queries,
            "notes": profile.notes,
        }
        summary = (
            f"Niche: {profile.niche_name}\n"
            f"Recommended Recipe: {profile.recipe}\n"
            f"B-Roll Type: {profile.broll_type}\n"
            f"Swap Rate: {profile.default_swap_rate}\n"
            f"Automatable: {profile.automatable_pct}%\n"
            f"Notes: {profile.notes}"
        )
        return {"profile": profile_dict, "summary": summary}
    except Exception as e:
        raise HTTPException(500, f"Niche analysis failed: {e}")


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------
@app.get("/api/history")
async def get_history(type: str = "all"):
    entries = []
    output = ROOT / "output"
    if not output.exists():
        return {"entries": []}

    for d in sorted(output.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        name = d.name

        if name.startswith("explainer_") or name.startswith("cine_") or name.startswith("job_") or name.startswith("avatar_job_"):
            if type not in ("all", "video"):
                continue
            video_files = list(d.glob("*.mp4"))
            if video_files:
                entries.append({
                    "type": "video",
                    "title": name,
                    "description": f"Video: {video_files[0].name}",
                    "timestamp": d.stat().st_mtime * 1000,
                    "path": str(video_files[0]),
                    "url": f"/api/files/{os.path.relpath(str(video_files[0]), str(ROOT))}",
                })

        elif name.startswith("voiceover") or (d / "voiceover.wav").exists():
            if type not in ("all", "voiceover"):
                continue
            wav_files = list(d.glob("*.wav"))
            if wav_files:
                entries.append({
                    "type": "voiceover",
                    "title": name,
                    "description": f"Voiceover: {wav_files[0].name}",
                    "timestamp": d.stat().st_mtime * 1000,
                })

        elif name.startswith("thumbnail"):
            if type not in ("all", "thumbnail"):
                continue
            img_files = list(d.glob("*.png")) + list(d.glob("*.jpg"))
            for img in img_files:
                entries.append({
                    "type": "thumbnail",
                    "title": name,
                    "description": img.name,
                    "timestamp": img.stat().st_mtime * 1000,
                    "url": f"/api/files/{os.path.relpath(str(img), str(ROOT))}",
                })

    return {"entries": entries[:50]}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
KEY_MAP = {
    "gemini": "GEMINI_KEY",
    "claude": "ANTHROPIC_KEY",
    "youtube": "YOUTUBE_API_KEY",
    "atlascloud": "ATLASCLOUD_KEY",
    "heygen": "HEYGEN_KEY",
    "pexels": "PEXELS_KEY",
    "downsub": "DOWNSUB_KEY",
}


@app.get("/api/settings/keys")
async def get_settings():
    result = {}
    for short, env_name in KEY_MAP.items():
        val = os.environ.get(env_name, "") or getattr(config, env_name, "")
        result[short] = {"configured": bool(val), "env_name": env_name}
    return result


@app.post("/api/settings/keys")
async def save_settings(keys: dict):
    env_path = ROOT / ".env"
    existing = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                existing[k.strip()] = v.strip()

    for short, value in keys.items():
        env_name = KEY_MAP.get(short)
        if env_name and value:
            existing[env_name] = value
            os.environ[env_name] = value
            setattr(config, env_name, value)

    with open(env_path, "w") as f:
        for k, v in existing.items():
            f.write(f"{k}={v}\n")

    return {"message": "Keys saved successfully"}


@app.post("/api/settings/test-key")
async def test_key(req: KeyTestRequest):
    env_name = KEY_MAP.get(req.key_name)
    key_val = req.key_value or os.environ.get(env_name or "", "") or getattr(config, env_name or "", "")

    if not key_val:
        return {"ok": False, "error": "Key not provided"}

    try:
        if req.key_name == "gemini":
            from google import genai
            client = genai.Client(api_key=key_val)
            client.models.generate_content(model="gemini-2.5-flash", contents="Say hi")
            return {"ok": True}

        elif req.key_name == "claude":
            import anthropic
            client = anthropic.Anthropic(api_key=key_val)
            client.messages.create(model="claude-haiku-4-5", max_tokens=10, messages=[{"role": "user", "content": "Hi"}])
            return {"ok": True}

        elif req.key_name == "youtube":
            import httpx
            r = httpx.get(f"https://www.googleapis.com/youtube/v3/channels?part=id&id=UC_x5XG1OV2P6uZZ5FSM9Ttw&key={key_val}", timeout=10)
            return {"ok": r.status_code == 200}

        elif req.key_name == "pexels":
            import httpx
            r = httpx.get("https://api.pexels.com/v1/search?query=test&per_page=1", headers={"Authorization": key_val}, timeout=10)
            return {"ok": r.status_code == 200}

        elif req.key_name == "heygen":
            import httpx
            r = httpx.get("https://api.heygen.com/v2/avatars", headers={"x-api-key": key_val}, timeout=10)
            return {"ok": r.status_code == 200}

        elif req.key_name == "atlascloud":
            return {"ok": bool(key_val)}

        else:
            return {"ok": bool(key_val)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ---------------------------------------------------------------------------
# File serving
# ---------------------------------------------------------------------------
@app.get("/api/files/{file_path:path}")
async def serve_file(file_path: str):
    full = ROOT / file_path
    if not full.exists():
        raise HTTPException(404, f"File not found: {file_path}")
    return FileResponse(str(full))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_niche(niche_key: str) -> dict | None:
    path = NICHES_DIR / f"{niche_key}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    print(f"\n  Video Factory")
    print(f"  http://localhost:8000\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
