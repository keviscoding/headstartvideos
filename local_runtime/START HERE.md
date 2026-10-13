# Set up ChannelRecipe on this computer

1. Sign in at https://channelrecipe.com/app#local-studio and download the studio.
2. Extract the whole ZIP. Open **Start on Mac.command** or **Start on Windows.cmd**.
   Keep the launcher open. First launch installs libraries and Chromium, then
   opens http://127.0.0.1:7860/cr-setup.
3. Follow setup: connect CR, install/sign in to Claude Code, connect CR tools,
   save the writing/visual models, and verify both. Claude calls use the signed-in
   Claude Code account and its limits.
4. Connect media accounts. Kie or Atlas can supply voice, images, image animation
   and supported presenters. Stock and specialist services need their own accounts.
   Enter keys in setup forms, never in chat.
5. Select a video recipe and check its requirements. Missing required tools/accounts
   block generation. Explicitly disable a feature only if you do not want it;
   missing features are not silently removed from your video.
6. Open Create, choose a recipe, title and duration, review options, and create your video.
   ChannelRecipe researches, writes, directs, generates assets,
   reviews complex visuals and assembles the result locally.
7. Review the result and timeline. Videos, thumbnails and exports are in
   **Projects** and the local **output** folder.

For Claude Code conversation control, open Claude Code from this extracted
folder and approve its project ChannelRecipe MCP connection. Ask it to use CR's
production tools. It starts saved projects with the returned cr_run.py command.

Reopen the launcher to use saved work. Successful stage responses and media IDs
are cached. Stopped or uncertain Claude work requires review; setup offers one
explicitly acknowledged recovery attempt, never an automatic paid retry.

Required: Python 3.10+, Node.js LTS, subtitle-capable FFmpeg, and Claude Code
2.1.268+. The launcher installs libraries and Chromium. Setup explains missing
tools. Use official installers; on Windows add Python to PATH.

The API-brain form is an explicit alternative using your saved API balance.
Saving media accounts alone does not change your selected brain.

Access remains restricted to CR admins. This owner download includes private production source and workflows; keep it private.
