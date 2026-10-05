"""Adaptive discovery and evidence screening with durable cloud checkpoints."""
from __future__ import annotations

import hashlib
import json
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from urllib.parse import parse_qs, urlparse

from core.niche_batch import BatchSettings, EvidenceClient, performance_evidence
from core.niche_daily_keywords import SIMPLE_PROBES
from core.niche_finder import _fetch_videos, _longform_from_uploads, _yt, run_niche_finder
from core.niche_scraper import _parse_search_cards, scrape_keyword_search

CLOUD_RUBRIC = "cloud-content-v6-presenter-priority"
SEED_HANDLES = ["GlenPritchardBuilds", "OpalRowe1945", "TheJapaneseMethod0"]
AVATAR_CLAIM = re.compile(
    r"\b(?:ai[- ](?:generated|powered|created|animated)\s+(?:host|presenter|avatar|character)|"
    r"(?:host|presenter|character|persona)\s+(?:is|are)\s+(?:fictional|synthetic|ai[- ]generated)|"
    r"(?:ai|virtual|digital)\s+(?:avatar|presenter)|fictional\s+(?:host|presenter|persona))\b", re.I,
)


@dataclass(frozen=True)
class HuntSettings:
    profile: str = "balanced"
    seconds: int = 1800
    target: int = 20
    candidate_cap: int = 300
    review_cap: int = 100
    search_cap: int = 100
    api_cap: int = 2000
    per_query: int = 35
    scrolls: int = 5
    max_subscribers: int = 300000
    min_views: int = 10000
    min_median_views: int = 20000
    min_recent_avg_views: int = 0
    min_hit_rate: float = 0.75
    review_workers: int = 3
    enrich_existing: bool = False
    existing_review_cap: int = 80

    @classmethod
    def from_request(cls, request):
        if request.get("profile", "balanced") not in {"balanced", "avatar"}:
            raise ValueError("Unknown discovery profile")
        return cls(
            profile=request.get("profile", "balanced"),
            seconds=max(300, min(7200, int(request.get("time_budget_seconds",1800)))),
            target=max(1,min(200,int(request.get("target_channels",20)))),
            candidate_cap=max(20,min(1200,int(request.get("candidate_cap",300)))),
            review_cap=max(5,min(400,int(request.get("review_cap",100)))),
            search_cap=max(20,min(300,int(request.get("search_cap",100)))),
            api_cap=max(100,min(4000,int(request.get("api_cap",2000)))),
            max_subscribers=max(100,min(1000000,int(request.get("max_subscribers",300000)))),
            min_recent_avg_views=max(0,int(request.get("min_recent_avg_views",0))),
            enrich_existing=request.get("enrich_existing") is True,
            existing_review_cap=max(0,min(200,int(request.get("existing_review_cap",80)))),
        )

    def signature(self, model):
        criteria={k:asdict(self)[k] for k in ("profile","min_views","min_median_views","min_hit_rate","min_recent_avg_views","max_subscribers")}
        return hashlib.sha256(json.dumps({**criteria,"rubric":CLOUD_RUBRIC,"model":model},sort_keys=True).encode()).hexdigest()


def video_id(url):
    parsed=urlparse(url or "")
    value=(parse_qs(parsed.query).get("v") or [""])[0]
    return value if re.fullmatch(r"[A-Za-z0-9_-]{11}",value) else ""


def learned_queries(titles, limit=2):
    """Learn search language from encountered videos without choosing a niche taxonomy."""
    out=[]
    for title in titles:
        words=re.findall(r"[A-Za-z][A-Za-z'-]+",re.sub(r"\|.*$","",title or ""))
        words=[w for w in words if w.lower() not in {"the","a","an","this","these","those","my","your","you","i","full","movie","video",
            "is","are","with","why","how","what","to","and","or","but","of","for","in","on","at","from","about",
            "should","could","would","will","never","anymore","only","before","after","still","here","no"}]
        query=" ".join(words[:7])
        if 3 <= len(words) and query.casefold() not in {q.casefold() for q in out}:
            out.append(query)
        if len(out)>=limit: break
    return out


