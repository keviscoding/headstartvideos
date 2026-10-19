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
   Never run `cr_studio.py` yourself inside a command or a background shell:
   when that command ends or times out, Studio closes, and any video it is
   making stops with it. Only the launcher's own window keeps it running.
5. **Open setup.** When the launcher says it's ready, open
   http://127.0.0.1:7860/cr-setup and tell the customer, in plain words, it's
   three buttons, top to bottom:
   1. **Connect my ChannelRecipe account**: it opens channelrecipe.com; they
      sign in if asked and press Approve, then land back on setup. No key to
      copy. (If that fails, "Paste a key instead" takes the key from the
      Studio page on channelrecipe.com.)
   2. **Set up Claude for videos**: connects Claude Code, picks the models and
      runs a tiny check. Claude Code must be signed in first (`claude`, then
      the browser login).
   3. **Paste a Kie.ai key** in step 3 (get one at https://kie.ai/api-key) and
      press the connect button.
   Keys go into the setup page only, never into this chat.
6. **First video.** When setup says ready, open http://127.0.0.1:7860/, pick a
   recipe, type a title and press Create video.

If they get stuck, suggest the ChannelRecipe Discord (https://discord.gg/aUbazAAqw3),
where the team answers setup questions.

## If they ask you to update Studio

Never download a new ZIP into a second folder: that hides their projects and
settings. Update in place instead:

1. Make sure no video is running, then ask the customer to close the Studio
   launcher window (or confirm it's closed: nothing should answer on
   http://127.0.0.1:7860).
2. From this folder run the studio's own Python: Mac
   `.venv/bin/python cr_update.py`, Windows `.venv\Scripts\python cr_update.py`.
3. Tell them what it printed. It keeps their output, accounts, keys, learned
   styles and any files they edited (new versions of edited files are saved
   beside them with `.new`). If it stops, nothing was changed or it restored the
   previous version — read the message to them.
4. Start Studio again with the launcher.

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

## Ranking Shorts and the Clip Board

Ranking Shorts is a Shorts recipe: real viral clips counted down to #1. In Create, pick Ranking Shorts, type what to
rank (for example "Funniest Dog Moments"), choose how many clips, and set the switches (narrator lines, vine booms,
meme sound effects, punch-ins, captions). Studio searches YouTube, has every candidate watched for its best moment,
picks and trims the clips, writes the narrator's lines, designs the sound and edits the Short.

To choose the clips yourself, open http://127.0.0.1:7860/cr-ranking (the Clip Board): find clips for a topic, hover a
card to watch its moment, tick 3 to 10 clips, drag them into order (the last one is #1), drop in your own clips if you
like, and press Make this Short.

Finding clips uses the plan's monthly clip watches (about 30 a Short). If they run out, the Short stops and says so;
the customer can turn on "Use credits if my clip watches run out" (0.02 credits a watch) or wait for next month. Never
turn that on for them. The narrator speaks with ElevenLabs v3 when an ElevenLabs key is set in setup (otherwise Gemini
through Kie). Footage belongs to the people who filmed it; the channel owner decides how they use it.

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
