---
name: my-videos
description: Show the creator's ChannelRecipe Studio videos, what is still being made, play a finished one, resume one that stopped or delete one. Use when they ask about their videos, progress, a stopped or missing video, want to watch or find a video file, or want a video gone.
argument-hint: "[video title]"
---

# My videos

Use ChannelRecipe Studio's tools (the `studio` server: `mcp__studio__…` in a Studio folder, `mcp__plugin_channelrecipe_studio__…` from the ChannelRecipe plugin). Free, except carrying on a stopped video (step 4); deleting (step 5) is asked too.

1. `progress()` shows what is being made right now (this computer, Studio Cloud and style studies), with percent and
   the last step, and the videos waiting in Studio's queue with their place in line. Say it in one or two lines a
   video. Videos on hold say why (`on_hold_because`, also in `queue`): tell them what to do, and call
   `carry_on_queue` only when they ask (it's asked too).
2. `list_videos()` lists finished videos, newest first: title, style, date, size, file path and `watch_url`;
   Studio Cloud videos have a `download_url`.
3. To watch one ($ARGUMENTS, or the newest): open its `watch_url`. In the Claude desktop app, open it in the
   Browser pane beside the chat (preview_start with the url); in a terminal, call
   `open_in_studio(page='video', id=…, open_browser=true)`. `page='folder'` shows the file in Finder or Explorer,
   `page='project'` opens its editor in Studio.
4. A video that stopped before finishing (a provider hiccup, a closed or updated Studio): `progress()` lists it under
   `stopped`. Tell them what stopped it (`progress(id)`). If they want it carried on, `resume_video(id)`: it reuses
   everything already made, so only the rest is paid for, and Claude Code asks them to approve it first.
   - `stopped_on` is `claude_stage` or `media` (a Claude step or a paid media task stopped and would stop again):
     read them `what_to_tell_them` and each stuck item's reason, and whether their Claude usage or money may already
     have gone on it. Only after they say yes, `recover_video(id, key=recovery_key)`: it allows exactly those
     attempts and carries the video on, reusing everything else. Do the `fix_first` items first (signing Claude Code
     in, for one). When `usage_resets` is still ahead, offer
     `recover_video(id, key=recovery_key, when='after_reset')`: Studio carries it on by itself after the reset. Never
     approve for them.
   - `carries_on_by_itself.would` true: Studio carries it on by itself within a minute or so (a closed Studio or a
     crash on this computer, nothing uncertain): nothing to do.
5. To delete a video they ask to delete: say which one goes (its title from `list_videos` or `progress()`) and that
   it waits in Studio's trash for 30 days (Recently deleted in Projects puts it back), then
   `delete_video(id, title)`. Claude Code asks them to approve. Their voice library, styles and other videos stay.
   A video still being made is refused.
6. How much Claude their videos used ("how many tokens", "how much of my plan"): `claude_usage()` gives today, the
   last 7 days and all time, and the videos that used the most; `claude_usage(id)` one video. Say what was used. Claude
   Code doesn't say how much of their plan is left, so never give a percentage; Studio's Projects page shows the same.
7. If Studio isn't running, `setup_check` says how to open it.
