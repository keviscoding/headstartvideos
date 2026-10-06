"""Small, bounded metadata scout. Produces research leads, not content verdicts."""
from __future__ import annotations

import hashlib
import json
import random
import re
import resource
import sys
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

import requests

from core.niche_cloud import HuntSettings, eligible_performance, learned_queries, video_id
from core.niche_daily_keywords import SIMPLE_PROBES
from core.niche_finder import _fetch_videos, _yt, run_niche_finder
from core.niche_scraper import _parse_search_cards, _search_url

LIGHT_RUBRIC = "metadata-leads-v1"
LANES = (("search", "broad"), ("search", "fresh"), ("related", "related"), ("search", "learned"))


def _walk(value):
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            yield item
            # Advertisements and Shorts are not discovery candidates.
            stack.extend(v for k, v in reversed(list(item.items()))
                         if not re.search(r"(?:^adSlot|^promoted|^reel|shorts)", k, re.I))
        elif isinstance(item, list):
            stack.extend(reversed(item))


def _text(value):
    if not isinstance(value, dict): return str(value or "")
    return value.get("simpleText") or value.get("content") or "".join(r.get("text", "") for r in value.get("runs", []))


def initial_data(html):
    match = re.search(r'(?:var\s+ytInitialData\s*=|window\["ytInitialData"\]\s*=|ytInitialData\s*=)\s*', html)
    if not match: raise ValueError("Public YouTube result data unavailable")
    return json.JSONDecoder().raw_decode(html[match.end():])[0]


