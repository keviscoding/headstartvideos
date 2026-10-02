# Frontier 1:1 Scorecard - OUTPUT-BASED EVALUATION

## Status: INCOMPLETE - Awaiting Full Render with Atlas + Pexels

**Critical Gap**: This evaluation cannot achieve ≥9.5/10 without rendering a real CR Frontier video with Atlas gpt-image-2 stills and Pexels b-roll, then comparing side-by-side against Whop Frontier Jung reference output.

**What's Been Validated**:
- ✅ Pipeline architecture implements all Frontier systems
- ✅ Smoke test proves video assembly works end-to-end
- ✅ Word-level kinetic ASS captions now implemented (was missing)
- ⚠️ Cannot score visual output without API keys (ATLASCLOUD_KEY, PEXELS_KEY missing)

---

## Reference Material

**Whop Frontier Jung**: `jung-whop-ref-45s.mp4` (45s, 960x540)
- Dark moody stills (books, coats, atmospheric scenes)
- Word-level kinetic captions - each word flashes gold as spoken  
- Cold/desaturated grade
- Smooth Ken Burns zooms
- Pexels b-roll filling gaps

**Jung Frame Samples**:
- Frame 15s: Dark coat on hook, gold kinetic text "unacceptable, then **buried** so thoroughly that"
- Frame 60s: Old book ("The Principles of Psychology"), warm lamp light
- Frame 120s: (not provided)

**Atlas Still Reference**: Old psychology book on wooden desk - warm cinematic lighting, shallow DoF, professional product shot quality

---

## Evaluation Framework

Score each dimension 0-10 based on **actual rendered output**:
- **10**: Perfect 1:1 match with Whop Frontier
- **9**: Visually indistinguishable in practice
- **8**: Very close, small observable differences
- **7**: Good, some noticeable differences
- **<7**: Needs improvement

**RULE**: Cannot score >5 on visual dimensions without comparing real MP4 outputs.

---

## 1. Still Look & Quality (Atlas gpt-image-2)

**Score: 5/10** (PROVISIONAL - awaiting real render)

✅ **Code Implements**:
- Atlas Cloud API calls to gpt-image-2 model
- Scene-based prompts (not quote-on-paper)
- Cinematic style suffix matching Frontier
- 1920x1080 resolution
- Parallel generation (12 workers default)

❌ **Cannot Verify Without Render**:
- Actual still aesthetic vs Jung reference (book, coat, atmospheric mood)
- Whether gpt-image-2 scenes match Frontier's dark psychology look
- Color palette match
- Composition quality
- Consistency across stills

**To Reach 10/10**: Render with real ATLASCLOUD_KEY, extract frames, compare side-by-side against Jung reference stills.

---

## 2. Pexels B-Roll Integration

**Score: 5/10** (PROVISIONAL - awaiting real render)

✅ **Code Implements**:
- LLM keyword generation from script (14 terms)
- Pexels API fetch (24 videos, 12 photos)
- No back-to-back repeat logic (_PexelsBag)
- Caching to prevent re-downloads

❌ **Cannot Verify Without Render**:
- Whether keywords match Frontier atmospheric style
- Quality/relevance of fetched Pexels assets
- Smoothness of transitions between stills and b-roll
- Whether gaps are actually filled (no black frames)

**To Reach 10/10**: Render with PEXELS_KEY, watch full video, verify smooth Pexels fills all gaps.

---

## 3. Motion Timing & Still Ownership

**Score: 6/10** (PARTIAL - smoke test validates structure)

✅ **Validated in Smoke Test**:
- Motion planner creates correct timeline structure
- Stills own specific time spans (start_sec to end_sec)
- Pexels fills gaps between stills
- No overlaps, no empty gaps
- Durations clamped (min 3.5s, max 10s, pad 0.8s)

```
Smoke test output (20s video):
1. ai_still (0.0s-4.8s) zoom=in
2. pexels_video (4.8s-8.0s) zoom=hold  
3. ai_still (8.0s-12.8s) zoom=out
4. pexels_video (12.8s-16.0s) zoom=hold
5. ai_still (16.0s-20.0s) zoom=in
```

❌ **Cannot Verify Without Full Render**:
- Whether still timing actually matches VO meaning
- Sentence-to-still matching accuracy
- Whether transitions feel natural
- Pacing vs Frontier reference

**To Reach 9/10**: Render with script+VO, verify stills appear at correct narrative moments.

---

## 4. Word-Level Kinetic Captions

