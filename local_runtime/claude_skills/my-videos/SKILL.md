---
name: my-videos
description: Show the creator's ChannelRecipe Studio videos, what is still being made, play a finished one and resume one that stopped. Use when they ask about their videos, progress, a stopped or missing video, or want to watch or find a video file.
argument-hint: "[video title]"
---

# My videos

Use ChannelRecipe Studio's tools (the `studio` server: `mcp__studio__…` in a Studio folder, `mcp__plugin_channelrecipe_studio__…` from the ChannelRecipe plugin). Free, except carrying on a stopped video (step 4).

1. `progress()` shows what is being made right now (this computer, Studio Cloud and style studies), with percent and
   the last step. Say it in one or two lines a video.
2. `list_videos()` lists finished videos, newest first: title, style, date, size, file path and `watch_url`;
   Studio Cloud videos have a `download_url`.
3. To watch one ($ARGUMENTS, or the newest): open its `watch_url`. In the Claude desktop app, open it in the
   Browser pane beside the chat (preview_start with the url); in a terminal, call
   `open_in_studio(page='video', id=…, open_browser=true)`. `page='folder'` shows the file in Finder or Explorer,
   `page='project'` opens its editor in Studio.
4. A video that stopped before finishing (a provider hiccup, a closed or updated Studio): `progress()` lists it under
   `stopped`. Tell them what stopped it (`progress(id)`). If they want it carried on, `resume_video(id)`: it reuses
   everything already made, so only the rest is paid for, and Claude Code asks them to approve it first.
5. If Studio isn't running, `setup_check` says how to open it.
