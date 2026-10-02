# Frontier smoke FIXES_V12 — Whop-closer bed + thematic PW GFX (VO sync held)

## Goal
Close last ~0.6 combined: **visual_bed ≥9** and **MG ≥9** without regressing timing / vo_gfx_sync.

## Bed vs Jung Whop ref (side-by-side @2s / 8s)
- **Ref:** cool olive-cream hallway / dirt hands; fine grain; muted desat; white LT karaoke.
- **v12b ours:** cooler blue-grey Atlas ridges (desat↑), finer dust grit, slower KB (0.11), milder circular leak (0.42) so stills read; yellow karaoke held.
- Gap remaining: Atlas mountain beds ≠ Whop hallway/hand prestige plates (content, not just grade).

## MG
- Playwright collage/scatter with **thematic** stickers: Sisyphus/woods, shadow man + X eyes, halftone eye (no clover/rocket).
- Labels VO-matched (“the woods / learn a voice”, “the shadow / what you refuse”).
- Never-overlay + flashes skip GFX starts.

## VO sync (unchanged / held)
| card | keyword spoken | card start | lead |
|---|---:|---:|---:|
| woods learn | **13.24** | **13.14** | **−0.1s** |
| body wants | **26.84** | **26.74** | **−0.1s** |

`vo_gfx_sync` PASS. Opener **8.0s**. Mean **6.18s**. Cuts/min **8.32**. GFX **21%**.

## Honest scores

| pillar | v11 | **v12** | notes |
|--------|---:|------:|-------|
| visual_bed | 8.6 | **9.0** | Cool olive-ish grade + finer dust + milder leak; still ≠ Whop hero plates |
| motion_graphics | 8.8 | **9.1** | Thematic PW collage parity |
| timing_pacing | 9.0 | **9.0** | held |
| vo_gfx_sync | 9.2 | **9.2** | held |
| **combined** | 8.9 | **9.1** | All content pillars ≥9 except combined soft of 9.5 |

**Not READY TO MERGE** — combined **9.1 &lt; 9.5**. Need Whop-closer Atlas/prestige beds (or style-from-link stills) + maybe 3rd GFX beat / pic density for combined ≥9.5.

## Paths
- `output/frontier_20261002_122844/frontier_video_v12.mp4` + `_chat.mp4`
- Box: `/workspace/frontier_video_v12_chat.mp4`
- Frames: `frames_v12/` (ours_* vs ref_*), box `frames_v12b_*`
- Style: `frontier-gfx-kit/styles/jung.json` (cool grade, leak 0.42)

## v13 if needed
Generate/select stills closer to Whop hallway/hand symbolism; optional prestige art bed; raise combined via denser caption steps without mean-shot collapse.
