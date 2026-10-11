# ChannelRecipe local studio

Version: `5.3-admin.10`. Entry point: https://channelrecipe.com/app#local-studio.

The preferred route runs the original Frontier 5.3 workflow through the creator's
signed-in Claude Code account. CR supplies the protected runtime, current workflow
contract, model profile, owned projects, stage authorizations and usage receipts
through authenticated MCP. Generation and rendering execute on the local computer.

## Website-to-video flow

1. Download from CR, extract the entire ZIP and open the Mac or Windows launcher.
2. Connect CR with the personal MCP key and acknowledge account usage.
3. Install/sign in to Claude Code, connect the project MCP configuration, save
   writing/visual models and explicitly verify both with two tiny text requests.
4. Connect media accounts. Optional Kie/Atlas substitution covers supported voice,
   stills, reference edits, image animation and presenters. Stock footage and
   specialist services retain their original requirements.
5. Choose an original preset and check its actual requested features. Required
   accounts, the browser, FFmpeg and Node must pass before generation is queued.
6. Create, inspect the result and timeline, and export from the original studio.

MCP conversation control follows the same path: `local_studio_prepare`,
`local_studio_begin`, the returned local `cr_run.py --project` command, and
`local_studio_progress`. The command contains only an owned project ID. The local
runner checks setup again and uses Frontier's own validation/queue construction.
MCP does not remotely launch a computer or sign the creator into Claude.

## Workflow and billing

The native route preserves the engine's original helpers, system/user instructions,
CLI tools, effort, response formats, research, local image review, Folio and 3D
review/repair loops. It wraps Claude subprocess execution rather than replacing
those helpers with a generic API call. Original preset/director rules determine
media choices; missing default features block instead of silently disappearing.

Default roles are Sonnet 5.5 for general work and Opus 5.5 for specialist work.
Either supported model can be explicitly selected for each role. A Claude
subscription login uses that account's limits; no CR generation key or platform
API balance is used. API/cloud environment credentials and key helpers are
excluded from these native subprocesses. CR never collects Claude OAuth secrets.

The API brain remains an explicit alternative. Its own encrypted per-user key,
provider protocols and research restrictions still apply. Connecting media alone
does not switch the selected brain. Kie motion uses Seedance V1 Pro Fast, 720p,
five seconds; current provider pricing remains the source of truth.

Original isolated timeline, reference and product Python workers inherit the owned
CR project and the same native Claude/media/render adapters. Non-worker FFmpeg,
Node and browser commands keep their original arguments.

The full engine/resources remain local, including FFmpeg, browser rendering,
Whisper, asset generation and output caches. Cloud work is small authenticated
project/stage metadata. Local Claude concurrency is two and each project has a
fixed stage ceiling. This is bounded capacity, not a demonstrated customer load
benchmark. No render workload is installed into the CR web container.

## Setup, isolation and recovery

- Account access is checked live, including for every Bearer/MCP call.
- Workflow/release/model profiles are pinned per project. A stale runtime cannot
  start new work. Approved adapter upgrades can resume projects with the same
  pinned engine/workflow and models, preserving completed-stage caches.
- Local readiness reports are checks performed by the runner, not cryptographic
  attestation of a customer-controlled computer.
- Stage inputs/results are hashed. Prompts, creative output and account secrets
  are not included in native stage authorization/receipt payloads.
- Completed creative responses remain in private local caches. Lost completion or failure
  receipts can be submitted again without invoking Claude or counting twice.
- Interrupted/failed Claude stages stop outside the original automatic retry and
  fallback handlers. One additional attempt requires explicit usage review and
  acknowledgement. CR does not automatically approve uncertain paid retries.
- A failed local render can reuse completed Claude work. Final artifact hashes
  and completion records can synchronize without generating again.
- Local HTTP access is loopback-only, with host/origin and per-process request
  token checks. Account changes are blocked during queued/running videos.
- Keys and MCP configuration use private local file permissions. Never ask for
  keys in chat or copy `.env`, `.cr-connection.json` or `.mcp.json` into a prompt.

Reported tokens are partial usage receipts, not an exact dollar bill. Native
source helpers with text output do not report token counts; the receipt marks
that fact. Claude/provider account reports determine actual usage and charges.

## Current access boundary

Access remains **admin-only**, as requested. The protected owner download retains
original local Frontier source/prompts. It is not an execution-only customer
package and cannot promise secret local prompts or prevent an owner from copying
that source. Strict private-brain distribution remains a separate public-release
architecture decision. Do not remove admin access as part of this integration.

The repository is public. Commit only generic integration code, catalog metadata,
tests and packaging instructions. Never commit the source archive, extracted
engine, prompts/styles, credentials or generated release. Do not publish the ZIP
in GitHub releases or public media storage.

## Build and deploy

Pinned source SHA-256:
`e379dc601672282040a2adbf30f7bf7f7d34fcdf38154e5f6a24ec6c2528cadf`.
The builder rejects a changed archive, preserves the original engine/resources,
excludes samples/output/user configuration and adds generic CR adapters/onboarding.
The original Claude customization guide is retained privately as FRONTIER_GUIDE.md;
CR account/launch instructions take precedence over its older setup guidance.

```sh
python scripts/build_frontier_admin.py --source /private/path/Frontier-5.3-FULL.zip
```

The ignored output is `data/frontier/channelrecipe-5.3-admin.10.zip`. The private
object is `private/frontier/channelrecipe-5.3-admin.10.zip`. Publishing checks its
ACL and unauthenticated origin/CDN access. Downloads stream through CR's
authenticated endpoint with `private, no-store`; no public object URL is returned.

Preserve `FRONTIER_SECRETS_KEY` for saved API-brain credentials across deploys.
The native Claude Code route does not require that API vault. Database changes
are additive native session/stage tables and indexes, compatible with SQLite and
PostgreSQL. Private runtime changes need a rebuilt protected download as well as
a website deployment.

## Validation

Tests cover setup failures, live authorization/tenant isolation, stage reservation,
result idempotency, explicit one-attempt recovery, render recovery and real MCP
HTTP transport. The private-bundle smoke executes actual original text/research/
vision/Folio/3D helper contracts with mocked paid results, media entry points and
real FFmpeg/Whisper subtitle processing. These checks establish routing and
runtime behavior; actual rendered-video quality and fresh-computer onboarding
must also be assessed before making quality claims.
