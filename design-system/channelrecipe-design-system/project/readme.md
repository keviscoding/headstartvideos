# ChannelRecipe — Design System

The brand & product design system for **ChannelRecipe**, a web app that turns proven YouTube niches into finished, uploadable faceless videos in ~15 minutes.

> **One line:** Proven recipes for faceless YouTube channels.
> **Tagline:** "Pick a recipe. We handle the cooking."

---

## 1. Product context

Users pick a **recipe** (a validated niche card with a preset format, voice, and thumbnail style); the app generates the script, voiceover, thumbnail, and rendered video, and hands back a complete **Upload Kit** (video file + title + description + tags). A new proven recipe drops every week. Free trial: 3 videos, 7 days, no card. One plan: **$27/month ≈ 15 videos**. The whole brand posture is **proof-first** — real dashboard receipts, never hype.

**Audience:** beginners starting faceless YouTube channels. Budget-conscious, globally distributed, heavily ESL, phone-first, deeply scam-wary. They want to be told exactly what to do, shown that it works, at a price they can afford. Keep all copy simple, concrete, jargon-free.

**Positioning:** the honest sibling of "autopilot" AI-video tools. Competitors sell magic that does it *for* you; ChannelRecipe sells **the proven recipe you cook**. Honest > hypey · Warm > corporate · Plain > clever · Confident > loud · Receipts > promises. The feeling on every screen: *"Oh — I can actually do this today."*

**Sibling brand:** ChannelRecipe is the software sibling of **HeadStart Channels** (cream canvas, near-black chunky grotesque headlines, one accent word, pill badges, rounded CTAs, generous single-column calm). ChannelRecipe *inherits the canvas, type discipline, and layout rhythm* but shifts the accent to **electric violet** and the energy to instant/"make something now." Marketing pages are **light** (cream); the app UI is **dark** (deep navy-violet). The dark product framed on the light page IS the visual identity.

## 2. The recipe metaphor — CRITICAL RULE

The recipe metaphor lives in **language and structure only — never literal food imagery.**
- ❌ Never: chef hats, whisks, forks, spoons, pots, steam, aprons, plates, kitchen scenes, food photos, cooking clipart.
- ✅ Instead: the **recipe card** (a clean software card with steps, ingredients metadata, and a cook time) carries the metaphor. The vocabulary does the work; visuals stay product/software.

**Brand vocabulary — use consistently:**

| Product concept | Brand word |
|---|---|
| Validated niche template | **Recipe** |
| Script / voice / thumbnail / style inputs | **Ingredients** |
| Generation time (~15 min) | **Cook time** |
| Free course / library | **The Cookbook** |
| Weekly niche release | **Recipe of the Week** / "This week's drop" |
| Generation in progress | "Cooking your video…" |
| Finished output bundle | **Upload Kit** |

## 3. Sources

This system was generated from a **written brand & design brief** (no codebase, Figma file, or slide deck was attached). All values below are authored from that brief. There is **no pre-existing logo** — the glyph and wordmark in `assets/` are an **original mark designed for this brief** to the spec it provided (recipe card with a folded-corner-as-play-triangle). If a real brand mark exists, replace the files in `assets/` and the `Logo` component.

Fonts are the free families named in the brief, loaded from their official hosted stylesheets (see Fonts below) — not substitutions.

---

## 4. CONTENT FUNDAMENTALS (voice & copy)

**Point of view:** speak to the reader as **"you"**; the company is **"we"**. Talk to a capable adult who hasn't done this yet — never talk down, never hype up.

**Casing:** **sentence case everywhere** — headlines, buttons, nav, chips are UPPERCASE only in the mono receipt/status context (`NEW`, `PROVEN`, `~15 MIN`). Never title-case headlines.

**Sentences:** short, active, plain verbs. Specific beats clever. One idea per sentence.

**Numbers = receipts.** Money and results are shown as **real numbers in mono type**, never as promises. If a real number doesn't exist yet, use a **claim-free placeholder** ("New: this week's recipe just dropped") — never fabricate a stat.

**Banned vocabulary (scam signals):** "insane," "secret," "hack," "printing money," "passive income machine," "guaranteed," "autopilot." No exclamation-point hype.

**Buttons say exactly what they do:** "Make your first video free," "Cook video," "Download Upload Kit" — never "Submit," "Get started," "Learn more."

**Emoji:** not used in product or marketing UI. (The recipe-card fold, chips, and mono numerals carry personality instead.) The ✅/❌ in *this document* are authoring shorthand, not brand assets.

