import json
import threading
from types import SimpleNamespace

import pytest

from core import niche_light as light
from webapp import database as db
from webapp.niche_store import NicheStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'IS_PG', False)
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'leads.db')
    db._init_db()
    db.create_niche_hunt_run(job_id='light', trigger='test', keywords=[], request={'target_channels': 3, 'enrich_existing': True})
    return NicheStore('light', 'worker')


def test_public_renderers_parse_fresh_longform_and_skip_ads_shorts_and_stale():
    def video(vid, age='3 days ago', duration='10:00'):
        return {'videoRenderer': {'videoId': vid, 'title': {'simpleText': 'Sample title'},
            'ownerText': {'runs': [{'text': 'Channel', 'navigationEndpoint': {'browseEndpoint': {'browseId': 'UCnew'}}}]},
            'viewCountText': {'simpleText': '40K views'}, 'publishedTimeText': {'simpleText': age},
            'lengthText': {'simpleText': duration}}}
    data = {'items': [video('abcdefghijk'), video('bcdefghijkl', duration='0:50'),
        video('cdefghijklm', age='3 years ago'), {'adSlotRenderer': video('defghijklmn')},
        {'reelShelfRenderer': video('efghijklmno')} ]}
    cards = light.public_cards(data, 'broad')
    assert [c['video_id'] for c in cards] == ['abcdefghijk']
    assert cards[0]['channel_id'] == 'UCnew' and cards[0]['view_count'] == 40000
    assert light.initial_data('var ytInitialData = ' + json.dumps(data) + '; other()') == data
    with pytest.raises(ValueError): light.initial_data('<html>Unavailable</html>')


def test_lockup_related_renderer_uses_channel_and_accessible_date():
    data = {'lockupViewModel': {'contentType': 'LOCKUP_CONTENT_TYPE_VIDEO', 'contentId': 'abcdefghijk',
        'metadata': {'lockupMetadataViewModel': {'title': {'content': 'Different topic'},
            'image': {'browseEndpoint': {'browseId': 'UCother'}},
            'metadata': {'contentMetadataViewModel': {'metadataRows': [
                {'metadataParts': [{'text': {'content': 'Other channel'}}]},
                {'metadataParts': [{'text': {'content': '42K'}, 'accessibilityLabel': '42 thousand views'},
                    {'text': {'content': '3d ago'}, 'accessibilityLabel': '3 days ago'}]}]}}}},
        'contentImage': {'thumbnailBadgeViewModel': {'text': '25:16'}}}}
    cards = light.public_cards(data, 'related')
    assert len(cards) == 1 and cards[0]['channel_id'] == 'UCother' and cards[0]['duration_sec'] == 1516
    assert cards[0]['view_count'] == 42000


def test_metadata_holds_exact_repetition_without_topic_or_language_rules():
    assert not light.metadata_checks({'sampled_videos': [{'title': 'Same video'}] * 8})['passes']
    assert light.metadata_checks({'sampled_videos': [{'title': t} for t in ['日本の生活', 'Food factories', 'Football', 'Ancient Rome']]})['passes']


def test_bulk_queue_deduplicates_in_one_frontier_and_keeps_fresh_variant(store):
    assert store.enqueue_many([('search', {'query': 'Home'}, 'broad', 0),
        ('search', {'query': 'home'}, 'learned', 0), ('search', {'query': 'home', 'upload_month': True}, 'fresh', 0)]) == 2
    assert len(store.claim_tasks('search', limit=10)) == 2


def test_leads_are_atomic_unclassified_new_only_and_cancel_safe(store):
    assert store.claim_run()
    hit = {'channel_id': 'UCnew', 'channel_name': 'New lead', 'recent_avg_views': 50000}
    store.enqueue('channel', {'channel_id': 'UCnew'}, source='broad')
    task = store.claim_tasks('channel')[0]
    with pytest.raises(ValueError): store.publish_lead(hit, {'passes': False}, task, {})
    assert store.publish_lead(hit, {'passes': True}, task, {'channel_id': 'UCnew'}) == 'added'
    row = db.list_niche_channels()[0]
    assert row['quality_status'] == 'metrics' and row['avatar_confidence'] == 'unknown'
    assert db.count_niche_channels(ai_presenter=True) == 0
    original = row['first_seen_at']
    store.enqueue('channel', {'channel_id': 'UCknown', 'review_existing': True}, source='existing')
    known = store.claim_tasks('channel')[0]
    db.upsert_niche_channel({**hit, 'channel_id': 'UCknown', 'channel_name': 'Known'})
    assert store.publish_lead({**hit, 'channel_id': 'UCknown', 'recent_avg_views': 999999}, {'passes': True}, known, {}) == 'already_present'
    assert db.list_niche_channels(q='Known')[0]['recent_avg_views'] == 50000
    store.enqueue('channel', {'channel_id': 'UCcancel'}, source='broad')
    cancelled = store.claim_tasks('channel')[0]
    db.cancel_niche_hunt_run('light')
    assert store.publish_lead({**hit, 'channel_id': 'UCcancel'}, {'passes': True}, cancelled, {}) == 'cancelled'
    assert db.list_niche_channels(q='New lead')[0]['first_seen_at'] == original


