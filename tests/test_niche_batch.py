from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from core import niche_batch as batch
from core import niche_finder as finder
from core.niche_scraper import _parse_search_cards, parse_relative_age_days


def video(name, views, age):
    return {"title": name, "url": f"https://www.youtube.com/watch?v={name}",
            "view_count": views, "duration_sec": 600,
            "published_at": (datetime.now(timezone.utc) - timedelta(days=age)).isoformat()}


@pytest.mark.parametrize("label,expected", [
    ("2d ago", 2), ("3w ago", 21), ("1mo ago", 30),
    ("22h ago", 22 / 24), ("3 months ago", 90), ("30m ago", 30 / 1440),
])
def test_compact_search_dates(label, expected):
    assert parse_relative_age_days(label) == pytest.approx(expected)


def test_collaboration_cards_without_channel_link_survive_until_api_resolution():
    cards = [
        {"title": "Home advice", "videoUrl": "https://youtube.com/watch?v=abcdefghijk",
         "channelName": "Two collaborators", "channelUrl": "",
         "durationText": "23:01", "meta": ["125K • 2d ago"]},
        {"title": "Short", "videoUrl": "https://youtube.com/watch?v=12345678901",
         "durationText": "0:51", "meta": ["2M", "1d ago"]},
        {"title": "Old", "videoUrl": "https://youtube.com/watch?v=98765432101",
         "durationText": "10:00", "meta": ["1M views", "1 year ago"]},
    ]
    hits = _parse_search_cards(cards, "home", 120, 240)
    assert len(hits) == 1
    assert hits[0]["view_count"] == 125000
    assert hits[0]["age_days"] == 2


def test_viral_hit_cannot_hide_zero_view_mature_uploads():
    hit = {"sampled_videos": [video("viral", 1_000_000, 20)] +
                            [video(str(i), 0, 10 + i) for i in range(7)]}
    result = batch.performance_evidence(hit, batch.BatchSettings())
    assert result["sample_size"] == 8
    assert result["wins"] == 1
    assert result["median_views"] == 0
    assert not result["passes"]


def test_young_uploads_are_not_called_flops_and_sparse_channels_stay_unverified():
    hit = {"sampled_videos": [video(str(i), 15000, 10 + i) for i in range(4)] +
                            [video("new", 2, 1)]}
    assert batch.performance_evidence(hit, batch.BatchSettings())["passes"]
    sparse = {"sampled_videos": [video("only", 500000, 8)]}
    assert not batch.performance_evidence(sparse, batch.BatchSettings())["passes"]


def test_cache_respects_expiry_and_changed_acceptance_criteria():
    settings = batch.BatchSettings()
    cache = {"channels": {
        "rejected": {"signature": batch.cache_signature(settings), "expires_at": 200},
        "expired": {"signature": batch.cache_signature(settings), "expires_at": 99},
    }}
    assert batch.excluded_from_cache(cache, settings, now=100) == {"rejected"}
    assert not batch.excluded_from_cache(cache, batch.BatchSettings(min_views=50000), now=100)


def test_processing_cap_applies_before_upload_history_reads(monkeypatch):
    from core import niche_scraper
    seeds = [{"video_id": str(i), "channel_id": f"UC{i}",
              "view_count": i * 10000, "published_at": video("x", 1, 10)["published_at"],
              "source_keyword": "home"} for i in range(1, 4)]
    monkeypatch.setattr(niche_scraper, "scrape_keywords", lambda *a, **kw: seeds)
    monkeypatch.setattr(finder, "_yt", lambda key: object())
    monkeypatch.setattr(finder, "_fetch_videos", lambda *a: seeds)
    monkeypatch.setattr(finder, "_fetch_channels", lambda *a: {
        f"UC{i}": {"channel_name": f"Channel {i}", "subscriber_count": 2000,
                   "video_count": 10, "avg_views_per_video": 20000,
                   "published_at": video("x", 1, 30)["published_at"],
                   "uploads_playlist": str(i)} for i in range(1, 4)
    })
    reads = []
    def uploads(yt, playlist, **kw):
        reads.append(playlist)
        return [dict(video("abcdefghijk", 20000, 10), video_id="abcdefghijk")]
    monkeypatch.setattr(finder, "_longform_from_uploads", uploads)
    result = finder.run_niche_finder(api_key="fake", keywords=["home"],
                                   max_enrich_channels=1, excluded_channel_ids={"UC3"})
    assert reads == ["2"]
    assert result["meta"]["channels_selected_for_enrichment"] == 1
    assert result["meta"]["cached_channels_encountered"] == 1


