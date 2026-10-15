# The 3D Documentary Harness

Two rules that turn "make me a 3D documentary" into videos that look and sound like the channels blowing up right now:

1. **The Visual DNA rule**: lock the look by keeping only what never changes across 3 or more reference videos.
2. **Rule 13**: the investigative hook, plus the full script structure built on it.

Paste this whole file into your AI's project instructions (a Claude Project, a `CLAUDE.md`, or a custom GPT), then give it your reference videos and your topic.

---

## How to use this harness

1. Pick **3 or more reference videos** from one channel or one niche. Use their best performers, not their latest uploads.
2. Run **Rule 1** on all of them and fill in the Visual DNA block.
3. Write your script with **Rule 13**: the hook first, then the structure.
4. Give your 3D tool, or your editor, the DNA block and the script together, and check every shot against the DNA before you render.

---

## RULE 1: The Visual DNA rule

> A trait belongs to the Visual DNA only if it appears in **every** reference video.
> - Seen in all of them: it goes in the **DNA**. Never break it.
> - Seen in some: it is **allowed**, so use it when the story needs it.
> - Seen in one: **ignore it**. It was a one-off, not the style.

Score the references on these five traits, in this order:

### 1. Camera: focal length and the slow isometric dolly

| Measure | How to measure it | Starter value (replace with yours) |
|---|---|---|
| Focal length | Do straight lines bend at the edges? Do near objects balloon? Match it to 24, 35, 50 or 85 mm (full-frame equivalent). | 35 mm for wides, 50 mm for mid shots and inserts, never fisheye |
| Camera angle | How far the camera looks down on the scene | Isometric feel: 30 to 35° down, the camera 1.5 to 2× the subject's height |
| Dolly speed | Time how long the frame takes to travel one frame-width | 2 to 3% of the frame width per second (a full cross takes about 40 s) |
| Move style | Constant speed, or eased? Does it ever stop? | Constant glide with a 0.5 s ease in and out; never cut mid-acceleration |

### 2. Lighting: single-point volumetric light in a dark room

| Measure | How to measure it | Starter value |
|---|---|---|
| Light sources | Count the shadows each object casts | One key light only (a spot or a practical lamp), 45° above and to the side |
| Volume | Can you see the beam in the air? | A visible beam: haze or fog at 10 to 20% |
| Darkness | How dark the background gets, from 0 (black) to 255 (white) | The background falls close to black (under about 25 of 255); no fill light |
| Separation | How subjects separate from the dark | A faint rim light on silhouettes |

### 3. Mannequins: consistent red and blue shaders

| Measure | How to measure it | Starter value |
|---|---|---|
| Faces | Are the people detailed or faceless? | Faceless mannequins, no hair, no texture |
| Colour code | Which side gets which colour, every time | **Red** = the people in danger (crew, passengers, victims). **Blue** = everyone else (rescuers, the other side, control). Never swapped. |
| Material | Glossy, matte or plastic? | One matte shader per colour, used in every scene; the same red and blue hex values all video long |

### 4. Shot pacing

| Measure | How to measure it | Starter value |
|---|---|---|
| Hook shot length | Median shot length in the first 30 s | 1.8 to 2.5 s |
| Body shot length | Median shot length after the hook | 3.5 to 5 s |
| Longest hold | The longest single shot | About 8 s, saved for the money shot |
| Breathing beats | What happens before each new section | 1 to 1.5 s with no narration, music only, before every section |

### 5. 3D text call-outs

| Measure | How to measure it | Starter value |
|---|---|---|
| Position | Where labels sit in the frame | Anchored to the object with a thin leader line, in the upper third, never over the subject or its action |
| Size | Text height as a share of the frame height | About 3.5% of the frame height, uppercase sans-serif |
| Timing | How long each stays on screen | At least 2 s; one call-out per shot |
| Entrance | How it appears | A quick fade-and-scale (about 6 frames), with no bounce |