def eligible_performance(hit, settings):
    trial=BatchSettings(min_views=settings.min_views,min_hit_rate=settings.min_hit_rate)
    evidence=performance_evidence(hit,trial)
    evidence["min_median_views"]=settings.min_median_views
    evidence["recent_average_views"]=int(hit.get("recent_avg_views") or 0)
    evidence["min_recent_avg_views"]=settings.min_recent_avg_views
    evidence["passes"]=bool(evidence["passes"] and evidence["median_views"]>=settings.min_median_views
        and evidence["recent_average_views"]>=settings.min_recent_avg_views)
    return evidence


def avatar_status(review, evidence):
    """Identity claims require disclosure; limited visual evidence remains a candidate."""
    triage=evidence.get("visual_triage") or {}
    if evidence.get("user_reference") is True and triage.get("presenter_visible") is True:
        return "reference"
    clips=triage.get("video_observations") or []
    valid_clips=(len(clips)==2 and all(isinstance(c,dict) and c.get("presenter_visible") is True
        and isinstance(c.get("observations"),list) and len(c["observations"])>=2
        and all(isinstance(o,dict) and isinstance(o.get("detail"),str) and o["detail"].strip()
            and not isinstance(o.get("second"),bool) and isinstance(o.get("second"),(int,float))
            and 0<=o["second"]<=45 for o in c["observations"]) for c in clips)
        and len({c.get("video_url") for c in clips})==2)
    if (triage.get("evidence_kind")=="video_openings" and triage.get("presenter_visible") is True
        and triage.get("avatar_style_confidence")=="high" and valid_clips
        and len(triage.get("avatar_observations") or [])>=4):
        return "disclosed" if evidence.get("explicit_avatar_claim") else "likely"
    if (valid_clips and triage.get("presenter_visible") is True and evidence.get("ai_video_samples",0)>=2
        and review.get("ai_reproducible") is True):
        return "possible"
    if review.get("presenter_visible") is not True:
        return "not_presenter"
    if evidence.get("explicit_avatar_claim"):
        return "disclosed"
    observations=review.get("avatar_observations")
    supported=(evidence.get("ai_video_samples",0)>=2 and review.get("avatar_style_confidence") in {"high","medium"})
    visual=(triage.get("presenter_visible") is True and triage.get("avatar_style_confidence")=="high"
            and review.get("avatar_style_confidence")=="high")
    if ((supported or visual)
        and isinstance(observations,list) and len(observations)>=2
        and all(isinstance(x,str) and x.strip() for x in observations)):
        return "likely"
    return "unknown"


def explicit_avatar_claim(text):
    # A disclaimer about AI footage, or a negated host claim, is not host disclosure.
    for sentence in re.split(r"[.!?\n]", text or ""):
        if re.search(r"\b(?:no|not|never|without|don't|doesn't|do not|isn't|aren't)\b",sentence,re.I):
            continue
        if AVATAR_CLAIM.search(sentence):
            return True
    return False


