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
    result=ns['niche_finder_channels'](user={},added_within_days=7,sort='score',q='test',min_subscribers=5000,offset=40)
    assert result['sort']=='newest'
    assert result['total']==12
    assert calls[0]['added_since']==calls[1]['added_since']==1_000_000-7*86400
    assert calls[0]['min_subscribers']==calls[1]['min_subscribers']==5000
    assert calls[0]['q']==calls[1]['q']=='test'
    assert calls[0]['offset']==40


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
