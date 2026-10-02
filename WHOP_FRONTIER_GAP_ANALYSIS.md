# Whop Frontier Architecture Gap Analysis

## Critical Finding: Missing Production GFX Layer

Independent videoReview correctly identified that Jung Whop Frontier is a **produced edit** with a full motion graphics layer, NOT just stills+Pexels+captions.

---

## Jung Whop Architecture (Source of Truth)

### 1. Motion Graphics Templates (`motion.py`)

**Skins**: collage_dark, noir, glass, paper, clean, astro
- `collage_dark`: Warm charcoal, gold/brick/teal accents, aged-paper sticker cutouts
- `noir`: Deep navy, frosted glass cards, vintage warm feel

**Templates**: scatter, pillars, photonote, deckfan, checklist, bar_chart, icon_flow, crowd, statistic, etc.
- Rendered via Chromium + ffmpeg at 1920x1080, 30fps
- Deterministic frame-by-frame rendering (`window.renderFrame(t)`)
- **Interleaved with stills+Pexels**: ~every 20-40s of VO gets a template card

**Pacing** (from `jung.json`):
```json
"graphic_every_min": 0.5,
"graphic_ratio": 0.35,
"graphic_dur_s": 6.0,
"graphic_max_s": 10.0
```

### 2. Jung Look (`jung.json`)

**Grade** (NOT purple):
```
"footage_grade": "eq=brightness=-0.06:saturation=0.62:contrast=1.10,colortemperature=temperature=5200"
```

**Caption Accent**:
```
"caption_accent": "&H96B8C9&"  # Gold/cream
```

**Fonts**:
```
"title_font": "'Inter Display Black','Inter Black',Inter,sans-serif"
"title_weight": "900"
```

**Skins Rotation**:
```
"skins": ["collage_dark", "collage_dark", "noir"]
```

### 3. Caption Burn (`_build_burn_ass` from Whop make_video)

- **Center screen** when using dark/noir skins (NOT bottom)
- **Karaoke highlight** that libass actually renders
- **Inter ExtraBold/Black** fonts
- `SecondaryColour` = accent gold for word flash

---

## Current CR Frontier vs Whop Frontier

### What CR Has (Partial):
✅ Atlas gpt-image-2 stills  
✅ Pexels b-roll  
✅ Ken Burns zoom  
✅ ASS karaoke tags (but wrong placement/style)  
✅ Vignette + dust  
⚠️ Grade (has purple cast, not Jung warm desat)  

### What CR is MISSING (Critical):
❌ **Motion graphics templates** (collage, scatter, pillars, etc.) - motion_graphics score 3.5/10  
❌ **GFX interleaving** (~every 20-40s pattern)  
❌ **Whop caption burn semantics** (center, Inter Black, proper karaoke)  
❌ **Jung look presets** from jung.json  
❌ **Skin-aware layout** (collage_dark/noir templates)  

---

## Agency Path Forward (PR #2, Keep Draft)

### Phase 1: Port Jung Look (Quick Win)

1. ✅ **Grade preset** from jung.json:
   ```python
   footage_grade = "eq=brightness=-0.06:saturation=0.62:contrast=1.10,colortemperature=temperature=5200"
   ```
   - Kill purple cast
   - Match Whop exactly

2. ✅ **Caption accent** from jung.json:
   ```python
   caption_accent = "&H96B8C9&"  # Gold/cream, not current color
   ```

3. ✅ **Fonts**: Inter Display Black / Inter Black for titles

### Phase 2: Minimal Motion Graphics Lane (Required for ≥9.0)

**Goal**: At least 2 templates (collage + scatter) rendering ~every 30s

**Options**:
- **A**: Vendor full `motion.py` into `core/motion_graphics.py` and call it
- **B**: Port minimal `collage` + `scatter` templates only (simpler HTML/CSS/JS)
- **C**: Create stub GFX cards with Jung styling (fastest, lower quality)

**Minimum Viable**:
- Interleave GFX segments between stills
- Use collage_dark/noir skins
- Render at 1920x1080 via Playwright/Chromium
- ~6s duration per card
- Deterministic frame rendering

**Without this**: motion_graphics cannot exceed ~4/10

### Phase 3: Whop Caption Burn Semantics

Port `_build_burn_ass` behavior:
- Center screen placement (Alignment=5) when dark/noir
- Inter ExtraBold/Black fonts
- Proper karaoke SecondaryColour rendering
- Larger font size (84+)

### Phase 4: Dust/Vignette from Whop

- Heavier dust/grain matching Whop
- Vignette matching Whop intensity

---

## Honest Scorecard (Independent videoReview v2)

**Current**:
- visual_bed: 4.5/10 (stills 6, Ken Burns 3, grade 4, dust 3, Pexels 4, vignette 5)
- motion_graphics: 3.5/10 (captions wrong, **collage/GFX missing entirely**)
- combined: 4.0/10 ❌

**To Reach ≥9.5**:
- visual_bed: Need 9.0+ (Jung grade, heavier dust, denser Pexels, smoother Ken Burns)
- motion_graphics: Need 9.0+ (**MUST have collage/scatter GFX**, center captions, yellow pop)
- combined: Need 9.5+ (both pillars strong)

---

## Recommendation

1. **Immediate**: Port Jung look (grade + caption accent) - 10 min fix
2. **Critical**: Vendor motion.py OR port minimal collage+scatter - 2-4 hour task
3. **Polish**: Whop caption burn + heavier dust - 1 hour
4. **Smoke**: Mac v3 with GFX layer rendering

**Do NOT claim 1:1 until smoke shows**:
- ✅ Collage GFX cards interleaved
- ✅ Center kinetic captions
- ✅ Jung warm desat grade
- ✅ No purple cast

**Keep PR draft until both pillars ≥9.0 and combined ≥9.5**

