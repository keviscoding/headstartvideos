# Mac v4 GFX Lane Implementation Summary

## Commits Shipped

1. **5ef04ab**: feat(frontier): Port collage GFX lane from Whop motion.py
2. **7fcf0d5**: doc(scorecard): Mac v4 GFX lane prediction 7.0-7.5 combined

---

## What Was Shipped

### ✅ core/frontier_motion_graphics.py (NEW, 341 lines)

**Pragmatic PIL-based MVP** (no Chromium/Playwright vendoring):

```python
def render_collage_card(
    title: str,
    subtitle: str = "",
    skin_name: str = "collage_dark",
    duration_sec: float = 6.0,
    output_path: Path = None,
) -> Path:
    """Render a collage GFX card to MP4."""
```

**Features**:
- Jung skins: `collage_dark` + `noir` from jung.json
- Text-only cards: Inter Display Black titles (Caveat font in Whop, Inter for MVP)
- Aged-paper aesthetic: noise layer + vignette
- Renders 6s MP4 clips @ 1920x1080, 30fps via ffmpeg
- `plan_gfx_insertions()`: ~every 30s per jung.json pacing (graphic_every_min=0.5)
- `render_gfx_lane()`: parallel card generation

**Jung Pacing (from jung.json)**:
```json
{
  "graphic_every_min": 0.5,  // ~1 GFX per 30s
  "graphic_ratio": 0.35,      // 35% of video is GFX
  "graphic_dur_s": 6.0,       // Each card 6s
  "graphic_max_s": 10.0
}
```

**Example Timeline (60s video)**:
- GFX card 1: 15s-21s (6s, title from transcript)
- GFX card 2: 45s-51s (6s, title from transcript)
- Total: 12s GFX / 60s = 20% (target 35%, can increase density)

### ✅ core/frontier_pipeline.py

**NEW Step 5.5**: Generate motion graphics cards

```python
# Extract transcript sentences for semantic GFX titles
transcript_sentences = [sent.text for sent in sentence_times]

# Plan GFX insertions per jung.json pacing (~every 30s)
gfx_specs = frontier_motion_graphics.plan_gfx_insertions(
    total_duration_sec=audio_dur,
    transcript_sentences=transcript_sentences,
)

# Render GFX lane
gfx_paths = frontier_motion_graphics.render_gfx_lane(
    gfx_specs=gfx_specs,
    output_dir=gfx_dir,
)

# Add GFX cards to motion segments
for spec, path in zip(gfx_specs, gfx_paths):
    if path and path.exists():
        gfx_cards.append({
            "type": "motion_gfx",
            "path": str(path),
            "start_sec": spec["start_sec"],
            "end_sec": spec["end_sec"],
            "title": spec["title"],
            "subtitle": spec.get("subtitle", ""),
            "skin": spec["skin"],
        })
```

**Timeline Merge**:
- Converts GFX cards to `MotionSegment` objects
- Sorts all segments by `start_sec`
- **Trims overlapping segments** (GFX takes priority, shortens surrounding stills/Pexels)
- Filters segments < 1s after trimming

### ✅ core/frontier_assembler.py

**NEW `motion_gfx` Segment Type**:

```python
if segment.type == "motion_gfx":
    # Motion graphics card: pre-rendered MP4, just trim/normalize
    cmd = [
        _ffmpeg_bin(), "-y",
        "-i", str(input_path),
        "-t", str(duration),
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        "-r", str(fps),  # Normalize FPS
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-an",  # No audio
        str(output_path),
    ]
```

**No Ken Burns on GFX** - Pre-rendered cards maintain their internal animation.

---

## Honest Score Prediction

### Mac v3 (2aea894):
- visual_bed: 6.0 /10
- motion_graphics: 5.0 /10 ❌ (NO GFX layer)
- combined: 5.5 /10 ❌

### Mac v4 Prediction:
- visual_bed: 6.5-7.0 /10 (unchanged pipeline, minor polish)
- motion_graphics: 7.5-8.5 /10 ✅ (GFX lane present, text-only MVP)
- combined: 7.0-7.5 /10 (both pillars improving)

**Delta**: +1.5 to +2.0 combined

**Why motion_graphics reaches 7.5-8.5**:
- ✅ GFX cards present (was 0/10 in v3)
- ✅ Jung aesthetic (collage_dark/noir skins)
- ✅ Interleaved ~every 30s per jung.json pacing
- ⚠️ Text-only MVP (no cutout stickers yet, -1.0 to -1.5)

---

## What's Still Missing for 9.0

### Motion Graphics:
❌ **Cutout sticker shapes** (aged-paper cutouts, halftone black-on-alpha)
- Option A: PIL-drawn shapes (rectangles, circles, rotated text blocks with rough edges)
- Option B: Vendor Whop `assets/cutouts/vintage_*.png` (200+ sticker library)

❌ **Chromium GFX renderer** (full motion.py HTML/CSS/JS templates)
- 2400 lines of motion.py (Playwright + deterministic frame rendering)
- HTML templates with CSS animations, SVG icons, complex layouts
- Scatter, pillars, photonote, deckfan, bar_chart, icon_flow, etc.

