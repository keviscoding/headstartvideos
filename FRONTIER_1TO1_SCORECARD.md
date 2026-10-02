# Frontier 1:1 Scorecard - Kevis 4-Pillar Evaluation

## Mandatory Scoring Framework

### Pillars (Each /10):

1. **visual_bed** /10
   - AI stills quality (Atlas gpt-image-2 scenes)
   - Pexels b-roll integration & density
   - Ken Burns zoom smoothness
   - Color grade (Jung warm desat, no purple)
   - Dust overlay & vignette

2. **motion_graphics** /10
   - Collage GFX cards present & frequency (~every 30s per jung.json)
   - Jung aesthetic (collage_dark/noir skins, Inter Black titles)
   - Cutout sticker shapes (aged-paper, halftone)
   - **HARD FAIL**: Captions covering GFX cards (motion_graphics = 0/10)
   - Caption system (center kinetic, gold karaoke pop)

3. **timing_pacing** /10
   - Cut speed & rhythm (not rushed, not sluggish vs Jung Whop)
   - Still hold durations (3-6s typical, not too long)
   - GFX insert rhythm (~every 30s, semantic placement)
   - Pexels gaps filled appropriately
   - Overall flow matching Jung Whop reference tempo

4. **combined** /10
   - Overall 1:1 match with Jung Whop Frontier
   - No single soft pillar (<9.0)
   - Visual cohesion across all elements
   - Honest side-by-side comparison with jung-whop-ref-45s.mp4

### Gate for Merge (Formula Rules):
- **combined ≥9.5** /10
- **timing_pacing ≥9.0** /10 (mandatory per formula)
- **visual_bed ≥9.0** /10 (mandatory per formula)
- **motion_graphics ≥9.0** /10 (mandatory per formula)
- **ALL 3 pillars ≥9.0** before combined can merge

**Formula mandate**: Ship only if timing + visual_bed + motion_graphics all ≥9 AND combined ≥9.5

---

## Mac v4 Honest Scores (Awaiting Smoke Test)

### Prediction:
- **visual_bed**: 6.5-7.0 /10 (unchanged from v3)
- **motion_graphics**: 7.5-8.5 /10 (GFX lane present, text-only MVP)
- **timing_pacing**: 6.0-7.0 /10 (new pillar, needs tuning)
- **combined**: 7.0-7.5 /10 (all pillars improving)

### What v4 Adds (Commit 5ef04ab + 7fcf0d5 + caption muting):
✅ **Collage GFX lane** (core/frontier_motion_graphics.py)
- Text-only collage cards every ~30s
- Jung skins: collage_dark + noir
- 6s duration per jung.json pacing
- Interleaved into timeline, overlaps trimmed

✅ **Caption muting during GFX** (frontier_assembler.py)
- Prevents caption/GFX overlap (HARD fail rule)
- Filters ASS events overlapping with motion_gfx ranges
- `no_sub_ranges` passed from assembler to _burn_subtitles

### What v4 Still Needs for 9.0:

**motion_graphics** (blocks 9.0):
- ❌ Cutout sticker shapes (PIL-drawn or vendor Whop assets)
- ❌ Center caption burn (_build_burn_ass: Alignment=5, Inter Black)
- ❌ Visible gold karaoke pop (proper rendering)

**timing_pacing** (blocks 9.0):
- ⚠️ Still hold tuning (Mac v3 felt rushed, verify 3-6s comfortable)
- ⚠️ GFX insert rhythm (semantic vs time-based, needs transcript analysis)
- ⚠️ Cut speed matching Jung Whop tempo

**visual_bed** (needs polish):
- ⚠️ Ken Burns smoothness (0.28 zoom needs frame-by-frame verification)
- ⚠️ Pexels density (denser than v2, verify comfortable vs rushed)
- ⚠️ Grade warmth (Jung EXACT ported, verify no purple cast)

---

## Pillar 1: visual_bed /10

### Components:
1. AI stills quality (Atlas gpt-image-2)
2. Pexels b-roll density & aesthetic
3. Ken Burns zoom (smoothness, speed, 2x headroom)
4. Color grade (Jung warm desat from jung.json)
5. Dust overlay & vignette

### Mac v3 Score: 6.0 /10

**What Worked**:
- ✅ Atlas stills generating (8/8 Mac success)
- ✅ Jung grade ported (eq=brightness=-0.06:sat=0.62:contrast=1.10,colortemp=5200)
- ✅ Dust overlay present (1080p procedural loop, 530KB)
- ✅ Vignette angle-only (no iris wipe)

**What Needs Work**:
- ⚠️ Ken Burns smoothness (0.28 zoom, needs frame verification)
- ⚠️ Pexels density (denser than v2, verify tempo)
- ⚠️ Grade warmth (verify no purple cast in render)

