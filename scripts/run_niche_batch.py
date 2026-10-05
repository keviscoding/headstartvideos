"""Run a bounded batch with configured API keys; never imports the database."""
from __future__ import annotations

import argparse
import fcntl
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
from core.niche_batch import BatchSettings, _write_json, run_batch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keyword", action="append", default=[])
    parser.add_argument("--replay-report", action="append", default=[])
    parser.add_argument("--channel-id", action="append", default=[])
    parser.add_argument("--exclude-name", action="append", default=[])
    parser.add_argument("--target", type=int, default=5)
    parser.add_argument("--candidate-cap", type=int, default=30)
    parser.add_argument("--review-cap", type=int, default=12)
    parser.add_argument("--seconds", type=int, default=480)
    parser.add_argument("--min-views", type=int, default=10000)
    parser.add_argument("--cache", default="data/niche_batch_cache.json")
    parser.add_argument("--report", default="output/niche_batch.json")
    args = parser.parse_args()
    if bool(args.keyword) == bool(args.replay_report):
        parser.error("Provide either --keyword or --replay-report")
    if args.channel_id and not args.replay_report:
        parser.error("--channel-id requires --replay-report")
    if not args.replay_report and not config.YOUTUBE_API_KEY:
        parser.error("YOUTUBE_API_KEY is required")
    source_hits = None
    if args.replay_report:
        try:
            rows = {}
            for source in args.replay_report:
                saved = json.loads(Path(source).read_text())
                for hit in saved.get("candidates", saved["results"]):
                    if not args.channel_id or hit["channel_id"] in args.channel_id:
                        rows[hit["channel_id"]] = hit
            source_hits = list(rows.values())
        except (OSError, KeyError, TypeError, ValueError) as exc:
            parser.error(f"Cannot read replay reports: {type(exc).__name__}")
        if not source_hits:
            parser.error("No matching channels in replay reports")
    settings = BatchSettings(target=args.target, candidate_cap=args.candidate_cap,
                             review_cap=args.review_cap, seconds=args.seconds,
                             min_views=args.min_views)
    lock_path = Path(args.cache + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("Another batch is using this cache; retry after it finishes")
        report = run_batch(
            keywords=args.keyword, youtube_key=config.YOUTUBE_API_KEY,
            cache_path=args.cache, report_path=args.report, settings=settings,
            downsub_key=config.DOWNSUB_KEY, atlas_key=config.ATLASCLOUD_KEY,
            model=config.ATLAS_TEXT_MODEL,
            excluded_channel_names=set(args.exclude_name),
            source_hits=source_hits,
        )
        if args.replay_report:
            report["source_reports"] = args.replay_report
            _write_json(args.report, report)
    print(json.dumps({k: report[k] for k in (
        "elapsed_seconds", "discovery_seconds", "content_passes", "content_reviews",
        "requests", "stop_reason",
    )}, indent=2))


if __name__ == "__main__":
    main()
