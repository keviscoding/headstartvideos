"""Optional Playwright GFX lane wrapping frontier-gfx-kit/motion.py.

Style-agnostic: skins/cutouts come from the channel style pack directory
(default: frontier-gfx-kit). Falls back by raising — caller may use Pillow lane.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_kit_motion(kit_root: Path | None = None):
    root = Path(kit_root) if kit_root else _repo_root() / "frontier-gfx-kit"
    motion_path = root / "motion.py"
    if not motion_path.exists():
        raise FileNotFoundError(f"kit motion.py missing: {motion_path}")
    # Ensure assets/cutouts symlink layout
    assets = root / "assets"
    assets.mkdir(exist_ok=True)
    for name in ("cutouts", "fonts", "textures"):
        src = root / name
        dst = assets / name
        if src.is_dir() and not dst.exists():
            try:
                dst.symlink_to(src.resolve(), target_is_directory=True)
            except OSError:
                pass
    spec = importlib.util.spec_from_file_location("frontier_kit_motion", motion_path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load kit motion")
    mod = importlib.util.module_from_spec(spec)
    # Prefer full ffmpeg for encode
    os.environ.setdefault(
        "FFMPEG_BIN",
        "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
    )
    # Insert kit dir so relative imports inside motion resolve if any
    sys.path.insert(0, str(root))
    spec.loader.exec_module(mod)
    return mod


def _pick_names(mod, text: str, n: int, seed: int) -> List[str]:
    words = [w.strip(".,!?;:\"'").lower() for w in (text or "").split()]
    names = []
    if hasattr(mod, "pick_cutouts"):
        try:
            names = list(mod.pick_cutouts(words, n=n, seed=seed) or [])
        except Exception:
            names = []
    keys = list(getattr(mod, "CUTOUTS", {}) or {})
    i = 0
    while len(names) < n and keys:
        k = keys[(seed + i) % len(keys)]
        if k not in names:
            names.append(k)
        i += 1
        if i > len(keys) + 5:
            break
    return names[:n]


def render_playwright_gfx_lane(
    specs: List[Dict[str, Any]],
    output_dir: Path | str,
    *,
    kit_root: Path | None = None,
    style_name: str = "jung",
    fps: int = 24,
    ground_path: Path | None = None,
) -> List[Path]:
    """Render each GFX spec via Playwright kit. Returns mp4 paths in order."""
    mod = load_kit_motion(kit_root)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ground = ground_path
    if ground is None:
        cand = (_repo_root() / "frontier-gfx-kit" / "textures" / "background_dark.png")
        if cand.exists():
            ground = cand

    jobs = []
    paths: List[Path] = []
    for i, spec in enumerate(specs):
        dur = float(spec.get("end_sec", 0) - spec.get("start_sec", 0))
        dur = max(2.4, min(4.5, dur if dur > 0 else 4.0))
        title = str(spec.get("title") or "")
        subtitle = str(spec.get("subtitle") or "")
        template = str(spec.get("template") or "collage")
        # Map Pillow templates → kit skins
        skin_map = {
            "collage": "collage_dark",
            "scatter": "collage_dark",
            "pillars": "noir",
            "opener": "noir",
            "photonote": "glass",
        }
        skin = skin_map.get(template, "collage_dark")
        need = {"scatter": 8, "pillars": 3, "opener": 6, "photonote": 1, "collage": 3}.get(template, 3)
        names = _pick_names(mod, title + " " + subtitle, need, seed=11 + i * 17)
        items = []
        # Derive short item labels from title words if not provided
        bits = [b.strip() for b in title.replace("—", ".").split(".") if b.strip()]
        if not bits:
            bits = title.split(",")
        for j, b in enumerate(bits[:3] or ["listen", "wait", "walk"]):
            words = b.split()
            items.append({
                "label": " ".join(words[:3])[:42],
                "text": " ".join(words[3:6])[:40] if len(words) > 3 else "",
            })
        while len(items) < 3:
            items.append({"label": names[len(items)] if len(items) < len(names) else "hold", "text": ""})

        sc = mod.scene_from_spec({
            "template": template if template in getattr(mod, "TEMPLATES", []) else "collage",
            "skin": skin if skin in getattr(mod, "SKINS", {}) else "collage_dark",
            "title": title[:110],
            "subtitle": subtitle[:130],
            "cutouts": names,
            "items": items,
        }, index=i, duration=dur, seed=7 + i)
        sc["cutout_names"] = names
        sc["duration"] = dur
        sc["brand"] = style_name
        if ground and Path(ground).exists():
            sc["ground_path"] = str(ground)
        out = out_dir / f"gfx_card_{i:03d}.mp4"
        if out.exists():
            out.unlink()
        jobs.append((sc, out))
        paths.append(out)

    work = out_dir / "_pw_work"
    mod.render_scenes(jobs, fps=fps, workers=1, workdir=work)
    return [p for p in paths if p.exists() and p.stat().st_size > 10_000]
