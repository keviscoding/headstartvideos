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

REVIEW={'decision':'pass','production_format':'presenter','avatar_confidence':'likely','ai_reproducible':True}
PERF={'passes':True,'median_views':50000}

def test_frontier_deduplicates_and_recovers_expired_work(store):
    assert store.enqueue('search',{'query':'Home Advice'},source='broad')
    assert not store.enqueue('search',{'query':'home advice'},source='learned')
    assert store.enqueue('search',{'query':'Home Advice','upload_month':True},source='fresh')
    first=store.claim_tasks('search')[0]
    other=NicheStore('test','worker-b')
    assert not other.claim_tasks('search',source='broad')
    with db._conn() as c: c.execute('UPDATE niche_discovery_tasks SET lease_until=0')
    assert other.claim_tasks('search',source='broad')[0]['id']==first['id']
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


@pytest.mark.parametrize('active',[0,1])
def test_existing_enrichment_preserves_dates_and_does_not_count_or_reactivate(store,active):
    db.upsert_niche_channel(hit('known'))
    with db._conn() as c:
        c.execute('UPDATE niche_channels SET first_seen_at=100,active=?',(active,))
    assert store.unreviewed_ids()==({'known'} if active else set())
    assert store.claim_run()
    store.enqueue('channel',{'channel_id':'known','review_existing':True},source='avatar_seed')
    t=store.claim_tasks('channel')[0]
    outcome=store.publish({**hit('known'),'recent_avg_views':60000},REVIEW,PERF,t,{'channel_id':'known'})
    assert outcome==('enriched_existing' if active else 'already_present')
    assert db.get_niche_hunt_run_by_job_id('test')['channels_upserted']==0
    with db._conn() as c:
        row=dict(c.execute('SELECT * FROM niche_channels').fetchone())
        assert row['first_seen_at']==100 and row['active']==active
        assert row['recent_avg_views']==(60000 if active else 50000)
        assert row['quality_status']==('screened' if active else '')
    if active:
        assert store.completed_results()[0]['status']=='enriched_existing'
        assert not store.unreviewed_ids()
        assert db.count_niche_channels(ai_presenter=True)==1
        assert db.list_niche_channels(ai_presenter=True)[0]['channel_id']=='known'
        assert db.count_niche_channels(ai_presenter=True,added_since=200)==0
    else:
        assert db.count_niche_channels(ai_presenter=True)==0

def test_review_cache_invalidates_when_rubric_or_profile_changes(store):
    sig=cloud.HuntSettings().signature('model')
    store.remember('hold',sig,{},60)
    assert store.cached_ids(sig)=={'hold'}
    assert not store.cached_ids(cloud.HuntSettings(profile='avatar').signature('model'))
    assert not store.cached_ids(cloud.HuntSettings(max_subscribers=2000).signature('model'))
    assert not store.cached_ids(cloud.HuntSettings(min_recent_avg_views=100000).signature('model'))


def test_admin_average_cutoff_is_separate_from_mature_median_and_small_subscriber_cap():
    settings=cloud.HuntSettings.from_request({'min_recent_avg_views':100000,'max_subscribers':2000})
    assert settings.max_subscribers==2000
    candidate={**hit(),'recent_avg_views':80000,'sampled_videos':[
        {'published_at':(datetime.now(timezone.utc)-timedelta(days=10+i)).isoformat(),
         'view_count':150000,'duration_sec':600} for i in range(6)]}
    evidence=cloud.eligible_performance(candidate,settings)
    assert evidence['median_views']==150000 and evidence['recent_average_views']==80000
    assert not evidence['passes']
    assert cloud.eligible_performance(candidate,cloud.HuntSettings())['passes']

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
    clip={'evidence_kind':'video_openings','presenter_visible':True,'avatar_style_confidence':'high',
        'video_observations':[{'video_url':str(i),'presenter_visible':True,'observations':[
            {'second':5,'detail':'observed rendering seam'},{'second':20,'detail':'observed artificial geometry'}]} for i in range(2)],
        'avatar_observations':['observed rendering seam']*4}
    assert cloud.avatar_status({'presenter_visible':False},{'visual_triage':clip})=='likely'
    clip['avatar_style_confidence']='unknown'
    assert cloud.avatar_status({'presenter_visible':False},{'visual_triage':clip})=='not_presenter'
    assert cloud.avatar_status({'ai_reproducible':True},{'visual_triage':clip,'ai_video_samples':2})=='possible'
    assert cloud.avatar_status({'ai_reproducible':True},{'visual_triage':clip,'ai_video_samples':1})=='not_presenter'
    assert cloud.avatar_status({'presenter_visible':False},{'visual_triage':clip,'user_reference':True})=='reference'