### The Visual DNA block (fill this in, then give it to your AI or editor)

```yaml
visual_dna:
  references: [video 1, video 2, video 3]
  camera:
    focal_length_mm: { wide: 35, mid: 50 }
    angle: isometric, 30-35 deg down
    dolly_speed: 2-3% of frame width per second, constant, 0.5 s ease
  lighting:
    key: single point, 45 deg high and to the side
    volume: visible beam, haze 10-20%
    background: near black, no fill, faint rim
  mannequins:
    faces: none
    red: "#C8372D"   # the people in danger
    blue: "#2F6FD6"  # everyone else
    material: matte, one shader per colour, never changes
  pacing:
    hook_median_s: 2.0
    body_median_s: 4.0
    longest_hold_s: 8
    breathing_beat_s: 1.2
  callouts:
    anchor: object, thin leader line
    zone: upper third, never over the action
    size: 3.5% of frame height, uppercase sans-serif
    min_on_screen_s: 2
    per_shot: 1
  allowed_not_dna: []   # traits seen in only some references
```

**Check every shot before it renders.** If it breaks a DNA rule, fix the shot, not the rule.

---

## RULE 13: The investigative hook

Open on three ingredients, in this order, within the first 15 to 20 seconds.

1. **Exact coordinates and the vessel's stats.** Give the precise time, the altitude, depth or location, then what the vessel is: its type, its size, how many people are aboard. Real numbers, spoken exactly.
2. **One specific human in a specific seat.** Name them, give their role and say exactly where they are sitting. The viewer rides the rest of the video in that seat.
3. **The sudden mechanical failure.** The moment the machine stops behaving. Show the part, the sound, the warning light. Don't explain it yet.

### Fill-in formula

> **[Exact time and date].** **[Altitude, depth or location]** above or below **[place]**.
> **[Vessel type]**, **[size or key stat]**, **[number] people on board**.
> In **[seat or station]**, **[full name]**, **[role]**, **[one human detail: hours into the shift, first flight, last trip]**.
> Then **[the part]** **[fails, in one verb]**.

### Example (B-59, October 1962)

> October 27th, 1962. One Soviet submarine. Eleven American warships. And one nuclear torpedo.
> B-59 is trapped off Cuba. Batteries dying. Air thick with carbon dioxide.

Coordinates, vessel stats and the failing machine all arrive in under 15 seconds. The human in the seat (Vasily Arkhipov) is held back for the decision point.

### Hook rules

- Every number is exact and sourced: altitude, time, headcount.
- Use present tense in the hook. It is happening now.
- No "in this video we will…" Let the failure be the promise.
- Each sentence gets its own shot, and every shot obeys the Visual DNA.

---

## RULE 13: The script structure

The hook is only the first beat. The whole script runs on the same three ingredients: **where, who, what broke.** These beats fit an 8 to 12 minute video.

| # | Beat | Time | What it does |
|---|---|---|---|
| 1 | **Cold open** | 0:00 to 0:20 | The three Rule 13 ingredients. Fast cuts (hook pacing). |
| 2 | **Stakes and promise** | 0:20 to 0:45 | One hard number (the odds, the toll, what was at risk) and what we will reconstruct, minute by minute. |
| 3 | **The machine** | 0:45 to 2:00 | A 3D cutaway of the vessel: the three facts that made it *should-have-been-safe*. Breathing beat before the next section. |
| 4 | **The people** | 2:00 to 3:00 | Two to four specific humans: name, seat or station, one trait each. Red or blue by side, kept for the whole video. |
| 5 | **The timeline** | 3:00 to 7:00 | Minute by minute with the clock on screen: normal, then the first anomaly, then the failure, then each decision. Present tense. |
| 6 | **The decision point** | 7:00 to 8:00 | One person, one choice, in the seat from the hook. Slow down and hold the money shot. |
| 7 | **The outcome** | 8:00 to 9:00 | What happened, shown plainly. |
| 8 | **The investigation** | 9:00 to 10:30 | What the recorders, reports or survivors revealed, and what everyone got wrong at the time. |
| 9 | **What changed** | 10:30 to 11:30 | The rule, design or procedure that exists because of this. |
| 10 | **The last line** | final 15 s | Back to the human in the seat: a quote, a final number, or the one thing they did. |

