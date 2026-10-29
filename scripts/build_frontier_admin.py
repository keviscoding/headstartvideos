"""Build the OWNER-ONLY preview from a private source archive; never commit it.

Usage: python scripts/build_frontier_admin.py --source /path/to/Frontier-5.3-FULL.zip
       [--upload]  # private Spaces object, not a public/CDN media upload
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SOURCE_SHA256 = "e379dc601672282040a2adbf30f7bf7f7d34fcdf38154e5f6a24ec6c2528cadf"
from scripts import studio_branding
VERSION = "5.3-admin.112"
PRIVATE_WORKFLOWS = {
    "cr-style-delivery-prompt.txt": ROOT / "data/frontier/reference2d-compiler.txt",
    "cr-style-study-brief.txt": ROOT / "data/frontier/reference2d-study.txt",
}
RUNTIME_SUFFIXES = {".py", ".html", ".md", ".command", ".cmd", ".js", ".css"}


def runtime_resources(root: Path):
    """Include owned browser assets without collecting caches or local secrets."""
    runtime = root / "local_runtime"
    for path in sorted(runtime.iterdir()):
        if path.suffix not in RUNTIME_SUFFIXES or not path.is_file():
            continue
        if path.is_symlink():
            raise ValueError("Runtime resources must be ordinary owned files.")
        yield path, path.name
    hand = runtime / "drawon_art" / "hand.png"           # the whiteboard reveal's drawing hand (cr_drawon)
    if hand.exists():
        if hand.is_symlink() or not hand.is_file() or hand.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("The drawing hand must be a small ordinary picture.")
        yield hand, "drawon_art/hand.png"
    skills = runtime / "claude_skills"                   # Claude Code skills for this folder (/make-videos and co)
    if skills.exists():
        if skills.is_symlink() or not skills.is_dir():
            raise ValueError("Claude Code skills must be an owned directory.")
        for path in sorted(skills.glob("*/SKILL.md")):
            if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
                raise ValueError("Claude Code skills must be ordinary owned files.")
            yield path, ".claude/skills/" + path.parent.name + "/SKILL.md"
    art = runtime / "workspace_art"
    if art.exists():
        if art.is_symlink() or not art.is_dir():
            raise ValueError("Workspace artwork must be an owned directory.")
        for path in sorted(art.iterdir()):
            if path.suffix != ".svg" or not path.is_file():
                continue
            if path.is_symlink():
                raise ValueError("Workspace artwork must be ordinary owned files.")
            yield path, "workspace_art/" + path.name


def claude_code_files() -> dict:
    """The folder's Claude Code setup, in the download itself, so Studio's tools are there from the very first
    Claude Code session (before Studio has ever run): .mcp.json names the folder's Node launcher, and
    .claude/settings.json approves both servers and asks before paid tools. Studio's own configure() keeps both
    current from then on; the updater never overwrites .mcp.json (it holds the account key)."""
    import importlib.util
    source = Path(__file__).resolve().parents[1] / "local_runtime" / "cr_claude_code.py"     # this repository's own
    spec = importlib.util.spec_from_file_location("cr_claude_code_build", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {".mcp.json": (json.dumps(module.first_run_servers(), indent=2) + "\n").encode(),
            ".claude/settings.json": (json.dumps(module.folder_settings({}), indent=2) + "\n").encode()}


def private_preview_resources(root: Path):
    """Package hash-bound style reference frames shown on recipe cards.

    These are labelled references for each recipe's look (with the source
    channel credited), not Studio output. The retired engine sample frames
    are never packaged.
    """
    folder=root / "data/frontier/recipe-references"
    if not folder.exists():
        return []
    manifest=folder / "manifest.json"
    if folder.is_symlink() or not folder.is_dir() or manifest.is_symlink() or not manifest.is_file():
        raise ValueError("Style reference frames must be ordinary owned files.")
    data=json.loads(manifest.read_text())
    if data.get("schema")!=2 or data.get("kind")!="style_reference" or not isinstance(data.get("frames"),dict):
        raise ValueError("Style reference frames need a schema 2 manifest.")
    resources=[(manifest,"cr_recipe_frames/manifest.json")]
    for style,row in data["frames"].items():
        if (not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,139}",style) or not isinstance(row,dict)
                or row.get("file")!=style+".jpg" or row.get("kind") not in ("style_reference","studio_sample")
                or (row.get("kind")=="style_reference" and not str(row.get("channel") or "").strip())):
            raise ValueError("Invalid style reference identity.")
        path=folder / row["file"]
        if path.is_symlink() or not path.is_file() or path.stat().st_size>1024*1024 or hashlib.sha256(path.read_bytes()).hexdigest()!=row.get("sha256"):
            raise ValueError("Style reference frame changed.")
        resources.append((path,"cr_recipe_frames/"+path.name))
    return resources


def private_sound_resources(root: Path):
    """ChannelRecipe's recorded effects library (generated once, owned), shipped under assets/sfx/cr/."""
    folder = root / "data/frontier/sfx-library"
    if not folder.exists():
        return []
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("The effects library must be an ordinary owned folder.")
    resources = []
    for path in sorted(folder.iterdir()):
        if path.name == "library.json" or (path.suffix == ".wav" and re.fullmatch(r"[a-z_]+_\d+\.wav", path.name)):
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("Effects library files must be small ordinary files.")
            resources.append((path, "assets/sfx/cr/" + path.name))
    # the sound of each place (cr_ambience): seamless loops, one folder deeper
    places = folder / "ambience"
    if places.exists():
        if places.is_symlink() or not places.is_dir():
            raise ValueError("The ambience library must be an ordinary owned folder.")
        for path in sorted(places.iterdir()):
            if path.name == "library.json" or re.fullmatch(r"[a-z]+_\d+\.mp3", path.name):
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError("Ambience library files must be small ordinary files.")
                resources.append((path, "assets/sfx/cr/ambience/" + path.name))
    return resources


SHORTS_SUFFIXES = {".py", ".js", ".mjs", ".json", ".md", ".html", ".css", ".txt", ".ttf", ".otf", ".woff2", ".woff", ".onnx",
                   ".glsl", ".wasm", ".png", ".jpg", ".svg", ".bin", ".hdr", ".ktx2", ".basis", ".wav", ".mp3"}


def private_shorts_resources(root: Path):
    """The Shorts pipelines (the 3D, photo, Ranking and Roblox Story Shorts recipes): their code and the Shorts 3D engine's code,
    shipped under shorts/. Assets (people, animals, mocap, places, the host, sound libraries) come in the first-use
    asset pack; the Ranking Shorts' meme sound library (about 17 MB of MP3s) ships with its pipeline."""
    folder = root / "data/frontier/shorts"
    if not folder.exists():
        return []
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("The Shorts pipelines must be an ordinary owned folder.")
    resources = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file() or p.is_symlink()):
        rel = path.relative_to(folder).as_posix()
        if "__pycache__" in rel or path.name.startswith("."):
            continue
        if path.is_symlink() or path.suffix.lower() not in SHORTS_SUFFIXES or path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("Shorts files must be small ordinary code or data files: " + rel)
        if rel.split("/")[0] not in {"zack", "hoodie", "sound", "rbx", "ranking", "roblox"}:
            raise ValueError("Unexpected Shorts folder: " + rel)
        resources.append((path, "shorts/" + rel))
    return resources


# ChannelRecipe's own additions to the 3D engine (Studio 79+): the designed-cast kit, kept in the gitignored
# data/frontier/threed/ (lib/designed/*.js and the catalog entries its kinds add), never in this public repository.
THREED_KIT = re.compile(r"lib/designed/[a-z_]+\.js")
THREED_CATALOG = "catalog-additions.json"


