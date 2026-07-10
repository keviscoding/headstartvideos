# Headstart Videos

AI-powered YouTube video production platform. Go from niche to finished video in 6 steps.

## Features

- **6-Step Pipeline** — Niche > Title > Script > Voice > Thumbnail > Build
- **4 Video Recipes** — Animated Explainer, B-Roll Documentary, Cinematic B-Roll, Avatar + Illustrations
- **Script Studio** — Channel analysis, idea generation, and script writing with Claude AI
- **Voiceover Studio** — 26 Gemini TTS voices with style presets
- **Thumbnail Studio** — AI-generated thumbnails with reference image matching
- **Niche Screener** — Analyze YouTube videos to extract visual patterns
- **Upload Kit** — Title, description, tags, and thumbnail ready for YouTube

## Quick Start

### Local Development

```bash
cp .env.example .env
# Add your API keys to .env

pip install -r requirements.txt
python -m webapp.server
```

Open **http://localhost:8000**

### Docker

```bash
cp .env.example .env
# Add your API keys to .env

docker compose up --build
```

Open **http://localhost:8000**

## API Keys

| Key | Purpose | Required? |
|-----|---------|-----------|
| **Gemini** | Script/title generation, voiceover, illustrations | Yes |
| **Atlas Cloud** | Thumbnail generation | For thumbnails |
| **Claude** | Script Studio (channel analysis, ideas, scripts) | For Script Studio |
| **YouTube API** | Channel data fetching | For Channel Analyzer |
| **HeyGen** | AI avatar videos (BYOK) | Avatar recipe — user pastes their own API key in Settings → Integrations |

| **Pexels** | Stock photos and video | For B-Roll recipes |

Get API keys:
- Gemini: [aistudio.google.com](https://aistudio.google.com/)
- Atlas Cloud: [atlascloud.ai](https://www.atlascloud.ai/)
- Claude: [console.anthropic.com](https://console.anthropic.com/)
- Pexels: [pexels.com/api](https://www.pexels.com/api/)

## Cook queue (production)

Renders are **FIFO-queued** with a hard concurrency cap so one busy cook cannot freeze the whole site.

| Env var | Default | Meaning |
|---------|---------|---------|
| `MAX_CONCURRENT_COOKS` | `1` | Max simultaneous cooks on this process (web or each worker) |
| `COOK_ON_WEB` | `1` | `1` = cooks run on the API process; `0` = enqueue only (workers claim) |
| `GROQ_API_KEY` | — | **Required in production** for Whisper (local whisper disabled) |
| `ILLUSTRATION_WORKERS_LITE` | `6` | Parallel image gens for trial/lite cooks |
| `EST_MINUTES_PER_COOK` | `7` | Heuristic for “~N min wait” in the UI |
| `WORKER_POLL_SECONDS` | `2` | How often workers poll for queued jobs |
| `WORKER_STALE_SECONDS` | `900` | Re-queue jobs whose worker heartbeat went silent |

### Single dyno (default)

Leave `COOK_ON_WEB=1` and `MAX_CONCURRENT_COOKS=1` on DigitalOcean. The API process runs the in-process FIFO queue.

### Optimum: web + workers

1. Set `COOK_ON_WEB=0` on the web/API service (same `DATABASE_URL`, Spaces, API keys).
2. Run one or more workers: `python -m webapp.worker` (or `docker compose up --scale worker=2`).
3. Workers claim jobs with Postgres `FOR UPDATE SKIP LOCKED` (SQLite uses an immediate transaction).
4. Scale workers with queue depth; keep `MAX_CONCURRENT_COOKS=1` per worker box unless the machine is large.
5. Workers must see the same `output/` uploads (shared volume) **or** run on the same host as the API. Finished videos still go to Spaces when configured.

Local split:

```bash
# terminal 1 — API only
COOK_ON_WEB=0 uvicorn webapp.server:app --reload --port 8000

# terminal 2 — cook worker
python -m webapp.worker
```

Or: `docker compose up --build` (web with `COOK_ON_WEB=0` + one worker).

## Architecture

```
webapp/
  server.py          FastAPI backend — API routes + static files
  job_queue.py       In-process FIFO cook queue (COOK_ON_WEB=1)
  cook_runner.py     Shared pipeline execution (web or worker)
  worker.py          Durable queue consumer (COOK_ON_WEB=0)
  static/
    index.html       Single-page app (Tailwind CSS)
    app.js           Client-side state machine + routing
    styles.css       Custom styles
  niches/
    *.json           Recipe/niche card definitions

core/
  explainer_pipeline.py    Animated explainer recipe
  pipeline.py              Standard + cinematic B-roll recipes
  avatar_pipeline.py       Avatar + illustrations recipe
  voiceover_gen.py         Gemini TTS generation
  thumbnail_gen.py         AI thumbnail generation
  script_gen.py            Claude-powered script studio
  ...                      25 supporting modules
```

## Tech Stack

- **Backend**: Python, FastAPI, SSE for real-time progress
- **Frontend**: HTML, Tailwind CSS (CDN), vanilla JavaScript
- **AI**: Google Gemini, Anthropic Claude, Atlas Cloud, HeyGen
- **Media**: FFmpeg, faster-whisper, Pillow