def test_expired_discovery_budget_stops_api_work(monkeypatch):
    from core import niche_scraper
    monkeypatch.setattr(niche_scraper, "scrape_keywords", lambda *a, **kw: [{"video_id": "abcdefghijk"}])
    def no_api(*a, **kw):
        raise AssertionError("API enrichment must not start after deadline")
    monkeypatch.setattr(finder, "_yt", no_api)
    result = finder.run_niche_finder(api_key="fake", keywords=["home"], deadline=0)
    assert result["hits"] == []


def test_search_reuses_one_browser_and_deduplicates_before_global_cap(monkeypatch):
    from core import niche_scraper
    import playwright.sync_api
    launches, searches, closes = [], [], []
    page = object()
    browser = SimpleNamespace(
        new_context=lambda **kw: SimpleNamespace(new_page=lambda: page),
        close=lambda: closes.append(True),
    )
    def launch(**kw):
        launches.append(True)
        return browser
    class Playwright:
        def __enter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch=launch))
        def __exit__(self, *args): pass
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", Playwright)
    def search(kw, **settings):
        assert settings["page"] is page
        searches.append(kw)
        return [{"video_id": "shared"}, {"video_id": kw}]
    monkeypatch.setattr(niche_scraper, "scrape_keyword_search", search)
    hits = niche_scraper.scrape_keywords(["a", "a", "b", "c"], max_videos=3)
    assert [h["video_id"] for h in hits] == ["shared", "a", "b"]
    assert searches == ["a", "b"]
    assert len(launches) == len(closes) == 1


def test_missing_content_evidence_cannot_auto_pass(monkeypatch):
    client = batch.EvidenceClient(deadline=10**12, atlas_key="fake")
    monkeypatch.setattr(client, "transcript", lambda vid: {"text": "", "source": "", "error": "missing"})
    class Response:
        status_code = 404
        content = b""
        headers = {}
        def raise_for_status(self):
            pass
        def json(self):
            return {"choices": [{"message": {"content":
                '{"decision":"pass","reproducible":true,"reasons":["looks good"],"concerns":[]}'}}]}
    monkeypatch.setattr(client.session, "get", lambda *a, **kw: Response())
    def no_model_call(*a, **kw):
        raise AssertionError("Missing evidence must not consume a model call")
    monkeypatch.setattr(client.session, "post", no_model_call)
    result = client.review({"channel_name": "Test", "sampled_videos": [
        video("abcdefghijk", 20000, 10), video("12345678901", 20000, 11)]})
    assert result["decision"] == "review"
    assert client.counters["model_requests"] == 0
    client.close()


@pytest.mark.parametrize("response,expected", [
    ("not JSON", "review"),
    ({"decision": "pass", "reproducible": True, "reasons": ["specific advice"],
      "concerns": [], "niche": "home care", "production_format": "presenter",
      "avatar_confidence": "likely", "caveats": ["Possible audience fatigue"]}, "pass"),
    ({"decision": "pass", "reproducible": True, "reasons": ["specific advice"],
      "concerns": ["evidence conflict"], "caveats": [], "niche": "home care", "production_format": "presenter"}, "review"),
    ({"decision": "pass", "reproducible": True, "reasons": [42],
      "concerns": [], "caveats": [], "niche": "home care", "production_format": "presenter"}, "review"),
])
def test_model_screen_fails_closed_and_never_confirms_avatar_identity(monkeypatch, response, expected):
    client = batch.EvidenceClient(deadline=10**12, atlas_key="fake")
    monkeypatch.setattr(client, "transcript", lambda vid: {
        "text": "Specific captioned advice. " * 100, "source": "captions", "error": "",
    })
    monkeypatch.setattr(client.session, "get", lambda url, **kw: SimpleNamespace(
        status_code=200, content=(url.encode() * 100), headers={"content-type": "image/jpeg"},
    ))
    response_text = response if isinstance(response, str) else json.dumps(response)
    monkeypatch.setattr(client.session, "post", lambda *a, **kw: SimpleNamespace(
        status_code=200,
        raise_for_status=lambda: None,
        json=lambda: {"choices": [{"message": {"content": response_text}}]},
    ))
    result = client.review({"channel_name": "Test", "sampled_videos": [
        video("abcdefghijk", 20000, 10), video("12345678901", 20000, 11)]})
    assert result["decision"] == expected
    if expected == "pass":
        assert result["avatar_confidence"] == "unknown"
    client.close()