def private_threed_resources(root: Path):
    """The designed-cast kit's engine modules, shipped under threed/engine/ (the engine registers the library through
    ENGINE_PATCHES; the director's catalog gains its kinds through merge_catalog)."""
    folder = root / "data/frontier/threed"
    if not folder.exists():
        return []
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("The 3D engine additions must be an ordinary owned folder.")
    resources = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file() or p.is_symlink()):
        rel = path.relative_to(folder).as_posix()
        if rel == THREED_CATALOG or path.name.startswith("."):
            continue
        if path.is_symlink() or not THREED_KIT.fullmatch(rel) or path.stat().st_size > 1024 * 1024:
            raise ValueError("3D engine additions must be small ordinary lib/designed/*.js files: " + rel)
        resources.append((path, "threed/engine/" + rel))
    if resources and not (folder / THREED_CATALOG).is_file():
        raise ValueError("The designed-cast kit needs its catalog entries (data/frontier/threed/catalog-additions.json).")
    return resources


# The animated 2D kit (Studio 103+; the renderer platform since 104): the page runtime every channel's Claude-written
# renderer runs in, its optional helpers and example, the build, casting and director prompts and the fight sounds
# (cr_animated2d.py drives it), kept in the gitignored data/frontier/animated2d/, never in this public repository.
ANIMATED2D_FILES = re.compile(r"(core|rig|draw|moves|fx|film|stage|bind|example-stick|example-own)\.js|(cast|director|build|review)\.txt|(cast|director)-\d+\.txt|"
                              r"AUTHORING\.md")
ANIMATED2D_SOUND = re.compile(r"sounds/[a-z_]+\.mp3")


def private_animated2d_resources(root: Path):
    """The animated 2D kit, shipped under animated2d/, and its fight sounds under assets/sfx/animated2d/ (Studio's mixer
    plays a "file:sfx/animated2d/<name>.mp3" mark)."""
    folder = root / "data/frontier/animated2d"
    if not folder.exists():
        return []
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("The animated 2D kit must be an ordinary owned folder.")
    resources = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file() or p.is_symlink()):
        rel = path.relative_to(folder).as_posix()
        if path.name.startswith("."):
            continue
        if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("Animated 2D kit files must be small ordinary files: " + rel)
        if ANIMATED2D_FILES.fullmatch(rel):
            resources.append((path, "animated2d/" + rel))
        elif ANIMATED2D_SOUND.fullmatch(rel):
            resources.append((path, "assets/sfx/animated2d/" + rel.split("/", 1)[1]))
        else:
            raise ValueError("Unexpected file in the animated 2D kit: " + rel)
    names = {dest.split("/", 1)[1] for _, dest in resources if dest.startswith("animated2d/")}
    missing = sorted({"core.js", "rig.js", "draw.js", "moves.js", "fx.js", "film.js", "stage.js", "bind.js", "example-stick.js",
                      "example-own.js", "cast.txt", "director.txt", "build.txt", "review.txt", "AUTHORING.md"} - names)
    if resources and missing:
        raise ValueError("The animated 2D kit is incomplete: " + ", ".join(missing))
    return resources


ANIMATED3D_FILES = ("core3d.js", "example3d.js", "AUTHORING.md")


def private_animated3d_resources(root: Path):
    """The animated 3D kit (Studio 105), shipped under animated3d/: its page runtime, example renderer and guide. The
    3D page reads three.js from the 3D engine Studio already ships (threed/engine/vendor/three)."""
    folder = root / "data/frontier/animated3d"
    if not folder.exists():
        return []
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("The animated 3D kit must be an ordinary owned folder.")
    resources = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file() or p.is_symlink()):
        rel = path.relative_to(folder).as_posix()
        if path.name.startswith("."):
            continue
        if path.is_symlink() or path.stat().st_size > 1024 * 1024 or rel not in ANIMATED3D_FILES:
            raise ValueError("Unexpected file in the animated 3D kit: " + rel)
        resources.append((path, "animated3d/" + rel))
    missing = sorted(set(ANIMATED3D_FILES) - {dest.split("/", 1)[1] for _, dest in resources})
    if resources and missing:
        raise ValueError("The animated 3D kit is incomplete: " + ", ".join(missing))
    return resources


def merge_catalog(contents: bytes, root: Path = None) -> bytes:
    """The engine's static catalog (what the 3D director may build) with the designed-cast kit's kinds and look preset."""
    extra = (root or ROOT) / "data/frontier/threed" / THREED_CATALOG
    if not extra.is_file():
        return contents
    add = json.loads(extra.read_text(encoding="utf-8"))
    catalog = json.loads(contents.decode("utf-8"))
    for key in ("kinds", "presets"):
        rows = add.get(key) or {}
        clash = [k for k in rows if k in (catalog.get(key) or {})]
        if clash:
            raise ValueError("The designed-cast catalog entries clash with the engine's own: " + ", ".join(clash[:5]))
        catalog.setdefault(key, {}).update(rows)
    return json.dumps(catalog, indent=1, ensure_ascii=False).encode("utf-8")


READY_MADE = re.compile(r"cr-[a-z0-9]+(?:-[a-z0-9]+){0,6}")


def private_recipe_resources(root: Path):
    """ChannelRecipe's ready-made recipes: styles learned from benchmark channels and tested end to end here,
    shipped as built-in styles (styles/<name>.json) with their own style drawing and character sheet
    (assets/styles/cr/<name>/). A recipe may only point at its own pictures."""
    folder = root / "data/frontier/recipes"
    if not folder.exists():
        return []
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("Ready-made recipes must be an ordinary owned folder.")
    resources = []
    for recipe in sorted(folder.iterdir()):
        if recipe.name.startswith(".") or not recipe.is_dir():
            continue
        if recipe.is_symlink() or not READY_MADE.fullmatch(recipe.name):
            raise ValueError("A ready-made recipe folder must be named cr-<words>.")
        style_path = recipe / "style.json"
        if style_path.is_symlink() or not style_path.is_file():
            raise ValueError(f"{recipe.name} has no style.json.")
        style = json.loads(style_path.read_text())
        if style.get("name") != recipe.name or "cr_style_clone" in style or style.get("builtin") is not True:
            raise ValueError(f"{recipe.name}: the style must carry its own name, builtin: true and no study records.")
        look = style.get("look") if isinstance(style.get("look"), dict) else {}
        own = {f"styles/cr/{recipe.name}/style.jpg", f"styles/cr/{recipe.name}/character.png"}
        for key in ("style_image", "character_image", "reference", "sample"):
            if look.get(key) and look[key] not in own:
                raise ValueError(f"{recipe.name}: look.{key} must be one of its own pictures.")
            if look.get(key) and not (recipe / Path(look[key]).name).is_file():
                raise ValueError(f"{recipe.name}: look.{key} is missing.")
        if _machine_paths(style):
            raise ValueError(f"{recipe.name}: the style points at files on the computer it was made on: "
                             + ", ".join(_machine_paths(style)[:3]))
        resources.append((style_path, f"styles/{recipe.name}.json"))
        for name in ("style.jpg", "character.png"):
            path = recipe / name
            if path.exists():
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
                    raise ValueError(f"{recipe.name}/{name} must be a small ordinary picture.")
                resources.append((path, f"assets/styles/cr/{recipe.name}/{name}"))
        resources += _recipe_kit(recipe, look)
    return resources


KIT_FILES = re.compile(r"(kit\.json|kit\.js|look_overrides\.json|fonts/[A-Za-z0-9 _.-]+\.(ttf|otf|woff2))")


def _machine_paths(value, at="") -> list:
    """Every setting holding an absolute path: [setting path]."""
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in _machine_paths(v, f"{at}.{k}" if at else k)]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in _machine_paths(v, f"{at}[{i}]")]
    return [at] if isinstance(value, str) and re.match(r"^(/|[A-Za-z]:[\\/]|~)", value) else []


