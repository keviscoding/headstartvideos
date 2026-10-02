# Frontier smoke FIXES_V4 — real Whop cutout collage GFX

## Goal
Lift `motion_graphics` toward ≥9 by replacing the cloud text-only PIL MVP with
**real Whop cutout PNGs** (40 files in `frontier-gfx-kit/cutouts/`).

## What landed
- Upgraded `core/frontier_motion_graphics.py`: collage / scatter / pillars with
  aged-paper sticker cards, cream ink, drop-in animation, Jung `collage_dark` /
  `noir` skins. Uses cutouts from `frontier-gfx-kit/` (not procedural placeholders).
- Fixed pipeline Step 5.5 `MotionSegment` kwargs (`zoom="hold"`, not invalid
  `zoom_direction` / `zoom_amount`).
- Added `motion_gfx` to `MotionSegment` Literal; assembler already scales
  pre-rendered GFX without double-grading.
- Short-smoke planner places **4 mid-timeline cards** inside the VO window
  (~7.5 / 15 / 23 / 31s on the Appalachian ~43s job).
- Reassembled `frontier_video_v4.mp4` with ffmpeg-full + dust + vignette + ASS.

## Timeline (v4)
| t (s) | template | note |
|------:|----------|------|
| 7.5–12.5 | collage | 3 cutout stickers + labels |
| 15.2–20.2 | scatter | ring of 8 stickers + title |
| 23.0–28.0 | pillars | 3 rising cutouts |
| 30.8–35.8 | collage | 2–3 stickers |

## Honest scores vs Jung Whop
| pillar | score | notes |
|--------|------:|-------|
| **visual_bed** | **6.5** | Stills + Pexels + warm Jung grade + dust similar to v3. Collage cards add scrapbook density when on screen. |
| **motion_graphics** | **7.5** | First real cutout collage lane on CR (cloud MVP was text-only ≈3). Visible stickers, tape, handwritten labels, scatter ring. Still short of Playwright motion.py (no dashed arrows, photonote, Caveat webfont, opener). |
| **combined** | **7.0** | Clear jump from v3 ~5.5. Path to ≥9.5: fuller Whop templates, Caveat, ~35% GFX ratio, photonote portraits, tighter still↔GFX rhythm. |

## Watch
- `output/frontier_20261002_122844/frontier_video_v4.mp4`
- `output/frontier_video_v4.mp4` (copy)
- Proof frames: `frames_v4/`, `frames_gfx/`
