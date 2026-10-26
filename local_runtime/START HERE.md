# Set up ChannelRecipe Studio

## Easiest: let Claude Code do it

1. Open this folder in Claude Code. In the Claude desktop app, choose **Code**
   and select this folder (or run `claude` inside it from a terminal). On
   Windows, Extract All puts this folder inside another one named after the
   download: choose this inner folder, the one with this file in it, or
   Studio's tools won't load.
2. Paste: *Help me install ChannelRecipe Studio on this computer. Check what is
   missing, install it for me one step at a time, then start the studio and open
   its setup page.*
3. Follow along. Claude explains each step and asks before it runs anything:
   press Allow (Bypass permissions isn't needed). Enter keys only on the setup
   page, never in the chat.

## Or do it yourself

1. Install **Python 3.13** if you don't have it: https://www.python.org/downloads/
   (on Windows, tick **Add python.exe to PATH**). It's the only tool you install
   yourself: Studio installs the rest from its setup page (step 3).
2. Open **Start on Mac.command** (if macOS blocks it: right-click → Open) or
   **Start on Windows.cmd** (if Windows asks whether to run it, press **Run**:
   Studio then clears that warning so it doesn't ask again). Keep that window
   open. The first launch downloads about 300 MB and takes a few minutes.
3. Your browser opens the setup page at http://127.0.0.1:7860/cr-setup:
   - if FFmpeg (video tools) or Node.js (YouTube helper) is missing, Studio
     downloads it into its own folder by itself while the page is open (no
     password); only if it says it couldn't, press **Fix it** next to it,
   - connect your ChannelRecipe account,
   - Claude: Studio needs Claude Code's command-line tool (the Claude desktop
     app alone isn't enough). Press **Install Claude Code** (Anthropic's
     official installer), then **Open the sign-in window** and sign in with your
     Claude Pro or Max account, then verify your models,
   - connect Kie.ai or Atlas Cloud for voice and images.
4. Open the studio, pick a recipe, type a title and press **Create video**. Or make it from Claude Code (next
   section).

## Make videos by asking Claude Code

Keep Studio running (its launcher window open), open Claude Code in this folder and say what you want, or type a
command:

- **/make-videos**: pick a style from a gallery, see the cost first, make up to 5 videos.
- **/niche-strategy**: channel ideas from ChannelRecipe's live niche database, with the evidence.
- **/clone-style**: clone a YouTube channel's look, or combine two or three channels into one new style.
- **/digital-product**: a product for your niche (the book, its landing page and checkout).
- **/my-videos**: what's being made, and your finished videos.

Claude Code asks you before anything that costs money. The first time you open this folder it asks whether to
trust it: say yes, so Studio's tools can work without asking every time.

Your videos, thumbnails and exports are in **Projects** and in this folder's
`output` folder. To use Studio again later, open the launcher again.

Fast mode (quick 3D graphics) is optional and needs Python 3.13+. Keep it off
until its setup check passes.

## Need help?

Ask in the ChannelRecipe Discord: https://discord.gg/aUbazAAqw3. We answer setup
questions there every day, and you can share your videos there too.