def _recipe_kit(recipe: Path, look: dict) -> list:
    """A ready-made recipe's own graphics kit (genkit/): its manifest, code, look overrides and any faces of its own,
    shipped under assets/styles/cr/<name>/genkit/ and named there by the style."""
    kit = recipe / "genkit"
    manifest = (look.get("genkit") or {}).get("manifest") if isinstance(look.get("genkit"), dict) else None
    want = f"assets/styles/cr/{recipe.name}/genkit/kit.json"
    if not manifest and not kit.exists():
        return []
    if manifest != want or kit.is_symlink() or not (kit / "kit.json").is_file() or not (kit / "kit.js").is_file():
        raise ValueError(f"{recipe.name}: look.genkit.manifest must be {want}, with genkit/kit.json and kit.js beside it.")
    spec = json.loads((kit / "kit.json").read_text())
    if spec.get("js", "kit.js") != "kit.js" or not isinstance(spec.get("types"), dict) or not spec["types"]:
        raise ValueError(f"{recipe.name}: genkit/kit.json must name kit.js and its graphics.")
    out = []
    for path in sorted(p for p in kit.rglob("*") if p.is_file() or p.is_symlink()):
        rel = path.relative_to(kit).as_posix()
        if path.is_symlink() or not KIT_FILES.fullmatch(rel) or path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError(f"{recipe.name}/genkit/{rel} is not a kit file.")
        out.append((path, f"assets/styles/cr/{recipe.name}/genkit/{rel}"))
    return out


# Engine sample films from the retired brand; recipe cards use style references.
RETIRED_SAMPLES = {"assets/brand/1965 Lotus 38 - Documentary sample.mp4",
                   "assets/brand/POV Paid-Off Civic - 2D Stories sample.mp4",
                   "assets/brand/The Shadow You Keep Feeding Without Knowing It.mp4"}


# Reviewed fixes to the pinned engine for customers' computers. Its machine
# checks were tuned for a dedicated render box: a Mac with under 20 GB free
# disk skipped the style study's real-browser test (so the study stopped at the
# end) and made Level-Up Stories wait forever. A few test frames need far less.
# Every patch must match exactly once, so a new engine can't silently skip one.
PRESET_WHITE_STUDIO = "  documentary: null,\n\n  // WHITE STUDIO (learned 2026-10-15 from clean 3D reconstruction channels): an all-white matte world. Every surface reads\n  // as white or pale grey plaster (albedo desaturated and lifted), the faceless mannequins white, the frame a pale duotone\n  // from a light cool grey to white, so a night or an interior stays high key: night is a cooler, slightly dimmer white.\n  // Soft shadows and soft grey outlines (models and label tags stay readable on white), no grain, a faint vignette; fire keeps a hint of its colour. Accents come from the graphics laid over the 3D.\n  white_studio: {\n    desc: 'all-white matte 3D reconstruction: every surface white or pale grey plaster, white faceless mannequins, a pale grey seamless world, soft studio light, high key even at night, no grain',\n    stylised: true, tragedy_ok: true,\n    grade: { mul: { exposure: 1.05 }, add: { lift: [0.015, 0.015, 0.018] },\n      set: { mono: 0.8, ink: [0.2, 0.22, 0.26], mid: [0.7, 0.72, 0.75], midAt: 0.35, paper: [0.95, 0.95, 0.955], sat: 0.5,\n        contrast: 1.3, vignette: 0.12, vigPow: 1.3, grain: 0, bloom: 0.04, shoulder: 0.9 } },\n    night: { mul: { exposure: 1.7 }, add: { lift: [0.03, 0.032, 0.038] },\n      set: { mono: 0.95, ink: [0.19, 0.21, 0.26], mid: [0.68, 0.7, 0.74], midAt: 0.35, paper: [0.93, 0.94, 0.95], sat: 0.5,\n        contrast: 1.1, vignette: 0.14, vigPow: 1.3, grain: 0, bloom: 0.04, shoulder: 0.9 } },\n    atmos: { tint: [0.9, 0.91, 0.93], tintAmt: 0.5, density: 0.25, sky: [0.9, 0.91, 0.93] },\n    ink: { amt: 0.55, px: 1.6, pxC: 0.9, col: [0.35, 0.36, 0.4], far: [400, 1200], depthK: 0.03, crease: 0.85 },\n    labelKeep: true, labelInk: 'dark',\n    world: { tufts: false },\n    material: { rough: [0.72, 0.95], metal: 0.0, env: 0.12, skin: 0xE6E6E6,\n      look: { flat: 6, nrm: 0, pal: 1, sat: 0.0, gamma: 0.5 } },\n    shadow: { soft: 2.6 },\n    qa: { hot_start: 0.95, hot_target: 0.9, hot_area: 0.97, face_white: 1.0, face_mean: 256, dark_sep: 0, scrim_max: 0.86 },\n  },\n"
LOOK_WHITE_STUDIO = ' "white_studio": {"director": "THE LOOK (this film\'s preset: WHITE STUDIO — the engine renders every shot as an all-white matte world on top of your look block; you never set it and never imitate it with grades): a clean 3D reconstruction in white and pale grey plaster, faceless white mannequins for every person (never a human face), a pale seamless ground and sky. Light EVERY scene as soft overcast daylight (time afternoon or morning, sky overcast, grade neutral), even when the story happens at night: this world is a timeless white model, and night is told by the words and the labels, not by darkness. Never stage a shot whose subject is only fog, sky or open water: always show a model (the ship, the room, the figures) at a scale that reads. A white model alone against the open sky disappears: frame aircraft and airships from above or level with the ground, a hangar, a mast or the horizon behind them, and keep a large subject at medium distance, never a speck far away. Real shapes, era-accurate ships, vehicles and buildings. Captions live in the bottom quarter of the frame: keep it free of text and of the subject\'s key action.", "review": "THE LOOK IS INTENTIONAL — this film uses the WHITE STUDIO preset: every surface white or pale grey, white faceless mannequins, a pale world, soft daylight even for night, labels in dark ink on pale tags. That is the look: do not score it as washed out, flat, colourless or low-detail, and do not fix it back toward realism, colour or darkness. Judge craft INSIDE the look: the story, the subject reading at a glance by its shape and shading, the motion, the hard fails H1-H8 (all still apply), faceless mannequins. Flag a shot that shows only fog, sky or water, and a white model that disappears against a white sky (fix: reframe it with the ground or a structure behind it, or bring it closer).", "qa": "THE LOOK OF THIS FILM IS INTENTIONAL: WHITE STUDIO — an all-white matte world, white faceless mannequins, pale sky and ground, soft daylight, labels in dark ink. Never flag the look itself as washed out or colourless; flag only what is wrong inside it, and a shot whose subject cannot be seen.", "cookbook": "LOOK PRESET white_studio (this film) — stage it like a white scale model on a studio table: soft overcast daylight in every scene, clear shapes and silhouettes, medium and wide views that read as models (ships, rooms, vehicles, crowds of white mannequins as counts), slow orbit or push_in, few props with strong outlines; aircraft and airships framed with the ground or a structure behind them, never alone against the sky. Avoid night lighting, fog-only shots and scenes whose meaning depends on colour or darkness."},\n'
PRESET_STUDIO_COLOR = "\n  // STUDIO COLOUR (2026-10-16): white studio's clean daylight models painted in the creator's palette (a base colour on most\n  // surfaces, one accent on what matters, a dark for contrast): matte painted plaster that keeps its colour, soft daylight\n  // even at night or underwater, a light seamless world, soft grey outlines, no grain. For creators who find white on white flat.\n  studio_color: {\n    desc: 'clean studio 3D reconstruction in the creator palette: matte painted models in a base colour with one accent and a dark, soft daylight, a light seamless world, no grain',\n    stylised: true, tragedy_ok: true,\n    grade: { mul: { exposure: 1.0 }, add: { lift: [0.012, 0.012, 0.012] },\n      set: { mono: 0.1, ink: [0.14, 0.14, 0.15], mid: [0.6, 0.58, 0.55], midAt: 0.4, paper: [0.96, 0.94, 0.88], sat: 0.95,\n        contrast: 1.2, vignette: 0.12, vigPow: 1.3, grain: 0, bloom: 0.04, shoulder: 0.9 } },\n    night: { mul: { exposure: 1.5 }, add: { lift: [0.025, 0.025, 0.028] },\n      set: { mono: 0.1, ink: [0.13, 0.13, 0.15], mid: [0.58, 0.57, 0.55], midAt: 0.4, paper: [0.94, 0.92, 0.87], sat: 0.95,\n        contrast: 1.2, vignette: 0.14, vigPow: 1.3, grain: 0, bloom: 0.04, shoulder: 0.9 } },\n    atmos: { tint: [0.9, 0.88, 0.83], tintAmt: 0.4, density: 0.15, sky: [0.88, 0.86, 0.8] },\n    ink: { amt: 0.5, px: 1.5, pxC: 0.8, col: [0.25, 0.25, 0.27], far: [400, 1200], depthK: 0.03, crease: 0.85 },\n    labelKeep: true, labelInk: 'dark',\n    world: { tufts: false },\n    material: { rough: [0.6, 0.95], metal: 0.0, env: 0.15, skin: 0xEDE6D6,\n      look: { flat: 6, nrm: 0, pal: 1, sat: 0.8, gamma: 0.85 } },\n    shadow: { soft: 2.6 },\n    qa: { hot_start: 0.95, hot_target: 0.9, hot_area: 0.97, face_white: 1.0, face_mean: 256, dark_sep: 0, scrim_max: 0.86 },\n  },\n"
LOOK_STUDIO_COLOR = ' "studio_color": {"director": "THE LOOK (this film\'s preset: STUDIO COLOUR — the engine renders every shot as a clean painted scale model on top of your look block; you never set it and never imitate it with grades): a clean 3D reconstruction like a painted model on a studio table. Paint it ONLY in the film\'s palette (3D SCENE PALETTE in the creative direction): its base colour on most surfaces, models and the faceless mannequins, its accent on the one thing that matters in each shot (the torpedo, the warning light, the key person\'s detail, the label), its dark for shadows, hulls and contrast; never real-world colour beyond the palette. Light EVERY scene as soft daylight (time afternoon or morning, sky overcast or clear, grade neutral), even when the story happens at night, in the dark or underwater: depth, night and danger are told by the words, the labels and the accent colour, never by darkness. No pitch-dark interiors, no night grades, no glowing red rooms. Never stage a shot whose subject is only fog, sky, open water or darkness: always show a model at a scale that reads, framed with the ground, a structure or the horizon behind it. Real shapes, era-accurate ships, vehicles and buildings. Captions live in the bottom quarter of the frame: keep it free of text and of the subject\'s key action.", "review": "THE LOOK IS INTENTIONAL — this film uses the STUDIO COLOUR preset: clean matte models painted in the film\'s palette (a base colour, one accent, a dark), soft daylight even for night or underwater scenes, labels in dark ink on pale tags. Do not score it as unrealistic or flat, and do not fix it toward realism, night or darkness. Judge craft INSIDE the look: the story, the subject reading at a glance, the motion, the hard fails H1-H8 (all still apply), faceless mannequins. Flag a dark, night-lit or underwater-dark shot (fix: soft daylight, the palette\'s base colour), a shot whose subject disappears into the background, and a shot that uses colours outside the palette or loses the accent on what matters.", "qa": "THE LOOK OF THIS FILM IS INTENTIONAL: STUDIO COLOUR — clean painted models in the film\'s palette, soft daylight even at night, labels in dark ink. Never flag the look itself as unrealistic; flag a dark shot, a subject that cannot be seen, and colours outside the palette.", "cookbook": "LOOK PRESET studio_color (this film) — stage it like a painted scale model on a studio table: soft daylight in every scene, the palette\'s base colour on most surfaces, its accent on one thing per shot, clear shapes and silhouettes, medium and wide views that read as models, slow orbit or push_in; subjects framed with the ground or a structure behind them. Avoid night lighting, dark interiors, underwater darkness, fog-only shots and colours outside the palette."},\n'

