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


def _pick_names(mod, text: str, n: int, seed: int, style_name: str = "jung") -> List[str]:
    """Thematic cutout names for the active style (Jung ≠ clover/rocket)."""
    names: List[str] = []
    # Prefer Channel Recipe thematic picker (style bans + force lists)
    try:
        from core.frontier_motion_graphics import pick_cutouts_for_text, list_cutouts
        paths = pick_cutouts_for_text(text or "", need=n, seed=seed, style_name=style_name)
        for path in paths:
            # Map filesystem stem → kit CUTOUTS key
            stem = path.stem
            keys = getattr(mod, "CUTOUTS", {}) or {}
            if stem in keys:
                names.append(stem)
                continue
            # fuzzy: spaces vs underscores
            alt = stem.replace(" ", "_")
            alt2 = stem.replace("_", " ")
            for k in keys:
                if k == alt or k == alt2 or k.replace("_", " ") == stem.replace("_", " "):
                    names.append(k)
                    break
    except Exception:
        names = []
    # Kit alias picker as secondary (still filter bans)
    ban_sub = {
        "jung": ["ctyrlistek", "diamond", "rocket", "recycle", "guitar", "apple", "bin", "jesus", "bible", "church"],
        "astro": ["jesus", "bible", "church"],
        "divine": ["rocket", "guitar", "bin"],
    }.get((style_name or "jung").lower(), [])
    if hasattr(mod, "pick_cutouts") and len(names) < n:
        words = [w.strip(".,!?;:\"'").lower() for w in (text or "").split()]
        try:
            extra = list(mod.pick_cutouts(words, n=n, seed=seed) or [])
        except Exception:
            extra = []
        for k in extra:
            if any(b in k.lower() for b in ban_sub):
                continue
            if k not in names:
                names.append(k)
    # Force-list fill from kit keys
    force = {
        "jung": ["vintage_moon", "vintage_eye", "vintage_tiger", "sisyphus_pushing_boulder_sketch",
                 "ancient_pilar", "vintage_cloud", "vintage_collage_bird_dove", "vintage_brain",
                 "vintage_lock", "vintage_key", "vintage_man_mad", "vintage_heartbroken", "vintage_alarm"],
        "astro": ["vintage_moon", "vintage_star", "vintage_cloud", "vintage_eye"],
        "divine": ["vintage_cloud", "vintage_star", "ancient_pilar", "vintage_open_book", "vintage_collage_bird_dove"],
    }.get((style_name or "jung").lower(), [])
    keys = list(getattr(mod, "CUTOUTS", {}) or {})
    for f in force:
        if len(names) >= n:
            break
        # match kit key
        hit = None
        for k in keys:
            if k == f or k.replace(" ", "_") == f.replace(" ", "_") or f.replace("_", " ") in k.replace("_", " "):
                hit = k
                break
        if hit and hit not in names and not any(b in hit.lower() for b in ban_sub):
            names.append(hit)
    i = 0
    while len(names) < n and keys:
        k = keys[(seed + i) % len(keys)]
        if k not in names and not any(b in k.lower() for b in ban_sub):
            names.append(k)
        i += 1
        if i > len(keys) + 8:
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
        need = {"scatter": 8, "pillars": 4, "opener": 6, "photonote": 1, "collage": 5}.get(template, 3)
        names = _pick_names(mod, title + " " + subtitle, need, seed=11 + i * 17, style_name=style_name)
        items = []
        # Prefer curated Jung/shadow micro-labels over raw title fragments / sticker filenames
        curated = {
            "jung": [
                ("the woods", "learn a voice"),
                ("the shadow", "what you refuse"),
                ("do not answer", "not once"),
                ("the persona", "the mask you wear"),
                ("keep walking", "do not look back"),
            ],
            "astro": [
                ("the chart", "what returns"),
                ("the transit", "now"),
                ("the moon", "what it pulls"),
            ],
            "divine": [
                ("listen", "stillness"),
                ("the sign", "already here"),
                ("faith", "without noise"),
            ],
        }.get((style_name or "jung").lower(), [])
        for lab, sub in curated[:3]:
            items.append({"label": lab, "text": sub})
        bits = [b.strip() for b in title.replace("—", ".").split(".") if b.strip()]
        for b in bits:
            if len(items) >= 3:
                break
            words = b.split()
            items.append({
                "label": " ".join(words[:3])[:42],
                "text": " ".join(words[3:6])[:40] if len(words) > 3 else "",
            })
        while len(items) < 3:
            items.append({"label": "hold", "text": ""})

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