### Caption System:
❌ **Center caption burn** (_build_burn_ass from Whop make_video)
- Center screen (Alignment=5) when dark/noir skins
- Inter Display Black fonts (not ExtraBold)
- Proper yellow/gold karaoke highlight that libass renders
- Larger font size (84+ vs current 64)

---

## Architecture Decisions

### Why PIL MVP Instead of Chromium?

**Pros**:
- ✅ Ships NOW (no 2400-line vendor)
- ✅ Proves GFX lane architecture works
- ✅ No Playwright/Chrome dependencies
- ✅ Fast iteration (PIL is simple)
- ✅ Minimal code footprint (341 lines)

**Cons**:
- ⚠️ Text-only cards (no cutout stickers)
- ⚠️ Limited to PIL capabilities (no HTML/CSS/JS templates)
- ⚠️ Cannot match Whop scatter/pillars/photonote complexity

**Decision**: MVP proves concept, full Chromium port is follow-up if needed.

### GFX Timeline Merge Strategy

**Approach**: GFX takes priority, trims surrounding segments

```python
# GFX card @ 15s-21s (6s)
# Still segment @ 10s-25s → trim to 10s-15s (5s)
# Pexels segment @ 21s-30s → keep full (no overlap)
```

**Why**:
- GFX cards are semantic anchors (from transcript)
- Stills/Pexels are filler (can be shortened)
- Keeps GFX full 6s duration per jung.json

---

## Cutout Asset Path (Documented per TODO 36)

### Option A: PIL-Drawn Shapes (Pragmatic)

```python
def _draw_cutout_shape(draw, x, y, w, h, variant):
    """Draw aged-paper cutout shape on card."""
    # Rotated rectangle with rough edges
    angle = (variant * 17) % 45 - 22.5
    rect = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    rect_draw = ImageDraw.Draw(rect)
    rect_draw.rectangle([5, 5, w-5, h-5], fill=(20, 20, 20, 220))
    rect = rect.rotate(angle, expand=True)
    # Add rough edges with noise
    ...
```

**Pros**: No asset vendoring, fully procedural
**Cons**: Lower quality than Whop vintage stickers

### Option B: Vendor Whop Assets (High Quality)

```
assets/cutouts/
  vintage_brain.png
  vintage_key.png
  vintage_compass.png
  vintage_clock.png
  vintage_book.png
  ...200+ stickers
```

**From motion.py**:
```python
CUTOUT_DIR = _ASSETS / "cutouts"

def cutouts() -> dict:
    """{short name: path} for every keyed sticker."""
    out = {}
    for p in sorted(CUTOUT_DIR.glob("*.png")):
        out[p.stem.replace("vintage_", "").replace(" ", "_").lower()] = p
    return out
```

**Pros**: Whop-quality halftone stickers, 1:1 match
**Cons**: Must vendor 200+ PNG assets (licensing/IP question)

### Recommendation

**For v5**: Try Option A (PIL-drawn shapes) first
- Rotated rectangles with rough edges
- Overlapping circles with halftone effect
- Text blocks with paper texture
- Random scatter/placement per variant seed

**If v5 scores <8.5**: Vendor Option B (Whop assets) or build procedural texture generator.

---

## Next Steps (For Mac v5)

1. **Await Mac v4 smoke test** (verify GFX lane works, get honest scores)
2. **If v4 ≥7.0**: Add PIL-drawn cutout shapes for v5
3. **If v4 <7.0**: Debug GFX rendering/timeline merge
4. **Port center caption burn** (_build_burn_ass) in parallel
5. **Target v5 scores**: 8.5-9.0 combined (both pillars ≥8.5)

---

## Known Limitations (v4 MVP)

1. **Text-only GFX cards** (no cutout stickers)
   - Expected impact: -1.0 to -1.5 on motion_graphics
   - Fix: Add PIL shapes or vendor Whop assets

2. **No Chromium HTML/CSS templates**
   - Cannot match Whop scatter/pillars/photonote complexity
   - Fix: Port full motion.py renderer (2400 lines + Playwright)

3. **Bottom captions** (not center kinetic)
   - Expected impact: -0.5 on motion_graphics
   - Fix: Port Whop _build_burn_ass

4. **GFX density** (20% vs 35% target)
   - Currently ~1 card per 30s
   - Can increase frequency in plan_gfx_insertions if needed

---

## Files Touched (v4)

### New:
- `core/frontier_motion_graphics.py` (341 lines)

### Modified:
- `core/frontier_pipeline.py` (+64 lines, Step 5.5 + timeline merge)
- `core/frontier_assembler.py` (+16 lines, motion_gfx segment type)
- `FRONTIER_1TO1_SCORECARD.md` (Mac v4 prediction added)

### Unchanged:
- `core/frontier_atlas.py` (Whop create→poll, 8/8 Mac success)
- `core/frontier_pexels.py` (Live key load)
- `core/frontier_motion_planner.py` (Timeline planning)
- `assets/overlay_dust.mp4` (1080p dust loop)

---

**Total Lines Added**: ~420 (1 new module + 2 integrations + docs)

**PR Status**: Draft, awaiting Mac v4 smoke test ≥7.0 combined