# DESIGNED (Studio 79): a designed-3D channel's own cast and sets (threed/engine/lib/designed), for the director, reviewer
# and QA (the engine's look preset comes with the kit, lib/designed/preset.js)
LOOK_DESIGNED = ' "designed": ' + json.dumps(json.loads((ROOT / "scripts/studio_designed_look.json").read_text(encoding="utf-8")),
                                             ensure_ascii=False) + ',\n'

ENGINE_PATCHES = {
    # TikTok (Ranking Shorts' pasted links) answers yt-dlp only through browser impersonation: its curl-cffi extra.
    # The floor moves with each release: requirements.txt changing is what makes an in-place update reinstall, and
    # nothing else ever upgrades a customer's yt-dlp (YouTube changes its player every few weeks).
    # Ranking Shorts take burned-in captions and watermarks off clips on this computer: RapidOCR finds the text
    # (LaMa, which paints it out, is a model file fetched on first use). Pinned: 1.3+ caps Python below 3.13 and renamed
    # the detector (text_detector -> text_det), so Python 3.12 customers got another API than 3.13 ones; 1.2.3 is
    # pure Python and installs on 3.10-3.14 (checked on PyPI, 2026-10-21).
    # truststore: Studio's Python checks certificates with the computer's own store, as the browser does (cr_trust),
    # so an antivirus or company firewall that inspects secure connections no longer fails every download. 0.10.5 or
    # later: before it, connections sharing one context could turn each other's certificate check off when they shook
    # hands at once (reproduced 2026-10-26); pure Python, 3.10+ (checked on PyPI, 2026-10-26).
    # OpenCV once, not twice: RapidOCR requires opencv-python (the full build), so the engine's opencv-python-headless
    # was a second copy of the same cv2 (about 44 MB more to download on Windows, the owner's test of 2026-10-26).
    # Both builds hold the same modules (the engine's VideoCapture, inpainting, optical flow, face and QR models); the
    # full one only adds desktop windows, which Studio never opens, and needs nothing more on Windows or a Mac.
    # Folders that already have both keep them (pip removes nothing); the Studio Cloud image keeps its own list.
    "requirements.txt": [
        ('yt-dlp>=2025.10.22; python_version >= "3.10"',
         'yt-dlp[default,curl-cffi]>=2026.8.19; python_version >= "3.10"\n'
         'rapidocr-onnxruntime==1.2.3; python_version >= "3.10"   # Ranking Shorts: finds burned-in text to remove\n'
         'truststore>=0.10.5; python_version >= "3.10"   # Studio trusts this computer\'s certificates, as the browser does'),
        ("opencv-python-headless>=4.8   # VEIL's keeper (loops chained and moved) and the presenter camera moves; "
         "pure-ffmpeg fallback without it",
         "opencv-python>=4.8   # VEIL's keeper (loops chained and moved) and the presenter camera moves; pure-ffmpeg "
         "fallback without it. The full build, as RapidOCR needs: one copy, not two"),
    ],
    "reflearn.py": [
        ('need_lv, need_gb = _envf("REFLEARN_MIN_MEM_LEVEL", 40), _envf("REFLEARN_MIN_DISK_GB", 20)',
         'need_lv, need_gb = _envf("REFLEARN_MIN_MEM_LEVEL", 20), _envf("REFLEARN_MIN_DISK_GB", 3)'),
    ],
    # The engine's own guide says "python app.py", which started the bare engine
    # (no setup page, keys in .env). Starting it that way now opens Studio.
    "app.py": [
        ('if __name__ == "__main__":\n    OUT.mkdir(exist_ok=True)\n',
         'if __name__ == "__main__":\n'
         '    # ChannelRecipe Studio runs this engine inside its own app (setup, accounts and\n'
         '    # workspace). Starting the engine on its own hands over to Studio.\n'
         '    import subprocess\n'
         '    print("Starting ChannelRecipe Studio...", flush=True)\n'
         '    raise SystemExit(subprocess.call([sys.executable, str(HERE / "cr_studio.py")], cwd=str(HERE)))\n'
         'if False:\n    OUT.mkdir(exist_ok=True)\n'),
    ],
    # A story with moving pictures animates its FIRST storyboard items and draws stills from the rest, so still
    # scene_k belongs to item video_n + k. The story timeline zipped the items with the stills, which put every
    # picture one sentence early and never showed the clips (found 2026-10-15 in a test video: the family picture
    # played over "London, 1820s"). Each still now sits on its own item, a clip plays in its own slot through its
    # first frame (the still renderer plays <still>.anim.mp4), and a clip that failed shows its first frame.
    "cinema.py": [
        # Every chapter card opened with a film-burn light leak, so a learned documentary whose references cut to black
        # at chapters flashed orange there. A learned look (autostyle v2 fx) gets it only when its references use a
        # leak_burst transition; the built-in recipes keep it.
        ("def _leak_in(mp4: Path) -> None:\n",
         "def _wants_leak(look) -> bool:\n"
         "    fx = look.get(\"fx\") if isinstance(look, dict) else None\n"
         "    if not isinstance(fx, dict):\n"
         "        return True\n"
         "    return any(isinstance(t, dict) and t.get(\"id\") == \"leak_burst\" for t in fx.get(\"transitions\") or [])\n"
         "\n\n"
         "def _leak_in(mp4: Path) -> None:\n"),
        ("                docgfx.render(jobs, workers=max(1, min(2, workers)), look=look)\n"
         "                for _sc, mp4 in jobs:\n                    _leak_in(Path(mp4))\n",
         "                docgfx.render(jobs, workers=max(1, min(2, workers)), look=look)\n"
         "                for _sc, mp4 in jobs:\n                    if _wants_leak(look):\n"
         "                        _leak_in(Path(mp4))\n"),
        ("                        for _sc, mp4 in jobs:\n                            _leak_in(Path(mp4))\n",
         "                        for _sc, mp4 in jobs:\n                            if _wants_leak(look):\n"
         "                                _leak_in(Path(mp4))\n"),
    ],
    # The clean 3D reconstruction channels render an all-white matte world (watched: monochrome white models and
    # mannequins, brightness 185 of 255, almost no colour); the engine's looks rendered the same story dark and
    # colourful. WHITE STUDIO is that look, for the renderer (presets.js) and for the director, reviewer and QA
    # (looks.json, which also lists the look so a style may ask for it).
    "threed/engine/core/presets.js": [
        ("  documentary: null,\n", PRESET_WHITE_STUDIO + PRESET_STUDIO_COLOR),
        # the designed look preset comes with the designed-cast kit (lib/designed/preset.js)
        ("import { V3_PRESETS } from './presets_v3.js';",
         "import { V3_PRESETS } from './presets_v3.js';\n"
         "import { DESIGNED_PRESETS } from '../lib/designed/preset.js';   // ChannelRecipe: designed-3D channels (lib/designed)"),
        ("Object.assign(PRESETS, V3_PRESETS);", "Object.assign(PRESETS, V3_PRESETS, DESIGNED_PRESETS);"),
        ("  if (P.ink) { g.outline = JSON.parse(JSON.stringify(P.ink)); g.labelMask = true; }   // grade.outline (grade.ink is the mono ink colour); never across a label\n",
         "  if (P.ink) { g.outline = JSON.parse(JSON.stringify(P.ink)); g.labelMask = true; }   // grade.outline (grade.ink is the mono ink colour); never across a label\n"
         "  if (P.labelKeep) { g.labelKeep = true; g.labelMask = true; }   // the labels keep their own colours through a mono grade (white studio)\n"),
    ],
    # a look may keep its labels out of the mono grade (labelKeep), so dark ink stays dark on a white world
    "threed/engine/core/post.js": [
        ("uWhite: { value: 0 }, uWhiteCol: { value: new THREE.Vector3(1, 1, 1) }, uTime: { value: 0 },",
         "uWhite: { value: 0 }, uWhiteCol: { value: new THREE.Vector3(1, 1, 1) }, uTime: { value: 0 }, uLabelKeep: { value: 0 },"),
        ("uniform float uWhite; uniform vec3 uWhiteCol; uniform float uTime;",
         "uniform float uWhite; uniform vec3 uWhiteCol; uniform float uTime; uniform float uLabelKeep;"),
        ("c = mix(c, ${film ? 'fMonoCol(l)' : 'mix(uInk, uPaper, smoothstep(0.0, 1.0, l))'}, ${film ? 'fMono' : 'uMono'});",
         "c = mix(c, ${film ? 'fMonoCol(l)' : 'mix(uInk, uPaper, smoothstep(0.0, 1.0, l))'}, ${film ? 'fMono' : 'uMono'} * (1.0 - labelMask(vUv) * uLabelKeep));"),
        ("u.uWhite.value = g.white ?? 0; u.uWhiteCol.value.set(...(g.whiteCol ?? [1, 1, 1]));",
         "u.uWhite.value = g.white ?? 0; u.uWhiteCol.value.set(...(g.whiteCol ?? [1, 1, 1])); u.uLabelKeep.value = g.labelKeep ? 1 : 0;"),
    ],
    # a look may set every label in dark ink on a pale tag (labelInk: 'dark'): no metering, no scrim
    "threed/engine/core/main.js": [
        ("    const shot = (mode) => { it.res.measureMode(mode); post.frame(1, (j) => renderSub(t, j, 1), Math.round(t * FPS), gradeAt(t), t); const r = region(it); it.res.measureMode(null); return r; };\n",
         "    const shot = (mode) => { it.res.measureMode(mode); post.frame(1, (j) => renderSub(t, j, 1), Math.round(t * FPS), gradeAt(t), t); const r = region(it); it.res.measureMode(null); return r; };\n"
         "    if (cur.P?.labelInk === 'dark') {   // a look whose labels are dark ink on a pale tag (white studio)\n"
         "      it.res.setScrim(0.62); it.res.setInk(true);\n"
         "      out.changes.push(`labels:${it.index} \"${String(q.text || '').slice(0, 40)}\": dark ink on a pale plate (the look's labels)`);\n"
         "      continue;\n    }\n"),
    ],
    "threed/director/looks.json": [
        ('{\n "documentary": {\n', '{\n' + LOOK_WHITE_STUDIO + LOOK_STUDIO_COLOR + ' "documentary": {\n'),
        (' "documentary": {\n', LOOK_DESIGNED + ' "documentary": {\n'),
    ],
    # the designed-cast kit (lib/designed): its library is loaded with the others, and a scene's look may carry the
    # channel's archetype (look.designed) without a warning
    "threed/engine/core/registry.js": [
        ("['v3', '../lib/v3/index.js']];", "['v3', '../lib/v3/index.js'], ['designed', '../lib/designed/index.js']];"),
    ],
    "threed/engine/core/spec.js": [
        ("'preset', 'dark', 'pitch_dark', 'film', 'keep_hue'],", "'preset', 'dark', 'pitch_dark', 'film', 'keep_hue', 'designed'],"),
    ],
    "make_video.py": [
        # A dark chapter card or learned graphic in a slot longer than itself held its faded-out last frame: seconds of
        # black while the narration went on (the brightness test misses dark cards). Like a documentary graphic, each now
        # holds its last full picture before the fade.
        ('    if tail > 0.35 and src_dur > 1.6 and (src.name.startswith("doc_") or _fades_out(src, src_dur)):\n',
         '    if tail > 0.35 and src_dur > 1.6 and (src.name.startswith(("doc_", "chapter_", "kit_")) or _fades_out(src, src_dur)):\n'),
        ("def _build_story_plan(job: Path, total: float, photos: list) -> list:\n",
         "def _story_media(job: Path, items: list, photos: list) -> list:\n"
         "    \"\"\"The picture for each storyboard item, in item order (ChannelRecipe fix): clips first, then stills by\n"
         "    their file number; the stills as given when they don't follow the scene_NN names.\"\"\"\n"
         "    job = Path(job)\n"
         "    video_n = len(list(job.glob(\"clip_src/vsrc_*.jpg\")))\n"
         "    scenes = {}\n"
         "    for p in photos:\n"
         "        m = re.search(r\"scene_(\\d+)\\.jpg$\", str(p))\n"
         "        if m:\n"
         "            scenes[int(m.group(1))] = str(p)\n"
         "    out = []\n"
         "    for k in range(len(items)):\n"
         "        if k < video_n:\n"
         "            still = job / \"clip_src\" / f\"vsrc_{k:02d}.jpg\"\n"
         "            clip = job / \"clips\" / f\"clip_{k:02d}.mp4\"\n"
         "            anim = still.with_suffix(\".anim.mp4\")\n"
         "            if clip.exists() and clip.stat().st_size > 20_000 and not anim.exists():\n"
         "                try:\n"
         "                    shutil.copyfile(clip, anim)\n"
         "                except OSError:\n"
         "                    pass\n"
         "            out.append(str(still) if still.exists() else None)\n"
         "        else:\n"
         "            out.append(scenes.get(k - video_n))\n"
         "    if sum(1 for x in out if x) < max(1, len([p for p in photos if p]) // 2):\n"
         "        return list(photos)\n"
         "    return out\n\n\n"
         "def _build_story_plan(job: Path, total: float, photos: list) -> list:\n"),
        ("    pairs = [(it if isinstance(it, dict) else {}, p) for it, p in zip(items, photos) if p]\n",
         "    pairs = [(it if isinstance(it, dict) else {}, p) for it, p in zip(items, _story_media(job, items, photos)) if p]\n"),
        ("            by_i[int(m.group(1))] = str(p)\n    if len(by_i) < max(3, len(photos) // 2):\n        return []\n"
         "    idx = sorted(by_i)\n",
         "            by_i[int(m.group(1))] = str(p)\n"
         "    # ChannelRecipe fix: a story's first items are its clips, so still scene_k is item video_n + k\n"
         "    _vn = len(list(Path(job).glob(\"clip_src/vsrc_*.jpg\")))\n"
         "    by_i = {i + _vn: p for i, p in by_i.items() if i + _vn < len(items)}\n"
         "    if len(by_i) < max(3, len(photos) // 2):\n        return []\n"
         "    idx = sorted(by_i)\n"),
        # A fast story (a picture every 2.6 s) has no slack against a fixed 2.5 s shortest hold: one short sentence
        # pushed every later picture, and the timeline set into a 2.5 s grid that drifted up to 4 s off the words.
        # The shortest hold scales with the story's own pace (1.2 to 2.5 s); slower stories keep 2.5 s.
        ("    beat = 2.5                              # shortest a picture may hold\n",
         "    beat = min(2.5, max(1.2, 0.55 * total / max(1, n)))   # shortest hold, from the story's own pace\n"),
        # Channels that hold their pictures still (stick-figure stories hold 9 in 10) could only push in or
        # alternate. look.zoom "still" holds every still; "mix:<share>[:alternate]" holds that share, spread evenly.
        ('    p = f"min(on/{max(1, frames - 1)}\\\\,1)"\n'
         '    if (mode or "in").lower() != "alternate":\n',
         '    p = f"min(on/{max(1, frames - 1)}\\\\,1)"\n'
         '    mode = (mode or "in").lower()\n'
         '    if mode == "still":\n'
         '        return "(1)"\n'
         '    if mode.startswith("mix:"):\n'
         '        parts = mode.split(":")\n'
         '        try:\n'
         '            share = max(0.0, min(1.0, float(parts[1])))\n'
         '        except (IndexError, ValueError):\n'
         '            share = 0.0\n'
         '        if (idx * 0.6180339887) % 1.0 < share:\n'
         '            return "(1)"\n'
         '        mode = "alternate" if "alternate" in parts[2:] else "in"\n'
         '    if mode != "alternate":\n'),
    ],
    # The background remover (removebg.py) works on kie.ai alone, but cut-out movement, photo spotlights and
    # objects were switched off without a WaveSpeed key, so drawn styles got no figure moves on a Kie-only setup.
    "photofx.py": [
        # a YouTube video's thumbnail is another creator's designed picture (their face, logo and title text), never
        # a plain photo of the subject: true crime pictures came back as other channels' thumbnails
        ('"pngwing.", "kindpng.", "pngitem.", "seekpng.", "clipart", "wallpaperaccess.", "wallpapercave.")\n',
         '"pngwing.", "kindpng.", "pngitem.", "seekpng.", "clipart", "wallpaperaccess.", "wallpapercave.",\n'
         '               "ytimg.com", "youtube.com", "youtu.be")\n'),
        ('              "posterazzi.", "fineartamerica.", "pixels.com", "displate.")\n',
         '              "posterazzi.", "fineartamerica.", "pixels.com", "displate.", "ytimg.com", "youtube.com", "youtu.be")\n'),
        ('    if not cfg["depth"] or every <= 0 or not os.environ.get("WAVESPEED_API_KEY"):\n',
         '    if not cfg["depth"] or every <= 0 or not (os.environ.get("WAVESPEED_API_KEY") or os.environ.get("KIE_API_KEY")):\n'),
        ('    cuts = bool((os.environ.get("WAVESPEED_API_KEY") or "").strip())\n',
         '    cuts = bool((os.environ.get("WAVESPEED_API_KEY") or "").strip() or (os.environ.get("KIE_API_KEY") or "").strip())\n'),
    ],
    # A footage search YouTube refused ("Sign in to confirm you're not a bot") was not recorded, so the video's
    # "no real footage" warning could not say why or what fixes it; the refused search now notes yt-dlp's error.
    "youtube.py": [
        # footage watching: a stalled upload to kie's file host fails in minutes, not fifteen, and is tried once more
        ('def _kie_upload(path: Path) -> str:\n    with open(path, "rb") as fh:\n        r = requests.post("https://kieai.redpandaai.co/api/file-stream-upload",\n                          headers={"Authorization": f"Bearer {_env(\'KIE_API_KEY\')}"},\n                          data={"uploadPath": "frontier", "fileName": path.name},\n                          files={"file": (path.name, fh, "video/mp4")}, timeout=900)\n    r.raise_for_status()\n',
         'def _watch_copy(path: Path) -> Path:\n    """What goes up to kie\'s file host: the low copy, re-encoded light when it is big (6 frames a second, mono\n    audio, about a tenth of the size). A 20-minute 360p copy is 50-75 MB, and three at a time timed out on a home\n    connection ("Connection aborted ... write operation timed out") video after video. The watcher samples about\n    one frame a second, so the shot log is the same."""\n    if path.stat().st_size <= 8_000_000:\n        return path\n    out = path.with_name(path.stem + "_w.mp4")\n    if out.exists() and out.stat().st_size > 10000:\n        return out\n    try:\n        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-vf", "scale=-2:360", "-r", "6",\n                        "-c:v", "libx264", "-crf", "32", "-preset", "veryfast", "-c:a", "aac", "-b:a", "32k", "-ac", "1",\n                        str(out)], capture_output=True, text=True, timeout=600)\n    except (OSError, subprocess.TimeoutExpired):\n        return path\n    return out if out.exists() and out.stat().st_size > 10000 else path\n\n\n'
         'def _kie_upload(path: Path) -> str:\n    # a light copy uploads in seconds. A dropped connection gets one more try; an upload that stalls (two minutes)\n    # goes to the next watcher at once, and _kie_log counts it towards the breaker.\n    for attempt in (1, 2):\n        started = time.monotonic()\n        try:\n            with open(path, "rb") as fh:\n                r = requests.post("https://kieai.redpandaai.co/api/file-stream-upload",\n                                  headers={"Authorization": f"Bearer {_env(\'KIE_API_KEY\')}"},\n                                  data={"uploadPath": "frontier", "fileName": path.name},\n                                  files={"file": (path.name, fh, "video/mp4")}, timeout=(15, 120))\n            break\n        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):\n            if attempt == 2 or time.monotonic() - started > 30:\n                raise\n    r.raise_for_status()\n'),
        # Studio sets YT_FORCE_IPV4 when this computer reaches YouTube over IPv4, which YouTube refuses far less often
        ("    if _num_env(\"YT_SLEEP\") > 0:\n",
         "    if _env(\"YT_FORCE_IPV4\") not in (\"\", \"0\", \"no\", \"off\", \"false\"):\n"
         "        cmd += [\"--force-ipv4\"]\n    if _num_env(\"YT_SLEEP\") > 0:\n"),
        # yt-dlp runs as a process of its own: Studio's copy goes through cr_ytdlp so it trusts this computer's
        # certificates too (an antivirus that scans secure connections failed every YouTube search and download)
        ("        except ImportError:\n            return []\n    if not shutil.which(\"deno\"):\n",
         "        except ImportError:\n            return []\n"
         "    try:\n        import cr_trust\n        cmd = cr_trust.ytdlp_command(cmd)\n"
         "    except Exception:\n        pass\n"
         "    if not shutil.which(\"deno\"):\n"),
        ("        except subprocess.TimeoutExpired:\n            return []\n        for line in p.stdout.splitlines():\n",
         "        except subprocess.TimeoutExpired:\n            return []\n        if p.returncode:\n"
         "            _note_err(p.stderr)\n        for line in p.stdout.splitlines():\n"),
        # kie's video watcher down for a while: three failed videos in a row and the rest of the search skips it
        ('def _kie_log(vid: str, prompt: str, work: Path, span: tuple = None) -> str:\n    # kie\'s gateway answers a busy minute with {"code": 500, "msg": "Network error, please try again later."}.\n    # The upload is the slow part, so it is done once and only the watching is tried again.\n    url = _kie_upload(_low_copy(vid, work, span))\n    last = None\n    for wait in (0, 20, 60):\n        if wait:\n            time.sleep(wait)\n        try:\n            return _chat_log("https://api.kie.ai/gemini-2.5-flash/v1/chat/completions", _env("KIE_API_KEY"), "",\n                             {"type": "image_url", "image_url": {"url": url}}, prompt)\n        except RuntimeError as e:                               # noqa: PERF203 - three tries, then the next model\n            last = e\n            if "HTTP 4" in str(e):\n                break\n    raise last\n',
         '# When kie\'s watcher is down ("internal error" video after video), each video still cost an upload and three tries\n# over 80 s: one footage search took 58 minutes to find nothing. After three videos in a row the rest skip it.\n_KIE_DOWN = {"fails": 0}\n\n\ndef _kie_log(vid: str, prompt: str, work: Path, span: tuple = None) -> str:\n    # kie\'s gateway answers a busy minute with {"code": 500, "msg": "Network error, please try again later."}.\n    # The upload is the slow part, so it is done once and only the watching is tried again.\n    if _KIE_DOWN["fails"] >= 3:\n        raise RuntimeError("kie\'s video watcher failed three videos in a row - this search goes on without it")\n    low = _low_copy(vid, work, span)\n    try:\n        url = _kie_upload(_watch_copy(low))\n    except Exception:\n        _KIE_DOWN["fails"] += 1                             # its file host stalled or dropped the upload\n        raise\n    last = None\n    for wait in (0, 20, 60):\n        if wait:\n            time.sleep(wait)\n        try:\n            text = _chat_log("https://api.kie.ai/gemini-2.5-flash/v1/chat/completions", _env("KIE_API_KEY"), "",\n                             {"type": "image_url", "image_url": {"url": url}}, prompt)\n            _KIE_DOWN["fails"] = 0\n            return text\n        except RuntimeError as e:                               # noqa: PERF203 - three tries, then the next model\n            last = e\n            if "HTTP 4" in str(e):\n                break\n    if "HTTP 4" not in str(last):\n        _KIE_DOWN["fails"] += 1\n    raise last\n'),
    ],
    # A learned style's own kit is named by its manifest path; Studio saves it relative to the Studio folder (so a
    # moved Studio, and a ready-made recipe shipped under assets/styles/cr/, keep their graphics).
    "kits.py": [
        # an overlay graphic (a name card, a place tag) sits on the picture of its seconds; when every picture already
        # grounded a scene it went on black. The nearest picture serves again.
        ("            pick = (timed or still or [None])[0]\n",
         "            pick = (timed or still or [None])[0]\n"
         "            if pick is None and pics:            # every picture grounds a scene already: the nearest again, never black\n"
         "                pick = min(pics, key=lambda p: abs(p[0] - at) if p[0] >= 0 else 1e9)\n"),
        ('        register_gen_kit(look["genkit"]["manifest"])\n',
         '        register_gen_kit(HERE / str(look["genkit"]["manifest"]))\n'),
    ],
    "history_kit.py": [
        # "\," in a plain f-string is an invalid escape: Python 3.12+ printed a SyntaxWarning twice in the launcher on
        # a first start (the owner's Windows test, 2026-10-26), which read like a crash. "\\," is the same text for
        # FFmpeg's filter (a backslash and a comma), with no warning.
        ("""lut = lambda k: f"clip(255*({g['lo']}+{g['hi'] - g['lo']:.4f}*pow(val/255\\,{g['gamma']}))*{k}\\,0\\,255)\"""",
         """lut = lambda k: f"clip(255*({g['lo']}+{g['hi'] - g['lo']:.4f}*pow(val/255\\\\,{g['gamma']}))*{k}\\\\,0\\\\,255)\""""),
    ],
    "quest_hud.py": [
        ("def _wait_machine(log=print, min_level: int = 40, min_free_gb: float = 20.0) -> None:",
         "def _wait_machine(log=print, min_level: int = 20, min_free_gb: float = 3.0) -> None:"),
        ("    lvl = 45 if light else 40\n", "    lvl = 25 if light else 20\n"),
        # The HUD registers Montserrat-Book.ttf, which no release ever shipped (it isn't in the engine source either):
        # reading it failed, so every QUEST video came out without its game HUD ("QUEST: HUD not laid —
        # FileNotFoundError ... Montserrat-Book.ttf", sweep 2026-10-26). A face that isn't there is left out; the
        # browser draws that weight with the nearest Montserrat the release has.
        ("             for fam, f, d in FONT_FACES]\n",
         "             for fam, f, d in FONT_FACES if (FONTS / f).is_file()]   # a face the release lacks: the nearest weight draws\n"),
    ],
}