**Score: 8/10** (CODE COMPLETE - awaiting visual verification)

✅ **Implemented (Post-Fix)**:
- `_add_kinetic_karaoke()` generates ASS `\k` tags from Whisper word timings
- Each word gets duration in centiseconds
- Color override `{\c&H96B8C9&}` flashes accent gold per word
- ASS styling matches Frontier: Inter ExtraBold, 73px, bottom-aligned, shadow 3.4

✅ **Smoke Test Validation**:
```
Dialogue: 0,0:00:00.00,0:00:04.00,Default,,0,0,0,,{\k20\c&H96B8C9&}The {\k40\c&H96B8C9&}shadow ...
```
Karaoke tags present and syntactically correct.

❌ **Cannot Verify Without Visual Render**:
- Whether timing sync is tight (word flash exactly as spoken)
- Whether accent color (#C9B896 -> &H96B8C9&) matches Jung gold
- Whether font/size looks identical to reference frames
- Readability at 1080p

**Why Not 10/10**: Need to see rendered video with captions to verify visual match. ASS syntax is correct but timing precision and color match need output validation.

**To Reach 10/10**: Render, watch with audio, confirm each word flashes in sync.

---

## 5. Color Grade & Look

**Score: 7/10** (PARTIAL - grade string matches, output unknown)

✅ **Code Implements**:
- Default grade: `eq=brightness=-0.06:saturation=0.62:contrast=1.10,colortemperature=temperature=5200`
- Matches Frontier Jung style exactly (cold, desaturated, filmic)
- Applied to all Pexels clips and photos in `_build_segment_video()`

❌ **Cannot Verify Without Render**:
- Whether grade actually produces Frontier's cold/dark look
- Color consistency across mixed Pexels assets
- Whether stills (from Atlas) match grade of footage
- Overall tonal unity vs Jung reference

**To Reach 10/10**: Render, extract frames at 5s intervals, compare tonal range/saturation/temperature to Jung reference.

---

## 6. Ken Burns Zooms

**Score: 7/10** (ARCHITECTURAL MATCH - visual smoothness unknown)

✅ **Code Implements**:
- `_ken_burns_filter()` generates ffmpeg zoompan expressions
- Linear interpolation: `start_zoom + (end_zoom - start_zoom) * (frame / total_frames)`
- Zoom amount 0.15 (15% scale) matches Frontier default
- Alternate strategy: in, out, in, out...
- Hold mode for videos (no zoom)

✅ **Smoke Test Validates**: Zoom directions alternate correctly in timeline.

❌ **Cannot Verify Without Render**:
- Smoothness of zoom motion
- Whether 0.15 zoom feels right for 3-10s still durations
- Whether alternating creates good rhythm
- Comparison to Frontier's perspective zoom (we use zoompan)

**To Reach 9/10**: Render, watch zooms frame-by-frame, verify smooth motion without jitter. Compare zoom speed/feel to Jung reference.

---

## 7. Dust Overlay & Vignette

**Score: 7/10** (PARTIAL - vignette works, dust asset now present)

✅ **Validated in Smoke Test**:
- Vignette applies correctly (dark edge fade, 0.35 strength)
- `_apply_vignette()` works end-to-end

✅ **Dust Overlay Asset**:
- `assets/overlay_dust.mp4` now present (32MB, 10s loop, procedural particles)
- Screen blend at 30% opacity coded and ready
- FFmpeg `-stream_loop -1` ensures seamless looping

❌ **Cannot Verify Without Render**:
- Whether vignette strength matches Jung reference darkness
- Dust particle aesthetic in final output
- Whether dust opacity/blend matches Frontier subtlety

**To Reach 9/10**: Run real Atlas+Pexels render, verify dust/vignette look matches Frontier reference.

---

## 8. Pipeline Execution

**Score: 9/10** (SMOKE TEST VALIDATES ARCHITECTURE)

✅ **Smoke Test Proves**:
- All 6 pipeline steps execute without errors
- Parallel workers function correctly  
- Progress callbacks work
- Timing tracked per step
- Output video created (0.49 MB, 20s, valid MP4)
- Segments concatenate cleanly
- Audio mix works
- No crashes or missing dependencies (except API keys)

✅ **Code Review**:
- Orchestration matches Frontier's run_pipeline pattern
- ThreadPoolExecutor for parallel stills/Pexels
- Error handling with retries
- Graceful fallbacks (e.g. if Whisper fails)

❌ **Minor Gap**:
- Cannot test full Atlas/Pexels integration without keys
- Worker count optimal for local but unverified for Fly Machine

**Why 9/10**: Architecture proven solid, but full integration needs keys.

---

## 9. Recipe & Admin Gating

**Score: 10/10** (VERIFIED - NON-VISUAL)

✅ **Verified**:
```python
frontier = get_recipe('frontier')
assert frontier['admin_only'] is True
assert 'ATLASCLOUD_KEY' in frontier['requires_keys']
assert 'PEXELS_KEY' in frontier['optional_keys']
```

✅ **Existing CR Infrastructure**:
- Admin check already tested (`nwalikelv@gmail.com`)
- Recipe validation works
- Niche JSON updated
- cook_runner.py handles "frontier" recipe

**Non-visual dimension - score stands.**

---

## Overall Score (3-Pillar Kevis Method)

### MANDATORY: Three-Pillar Scoring

Per Kevis scoring rule, ALL Frontier evaluations must report three numbers:

**1. visual_bed /10** — Stills quality, Pexels motion bed, Ken Burns, color grade, vignette, dust overlay
**2. motion_graphics /10** — Kinetic captions (word flash/highlight), on-screen GFX (NOT the stills themselves)
**3. combined /10** — Overall 1:1 Whop Frontier match (both pillars must be strong; do NOT average away a zero)

**Gate for merge**: `combined ≥9.5` AND neither pillar below ~9.0

---

### Current Scores (Output-Based, Independent videoReview of Mac v2)

**AUTHORITATIVE REVIEW**: Independent videoReview agent analyzed Mac v2 output vs Jung reference.

#### 1. Visual Bed: **4.5/10** ❌

Breakdown:
- Still Look: **6/10** (Atlas generated, decent quality but not Jung cinematic)
- Pexels Motion: **4/10** (denser than v1 but still sparse vs Whop density)
- Ken Burns: **3/10** (visible but weak, not smooth Whop motion)
- Color Grade: **4/10** (purple cast, not Jung warm desaturated look)
- Dust: **3/10** (960x540 broke blend, needs 1080p + scale-safe + heavier grain)
- Vignette: **5/10** (iris wipe artifact from a={strength} param)

**visual_bed: 4.5/10** ❌

#### 2. Motion Graphics: **3.5/10** ❌

**CRITICAL FINDING**: Jung motion graphics are NOT lower-third white SRT captions.

Jung Frontier has:
- ✅ **Centered bold kinetic captions** (not bottom)
- ✅ **Word-by-word yellow/gold highlight + bounce/pop**
- ✅ **Collage cutouts / animated underlines / PiP framing resets**

Mac v2 has:
- ❌ Bottom lower-third white captions (wrong placement)
- ❌ Weak karaoke rendering (no visible yellow highlight/bounce)
- ❌ No collage/cutouts/underlines/PiP (missing entirely)

Breakdown:
- Kinetic Captions Placement: **2/10** (bottom not center)
- Word Flash/Highlight: **3/10** (karaoke renders but weak, no pop/bounce)
- Collage/Cutouts/Underlines: **0/10** (not implemented)
- PiP Framing: **0/10** (not implemented)

**motion_graphics: 3.5/10** ❌

#### 3. Combined: **4.0/10** ❌

Independent videoReview combined score. Both pillars weak.

**combined: 4.0/10** ❌ (FAILS ≥9.5 requirement, FAILS pillar minimum ~9.0)

---

## Critical Gaps Found by Independent videoReview

**Motion Graphics (3.5/10 - biggest gap)**:
1. ❌ Captions bottom-aligned (should be centered like Jung)
2. ❌ No visible yellow/gold highlight bounce/pop on words
3. ❌ Missing collage cutouts entirely
4. ❌ Missing animated underlines
5. ❌ Missing PiP framing resets

**Visual Bed (4.5/10)**:
1. ❌ Purple cast in grade (Jung is warm desaturated)
2. ❌ Dust 960x540 broke blend (needs 1080p scale-safe)
3. ❌ Vignette iris wipe artifact (wrong param usage)
4. ❌ Ken Burns weak/not smooth
5. ❌ Pexels still too sparse
6. ❌ Dust/grain not heavy enough vs Whop

---

## HONEST ASSESSMENT

### What CR Frontier Has (Partial 1:1):

✅ Atlas gpt-image-2 stills generation  
✅ Pexels b-roll fetching and integration  
✅ Ken Burns zoom on stills (0.28, needs smoothness polish)  
✅ ASS karaoke word-level timing (but wrong placement/style)  
✅ Vignette + dust overlay  
✅ SRT-to-ASS conversion  
⚠️ Grade (improved but still has purple cast vs Jung warm desat)  

### What CR Frontier is MISSING (Critical for 1:1):

**Motion Graphics Layer (WHY motion_graphics = 3.5/10)**:
❌ **Collage templates** (aged-paper sticker cutouts, scatter, pillars, photonote, deckfan)  
❌ **GFX interleaving** (~every 20-40s pattern per Whop pacing)  
❌ **Skin-aware rendering** (collage_dark/noir templates with Jung styling)  
❌ **Chromium+ffmpeg GFX pipeline** (motion.py deterministic frame rendering)  

**Caption System (Wrong Style)**:
❌ Whop uses **center screen** kinetic captions (Alignment=5), not bottom  
❌ Whop uses **Inter Display Black** fonts, not Inter ExtraBold  
❌ Whop caption burn via `_build_burn_ass`, different from current ASS approach  
❌ Yellow/gold pop on words not rendering visibly  

**Jung Look (From jung.json)**:
❌ Grade preset: `eq=brightness=-0.06:saturation=0.62:contrast=1.10,colortemperature=temperature=5200`  
❌ Caption accent: `&H96B8C9&` (Jung gold/cream)  
❌ Fonts: `'Inter Display Black','Inter Black',Inter,sans-serif` weight 900  

---

## Whop Frontier Source of Truth (Attached)

From `uploads/jung.json` + `uploads/motion.py`:

**Jung Look**:
```json
{
  "look": {
    "title_font": "'Inter Display Black','Inter Black',Inter,sans-serif",
    "title_weight": "900",
    "caption_accent": "&H96B8C9&",
    "footage_grade": "eq=brightness=-0.06:saturation=0.62:contrast=1.10,colortemperature=temperature=5200",
    "skins": ["collage_dark", "collage_dark", "noir"]
  },
  "pacing": {
    "graphic_every_min": 0.5,
    "graphic_ratio": 0.35,
    "graphic_dur_s": 6.0
  }
}
```

**Motion Graphics** (`motion.py`):
- Templates: collage, scatter, pillars, photonote, deckfan, bar_chart, icon_flow, etc.
- Skins: collage_dark (aged-paper cutouts), noir (frosted glass), glass, paper, clean
- Rendered: Chromium + ffmpeg, 1920x1080, 30fps, deterministic frame-by-frame
- **Interleaved with stills+Pexels**: ~every 20-40s of VO gets a 6s GFX card

**Without motion graphics templates, CR Frontier cannot match Jung Whop 1:1.**

---

## Path to ≥9.5 (Requires Motion GFX Layer)

### Phase 1: Port Jung Look ✅ (Done in v2.1)
- Grade preset from jung.json (kill purple)
- Caption accent &H96B8C9&
- Inter Display Black fonts

### Phase 2: Motion Graphics Lane ❌ (CRITICAL, Not Started)
**Minimum Viable**:
- Vendor `motion.py` into `core/motion_graphics.py` OR
- Port minimal collage + scatter templates (HTML/CSS/JS via Playwright)
- Interleave GFX segments between stills (~every 30s)
- Render at 1920x1080, 6s duration
- Use collage_dark/noir skins

**Without this**: motion_graphics CANNOT reach 9.0/10

### Phase 3: Whop Caption Burn Semantics ❌ (Not Started)
- Port `_build_burn_ass` from Whop make_video
- Center screen (Alignment=5) when dark/noir skins
- Inter Display Black fonts
- Proper yellow pop rendering

### Phase 4: Dust/Vignette Polish ⚠️ (Partial)
- Heavier dust/grain matching Whop intensity
- Vignette matching Whop look

---

## Gate for Merge

**Remains**: `combined ≥9.5` AND both pillars `≥~9.0`

**Current blockers**:
1. Motion graphics templates missing (blocks motion_graphics ≥9.0)
2. Whop caption burn not ported (blocks motion_graphics polish)
3. Jung look partially ported (visual_bed needs testing)

**DO NOT CLAIM 1:1** until Mac v3 smoke shows:
- ✅ Collage GFX cards interleaved with stills
- ✅ Center kinetic captions (not bottom)
- ✅ Jung warm desat grade (no purple)
- ✅ Yellow word pop rendering visibly

**Keep PR draft.**
