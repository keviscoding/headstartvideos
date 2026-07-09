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
# Default Gemini text model (gemini-2.5-flash was retired — use 3.5 Flash).
GEMINI_TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-3.5-flash")
# Concept segmentation model (kept configurable so we can trade speed/quality).
CONCEPT_SEGMENTER_MODEL = os.getenv("CONCEPT_SEGMENTER_MODEL", GEMINI_TEXT_MODEL)
# Illustration generation concurrency (API-bound, safe to raise).
ILLUSTRATION_WORKERS = int(os.getenv("ILLUSTRATION_WORKERS", "16"))
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

# Comma-separated list of admin emails allowed to touch ops-only endpoints
# (Settings / API keys). If empty, those endpoints are locked to everyone.
ADMIN_EMAILS = [e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()]

# Telemetry (all optional — everything stays inert if these are blank)
POSTHOG_KEY = os.getenv("POSTHOG_KEY", "")            # PostHog project API key (public)
POSTHOG_HOST = os.getenv("POSTHOG_HOST", "https://us.i.posthog.com")
SENTRY_DSN = os.getenv("SENTRY_DSN", "")              # Sentry DSN (backend + frontend)

# Object storage (DigitalOcean Spaces / S3). If unset, files stay on local disk.
SPACES_KEY = os.getenv("SPACES_KEY", "")
SPACES_SECRET = os.getenv("SPACES_SECRET", "")
SPACES_BUCKET = os.getenv("SPACES_BUCKET", "")
SPACES_REGION = os.getenv("SPACES_REGION", "")                 # e.g. "fra1", "nyc3"
SPACES_ENDPOINT = os.getenv("SPACES_ENDPOINT", "")            # e.g. "https://fra1.digitaloceanspaces.com"
SPACES_CDN_ENDPOINT = os.getenv("SPACES_CDN_ENDPOINT", "")    # optional CDN base for public URLs

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
