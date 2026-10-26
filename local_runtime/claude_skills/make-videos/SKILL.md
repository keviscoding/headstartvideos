---
name: make-videos
description: Make one to five videos with ChannelRecipe Studio. Use whenever the creator wants to make, create or start a video. Pick a style (visual gallery), choose this computer or Studio Cloud, see the cost, start, then watch them.
argument-hint: "[titles or video idea]"
---

# Make videos with ChannelRecipe Studio

The creator wants videos. Use ChannelRecipe Studio's tools (the `studio` server: `mcp__studio__…` in a Studio folder, `mcp__plugin_channelrecipe_studio__…` from the ChannelRecipe plugin): they drive the creator's own Studio, with the
same checks as its page. Speak plainly, one step at a time, and never read .env, .mcp.json or .cr-* files.

What they asked for: $ARGUMENTS

1. **Check Studio.** Call `setup_check`.
   - Not running: tell them to open Studio with its launcher ("Start on Mac.command" or "Start on Windows.cmd" in
     this folder) and keep that window open. On a Mac you may offer to run `open "Start on Mac.command"` for them.
     Wait until `setup_check` says running.
   - Blockers: read them out in plain words. If one needs the setup page, open `setup_page` (see "Showing pages").
     A blocker that only asks to verify the Claude models fixes itself when the video starts.
2. **Titles.** Use the titles or idea above, or ask. Up to 5 titles, one line each. If they gave an idea, suggest
   3 strong titles and let them choose.
3. **Style.** If they named one, find it with `list_styles(search=…)`. Otherwise call `list_styles` and show the
   gallery: open `gallery_url` (see "Showing pages") and say "Pick a style in the gallery and press Use this style".
   Then call `get_style_pick(pick_id, wait_seconds=90)`; repeat while it says waiting (tell them you're waiting).
   - When only two to four styles fit, you can ask with a multiple-choice question instead.
   - If they chose "Clone a new style", follow the clone-style skill with their YouTube link, then come back here.
4. **Length.** Minutes for long videos (1 to 60, suggest 8 to 12). Shorts use seconds (options.short_seconds).
5. **Where it renders.** If the style's `renders_on` includes studio_cloud and `setup_check` says Studio Cloud is
   available, get both prices with `quote` (where=this_computer and where=studio_cloud) and ask:
   - **This computer:** no machine to pay; one video at a time (more wait in Studio's queue and start by
     themselves); keeps the computer busy.
   - **Studio Cloud:** a ChannelRecipe machine renders, several side by side; costs the quoted machine time from the
     cloud balance (it reserves the most and charges only what it used).
   Otherwise it renders on this computer; get its `quote`.
6. **Confirm the cost.** Say the quote's `summary` in one or two sentences and ask "Shall I start?". Only after a yes
   call `make_videos` with the same titles, style, minutes, where, options and the `quote_id`. Claude Code then asks
   them to approve the tool call too. Never call it before they agreed to the cost.
7. **After it starts.** Give the ids, say roughly how long (a 10-minute video takes about 30 to 60 minutes on this
   computer; on Studio Cloud about 15 minutes plus 2.5 minutes per video minute) and that Studio shows a
   notification when each is done. Don't wait in a loop: check `progress` when they ask, or now and then.
   - **More while one is made:** on this computer Studio makes one video at a time, and every video they ask for
     joins its queue, even while another is being made (in any style, with any options): call `quote` and
     `make_videos` again. Each video comes back with its place in line (`videos_before_it`) and starts by itself, so
     they can walk away. `queue` shows the line.
   - **On hold:** if something every video needs stops (Claude's usage limit, a balance or key, the disk, memory),
     the videos waiting go on hold instead of each failing the same way, and nothing of theirs is spent;
     `on_hold_because` says why and what to do. Tell them, and only when they've fixed it and ask, call
     `carry_on_queue` (Studio checks each one's setup again; `remove=true` takes them out instead). Never carry on
     for them.
8. **Watch.** When one is done, `progress(id)` or `list_videos` gives `watch_url` and the file path. Open the
   watch link (see "Showing pages"); offer `open_in_studio(page='folder', id=…)` to show the file.

## Showing pages

- **Claude desktop app** (you have the Browser pane tools): open the http://localhost:7860 link in the Browser
  pane beside the chat (preview_start with the url), so the creator sees it next to this conversation.
- **Terminal:** call `open_in_studio` with the page and `open_browser=true`; it opens their browser.

## Good to know

- Options (all optional): captions, thumbnails, thumbnail_count, description, extra_instructions (creative
  direction), script (their own narration, one title), features {key: true/false} (the style's switches, shown by
  `quote` under media), short_seconds, ranking {...} for Ranking Shorts.
- **Captions** start at the style's own choice: a style card with `captions: false` has none by design, like its
  channel. If they want captions on such a style, pass `options.captions: true` and Studio burns them in anyway.
  When a video should have had captions but has none, `progress(id)` gives `captions_note` with the reason: tell them.
- **A different character** (the style's card shows its `character`): pass `options.character` (words: age, hair,
  face, clothes, one recognisable detail) or `options.character_picture` (the path of their picture), rather than
  extra_instructions: the style's picture of its old character would still go with every image, so nothing keeps
  the new face the same from picture to picture. On a style with a
  character picture, new words are drawn once in the style's look (one picture, about $0.02; `quote` says so under
  `character`). Shorts keep their own host.
- ChannelRecipe's own recipes (ids starting cr-), learned styles (cr_clone_…), FOLIO and Manga Recaps render on
  this computer for now.
- Never turn on paid options the creator didn't ask for, and never retry a stopped video by yourself: tell them what
  Studio said and let them choose.
