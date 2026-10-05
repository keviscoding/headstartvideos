# Cloud discovery and catalog admissions

The production discovery worker is an ephemeral Fly Machine with a dedicated image. Web requests only create a PostgreSQL run and launch it; Fly failures do not run Chromium on the web server. Cook Machines and their release image are independent.

## Exploration

A balanced daily hunt rotates broad randomly drawn search probes, related videos from a shuffled sample of the existing catalog, and searches learned from titles encountered during this run. It expands even promising channels that fail the content screen, avoiding a feedback loop that only follows accepted formats. Queue sources rotate during enrichment as well as discovery; each encountered pool interleaves high-view and randomized channels. It has no fixed niche taxonomy and cannot guarantee exhaustive YouTube coverage.

The one-off avatar profile adds the user's three reference presenters as starting points while retaining broad and learned exploration. References are discovery bridges, not automatically accepted additions. Related results and title searches recursively expand the frontier, with maximum depth three. Existing catalog IDs are skipped before history reads and may bridge to unseen neighbours.

Avatar is a discovery priority, not an exclusive catalog format. Other formats with two public AI-content disclosures can also enter after the same performance and AI-production quality checks. They retain their own production format and unknown avatar confidence; the AI presenter filter excludes them. Channels without avatar evidence or public AI disclosures remain held by this focused profile. Balanced daily discovery screens all formats without requiring these disclosure signals.

One-off runs can opt into `enrich_existing` (`--enrich-existing` in the CLI). This queues up to 80 active catalog entries that have never passed the quality screen, including matching references. It updates fresh metrics and quality evidence only after the same admission checks; hidden entries stay hidden. Reports count `enriched_existing` separately from additions, and the original `first_seen_at` never changes. Daily discovery leaves this mode off so catalog refreshes do not consume the entire novelty budget.

The enrichment seed pool uses existing metadata as a cheap lead: active entries with recorded recent averages of at least 20,000 views, 4–80 uploads, and recent posting activity. It rotates source-keyword groups and shuffles IDs per run, then fetches fresh histories before screening. Stored metrics are not treated as current admission evidence. All earlier review and daily limits still apply.

## Admission

Use the latest twelve long-form uploads. Ignore uploads younger than seven days when judging failures. Require at least four uploads aged 7–60 days, at least 75% with 10,000 views, a median of at least 20,000 views, and an upload in the last 21 days. These are research admission thresholds, not guarantees of monetization or profitable replication.

Performance-qualified avatar candidates first receive a visual triage when public disclosure is missing. With native Gemini configured, this inspects two public 0–45 second openings at one frame per second, using timestamped observations for each URL. This catches introductory hosts absent from YouTube's representative stills and allows different hosts across videos. A response exceeding 30,000 input tokens disables further opening requests; unsupported video requests fall back to static stills. The direct URL/clipping mechanism is documented by [Google](https://ai.google.dev/gemini-api/docs/generate-content/video-understanding).

Uncertain appearances are held before caption requests; likely matches receive the separate content screen: two captions with at least 1,000 characters each and representative numbered stills from both videos. Three independent review clients may run concurrently. Model decisions must use a valid structured response, substantive content feasible with scripts and AI/stock visuals, and no serious unresolved concerns. Repeatable real-world filming alone does not pass. Missing evidence goes to review, never automatic admission.

Atlas billing failures (HTTP 402) switch reviews to the already configured native Gemini provider. The native model stays configurable with `GEMINI_TEXT_MODEL`; transient server failures receive one bounded retry. Provider failures do not enter the channel rejection cache. If review providers remain unavailable, the run stops with an error and retains its frontier and completed additions. The job never automatically purchases credits or changes account billing.

Native requests use constrained JSON output schemas; local validation still checks all evidence and timestamps. A single-object array wrapper can be normalized, but ambiguous multiple-review arrays are rejected. API counters are included in every heartbeat so a resume during a slow review retains its request usage.

