---
name: cloud-batch
description: Make up to five videos at once on Studio Cloud (ChannelRecipe's machines render them side by side), paid from the cloud balance.
disable-model-invocation: true
argument-hint: "[topic or titles]"
---

# A batch of videos on Studio Cloud

Use ChannelRecipe Studio's tools (the `studio` server: `mcp__studio__…` in a Studio folder, `mcp__plugin_channelrecipe_studio__…` from the ChannelRecipe plugin). Studio Cloud renders several videos side by side on ChannelRecipe's
machines, so the creator's computer stays free. Studio still writes the scripts and makes the voice and pictures
here with their Claude Code, so Studio must stay open.

What they asked for: $ARGUMENTS

1. **Check** with `setup_check` and `cloud_balance`. If Studio Cloud isn't available, say why (its `why`) and offer
   the make-videos skill on this computer instead. Show the balance.
2. **Titles.** Up to 5, one line each. From a topic, suggest 5 strong titles and let them edit the list.
3. **Style.** Only styles whose `renders_on` includes studio_cloud: `list_styles(kind='cloud')`, then the gallery
   (`gallery_url`, see make-videos "Showing pages") and `get_style_pick(pick_id, wait_seconds=90)`. If they pick one
   that renders on this computer only, say so and offer that or another style.
4. **Length.** Minutes per video (1 to 60).
5. **Cost.** `quote(style, minutes, titles=N, where='studio_cloud')`. Say its `summary`: the expected machine cost,
   the most each video reserves, the media on their accounts, and the balance. If it is `short_by_usd`, give the
   `topup_url` (amounts $5, $10, $25, $50) and wait until they've topped up (`cloud_balance` again).
6. **Start** only after a clear yes: `make_videos(titles, style, minutes, where='studio_cloud', quote_id=…)`. Claude
   Code asks them to approve the tool call.
7. **Follow.** `progress(id='cloud')` shows the state, the machine's last lines and finished videos with download
   links. Check now and then, not in a loop; Studio shows a notification when they're done. Finished videos can be
   downloaded from their links or saved to Projects on the Studio page (open_in_studio(page='projects')).