def test_target_stops_content_work_and_results_are_checkpointed(monkeypatch, tmp_path):
    hits = [{"channel_id": f"UC{i}", "channel_name": f"Channel {i}",
             "sampled_videos": [video(str(j), 20000, 10 + j) for j in range(4)]}
            for i in range(5)]
    monkeypatch.setattr(batch, "run_niche_finder", lambda **kw: {"hits": hits, "meta": {}})
    calls = []
    class Client:
        counters = {}
        def __init__(self, **kw): pass
        def close(self): pass
        def review(self, hit):
            calls.append(hit["channel_id"])
            return {"decision": "pass"}
    monkeypatch.setattr(batch, "EvidenceClient", Client)
    result = batch.run_batch(
        keywords=["home"], youtube_key="fake",
        cache_path=tmp_path / "cache.json", report_path=tmp_path / "report.json",
        settings=batch.BatchSettings(target=2), progress=lambda x: None,
    )
    assert len(calls) == 2
    assert result["stop_reason"] == "target_reached"
    assert len(result["candidates"]) == 5
    assert len(result["results"]) == 2
    assert (tmp_path / "cache.json").exists()
    assert (tmp_path / "report.json").exists()


def test_saved_report_replay_avoids_new_discovery_work(monkeypatch, tmp_path):
    def no_discovery(**kw):
        raise AssertionError("Replay must reuse statistics")
    monkeypatch.setattr(batch, "run_niche_finder", no_discovery)
    hit = {"channel_id": "test", "channel_name": "Test", "sampled_videos": []}
    result = batch.run_batch(
        keywords=[], youtube_key="", source_hits=[hit],
        cache_path=tmp_path / "cache.json", report_path=tmp_path / "report.json",
        progress=lambda x: None,
    )
    assert result["discovery"]["discovery"] == "saved_report_replay"
    assert result["content_reviews"] == 0
    assert result["rubric_version"] == batch.RUBRIC_VERSION


def test_native_fallback_preserves_images_and_skips_payment_blocked_atlas(monkeypatch):
    import requests
    calls=[]
    def post(url,**kwargs):
        calls.append((url,kwargs))
        response=requests.Response()
        response.status_code=402 if 'atlascloud' in url else 200
        response._content=json.dumps({'candidates':[{'content':{'parts':[{'text':'{"ready":true}'}]}}],
            'usageMetadata':{'promptTokenCount':17,'candidatesTokenCount':4}}).encode()
        return response
    state={}
    client=batch.EvidenceClient(deadline=10**12,atlas_key='atlas-test',gemini_key='native-test',provider_state=state)
    monkeypatch.setattr(client.session,'post',post)
    messages=[{'role':'system','content':'Check evidence'}, {'role':'user','content':[
        {'type':'text','text':'sample'}, {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,YWJj'}}]}]
    data=client.model_json(messages)
    client.model_json(messages)
    assert len(calls)==3 and sum('atlascloud' in x[0] for x in calls)==1
    assert state['atlas_payment_blocked'] and state['active']=='gemini'
    native=calls[1]
    assert native[1]['headers']=={'x-goog-api-key':'native-test'}
    assert 'native-test' not in native[0]
    assert native[1]['json']['contents'][0]['parts'][1]['inlineData']=={'mimeType':'image/jpeg','data':'YWJj'}
    assert data['usage']['prompt_tokens']==17 and client.counters['model_requests']==3


def test_native_transient_retry_is_bounded(monkeypatch):
    import requests
    client=batch.EvidenceClient(deadline=10**12,gemini_key='test')
    calls=[]
    def post(*a,**k):
        calls.append(1)
        response=requests.Response();response.status_code=503;response._content=b'{}'
        return response
    monkeypatch.setattr(client.session,'post',post)
    monkeypatch.setattr(batch.time,'sleep',lambda *_:None)
    with pytest.raises(requests.HTTPError): client.model_json([{'role':'user','content':'test'}])
    assert len(calls)==2 and client.provider_state['native_failures']==1


def test_atlas_other_errors_do_not_switch_providers(monkeypatch):
    import requests
    client=batch.EvidenceClient(deadline=10**12,atlas_key='test',gemini_key='test')
    calls=[]
    def post(url,**k):
        calls.append(url)
        response=requests.Response();response.status_code=400;response._content=b'{}'
        return response
    monkeypatch.setattr(client.session,'post',post)
    with pytest.raises(requests.HTTPError): client.model_json([{'role':'user','content':'test'}])
    assert len(calls)==1 and 'atlascloud' in calls[0]
