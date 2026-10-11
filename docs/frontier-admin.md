# ChannelRecipe local studio: admin migration preview

Version: `5.3-admin.7`. Entry point: `https://channelrecipe.com/app#local-studio`.

This is the first runnable integration checkpoint, **not completion of the full
Frontier migration**. Ordinary users, including paying subscribers, cannot use
its website APIs, download or MCP tools. Access is checked against the current
`ADMIN_EMAILS` on every request, including local-runner Bearer requests.

## What this checkpoint supplies

- An admin navigation entry and five-step onboarding: explanation, download,
  brain selection, account connection and first video.
- A searchable catalog of the 26 main presets in the selected 5.3 package.
- A protected Mac/Windows download retaining the original production engine,
  timeline editor, style files, graphics, 3D libraries and required assets.
- Local setup checks, secure account entry and a spending acknowledgement.
- Text, supported web research and sampled-image review calls routed to CR using
  the admin's own Anthropic, Google Gemini, Kie.ai or Atlas Cloud API key. Separate
  general and specialist visual models are selected explicitly. No CR generation
  key is used as a fallback.
- An optional one-key model account in the local setup: checked narration,
  images/reference edits, Seedance motion and Kling Standard/Pro presenter tasks
  go directly to Kie.ai or Atlas Cloud. The same key can connect the CR brain
  with explicit form consent. Unchecked tasks retain their original providers.
- Encrypted brain keys, owned project records, provider-reported token receipts,
  request idempotency, bounded concurrency and a per-project request ceiling.

Large rendering, media generation, Whisper and browser rendering remain local.
CR does not install the downloaded engine into its web container. The preview
allows two active brain calls per web process; project limits and request records
are shared in PostgreSQL. This is bounded admin capacity, not a distributed
production queue or a demonstrated customer scaling limit.

## Deliberate admin-only boundary

The current preview download contains original private prompts and style rules.
The generic `/api/frontier/brain` relay is **not** the target private-brain
architecture. Do not relax admin access or sell this bundle as the customer
runtime. Public release requires named server-side stages, a private engine
deployment and an explicit execution-only package allowlist.

The GitHub repository is public. Only integration code, catalog descriptions,
tests and packaging instructions belong in it. Never commit the source archive,
extracted engine, private stage templates, model keys or generated bundle. Do not
publish the bundle to GitHub releases or use the public media upload helper.

## Building and publishing the private download

The selected source is `Frontier-5.3-FULL.zip` with SHA-256
`e379dc601672282040a2adbf30f7bf7f7d34fcdf38154e5f6a24ec6c2528cadf`.
The builder rejects a different source. It excludes samples, generated jobs,
user presets, environment files and the old agent instructions. Third-party
asset notices are retained. It writes a version/manifest into the bundle.

```sh
python scripts/build_frontier_admin.py --source /private/path/Frontier-5.3-FULL.zip
```

The output defaults to git-ignored `data/frontier/channelrecipe-5.3-admin.7.zip`.
`--upload` publishes to the private Spaces object
`private/frontier/channelrecipe-5.3-admin.7.zip`. It checks both the object ACL
and unauthenticated origin/CDN access, testing a small canary before uploading
the engine. Failure removes the uploaded release. Never print storage credentials.

Downloads pass through CR's authenticated endpoint with `private, no-store`
headers. No public object URL or signed URL is returned. For local development,
set `FRONTIER_RELEASE_PATH` to the generated ZIP's absolute path.

## Brain-key storage and billing

Set `FRONTIER_SECRETS_KEY` to a dedicated Fernet key in the service's secret
environment. Preserve it across deployments and back it up privately. It is
separate from the existing credential encryption key so this feature does not
rotate unrelated HeyGen credentials. Without it, key saving fails before any
provider call. Rotation requires decrypting/re-encrypting the frontier credentials
or asking admins to reconnect; replacing the key silently is not a rotation.

Admins connect Anthropic, Google Gemini, Kie.ai or Atlas Cloud through the CR
brain form, or paste a hub key once in the local setup and select its brain checkbox.
Read-only model/account lookup verifies API access without generating content.
The shared `webapp/frontier/providers.json` catalog supplies the allowed model IDs,
protocols, model labels and research support for the website and packaged runner.
Claude uses native Messages requests and streaming through the selected account.
Kie Gemini uses its model-specific Chat Completions endpoint, including googleSearch
for research; Atlas Gemini uses native contents/tools and multimodal parts.
Connection is not a promise of credit availability or production quality.

| API account | General / specialist Claude choices | Hosted research on its Claude route |
| --- | --- | --- |
| Anthropic | `claude-sonnet-5-5` / `claude-opus-5-5` | Native web search |
| Kie.ai | `claude-sonnet-5-5` / `claude-opus-5-5` | Unverified; stopped before submission |
| Atlas Cloud | `anthropic/claude-sonnet-5.5-ccmax` / `anthropic/claude-opus-5.5` | Unverified; stopped before submission |

