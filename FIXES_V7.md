# Frontier smoke FIXES_V7 — Whop pacing locks (Dissect formula)

## Goal
Kill the **v6 rush fail** (`timing_pacing 3.5`) using Dissect formula
`/workspace/dissections/frontier-formula-2026-10-02/formula.md`: first hard cut ≥12s
(target ~16.9), mean shot 5–9s, pic/min 15–22, GFX ~20–25% (not 35%), keep mute
karaoke over GFX, bottom-centre karaoke on beds.

## What landed (code)

### `core/frontier_motion_planner.py`
- Defaults: `still_min_hold_sec=6`, `still_max_hold_sec=12`, `first_cut_min/target`.
- Opener hook lock: first still owns `[0, first_cut_target]`.
- **`build_paced_bed_timeline()`** — long opener + 5–9s stills / 3–7s Pexels bridges.
- **`coalesce_short_segments`**, **`enforce_first_hard_cut`**, **`pacing_metrics`**, `WHOP_PACING`.

### `core/frontier_motion_graphics.py`
- `PACING.graphic_ratio` **0.35 → 0.22**; `first_gfx_min_sec=14`; gap ≥6–8s.
- `plan_gfx_insertions`: no cards before opener cut; aim 20–25% ratio (2–3 cards on ~43s).

### `core/frontier_assembler.py`
- Default karaoke **bottom-centre** (`center=False`, Alignment=2).
- Mute ASS + dust gate on `motion_gfx` retained from v6.

### Assemble
- `rebuild_v7.py` → `frontier_video_v7.mp4` + `_chat.mp4` (1280 / crf28).

## Measured pacing (v7 vs v6 vs formula)

| Metric | Formula | v6 | **v7** | Pass? |
|---|---|---|---|---|
| First hard cut | ≥12s (≈16.9) | 1.68s | **16.93s** | ✅ |
| Mean shot | 5–9s | ~2.0s | **5.41s** | ✅ |
| Cuts/min | 6–12 | ~29 | **9.71** | ✅ |
| Pic changes/min (hard path) | 15–22 | ~30 | **11.1** | ⚠️ under (karaoke steps add density; still soft) |
| GFX ratio | 20–25% | 35% | **23.6%** | ✅ |
| GFX cards | ≥1 / 35s | 5 | **3** @ 3.4s | ✅ |
| First GFX start | after hook | 3.7s | **16.93s** | ✅ |
| Karaoke on GFX | ZERO | ZERO | **ZERO** | ✅ |
| Bed karaoke | bottom-centre | centre | **bottom-centre** | ✅ |

Timeline: opener still 0–16.93 → collage → still → scatter → pexels → still → pillars → pexels.

## Honest 4-pillar scores vs Jung + formula

| pillar | score | notes |
|--------|------:|-------|
| **visual_bed** | **7.0** | Same Atlas/Pexels/warm grade/dust 1.85; longer Ken Burns holds read better. Still short of Jung film density / flash dissolves. |
| **motion_graphics** | **7.8** | Overlay clash still clear; script cutouts; 3 Whop-paced cards. Density intentionally lower than v5/v6 (correct). |
| **timing_pacing** | **7.8** | **Huge lift from 3.5.** First cut / mean / cuts/min / GFX% in band. Soft: hard-path pic/min 11 (&lt;15) — needs caption-step counting or one more mid-bed refresh (flash/still swap) without re-rushing. |
| **combined** | **7.4** | Up from v6 **5.8**. **Not ≥9.5.** No hard-fail clash. Soft pillars: visual_bed + pic/min density. |

## Paths
- Job: `output/frontier_20261002_122844/frontier_video_v7.mp4` (full ~245MB)
- Chat: `…/frontier_video_v7_chat.mp4` (~1.8MB) + flat `output/frontier_video_v7_chat.mp4`
- Box: `/workspace/frontier_video_v7.mp4` (chat encode), `/workspace/frontier_video_v7_chat.mp4`
- Box frames: `/workspace/frames_v7_bed_12.0s.jpg`, `frames_v7_mid_collage_18.6s.jpg`, `frames_v7_mid_scatter_28.0s.jpg`
- v6 chat (also on box): `/workspace/frontier_video_v6_chat.mp4` + mid-GFX JPGs
- Plan/metrics: `motion_plan_v7.json`, `pacing_metrics_v7.json`
- Frames: `frames_v7/`

## v8 blockers (toward ≥9.5)
1. Raise **effective pic/min** into 15–22 without mean-shot collapse (white-flash dissolves, caption-step metric, optional mid-opener still micro-refresh that is NOT a hard cut).
2. Jung **flash dissolve** chapter commas; denser film plate.
3. Yellow `#F5D76E` karaoke (Whop) vs current warm-gold centre flash leftover.
4. Playwright / richer collage motion; thematic cutout polish.
5. Loudness QA → **−16 LUFS** explicit normalize if off.

## Follow-up (post cloud a9fc6dd / 1fc8171 pull)
- Rebased onto cloud formula locks; kept `build_paced_bed_timeline` + GFX gap scheduler (ratio 0.22, first_gfx≥14).
- Aliased `first_still_min_hold_sec` → `first_cut_min_sec` for cloud API compat.
- Fixed inverted first-caption clamp: formula is **≤2.0s** (pull late cues forward only; do not delay 0.0→2.0).
- Cleaned broken merge hybrid in `_convert_srt_to_ass` (audible_fragments mute + bottom-centre).
- Two-pass **loudnorm −16 LUFS** lives in `_mix_audio` for future cooks; v7 chat already measures **~−16.8 LUFS** in (no full reassemble required).
