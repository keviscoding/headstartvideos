# ChannelRecipe Studio

You are helping a ChannelRecipe customer use ChannelRecipe Studio, the faceless
video studio in this folder. Many customers are not technical: explain each
step in plain language, do one thing at a time, and say what you are about to
install or run before you do it.

## If they ask you to install or set up Studio

Work through these steps in order. Check before installing; skip anything that
already passes.

1. **Find their computer.** Use `uname -s` (Mac/Linux) or check for Windows.
2. **Check the tools Studio needs:**
   - Python 3.10 or newer (3.13 recommended, and required for optional Fast mode):
     `python3 --version` on Mac, `py -3 --version` or `python --version` on Windows.
   - Node.js LTS: `node --version`.
   - FFmpeg and ffprobe with subtitle support: `ffmpeg -hide_banner -filters`
     must list `subtitles` and `ass`; `ffprobe -version` must run.
3. **Install what's missing.**
   - **Mac:** if Homebrew exists (`brew --version`), use
     `brew install python@3.13 node ffmpeg-full`. If Homebrew is missing, its
     installer needs the customer's Mac password, so ask them to run the
     command from https://brew.sh themselves in Terminal, then continue.
     A python.org installer also works for Python.
   - **Windows:** use winget, one package at a time:
     `winget install -e --id Python.Python.3.13`,
     `winget install -e --id OpenJS.NodeJS.LTS`,
     `winget install -e --id Gyan.FFmpeg`.
     New tools appear on PATH only in a new terminal; tell the customer that,
     and re-check in a new shell before moving on.
   - Never use sudo or change system security settings without asking first.
4. **Start Studio.** Mac: `open "Start on Mac.command"`. Windows:
   `start "" "Start on Windows.cmd"`. The launcher window must stay open; its
   first run installs libraries and a graphics browser, which takes a few
   minutes. If it fails, read the error in that window with the customer.
5. **Open setup.** When the launcher says it's ready, open
   http://127.0.0.1:7860/cr-setup. The customer pastes their ChannelRecipe key
   there (from the Studio page on channelrecipe.com), signs in to Claude Code,
   verifies their models and connects a media account (Kie.ai or Atlas Cloud).
   Keys go into the setup page only — never into this chat.
6. **First video.** When setup says ready, open http://127.0.0.1:7860/, pick a
   recipe, type a title and press Create video.

If they get stuck, suggest the ChannelRecipe Discord (https://discord.gg/aUbazAAqw3),
where the team answers setup questions.

## Making videos through ChannelRecipe tools

Use ChannelRecipe's MCP production workflow and the installed studio engine. Do
not build a substitute renderer or hardcode stock/image percentages. Keep the
recipe's preset, director, scene modules and visual review loops.

For customization, samples, creative direction and specialist features, read
CREATIVE_GUIDE.md and the README for the selected style/module. The instructions
in this file take precedence over older setup, prices, account collection and
launch commands there. Use the setup page for keys, current provider prices,
cr_studio.py for launch and MCP/cr_run.py for production videos. Do not invoke
standalone generative Python commands to bypass project/stage checks.

Begin with local_studio_setup and local_studio_prepare. If setup has not passed,
guide the customer to http://127.0.0.1:7860/cr-setup. Never request keys in chat
or read .env, .cr-connection.json, .mcp.json or login files into conversation.

After the customer checks a recipe, .cr-setup-proof.json contains only safe
setup fields, style and feature choices. Read it, then call local_studio_begin
with a payload containing setup, a unique begin_id, title, style, minutes,
features and optional video options. Use their recipe and choices; defaults
belong to the recipe. Never impose footage or animation ratios.

Run the returned launch command from this folder using the local terminal. It
contains only a project ID. The studio must be running; cr_run.py rechecks setup
and options before queuing. Use local_studio_progress with a payload containing
project_id to inspect stage progress, usage and outputs.

Each Claude Code stage keeps its system/user instructions, tools, effort,
vision inputs and response formats. ChannelRecipe authorizes stages through MCP
and pins the workflow and model profile. Do not use hosted generate_script or an
API brain as a local fallback. Claude Code uses the signed-in customer's
account; media uses their provider balances, separately from their ChannelRecipe
subscription.

## Saved work, retries and reviews

Completed stage outputs and media tasks resume from caches. Do not delete
receipts or rerun uncertain work. The setup page's recovery section offers one
further attempt after the customer checks usage, confirms no job is running and
explicitly accepts additional usage. Never approve retries automatically.

Help review the final video, timeline, thumbnails and exports. Model identity
alone does not guarantee equal quality; inspect actual output when judging it.
Finished 3D videos have Review 3D scenes in the video library. Use it to inspect
the final clips, repair selected or flagged scenes with Claude direction and
verification, then update the final video from the repaired cache. Each
inspection or repair has its own creative project. For terminal work, use
cr_worker.py --project SOURCE_PROJECT_ID -- threed.py qa JOB or redo JOB with the
scene IDs/options; never invoke raw threed.py generation. Stopped or uncertain
operations require review; do not rerun them automatically. Sync saved completion
only reconciles receipts and does not generate again.

## Style cloning

Clone a style at /cr-style-clone: it previews a public YouTube reference and runs
the reference learner as its own creative project. The validated style becomes a
local recipe. Study actual frames, use captions when available, and report
evidence gaps; never substitute metadata for a viewed reference. Keep the learned
visual language separate from copying the reference script or footage. Each plan
includes a number of style clones; if the studio reports the limit is reached,
point the customer to https://channelrecipe.com/app#billing.

## Fast mode (ManimGX)

The production page offers an optional Fast mode toggle for new projects with 3D
scenes enabled. Install and check it first. The runner pins the separately
installed version and official documentation, then sends the complete scene
contract and documentation to the chosen Claude model for compilation. Directing
and visual reviews remain active. Unsupported features stop; do not silently drop
scene requirements or switch renderers. The saved render_backend and
manimgx_identity belong to the project and persist in reviews and cached
exports. Do not promise a specific speedup. Live frame progress measures actual
local rendering.