class Explorer:
    def __init__(self, deadline):
        from playwright.sync_api import sync_playwright
        self.deadline=deadline
        self.consent_attempts=0
        self.playwright=sync_playwright().start()
        self.browser=self.playwright.chromium.launch(headless=True,args=["--no-sandbox"])
        self.page=self.browser.new_context(locale="en-US",viewport={"width":1400,"height":900}).new_page()

    def close(self):
        self.browser.close()
        self.playwright.stop()

    def navigate(self, url):
        left=self.deadline-time.monotonic()
        if left<=0: raise TimeoutError("Hunt deadline reached")
        self.page.goto(url,wait_until="domcontentloaded",timeout=min(25000,int(left*1000)))
        self.page.wait_for_timeout(1200)
        if self.consent_attempts<2:
            self.consent_attempts+=1
            try:
                self.page.get_by_role("button",name=re.compile(r"^Reject (?:all|the use of cookies)",re.I)).first.wait_for(state="visible",timeout=3500)
            except Exception:
                pass
        for text in ("Reject all","Accept all"):
            button=self.page.get_by_role("button",name=re.compile(r"^"+text.split()[0]+r" (?:all|the use of cookies)",re.I))
            if button.count() and button.first.is_visible():
                button.first.click(timeout=1500)
                break

    def search(self, query, settings, upload_month=False):
        return scrape_keyword_search(query,page=self.page,scroll_count=settings.scrolls,
            max_results=settings.per_query,max_age_days=31 if upload_month else 120,
            deadline=self.deadline,use_duration_filter=False,upload_month=upload_month)

    def related(self, vid):
        self.navigate(f"https://www.youtube.com/watch?v={vid}")
        raw=self.page.evaluate(r"""() => Array.from(document.querySelectorAll(
            'ytd-compact-video-renderer, yt-lockup-view-model')).map(v => {
            const a=v.querySelector('a#video-title, h3 a, a[href*="/watch?v="]');
            const c=v.querySelector('ytd-channel-name a, a[href*="/@"], a[href*="/channel/"]');
            const lines=(v.innerText || '').split('\n');
            const duration=(v.innerText || '').match(/\b(?:\d{1,2}:)?\d{1,2}:\d{2}\b/);
            return {title:a?.title || a?.textContent || '', videoUrl:a?.href || '',
                channelName:c?.textContent || '',channelUrl:c?.href || '',meta:lines,
                durationText:duration ? duration[0] : ''};
        })""")
        return _parse_search_cards(raw,"related video",120,240)[:30]

    def avatar_evidence(self, hit):
        sources=[]
        descriptions=[hit.get("channel_description","")]
        for video in (hit.get("sampled_videos") or [])[:2]:
            descriptions.append(video.get("description", ""))
            vid=video_id(video.get("url"))
            if not vid: continue
            label=False
            try:
                self.navigate(f"https://www.youtube.com/watch?v={vid}")
                expand=self.page.locator("ytd-watch-metadata #expand")
                try:
                    expand.first.wait_for(state="visible",timeout=2500)
                    expand.first.click(timeout=2500)
                    self.page.wait_for_timeout(350)
                except Exception:
                    pass
                label=self.page.evaluate("""() => {
                    const metadata=document.querySelector('#primary-inner') || document.querySelector('ytd-watch-metadata');
                    if (!metadata) return false;
                    const labels=Array.from(metadata.querySelectorAll('[aria-label],img[alt]'))
                        .map(n => n.getAttribute('aria-label') || n.getAttribute('alt') || '');
                    const panels=Array.from(metadata.querySelectorAll('ytd-info-panel-content-renderer,ytd-video-description-infocards-section-renderer'));
                    return labels.some(t => /content (?:was )?(?:made|created|generated) (?:using|with) AI|altered or synthetic/i.test(t))
                        || panels.some(n => /altered or synthetic content/i.test(n.innerText || ''));
                }""")
            except Exception:
                pass
            sources.append({"url":f"https://www.youtube.com/watch?v={vid}","ai_label":bool(label)})
        explicit=any(explicit_avatar_claim(d) for d in descriptions)
        return {"sources":sources,"ai_video_samples":sum(s["ai_label"] for s in sources),
                "explicit_avatar_claim":explicit,
                "scope":"Public disclosure and sampled visual evidence; a realistic face alone does not prove a synthetic host."}


