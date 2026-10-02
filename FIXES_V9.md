# Frontier smoke FIXES_V9 — kinetic opener + circular lightleak + Whisper karaoke

## Goal
Kevis HARD: opener felt dead at ~16.9s. **First hard cut → ~6–10s** (landed **8.0s**).
Plus: circular Whop light-leak commas, denser dust plate, Whisper word-lock yellow karaoke,
richer collage (Pillow), never-overlay + multi-style-safe asset paths.

## What landed (code)

### Kinetic opener (not formula 16.9)
- `core/frontier_motion_planner.py`: `first_cut_min/target` default **6 / 8**; cloud alias `first_still_min_hold_sec=6.0`.
- `build_paced_bed_timeline(..., first_cut_min_sec=6, first_cut_target_sec=8)` — no `max(12, …)` floor.
- `core/frontier_pipeline.py`: passes `first_still_min_hold_sec=6.0` (aligned with cloud `a283be0`).
- `core/frontier_motion_graphics.py`: `first_gfx_min_sec` **10** (was 14); usable_start floor 8.

### Circular light-leak (not flat brightness wipe)
- `core/frontier_assembler.py` `_apply_white_flashes`: screen-blend `assets/lightleak.mp4` in gbrp;
  **probes program W×H** so 1080 master and 720 chat both work.
- Fallback brightness pulse only if leak asset missing.
- Style-agnostic candidates: `assets/`, `frontier-gfx-kit/textures/`, `assets/whop-gfx/textures/`.

### Dust / karaoke / Whisper
- Dust plate + gate scale to program size; `dust_strength=2.25`, vignette **0.75** (PI/4).
- Whisper `word_timings.json` (148 words) → ASS `\1c&H6ED7F5&` (#F5D76E) word pops.
- Mute ASS over GFX + flash envelopes (never-overlay).

### Multi-style (mandate)
- Pacing / grade / dust / leak loaded via **params + asset-root lookup**, not Jung-only hardcoded floors of 12–17s.
- Jung smoke remains the proving ground; style JSON (jung.json / divine / …) owns look after ≥9.5 merge+deploy.
- Do **not** merge until pillars ≥9.5.

## Timeline (v9)

| t | type | note |
|---|---|---|
| 0.00–**8.00** | ai_still_000 | kinetic opener |
| 8.00–13.59 | pexels | first hard cut @ **8.0s** |
| 13.59–16.99 | collage GFX | |
| … | beds + scatter + pillars | GFX ratio **23.6%** |

Flashes: `[1.75, 8.0, 13.59, 16.99, 24.18, 27.58, 31.82]` (circular leak).

## Metrics vs v8 / formula (Kevis kinetic band)

| Metric | Formula (legacy) | Kevis kinetic | v8 | **v9** | Pass? |
|---|---|---|---|---|---|
| First hard cut | ≥12 ≈16.9 | **6–10** | 16.93 | **8.0** | ✅ Kevis |
| Mean shot | 5–9s | 5–9 | 5.41 | **4.33** | ⚠️ soft low |
| Cuts/min | 6–12 | 6–12 | 9.71 | **12.48** | ⚠️ high edge |
| Hard+flash pic/min | 15–22 | 15–22 | ~22 | **~22.2** | ✅ |
| GFX ratio | 20–25% | 20–25 | 23.6 | **23.6** | ✅ |
| Karaoke on GFX | ZERO | ZERO | ZERO | **ZERO** | ✅ |
| Word-lock yellow | #F5D76E | yes | phrase ASS | **Whisper words** | ✅ |

## Honest 4-pillar scores

| pillar | v8 | **v9** | notes |
|--------|---:|------:|-------|
| **visual_bed** | 7.6 | **8.1** | Circular lightleak commas + denser grit plate + Ken Burns beds; grade still a touch yellow-green vs Whop. |
| **motion_graphics** | 8.0 | **8.2** | Collage/scatter/pillars richer bob; never-overlay held. Still Pillow ≠ Playwright kit `motion.py`. |
| **timing_pacing** | 8.2 | **8.0** | **Opener fixed (8.0s first cut)** — big Kevis win. Soft regression: mean shot 4.33 & cuts/min 12.5 slightly outside ideal band. |
| **combined** | 7.9 | **8.1** | Combined **>7.9**; bed ≥8.0. **Not ≥9.5** — no merge/deploy yet. |

## Paths
- Job: `output/frontier_20261002_122844/frontier_video_v9.mp4` (~241MB)
- Chat: `…/frontier_video_v9_chat.mp4` (~2.4MB) → box `/workspace/frontier_video_v9_chat.mp4`
- Frames: `frames_v9/` · box proofs `frames_v9_{leak_1.8s,firstcut_8.0s,bed_yellow_10s,mid_collage_15.3s,opener_mid_4s}.jpg`
- Plan/metrics: `motion_plan_v9.json`, `pacing_metrics_v9.json`, `word_timings.json`
- Rebuild: `rebuild_v9.py`

## v10 blockers (→ ≥9.5)
1. Coalesce beds so mean shot returns to **5–9s** without lengthening opener past 10s.
2. Playwright-parity GFX via kit `motion.py` skins (style JSON).
3. Second channel style smoke (non-Jung) before merge.
4. Lightleak disk stronger / less full-frame yellow wash; karaoke yellow more readable mid-word.