def test_classifier_change_reconsiders_uncertain_reviews_without_replaying_additions(store):
    assert store.claim_run()
    t=task(store,'added')
    assert store.publish(hit('added'),REVIEW,PERF,t,{'channel_id':'added'})=='added'
    uncertain=task(store,'uncertain')
    store.finish_task(uncertain,{'status':'avatar_hold','performance':{'passes':True}})
    failure=task(store,'weak')
    store.finish_task(failure,{'status':'performance_hold','performance':{'passes':False}})
    store.reconsider_review_holds()
    reclaimed=store.claim_tasks('channel',limit=10)
    assert [t['payload']['channel_id'] for t in reclaimed]==['uncertain']
    assert db.count_niche_channels()==1 and db.get_niche_hunt_run_by_job_id('test')['channels_upserted']==1


def test_database_bootstrap_supports_avatar_filter_before_any_worker(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'IS_PG',False)
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'fresh.db')
    db._init_db()
    assert db.count_niche_channels(ai_presenter=True)==0
    assert db.list_niche_channels(ai_presenter=True)==[]


def test_catalog_enrichment_seed_pool_rotates_sources_and_excludes_hidden_entries(store):
    for cid,source in [('a1','A'),('a2','A'),('b','B'),('hidden','C')]:
        db.upsert_niche_channel({**hit(cid),'source_keyword':source,'video_count':10,'subscriber_count':2000})
    with db._conn() as c: c.execute("UPDATE niche_channels SET active=0 WHERE channel_id='hidden'")
    pool=store.enrichment_candidates(limit=2)
    assert len(pool)==2 and {c['source_keyword'] for c in pool}=={'A','B'}

