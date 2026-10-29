---
name: cloud-batch
description: Make up to five videos at once on Studio Cloud (machines on the creator's own Modal account render them side by side, billed by Modal).
argument-hint: "[topic or titles]"
---

# A batch of videos on Studio Cloud

Use ChannelRecipe Studio's tools (the `studio` server: `mcp__studio__…` in a Studio folder, `mcp__plugin_channelrecipe_studio__…` from the ChannelRecipe plugin). Studio Cloud renders several videos side by side on machines in the
creator's OWN Modal account (modal.com; Modal bills them per second), so their computer stays free. It is not faster
for one short video; it renders off their computer, several at once. Studio still writes the scripts and makes the voice and pictures
here with their Claude Code, so Studio must stay open.

What they asked for: $ARGUMENTS

1. **Check** with `setup_check` and `cloud_balance`. If Studio Cloud isn't available, say why (its `why`). Without a
   Modal account, show them `setup_page` (Setup → Render in the cloud): make a Modal account, add a card there only
   for 3D, create a token (Settings → API Tokens) and paste both parts on that page, never in chat. Or offer the
   make-videos skill on this computer instead.
2. **Titles.** Up to 5, one line each. From a topic, suggest 5 strong titles and let them edit the list.
3. **Style.** Only styles whose `renders_on` includes studio_cloud: `list_styles(kind='cloud')`, then the gallery
   (`gallery_url`, see make-videos "Showing pages") and `get_style_pick(pick_id, wait_seconds=90)`. If they pick one
   that renders on this computer only, say so and offer that or another style.
4. **Length.** Minutes per video (1 to 60).
5. **Cost.** `quote(style, minutes, titles=N, where='studio_cloud')`. Say its `summary`: the expected Modal machine
   cost on their own Modal bill, the most each video can use, the media on their accounts, and (the first time) that
   Studio sets up its machines on their Modal account, about 5 to 10 minutes once. (ChannelRecipe's team may see a
   balance and `short_by_usd` instead: then give the `topup_url` and wait.)
6. **Start** only after a clear yes: `make_videos(titles, style, minutes, where='studio_cloud', quote_id=…)`. Claude
   Code asks them to approve the tool call.
7. **Follow.** `progress(id='cloud')` shows the state, the machine's last lines and finished videos with download
   links. Check now and then, not in a loop; Studio shows a notification when they're done. Finished videos can be
   downloaded from their links or saved to Projects on the Studio page (open_in_studio(page='projects')).
