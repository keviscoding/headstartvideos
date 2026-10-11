# ChannelRecipe local studio: admin migration preview

Version: `5.3-admin.3`. Entry point: `https://channelrecipe.com/app#local-studio`.

This is the first runnable integration checkpoint, **not completion of the full
Frontier migration**. Ordinary users, including paying subscribers, cannot use
its website APIs, download or MCP tools. Access is checked against the current
`ADMIN_EMAILS` on every request, including local-runner Bearer requests.

## What this checkpoint supplies

- An admin navigation entry and five-step onboarding: explanation, download,
  Claude Code connection, account connection and first video.
- A searchable catalog of the 26 main presets in the selected 5.3 package.
- A protected Mac/Windows download retaining the original production engine,
  timeline editor, style files, graphics, 3D libraries and required assets.
- Local setup checks, secure account entry and a spending acknowledgement.
- Text, grounded research and sampled-image review calls routed to CR using the
  admin's own Google Gemini, Kie.ai or Atlas Cloud key. No CR generation key is used as a fallback.
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

The output defaults to git-ignored `data/frontier/channelrecipe-5.3-admin.3.zip`.
`--upload` publishes to the private Spaces object
`private/frontier/channelrecipe-5.3-admin.3.zip`. It checks both the object ACL
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

Admins connect Google Gemini, Kie.ai or Atlas Cloud through the CR brain form,
or paste a hub key once in the local setup and select its brain checkbox.
Read-only model/account lookup verifies API access without generating content.
The supported hub brain is Gemini 3.8 Flash (provider-specific model identifiers).
Kie uses its model-specific Chat Completions endpoint, including googleSearch
for research; Atlas uses native Gemini contents/tools and multimodal parts.
Changing model availability or output quality requires review.
Connection is not a promise of credit availability or production quality.

Media keys are stored locally with private file permissions. They travel to CR
only when the user explicitly selects the same account for the brain. The local connect key is the existing personal
CR MCP key; users can revoke it using the existing rotation control. Never ask
for keys in chat or pass them as shell command arguments.

Receipts show reported brain tokens, including thinking tokens where reported.
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

The provider's idle read timeout remains 180 seconds. Active native streams are
also checked against a 180-second generation window; an outstanding blocking
read can outlast that window until its idle timeout. This does not detach the
compatibility relay from App Platform or local HTTP timeouts. If the local
connection stops while CR is finishing, review its recorded status before
resuming. A persistent job submission/polling contract remains migration work.
These controls improve latency but do not guarantee provider availability or
prove original-vs-integrated output quality across every style.

The hub profile replaces tasks, not every model in the engine. Its catalog is
shared between the server and packaged runner. Defaults use Nano Banana for
images/edits, ElevenLabs for narration, Seedance for image motion, and Kling for
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

The Mac launcher and imported engine were exercised on the development Mac.
The Windows launcher requires testing on a fresh Windows machine. Python,
FFmpeg and Node installation still needs guided help when absent; signed native
installers and automatic prerequisite repair remain onboarding work.

## Verification and next migration work

```sh
python -m pytest -q tests/test_frontier_providers.py tests/test_frontier_studio.py tests/test_frontier_bridge.py \
  tests/test_frontier_media.py tests/test_mcp_billing.py tests/test_niche_cron.py
python scripts/smoke_frontier_admin.py --bundle data/frontier/channelrecipe-5.3-admin.3.zip
```

The bundle smoke requires its runtime dependencies. It imports the actual pinned
engine and tests local screens, local access boundaries and the CR bridge using
stubbed model responses, plus actual 5.3 image/motion/presenter hook routing;
it does not spend provider credits or certify a video. New provider contracts
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
