import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from core import niche_cloud as cloud
from webapp import database as db
from webapp.niche_store import NicheStore

@pytest.fixture
def store(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'IS_PG',False)
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'catalog.db',raising=False)
    db._init_db()
    db.create_niche_hunt_run(job_id='test',trigger='test',keywords=[],request={})
    return NicheStore('test','worker-a')

def task(store,cid='new'):
    store.enqueue('channel',{'channel_id':cid},source='broad')
    return store.claim_tasks('channel')[0]

def hit(cid='new'):
    return {'channel_id':cid,'channel_name':cid,'channel_url':'https://youtube.com/channel/'+cid,
            'recent_avg_views':50000,'videos_last_14d':5}

REVIEW={'decision':'pass','production_format':'presenter','avatar_confidence':'likely'}
PERF={'passes':True,'median_views':50000}

def test_frontier_deduplicates_and_recovers_expired_work(store):
    assert store.enqueue('search',{'query':'Home Advice'},source='broad')
    assert not store.enqueue('search',{'query':'home advice'},source='learned')
    first=store.claim_tasks('search')[0]
    other=NicheStore('test','worker-b')
    assert not other.claim_tasks('search')
    with db._conn() as c: c.execute('UPDATE niche_discovery_tasks SET lease_until=0')
    assert other.claim_tasks('search')[0]['id']==first['id']
    store.finish_task(first,{'stale':True})
    assert not store.completed_results()

def test_only_one_catalog_lease_survives_crash(store):
    assert store.claim_run()
    other=NicheStore('test','worker-b')
    assert not other.claim_run()
    store.release_run()
    assert other.claim_run()

def test_daily_budget_is_shared_and_bounded(store):
    assert store.reserve_budget('youtube',25,50)
    assert NicheStore('test','b').reserve_budget('youtube',25,50)
    assert not store.reserve_budget('youtube',1,50)

def test_publication_and_checkpoint_are_atomic_and_new_only(store):
    assert store.claim_run()
    t=task(store)
    assert store.publish(hit(),REVIEW,PERF,t,{'channel_id':'new'})=='added'
    assert store.completed_results()[0]['status']=='added'
    assert db.get_niche_hunt_run_by_job_id('test')['channels_upserted']==1
    assert db.list_niche_channels()[0]['avatar_confidence']=='likely'
    # Replaying a task cannot refresh or count the existing channel again.
    assert store.publish(hit(),REVIEW,PERF,t,{'channel_id':'new'})=='cancelled'
    assert db.count_niche_channels()==1

def test_cancelled_worker_cannot_publish_or_overwrite_final_status(store):
    assert store.claim_run()
    t=task(store)
    run=db.cancel_niche_hunt_run('test')
    assert store.publish(hit(),REVIEW,PERF,t,{})=='cancelled'
    db.finish_niche_hunt_run(run['id'],status='completed',channels_upserted=99)
    assert db.count_niche_channels()==0
    assert db.get_niche_hunt_run_by_job_id('test')['status']=='cancelled'

def test_insertion_date_is_immutable_and_newest_filter_paginates_stably(store):
    for cid,stamp,activity in [('old',100,100),('a',200,0),('b',200,20),('c',300,0)]:
        db.upsert_niche_channel({**hit(cid),'videos_last_14d':activity})
        with db._conn() as c: c.execute('UPDATE niche_channels SET first_seen_at=? WHERE channel_id=?',(stamp,cid))
    db.upsert_niche_channel(hit('old'))
    assert db.count_niche_channels(added_since=200)==3
    pages=[db.list_niche_channels(sort='newest',added_since=200,active_recently=True,limit=1,offset=i)[0]['channel_id'] for i in range(3)]
    assert pages==['c','a','b']
    assert db.list_niche_channels(q='old')[0]['first_seen_at']==100