### Script rules

- **One sentence, one shot.** Write the script so every sentence can be a single 3D scene.
- **Numbers are spoken exactly** ("seventeen thousand feet", not "very high"), and every one has a source.
- **Never invent dialogue.** Quote recordings and reports, or say that nobody knows.
- **Put a breathing beat before each new section:** 1 to 1.5 seconds of music, no narration.
- **Every call-out names something real:** a part, a place, a time, a person.

---

## The niche list: proven niches you can break into today

The goal isn't a million-dollar channel. It's a steady $3,000 to $5,000 a month from a niche that has worked for years, with over a billion views between its channels. Every style below is one you can clone in ChannelRecipe today.

Views are for each channel's breakout video. Run Rule 1 on each channel's top three videos.

### 1. Footage documentary

- [LEMMiNO](https://www.youtube.com/watch?v=I2O7blSSzpI), 42M
- [SunnyV2](https://www.youtube.com/watch?v=nFWDteOZLFM), 10M
- [Aperture](https://www.youtube.com/watch?v=IqXpzcfdtn4), 3.2M
- [Newsthink](https://www.youtube.com/watch?v=Y1oahLVhlpU), 1.5M

### 2. 3D reconstruction

- [Fern](https://www.youtube.com/watch?v=wLFY_Zu_O08), 15M
- [Neurox](https://www.youtube.com/watch?v=jr2qm1C8DHQ), 168K, rising
- [Plano](https://www.youtube.com/watch?v=xXH3JZCsAHo), 112K, rising
- [3D Crash](https://www.youtube.com/watch?v=SStzg9hh0cI), 85K

### 3. Finance and charts

- [The Swedish Investor](https://www.youtube.com/watch?v=5Vxjyz9DZ6k), 5.3M
- [Economics Explained](https://www.youtube.com/watch?v=F_VUBpALcVE), 3.6M
- [How Money Works](https://www.youtube.com/watch?v=QmWyQ-BCD8E), 3.0M
- [PolyMatter](https://www.youtube.com/watch?v=d7SVsqg4Jzk), 1.5M

### 4. True crime (all rising channels)

- [Bodycam Verdicts](https://www.youtube.com/watch?v=NrTaqT_PgmI), 745K
- [DOCS187s](https://www.youtube.com/watch?v=hPkSuPn12PY), 445K
- [Law Decoded](https://www.youtube.com/watch?v=5eYQQtuK6Go), 220K
- [Cold Room Interrogations](https://www.youtube.com/watch?v=hyLxrj3m-68), 78K

### 5. AI avatar presenter

- [Frank Miller Cuts](https://www.youtube.com/watch?v=wR1l5k8CKgg), 491K
- [Wade Cooper Pours](https://www.youtube.com/watch?v=IxBYiI5j-MI), 190K
- [Markus Stein](https://www.youtube.com/watch?v=GxzDTocI3DM), 141K, German
- [Aussie Trucker Life](https://www.youtube.com/watch?v=3dXr3IC3XUQ), 128K

### 6. Anime stories

- [Tobs Manhwa](https://www.youtube.com/watch?v=t8JEQXEAKSs), 865K
- [AniTiger](https://www.youtube.com/watch?v=x0S1o25N8vU), 563K
- [Amagi](https://www.youtube.com/watch?v=loDprUgqTYs), 521K, "what if" style

### 7. Kinetic text and motion essay

- [Sisyphus 55](https://www.youtube.com/watch?v=sSo2b4y5nnA), 2.2M
- [Academy of Ideas](https://www.youtube.com/watch?v=sNg5FXt_DDM), 786K

### 8. Stick figures on detailed backgrounds

- [StickTory](https://www.youtube.com/watch?v=k8YGIx-HWkw), 1.0M
- [Stickfigurist](https://www.youtube.com/watch?v=dN_RMA8LVi0), 100K

### 9. 2D cartoon history

- [Sam O'Nella Academy](https://www.youtube.com/watch?v=0zMSMJZc9EA), 23M
- [Serious History](https://www.youtube.com/watch?v=0fA_SpTVBnE), 2.7M
- [History Matters](https://www.youtube.com/watch?v=QdmJsqDceV4), 2.4M
- [Simple History](https://www.youtube.com/watch?v=d3tXkJkgbQw), 1.9M

### 10. Geopolitics maps

- [Wendover Productions](https://www.youtube.com/watch?v=yIZRffLA294), 3.9M
- [Caspian Report](https://www.youtube.com/watch?v=rgCrnyf5JEI), 3.2M
- [Kings and Generals](https://www.youtube.com/watch?v=5ij0tDYhIhg), 929K

### 11. Painting and art-led history

- [Fall of Civilizations](https://www.youtube.com/watch?v=d2lJUOv0hLA), 40M
- [Epic History](https://www.youtube.com/watch?v=2Ih1ffiuwj0), 3.7M
- [Sleepless Historian](https://www.youtube.com/watch?v=4mMTjYJ3zd4), 363K
- [History Calling](https://www.youtube.com/watch?v=4lz_G9jj9Hg), 334K

### 12. Hidden-billionaire AI mini-movie

- [Life Turns](https://www.youtube.com/watch?v=NW6vsvGOXoc), 549K
- [Bitter Echoes](https://www.youtube.com/watch?v=vqw9sSGtrdU), 280K
- [Bitter Whispers](https://www.youtube.com/watch?v=uST5eNc-4YM), 196K
- [Bike Heart Stories](https://www.youtube.com/watch?v=3BNIxqdWzTc), 188K

### 13. Dark psychology and stoic essay

- [RF Motivational Vibes](https://www.youtube.com/watch?v=VCzK1x39iQM), 841K
- [Philosophies for Life](https://www.youtube.com/watch?v=m4Nrs0fSF-4), 177K

### 14. Rise and fall of companies

- [Modern MBA](https://www.youtube.com/watch?v=Ha2oNXDZuSg), 2.2M
- [Bagholder Association](https://www.youtube.com/watch?v=3v5rU97gyoc), 1.1M
- [Old American Empires](https://www.youtube.com/watch?v=XSzEWQhLhGM), 431K
- [Everything Fender](https://www.youtube.com/watch?v=8JQSOHnR3Zk), 296K

### 15. Over-60 AI-host tips

- [Garrett Pruitt Trucker](https://www.youtube.com/watch?v=tLrx_EkSnus), 298K
- [Cruiser Pro](https://www.youtube.com/watch?v=bS4AytvFgEM), 156K
- [Susan Parker Travel](https://www.youtube.com/watch?v=oj1kx4veIL4), 83K
- [Logan Murry](https://www.youtube.com/watch?v=CR9S5VInGz0), 78K

**Clone any of these styles in ChannelRecipe today.** Paste three of a channel's videos and Studio learns its look, pacing, music and voice as your own recipe.

---

## Get cooking in ChannelRecipe

Building all of this by hand takes days. **ChannelRecipe** does it for you:

- **Clone a channel's style.** Paste your reference videos: Studio measures their cuts, motion, colour, music and sound effects, watches them, and learns the look as a recipe you can reuse. That is Rule 1, done for you.
- **Use the 3D Reconstruction recipe.** Paste a Rule 13 script, press **Create video**, and Studio directs, reviews and renders the 3D scenes on your own computer.
- **Keep your voice.** Pick your narrator, your palette and your 3D look (clean white studio or colour film).

Start at **channelrecipe.com**.
