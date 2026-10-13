# Set up ChannelRecipe Studio

## Easiest: let Claude Code do it

1. Open this folder in Claude Code. In the Claude desktop app, choose **Code**
   and select this folder (or run `claude` inside it from a terminal).
2. Paste: *Help me install ChannelRecipe Studio on this computer. Check what is
   missing, install it for me one step at a time, then start the studio and open
   its setup page.*
3. Follow along. Claude explains each step. Enter keys only on the setup page,
   never in the chat.

## Or do it yourself

1. Install these if you don't have them:
   - **Python 3.13** — https://www.python.org/downloads/ (on Windows, tick
     **Add Python to PATH**)
   - **Node.js LTS** — https://nodejs.org
   - **FFmpeg with subtitle support** — Mac: `brew install ffmpeg-full`;
     Windows: `winget install -e --id Gyan.FFmpeg`
   - **Claude Code** — https://claude.com/download (sign in with your Claude
     Pro or Max account)
2. Open **Start on Mac.command** (if macOS blocks it: right-click → Open) or
   **Start on Windows.cmd**. Keep that window open. The first launch takes a few
   minutes.
3. Your browser opens the setup page at http://127.0.0.1:7860/cr-setup:
   - paste your ChannelRecipe key (Studio page on channelrecipe.com),
   - sign in to Claude Code and verify your models,
   - connect Kie.ai or Atlas Cloud for voice and images.
4. Open the studio, pick a recipe, type a title and press **Create video**.

Your videos, thumbnails and exports are in **Projects** and in this folder's
`output` folder. To use Studio again later, open the launcher again.

Fast mode (quick 3D graphics) is optional and needs Python 3.13+. Keep it off
until its setup check passes.

## Need help?

Ask in the ChannelRecipe Discord: https://discord.gg/aUbazAAqw3. We answer setup
questions there every day — and share your videos when they're done.