New setup forms offer Sonnet 5.5 for writing, planning and research, and Opus 5.5
for demanding visual work. Saving the form makes that profile explicit. Existing
Gemini connections keep their saved model; an API request that omits the specialist
model uses its general model for both roles. Media account changes leave the brain
in place unless the user explicitly selects the brain checkbox and model choices.
The bridge carries the original stage's requested model as routing metadata:
Opus requests use the chosen specialist model; other requests use the general model.
This preserves the original role distinction for Folio and 3D without introducing
new creative choices. Image review by itself does not force every call onto Opus.

The account owns the API charges. An Anthropic API key uses that account's API
billing; it does not use a Claude Code or Claude subscription login. Kie and Atlas
charges use their respective API balances. Claude Code can remain an optional
assistant for local setup, but there is no production option to bypass CR's brain
with a local Claude Code login. No Claude OAuth credentials are collected. Direct
Anthropic brain keys entered locally are sent to CR's encrypted per-user vault
and are not saved to the local media environment.

Anthropic research exposes hosted web search with a bounded five-use setting.
The relay does not provide the original CLI's local tool execution, WebFetch tool,
or automatic `pause_turn` continuation. A tool error, pause or incomplete answer
stops for review. Kie/Atlas Claude research is rejected before a paid provider
request because the supported contracts do not establish hosted search. Users can
choose direct Anthropic, or a catalog model with verified research support; CR
never silently changes the account or model to obtain it.

Media keys are stored locally with private file permissions. They travel to CR
only when the user explicitly selects the same account for the brain. The local connect key is the existing personal
CR MCP key; users can revoke it using the existing rotation control. Never ask
for keys in chat or pass them as shell command arguments.

Receipts show reported brain tokens, including thinking tokens where reported.
Claude's output count already includes reasoning; a separate reasoning count is
not invented. Cache-read and cache-write input tokens are included in the input
total, with separate safe diagnostic fields when reported.
They are not an exact total dollar bill. Media, search, interrupted requests and
provider subscriptions can have additional charges. Provider usage reports are
the source of truth. The preview does not enforce a cross-provider dollar budget.

Gemini 3 brain calls explicitly use `low` thinking, matching the original
engine's preference for limited reasoning on script and review helpers. Gemini
3.8 cannot fully disable thinking; its default `medium` setting can consume the
same output budget as the answer. Older/custom Google models keep their existing
settings. Kie uses its documented `reasoning_effort: low`; hiding thoughts alone
does not limit their generation. Output budgets and project request ceilings
are unchanged. Google and Atlas native responses are consumed as SSE streams,
with reported cumulative usage counted once and a terminal finish reason required.
Only a complete answer is stored; partial text and thinking are discarded.

Claude keeps its model reasoning defaults; Gemini's `low` reasoning settings do
not apply to Claude. Only a terminal complete Messages stream supplies an answer;
reasoning, partial text and upstream error bodies are not stored as results.

The provider's idle read timeout remains 180 seconds. Active native streams are
checked against a 180-second Gemini or 600-second Claude generation window.
The packaged bridge allows 650 seconds for a brain response and 220 seconds for
other CR responses. An outstanding blocking provider read can outlast its stream
window until the idle timeout. This does not detach the compatibility relay from
App Platform or local HTTP timeouts. If the local
connection stops while CR is finishing, review its recorded status before
resuming. A persistent job submission/polling contract remains migration work.
These controls improve latency but do not guarantee provider availability or
prove original-vs-integrated output quality across every style.

The hub profile replaces tasks, not every model in the engine. Its catalog is
shared between the server and packaged runner. Defaults use Nano Banana for
images/edits, ElevenLabs for narration, Seedance V1 Pro Fast (720p, five seconds) for Kie image motion, and Kling for
presenters. The hub's setup voice replaces preset voices. Private clones, licensed
music, stock/search services, other avatar models, effects and upscalers do not
automatically transfer. Unsupported specialised substitutions stop with guidance;
they do not silently change wallets. Original per-provider fallback behavior can
still apply to tasks the user explicitly leaves on individual providers.

Hub media jobs have at most two active calls on the local computer. Creation is
recorded before sending; successful IDs are persisted before polling. Polling
and file downloads can resume without another submission. An uncertain creation
without an ID, or a failed task, stops until an operator reviews the provider log
and deliberately clears that specific receipt. No automatic paid retry occurs.
Receipts contain task IDs and reported usage, not API keys or private prompts.
Uploads go to the chosen provider; downloads never carry its Authorization.
Cached uploads expire after 12 hours. Account changes are blocked during jobs.

## Recovery and limitations

