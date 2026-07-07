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