def test_light_run_overlaps_scouting_skips_cleanup_and_never_calls_models(store, monkeypatch):
    import config
    import core.niche_cloud as cloud
    from core.niche_batch import EvidenceClient
    db.upsert_niche_channel({'channel_id': 'UCknown', 'channel_name': 'Known', 'recent_avg_views': 100})
    monkeypatch.setattr(NicheStore, 'seed_channels', lambda self, **k: [])
    def forbidden(*a, **k): raise AssertionError('Heavy or cleanup work invoked')
    monkeypatch.setattr(EvidenceClient, '__init__', forbidden)
    monkeypatch.setattr(NicheStore, 'enrichment_candidates', forbidden)
    monkeypatch.setattr(NicheStore, 'unreviewed_ids', forbidden)
    monkeypatch.setattr(cloud.Explorer, '__init__', forbidden)
    monkeypatch.setattr(config, 'YOUTUBE_API_KEY', 'test')
    fake_youtube = SimpleNamespace(_http=SimpleNamespace(niche_stats={'requests': 0, 'seconds': 0}))
    monkeypatch.setattr(light, '_yt', lambda *a, **k: fake_youtube)
    monkeypatch.setattr(light, '_fetch_videos', lambda yt, ids: [{'video_id': v, 'channel_id': 'UC'+v,
        'view_count': 50000, 'title': v, 'url': 'https://youtube.com/watch?v='+v} for v in ids])
    enrichment_seen = threading.Event()
    overlapped = []
    def fetch(task, deadline):
        if task['source'] == 'fresh': overlapped.append(enrichment_seen.wait(2))
        cards = [{'video_id': str(i), 'channel_id': 'UC'+str(i), 'view_count': 50000, 'title': 'Sample '+str(i)} for i in range(5)]
        cards.append({'video_id': 'known', 'channel_id': 'UCknown', 'view_count': 100})
        return cards, {'requests': 1, 'seconds': 0.01}
    monkeypatch.setattr(light, 'fetch_public', fetch)
    def enrich(**kwargs):
        assert 'UCknown' not in kwargs['direct_channel_ids']
        enrichment_seen.set()
        return {'hits': [{'channel_id': cid, 'channel_name': cid, 'channel_url': 'https://youtube.com/channel/'+cid,
            'recent_avg_views': 50000, 'sampled_videos': []} for cid in kwargs['direct_channel_ids']], 'meta': {}}
    monkeypatch.setattr(light, 'run_niche_finder', enrich)
    monkeypatch.setattr(light, 'eligible_performance', lambda *a: {'passes': True, 'median_views': 50000})
    result = cloud.run_cloud_hunt('light')
    assert result['added'] == 3 and result['stop_reason'] == 'target_reached'
    assert result['model_requests'] == result['transcript_requests'] == result['image_requests'] == result['existing_enriched'] == 0
    assert all(overlapped) and enrichment_seen.is_set()
    assert db.get_niche_hunt_run_by_job_id('light')['status'] == 'completed'
    assert db.list_niche_channels(q='Known')[0]['recent_avg_views'] == 100


def test_niche_machine_resources_and_credentials_are_independent_of_cooks(monkeypatch):
    import config
    from webapp import fly_bridge
    monkeypatch.setattr(config, 'COOK_ON_FLY', True)
    monkeypatch.setattr(config, 'FLY_NICHE_IMAGE', 'registry/image@sha256:test')
    monkeypatch.setattr(config, 'FLY_NICHE_MEMORY_MB', 512)
    monkeypatch.setattr(config, 'FLY_COOK_CPUS', 8)
    monkeypatch.setattr(config, 'FLY_COOK_MEMORY_MB', 8192)
    monkeypatch.setattr(config, 'YOUTUBE_API_KEY', 'test')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://test')
    calls=[]
    monkeypatch.setattr(fly_bridge, '_request', lambda method,path,body: calls.append(body) or {'id': 'scout'})
    assert fly_bridge.spawn_niche_scrape_machine('light') == 'scout'
    machine = calls[0]['config']
    assert machine['guest'] == {'cpu_kind': 'shared', 'cpus': 1, 'memory_mb': 512}
    assert machine['auto_destroy'] is True
    assert not {'GEMINI_KEY', 'ATLASCLOUD_KEY', 'DOWNSUB_KEY', 'HEYGEN_KEY', 'SPACES_SECRET'} & machine['env'].keys()