Completed identical requests reuse the stored answer. Capacity rejections are
retried at most four times locally; network/provider failures are not
automatically repeated or sent to a different paid account. An uncertain or
truncated answer leaves the project marked `needs_review`. Resuming that same
request stops to prevent an accidental second charge. The CR page surfaces stopped
requests, safe failure details and any provider-reported usage. Earlier preview
failures can have no retained reason; their charge remains unknown. Private prompts,
partial scripts, keys and upstream bodies are not included in this diagnostic history.
New failures distinguish an idle read timeout, an active stream exceeding its
window and an incomplete stream. Safe diagnostics retain elapsed time, reported
answer/thinking token counts and input lengths, never the input text or images.

An admin can acknowledge a possible additional provider charge and allow one
recovery retry for a specific stopped request. Approval itself makes no provider call.
Resume the same video locally to submit the identical recorded input once. Approval
is tied to the chosen model account, consumed under the project row lock and bounded
by the existing project request limit. Completed answers continue to reuse their
saved results. A failed recovery does not automatically receive another retry.
No new download is required for this server-side recovery flow. The original error
and retry approval remain recorded; no usage counters are reset. This is admin
recovery, not the complete per-stage repair/quote flow needed for public release.

Original provider options and render behavior are preserved, but every preset
and optional branch has not completed real production validation. Existing
farm, learning, product, avatar and 3D files are retained; their presence is not
proof of cloud compatibility or parity. The local bridge changes the reasoning
model, so output parity must be measured before claiming preservation of quality.
Using the same Claude model name does not reproduce the original Claude Code
execution contract. The current generic relay still flattens some separate system
and user prompts, omits specialist effort/cache controls, bounds review images to
eight 1280-pixel samples and does not reproduce all original research tool loops.
These are remaining named-stage migration tasks, not a completed private brain.

The Mac launcher and imported engine were exercised on the development Mac.
The Windows launcher requires testing on a fresh Windows machine. Python,
FFmpeg and Node installation still needs guided help when absent; signed native
installers and automatic prerequisite repair remain onboarding work.

## Verification and next migration work

```sh
python -m pytest -q tests/test_frontier_providers.py tests/test_frontier_studio.py tests/test_frontier_bridge.py \
  tests/test_frontier_media.py tests/test_frontier_render.py tests/test_mcp_billing.py tests/test_niche_cron.py
python scripts/smoke_frontier_admin.py --bundle data/frontier/channelrecipe-5.3-admin.7.zip
```

The bundle smoke requires its runtime dependencies. It imports the actual pinned
engine and tests local screens, local access boundaries and the CR bridge using
stubbed model responses, plus actual 5.3 image/motion/presenter hook routing;
it also performs a real subtitle/audio final mux with local FFmpeg. It spends no provider credits and does not certify every style. New provider contracts
are verified against official API docs; live paid video quality remains a gate.
Browser checks exercise all five onboarding steps, account saving, style search
and family filters, and narrow/mobile layouts.

Continue the approved migration with private named-stage extraction, full
feature/provider inventory, persistent worker contracts, a real long-form
original-vs-integrated video, interrupted/resumed work and controlled repairs.
Then validate each style family, shared hosted execution and the optional farm
and product paths. Public entitlements, quotes, cancellation, customer support
diagnostics and a fresh Mac/Windows pilot remain release gates.

Rollback the web release to hide the entry and deny new use. Keep the private
bundle and encryption secret for recovery. Do not delete project tables or rotate
keys as part of rollback. The current runner requires compatible admin APIs;
maintain them while downloaded preview jobs are still in use.

## Separate narration choice (admin.3)

The local setup has a visible narration selector: inherit the shared model account, Kie.ai, Atlas Cloud, or individual Frontier voice settings. An explicit narration choice overrides only narration. It can reuse a locally saved key after a read-only account check; it does not call the brain-key API. A new key replaces that provider’s saved local key. Account changes are blocked while a video is running or queued. The setup displays its local version so a newer cloud release can be identified. Old installations require the updated runtime.

Kie narration offers an explicit choice between Gemini 3.8 Flash TTS and ElevenLabs Turbo 2.5. Existing shared-hub choices retain their ElevenLabs default; selecting a separate voice model is explicit. Gemini uses one `Speaker 1` and preserves the script in `dialogue_turns`. Its WAV output is converted locally to MP3 before existing subtitle timing and audio processing. Provider failures stop the job and retain their error and reported credits; there is no automatic paid fallback.

Subtitle timing decodes the saved audio with FFmpeg to 16 kHz mono PCM and passes the waveform to local Whisper. This avoids incompatibilities between installed PyAV and faster-whisper versions without upgrading the original engine's dependency set.


## Kie image routing and bounded fallbacks (admin.4)

