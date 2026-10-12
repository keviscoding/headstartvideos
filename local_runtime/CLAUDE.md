# ChannelRecipe production studio

Use CR's MCP production workflow and the installed Frontier engine. Do not build
a substitute renderer or hardcode stock/image percentages. Preserve the original
preset, director, scene modules and visual review loops.

For customization, samples, creative direction and specialist features, read
FRONTIER_GUIDE.md and the original README for the selected style/module. The
original creative guidance remains available in this private owner download.
The CR instructions here take precedence over its older setup, prices, account
collection and launch commands. Use setup forms for keys, current provider prices,
cr_studio.py for launch and MCP/cr_run.py for production videos. Do not invoke
standalone generative Python commands to bypass the CR project/stage checks.

Begin with local_studio_setup and local_studio_prepare. If setup has not passed,
guide the creator to http://127.0.0.1:7860/cr-setup. Never request keys in chat or
read .env, .cr-connection.json, .mcp.json or login files into conversation.

After the creator checks a preset, .cr-setup-proof.json contains only safe setup
fields, style and feature choices. Read it, then call local_studio_begin with
payload containing setup, a unique begin_id, title, style, minutes, features and
optional original video options. Use their preset and choices; defaults belong
to Frontier. Never impose footage or animation ratios.

Run the returned launch command from this folder using the local terminal.
It contains only a project ID. The local studio must be running; cr_run.py
rechecks setup and options before queuing. Use local_studio_progress with
payload containing project_id to inspect stage progress, usage and outputs.

Each original Claude Code stage retains its system/user instructions, tools,
effort, vision inputs and response formats. CR authorizes stages through MCP and
pins the workflow and model profile. Do not use hosted generate_script or an API
brain as a local fallback. Claude Code uses the signed-in creator's account;
media uses their provider balances, separately from the CR subscription.

Completed stage outputs and media tasks resume from caches. Do not delete
receipts or rerun uncertain work. The local setup recovery section offers one
further attempt after the creator checks usage, confirms no active job and
explicitly accepts additional usage. Never approve retries automatically.

Help review the final video, timeline, thumbnails and exports. Model identity
alone does not guarantee equal quality; inspect actual output when judging it.
Finished 3D videos have Review 3D scenes in the local video library. Use it to
inspect the final clips, repair selected or flagged scenes with original Claude
direction and verification, then update the final video from the repaired cache.
Each inspection or repair has a separate owned creative project. For terminal
work, use cr_worker.py --project SOURCE_PROJECT_ID -- threed.py qa JOB or redo
JOB with the original scene IDs/options; never invoke raw threed.py generation.
Stopped or uncertain operations require review; do not rerun them automatically.
Sync saved completion only reconciles receipts and does not generate again.
This owner package remains admin-only and retains original private source.
