"""
Niche Preset system -- persistent per-niche configuration.

Each preset is a directory under videofactory/presets/{name}/ containing:
  config.json          -- visual style, swap rate, avatar settings, background
  thumbnails/
    ref_001.png ...    -- reference screenshots for thumbnail generation
    thumb_config.json  -- style prompt, model preference
  channel/
    data.json          -- titles, transcripts, view counts
    analysis.json      -- Claude channel analysis output
  niche_profile.json   -- from Niche Screener (Gemini video analysis)
"""

from __future__ import annotations
import json
import shutil
from pathlib import Path
from dataclasses import dataclass, field, asdict

PRESETS_DIR = Path(__file__).parent.parent / "presets"
PRESETS_DIR.mkdir(exist_ok=True)


@dataclass
class PresetConfig:
    name: str = ""
    # Build Video settings
    recipe: str = "broll_only"
    swap_rate: str = "medium"
    style: str = "auto"
    # Avatar settings
    avatar_id: str = ""
    voice_id: str = ""
    avatar_ratio: float = 0.5
    use_ai_images: bool = True
    background: dict = field(default_factory=lambda: {"type": "color", "value": "#FFFFFF"})
    # Thumbnail settings
    thumbnail_style_prompt: str = ""
    thumbnail_model: str = "google/nano-banana-pro/text-to-image"
    # Script settings
    anthropic_model: str = "claude-sonnet-4-20250514"


def _preset_dir(name: str) -> Path:
    safe_name = name.strip().replace(" ", "_").replace("/", "_").lower()
    return PRESETS_DIR / safe_name


def create_preset(name: str, config: PresetConfig | None = None) -> Path:
    """Create a new preset directory with default config."""
    d = _preset_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "thumbnails").mkdir(exist_ok=True)
    (d / "channel").mkdir(exist_ok=True)

    cfg = config or PresetConfig(name=name)
    cfg.name = name
    save_config(name, cfg)
    return d


def save_config(name: str, config: PresetConfig) -> None:
    """Save the preset's config.json."""
    d = _preset_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "config.json", "w") as f:
        json.dump(asdict(config), f, indent=2)


def load_config(name: str) -> PresetConfig:
    """Load a preset's config. Returns defaults if file missing."""
    d = _preset_dir(name)
    cfg_path = d / "config.json"
    if cfg_path.exists():
        with open(cfg_path) as f:
            data = json.load(f)
        return PresetConfig(**{k: v for k, v in data.items()
                              if k in PresetConfig.__dataclass_fields__})
    return PresetConfig(name=name)


def list_presets() -> list[str]:
    """Return names of all saved presets."""
    if not PRESETS_DIR.exists():
        return []
    return sorted(
        d.name for d in PRESETS_DIR.iterdir()
        if d.is_dir() and (d / "config.json").exists()
    )


def delete_preset(name: str) -> bool:
    """Delete a preset directory entirely."""
    d = _preset_dir(name)
    if d.exists():
        shutil.rmtree(d)
        return True
    return False


def preset_exists(name: str) -> bool:
    return (_preset_dir(name) / "config.json").exists()


# --- Thumbnail references ---

def get_thumbnail_refs(name: str) -> list[str]:
    """Return paths to all saved thumbnail reference images."""
    thumb_dir = _preset_dir(name) / "thumbnails"
    if not thumb_dir.exists():
        return []
    exts = {".png", ".jpg", ".jpeg", ".webp"}
    return sorted(
        str(p) for p in thumb_dir.iterdir()
        if p.suffix.lower() in exts
    )


def save_thumbnail_ref(name: str, image_path: str) -> str:
    """Copy a reference image into the preset's thumbnails folder."""
    thumb_dir = _preset_dir(name) / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    existing = get_thumbnail_refs(name)
    idx = len(existing) + 1
    src = Path(image_path)
    dest = thumb_dir / f"ref_{idx:03d}{src.suffix}"
    shutil.copy2(str(src), str(dest))
    return str(dest)