def patch_engine(relative: str, contents: bytes) -> bytes:
    if relative == "threed/catalog.json":
        return merge_catalog(contents)
    patches = ENGINE_PATCHES.get(relative)
    if not patches:
        return contents
    text = contents.decode("utf-8")
    for old, new in patches:
        if text.count(old) != 1:
            raise ValueError("Engine patch no longer matches " + relative + ". Review it against the new source.")
        text = text.replace(old, new)
    return text.encode("utf-8")


def compose_claude_instructions(original: bytes, overrides: bytes) -> bytes:
    """Restore private interactive creative context without publishing its text."""
    introduction = (
        "# ChannelRecipe studio instructions\n\n"
        "If the customer asks you to install, set up or start ChannelRecipe Studio, go straight to "
        "\"If they ask you to install or set up Studio\" in the operational overrides at the end "
        "of this file and follow those steps.\n\n"
        "The private creative guide follows. Use it for creative direction, "
        "style customization, specialist modules, visual verification and editing.\n\n"
        "The ChannelRecipe operational overrides at the end govern setup, account/key handling, "
        "billing, launch commands, MCP authorization and recovery whenever the older guide differs. "
        "Use local setup forms for keys; never collect or reveal them in chat. Launch production "
        "through CR's maintained tools and project/stage checks. Do not follow older standalone "
        "generation commands or API fallback instructions that bypass those checks.\n\n"
        "## ChannelRecipe creative guide\n\n"
    ).encode("utf-8")
    return introduction + original + b"\n\n---\n\n# ChannelRecipe operational overrides\n\n" + overrides


