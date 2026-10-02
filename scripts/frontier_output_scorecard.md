# Frontier Output Scorecard

**Date**: ____________  
**Evaluator**: ____________  
**Channel Recipe Branch**: `cursor/frontier-admin-recipe-e6f9`  
**Reference**: `jung-whop-ref-45s.mp4` (Whop Frontier production output)

---

## Required Attachments

Before scoring, you **MUST** attach:

1. **Smoke Test Video Path**: Full path to the Channel Recipe Frontier render MP4
   - Example: `/Users/kevis/dev/channel-recipe/output/frontier_smoke_20261002_093045.mp4`
   - Duration: ≥20 seconds
   - Generated via: `frontier` recipe with `ATLASCLOUD_KEY` + optional `PEXELS_KEY`

2. **Frame Grabs**: Extract and attach 4 representative frames from your smoke render
   ```bash
   ffmpeg -i <smoke_video.mp4> -vf "select='eq(n,30)+eq(n,120)+eq(n,240)+eq(n,360)'" -vsync vfr frame_%03d.png
   ```
   - `frame_001.png` — Early frame (1 second)
   - `frame_002.png` — Mid-early frame (4 seconds)
   - `frame_003.png` — Mid-late frame (8 seconds)
   - `frame_004.png` — Late frame (12 seconds)

3. **Reference Frame Grabs**: Extract 4 frames from `jung-whop-ref-45s.mp4`
   ```bash
   ffmpeg -i jung-whop-ref-45s.mp4 -vf "select='eq(n,30)+eq(n,360)+eq(n,720)+eq(n,1080)'" -vsync vfr ref_frame_%03d.png
   ```

---

## Visual Quality Dimensions

For each dimension, compare Channel Recipe output frames against Whop Frontier reference frames.

### 1. Still Image Quality (Atlas `gpt-image-2`)

**Channel Recipe Output**:
- [ ] Stills are 1920x1080, cinematic composition
- [ ] Atmospheric, not literal/clipart style
- [ ] No text/watermarks burned into stills

**Score**: ___/10  
**Notes**:


### 2. Pexels B-Roll Integration

**Channel Recipe Output**:
- [ ] Stock video/photo clips fill gaps between stills
- [ ] Clips are HD (≥1080p)
- [ ] Clips match script themes/keywords
- [ ] No jarring transitions or mismatched content

**Score**: ___/10  
**Notes**:


### 3. Motion Planning (Word-Locked Stills)

**Channel Recipe Output**:
- [ ] Stills own spoken word spans from Whisper/SRT alignment
- [ ] Ken Burns zoom (in/out) alternates per still
- [ ] Minimum still hold ~3.5s, maximum ~10s
- [ ] Padding (~0.8s) around still ownership
- [ ] Timeline fully packed: no black frames or gaps

**Score**: ___/10  
**Notes**:


### 4. Kinetic Captions (ASS Word-Level)

**Channel Recipe Output**:
- [ ] Word-level timing synchronized with voiceover
- [ ] Gold accent color (`&H96B8C9&`) flashes on active word
- [ ] Font: Inter ExtraBold, size 73
- [ ] Bottom center alignment (MarginV: 60)
- [ ] Outline: 3.4px black with 64% transparent background

**Score**: ___/10  
**Notes**:


### 5. Color Grade (Jung/Frontier)

**Channel Recipe Output**:
- [ ] Brightness: `-0.06`
- [ ] Saturation: `0.62` (desaturated/muted)
- [ ] Contrast: `1.10`
- [ ] Color temperature: `5200K` (warm bias)
- [ ] Overall: matches Frontier's cinematic muted teal/gold look

**Score**: ___/10  
**Notes**:


### 6. Dust Overlay

**Channel Recipe Output**:
- [ ] `assets/overlay_dust.mp4` asset exists
- [ ] Subtle particle effect blended at 30% opacity (screen mode)
- [ ] Loops seamlessly throughout video
- [ ] No jarring artifacts or temporal discontinuities

**Score**: ___/10  
**Notes**:


### 7. Vignette

**Channel Recipe Output**:
- [ ] Dark vignette applied (strength: 0.35)
- [ ] Smooth gradient from center to edges
- [ ] Matches Frontier's cinematic framing

**Score**: ___/10  
**Notes**:


### 8. Audio Mix

**Channel Recipe Output**:
- [ ] Voiceover audio clear and well-mixed
- [ ] AAC 192kbps encoding
- [ ] No clipping, distortion, or sync issues

**Score**: ___/10  
**Notes**:


### 9. Overall Visual Fidelity

Compare side-by-side: Channel Recipe frames vs. Whop Frontier reference frames.

**Channel Recipe Output**:
- [ ] Color palette matches reference
- [ ] Motion timing/pacing feels equivalent
- [ ] Captions are visually indistinguishable
- [ ] Could pass as authentic Whop Frontier output in blind test

**Score**: ___/10  
**Notes**:


### 10. Admin-Only Access

**Channel Recipe Output**:
- [ ] Recipe `admin_only: True` enforced
- [ ] Only `nwalikelv@gmail.com` can trigger Frontier cooks
- [ ] Non-admin requests rejected with error

**Score**: ___/10  
**Notes**:


---

## Overall Visual Score

**Total**: ___/100  
**Average (out of 10)**: ___/10  

**Pass Threshold**: ≥9.5/10 average across all visual dimensions.

---

## Approval Decision

- [ ] **APPROVED**: Visual score ≥9.5/10 → Mark PR #2 ready for review
- [ ] **NEEDS WORK**: Visual score <9.5/10 → Identify gaps and iterate

**Blocker Issues** (if any):


**Next Steps**:


---

## Evaluator Sign-Off

**Name**: ____________  
**Date**: ____________  
**Signature**: ____________  

