# ChannelRecipe local studio

This is the admin preview of the current Frontier engine connected to ChannelRecipe.

1. Extract the whole downloaded ZIP into a folder you can find again.
2. Open **Start on Mac.command** or **Start on Windows.cmd**. Keep its window open while making videos. The first launch installs the video tools' Python libraries and browser; later launches reuse them.
3. The setup page opens in your browser. Copy your **connect key** from ChannelRecipe's Local studio page into its account form. This is separate from your Google/provider API keys.
4. Add your voice and image accounts in the local setup page. Keys stay on this computer. Check each provider's balance and prices before production.
5. Check the spending agreement and open your studio. Pick a style, give it a title and make your first video.

Need Python? Install Python 3.12 from https://www.python.org/downloads/ (Windows: select Add Python to PATH). Need FFmpeg/Node? Open this folder in Claude Code and ask it to help with the missing setup checks. It can read the included CLAUDE.md. FFmpeg, Node and Python are free tools; AI providers have their own charges.

If macOS blocks opening the launcher, use right-click → Open. If you moved the folder, first launch may need to rebuild its Python environment. Don't move it while a video is running.

Use the **Setup & accounts** link at the top of the local studio to check your connection. Keep your connect key private. No model/generation API keys are included in this download.

The engine uses your provider accounts. CR brain token usage is recorded in output/cr-receipts and in your CR project history. Other media charges are recorded by the original engine/providers and must be checked there. A fixed per-video price is not promised during the preview.

This package is only for CR admins. It retains original Frontier source for migration testing. Do not distribute it to customers; their private-brain runtime is a separate release.