def verify_private(client, bucket, key, endpoint, cdn=""):
    acl = client.get_object_acl(Bucket=bucket, Key=key)
    if any(g.get("Grantee", {}).get("URI", "").endswith(("/AllUsers", "/AuthenticatedUsers")) for g in acl.get("Grants", [])):
        raise RuntimeError("Release ACL must be private.")
    host = urlsplit(endpoint).netloc
    urls = ["https://" + bucket + "." + host + "/" + quote(key)]
    if cdn:
        urls.append(cdn.rstrip("/") + "/" + quote(key))
    # Check anonymously too: bucket policy/CDN can override an object ACL.
    for url in urls:
        response = requests.head(url, timeout=20, allow_redirects=True)
        if response.status_code not in (403, 404):
            raise RuntimeError("Anonymous access was not denied. Do not publish this bundle.")


def previous_release(folder: Path) -> Path | None:
    """The newest earlier release ZIP built on this machine, if any."""
    current = int(VERSION.rsplit(".", 1)[1])
    found = []
    for path in folder.glob("channelrecipe-5.3-admin.*.zip"):
        build_number = path.name[len("channelrecipe-5.3-admin."):-len(".zip")]
        if build_number.isdigit() and int(build_number) < current:
            found.append((int(build_number), path))
    return max(found)[1] if found else None


