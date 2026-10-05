# Cloud discovery and catalog admissions

The production discovery worker is an ephemeral Fly Machine with a dedicated image. Web requests only create a PostgreSQL run and launch it; Fly failures do not run Chromium on the web server. Cook Machines and their release image are independent.

## Exploration

A balanced daily hunt rotates broad randomly drawn search probes, related videos from a shuffled sample of the existing catalog, and searches learned from titles encountered during this run. It expands even promising channels that fail the content screen, avoiding a feedback loop that only follows accepted formats. Queue sources rotate during enrichment as well as discovery; each encountered pool interleaves high-view and randomized channels. It has no fixed niche taxonomy and cannot guarantee exhaustive YouTube coverage.

The one-off avatar profile adds the user's three reference presenters as starting points while retaining broad and learned exploration. References are discovery bridges, not automatically accepted additions. Related results and title searches recursively expand the frontier, with maximum depth three. Existing catalog IDs are skipped before history reads and may bridge to unseen neighbours.

## Admission

Use the latest twelve long-form uploads. Ignore uploads younger than seven days when judging failures. Require at least four uploads aged 7–60 days, at least 75% with 10,000 views, a median of at least 20,000 views, and an upload in the last 21 days. These are research admission thresholds, not guarantees of monetization or profitable replication.

Performance-qualified avatar candidates first receive a cheap visual triage when public disclosure is missing. Uncertain appearances are held before caption requests; likely matches receive the expensive content screen: two captions with at least 1,000 characters each and representative numbered stills from both videos. Three independent review clients may run concurrently. Model decisions must use a valid structured response, substantive content feasible with scripts and AI/stock visuals, and no serious unresolved concerns. Repeatable real-world filming alone does not pass. Missing evidence goes to review, never automatic admission.

AI host disclosure means an explicit public host/avatar claim plus an observed presenter. The weaker `likely` candidate label requires two public YouTube AI labels and plausible presenter-style observations, or strong synthetic styling in both visual triage and the full content screen. Triage results are withheld from the second model prompt to reduce anchoring. A realistic face, generated thumbnail, or AI scenery alone does not qualify. Limited stills cannot prove synthetic identity; the product labels `likely` as a candidate.

## Persistence and budgets

PostgreSQL stores the frontier, evidence decisions, immutable catalog insertion date, and every addition's audit checkpoint. One renewable catalog lease prevents overlapping adaptive hunts. Tasks have expiring ownership and up to three claims; an interrupted running job may resume within its original wall-clock budget. Insertion and its task checkpoint are one transaction and lock the run row, so cancellation prevents further admission. A completed or cancelled run cannot be overwritten by a late worker.

Per-run limits cover wall-clock time, explored pages, enriched channels, content reviews, and actual YouTube read requests. Shared UTC-day reservations cap discovery at 6,000 YouTube read requests and 300 review attempts; abandoned reservation blocks remain counted conservatively. These are application budgets, not a claim about the account's remaining provider quota. Cache keys include model and acceptance criteria; rejected content is cached for seven days, insufficient evidence for a shorter retry window.

Default daily limits: 30 minutes, 300 enriched channels, 100 content reviews, 100 explored pages, 2,000 API reads, and a target of 20 additions. A target is a ceiling; the worker never weakens admission to fill it.

## Deploy and operate

Build without changing the cook app's release:

```
flyctl deploy -c fly.niche.toml --build-only --push --remote-only --image-label niche-<version>
```

Set `FLY_NICHE_IMAGE` on the web app to the resulting registry digest. The web bridge injects fresh database, YouTube, Atlas, and caption provider configuration into each discovery Machine. Do not put credentials in commands or source control.

With production environment available securely:

```
python -m scripts.cloud_niche_hunt start --profile avatar --seconds 3600 --target 40 --candidates 800 --reviews 240 --pages 200 --api 2400
python -m scripts.cloud_niche_hunt status --job-id <id>
python -m scripts.cloud_niche_hunt resume --job-id <id>
```

Resume uses the same run ID, existing frontier, addition checkpoints, and original work/time limits. A stopped or completed job needs a new run; do not reset its timestamp to bypass budgets.

## Newly added

The toggle shows original additions from the past seven days, ordered by insertion date and channel ID for stable ties. Refreshing metrics preserves `first_seen_at`. Cards display relative time, a calendar date for older entries, and an exact timestamp tooltip. Count and pagination use the same cutoff and other filters. Older catalog entries have not been retroactively quality-screened by this change.
