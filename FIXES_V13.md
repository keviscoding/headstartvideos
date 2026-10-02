# Frontier FIXES_V13 — prestige Atlas beds + 3rd VO-locked GFX

## Goal
Close combined **9.1 → ≥9.5** by replacing Atlas mountain beds with Whop-grammar prestige stills (hallway / dirt-hands / listen / shadow path). Lightleak remains **OFF**.

## Still content (Atlas generateImage → poll prediction → download)
| file | role | Whop analogue |
|------|------|----------------|
| `stills_v13/v13_hallway.jpg` | opener 0–8s | dark hallway → door |
| `stills_v13/v13_hands.jpg` | 8–13.14s | hands in soil |
| `stills_v13/v13_listen.jpg` | 17.64–26.74s | empty room / listening (replaces pexels) |
| `stills_v13/v13_path.jpg` | 31.24–38.42s | night forest walker (replaces pexels) |
| `stills_v13/v13_coat.jpg` | spare | coat hallway (unused; GFX3 rides to end) |

Proven path: `core/frontier_atlas.py` (`openai/gpt-image-2/text-to-image`, size `1536x864`).

## GFX
- Kept v12 woods + body cards (VO −0.1s)
- **New 3rd card** `@38.42`: “if the forest calls your name” (forest spoken **38.52**)
- Never-overlay; dust/subs muted on GFX

## Held
- Lightleak / chapter flash: **OFF** (`flash_times=None`, `use_lightleak=False`)
- Opener first hard cut **8.0s**
- VO sync all 3 cards lead **−0.1s**, late=[]
- Loudness **−16.0 LUFS** / TP **−1.6**
- Mean shot **~6.15s**, gfx_count **3**

## Honest scores

| pillar | v12c | **v13** | notes |
|--------|---:|------:|-------|
| visual_bed | 9.0 | **9.4** | Hallway + dirt-hands hero plates; still slightly crush-darker than Whop olive-cream; path ≠ Whop coat closer |
| motion_graphics | 9.1 | **9.3** | +3rd thematic collage on forest-calls |
| timing_pacing | 9.0 | **9.1** | 8s opener held; mean 6.15; denser GFX ok |
| vo_gfx_sync | 9.2 | **9.3** | 3/3 keyword −0.1s |
| **combined** | 9.1 | **9.3** | All pillars ≥9; **combined 9.3 < 9.5** |

**NOT READY TO MERGE.**

## Blockers for ≥9.5 (v14)
1. Nudge grade toward Whop olive-cream (less pure black crush; match hallway cream wainscot warmth).
2. Optional closer still: insert `v13_coat` in final ~4s *or* regenerate coat-hallway matched to VO “keep walking”.
3. Karaoke line still reads VO literally (“Mountains that does not”) — script/chunking polish only if it hurts bed read.
4. Do **not** restore lightleak.

## Artifacts
- `output/frontier_20261002_122844/frontier_video_v13.mp4` + `_chat.mp4`
- `frames_v13/`, `stills_v13/`, `gfx_cards_v13/`, `motion_plan_v13.json`, `pacing_metrics_v13.json`
- Box: `/workspace/frontier_video_v13_chat.mp4`, `/workspace/FIXES_V13.md`
