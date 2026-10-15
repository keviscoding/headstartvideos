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

## The niche list: channels blowing up this week

| Channel | What they make | Breakout video | Views |
|---|---|---|---|
| Reconstructed (@Reconstructednews) | 3D cockpit and incident reconstructions | What Really Happened Inside the flydubai Cockpit | 3.2M |
| Neurox-EK (@Neurox-EK) | Minute-by-minute flight reconstructions | What Really Happened Inside the Four 9/11 Flights | 169K |
| Scary Interesting | Investigative disaster stories | The Most Dangerous Mountain You've Never Heard Of | 911K |
| | | | |
| | | | |
| | | | |

---

## Get cooking in ChannelRecipe

Building all of this by hand takes days. **ChannelRecipe** does it for you:

- **Clone a channel's style.** Paste your reference videos: Studio measures their cuts, motion, colour, music and sound effects, watches them, and learns the look as a recipe you can reuse. That is Rule 1, done for you.
- **Use the 3D Reconstruction recipe.** Paste a Rule 13 script, press **Create video**, and Studio directs, reviews and renders the 3D scenes on your own computer.
- **Keep your voice.** Pick your narrator, your palette and your 3D look (clean white studio or colour film).

Start at **channelrecipe.com**.
