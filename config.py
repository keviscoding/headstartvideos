import os
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

PEXELS_KEY = os.getenv("PEXELS_KEY", "")
GEMINI_KEY = os.getenv("GEMINI_KEY", "")
ELEVENLABS_KEY = os.getenv("ELEVENLABS_KEY", "")
HEYGEN_KEY = os.getenv("HEYGEN_KEY", "")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_KEY", "")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
DOWNSUB_KEY = os.getenv("DOWNSUB_KEY", "")
PIXABAY_KEY = os.getenv("PIXABAY_KEY", "")
ATLASCLOUD_KEY = os.getenv("ATLASCLOUD_KEY", "")
# Groq — fast, cheap Whisper transcription API. If set, word-level alignment is
# offloaded here (large-v3-turbo) instead of running whisper on the local CPU.
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_WHISPER_MODEL = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")
# Trial grant (Stripe checkout with trial_period_days). Was 3; cut to 2 for COGS.
TRIAL_CREDITS = max(1, int(os.getenv("TRIAL_CREDITS", "2")))
# Cheap Atlas text for titles/scripts/planning (~$0.25/$1.50 per 1M vs 3.5's $1.50/$9).
GEMINI_TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-3.1-flash-lite")
ATLAS_TEXT_MODEL = os.getenv("ATLAS_TEXT_MODEL", "google/gemini-3.1-flash-lite")
# Explainer hook stills only — never cinematic / body B-roll.
ATLAS_PREMIUM_IMAGE_MODEL = os.getenv(
    "ATLAS_PREMIUM_IMAGE_MODEL",
    "google/nano-banana-2-lite/text-to-image-developer",
)
# Concept segmentation model (kept configurable so we can trade speed/quality).
CONCEPT_SEGMENTER_MODEL = os.getenv("CONCEPT_SEGMENTER_MODEL", GEMINI_TEXT_MODEL)
# Illustration generation concurrency (API-bound, safe to raise).
ILLUSTRATION_WORKERS = int(os.getenv("ILLUSTRATION_WORKERS", "16"))
# Trial / lite cooks — keep the box healthy under the FIFO cook queue.
ILLUSTRATION_WORKERS_LITE = int(os.getenv("ILLUSTRATION_WORKERS_LITE", "6"))
# Max simultaneous cooks on THIS process (web in-process queue or each worker).
MAX_CONCURRENT_COOKS = int(os.getenv("MAX_CONCURRENT_COOKS", "1"))
# When false, the web dyno only enqueues jobs — a separate `python -m webapp.worker`
# process must claim and run them (optimum / multi-worker setup).
COOK_ON_WEB = os.getenv("COOK_ON_WEB", "1").strip().lower() in ("1", "true", "yes", "on")
# When true, web spawns cooks on Modal (scale-to-zero). Prefer over always-on DO workers.
COOK_ON_MODAL = os.getenv("COOK_ON_MODAL", "0").strip().lower() in ("1", "true", "yes", "on")
MODAL_APP_NAME = (os.getenv("MODAL_APP_NAME", "channelrecipe-cook") or "channelrecipe-cook").strip()
# Cap parallel Modal cook containers (cost ceiling under burst).
MODAL_MAX_CONCURRENT = max(1, int(os.getenv("MODAL_MAX_CONCURRENT", "8")))
# Fly Machines one-shot cooks (alternative when Modal billing rejects cards).
COOK_ON_FLY = os.getenv("COOK_ON_FLY", "0").strip().lower() in ("1", "true", "yes", "on")
FLY_API_TOKEN = (os.getenv("FLY_API_TOKEN", "") or "").strip()
FLY_COOK_APP = (os.getenv("FLY_COOK_APP", "channelrecipe-cook") or "channelrecipe-cook").strip()
FLY_COOK_IMAGE = (os.getenv("FLY_COOK_IMAGE", "") or "").strip()
FLY_COOK_REGION = (os.getenv("FLY_COOK_REGION", "sjc") or "sjc").strip()
FLY_COOK_CPUS = max(1, int(os.getenv("FLY_COOK_CPUS", "2")))
FLY_COOK_MEMORY_MB = max(1024, int(os.getenv("FLY_COOK_MEMORY_MB", "4096")))
# How often workers poll for new jobs (seconds).
WORKER_POLL_SECONDS = float(os.getenv("WORKER_POLL_SECONDS", "2"))
# Reclaim jobs stuck in "running" with a stale heartbeat (seconds).
# Keep short so redeploys don't strand cooks for 15 minutes.
WORKER_STALE_SECONDS = int(os.getenv("WORKER_STALE_SECONDS", "180"))
# How long a worker waits for in-flight cooks after SIGTERM before re-queueing them.
WORKER_DRAIN_SECONDS = int(os.getenv("WORKER_DRAIN_SECONDS", "1200"))
# Cap parallel Atlas TTS calls on the web dyno (each can take 30–90s).
MAX_CONCURRENT_VOICEOVERS = max(1, int(os.getenv("MAX_CONCURRENT_VOICEOVERS", "2")))
# Threadpool size for sync FastAPI routes (voiceover/thumbnail/Gemini).
WEB_THREADPOOL_SIZE = max(8, int(os.getenv("WEB_THREADPOOL_SIZE", "32")))
# Fallback queue ETA when we lack recent render_events (minutes).
EST_MINUTES_PER_COOK = float(os.getenv("EST_MINUTES_PER_COOK", "7"))

