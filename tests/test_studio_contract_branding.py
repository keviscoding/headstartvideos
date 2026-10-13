"""A display-name migration cannot replay models or change saved content."""
import hashlib
import json
from types import SimpleNamespace
import pytest
from local_runtime import cr_bridge, cr_claude

OLD = 'frontier-5.3-claudecode.1'
NEW = 'channelrecipe-5.3-claudecode.1'
SOURCE = 'e379dc601672282040a2adbf30f7bf7f7d34fcdf38154e5f6a24ec6c2528cadf'


def test_mcp_contract_view_preserves_user_content_and_original_result(tmp_path, monkeypatch):
    monkeypatch.setattr(cr_bridge, 'HOME', tmp_path)
    (tmp_path/'cr-release.json').write_text(json.dumps({'workflow_version':NEW}))
    data={'workflow_version':OLD,'workflow':{'workflow_version':OLD},'title':OLD,
          'job_spec':{'workflow_version':OLD},'artifacts':[{'name':OLD}]}
    before=json.dumps(data)
    result=cr_bridge.workflow_view(data)
    assert result['workflow_version']==result['workflow']['workflow_version']==NEW
    assert result['title']==result['job_spec']['workflow_version']==result['artifacts'][0]['name']==OLD
    assert json.dumps(data)==before


@pytest.mark.parametrize('unknown',[None,'future-workflow','channelrecipe-6.0-claudecode.1'])
def test_workflow_view_never_invents_an_unknown_contract(tmp_path,monkeypatch,unknown):
    monkeypatch.setattr(cr_bridge,'HOME',tmp_path)
    (tmp_path/'cr-release.json').write_text(json.dumps({'workflow_version':unknown}))
    data={'workflow_version':OLD};assert cr_bridge.workflow_view(data)==data


@pytest.fixture
def verification(tmp_path,monkeypatch):
    monkeypatch.setattr(cr_claude,'HOME',tmp_path)
    monkeypatch.setattr(cr_claude.time,'time',lambda:200000.0)
    cfg={'brain_mode':'claudecode','general_model':cr_claude.GENERAL_MODEL,'visual_model':cr_claude.VISUAL_MODEL,'local_version':'5.3-admin.42'}
    local={'ready':True,'version':'2.1.291','auth_method':'claude.ai','api_provider':'firstParty','account_fingerprint':'a'*64}
    prior={'workflow':OLD,'source':SOURCE}
    marker={'schema_version':2,'workflow':prior,'verified_at':199940,
            'identity':cr_claude._verification_identity_for_workflow(cfg,local,prior)}
    (tmp_path/'.cr-claude-verified.json').write_text(json.dumps(marker))
    (tmp_path/'cr-release.json').write_text(json.dumps({'workflow_version':NEW,'source_sha256':SOURCE}))
    return SimpleNamespace(root=tmp_path,cfg=cfg,local=local,marker=marker)


def test_name_only_upgrade_keeps_verified_account_models_age_and_zero_probe(verification):
    v=verification;path=v.root/'.cr-claude-verified.json';before=path.read_bytes()
    assert cr_claude.models_verified(v.cfg,v.local) and path.read_bytes()==before
    assert cr_claude.preserve_model_verification(v.cfg,v.local)
    saved=json.loads(path.read_text())
    assert saved['verified_at']==v.marker['verified_at']
    assert saved['workflow']=={'workflow':NEW,'source':SOURCE}
    assert saved['identity']==cr_claude._verification_identity(v.cfg,v.local)


@pytest.mark.parametrize('place,key,value',[
    ('cfg','general_model','another-model'),('cfg','brain_mode','api'),
    ('local','account_fingerprint','b'*64),('local','version','different-version'),
    ('local','ready',False),('release','workflow_version','future-workflow'),
    ('release','source_sha256','c'*64)])
def test_branding_cannot_launder_changed_account_models_or_engine(verification,place,key,value):
    v=verification
    if place=='release':
        path=v.root/'cr-release.json';release=json.loads(path.read_text());release[key]=value;path.write_text(json.dumps(release))
    else:getattr(v,place)[key]=value
    assert not cr_claude.models_verified(v.cfg,v.local)