@pytest.mark.parametrize('provider_unavailable,existing_enrichment,public_ai_format',[(False,False,False),(True,False,False),(False,True,False),(False,False,True)])
def test_hunt_uses_a_live_frontier_and_publishes_only_review_passes(store,monkeypatch,provider_unavailable,existing_enrichment,public_ai_format):
    import config
    request={'time_budget_seconds':300,'target_channels':1,'profile':'avatar','enrich_existing':existing_enrichment}
    if existing_enrichment:
        db.upsert_niche_channel(hit())
        with db._conn() as c: c.execute('UPDATE niche_channels SET first_seen_at=100')
    with db._conn() as c: c.execute('UPDATE niche_hunt_runs SET request_json=?',(json.dumps(request),))
    api=SimpleNamespace(_http=SimpleNamespace(niche_stats={'requests':0,'seconds':0}))
    monkeypatch.setattr(cloud,'_yt',lambda *a,**k:api)
    monkeypatch.setattr(config,'YOUTUBE_API_KEY','test')
    monkeypatch.setattr(cloud,'SIMPLE_PROBES',['unexpected topic'])
    monkeypatch.setattr(cloud,'SEED_HANDLES',[])
    monkeypatch.setattr(NicheStore,'seed_channels',lambda *a,**k:[])
    class Explorer:
        def __init__(self,*a): pass
        def close(self): pass
        def search(self,query,settings,**kw): return [{'video_id':'abcdefghijk'}]
        def related(self,vid): return []
        def avatar_evidence(self,hit): return {'ai_video_samples':2,'explicit_avatar_claim':False} if public_ai_format else {}
    monkeypatch.setattr(cloud,'Explorer',Explorer)
    monkeypatch.setattr(cloud,'_fetch_videos',lambda *a:[{'video_id':'abcdefghijk','channel_id':'new','view_count':50000}])
    h=hit()
    h['sampled_videos']=[{'title':'Unexpected home repair discoveries','url':'https://youtube.com/watch?v=abcdefghijk',
        'duration_sec':600,'view_count':50000,'published_at':(datetime.now(timezone.utc)-timedelta(days=10+i)).isoformat()} for i in range(6)]
    monkeypatch.setattr(cloud,'run_niche_finder',lambda **kw:{'hits':[h]})
    class Evidence:
        def __init__(self,**kw):
            self.counters={'model_requests':1}
            self.state=kw['provider_state']
        def review(self,hit):
            self.state['content_checked']=True
            if provider_unavailable:
                self.state.update(atlas_payment_blocked=True,native_unavailable=429)
                return {'decision':'review','screen_error':'HTTPError'}
            return {**REVIEW,'reproducible':True,'production_format':'animation' if public_ai_format else 'presenter'}
        def presenter_style(self,hit):
            assert self.state.get('content_checked'), 'Content must be reviewed before opening clips'
            self.state['opening_checked']=True
            return {'presenter_visible':False,'avatar_style_confidence':'unknown','avatar_observations':['Animated scene','No host visible']}
        def close(self): pass
    monkeypatch.setattr(cloud,'EvidenceClient',Evidence)
    result=cloud.run_evidence_hunt('test')
    assert result['added']==(0 if provider_unavailable or existing_enrichment else 1)
    assert result['existing_enriched']==(1 if existing_enrichment else 0)
    assert result['stop_reason']==('review_providers_unavailable' if provider_unavailable else 'work_budget_or_frontier_exhausted' if existing_enrichment else 'target_reached')
    assert db.count_niche_channels()==(0 if provider_unavailable else 1)
    assert db.get_niche_hunt_run_by_job_id('test')['status']==('error' if provider_unavailable else 'completed')
    assert result['review_provider'].get('opening_checked',False)==(not provider_unavailable)
    with db._conn() as c:
        assert c.execute("SELECT count(*) FROM niche_discovery_tasks WHERE source='learned'").fetchone()[0]>0
        assert c.execute('SELECT count(*) FROM niche_discovery_lease').fetchone()[0]==0
        if provider_unavailable: assert c.execute('SELECT count(*) FROM niche_discovery_cache').fetchone()[0]==0
    if existing_enrichment:
        assert result['existing_queued']==1
        assert db.list_niche_channels()[0]['first_seen_at']==100
    if public_ai_format:
        assert db.list_niche_channels()[0]['production_format']=='animation'
        assert db.list_niche_channels()[0]['avatar_confidence']=='unknown'
        assert db.count_niche_channels(ai_presenter=True)==0

def test_stale_heartbeat_cannot_erase_an_admission_counter(store):
    assert store.claim_run()
    t=task(store)
    assert store.publish(hit(),REVIEW,PERF,t,{})=='added'
    assert store.heartbeat({'added':0})
    assert db.get_niche_hunt_run_by_job_id('test')['channels_upserted']==1

def test_generic_repeatable_filming_is_not_ai_production(store):
    assert store.claim_run()
    with pytest.raises(ValueError):
        store.publish(hit(),{**REVIEW,'ai_reproducible':False},PERF,task(store),{})
    assert db.count_niche_channels()==0

def test_undisclosed_presenter_requires_strong_evidence_in_both_stages():
    review={'presenter_visible':True,'avatar_style_confidence':'high','avatar_observations':['virtual skin styling','repeated rendered setup']}
    assert cloud.avatar_status(review,{})=='unknown'
    evidence={'visual_triage':{'presenter_visible':True,'avatar_style_confidence':'high'}}
    assert cloud.avatar_status(review,evidence)=='likely'
    assert cloud.avatar_status({**review,'avatar_style_confidence':'medium'},evidence)=='unknown'
    assert cloud.avatar_status({**review,'avatar_style_confidence':'medium'},{'ai_video_samples':2})=='likely'
