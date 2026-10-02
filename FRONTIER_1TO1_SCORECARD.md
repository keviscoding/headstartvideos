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

### Current Scores (Output-Based, Honest from Mac Smoke v1)

#### 1. Visual Bed: **3.0/10** ❌

Breakdown:
- Still Look: **5/10** (Atlas generated but quality not Jung-level)
- Pexels Motion: **2/10** (only 2 videos + 2 photos vs 7 stills - too sparse, need denser gaps)
- Ken Burns: **1/10** (not visible in v1 - static stills, zoom broken)
- Color Grade: **4/10** (present but weak vs Jung cold/dark look)
- Dust: **0/10** (missing from v1 render - file not found)
- Vignette: **5/10** (present but weak)

**visual_bed: 3.0/10** ❌

#### 2. Motion Graphics: **0/10** ❌

Breakdown:
- Kinetic Captions: **0/10** (CRITICAL: v1 leaked raw `{\k...}` ASS tags on screen - not rendered)
- Word Flash/Highlight: **0/10** (karaoke broken - tags visible as text)
- On-Screen GFX: **0/10** (none implemented)

**motion_graphics: 0/10** ❌ (User says: "v1 has ZERO motion graphics")

#### 3. Combined: **3.0/10** ❌

With motion_graphics at ZERO, combined cannot exceed visual_bed. Both pillars must be strong.

**combined: 3.0/10** ❌ (FAILS ≥9.5 requirement, FAILS pillar minimum ~9.0)

---

## Critical Bugs Found in Mac Smoke v1

1. ❌ **ASS karaoke tags leaked** - `{\k20}` visible on screen (escape bug after adding overrides)
2. ❌ **Ken Burns not visible** - zoom broken, stills appear static
3. ❌ **Pexels too sparse** - only 2 videos + 2 photos for 7 stills (need denser motion bed)
4. ❌ **Dust missing** - `overlay_dust.mp4` file not found at runtime
5. ❌ **Grade weak** - not matching Jung's cold/dark cinematic look
6. ❌ **FPS mismatch** - concat duration metadata wrong

---

## HONEST ASSESSMENT

### Fixes Pushed (v2):

1. ✅ ASS escape fix - only escape plain text, preserve `{\k...}` overrides
2. ✅ Ken Burns zoom increased - 20% delta minimum, better zoompan expression
3. ✅ Denser Pexels - reduced still_max_hold 10→6s, still_pad 0.8→0.5s
4. ✅ FPS normalization - all segments now output at same fps before concat
5. ✅ Dust path fallbacks - multiple locations checked with logging
6. ✅ PEXELS_KEY live load - reads os.environ, not frozen config

### Path to ≥9.5:

**MANDATORY**:
1. Mac smoke v2 with fixed assembler
2. Verify kinetic captions render correctly (gold flash on words)
3. Verify Ken Burns zoom is visible
4. Verify denser Pexels motion bed
5. Verify dust overlay applies
6. Score honestly: visual_bed ≥9, motion_graphics ≥9, combined ≥9.5

**Cannot merge until both pillars strong.**