def public_cards(data, source, max_age_days=120):
    raw = []
    for node in _walk(data):
        renderer = node.get("videoRenderer") or node.get("compactVideoRenderer")
        if renderer:
            vid = renderer.get("videoId", "")
            byline = renderer.get("ownerText") or renderer.get("shortBylineText") or renderer.get("longBylineText") or {}
            endpoint = next((r.get("navigationEndpoint", {}) for r in byline.get("runs", []) if r.get("navigationEndpoint")), {})
            cid = (endpoint.get("browseEndpoint") or {}).get("browseId", "")
            raw.append(dict(title=_text(renderer.get("title")), videoUrl="https://www.youtube.com/watch?v=" + vid,
                channelName=_text(byline), channelUrl="https://www.youtube.com/channel/" + cid,
                meta=[_text(renderer.get("viewCountText")), _text(renderer.get("publishedTimeText"))],
                durationText=_text(renderer.get("lengthText"))))
        lockup = node.get("lockupViewModel")
        if not lockup or lockup.get("contentType") != "LOCKUP_CONTENT_TYPE_VIDEO": continue
        metadata = (lockup.get("metadata") or {}).get("lockupMetadataViewModel", {})
        rows = (metadata.get("metadata") or {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
        parts = [p for row in rows for p in row.get("metadataParts", [])]
        cid = next((n["browseEndpoint"].get("browseId", "") for n in _walk(metadata.get("image", {})) if "browseEndpoint" in n), "")
        duration = next((n["thumbnailBadgeViewModel"].get("text", "") for n in _walk(lockup.get("contentImage", {}))
                         if "thumbnailBadgeViewModel" in n and re.fullmatch(r"(?:\d+:)?\d+:\d{2}", n["thumbnailBadgeViewModel"].get("text", ""))), "")
        raw.append(dict(title=_text(metadata.get("title")), videoUrl="https://www.youtube.com/watch?v=" + lockup.get("contentId", ""),
            channelName=_text(parts[0].get("text")) if parts else "", channelUrl="https://www.youtube.com/channel/" + cid,
            meta=[_text(p.get("text")) or p.get("accessibilityLabel", "") for p in parts[1:]], durationText=duration))
    return list({c["video_id"]: c for c in _parse_search_cards(raw, source, max_age_days, 240)}.values())


def fetch_public(task, deadline):
    """Read public result data without Chromium, video playback, or downloads."""
    payload = task["payload"]
    source = payload.get("query") or "related video"
    url = (_search_url(source, use_duration_filter=False, upload_month=bool(payload.get("upload_month")))
           if task["kind"] == "search" else "https://www.youtube.com/watch?v=" + payload["video_id"])
    started = time.monotonic()
    reads = 0
    def timeout():
        left = deadline - time.monotonic()
        if left <= 0: raise TimeoutError("Discovery deadline reached")
        return min(10, left)
    with requests.Session() as session:
        session.headers.update({"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"})
        response = session.get(url + ("&" if "?" in url else "?") + "hl=en", timeout=timeout())
        reads += 1
        response.raise_for_status()
        if len(response.content) > 6_000_000: raise ValueError("Public result page too large")
        html = response.text
        data = initial_data(html)
        cards = public_cards(data, source, 31 if payload.get("upload_month") else 120)
        # One extra search page gives variety without a long scrolling session.
        token = next((n["continuationCommand"].get("token") for n in _walk(data) if "continuationCommand" in n), None)
        version_match = re.search(r'"INNERTUBE_CLIENT_VERSION"\s*:\s*"([^"\\]+)"', html)
        version = version_match[1] if version_match else None
        if task["kind"] == "search" and token and version and time.monotonic() < deadline:
            try:
                response = session.post("https://www.youtube.com/youtubei/v1/search", json={
                    "context": {"client": {"clientName": "WEB", "clientVersion": version, "hl": "en"}},
                    "continuation": token}, timeout=timeout())
                reads += 1
                response.raise_for_status()
                cards += public_cards(response.json(), source, 31 if payload.get("upload_month") else 120)
            except (requests.RequestException, ValueError): pass  # Initial results are still usable.
    cards = list({c["video_id"]: c for c in cards}.values())[:40]
    if task["kind"] == "related": cards = [c for c in cards if c["video_id"] != payload["video_id"]]
    return cards, {"requests": reads, "seconds": round(time.monotonic() - started, 3)}


def metadata_checks(hit):
    """Only obvious repeated-title noise; no subject, style, or identity classifier."""
    titles = [re.sub(r"\W+", " ", v.get("title", "")).casefold().strip()
              for v in hit.get("sampled_videos", [])[:12]]
    titles = [t for t in titles if t]
    repeated = len(titles) >= 4 and len(set(titles)) <= len(titles) / 2
    return {"passes": not repeated, "repeated_titles": repeated, "content_assessed": False}


def run_light_hunt(job_id):
    import config
    from webapp import database as db
    from webapp.niche_store import NicheStore

    run = db.get_niche_hunt_run_by_job_id(job_id)
    if not run or run["status"] != "running": return {"skipped": "not running"}
    settings = HuntSettings.from_request(run.get("request") or {})
    store = NicheStore(job_id, str(uuid.uuid4()))
    if not store.claim_run():
        with db._conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT job_id FROM niche_discovery_lease WHERE lease_key='catalog'")
            current = cur.fetchone()
        if current and dict(current)['job_id'] != job_id:
            db.finish_niche_hunt_run(run['id'], status='error', error='Another discovery run owns the catalog lease')
        return {"skipped": "leased"}
    wall_started = float(run["started_at"])
    deadline = time.monotonic() + max(0, settings.seconds - (time.time() - wall_started))
    signature = hashlib.sha256(json.dumps({"rubric": LIGHT_RUBRIC, "criteria": settings.signature("metadata")}, sort_keys=True).encode()).hexdigest()
    existing = store.existing_ids()
    cached = store.cached_ids(signature)
    previous = run.get("meta") or {}
    results = [r for r in store.completed_results() if r.get("status") == "added"]
    stats = {"runner": "fly", "rubric": LIGHT_RUBRIC, "mode": "performance_leads", "profile": "balanced",
        "added": len(results), "results": results, "enriched": 0, "searches": 0, "related_pages": 0,
        "existing_skipped": 0, "cached_skipped": 0, "errors": 0, "reviewed": 0, "full_reviews": 0,
        "model_requests": 0, "transcript_requests": 0, "image_requests": 0, "existing_enriched": 0,
        "public_http": {"requests": 0, "seconds": 0}, "scout_workers": 2, "settings": {"seconds": settings.seconds,
        "target": settings.target, "candidate_cap": settings.candidate_cap, "api_cap": settings.api_cap}}
    for key in ("enriched", "searches", "related_pages", "existing_skipped", "cached_skipped", "errors", "public_http"):
        if key in previous: stats[key] = previous[key]
    stop = threading.Event()
    reservation = {"left": 0, "exhausted": False}
    prior_api = previous.get("youtube_api") or {}
    def reserve_request():
        if reservation["exhausted"]: return False
        if not reservation["left"]:
            if not store.reserve_budget("youtube_read", 25, 6000):
                reservation["exhausted"] = True
                return False
            reservation["left"] = 25
        reservation["left"] -= 1
        return True
    youtube = _yt(config.YOUTUBE_API_KEY, deadline=deadline, max_requests=max(1, settings.api_cap - prior_api.get("requests", 0)), reserve_request=reserve_request)
    def snapshot():
        api = youtube._http.niche_stats
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return {**stats, "results": list(stats["results"]), "elapsed_seconds": round(time.time() - wall_started, 2),
            "heartbeat_at": time.time(), "youtube_api": {k: v + prior_api.get(k, 0) for k, v in api.items()},
            "process": {"peak_rss_mb": round(usage.ru_maxrss / (1024 * 1024 if sys.platform == 'darwin' else 1024), 1),
                "cpu_seconds": round(usage.ru_utime + usage.ru_stime, 2)}}
    def heartbeat():
        while not stop.wait(20):
            try:
                if not store.heartbeat(snapshot()): stop.set()
            except Exception: pass
    def progress(message):
        print("[niche-light] " + message, flush=True)
        db.append_niche_hunt_progress(job_id, message)
    def enqueue_expansion(videos, depth):
        if depth >= 2: return
        entries = []
        for v in videos[:1]:
            vid = video_id(v.get("url", "")) or v.get("video_id")
            if vid: entries.append(("related", {"video_id": vid}, "related", depth + 1))
        for query in learned_queries([v.get("title", "") for v in videos], limit=1):
            entries.append(("search", {"query": query}, "learned", depth + 1))
        store.enqueue_many(entries)
    def queue_cards(cards, task):
        eligible = []
        for c in cards:
            if c.get("channel_id") in existing:
                stats["existing_skipped"] += 1
                # Old entries can supply neighbours, but never consume history reads.
                if c.get("view_count", 0) >= 10000: enqueue_expansion([c], task["depth"])
            elif c.get("channel_id") in cached: stats["cached_skipped"] += 1
            else: eligible.append(c)
        videos = _fetch_videos(youtube, [c["video_id"] for c in eligible]) if eligible else []
        groups = {}
        for v in videos:
            cid = v["channel_id"]
            if cid in existing or cid in cached: continue
            v["source_keyword"] = task["payload"].get("query") or "related video"
            groups.setdefault(cid, []).append(v)
        # Mix top-view cards with a shuffled tail; no niche whitelist or winner-only loop.
        ranked = sorted(groups, key=lambda cid: max(v["view_count"] for v in groups[cid]), reverse=True)
        tail = ranked[6:]
        random.Random(job_id + task["task_key"]).shuffle(tail)
        top = ranked[:6]
        ordered = [cid for pair in zip(top, tail) for cid in pair] + top[len(tail):] + tail[len(top):]
        store.enqueue_many([("channel", {"channel_id": cid, "videos": groups[cid]}, task["source"], task["depth"]) for cid in ordered])
        return {"videos": len(videos), "channels": len(groups)}

    thread = threading.Thread(target=heartbeat, daemon=True)
    pool = ThreadPoolExecutor(max_workers=2)
    pending = {}
    iteration = 0
    consecutive_errors = 0
    try:
        progress("Light discovery: unseen channels, performance checks, no content/identity models")
        thread.start()
        probes = list(dict.fromkeys(SIMPLE_PROBES))
        random.Random(job_id).shuffle(probes)
        entries = [("search", {"query": q}, "broad", 0) for q in list(dict.fromkeys((run.get("keywords") or []) + probes))[:100]]
        entries += [("search", {"query": q, "upload_month": True}, "fresh", 0) for q in probes[:50]]
        for seed in store.seed_channels(limit=24):
            try: videos = json.loads(seed["recent_videos_json"] or seed["popular_videos_json"] or "[]")
            except ValueError: continue
            for v in videos[:1]:
                vid = video_id(v.get("url", ""))
                if vid: entries.append(("related", {"video_id": vid}, "related", 0))
                for q in learned_queries([v.get("title", "")], limit=1):
                    entries.append(("search", {"query": q}, "learned", 0))
        store.enqueue_many(entries)
        while not stop.is_set() and time.monotonic() < deadline and stats["added"] < settings.target:
            if snapshot()["youtube_api"]["requests"] >= settings.api_cap or reservation["exhausted"]: break
            if stats["enriched"] >= settings.candidate_cap: break
            while len(pending) < 2 and stats["searches"] + stats["related_pages"] + len(pending) < settings.search_cap:
                kind, source = LANES[iteration % len(LANES)]
                iteration += 1
                tasks = store.claim_tasks(kind, source=source)
                if not tasks: tasks = store.claim_tasks("search") or store.claim_tasks("related")
                if not tasks: break
                task = tasks[0]
                pending[pool.submit(fetch_public, task, deadline)] = task
            for future, task in list(pending.items()):
                if not future.done(): continue
                del pending[future]
                try:
                    cards, cost = future.result()
                    stats["public_http"]["requests"] += cost["requests"]
                    stats["public_http"]["seconds"] += cost["seconds"]
                    stats["searches" if task["kind"] == "search" else "related_pages"] += 1
                    store.finish_task(task, queue_cards(cards, task))
                    consecutive_errors = 0
                except Exception as error:
                    stats["errors"] += 1
                    consecutive_errors += 1
                    store.finish_task(task, {"error": type(error).__name__}, error=True)
                    if consecutive_errors >= 5: stop.set()
            channel_tasks = []
            for lane in ("broad", "fresh", "related", "learned"):
                remaining = min(24, settings.candidate_cap - stats["enriched"]) - len(channel_tasks)
                if remaining <= 0: break
                channel_tasks += store.claim_tasks("channel", source=lane, limit=min(6, remaining))
            if not channel_tasks:
                if not pending: break
                wait(tuple(pending), timeout=min(1, max(0, deadline-time.monotonic())), return_when=FIRST_COMPLETED)
                continue
            kept = []
            for task in channel_tasks:
                cid = task["payload"]["channel_id"]
                if cid in existing or cid in cached:
                    store.finish_task(task, {"channel_id": cid, "status": "already_present" if cid in existing else "cached"})
                else: kept.append(task)
            if not kept: continue
            result = run_niche_finder(api_key=config.YOUTUBE_API_KEY, keywords=["adaptive"],
                discovered_videos=[v for t in kept for v in t["payload"].get("videos", [])],
                direct_channel_ids=[t["payload"]["channel_id"] for t in kept], youtube_client=youtube,
                max_enrich_channels=len(kept), max_subscribers=settings.max_subscribers, deadline=deadline)
            hits = {h["channel_id"]: h for h in result["hits"]}
            for task in kept:
                if stop.is_set() or time.monotonic() >= deadline or stats["added"] >= settings.target: break
                cid = task["payload"]["channel_id"]
                stats["enriched"] += 1
                hit = hits.get(cid)
                if not hit:
                    failed = result.get("meta", {}).get("enrichment_errors", {}).get(cid)
                    incomplete = failed or time.monotonic() >= deadline or snapshot()['youtube_api']['requests'] >= settings.api_cap or reservation['exhausted']
                    outcome = {"channel_id": cid, "status": "enrichment_error" if incomplete else "ineligible"}
                    store.finish_task(task, outcome)
                    if not incomplete: store.remember(cid, signature, outcome, 86400)
                    continue
                performance = eligible_performance(hit, settings)
                checks = metadata_checks(hit)
                if performance["median_views"] >= 5000: enqueue_expansion(hit.get("sampled_videos", []), task["depth"])
                outcome = {"channel_id": cid, "channel_name": hit["channel_name"], "channel_url": hit["channel_url"],
                    "status": "performance_hold" if not performance["passes"] else "metadata_hold" if not checks["passes"] else "ready",
                    "performance": performance, "metadata_checks": checks}
                if performance["passes"] and checks["passes"]:
                    outcome["status"] = store.publish_lead(hit, performance, task, outcome)
                    if outcome["status"] == "added":
                        existing.add(cid)
                        stats["added"] += 1
                        stats["results"].append(outcome)
                        progress(f"Added {stats['added']}/{settings.target}: {hit['channel_name']} ({performance['median_views']:,} mature median; performance lead)")
                    elif outcome["status"] == "cancelled": stop.set()
                store.finish_task(task, outcome)
                if outcome["status"] not in {"ready", "cancelled"}:
                    store.remember(cid, signature, outcome, 21600 if not performance["passes"] else 86400)
            if not store.heartbeat(snapshot()): stop.set()
        reason = ("public_discovery_unavailable" if consecutive_errors >= 5 else "daily_api_budget" if reservation["exhausted"]
            else "cancelled" if stop.is_set() else "target_reached" if stats["added"] >= settings.target
            else "time_budget" if time.monotonic() >= deadline else "work_budget_or_frontier_exhausted")
        meta = {**snapshot(), "stop_reason": reason}
        current = db.get_niche_hunt_run_by_job_id(job_id)
        if current and current["status"] == "running":
            db.finish_niche_hunt_run(run["id"], status="error" if consecutive_errors >= 5 else "completed", meta=meta,
                channels_upserted=stats["added"], error="Public discovery unavailable; frontier retained" if consecutive_errors >= 5 else "")
        progress(f"Finished: {stats['added']} new performance leads in {meta['elapsed_seconds']:.0f}s ({reason})")
        return meta
    except Exception as error:
        db.finish_niche_hunt_run(run["id"], status="error", meta=snapshot(), channels_upserted=stats["added"], error=type(error).__name__)
        raise
    finally:
        stop.set()
        if thread.is_alive(): thread.join(timeout=3)
        pool.shutdown(wait=True, cancel_futures=True)
        store.release_run()
