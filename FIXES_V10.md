# Frontier smoke FIXES_V10 — mean shot healed + Playwright GFX + style grade

## Goal
v9 soft timing (**mean 4.33**, cuts/min 12.5) while keeping kinetic opener ≤10s.
Push bed + MG toward 9 via Whop lightleak/dust/grade and **Playwright kit** collage.

## What landed

### Timing (body beds, not hook)
- `build_body_still_timeline`: long Atlas beds after kinetic opener.
- `heal_bed_durations`: absorb post-GFX crumbs without touching opener.
- GFX at **bed boundaries**, **2×4.5s** cards (ratio ~21%) instead of 3 choppy 3.4s.
- Opener stays **8.0s** (Kevis 6–10).

### Motion graphics
- New `core/frontier_playwright_gfx.py` wrapping `frontier-gfx-kit/motion.py`.
- Kit `_frames_to_mp4` uses `FFMPEG_BIN` / ffmpeg-full (homebrew ffmpeg SIGABRT).
- Assets layout: `frontier-gfx-kit/assets/{cutouts,fonts,textures}` → symlinks.
- v10 smoke: **Playwright 2/2** (collage + scatter). Pillow remains fallback.

### Visual bed
- Style-driven grade from `jung.json` look (cooler, less yellow-green crush).
- Slower Ken Burns (`zoom_amount=0.18`), dust **2.3**, vignette **0.78**.
- Circular lightleak chapter commas kept; Whisper yellow karaoke kept; never-overlay.

### Multi-style
- Grade / first_cut / bed_target / kit root are params + JSON — not Jung-only floors.
- `heal` / `body_still` accept channel pacing overrides.

## Metrics (v10 vs v9)

| Metric | Want | v9 | **v10** | Pass? |
|---|---|---|---|---|
| First hard cut | 6–10s | 8.0 | **8.0** | ✅ |
| Mean shot | 5–9s | 4.33 | **7.21** | ✅ |
| Median shot | ~6 | 4.23 | **8.13** | ✅ |
| Cuts/min | 6–12 | 12.5 | **6.93** | ✅ |
| GFX ratio | 20–25% | 23.6 | **20.8** | ✅ |
| Hard+flash pic/min | 15–22 | ~22 | **~15.3** | ✅ low edge |
| GFX backend | — | pillow | **playwright** | ✅ |
| Karaoke on GFX | ZERO | ZERO | **ZERO** | ✅ |

## Honest 4-pillar

| pillar | v9 | **v10** | notes |
|--------|---:|------:|-------|
| **visual_bed** | 8.1 | **8.5** | Leak wash readable; dust/grade closer; KB slower. Soft: leak still more wash than crisp Whop disk; Atlas still ≠ Whop hero plate. |
| **motion_graphics** | 8.2 | **8.7** | Real Playwright collage/scatter + tape. Soft: cutout theme weak (clover/diamond vs forest script); item labels noisy. |
| **timing_pacing** | 8.0 | **8.9** | Mean/cuts/opener all in band. Soft: pic/min at low edge of 15–22. |
| **combined** | 8.1 | **8.7** | Clear lift. **Not ≥9.5** — no merge/deploy. |

## Paths
- Job: `output/frontier_20261002_122844/frontier_video_v10.mp4` (~287MB)
- Chat: `…/frontier_video_v10_chat.mp4` → box `/workspace/frontier_video_v10_chat.mp4`
- Frames: `frames_v10/` · box `frames_v10_{leak,bed_yellow,pw_collage,pw_scatter,firstcut}*`
- Plan/metrics: `motion_plan_v10.json`, `pacing_metrics_v10.json`
- Rebuild: `rebuild_v10.py`

## v11 blockers (→ ≥9.5 / pillars ≥9)
1. Thematic cutout picker (forest/shadow script → moon/tiger/sisyphus, not clover/rocket).
2. Stronger circular lightleak disk + Whop-closer Atlas stills / grade match.
3. Second style smoke (non-Jung JSON) before merge.
4. Optional: raise pic/min via caption-step density without chopping beds.
