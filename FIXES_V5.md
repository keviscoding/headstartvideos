# Frontier smoke FIXES_V5 — denser multi-template cutout GFX

## Goal
Lift toward ≥9.5 by densifying GFX to Jung’s **~35% timeline ratio**, porting more
Whop `motion.py` templates (scatter / pillars / opener / photonote), and switching
labels to **Caveat Bold cream** on `collage_dark`. Keep v3 warm grade + dust +
center kinetic captions. Do **not** inflate scores — videoReview will re-score.

## What landed (code)

### `core/frontier_motion_graphics.py`
- **PACING**: `graphic_dur_s` 5.0 → **3.2**, planner targets **`graphic_ratio=0.35`**.
  Short smokes now place **5 cards** with shorter holds (not 4×5s ≈46%).
- **Templates**: `collage` + improved **`scatter`** (title centered *inside* dashed
  scribble ring) + **`pillars`** (scaleY rise) + new Pillow **`opener`** (taped still
  + edge stickers + underline) + **`photonote`** (taped photo + cream annotation +
  arrow). No Chromium/Playwright required.
- **Fonts**: prefer `frontier-gfx-kit/fonts/Caveat-Bold.ttf` (Jung collage_dark),
  then Bradley Hand / Chalkboard. Cream ink `#ece5d6` unchanged.
- **plan_gfx_insertions**: accepts `still_paths` for opener/photonote portraits;
  evenly spaces cards across mid-body; logs GFX %.

### Kit
- Added `Caveat-Bold.ttf` (+ static twin) under `frontier-gfx-kit/fonts/`.

### Assemble (Mac job)
- `rebuild_v5.py` uses **v3 warm grade**
  (`sat=0.52`, `temp=4300`, red lift / blue cut) + dust_1080 + vignette + ASS.
- Output: `frontier_video_v5.mp4` (~43s).

## Timeline (v5) — 15.1s GFX / 43.3s = **35%**
| t (s) | template  | note |
|------:|-----------|------|
| 3.7–6.7 | opener | taped Atlas still + 6 edge stickers + Caveat title |
| 11.7–14.7 | collage | 3 cutout stickers + cream labels |
| 19.7–22.7 | scatter | 8 stickers + centered title in dashed ring |
| 27.7–30.8 | pillars | 3 rising cutouts + floor line |
| 35.7–38.8 | photonote | taped still + arrow + handwritten note |

## Honest scores vs Jung Whop (self — expect videoReview ±0.5)
| pillar | score | notes |
|--------|------:|-------|
| **visual_bed** | **6.5** | Same stills/Pexels/warm grade/dust as v3–v4. Bed not the leap this pass. |
| **motion_graphics** | **8.0** | Real density jump: 5 templates, 35% ratio, Caveat, centered scatter, opener+photonote. Still short of Playwright motion.py fluidity, thematic cutout picking, and caption-off-during-GFX (ASS still burns over cards). |
| **combined** | **7.3** | Up from v4 self **7.0**. **Not ≥9.5.** Path: mute/lower captions on GFX windows, thematic cutout selection, dashed-arrow polish, denser dust on beds, Whisper word timings. |

## Watch / proof
- `output/frontier_20261002_122844/frontier_video_v5.mp4`
- `output/frontier_video_v5.mp4` (flat copy)
- Frames: `frames_v5/`, previews `frames_v5_preview_{scatter,opener,pillars}.jpg`
- Plan: `motion_plan_v5.json` · Cards: `gfx_cards_v5/`

## Still gaps vs Jung
1. Kinetic ASS captions overlay full-screen GFX (clutters opener/scatter).
2. Cutouts are RNG-shuffled, not script-matched.
3. No Playwright CSS easing / handwriting stroke draw.
4. Photonote arrow is crude vs Whop SVG.
5. visual_bed still needs denser film texture / more Pexels cut energy.
