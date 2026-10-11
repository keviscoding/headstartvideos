# ChannelRecipe local studio

This is the admin preview of the current Frontier engine connected to ChannelRecipe.

1. Extract the whole downloaded ZIP into a folder you can find again.
2. Open **Start on Mac.command** or **Start on Windows.cmd**. Keep its window open while making videos. The first launch installs the video tools' Python libraries and browser; later launches reuse them.
3. The setup page opens in your browser. Copy your **connect key** from ChannelRecipe's Local studio page into its account form. This is separate from your Google/provider API keys.
4. In **Choose your brain**, select your own API account and writing/visual models. Claude Sonnet 5.5 and Opus 5.5 are available through Anthropic and Kie.ai; Atlas has its own named variants. A separate visual choice preserves Frontier's specialist model roles. Direct Anthropic supports research with web search; Kie/Atlas Claude search support is not verified, and that step stops rather than inventing sources. Existing Gemini connections stay selected until you change them.
5. Choose **Kie.ai** or **Atlas Cloud** for media and paste your API key. It connects the checked media tasks. This leaves your brain account in place. To replace it with the same key, explicitly check **Also use this account for the CR brain** and choose its brain models. Brain keys are stored encrypted in CR; media keys stay local. Or choose **Keep individual Frontier providers**. Check provider balances and prices before production.
6. Check the spending agreement and open your studio. Pick a style, give it a title and make your first video.

Need Python? Install Python 3.12 from https://www.python.org/downloads/ (Windows: select Add Python to PATH). Need FFmpeg/Node? Open this folder in Claude Code and ask it to help with the missing setup checks. It can read the included CLAUDE.md. FFmpeg, Node and Python are free tools; AI providers have their own charges.

If macOS blocks opening the launcher, use right-click → Open. If you moved the folder, first launch may need to rebuild its Python environment. Don't move it while a video is running.

Use the **Setup & accounts** link at the top of the local studio to check your connection. Keep your connect key private. No model/generation API keys are included in this download.

The engine uses your provider accounts. CR brain token usage is recorded in output/cr-receipts and in your CR project history. Model-account media task IDs are saved in .cr-tasks folders beside assets. Resume a pending task using its saved ID. An uncertain submission requires checking the provider log before retrying. A fixed per-video price is not promised during the preview.

The single-account path covers narration, standard images/reference edits, Seedance image animation and Kling Standard/Pro presenters. Narration uses your setup voice, replacing preset voice selections; private clones cannot be assumed to transfer. Stock footage, licensed music, other presenter models, special effects, upscalers and optional specialised paths need their original accounts. Uncheck a replacement to use its individual provider. Claude Code's assistant account remains separate.

This package is only for CR admins. It retains original Frontier source for migration testing. Do not distribute it to customers; their private-brain runtime is a separate release.

Choose narration separately in **Setup & accounts → Choose how your voice is made**. Kie.ai offers Gemini 3.8 Flash voices and ElevenLabs Turbo 2.5 using your Kie key and balance; a separate ElevenLabs key is not needed. Atlas Cloud is also available. Leave the key blank to use a saved account. This choice does not switch your CR brain or image models. Voice generation is paid; checking an account only validates access.
