---
name: clone-style
description: Clone a video style from a YouTube channel or video into ChannelRecipe Studio, so new videos can be made in that look.
disable-model-invocation: true
argument-hint: "[YouTube channel or video link]"
---

# Clone a style from YouTube

Use ChannelRecipe Studio's tools (the `studio` server: `mcp__studio__…` in a Studio folder, `mcp__plugin_channelrecipe_studio__…` from the ChannelRecipe plugin). Studio learns the visual language (pictures, motion, edit, captions,
sound), never the channel's scripts or footage. Speak plainly and never read .env, .mcp.json or .cr-* files.

Link: $ARGUMENTS

1. **Check Studio** with `setup_check` (see the make-videos skill for what to say when it isn't running or ready).
2. **The link.** Use the link above or ask for a YouTube channel (@name) or one video. To **combine** two or three
   channels into one new style (cross-niche arbitrage), take all their links.
3. **Look first (free).** Call `clone_style_look(youtube_url)`, or `clone_style_look(youtube_urls=[…])` to combine.
   Studio lists the videos and looks at three of the most viewed, taking them in turn from each channel. No Claude
   is used and nothing is charged. Tell them which videos it is studying; if they prefer others, call it again
   with `reference_ids` from `other_videos`. When combining, write the study's `brief` in step 7: what to take
   from each channel (for example "the storytelling and pacing of channel A, the 3D look of channel B").
4. **Wait for the look.** Check `progress(id=look_id)` about once a minute (a few minutes in all). Say what it's doing.
5. **Their answers.** When the look is done, ask each of its `questions` (a multiple-choice question works well;
   Studio's guess is the first option). Also ask:
   - a name for the new style (for example "My History Style");
   - an example video title in the channel's subject.
   Mention `suggested_base` and `because` in one line: the recipe the new style builds on.
6. **Confirm.** Explain: the study runs on their Claude Code for about 15 to 30 minutes and uses one of their plan's
   style clones. Ask "Shall I start the study?".
7. **Start.** After a yes, call `clone_style` with look_id, style_name, example_title, answers (and the brief
   when combining). Claude Code asks
   them to approve the tool call. If it returns a `medium_gap`, read it to them; only with their OK call again with
   `accept_medium=true`.
8. **Progress.** Give the page link (`page`) and say Studio shows a notification when the style is ready. Check
   `progress(id=op_…)` when they ask. When it's ready the style appears in `list_styles(kind='mine')`; offer to make
   a first video with it (the make-videos skill). Styles learned from a channel render on this computer.

If the plan's style clones are used up, Studio says so: point them to https://channelrecipe.com/app#billing.
If a study stops, read what Studio said; never start it again by yourself.
