# Frontier smoke FIXES_V11 — VO-locked GFX + thematic cutouts + stronger leak

## Kevis HARD: VO–GFX sync

Cards must illustrate the line **while it is spoken** (≤0.3s lag), not a beat later.

### Before / after (one card)

| | v10 (bed-boundary) | **v11 (Whisper VO-lock)** |
|---|---|---|
| Card title | “If something in the dark calls your name…” | “the woods can learn a voice” |
| Card `start_sec` | **16.81** | **13.14** |
| Keyword spoken | “dark/calls” ≈ **4.3–6.0s** | “woods/learn” **13.24–13.88** |
| Lead / lag | **~+12s LATE** (hard fail) | **−0.1s** (start = keyword − 0.1s) ✅ |

Second card: “body/wants” spoken **26.84** → card **26.74** (lead −0.1s).

`vo_gfx_sync`: **PASS** (0 late cards). API: `plan_gfx_insertions(..., word_timings=..., vo_sync_lag_tolerance_sec=0.3)` → delegates to thesis-aware `plan_gfx_vo_locked`.

## What else landed
- Thematic Jung cutouts (ban clover/rocket/jesus; force moon/tiger/sisyphus/pillar)
- Stronger circular lightleak (`leak_strength≈0.82`, vignetted disk); flashes **not** on GFX starts
- Multi-style: `frontier-gfx-kit/styles/{jung,divine}.json` + divine VO plan smoke (`multi_style_smoke_v11.json`)
- Kinetic opener **8.0s** kept; mean **6.18s**; cuts/min **8.32**; GFX **21%**; hard+flash pic/min **~19.4**

## Honest 4-pillar (+ vo_gfx_sync)

| pillar | v10 | **v11** | notes |
|--------|---:|------:|-------|
| visual_bed | 8.5 | **8.6** | Stronger leak disk; still ≠ Whop hero plate |
| motion_graphics | 8.7 | **8.8** | Thematic PW stickers + VO sync; early flash-wash fixed in v11b |
| timing_pacing | 8.9 | **9.0** | Opener/mean/cuts held; pic/min mid-band |
| **vo_gfx_sync** | fail | **9.2** | Both cards ≤0.3s; −0.1s anticipation |
| **combined** | 8.7 | **8.9** | Not ≥9.5 — **no merge/deploy** |

## Paths
- Job video/chat: `output/frontier_20261002_122844/frontier_video_v11.mp4` + `_chat.mp4`
- Box: `/workspace/frontier_video_v11_chat.mp4`
- Sync JSON: `vo_gfx_sync_v11.json`, `pacing_metrics_v11.json`
- Cloud base: `019b38e` VO-lock signature; Mac wires Whisper + thematic planner

## v12 blockers
Whop-closer Atlas/grade; cutout theme polish; second full-style cook (divine); push all pillars ≥9 / combined ≥9.5.