**Microcopy voice samples:**
- Empty state: "Pick your first recipe."
- Progress: "Cooking your video — about 3 minutes."
- Success: "Recipe followed. Video ready."
- Failure (a trust moment): "That render failed — your credit is back in your account. Try again."
- Trust line: "3 free videos · no card · 2-minute setup"
- Weekly email subject: "This week's recipe just dropped"
- Footer signature: "From the team behind HeadStart Channels."

---

## 5. VISUAL FOUNDATIONS

**Two canvases.** Marketing = cream (`--canvas` #F7F5F0) with near-black ink. App = dark navy-violet (`--app-bg` #0F1222) with off-white text. Product screenshots always sit inside a `BrowserFrame` — the dark product on the light page is the identity.

**Color.** *One accent:* electric violet (`--accent` #6D5AE0) does every CTA, link, accent word, and NEW badge. **Green (`--success`) is a status semantic only** — confirmations, "monetized"/"proven" chips — never decoration. Exactly **one violet word per headline**. No secondary accent, no gradients-as-decoration.

**Type.** Display = **Cabinet Grotesk** (heavy 800, tight leading ~1.02–1.12, letter-spacing −0.02em, sentence case) — carries all personality. Body = **General Sans** (400–600, line-height 1.5–1.6, never below 16px on mobile) — stays quiet. Data = **JetBrains Mono** for every number-as-receipt (cook times, credits, view counts, RPM, dashboard stats); the mono texture signals "real data, not marketing." Strong contrast between display and body sizes.

**Spacing & layout.** 4px base grid. Landing page is a single calm column, max ~1100px, generous whitespace, ~96px between sections, mobile-first (H1 + demo must work at 390px). Prefer flex/grid with `gap`.

**Corner radius.** 16px is the signature recipe-card radius (`--radius-card`); 12px for buttons/inputs, 8px small, 999px pills. The **folded top-right corner forming a play triangle** is the brand's atomic shape — on the logo and every recipe card.

**Backgrounds.** Flat cream or flat dark — no photographic backgrounds, no noise/texture, no full-bleed hero imagery. Section banding uses `--canvas-2` (a hair deeper cream). Depth comes from cards + soft shadow, not from gradients.

**Shadows.** Soft and low on cream (`--shadow-card` = `0 6px 24px rgba(22,22,26,.08)`); violet CTAs get a tinted lift (`--shadow-accent`). Dark surfaces use borders (`--app-border`) more than shadow, plus a deep `--shadow-dark` for elevated panels.

**Borders.** 1px hairlines: `--hairline` #E5E1D8 on cream, `--app-border` #262B45 on dark. Selected/active states switch a border to violet (often 2px) rather than adding a glow.

**Cards.** White (or dark surface) · 16px radius · 1px hairline · soft shadow · the violet fold-play in the top-right corner. Same frame is reused for recipe, pricing, Upload Kit, and testimonial/result cards.

**Motion.** Purposeful and quick — `--dur-fast` 120ms for hovers, `--dur-base` 200ms for state, `--dur-slow` 360ms for progress fills. Easing `cubic-bezier(0.2,0.8,0.2,1)`. Progress bars animate width; the cooking spinner is the only looping animation. No bounces, no parallax, no decorative loops. Respect `prefers-reduced-motion`.

**Hover / press.** Primary button hover → `--accent-hover` (darker); secondary/ghost hover → faint fill (`--canvas-2` / `--app-surface-2`). No opacity-dimming as the primary hover language. Focus/selection uses a violet ring or border.

**Transparency & blur.** Used sparingly — soft ring shadows via `color-mix`, chip tints. No heavy glassmorphism.

---

## 6. ICONOGRAPHY

There was **no icon set in the sources.** ChannelRecipe uses a **thin, single-weight, geometric line icon** language that matches Cabinet Grotesk's clean grotesque feel.

- **Substitution (flagged):** UI kits load **[Lucide](https://lucide.dev)** from CDN (`unpkg.com/lucide@latest`) — a free, MIT-licensed, ~1.5px single-weight geometric set that matches the brand's line language. If you adopt a different set later, keep the single-weight geometric style. *This is a chosen match, not a provided asset — confirm or replace.*
- **Bespoke glyphs** authored inline as SVG (single-weight, currentColor): the **play triangle** and **folded card** (logo/motif), the **dashboard spark** on `PROVEN` chips, the **check** on completed steps, and the cooking **spinner**. These live inside components, not as separate files.
- **Brand assets** in `assets/`: `glyph.svg`, `glyph-mono.svg`, `app-icon.svg`, `favicon.svg`.
- **Emoji / unicode-as-icons:** not used. Chips use text labels (`NEW`, `PROVEN`) + the occasional bespoke spark, never emoji.
- **Icon color:** inherits text color; violet only when the icon is itself an accent/CTA affordance (e.g. a play button fill).

---

## 7. Component index (`components/`)

Public namespace at runtime: `window.ChannelRecipeDesignSystem_51526c` (run `check_design_system` to confirm). Each directory has a `@dsCard` HTML thumbnail.

- **brand/** — `Logo` (glyph · wordmark · horizontal lockup; light/dark).
- **recipe/** — `RecipeCard` — the signature atomic object (fold-as-play corner, name → promise → mono meta → status chip; light/dark, `new`/`proven`, selectable).
- **forms/** — `Button` (primary/secondary/ghost/subtle · sm/md/lg · light/dark), `Input` (label/hint/error, light/dark).
- **display/** — `PillBadge` (eyebrow pill w/ drop dot), `StatusChip` (`new`/`proven`/`monetized`/`warn`/`error`/`neutral`), `CreditsChip` (mono credits counter), `BrowserFrame` (browser-chrome mockup — the dark-product-on-light-page carrier).
- **app/** — `Stepper` (6-step pipeline), `VoiceOption` (selectable voice row + play), `ProgressStages` (honest narrated cooking progress).

**Intentional additions:** none beyond the brief. The set is authored from the brief's described surfaces (there was no source component inventory to enumerate); `PillBadge`, `StatusChip`, `CreditsChip`, `BrowserFrame`, `Stepper`, `VoiceOption`, and `ProgressStages` each map directly to a named element in the brief (§7, §9, §10).

## 8. UI kits (`ui_kits/`)

- **ui_kits/marketing/** — the light cream landing page (nav, fold with demo loop in a browser frame, 3-step how-it-works, proof strip, recipe wall, one pricing card, FAQ, final CTA, footer). Desktop + a 390px mobile view.
- **ui_kits/app/** — the dark product: recipe wall, the 6-step pipeline (Recipe → Title → Script → Voice → Thumbnail → Cook), the cooking/waiting state, the Upload Kit payoff, sign-up, and account/credits.

## 9. Foundations (`guidelines/`)

Specimen cards shown in the Design System tab, grouped **Colors · Type · Spacing · Brand**: accent ramp, neutrals, app-dark palette, status semantics; display/body/mono type; spacing scale, radius & shadow; logo lockups, the recipe-card motif, and the one-accent rule.

## 10. Root manifest

- `styles.css` — the entry point consumers link (＠import list only).
- `tokens/` — `fonts.css`, `colors.css`, `typography.css`, `spacing.css` (also holds radius, shadow, layout, motion).
- `components/` — reusable primitives (`brand/ recipe/ forms/ display/ app/`), each with `.jsx` + `.d.ts` + `.prompt.md` + a `@dsCard` HTML.
- `guidelines/` — foundation specimen cards.
- `ui_kits/` — full-screen product recreations (`marketing/`, `app/`).
- `assets/` — logo glyph, wordmark-free marks, app icon, favicon.
- `SKILL.md` — Agent-Skills-compatible entry so this system can be used from Claude Code.

## 11. Fonts

Loaded from official hosted stylesheets in `tokens/fonts.css` (each ships the `@font-face` rules):
- **Cabinet Grotesk** (display) — Fontshare.
- **General Sans** (body) — Fontshare.
- **JetBrains Mono** (data/receipts) — Google Fonts.

These are the exact free families named in the brief — no substitution. **Font loading verified:** the Fontshare stylesheet serves both Cabinet Grotesk and General Sans (`document.fonts.check` confirms faces load; weights load lazily on use). The design-system checker will still print a *"no project font file — remote stylesheet may serve it"* notice for these two families: **that notice is expected and requires no action.** We deliberately do **not** bundle the binaries — Fontshare's ITF Free Font License is more restrictive about redistribution than OFL, so a redistributable design system should load them from Fontshare's own CDN. If you own a license path that permits it and prefer self-hosting, drop the `.woff2` files into `assets/fonts/` and swap the `@import`s in `tokens/fonts.css` for local `@font-face` rules.
