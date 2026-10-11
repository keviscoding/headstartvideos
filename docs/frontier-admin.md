# ChannelRecipe local studio: admin migration preview

Version: `5.3-admin.1`. Entry point: `https://channelrecipe.com/app#local-studio`.

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
  admin's own Gemini key. No CR generation key is used as a fallback.
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

The output defaults to git-ignored `data/frontier/channelrecipe-5.3-admin.1.zip`.
`--upload` publishes to the private Spaces object
`private/frontier/channelrecipe-5.3-admin.1.zip`. It checks both the object ACL
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

Admins enter their own Gemini API key through the CR account form. Model lookup
verifies API access without generating content. The configured default model is
`gemini-3.8-flash`; changing its availability or output quality requires review.
Connection is not a promise of credit availability or production quality.

Media keys are stored locally with private file permissions. They never travel
through the CR account endpoints. The local connect key is the existing personal
CR MCP key; users can revoke it using the existing rotation control. Never ask
for keys in chat or pass them as shell command arguments.

Receipts show reported brain tokens, including thinking tokens where reported.
They are not an exact total dollar bill. Media, search, interrupted requests and
provider subscriptions can have additional charges. Provider usage reports are
the source of truth. The preview does not enforce a cross-provider dollar budget.

## Recovery and limitations

Completed identical requests reuse the stored answer. Capacity rejections are
retried at most four times locally; network/provider failures are not
automatically repeated or sent to a different paid account. An uncertain or
truncated answer leaves the project marked `needs_review`. Resuming that same
request stops to prevent an accidental second charge. Check provider usage and
the local job log before deliberately restarting with a new project. A guided
per-stage retry approval flow is still required for public release.

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
python -m pytest -q tests/test_frontier_studio.py tests/test_frontier_bridge.py \
  tests/test_mcp_billing.py tests/test_niche_cron.py
python scripts/smoke_frontier_admin.py --bundle data/frontier/channelrecipe-5.3-admin.1.zip
```

The bundle smoke requires its runtime dependencies. It imports the actual pinned
engine and tests local screens, local access boundaries and the CR bridge using
stubbed model responses; it does not spend provider credits or certify a video.
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