### Mac v4 Prediction: 6.5-7.0 /10 (unchanged pipeline)

---

## Pillar 2: motion_graphics /10

### Components:
1. **Collage GFX cards** (frequency, Jung aesthetic)
2. **Cutout stickers** (aged-paper shapes, halftone)
3. **Caption system** (center kinetic, gold karaoke)
4. **HARD FAIL rule**: Captions covering GFX = 0/10

### Mac v3 Score: 5.0 /10
- ❌ NO GFX lane at all
- ⚠️ Captions present but bottom-aligned (not center)

### Mac v4 Prediction: 7.5-8.5 /10

**What v4 Adds** (Commits 5ef04ab + caption muting):
- ✅ **Collage GFX lane present** (~every 30s per jung.json)
- ✅ **Jung skins** (collage_dark + noir)
- ✅ **Text-only cards** (Inter Display Black titles, aged-paper aesthetic)
- ✅ **Caption muting during GFX** (no overlap, HARD fail prevented)
- ✅ **Interleaved timeline** (GFX segments merged, overlaps trimmed)

**What v4 Still Missing**:
- ❌ **Cutout sticker shapes** (aged-paper cutouts, halftone) → -1.0 to -1.5
- ❌ **Center caption burn** (Alignment=5, Inter Black) → -0.5
- ❌ **Visible gold karaoke** (proper rendering) → -0.5

**Why 7.5-8.5 Not 9.0**:
- Text-only GFX cards are MVP (no stickers yet)
- Captions still bottom-aligned (not center kinetic)
- Gold karaoke may not render visibly

**Path to 9.0**:
1. Add PIL-drawn cutout shapes OR vendor Whop assets
2. Port Whop _build_burn_ass (center, Inter Black, proper karaoke)
3. Verify gold highlight renders visibly on GFX cards

---

## Pillar 3: timing_pacing /10 (NEW)

### Components:
1. **Cut speed & rhythm** (Jung Whop tempo)
2. **Still hold durations** (3-6s typical, not too long)
3. **GFX insert rhythm** (~every 30s, semantic placement)
4. **Pexels gaps** (filled appropriately, not jarring)
5. **Overall flow** (comfortable, not rushed or sluggish)

### Formula Rules (Mandatory):
- **Hook lock**: First hard cut 6-10s (kinetic hook per Kevis; formula measured Whop @ 16.9s but rejected as overhold), first caption ≤2.0s
- **GFX cadence**: Auto-insert so gap ≤35s (collage ~3.2s, bar ~2.0s, UI ~4.0s)
- **Whop targets**: ~18 pic/min (~3.3s mean shot), ~7.5s mean shot with GFX
- **Gate**: timing_pacing ≥9.0 required for merge

### Mac v3 Score: Unknown (pillar not evaluated)

### Mac v4 Prediction: 6.0-7.0 /10

