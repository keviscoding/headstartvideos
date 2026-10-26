---
name: clone-style
description: Clone a video style from a YouTube channel or video into ChannelRecipe Studio, so new videos can be made in that look.
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
   Studio's guess is the first option). Ask `made` first: it decides how the videos are made. Written in code
   (recommended) has their Claude Code write the channel's own renderer during the study, for any kind of channel
   (usually 5 to 15 more minutes of their Claude usage, no money), and its videos cost their voice. Instead of code
   gives Studio's ready-made pictures (AI pictures, some AI clips) or its fixed 3D engine: ready at once, but not
   exactly the channel's look. Say both, plainly. If `look_progress` shows a `medium_gap`, tell them now what their
   videos will be (`how_made`). A Shorts channel's videos come out vertical with its captions. Once the style is
   saved, they can keep improving it with you in Style Lab (see below). Also ask:
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
   `progress(id=op_…)` when they ask. When it's ready the style appears in `list_styles(kind='mine')`; for a style
   written in code, read `animation.what_to_tell_them` first, and offer to make it closer in Style Lab (below) before
   or after a first video (the make-videos skill). Styles learned from a channel render on this computer.

If the plan's style clones are used up, Studio says so: point them to https://channelrecipe.com/app#billing.
If a study stops, read what Studio said; never start it again by yourself.

## Style Lab: improve a style's renderer

For a style drawn by its own renderer (animated 2D or 3D, or any channel written in code), the creator can ask you to
make it look more like the channel. Use the `style_lab` tool:
1. `action: start` with the style id: a workspace folder with `renderer.js`, `scene.json` (a short test scene),
   the channel's frames (`refs/`) and `GUIDE.md`. Read GUIDE.md and look at the frames first.
2. Edit `renderer.js` (and `scene.json` to show what you're working on) yourself: anything goes, inside the guide's
   sandbox rules.
3. `action: preview`: Studio checks the code (it runs, frames are a pure function of time, speed, the manifest,
   sound names) and draws test shots plus a 5 s video. Show the creator the shots and the video, side by side with
   the channel's frames. Fix the problems it lists.
4. `action: check`: the likeness judge scores the shots against the channel's frames and its bar, and says what
   still differs. Its score moves about half a point either way between runs: don't chase it.
5. `action: save` when the creator is happy: every new video in the style uses it. No money is spent at any step.
