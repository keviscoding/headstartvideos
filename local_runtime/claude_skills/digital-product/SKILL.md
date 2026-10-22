---
name: digital-product
description: Plan and build a digital product (an illustrated, fact-checked book or cookbook with a landing page, Stripe checkout and launch guide) for a niche or channel with ChannelRecipe Studio, so the channel earns beyond AdSense.
disable-model-invocation: true
argument-hint: "[the niche or channel]"
---

# A digital product for your niche

Use ChannelRecipe Studio's tools (the `studio` server: `mcp__studio__…` in a Studio folder,
`mcp__plugin_channelrecipe_studio__…` from the ChannelRecipe plugin). Everyone can earn AdSense; a product of
their own is how a creator gets paid properly for the attention they win. It works before the channel even
exists: pick the product while picking the niche.

What they gave: $ARGUMENTS

1. **Check Studio** with `setup_check` (see the make-videos skill when it isn't running or ready).
2. **The brief.** A few lines: the niche, who watches, the channel idea or its YouTube link. Use what they gave or
   ask once.
3. **Ideas (no money).** `product_ideas(about, channel)` takes 1 to 2 minutes of their Claude Code. It returns:
   - product ideas held against what 140+ channels sell (title, promise, kind, price);
   - channel names and .com domains nobody owns yet;
   - pricing, the funnel (description line, pinned comment, mention lines) and the launch order.

   Show the ideas as a short numbered list and the best two names and domains. Say plainly that a domain is
   bought separately (about $11 a year on Vercel); Studio never buys one.
4. **Confirm the build.** It takes about an hour and spends about $0.30 of pictures on their image account plus
   about 20 Claude Code calls. It makes:
   - the illustrated, fact-checked book PDF and a cheat sheet;
   - the landing page with a Stripe checkout;
   - the launch guide.

   Ask "Shall I build number N?". Claude Code also asks them to approve `build_product`.
5. **Build.** `build_product(idea_number, domain?, channel_name?)`. Give the id; check `products(id)` now and then,
   not in a loop. It lands in `output/offers/<id>/`.
6. **Sell it.** When it's done:
   - Studio videos can mention it (pick it under Your product on the Studio page).
   - They get a QR card and a short link per video, so they see which video sold.
   - `LAUNCH-GUIDE.pdf` walks them through Stripe, Vercel, the domain and a test purchase.

   Never put a product live or take payments for them: they publish it themselves.