The CR image adapter now handles the native engine’s selected Kie image provider even when no shared media hub is configured. The native provider selection is captured before installing hooks; saving a Kie narration key does not itself switch another image provider. An optional, separate Kie image choice takes priority over a shared image hub. Its setup form reuses a saved local key after a read-only account check and does not change the CR brain or narration. Account/model edits are blocked while work is running or queued.

Supported families are Nano Banana, Nano Banana 2, Nano Banana 2.1, GPT Image 2, and GPT Image 2.5 Flare. Each family has its documented text/reference-edit model ID and reference field (`image_urls`, `image_input`, or `input_urls`). Newer models explicitly use 1K output; original Nano Banana keeps its default output size. Each reference stays attached through fallback. Catalog/schema sources: https://docs.kie.ai/market/google/nanobanana2, https://docs.kie.ai/market/google/nanobanana-2-1, https://docs.kie.ai/market/gpt/gpt-image-2-text-to-image, and https://docs.kie.ai/market/gpt/gpt-image-2-5-flare-text-to-image, plus the corresponding edit pages.

The default Kie chain keeps the selected primary image model, then tries Nano Banana 2.1, GPT Image 2, Nano Banana 2 and GPT Image 2.5 Flare, omitting duplicates. Users can select a primary and disable any/all backups. At most five distinct model attempts run per image, once per model, on the same Kie wallet. Attempts may consume credits; this is an attempt limit, not a fixed-price guarantee. Only a confirmed moderation refusal or terminal model failure advances the chain, with the same prompt. Provider moderation still applies and may refuse every selected model. Balance/access/rate-limit/validation errors, cancellation, uncertain creation, pending jobs and failed downloads stop instead of launching another paid job.

A per-model receipt retains each task ID, outcome, provider reason and reported credits. An explicit client rejection without an ID is saved as a known failure; a 5xx/timeout without an ID remains uncertain. Resume skips known failed attempts and reuses the successful fallback or polls the pending task. It never resubmits a failed model. Narration remains separate: a Gemini TTS failure cannot be fixed by changing image models, and there is no automatic paid voice fallback.


## Fixed Kie motion cost and subtitle-capable rendering (admin.5)

All Kie image animation uses `bytedance/v1-pro-fast-image-to-video` with `image_url`, `resolution: "720p"` and the string `duration: "5"`. This includes the shared hub, native Kie clips and the history kit's formerly direct provider call. Longer scene holds keep the five-second generated clip and the engine's existing playback behavior; they do not select ten seconds or a more expensive model. Explicit Atlas motion selections keep their existing Atlas adapter. Reference: https://docs.kie.ai/market/bytedance/v1-pro-fast-image-to-video.

History animation now uses the same per-file task receipts and two active local media calls. Cached animated clips are retained. Failed, pending or uncertain motion tasks stop without a new paid submission or another model. The native Kie context is limited to its worker thread, so a Kie voice key does not replace unrelated WaveSpeed tasks.

Video setup checks require the `subtitles` filter and H.264/AAC/MP3 encoders. A minimal FFmpeg build can pass `-version` and still fail the final render with exit 234. On Mac the studio selects an already installed compatible `ffmpeg-full` pair for its own process. If none is available, setup reports that video tools need attention. Final mux failures retain a private, sanitized local diagnostic and explain that generated assets remain saved.


## Original process and adapter corrections (admin.6)

The original director, feature resolver, style defaults, B-roll sourcing, local
graphics, 3D modules and compositor remain the production entry points. A
provider account changes which supported service executes a task; it does not
choose a preset, enable every feature or impose a percentage of footage.

The original History preset deliberately offers AI images, local motion
graphics and sound, excludes footage and disables captions. Its timeline pass
uses moving paintings with embedded title/counter/cutout graphics. Documentary
(per-title style creation and the broader toolset) and ALMANAC (archive footage,
historical objects and graphics) are existing original alternatives. Their
output remains subject to the selected features and required accounts.

History animation executes the original function code, including shot ranking,
cache validation and scene prompt construction. A scoped transport adapts only
its upload, provider execution and download operations to durable CR receipts.
The user-selected cheaper Kie model still uses 720p/five-second clips. Its
documented schema lacks the original model's camera_fixed parameter, so the
original camera direction remains in the prompt; identical model output is not
guaranteed. The previous CR copy of creative selection logic was removed.

Readiness now recognizes configured CR brain access and supported replacement
voice, image and Kling accounts. Remote brain access is still checked before
starting each job. The original feature offered/default/resolve rules and all
specialized stock, video-watcher, cutout and 3D dependencies remain intact. A
connected brain account is not itself a video-watcher account: the native
YouTube footage analyzer still needs its supported local provider configuration.
Original presenter calls preserve positional motion options when the hub is off.

The private bundle smoke checks actual original animation code and feature
resolution with stubbed provider execution. This establishes integration
boundaries, not successful full-video output for every preset. No new paid
requests or regenerated job assets are required by this correction.
