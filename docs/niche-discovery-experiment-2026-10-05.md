# Niche discovery experiment — 5 October 2026

The final fresh batch returned three research candidates in **171.04 seconds
(2m 51s)**. It selected 30 channels for upload-history reads, returned 29 usable
channel snapshots, content-reviewed four, and stopped when three passed. No
channels were written to the live library and no deployment was performed.

These are automated research passes, not verified niche recommendations.
Editorial quality, synthetic identity, factual accuracy, rights, monetization,
and whether multiple independent channels follow the same successful recipe
still require human assessment. The experiment uses current public view counts,
not historical views measured at a fixed age.

## Measurements

| Run | Search queries | Channel snapshots | Content reviews | Passes | Wall time |
| --- | ---: | ---: | ---: | ---: | ---: |
| Broad discovery, rubric 1 | 8 | 28 | 9 | 0 | 288.56s / 4m 49s |
| Household/presenter discovery, rubric 1 | 8 | 28 | 2 | 0 | 184.04s / 3m 04s |
| Repeat household searches with cache, rubric 1 | 8 | 25 | 3 | 0 | 151.58s / 2m 32s |
| Borderline-content diagnostic, rubric 1 | saved data | 5 | 5 | 0 | 122.65s / 2m 03s |
| Strong-candidate content replay, rubric 2 | saved data | 4 supplied | 3 | 3 | 62.43s / 1m 02s |
| Final fresh discovery, rubric 2 | 4 | 29 | 4 | 3 | 171.04s / 2m 51s |

Execution time across these six runs was 980.30 seconds (16m 20s). This excludes
implementation, environment setup, tests, manual inspection, and an initial
10.12-second single-query scraping probe. No unchanged legacy baseline was
timed, so these results do not establish a speedup factor.

The repeat run encountered **27 cached channel IDs**, excluded them before
detailed upload reads, and returned 25 other candidate snapshots. The cap still
allowed 30 new history reads; cache exclusion improves novelty rather than
necessarily reducing the number of API calls in a full batch. Search results
varied between runs, so the repeat runtime is not a controlled comparison.

The final fresh run spent 51.90s discovering and enriching candidates. It made
63 measured YouTube Data API requests, eight caption-retrieval attempts, 16
still-image requests, and four model requests. Reported model usage was 42,279
input tokens and 679 output tokens. Caption attempts include provider/fallback
retrieval attempts rather than every underlying HTTP request. Monetary cost and
sustained provider quotas were not measured.

## Candidate channels

The performance sample is the latest up to 12 sampled uploads aged 7–60 days.
Each automatic pass also requires at least four mature uploads, at least 87.5%
above 10,000 views, and an upload within 21 days. These cutoffs are experimental;
they are not a universal definition of a successful video.