AI host disclosure means an explicit public host/avatar claim plus an observed presenter. The weaker `likely` candidate label requires two public YouTube AI labels and plausible presenter-style observations, strong synthetic styling in both static triage and the full screen, or strong virtual styling supported by at least two timestamped observations in each opening excerpt. Opening evidence can establish a presenter format even when later stills contain supporting footage. Triage results are withheld from the content model prompt to reduce anchoring. A realistic face, generated thumbnail, or AI scenery alone does not qualify. Automated sampled evidence cannot prove synthetic identity; the product labels `likely` as a candidate.

The user's three named examples can receive a distinct `reference` label after the same performance and content checks plus an observed introductory host. This records the human-supplied reference; it does not claim independent identity verification. It does not propagate to neighbouring channels. Classifier changes invalidate review caches and reconsider uncertain completed reviews within the original remaining budget, while preserving addition checkpoints.

`possible` is a weaker discovery lead: a presenter in both validated opening excerpts, two public AI-content labels, and a substantive AI-reproducible format. It is displayed as **AI-assisted presenter**, with an explicit tooltip that AI may only be used in supporting visuals and the host could be real. It does not claim strong synthetic styling or independent identity verification, and does not relax the performance/content admission checks.

## Persistence and budgets

PostgreSQL stores the frontier, evidence decisions, immutable catalog insertion date, and every addition's audit checkpoint. One renewable catalog lease prevents overlapping adaptive hunts. Tasks have expiring ownership and up to three claims; an interrupted running job may resume within its original wall-clock budget. Insertion and its task checkpoint are one transaction and lock the run row, so cancellation prevents further admission. A completed or cancelled run cannot be overwritten by a late worker.

Per-run limits cover wall-clock time, explored pages, enriched channels, content reviews, and actual YouTube read requests. Shared UTC-day reservations cap discovery at 6,000 YouTube read requests and 300 review attempts; abandoned reservation blocks remain counted conservatively. These are application budgets, not a claim about the account's remaining provider quota. Cache keys include model and acceptance criteria; rejected content is cached for seven days, insufficient evidence for a shorter retry window.

Default daily limits: 30 minutes, 300 enriched channels, 100 content reviews, 100 explored pages, 2,000 API reads, and a target of 20 additions. A target is a ceiling; the worker never weakens admission to fill it.

## Deploy and operate

Build without changing the cook app's release:

```
flyctl deploy -c fly.niche.toml --build-only --push --remote-only --image-label niche-<version>
```

Set `FLY_NICHE_IMAGE` on the web app to the resulting registry digest. The web bridge injects fresh database, YouTube, Atlas, native Gemini, and caption provider configuration into each discovery Machine. Do not put credentials in commands or source control.

With production environment available securely:

```
python -m scripts.cloud_niche_hunt start --profile avatar --enrich-existing --seconds 3600 --target 40 --candidates 1000 --reviews 240 --pages 250 --api 3000
python -m scripts.cloud_niche_hunt status --job-id <id>
python -m scripts.cloud_niche_hunt resume --job-id <id>
```

Resume uses the same run ID, existing frontier, addition checkpoints, and original work/time limits. A stopped or completed job needs a new run; do not reset its timestamp to bypass budgets.

## Newly added

The toggle shows original additions from the past seven days, ordered by insertion date and channel ID for stable ties. Refreshing metrics preserves `first_seen_at`. Cards display relative time, a calendar date for older entries, and an exact timestamp tooltip. Count and pagination use the same cutoff and other filters. Older catalog entries have not been retroactively quality-screened by this change.

The separate AI presenter candidates toggle includes only quality-screened entries with `likely`, `disclosed`, explicitly labelled `reference`, or clearly labelled AI-assisted `possible` evidence. It can be combined with Newly added for true new additions, or used alone to include quality-enriched existing entries. Defaults continue to browse all formats.