def save_thumbnail_config(name: str, style_prompt: str, model: str) -> None:
    """Save thumbnail-specific config."""
    thumb_dir = _preset_dir(name) / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    with open(thumb_dir / "thumb_config.json", "w") as f:
        json.dump({"style_prompt": style_prompt, "model": model}, f, indent=2)


def load_thumbnail_config(name: str) -> dict:
    """Load thumbnail-specific config."""
    cfg_path = _preset_dir(name) / "thumbnails" / "thumb_config.json"
    if cfg_path.exists():
        with open(cfg_path) as f:
            return json.load(f)
    return {"style_prompt": "", "model": "google/nano-banana-pro/text-to-image"}


# --- Channel data ---

def save_channel_data(name: str, data: dict) -> None:
    """Save channel data (titles, transcripts, view counts)."""
    ch_dir = _preset_dir(name) / "channel"
    ch_dir.mkdir(parents=True, exist_ok=True)
    with open(ch_dir / "data.json", "w") as f:
        json.dump(data, f, indent=2)


def load_channel_data(name: str) -> dict | None:
    """Load channel data. Returns None if not found."""
    path = _preset_dir(name) / "channel" / "data.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


def save_channel_analysis(name: str, analysis: dict) -> None:
    """Save Claude's channel analysis output."""
    ch_dir = _preset_dir(name) / "channel"
    ch_dir.mkdir(parents=True, exist_ok=True)
    with open(ch_dir / "analysis.json", "w") as f:
        json.dump(analysis, f, indent=2)


def load_channel_analysis(name: str) -> dict | None:
    """Load channel analysis. Returns None if not found."""
    path = _preset_dir(name) / "channel" / "analysis.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


# --- Script Studio state (ideas, titles, script) ---

def _studio_path(name: str) -> Path:
    return _preset_dir(name) / "channel" / "studio.json"


def _load_studio(name: str) -> dict:
    p = _studio_path(name)
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return {}


def _save_studio(name: str, data: dict) -> None:
    d = _preset_dir(name) / "channel"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "studio.json", "w") as f:
        json.dump(data, f, indent=2)


def save_studio_field(name: str, key: str, value: str) -> None:
    """Save a single Script Studio field (ideas, titles, script, etc.)."""
    data = _load_studio(name)
    data[key] = value
    _save_studio(name, data)


def load_studio_field(name: str, key: str) -> str:
    """Load a single Script Studio field."""
    return _load_studio(name).get(key, "")


def load_all_studio(name: str) -> dict:
    """Load all Script Studio fields at once."""
    return _load_studio(name)


# --- History system ---

def _history_path(name: str) -> Path:
    return _preset_dir(name) / "history.json"


def _load_history(name: str) -> list[dict]:
    p = _history_path(name)
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return []


def _save_history(name: str, history: list[dict]) -> None:
    d = _preset_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "history.json", "w") as f:
        json.dump(history, f, indent=2)


def add_history_entry(name: str, entry_type: str, data: dict) -> None:
    """Add an entry to the preset's history log.

    entry_type: 'ideas', 'titles', 'script', 'thumbnail', 'video', 'voiceover', 'analysis'
    data: dict with content and metadata
    """
    import time
    history = _load_history(name)
    history.append({
        "type": entry_type,
        "timestamp": time.time(),
        "data": data,
    })
    _save_history(name, history)


def get_history(name: str, entry_type: str = "") -> list[dict]:
    """Get history entries, optionally filtered by type."""
    history = _load_history(name)
    if entry_type:
        return [h for h in history if h["type"] == entry_type]
    return history


# --- Niche profile (from video analyzer) ---

def save_niche_profile(name: str, profile: dict) -> None:
    """Save the NicheProfile JSON from the Niche Screener."""
    d = _preset_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "niche_profile.json", "w") as f:
        json.dump(profile, f, indent=2)


def load_niche_profile(name: str) -> dict | None:
    """Load saved niche profile."""
    path = _preset_dir(name) / "niche_profile.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None