**What's Implemented**:
- ✅ GFX every ~30s (jung.json graphic_every_min=0.5)
- ✅ Still holds 3-6s (still_min_hold=3.0, still_max_hold=6.0)
- ✅ Still pad 0.5s (tighter than v2's 0.8s)
- ✅ Pexels fills gaps between stills

**What Needs Verification**:
- ⚠️ Cut speed feels comfortable (not rushed from denser Pexels)
- ⚠️ Still holds not too short (3-6s range, verify natural)
- ⚠️ GFX placement semantic (vs pure time-based)
- ⚠️ Overall tempo matches Jung Whop (side-by-side comparison)

**Why 6.0-7.0 Not 9.0**:
- Mac v2/v3 feedback: "felt rushed" → denser Pexels may need tuning
- GFX placement is time-based (every 30s), not semantically anchored to script beats
- No explicit tempo matching vs Jung Whop reference (just replicating parameters)

**Path to 9.0**:
1. Mac smoke: side-by-side Jung Whop tempo comparison
2. Tune still_max_hold if holds feel too short
3. Semantic GFX placement (match script beats, not clock time)
4. Verify Pexels gaps feel natural (not jarring cuts)

---

## Pillar 4: combined /10

### Definition:
Overall 1:1 match with Jung Whop Frontier. Honest side-by-side comparison of full rendered output against `jung-whop-ref-45s.mp4`.

### Gate:
- **combined ≥9.5** AND **no pillar <~9.0**

### Mac v3 Score: 5.5 /10
- Visual bed okay (6.0)
- Motion graphics missing (5.0)
- Timing/pacing not evaluated

### Mac v4 Prediction: 7.0-7.5 /10

**Why 7.0-7.5**:
- All pillars in 6.5-8.5 range (no strong pillar yet)
- GFX lane present but text-only MVP
- Timing/pacing needs verification
- Still ~2.0 points from merge gate

**Path to 9.5**:
1. **motion_graphics 9.0+**: Add cutout shapes + center captions
2. **timing_pacing 9.0+**: Tune tempo, semantic GFX placement
3. **visual_bed 9.0+**: Verify Ken Burns + grade + Pexels density
4. **combined 9.5+**: Honest side-by-side with Jung Whop reference

---

## HARD FAIL Rules

### 1. Captions Covering GFX Cards
**Rule**: If captions render on top of motion_gfx cards, **motion_graphics = 0/10** immediately.

**Prevention** (v4):
- ✅ `no_sub_ranges` collected from motion_gfx segments
- ✅ ASS events filtered in `_convert_srt_to_ass`
- ✅ Captions with `start_sec < gfx_end AND end_sec > gfx_start` are skipped

**Verification**: Mac v4 smoke must confirm NO captions visible during GFX cards.

### 2. Purple Color Cast
**Rule**: If purple cast visible (vs Jung warm desat), **visual_bed penalty -2.0**.

**Prevention** (v3/v4):
- ✅ Jung grade EXACT: `eq=brightness=-0.06:saturation=0.62:contrast=1.10,colortemperature=temperature=5200`
- ✅ No hue shift (was causing purple in v1/v2)

**Verification**: Mac v4 smoke visual inspection vs Jung Whop reference.

---

## Mac v4 Summary

### Expected Scores:
- **visual_bed**: 6.5-7.0 /10
- **motion_graphics**: 7.5-8.5 /10 ✅ (GFX lane present)
- **timing_pacing**: 6.0-7.0 /10 ⚠️ (needs verification)
- **combined**: 7.0-7.5 /10 (all pillars improving)

### Delta from v3:
- visual_bed: +0.5 to +1.0
- motion_graphics: +2.5 to +3.5 ✅ (GFX lane vs none)
- timing_pacing: NEW pillar, 6.0-7.0
- combined: +1.5 to +2.0

### Blockers to 9.5:
1. **motion_graphics <9.0**: Text-only GFX (no cutouts), bottom captions
2. **timing_pacing <9.0**: Needs tempo verification, semantic GFX placement
3. **visual_bed <9.0**: Ken Burns/Pexels density needs polish

### Next Steps:
1. **Await Mac v4 smoke** (verify GFX lane + caption muting works)
2. **If v4 ≥7.0**: Add PIL cutout shapes for v5
3. **If v4 <7.0**: Debug GFX rendering/timeline
4. **Port center captions** (_build_burn_ass) in parallel
5. **Target v5**: 8.5-9.0 combined (all pillars ≥8.5)

---

## Reference Material

**Multi-Style Architecture** (Kevis mandate):
- Frontier must work across styles via **style learner / per-channel JSON**
- NOT Jung-only: after ≥9.5, PR #2 merges + deploys DO + Fly cook
- Each channel has its own look JSON (e.g. `jung.json`, `divine.json`, `astro.json`)
- Style parameters: fonts, colors, skins, GFX templates, grade, pacing
- Current Jung smoke tests the architecture; must generalize before production

**Whop Frontier Jung**: `jung-whop-ref-45s.mp4` (45s, 960x540)
- Dark moody stills (books, coats, atmospheric scenes)
- Collage GFX cards with aged-paper sticker cutouts
- Word-level kinetic captions (centered, gold flash per word)
- Cold/desaturated grade (Jung look)
- Smooth Ken Burns zooms
- Pexels b-roll filling gaps
- Tempo: deliberate, not rushed (60-90s typical stills)

**Jung Pacing** (from jung.json):
```json
{
  "graphic_every_min": 0.5,
  "graphic_ratio": 0.35,
  "graphic_dur_s": 6.0
}
```

**Whop Targets** (from formula dissection):
- **~18 pic/min** (0.3 pics/sec, ~3.3s mean shot)
- **~7.5s mean shot** (accounting for GFX cards)
- **First cut 6-10s** (kinetic hook per Kevis; formula measured Whop @ ~16.9s but rejected as overhold)
- **First caption ≤2.0s** (hook lock timing)
- **GFX gap ≤35s** (cadence scheduler auto-insert)
- **GFX durations**: collage ~3.2s, bar ~2.0s, UI ~4.0s

**Jung Look** (from jung.json):
```json
{
  "title_font": "'Inter Display Black','Inter Black',Inter,sans-serif",
  "caption_accent": "&H96B8C9&",
  "footage_grade": "eq=brightness=-0.06:saturation=0.62:contrast=1.10,colortemperature=temperature=5200",
  "skins": ["collage_dark", "collage_dark", "noir"]
}
```

---

**Mac: Run v4 smoke and report 4-pillar scores. Caption muting during GFX must be verified (HARD fail rule).**