def test_review_cache_invalidates_when_rubric_or_profile_changes(store):
    sig=cloud.HuntSettings().signature('model')
    store.remember('hold',sig,{},60)
    assert store.cached_ids(sig)=={'hold'}
    assert not store.cached_ids(cloud.HuntSettings(profile='avatar').signature('model'))

@pytest.mark.parametrize('text,expected',[
    ('Our host is fictional and the videos use AI.',True),
    ('Our AI-generated presenter explains gardening.',True),
    ('We do not use AI avatars.',False),
    ('This video contains AI-generated scenery.',False),
    ('The host is not fictional.',False),
])
def test_host_disclosure_does_not_confuse_ai_broll_or_negations(text,expected):
    assert cloud.explicit_avatar_claim(text)==expected

def test_face_alone_and_ai_broll_cannot_become_avatar_channel():
    assert cloud.avatar_status({'presenter_visible':True},{})=='unknown'
    assert cloud.avatar_status({'presenter_visible':False},{'explicit_avatar_claim':True})=='not_presenter'
    assert cloud.avatar_status({'presenter_visible':True,'avatar_style_confidence':'unknown'}, {'ai_video_samples':2})=='unknown'
    assert cloud.avatar_status({'presenter_visible':True}, {'explicit_avatar_claim':True})=='disclosed'
    assert cloud.avatar_status({'presenter_visible':True,'avatar_style_confidence':'high','avatar_observations':['virtual host','same virtual setup']}, {'ai_video_samples':2})=='likely'

def test_hunt_uses_a_live_frontier_and_publishes_only_review_passes(store,monkeypatch):
    import config
    request={'time_budget_seconds':300,'target_channels':1,'profile':'balanced'}
    with db._conn() as c: c.execute('UPDATE niche_hunt_runs SET request_json=?',(json.dumps(request),))
    api=SimpleNamespace(_http=SimpleNamespace(niche_stats={'requests':0,'seconds':0}))
    monkeypatch.setattr(cloud,'_yt',lambda *a,**k:api)
    monkeypatch.setattr(config,'YOUTUBE_API_KEY','test')
    monkeypatch.setattr(cloud,'SIMPLE_PROBES',['unexpected topic'])
    monkeypatch.setattr(NicheStore,'seed_channels',lambda *a,**k:[])
    class Explorer:
        def __init__(self,*a): pass
        def close(self): pass
        def search(self,query,settings): return [{'video_id':'abcdefghijk'}]
        def related(self,vid): return []
        def avatar_evidence(self,hit): return {}
    monkeypatch.setattr(cloud,'Explorer',Explorer)
    monkeypatch.setattr(cloud,'_fetch_videos',lambda *a:[{'video_id':'abcdefghijk','channel_id':'new','view_count':50000}])
    h=hit()
    h['sampled_videos']=[{'title':'Unexpected home repair discoveries','url':'https://youtube.com/watch?v=abcdefghijk',
        'duration_sec':600,'view_count':50000,'published_at':(datetime.now(timezone.utc)-timedelta(days=10+i)).isoformat()} for i in range(6)]
    monkeypatch.setattr(cloud,'run_niche_finder',lambda **kw:{'hits':[h]})
    class Evidence:
        def __init__(self,**kw): self.counters={'model_requests':1}
        def review(self,hit): return {**REVIEW,'reproducible':True}
        def close(self): pass
    monkeypatch.setattr(cloud,'EvidenceClient',Evidence)
    result=cloud.run_cloud_hunt('test')
    assert result['added']==1
    assert result['stop_reason']=='target_reached'
    assert db.count_niche_channels()==1
    assert db.get_niche_hunt_run_by_job_id('test')['status']=='completed'
    with db._conn() as c:
        assert c.execute("SELECT count(*) FROM niche_discovery_tasks WHERE source='learned'").fetchone()[0]>0
        assert c.execute('SELECT count(*) FROM niche_discovery_lease').fetchone()[0]==0
