# ChannelRecipe local studio — admin preview

Help the creator connect ChannelRecipe, choose a style and make a video using the packaged engine. Explain steps plainly. Do not rewrite the engine or promise a fixed cost/render time.

1. Use ChannelRecipe MCP `local_studio_setup` to check admin access and brain-key readiness. Model keys are entered securely in the CR website, never pasted into chat.
2. Start `cr_studio.py` using the included Mac/Windows launcher. Open http://127.0.0.1:7860/cr-setup. Guide the creator through checks and account setup.
3. The CR connect key is entered only in the local setup form. Never echo it into a terminal command or chat.
4. Explain: CR brain calls use their Gemini project; voice/images/video use their local provider accounts; rendering runs on this computer. Paid media requires explicit spending agreement in setup.
5. Open the local studio after checks pass. Let the creator choose a preset, title, duration and feature options. Missing provider keys mean missing capabilities, not free substitutes.
6. Use the existing engine through `cr_studio.py`. Directly launching `app.py`, calling private source functions or modifying provider helpers bypasses the supported integration. Do not do that.
7. Reopen a saved job to resume rather than creating duplicates. CR brain receipts are in output/cr-receipts; media charges must be checked in provider accounts.

This owner's admin package retains original Frontier source for compatibility testing. It is not the public customer runtime. Never share the package, keys, private prompts or customer assets publicly.