def run_cloud_hunt(job_id):
    import config
    from webapp import database as db
    from webapp.niche_store import NicheStore
    import uuid

    run=db.get_niche_hunt_run_by_job_id(job_id)
    if not run or run["status"]!="running": return {"skipped":"not running"}
    settings=HuntSettings.from_request(run.get("request") or {})
    store=NicheStore(job_id,str(uuid.uuid4()))
    if not store.claim_run():
        with db._conn() as conn:
            cur=conn.cursor()
            cur.execute("SELECT job_id FROM niche_discovery_lease WHERE lease_key='catalog'")
            current=cur.fetchone()
        if current and dict(current)["job_id"]!=job_id:
            db.finish_niche_hunt_run(run["id"],status="error",error="Another discovery run owns the catalog lease")
        return {"skipped":"leased"}
    started=time.monotonic()
    wall_started=float(run["started_at"])
    deadline=started+max(0,settings.seconds-(time.time()-wall_started))
    signature=settings.signature(config.ATLAS_TEXT_MODEL+"|"+config.GEMINI_TEXT_MODEL)
    existing=store.existing_ids()
    unreviewed_existing=store.unreviewed_ids() if settings.enrich_existing else set()
    cached=store.cached_ids(signature)
    stop=threading.Event()
    provider_state={}
    stats={"runner":"fly","rubric":CLOUD_RUBRIC,"profile":settings.profile,"settings":asdict(settings),
        "added":0,"reviewed":0,"enriched":0,"searches":0,"related_pages":0,"cached_skipped":0,
        "existing_skipped":0,"results":[],"model_requests":0,"prompt_tokens":0,"completion_tokens":0,
        "transcript_requests":0,"image_requests":0,"errors":0,"triaged":0,"full_reviews":0,
        "existing_queued":0,"existing_enriched":0}
    previous=run.get("meta") or {}
    if previous and previous.get("rubric")!=CLOUD_RUBRIC:
        store.reconsider_review_holds()
    for key in list(stats):
        if isinstance(stats[key],int) and isinstance(previous.get(key),int): stats[key]=previous[key]
    prior=store.completed_results()
    stats["results"]=[r for r in prior if r.get("status") in {"added","enriched_existing"}]
    stats["added"]=sum(r["status"]=="added" for r in stats["results"])
    stats["existing_enriched"]=sum(r["status"]=="enriched_existing" for r in stats["results"])
    stats["enriched"]=max(stats["enriched"],len(prior))
    stats["reviewed"]=max(stats["reviewed"],sum(bool(r.get("content_review")) for r in prior))
    lock=threading.Lock()

    def snapshot():
        with lock: meta={**stats,"results":list(stats["results"])}
        meta.update(elapsed_seconds=round(time.time()-wall_started,2),heartbeat_at=time.time(),tasks=store.task_counts())
        meta["review_provider"]=dict(provider_state)
        meta["youtube_api"]=api_stats()
        return meta

    def heartbeat():
        while not stop.wait(20):
            try:
                if not store.heartbeat(snapshot()): stop.set()
            except Exception:
                # One transient database failure does not revoke a live lease.
                pass

    def progress(msg):
        print(f"[niche-cloud] {msg}",flush=True)
        db.append_niche_hunt_progress(job_id,msg)

    prior_api=(previous.get("youtube_api") or {}).get("requests",0)
    reservation={"left":0,"exhausted":False}
    def reserve_request():
        if reservation["exhausted"]: return False
        if reservation["left"]<=0:
            if not store.reserve_budget("youtube_read",25,6000):
                reservation["exhausted"]=True
                return False
            reservation["left"]=25
        reservation["left"]-=1
        return True
    youtube=_yt(config.YOUTUBE_API_KEY,deadline=deadline,max_requests=max(1,settings.api_cap-prior_api),reserve_request=reserve_request)
    def api_stats():
        current=dict(youtube._http.niche_stats)
        current["requests"]+=prior_api
        current["seconds"]+=(previous.get("youtube_api") or {}).get("seconds",0)
        return current
    explorer=None
    reference_ids=set()
    pool=ThreadPoolExecutor(max_workers=settings.review_workers)
    thread=threading.Thread(target=heartbeat,daemon=True)
    thread.start()

    def queue_channel(cid, videos, source, depth=0):
        known=cid in existing
        if cid in cached: return False
        if known and (cid not in unreviewed_existing or stats["existing_queued"]>=settings.existing_review_cap):
            return False
        queued=store.enqueue("channel",{"channel_id":cid,"videos":videos,"review_existing":known,"origin_source":source},source="existing" if known else source,depth=depth)
        if queued and known: stats["existing_queued"]+=1
        return queued

    def add_expansion(hit, depth, source="related"):
        if depth>=3: return
        vids=hit.get("sampled_videos") or hit.get("recent_videos") or []
        for v in vids[:2]:
            vid=video_id(v.get("url"))
            if vid: store.enqueue("related",{"video_id":vid},source="avatar_seed" if source=="avatar_seed" else "related",depth=depth+1)
        for query in learned_queries([v.get("title","") for v in vids]):
            store.enqueue("search",{"query":query},source="learned",depth=depth+1)
            store.enqueue("search",{"query":query,"upload_month":True},source="fresh",depth=depth+1)

    def queue_cards(cards, task):
        ids=list(dict.fromkeys(c["video_id"] for c in cards))
        videos=_fetch_videos(youtube,ids) if ids else []
        grouped={}
        for v in videos:
            v["source_keyword"]=task["payload"].get("query") or "related video"
            grouped.setdefault(v["channel_id"],[]).append(v)
        # A random portion of each encountered pool survives, not only top virals.
        ordered=sorted(grouped,key=lambda cid:max(v["view_count"] for v in grouped[cid]),reverse=True)
        tail=ordered[8:]
        random.Random(job_id+task["task_key"]).shuffle(tail)
        # Interleave exploration with proven videos rather than putting every
        # lower-view result behind the virals from the first query.
        top=ordered[:8]
        ordered=[cid for pair in zip(top,tail) for cid in pair]+top[len(tail):]+tail[len(top):]
        for cid in ordered:
            if cid in existing:
                if not queue_channel(cid,grouped[cid],task["source"],task["depth"]):
                    stats["existing_skipped"]+=1
                # Existing channels can bridge to unseen neighbours without rescoring.
                if task["depth"]<2:
                    v=max(grouped[cid],key=lambda v:v["view_count"])
                    store.enqueue("related",{"video_id":v["video_id"]},source="avatar_seed" if task["source"]=="avatar_seed" else "related",depth=task["depth"]+1)
                continue
            if cid in cached:
                stats["cached_skipped"]+=1
                continue
            queue_channel(cid,grouped[cid],task["source"],task["depth"])
        return {"videos":len(videos),"channels":len(grouped)}

    def content_review(hit):
        client=EvidenceClient(deadline=deadline,downsub_key=config.DOWNSUB_KEY,
            atlas_key=config.ATLASCLOUD_KEY,model=config.ATLAS_TEXT_MODEL,avatar_screen=True,
            gemini_key=config.GEMINI_KEY,gemini_model=config.GEMINI_TEXT_MODEL,provider_state=provider_state)
        try:
            review=client.review(hit)
            review["screen_stage"]="content"
            # Establish useful, AI-reproducible content before spending on video
            # openings. Uncertain presenter identity cannot veto other formats.
            if (review.get("decision")=="pass" and review.get("ai_reproducible") is True
                and (review.get("presenter_visible") is True
                    or (settings.profile=="avatar" and review.get("production_format") in {"presenter","mixed"}))):
                if store.reserve_budget("content_review",1,300):
                    hit["avatar_evidence"]["visual_triage"]=client.presenter_style(hit)
                else:
                    review["avatar_check_skipped"]="daily_model_budget"
            return review,client.counters.copy()
        finally: client.close()

    try:
        progress(f"Adaptive {settings.profile} hunt: up to {settings.candidate_cap} channels / {settings.review_cap} reviews / {settings.seconds//60} minutes")
        store.heartbeat(snapshot())
        probes=list(dict.fromkeys(SIMPLE_PROBES))
        random.Random(job_id).shuffle(probes)
        for query in (run.get("keywords") or [])+probes[:100]:
            store.enqueue("search",{"query":query},source="broad")
        for query in probes[:8]:
            store.enqueue("search",{"query":query,"upload_month":True},source="fresh")
        for seed in store.seed_channels(limit=24):
            try: vids=json.loads(seed["recent_videos_json"] or seed["popular_videos_json"] or "[]")
            except ValueError: continue
            for v in vids[:1]:
                vid=video_id(v.get("url"))
                if vid: store.enqueue("related",{"video_id":vid},source="related")
                for query in learned_queries([v.get("title","")],limit=1):
                    store.enqueue("search",{"query":query,"upload_month":True},source="fresh")
        if settings.profile=="avatar":
            for handle in SEED_HANDLES:
                response=youtube.channels().list(part="id,contentDetails",forHandle=handle).execute()
                for item in response.get("items",[]):
                    reference_ids.add(item["id"])
                    playlist=item.get("contentDetails",{}).get("relatedPlaylists",{}).get("uploads","")
                    seed_videos=_longform_from_uploads(youtube,playlist,want=2)
                    for query in learned_queries([v["title"] for v in seed_videos]):
                        store.enqueue("search",{"query":query},source="learned")
                        store.enqueue("search",{"query":query,"upload_month":True},source="fresh")
                    queue_channel(item["id"],seed_videos,"avatar_seed")
                    for v in seed_videos:
                        store.enqueue("related",{"video_id":v["video_id"]},source="avatar_seed")
        if settings.enrich_existing:
            for candidate in store.enrichment_candidates(limit=settings.existing_review_cap):
                queue_channel(candidate['channel_id'],[],"catalog")
        explorer=Explorer(deadline)
        rotation=[("search","broad"),("related",None),("search","learned"),("search","fresh"),("related",None),("search","broad")]
        if settings.profile=="avatar":
            rotation=[("related","avatar_seed"),("search","fresh"),("related","related"),("search","learned"),("related","avatar_seed"),("search","broad")]
        iteration=0
        while not stop.is_set() and time.monotonic()<deadline and stats["added"]<settings.target:
            if api_stats()["requests"]>=settings.api_cap or reservation["exhausted"]: break
            if stats["enriched"]>=settings.candidate_cap or stats["reviewed"]>=settings.review_cap: break
            kind,source=rotation[iteration%len(rotation)]
            iteration+=1
            tasks=[]
            if stats["searches"]+stats["related_pages"]<settings.search_cap:
                tasks=store.claim_tasks(kind,source=source)
                if not tasks: tasks=store.claim_tasks("search") or store.claim_tasks("related")
            for task in tasks:
                try:
                    if task["kind"]=="search":
                        query=task["payload"]["query"]
                        progress(f"Explore {stats['searches']+stats['related_pages']+1}: {query}")
                        cards=explorer.search(query,settings,upload_month=task["payload"].get("upload_month") is True)
                        stats["searches"]+=1
                    else:
                        cards=explorer.related(task["payload"]["video_id"])
                        stats["related_pages"]+=1
                    store.finish_task(task,queue_cards(cards,task))
                except Exception as exc:
                    stats["errors"]+=1
                    store.finish_task(task,{"error":type(exc).__name__},error=True)
            channel_tasks=[]
            # Rotate channel pools as well as queries so one crowded query
            # cannot consume the entire enrichment budget.
            for slot in range(min(5,settings.candidate_cap-stats["enriched"])):
                pools=("broad","related","learned","avatar_seed","fresh","existing" if settings.enrich_existing else "broad")
                wanted=pools[(iteration+slot)%len(pools)]
                channel_tasks.extend(store.claim_tasks("channel",source=wanted) or store.claim_tasks("channel"))
            if not tasks and not channel_tasks: break
            if not channel_tasks: continue
            cids=[t["payload"]["channel_id"] for t in channel_tasks]
            seeds=[v for t in channel_tasks for v in t["payload"].get("videos",[])]
            result=run_niche_finder(api_key=config.YOUTUBE_API_KEY,keywords=["adaptive"],
                discovered_videos=seeds,direct_channel_ids=cids,youtube_client=youtube,
                max_enrich_channels=len(cids),max_subscribers=settings.max_subscribers,
                deadline=deadline,progress=lambda message:None)
            hits={h["channel_id"]:h for h in result["hits"]}
            pending=[]
            for task in channel_tasks:
                cid=task["payload"]["channel_id"]
                stats["enriched"]+=1
                hit=hits.get(cid)
                if not hit:
                    failed=(result.get("meta") or {}).get("enrichment_errors",{}).get(cid)
                    incomplete=bool(failed or time.monotonic()>=deadline or api_stats()["requests"]>=settings.api_cap)
                    outcome={"channel_id":cid,"status":"enrichment_error" if incomplete else "ineligible"}
                    if failed: outcome["error"]=failed
                    store.finish_task(task,outcome)
                    if not incomplete: store.remember(cid,signature,outcome,86400)
                    continue
                perf=eligible_performance(hit,settings)
                if perf["median_views"]>=5000: add_expansion(hit,task["depth"],task["payload"].get("origin_source",task["source"]))
                if not perf["passes"]:
                    outcome={"channel_id":cid,"channel_name":hit["channel_name"],"status":"performance_hold","performance":perf}
                    store.finish_task(task,outcome)
                    store.remember(cid,signature,outcome,21600)
                    continue
                if stats["reviewed"]>=settings.review_cap or stop.is_set() or time.monotonic()>=deadline:
                    continue  # lease can be reclaimed for resume
                hit["avatar_evidence"]=explorer.avatar_evidence(hit)
                hit["avatar_evidence"]["user_reference"]=cid in reference_ids
                if not store.reserve_budget("content_review",1,300):
                    stop.set()
                    stats["budget_exhausted"]="daily_content_reviews"
                    continue
                stats["reviewed"]+=1
                pending.append((task,hit,perf,pool.submit(content_review,hit)))
            for task,hit,perf,future in pending:
                review,counters=future.result()
                review["avatar_sources"]=hit["avatar_evidence"]
                if hit["avatar_evidence"].get("visual_triage"):
                    stats["triaged"]=stats.get("triaged",0)+1
                if review.get("screen_stage")=="content":
                    stats["full_reviews"]=stats.get("full_reviews",0)+1
                queries=review.get("discovery_queries")
                for query in (queries if isinstance(queries,list) else [])[:3]:
                    if isinstance(query,str) and 3<=len(query.strip())<=100 and task["depth"]<3:
                        store.enqueue("search",{"query":query.strip()},source="learned",depth=task["depth"]+1)
                        store.enqueue("search",{"query":query.strip(),"upload_month":True},source="fresh",depth=task["depth"]+1)
                for key,value in counters.items(): stats[key]=stats.get(key,0)+value
                avatar=avatar_status(review,hit["avatar_evidence"])
                review["avatar_confidence"]=avatar if avatar in {"disclosed","likely","reference","possible"} else "unknown"
                status=review["decision"]
                outcome={"channel_id":hit["channel_id"],"channel_name":hit["channel_name"],
                    "channel_url":hit["channel_url"],"status":status,"performance":perf,"content_review":review}
                if (status=="pass" and not stop.is_set() and stats["added"]<settings.target
                    and store.heartbeat(snapshot())):
                    admitted=store.publish(hit,review,perf,task,outcome)
                    outcome["status"]=admitted
                    if admitted=="added":
                        existing.add(hit["channel_id"])
                        stats["added"]+=1
                        stats["results"].append(outcome)
                        progress(f"Added {stats['added']}/{settings.target}: {hit['channel_name']} ({perf['median_views']:,} mature median; avatar {review['avatar_confidence']})")
                    elif admitted=="enriched_existing":
                        stats["existing_enriched"]+=1
                        stats["results"].append(outcome)
                        progress(f"Quality-enriched existing channel: {hit['channel_name']} ({perf['median_views']:,} mature median; avatar {review['avatar_confidence']})")
                    elif admitted=="cancelled": stop.set()
                elif status=="pass":
                    outcome["status"]="ready"
                store.finish_task(task,outcome)
                screen_error=review.get("screen_error") or (review.get("avatar_triage") or {}).get("error")
                if outcome["status"] not in {"ready","cancelled"} and not screen_error:
                    store.remember(hit["channel_id"],signature,outcome,604800 if status=="reject" else 21600 if status=="avatar_hold" else 86400)
            stats["youtube_api"]=api_stats()
            if provider_state.get("atlas_payment_blocked") and (not config.GEMINI_KEY or provider_state.get("native_unavailable") or provider_state.get("native_failures",0)>=3):
                stats["budget_exhausted"]="review_providers_unavailable"
                stop.set()
            store.heartbeat(snapshot())
        reason=(stats.get("budget_exhausted") or ("daily_api_budget" if reservation["exhausted"]
            else "cancelled" if stop.is_set() else "target_reached" if stats["added"]>=settings.target
            else "time_budget" if time.monotonic()>=deadline else "work_budget_or_frontier_exhausted"))
        meta=snapshot()
        meta["stop_reason"]=reason
        meta["youtube_api"]=api_stats()
        current=db.get_niche_hunt_run_by_job_id(job_id)
        if current and current["status"]=="running":
            unavailable=reason=="review_providers_unavailable"
            db.finish_niche_hunt_run(run["id"],status="error" if unavailable else "completed",meta=meta,channels_upserted=stats["added"],error="Review providers unavailable; frontier retained" if unavailable else "")
        progress(f"Finished: {stats['added']} additions, {stats['existing_enriched']} existing quality updates in {meta['elapsed_seconds']:.0f}s ({reason})")
        return meta
    except Exception as exc:
        meta=snapshot()
        db.finish_niche_hunt_run(run["id"],status="error",meta=meta,channels_upserted=stats["added"],error=type(exc).__name__)
        raise
    finally:
        stop.set()
        thread.join(timeout=3)
        pool.shutdown(wait=True,cancel_futures=True)
        try:
            if explorer: explorer.close()
        finally:
            store.release_run()