def retired_files(previous: Path | None, shipped: set) -> dict:
    """Files earlier releases shipped that this one doesn't, with every hash they shipped under.

    Installed Studios remove these on update/start only while the file still
    has one of those hashes, so creator edits are never deleted.
    """
    if previous is None:
        return {}
    with zipfile.ZipFile(previous) as archive:
        try:
            listing = json.loads(archive.read("ChannelRecipe studio/cr-files.json"))
        except KeyError:
            return {}
    retired = {}
    for path, hashes in (listing.get("retired") or {}).items():
        retired.setdefault(path, set()).update(hashes)
    for path, digest in (listing.get("files") or {}).items():
        retired.setdefault(path, set()).add(digest)
    return {path: sorted(hashes) for path, hashes in sorted(retired.items()) if path not in shipped}


def build(source: Path, target: Path, previous: Path | None = None, with_shorts: bool = True):
    if any(not path.is_file() for path in PRIVATE_WORKFLOWS.values()):
        raise ValueError("The private reference animation workflow resources are required for this admin build.")
    with source.open("rb") as f:
        actual = hashlib.file_digest(f, "sha256").hexdigest()
    if actual != SOURCE_SHA256:
        raise ValueError("Source changed. Review and pin its new manifest before building a release.")
    with zipfile.ZipFile(source) as original:
        try:
            private_guide = original.read("Frontier/CLAUDE.md")
        except KeyError:
            raise ValueError("The pinned source must include its original Claude creative guide.") from None
    operational_overrides = (ROOT / "local_runtime/CLAUDE.md").read_bytes()
    # Validate the complete resource inventory before replacing an existing ZIP.
    resources = list(runtime_resources(ROOT))
    # the Shorts recipes and their pipelines ship with every release since 63 (their asset pack downloads on first use);
    # --without-shorts builds a release without them
    shorts = with_shorts
    recipes = [r for r in private_recipe_resources(ROOT) if shorts or not r[1].startswith("styles/cr-shorts-")]
    preview_resources = (private_preview_resources(ROOT) + private_sound_resources(ROOT)
                         + recipes + (private_shorts_resources(ROOT) if shorts else []) + private_threed_resources(ROOT)
                         + private_animated2d_resources(ROOT) + private_animated3d_resources(ROOT))
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    transformed_files = []
    logo = ROOT / "scripts/assets/channelrecipe.png"
    brand_png = logo.read_bytes() if logo.is_file() and not logo.is_symlink() else None
    # A rejected unknown/binary branding hit must not destroy a prior download.
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".cr-build-", suffix=".zip", delete=False) as temporary:
        staged = Path(temporary.name)
    try:
        with zipfile.ZipFile(source) as original, zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as result:
            written = set()
            shipped = {}

            def write_resource(relative, contents, *, executable=False):
                branded, data, record = studio_branding.transform_resource(relative, contents, brand_png=brand_png)
                name = "ChannelRecipe studio/" + branded
                if name in written:
                    raise ValueError("Duplicate branded studio resource: " + name)
                written.add(name)
                shipped[branded] = hashlib.sha256(data).hexdigest()
                if record:
                    transformed_files.append(record)
                info = zipfile.ZipInfo(name)
                info.external_attr = (0o100755 if executable else 0o100644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                result.writestr(info, data)

            for info in original.infolist():
                parts = PurePosixPath(info.filename).parts
                if not parts or parts[0] != "Frontier" or ".." in parts or info.is_dir():
                    continue
                rel = PurePosixPath(*parts[1:])
                if not rel.parts:
                    continue
                if rel.parts[0] in {"samples", ".frontier", "output", "presets", "__MACOSX"}:
                    continue
                if rel.name in {".env", ".env.example", ".DS_Store"} or rel.suffix == ".pyc":
                    continue
                if str(rel) in RETIRED_SAMPLES:
                    continue
                # Preserve the source inventory and source hash; record every
                # owned branding transformation instead of claiming byte parity.
                relative = "CREATIVE_GUIDE.md" if str(rel) == "CLAUDE.md" else str(rel)
                write_resource(relative, patch_engine(str(rel), original.read(info)),
                               executable=bool(info.external_attr >> 16 & 0o111))
                count += 1
            for path, relative in resources:
                contents = compose_claude_instructions(private_guide, operational_overrides) if path.name == "CLAUDE.md" else path.read_bytes()
                write_resource(relative, contents, executable=path.suffix == ".command")
            write_resource("cr-providers.json", (ROOT / "webapp/frontier/providers.json").read_bytes())
            for relative, contents in claude_code_files().items():
                write_resource(relative, contents)
            for name, path in PRIVATE_WORKFLOWS.items():
                write_resource(name, path.read_bytes())
            for path, relative in preview_resources:
                write_resource(relative, path.read_bytes())
            write_resource("cr-release.json", json.dumps({
                "version": VERSION, "access": "subscriber", "source_sha256": SOURCE_SHA256,
                "public_release_allowed": True, "engine_files": count,
                "workflow_version": "channelrecipe-5.3-claudecode.1", "preferred_brain": "claudecode",
                "branding_version": studio_branding.BRANDING_VERSION,
                "note": "ChannelRecipe Studio for Creator, Pro and Max subscribers."}, indent=2).encode())
            write_resource("cr-branding.json", json.dumps({
                "schema": 1, "branding_version": studio_branding.BRANDING_VERSION,
                "source_archive_sha256": SOURCE_SHA256, "source_engine_files": count,
                "transformed_files": transformed_files,
            }, indent=2).encode())
            # Every shipped file's hash, so the in-place updater can tell which
            # installed files a creator edited and keep them, plus the files
            # earlier releases shipped that should now be removed.
            retired = retired_files(previous, set(shipped) | {"cr-files.json"})
            write_resource("cr-files.json", json.dumps({
                "schema": 1, "version": VERSION, "files": dict(sorted(shipped.items())),
                "retired": retired,
            }, indent=1).encode())
        with zipfile.ZipFile(staged) as archive:
            branding_report = studio_branding.assert_clean_archive(archive)
        staged.replace(target)
    finally:
        staged.unlink(missing_ok=True)
    with target.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    return {"version": VERSION, "bytes": target.stat().st_size, "sha256": digest,
            "engine_files": count, "transformed_files": len(transformed_files), "retired_files": len(retired),
            "branding": branding_report}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "data/frontier" / ("channelrecipe-" + VERSION + ".zip"))
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--without-shorts", action="store_true", help="leave the Shorts recipes and pipelines out")
    parser.add_argument("--previous", type=Path, help="Earlier release ZIP whose dropped files to retire "
                        "(default: newest earlier release in the output folder)")
    args = parser.parse_args()
    previous = args.previous or previous_release(args.output.parent)
    manifest = build(args.source, args.output, previous, with_shorts=not args.without_shorts)
    (args.output.parent / "manifest.json").write_text(json.dumps(manifest, indent=2))
    if args.upload:
        from webapp import storage
        from webapp.frontier_studio import RELEASE_KEY
        import config
        client = storage._get_client()
        _, _, bucket, _, endpoint = storage._spaces_creds()
        canary = RELEASE_KEY + ".privacy-check"
        try:
            client.put_object(Bucket=bucket, Key=canary, Body=b"private access check", ACL="private")
            verify_private(client, bucket, canary, endpoint, config.SPACES_CDN_ENDPOINT)
        finally:
            client.delete_object(Bucket=bucket, Key=canary)
        try:
            from boto3.s3.transfer import TransferConfig
            # Few parallel parts: large single bursts were dropped by the storage endpoint.
            transfer = TransferConfig(multipart_threshold=16 * 1024 * 1024, multipart_chunksize=16 * 1024 * 1024,
                                      max_concurrency=2)
            client.upload_file(str(args.output), bucket, RELEASE_KEY, Config=transfer,
                               ExtraArgs={"ACL": "private", "ContentType": "application/zip",
                                          "CacheControl": "private, no-store", "Metadata": {"sha256": manifest["sha256"]}})
            verify_private(client, bucket, RELEASE_KEY, endpoint, config.SPACES_CDN_ENDPOINT)
        except Exception:
            client.delete_object(Bucket=bucket, Key=RELEASE_KEY)
            raise
        print("Private release uploaded; object ACL and anonymous access checked.")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
