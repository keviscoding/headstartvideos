"""Exercise the production route functions without importing unrelated render providers."""
import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
import time
import pytest
from fastapi import Depends,HTTPException
from pydantic import BaseModel,Field,ValidationError


def routes(**overrides):
    tree=ast.parse((Path(__file__).resolve().parents[1]/'webapp/server.py').read_text())
    selected=[]
    for node in tree.body:
        if isinstance(node,(ast.FunctionDef,ast.ClassDef)) and node.name in {'niche_finder_channels','NicheFinderJobRequest','_start_niche_hunt'}:
            if isinstance(node,ast.FunctionDef): node.decorator_list=[]
            selected.append(node)
    namespace={'Depends':Depends,'require_user':lambda:None,'HTTPException':HTTPException,
        'time':SimpleNamespace(time=lambda:1_000_000),'Literal':Literal,'BaseModel':BaseModel,'Field':Field,
        '_niche_finder_can_browse':lambda user:True,**overrides}
    exec(compile(ast.Module(body=selected,type_ignores=[]),'production_routes','exec'),namespace)
    return namespace


def test_count_and_list_use_identical_added_cutoff_and_preserve_other_filters():
    calls=[]
    def catalog(**kwargs): calls.append(kwargs);return []
    def count(**kwargs): calls.append(kwargs);return 12
    ns=routes(list_niche_channels=catalog,count_niche_channels=count)
    result=ns['niche_finder_channels'](user={},added_within_days=7,ai_presenter=True,sort='score',q='test',min_subscribers=5000,offset=40)
    assert result['sort']=='newest'
    assert result['total']==12
    assert calls[0]['added_since']==calls[1]['added_since']==1_000_000-7*86400
    assert calls[0]['min_subscribers']==calls[1]['min_subscribers']==5000
    assert calls[0]['q']==calls[1]['q']=='test'
    assert calls[0]['offset']==40
    assert calls[0]['ai_presenter']==calls[1]['ai_presenter'] is True


def test_long_healthy_cloud_run_is_not_timed_out():
    finished=[]
    ns=routes(get_latest_running_niche_hunt=lambda:{'job_id':'healthy','id':1,'started_at':990000,'meta':{'heartbeat_at':999980}},
              finish_niche_hunt_run=lambda *a,**k:finished.append(k))
    with pytest.raises(HTTPException) as error:
        ns['_start_niche_hunt'](keywords=[],max_per_keyword=0,max_channels=0,min_recent_avg_views=0,
            max_subscribers=300000,trigger='test')
    assert error.value.status_code==409
    assert not finished


def test_cloud_work_budgets_and_profiles_validate():
    request=routes()['NicheFinderJobRequest']
    assert request().profile=='balanced'
    with pytest.raises(ValidationError): request(profile='only_our_favourite_niche')
    with pytest.raises(ValidationError): request(time_budget_seconds=86400)
    with pytest.raises(ValidationError): request(review_cap=100000)


def _fallback_routes(monkeypatch, *, fallback, reason):
    import threading
    import uuid
    import config
    from webapp import fly_bridge
    monkeypatch.setattr(config, 'NICHE_WEB_FALLBACK', fallback, raising=False)
    monkeypatch.setattr(fly_bridge, 'spawn_niche_scrape_machine_with_reason', lambda job_id: ('', reason))
    events = {'progress': [], 'finished': [], 'ran': []}
    done = threading.Event()
    def run_locally(*, job_id, runner):
        events['ran'].append((job_id, runner))
        done.set()
    ns = routes(get_latest_running_niche_hunt=lambda: None, uuid=uuid, config=config, COOK_ON_FLY=True,
        create_niche_hunt_run=lambda **k: 7,
        append_niche_hunt_progress=lambda job_id, msg: events['progress'].append(msg),
        finish_niche_hunt_run=lambda *a, **k: events['finished'].append((a, k)),
        _niche_scrape_lock=threading.Lock(), _run_niche_hunt_locally=run_locally)
    return ns, events, done


def _start(ns):
    return ns['_start_niche_hunt'](keywords=['history'], max_per_keyword=0, max_channels=0,
        min_recent_avg_views=0, max_subscribers=300000, trigger='cron')


def test_refused_fly_worker_runs_light_discovery_on_the_web_server(monkeypatch):
    reason = 'Fly API POST /v1/apps/channelrecipe-cook/machines → 422: {"error":"We require your billing information"}'
    ns, events, done = _fallback_routes(monkeypatch, fallback=True, reason=reason)
    job_id = _start(ns)
    assert done.wait(5)
    assert events['ran'] == [(job_id, 'web')]
    assert not events['finished']
    assert any('Fly worker unavailable' in m and 'billing information' in m for m in events['progress'])


def test_without_web_fallback_the_run_records_flys_reason(monkeypatch):
    ns, events, done = _fallback_routes(monkeypatch, fallback=False, reason='FLY_NICHE_IMAGE missing')
    with pytest.raises(HTTPException) as error:
        _start(ns)
    assert error.value.status_code == 503
    assert not events['ran']
    (args, kwargs), = events['finished']
    assert kwargs['status'] == 'error' and 'FLY_NICHE_IMAGE missing' in kwargs['error']


def test_fly_refusal_reason_is_returned(monkeypatch):
    import config
    from webapp import fly_bridge
    monkeypatch.setattr(config, 'COOK_ON_FLY', True)
    monkeypatch.setattr(config, 'FLY_NICHE_IMAGE', 'registry/image@sha256:test')
    monkeypatch.setattr(config, 'YOUTUBE_API_KEY', 'test')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://test')
    def refuse(method, path, body):
        raise RuntimeError('Fly API POST /v1/apps/x/machines → 422: {"error":"We require your billing information"}')
    monkeypatch.setattr(fly_bridge, '_request', refuse)
    mid, reason = fly_bridge.spawn_niche_scrape_machine_with_reason('job')
    assert mid == '' and '422' in reason and 'billing' in reason
    assert fly_bridge.spawn_niche_scrape_machine('job') == ''
