# Frontier smoke FIXES_V6 — mute karaoke on GFX + denser grit + script cutouts

## Goal
Kill **caption–GFX collision** (Whop `no_sub_ranges`), densify film dust/grain toward Jung grit,
and pick **script-relevant cutouts**. Keep v5 35% GFX templates. Score four pillars against
Dissect formula `/workspace/dissections/frontier-formula-2026-10-02/formula.md`.

## What landed (code)

### `core/frontier_assembler.py`
- **`no_sub_ranges` / `no_dust_ranges`**: auto from `motion_gfx` segments; clip ASS Dialogue
  fragments with Whop `audible_fragments` so karaoke never burns over collage/scatter/pillars/
  opener/photonote windows.
- **`dust_strength`** (default 1.0): v6 uses **1.85** → brighter dust plate + `noise=alls≈16`.
- Dust black-gated during GFX (screen w/ black = noop) so cards stay clean.

### `core/frontier_motion_graphics.py`
- **`CUTOUT_KEYWORDS` + `pick_cutouts_for_text()`**: title/subtitle/item labels → filename stems
  (forest/call/voice/alone/eye/lock/…). Used by `compose_collage_frame` + `render_gfx_lane`.
- Still falls back to seeded shuffle to fill `need`.

### Assemble (Mac job)
- `output/frontier_20261002_122844/rebuild_v6.py` (+ resume assemble log).
- Warm Jung grade unchanged (`sat=0.52`, `temp=4300`).
- Output: `frontier_video_v6.mp4` (~43.3s).

## Mute windows (verified)
| t (s) | template  | mid-frame proof |
|------:|-----------|-----------------|
| 3.7–6.7 | opener | `frames_v6/frame_04_5.2s.jpg` — Caveat card type only, **no Inter karaoke** |
| 11.7–14.7 | collage | `frame_07_13.2s.jpg` — card labels only |
| 19.7–22.7 | scatter | `frame_10_21.2s.jpg` — centered Caveat title in ring |
| 27.7–30.8 | pillars | `frame_13_29.2s.jpg` — pillars + labels, no karaoke |
| 35.7–38.8 | photonote | `frame_16_37.3s.jpg` |

Bed karaoke still present: `frame_01_1.5s.jpg` / `frame_02_2.0s.jpg` (Inter Bold word flash).

## Dissect formula check (Whop targets)
| Metric | Target | v6 measured | Pass? |
|---|---|---|---|
| Pic changes/min | 15–22 | **~30.5** | ❌ rush |
| Cuts/min | 6–12 | **~29** | ❌ rush |
| Mean shot | 5–9 s | **~2.0 s** | ❌ rush |
| First hard cut | ≥12 s | **1.68 s** | ❌ hook fail |
| First caption | ≤2.0 s | **0.0 s** | ✅ |
| GFX card dur | collage 2.5–4.5 s | **3.0 s** | ✅ |
| ≥1 GFX / 35 s | yes | 5 / 43 s | ✅ (dense) |
| Karaoke on GFX | ZERO | **ZERO** (mute works) | ✅ |
| Dust on beds | yes | yes @ strength 1.85 | ✅ |
| Music | OFF | OFF | ✅ |

## Honest scores vs Jung Whop + formula (self)
| pillar | score | notes |
|--------|------:|-------|
| **visual_bed** | **6.8** | Warm grade + denser dust/grain readable on beds; Atlas+Pexels. Still short of Jung film density; karaoke is centre (formula wants bottom-centre); beds choppy because timeline is rushed. |
| **motion_graphics** | **7.8** | **No overlay clash** (hard-fail gate cleared). 5 templates @ ~35%, Caveat cream, script-matched stickers (mic/lock/key/eye/hearts). Pillow port still less fluid than Whop Playwright; some scatter stickers less thematic. |
| **timing_pacing** | **3.5** | **Rush failure mode** (formula): first cut 1.68s (need ≥12), mean shot ~2s (need 5–9), pic/min ~30 (need 15–22). Overlay clash = 0 so not hard-capped to ≤3 from clash, but rush dominates. |
| **combined** | **5.8** | Mute + grit + cutout match are real wins vs v5 (~7.3 self without timing pillar). **Not ≥9.5.** Soft pillar = timing. |

### Combined with Kevis rule
Overlay clash would hard-fail MG+timing → combined floor. **Clash cleared.** Combined still capped by timing_pacing 3.5.

## Paths
- Job MP4: `output/frontier_20261002_122844/frontier_video_v6.mp4`
- Flat copy: `output/frontier_video_v6.mp4`
- Frames: `output/frontier_20261002_122844/frames_v6/`
- Plan: `motion_plan_v6.json` · Cards: `gfx_cards_v6/`
- Formula: `/workspace/dissections/frontier-formula-2026-10-02/formula.md`
- Log: `rebuild_v6_assemble.log`

## v7 cadence / hook locks (do next — do not claim 1:1 yet)
1. **Hold first bed ≥12–17 s** before first hard cut; refresh with caption steps + optional flash only.
2. Target **mean shot 5–9 s**, **pic/min 15–22**, hard cuts **6–12/min**.
3. GFX ratio closer to Whop **20–25%** on full cooks (short smokes may keep denser cards but must not chop beds to &lt;3 s).
4. Move karaoke to **bottom-centre** (Alignment=2, MarginV≈8%) per formula; keep mute on GFX.
5. Optional: yellow `#F5D76E` karaoke (Whop) instead of warm gold centre flash.
