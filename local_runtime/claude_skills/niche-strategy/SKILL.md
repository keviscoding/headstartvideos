---
name: niche-strategy
description: Cross-niche arbitrage. Find a winning channel idea from ChannelRecipe's live niche database, then propose strategies (niche bend, a lane, or combining two channels or styles) as concrete channel candidates with evidence, first titles and a product. Use when the creator asks what channel to start, how to stand out, what to combine, or for a strategy instead of a plain clone.
argument-hint: "[a niche, a channel link, or an idea]"
---

# Niche strategy: cross-niche arbitrage

The creator wants a strategy, not a copy. Copying a winning channel makes them the second-best version of it.
Instead, find what works in one place that nobody has brought somewhere else yet. Work from real data only.

What they gave: $ARGUMENTS

## 1. Get the data (free)

ChannelRecipe's niche database is the `channelrecipe` server's tools (in a Studio folder they are connected; from
the plugin, the ChannelRecipe connector):
- `list_niches`: the niches in the live database. Always call it first; never invent a niche name.
- `list_niche_subjects(niche)`: the subjects pulling views in that niche right now.
- `list_niche_channels(niche)`: channels in it with subscribers, recent average views and recent titles.

Pull 2 to 4 niches: the one they named (or closest), plus neighbours whose audience or format could cross over.
If they gave a channel link, find its niche and look at that channel's recent titles too. If a tool says the
database is empty or the plan caps it, say so plainly and work with what came back. Never fill gaps from memory.

## 2. Think in three moves

- **Niche bend:** a format that is winning in niche A, pointed at niche B's audience (e.g. the slow cinematic
  "what if" explainer from science, pointed at money).
- **Lane:** inside a hot niche, a subject with strong views (from `list_niche_subjects`) that few channels cover
  well (from `list_niche_channels` titles).
- **Combination:** two channels, or two styles, made into one new thing (e.g. a history channel's storytelling
  with a 3D reconstruction look).

## 3. Give 3 to 5 candidates

One short block each:
- **Concept** in one line, and which move it is.
- **Audience** and **evidence**: the real channels, subjects and numbers from the database that show the demand
  (name them; numbers as the tools gave them).
- **Look:** one installed style (`list_styles` from the `studio` server), a clone of one channel, or two channels
  combined. Say which, with the channel links.
- **First 5 titles.**
- **Beyond AdSense:** one product idea for this audience (the digital-product skill builds it).
- **Risk:** the one honest reason it could fail.

Then ask which one they want, with a multiple-choice question.

## 4. Then act (only what they choose)

- Combine or clone the look: the clone-style skill (`clone_style_look` takes `youtube_urls` with two or three
  channels to combine). Studying a style uses their Claude Code and one of their plan's style clones; it asks first.
- First videos: the make-videos skill, with the candidate's titles and style. It shows the cost and asks first.
- A product: the digital-product skill.

Nothing in steps 1 to 3 spends money. Never start paid work without their yes.
