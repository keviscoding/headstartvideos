# Bounded niche discovery experiment

Run from the repository root with the normal configured environment:

    python scripts/run_niche_batch.py \
      --keyword "Japanese home cleaning mistakes" \
      --keyword "Appalachian cooking tricks" \
      --exclude-name "Opal Rowe" \
      --target 5 --candidate-cap 30 --review-cap 12 \
      --seconds 480 --report output/niche-batch.json

Required: YOUTUBE_API_KEY. Content screening additionally uses ATLASCLOUD_KEY
and the configured ATLAS_TEXT_MODEL. DOWNSUB_KEY enables the existing caption
provider; public YouTube captions are the fallback. No database imports or live
library writes occur. Cache and reports live in ignored data/ and output/.

The search stage reuses one browser, bounds scrolling and result counts, and
resolves collaborator cards through official video statistics even when the
search page has no channel link. Detailed upload histories are fetched for at
most candidate-cap channels. Cached decisions are excluded before those reads.

The default experiment checks the latest up to 12 sampled uploads aged 7–60
days: at least four must be present, at least 87.5% must exceed 10,000 views,
and the channel must have uploaded within 21 days. These are explicit trial
settings, not a universal definition of a successful video. Raw current views
cannot establish seven-day growth or historical velocity. A small sample of
uploads cannot establish the first upload date or the full channel history.

Only performance-qualified channels receive a content screen. Two recent
transcripts and at least one distinct numbered YouTube still per video are required.
Missing evidence, malformed model responses, serious unresolved concerns and original
filming requirements prevent an automatic pass. Static stills cannot confirm
synthetic identity; avatar confidence remains unknown.

Rubric version 2 separates serious observed concerns from minor/hypothetical
caveats, so a generic concern about possible audience fatigue does not veto
otherwise substantive content. A rubric change invalidates cached decisions.

To retest content screening without paying for another discovery/history pass:

    python scripts/run_niche_batch.py \
      --replay-report output/niche-batch.json \
      --channel-id UC0wJUOkjOcKqPtg-rTI5-xg \
      --target 1 --candidate-cap 1 --review-cap 1 \
      --report output/niche-replay.json

Replay reuses the saved view counts and publication dates; it is not a fresh
performance measurement. It still applies the current performance screen.
The discovery checkpoint retains all candidate snapshots, including candidates
not reviewed after the target is reached, so later reviews can reuse that work.

An automatic pass means a promising research candidate. It does not verify
factual accuracy, monetization, multi-competitor niche success, emergence, or
synthetic identity. Content reviews are model judgments and need calibration
against human decisions before publication is automated.

The target and review cap stop further content work. A monotonic deadline stops
new work; in-flight requests have finite timeouts and can finish slightly after
the deadline. Partial review results are atomically checkpointed. Rejections
expire after seven days, successful/weak-performance checks after one day,
and missing-evidence cases after one hour. Changed criteria invalidate cached
decisions. A file lock prevents concurrent CLI runs sharing one cache.

Reports contain actual wall time, discovery statistics, API request timing,
caption/image/model request counts, model token usage, source links, sample sizes
and rejection reasons. Reports omit transcript text and credentials.

This is a single-worker experiment. Before deploying across ephemeral workers,
move cache/decisions to shared persistence, add leases/checkpoints, and enforce
provider quotas centrally. The daily production cron retains its existing
selection behavior until this experiment is reviewed and explicitly integrated.

Validation:

    python -m pytest -q tests/test_niche_batch.py
