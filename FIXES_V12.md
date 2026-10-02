# Frontier FIXES_V12 — Kevis lightleak kill + Whop-closer bed/MG

## Kevis HARD (2026-10-02 interrupt)
Circular **light-leak chapter flash is ANNOYING** — not clean.
- **Removed entirely** for this render (`flash_times=None`, `use_lightleak=False`, `leak_strength=0`).
- Assembler default: lightleak **OFF**. Soft path only if flashes requested: ≤2 frames @ ≤15% brightness (no bright disk).
- Style `jung.json`: `leak_strength=0`, `flash_mid_opener=false`, `use_lightleak=false`, `chapter_flash_mode=off`.
- Verified frames @1.75s / 8s: **0%** warm-hot orange disk pixels (vs prior `leak_1.8s.jpg` which had a clear amber burn).

## Also held
1. VO–GFX sync ≤0.3s (lead −0.1s on keyword)
2. Kinetic opener first hard cut **8.0s** (6–10s band)
3. Never-overlay GFX
4. Loudness **−16.0 LUFS** (TP −1.6)

## VO sync
| card | spoken | gfx_start | lead | late |
|---|---:|---:|---:|---:|
| the woods can learn a voice | 13.24 | 13.14 | −0.1 | 0 |
| your body wants to turn toward it | 26.84 | 26.74 | −0.1 | 0 |

`vo_gfx_sync` PASS (`n_cards=2`, `late=[]`).

## Pacing
- duration ~43.23s · first hard cut **8.0s** · mean shot **6.18s**
- hard cuts 6 · cuts/min ~8.32 · pic/min ~9.71 (no flash padding)
- GFX 2 cards / 9.0s (~21%) · flash_count **0**

## Honest scores (post lightleak-kill re-render)

| pillar | v11 | v12 (w/ leak) | **v12c (no leak)** | notes |
|--------|---:|---:|---:|-------|
| visual_bed | 8.6 | 9.0 | **9.0** | Cleaner (no annoying disk); grade cool olive-ish; **still ≠ Whop hallway/hands hero plates** (Atlas mountains/trees) |
| motion_graphics | 8.8 | 9.1 | **9.1** | Thematic PW collage (Sisyphus / X-eyes shadow / halftone eye); body scatter held |
| timing_pacing | 9.0 | 9.0 | **9.0** | opener 8s; mean 6.18; no flash-inflated pic/min |
| vo_gfx_sync | 9.2 | 9.2 | **9.2** | keyword −0.1s lock held |
| **combined** | 8.9 | 9.1 | **9.1** | All content pillars ≥9; combined **9.1 < 9.5** |

**NOT READY TO MERGE** — need combined ≥9.5.

## Blockers for ≥9.5 (v13)
1. **Atlas / prestige still content** closer to Whop hallway + dirt-hands symbolism (grade alone won’t close the gap).
2. Optional 3rd thematic GFX beat without breaking mean-shot / never-overlay.
3. Do **not** restore bright circular lightleak. Soft 2-frame dissolve only if pic/min genuinely needs a comma.
4. Keep VO sync, 6–10s opener, −16 LUFS.

## Artifacts
- `output/frontier_20261002_122844/frontier_video_v12.mp4` (full) + `frontier_video_v12_chat.mp4`
- Frames: `frames_v12/no_leak_*.jpg`, `frames_v12c/`, compare `leak_1.8s.jpg` (old) vs `no_leak_1.8s.jpg`
- Box: `/workspace/frontier_video_v12_chat.mp4` + frames
- Metrics: `pacing_metrics_v12.json` (`use_lightleak: false`, `flash_count: 0`)
- Code: `core/frontier_assembler.py` defaults; `frontier-gfx-kit/styles/jung.json`
