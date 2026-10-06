# Lightweight cloud niche discovery

The production finder collects promising new channel leads using observed performance numbers. It does not clean up the existing catalog, fetch captions or images, classify AI identity, or call language models. Channels are marked `quality_status=metrics` and shown as **Performance lead**, rather than represented as content-reviewed or monetization-verified.

## Discovery and diversity

Two concurrent HTTP scouts read YouTube's public initial result data, with at most one search continuation per page. There is no Chromium, video playback, media download, or ASR process. Public result layouts are subject to change; repeated failures stop the run and retain its frontier rather than spawning a heavier browser fallback.

The scout rotates four equal discovery lanes: broad everyday-word searches, recent-upload searches, related videos, and phrases learned from encountered titles. Each run shuffles its probes and draws 24 different existing-library channel seeds. Those seeds supply neighbours; they are never re-reviewed or updated. Related/title expansion stops at depth two. Top-view cards are mixed with a shuffled tail, and candidate processing draws from all four lanes. There is no niche whitelist or fixed avatar funnel. Historical `profile`/`enrich_existing` request fields remain accepted for compatibility but cannot enable classification or catalog cleanup in the production path.

Known and recently checked channel IDs are skipped before expensive upload-history reads. Frontier insertion batches share one database transaction. Channel metadata is read in batches, and the existing API client remains confined to the coordinator to avoid unsafe concurrent googleapiclient use. Scouting continues while the coordinator checks channel histories.

## Admission numbers

Defaults retain the previous measurable checks:

- Long-form videos are at least four minutes.
- At least four sampled uploads aged 7–60 days.
- At least 75% of the latest up to 12 mature samples have at least 10,000 views.
- Mature median at least 20,000 views, and an upload within 21 days.
- The admin subscriber cap and optional recent-average cutoff are honored separately.
- A majority of exactly repeated normalized titles puts a candidate on hold; there are no topic or language rejection rules.

Unseen channels passing these checks are inserted with their real addition time, source, sample videos, and numeric audit evidence. Content quality, AI identity, copyright/licensing, competitor comparisons, factual accuracy, and profitability are not asserted by these checks. Missing/weak numerical samples remain held. The AI presenter filter continues to cover previously screened entries; new metadata leads do not receive speculative AI labels.

## Fly and budgets

Discovery requests one shared CPU and 512 MB RAM by default, using `FLY_NICHE_MEMORY_MB` (bounded to 256–1024 MB), independently of cook worker sizing. Its dedicated image has no Chromium, caption client, or Pillow dependency. Its injected credentials are limited to database access, YouTube metadata access, and optional error reporting. Cook image, worker command, and resource settings are unchanged.

Default runs have a ten-minute ceiling, a target of 50 new leads, 600 candidate checks, 100 result pages, and 2,000 metered YouTube reads. The target is a ceiling, never an instruction to weaken admission. The shared UTC-day read reservation remains capped at 6,000. Only one hunt owns the catalog lease; two HTTP scouts run inside that one small machine. The machine auto-destroys on exit.

PostgreSQL retains the deduplicated frontier, expiring task ownership, checkpoints, cached numeric decisions, and immutable catalog insertion dates. Atomic insertion and task checkpoint lock the run and verify its current lease; cancellation prevents further admission. Interrupted jobs can resume only within their original remaining time/work limits. Existing channels are never reactivated, refreshed, or counted as additions.

Metrics record model/caption/image calls (all zero in this path), HTTP and YouTube request counts, total elapsed time, peak process RSS, and process CPU seconds. Rates from one short run are measurements of that run, not guaranteed sustained yield. These leads have a different acceptance scope from the earlier full content-review experiments.

## Operations

Build only; do not deploy a cook release:

```sh
flyctl deploy -c fly.niche.toml --build-only --push --remote-only --image-label niche-light-v1
```

Set the web app's `FLY_NICHE_IMAGE` to the resulting dedicated digest, then launch with the web admin control or:

```sh
python -m scripts.cloud_niche_hunt start --seconds 600 --target 50 --candidates 600 --pages 100 --api 2000
python -m scripts.cloud_niche_hunt status --job-id JOB_ID
python -m scripts.cloud_niche_hunt resume --job-id JOB_ID
```

The former evidence experiment is retained as `run_evidence_hunt` for reference/research tests. The production entry point always dispatches `run_light_hunt`; neither legacy request fields nor a missing model key change that.

API references: [YouTube videos.list](https://developers.google.com/youtube/v3/docs/videos/list), [Fly Machine configuration](https://docs.fly.io/machines/api/machines-resource).