# Recipe Brain chat (starter pack API always works; chat gated).
RECIPE_BRAIN_ENABLED = os.getenv("RECIPE_BRAIN_ENABLED", "0").strip().lower() in (
    "1", "true", "yes", "on",
)

# Fish Audio voice clone (rights-gated). Off by default.
FISH_API_KEY = (os.getenv("FISH_API_KEY", "") or "").strip()
VOICE_CLONE_ENABLED = os.getenv("VOICE_CLONE_ENABLED", "0").strip().lower() in (
    "1", "true", "yes", "on",
)
# Premium: credits charged to create a persistent Fish clone (0 = free when enabled).
VOICE_CLONE_CREDIT_COST = max(0, int(os.getenv("VOICE_CLONE_CREDIT_COST", "1")))

RESEND_KEY = os.getenv("RESEND_KEY", "")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
# Legacy (kept for backward compat)
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")
STRIPE_PRICE_ID_ANNUAL = os.getenv("STRIPE_PRICE_ID_ANNUAL", "")
# Two-tier pricing
STRIPE_PRICE_STARTER_MONTHLY = os.getenv("STRIPE_PRICE_STARTER_MONTHLY", "")
STRIPE_PRICE_STARTER_ANNUAL = os.getenv("STRIPE_PRICE_STARTER_ANNUAL", "")
STRIPE_PRICE_DAILY_MONTHLY = os.getenv("STRIPE_PRICE_DAILY_MONTHLY", "")
STRIPE_PRICE_DAILY_ANNUAL = os.getenv("STRIPE_PRICE_DAILY_ANNUAL", "")
STRIPE_PRICE_TOPUP_5 = os.getenv("STRIPE_PRICE_TOPUP_5", "")
STRIPE_PRICE_TOPUP_15 = os.getenv("STRIPE_PRICE_TOPUP_15", "")

# Fernet key (or passphrase) for encrypting per-user BYOK secrets (HeyGen, etc.).
# Generate once: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
SECRETS_KEY = (os.getenv("SECRETS_KEY", "") or "").strip()

# Admin emails (comma-separated). Required for /api/admin/* and Settings keys.
ADMIN_EMAILS = [
    e.strip().lower()
    for e in (os.getenv("ADMIN_EMAILS", "") or "").split(",")
    if e.strip()
]

# Telemetry (all optional — everything stays inert if these are blank)
POSTHOG_KEY = (os.getenv("POSTHOG_KEY", "") or "").strip()
POSTHOG_HOST = (os.getenv("POSTHOG_HOST", "https://us.i.posthog.com") or "").strip()
SENTRY_DSN = (os.getenv("SENTRY_DSN", "") or "").strip()

# Object storage (DigitalOcean Spaces / S3). If unset, files stay on local disk.
# Strip whitespace carefully — DO/Fly env paste often leaves a trailing \n.
def _env_clean(name: str) -> str:
    return "".join((os.getenv(name, "") or "").split())

def _env_secret(name: str) -> str:
    # Access keys/secrets: trim ends + quotes only (preserve + / = inside).
    return (os.getenv(name, "") or "").strip().strip('"').strip("'")

SPACES_KEY = _env_secret("SPACES_KEY")
SPACES_SECRET = _env_secret("SPACES_SECRET")
SPACES_BUCKET = _env_clean("SPACES_BUCKET")
SPACES_REGION = _env_clean("SPACES_REGION")                 # e.g. "fra1", "nyc3"
SPACES_ENDPOINT = _env_clean("SPACES_ENDPOINT")            # e.g. "https://fra1.digitaloceanspaces.com"
SPACES_CDN_ENDPOINT = _env_clean("SPACES_CDN_ENDPOINT")    # optional CDN base for public URLs

OUTPUT_DIR = ROOT_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
VIDEO_FPS = 30

WIKIMEDIA_USER_AGENT = "VideoFactory/1.0 (https://github.com/videofactory; contact@videofactory.dev)"
WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"
PEXELS_API = "https://api.pexels.com/v1/search"
PEXELS_VIDEO_API = "https://api.pexels.com/v1/videos/search"
PIXABAY_VIDEO_API = "https://pixabay.com/api/videos/"
HEYGEN_API = "https://api.heygen.com"

SWAP_RATE_PRESETS = {
    "fast": (2, 4),
    "medium": (5, 10),
    "slow": (10, 20),
}
