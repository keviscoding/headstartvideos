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

**Critical:** long Atlas voiceovers / Gemini / thumbnails run as **sync FastAPI routes** (threadpool), never on the asyncio event loop — otherwise the homepage freezes for 60–120s.

| Env var | Default | Meaning |
|---------|---------|---------|
| `MAX_CONCURRENT_COOKS` | `1` | Max simultaneous cooks on this process (web or each worker) |
| `MAX_CONCURRENT_VOICEOVERS` | `2` | Cap parallel Atlas TTS jobs on this process |
| `WEB_THREADPOOL_SIZE` | `32` | Threadpool for sync routes (VO / thumb / Gemini) |
| `COOK_ON_WEB` | `1` | `1` = cooks run on the API process; `0` = enqueue only (workers claim) |
| `GROQ_API_KEY` | — | **Required in production** for Whisper (local whisper disabled) |
| `ILLUSTRATION_WORKERS_LITE` | `6` | Parallel image gens for trial/lite cooks |
| `EST_MINUTES_PER_COOK` | `7` | Heuristic for “~N min wait” in the UI |
| `WORKER_POLL_SECONDS` | `2` | How often workers poll for queued jobs |
| `WORKER_STALE_SECONDS` | `900` | Re-queue jobs whose worker heartbeat went silent |

### Single dyno (default)

Leave `COOK_ON_WEB=1` and `MAX_CONCURRENT_COOKS=1` on DigitalOcean. The API process runs the in-process FIFO queue. Voiceovers no longer block the event loop, but heavy cooks still compete for CPU — prefer workers in production.

### Optimum: web + Modal (scale-to-zero — preferred)

Don’t buy more DO workers as traffic grows. Cooks run on Modal: pay per second, burst in parallel, **$0 when idle**.

1. `pip install modal && modal setup`
2. `modal secret create channelrecipe-env` with DATABASE_URL, SPACES_*, GEMINI_KEY, ATLASCLOUD_KEY, GROQ_API_KEY, etc. (see `modal_cook.py`)
3. `modal deploy modal_cook.py`
4. On DO **Web**: `COOK_ON_WEB=0`, `COOK_ON_MODAL=1`, `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`
5. Scale DO **cook-worker** to **0** instances

### Fallback: web + always-on DO workers

1. Confirm Spaces env vars (`SPACES_KEY/SECRET/BUCKET/ENDPOINT`) — voiceovers/thumbnails are staged there so workers can fetch them.
2. Set `COOK_ON_WEB=0` on the **Web** component.
3. Add a **Worker** component (same Docker image / env), run command:
   ```bash
   python -m webapp.worker
   ```
4. Workers claim jobs with Postgres `FOR UPDATE SKIP LOCKED`.
5. Scale workers with queue depth; keep `MAX_CONCURRENT_COOKS=1` per worker instance.
6. Health check path: `GET /api/health`

Local split:

```bash
# terminal 1 — API only
COOK_ON_WEB=0 uvicorn webapp.server:app --reload --port 8000

# terminal 2 — cook worker
python -m webapp.worker
```

Or: `docker compose up --build` (web with `COOK_ON_WEB=0` + one worker).

## Niche Finder library (scroll discovery + cron)

Niche Finder is a **shared database** users browse — not an on-demand hunt for everyone.
Discovery **scrolls real YouTube search pages** (Playwright), like ViewHunt — not API search ranking.
Videos older than **6 months** are ignored. Results upsert into `niche_channels`.

Isolation: niche scrape prefers an ephemeral **Fly Machine** on the cook app image (`python -m webapp.fly_niche_oneshot`) — same image as cooks, different command, **not** the cook queue. Progress lives in `niche_hunt_runs` so refreshing the page can resume polling. If Fly is off, it falls back to a web-dyno background thread.

1. Set `CRON_SECRET`, `YOUTUBE_API_KEY`, and (for Fly) `COOK_ON_FLY=1` + cook Fly secrets on the web app.
2. Redeploy the cook image after niche code changes so Machines pick up `fly_niche_oneshot`.
3. Call the endpoint **1–2×/day**:

```bash
curl -X POST https://channelrecipe.com/api/internal/niche-finder/cron \
  -H "Authorization: Bearer $CRON_SECRET" \
  -H "Content-Type: application/json" \
  -d '{}'
```

Optional JSON: `keywords`, `scroll_count` (default 20), `max_video_age_days` (default 180), `max_channels`.
Admin can also hit **Add niches** in the Niche Finder UI.

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