| Channel | Topic / format | Mature sample above 10k | Median views | Result |
| --- | --- | ---: | ---: | --- |
| [Axis Studio](https://www.youtube.com/channel/UC65qu2LupcgNS-q0wyTky1g) | Romantic cinematic stories | 9/9 | 125,194 | Final batch research pass |
| [@Beyond The Heart series](https://www.youtube.com/channel/UCIwsyJBqE34QTCSc3e8PQcw) | Emotional / billionaire drama stories | 6/6 | 102,452 | Final batch research pass |
| [Retired Life with Reese Will](https://www.youtube.com/channel/UCVsMLw0qZlzlY2H6lZC80Ww) | Presenter life advice for people over 50 | 8/8 | 44,578 | Final batch research pass; AI identity unconfirmed |
| [Britain Retold](https://www.youtube.com/channel/UC0wJUOkjOcKqPtg-rTI5-xg) | British history and domestic nostalgia | 12/12 | 51,654 | Earlier discovery, rubric 2 replay pass |

Direct browser inspection found that Axis Studio describes its films as
original AI-generated dramas and shows a join date of 24 August 2026.
Beyond The Heart shows a join date of 7 August 2026; its representative
[hotel story](https://www.youtube.com/watch?v=MNyNRUA-jDY) has YouTube's visible
AI-content label. These signals support AI use, not any particular production
tool or proof of originality. They provide two successful candidate examples
of a related story format, subject to a closer recipe comparison.

Reese Will's profile describes personal retirement experience and shows a join
date of 7 March 2025. It is a presenter-format reference, not a confirmed new
AI-avatar channel. Static stills cannot establish whether a human-looking host
is synthetic. The automatic screen keeps avatar confidence unknown.

Other relevant leads included The 5 Minute Senior Kitchen (90,060 median;
10/12 mature uploads above 10k), Forgotten Appalachian Skills (43,280; 10/12),
Forgotten Home Engineering (15,618; 9/12), and Ezra Cade (10,848; 7/12).
They did not meet the chosen performance cutoff. The extra content diagnostic
also held or rejected them; they were not silently promoted to fill a quota.

The screen rejected Drama Rush Time despite 12/12 mature uploads exceeding 10k
because its model review judged the format dependent on actor-based footage
rather than the intended AI production workflow. That is a model assessment,
not a verified claim about ownership or infringement.

## What changed after the first trials

Rubric 1 treated every stated concern as blocking. Reviews of otherwise
substantive samples were held for generic, hypothetical audience fatigue.
Rubric 2 separates observed serious concerns from minor editorial caveats.
Performance thresholds stayed unchanged. Two transcripts and at least one
distinct numbered still per video remain required; missing evidence or invalid
model output cannot produce an automatic pass.

The revised screen produced three passes in a saved-data replay, then reached
the same target in a fresh end-to-end run. The target was three in the final
validation run, rather than the five requested by the initial trial settings.
The experiment did not demonstrate reliably finding five qualifying channels
in every batch.

## Scalability judgment

Discovery and selective screening are viable as a bounded single-worker flow.
One browser is reused, video/channel/review caps apply before expensive work,
cached IDs prevent duplicate history reads, and caption failures skip model
work. A deadline stops new work, finite timeouts bound in-flight requests,
partial decisions are checkpointed, and a local lock prevents shared-cache
CLI overlap. Saved candidate snapshots can be replayed for rubric changes.

Unattended publication is not established. These small runs do not measure
classifier precision, reliably confirm avatar identity, or validate emergence,
monetization and competition. Human review should remain the final library
gate. Before multi-worker deployment, cache and decisions need shared storage,
leases and central quota enforcement. DOM scraping also needs monitoring when
YouTube changes its search layout.

## Reproduction and validation

See [the CLI guide](../scripts/niche_batch.md). Final fresh queries:

    retirement life lessons
    British childhood nostalgia
    billionaire romance full story
    Japanese decluttering habits

Settings: target 3, candidate cap 30, review cap 12, four scrolls, 25 results per
query, 200 total videos, 360-second budget, up to 150k subscribers, 120-day
discovery freshness. The known seed names Glen Pritchard, Opal Rowe, The Japanese
Method and Japanese Home Hacks were excluded. Changing the rubric invalidated
earlier decision-cache entries for the final fresh run.

Raw reports are retained locally under ignored `output/`:
`niche-batch-01.json`, `niche-batch-02.json`, `niche-batch-03-warm.json`,
`niche-shortlist-review.json`, `niche-replay-v2.json`, `niche-batch-04-v2.json`.
Reports omit transcript text and credentials.

Validation: 20 focused pytest tests passed, including processing caps before
history reads, cache expiry/criteria changes, compact-date and collaboration
cards, browser reuse/deduplication, maturity and outlier handling, expired
budgets, missing/malformed evidence, minor-versus-serious concerns, avatar
uncertainty, saved-data replay and target stopping. Python compilation and
`git diff --check` also passed. Production database/integration tests were not
run because the experiment does not import or write the database.
