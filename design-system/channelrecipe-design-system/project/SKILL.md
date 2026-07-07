---
name: channelrecipe-design
description: Use this skill to generate well-branded interfaces and assets for ChannelRecipe, either for production or throwaway prototypes/mocks/etc. Contains essential design guidelines, colors, type, fonts, assets, and UI kit components for prototyping.
user-invocable: true
---

Read the README.md file within this skill, and explore the other available files.

ChannelRecipe turns proven YouTube niches into finished faceless videos in ~15 minutes. The brand is **proof-first, honest, warm, plain** — the software sibling of HeadStart Channels. Two canvases: **cream** marketing pages, **dark navy-violet** app. One accent: **electric violet**. The **recipe metaphor is verbal + card-structure only — zero food imagery, ever.** Numbers are always **mono** (receipts, not promises).

Key files:
- `README.md` — full context, content & visual foundations, iconography, component + UI-kit index.
- `styles.css` → `tokens/` — link `styles.css` for all color/type/spacing/radius/shadow tokens and the three webfonts.
- `components/` — reusable primitives (RecipeCard, Logo, Button, Input, PillBadge, StatusChip, CreditsChip, BrowserFrame, Stepper, VoiceOption, ProgressStages), each with a `.prompt.md` usage note.
- `ui_kits/marketing/` and `ui_kits/app/` — full-screen recreations to copy from.
- `assets/` — logo glyph, app icon, favicon.
- `guidelines/` — foundation specimen cards.

If creating visual artifacts (slides, mocks, throwaway prototypes, etc), copy assets out and create static HTML files for the user to view. If working on production code, you can copy assets and read the rules here to become an expert in designing with this brand.

If the user invokes this skill without any other guidance, ask them what they want to build or design, ask some questions, and act as an expert designer who outputs HTML artifacts _or_ production code, depending on the need.
