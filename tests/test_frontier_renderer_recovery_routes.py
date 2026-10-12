"""Recovery controls never accept paths, replay work or implicitly spend Claude usage."""
import ast
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from flask import Flask, g, jsonify, request
import pytest

RUNTIME = Path(__file__).parents[1] / 'local_runtime'
sys.path.insert(0, str(RUNTIME))
import cr_optional_renderer as routes
import cr_studio


@pytest.fixture
def saved(tmp_path, monkeypatch):
    project = 'crv_' + '1' * 24
    selection = 'a' * 64
    job = tmp_path / 'output' / '3d' / '_work' / 'saved-video'
    outdir = job / 'threed' / 'preview' / 'moment'
    outdir.mkdir(parents=True)
    marker = job / 'cr-project.json'
    marker.write_text(json.dumps({'id': project, 'brain_mode': 'claudecode',
                                  'job_spec': {'render_backend': 'manimgx'}}))
    app = Flask('isolated-renderer-recovery')
    app.testing = True
    studio = SimpleNamespace(app=app, RUNNING={}, QUEUE=[], mv=SimpleNamespace(CLAUDE_PROVIDER='claudecode'))
    activity = cr_studio.CreativeActivity(studio)
    state = SimpleNamespace(ready=True, renderer_ready=True, mode='claudecode', allowance=False,
        threads=[], calls=[], cloud_calls=[], stale=False, preset_ready=True, proof_revision=1,
        cloud={'project_id': project, 'project': {'id': project, 'status': 'failed', 'title': 'Saved video'},
               'brain_mode': 'claudecode', 'job_spec': {'title': 'Saved video', 'style': '3d', 'render_backend': 'manimgx'}},
        choice={'project_id': project, 'selection_id': selection, 'scene_id': 'bee', 'title': 'Saved video',
                'state': 'available', 'can_validate': True, 'can_correct': False, 'outdir': str(outdir),
                'receipt_path': str(outdir / 'private-receipt.json'), 'diagnostic': 'private-source-code', 'key': 'secret-key'})
    class Deferred:
        def __init__(self, *, target, daemon):
            self.target, self.alive = target, False
            state.threads.append(self)
        def start(self): self.alive = True
        def is_alive(self): return self.alive
        def finish(self):
            try: self.target()
            finally: self.alive = False
    monkeypatch.setattr(routes, 'threading', SimpleNamespace(Thread=Deferred))
    monkeypatch.setattr(cr_studio.cr_workers, 'busy', lambda: False)
    monkeypatch.setattr(routes, 'ready', lambda *a, **k: {'ready': state.renderer_ready, 'version': 'fixture'})
    monkeypatch.setattr(routes.cr_manimgx, 'progress_status', lambda _: {})
    monkeypatch.setattr(routes.cr_bridge, 'load_settings', lambda: {'brain_mode': state.mode})
    def progress(pid):
        state.cloud_calls.append(pid)
        return state.cloud
    monkeypatch.setattr(routes.cr_bridge, 'project_progress', progress)
    monkeypatch.setattr(routes.cr_readiness, 'proof', lambda *a, **k: {'brain_mode': 'claudecode', 'revision': state.proof_revision})
    def preflight(*args):
        state.calls.append(('preset', args[2]))
        return {'ready': state.preset_ready, 'blockers': ['Connect saved narration account.'],
                'proof': {'brain_mode': 'claudecode', 'media_ready': True, 'purpose': 'video', 'revision': state.proof_revision}}
    monkeypatch.setattr(routes.cr_readiness, 'preset', preflight)
    def discover(home, path, cloud):
        assert home == tmp_path and path == job and cloud is state.cloud
        return [dict(state.choice)]
    def resolve(home, path, cloud, sid):
        if state.stale or sid != selection:
            raise RuntimeError('The saved recovery changed; refresh its status.')
        return discover(home, path, cloud)[0]
    def validate(home, pid, output, scene, cloud, *, acknowledge_recovery):
        assert activity.busy() and home == tmp_path and pid == project
        assert output == str(outdir) and scene == 'bee' and acknowledge_recovery
        state.calls.append(('validate',))
        state.choice.update(state='validated', can_validate=False)
        return {'accepted': True, 'private': 'not-ui-data'}
    def correct(home, pid, output, scene, cloud, *, acknowledge_correction, engine, setup_proof):
        assert acknowledge_correction and engine is studio.mv and activity.busy()
        assert setup_proof['media_ready'] and setup_proof['purpose'] == 'video'
        state.calls.append(('correct',))
        state.choice.update(state='validated', can_correct=False)
        return {'accepted': True, 'private': 'not-ui-data'}
    helper = SimpleNamespace(discover=discover, resolve_selection=resolve, recover=validate, correct=correct)
    monkeypatch.setitem(sys.modules, 'cr_manimgx_recovery', helper)
    def readiness():
        # jsonify requires the worker to enter its own app context.
        return jsonify(readiness={'ready': state.ready}, claude_code={'allowance': {'blocked': state.allowance}})
    routes.install(studio, activity, home=tmp_path, readiness=readiness)
    return SimpleNamespace(home=tmp_path, job=job, marker=marker, project=project, selection=selection,
        client=app.test_client(), activity=activity, state=state, helper=helper, studio=studio,
        payload={'action': 'validate', 'project_id': project, 'selection_id': selection, 'acknowledge_recovery': True})


def test_discovery_is_explicit_owned_and_does_not_return_private_paths_or_code(saved):
    assert saved.client.get('/cr-renderer').status_code == 200
    assert saved.state.cloud_calls == [] and saved.state.calls == []
    response = saved.client.get('/cr-renderer/recovery')
    assert response.status_code == 200 and len(response.json['choices']) == 1
    assert saved.state.cloud_calls == [saved.project]
    text = response.get_data(as_text=True)
    assert str(saved.job) not in text and 'private-source-code' not in text and 'secret-key' not in text
    assert not saved.state.calls and not saved.state.threads
    saved.state.cloud['project']['id'] = 'crv_' + '2' * 24
    assert saved.client.get('/cr-renderer/recovery').json['choices'] == []


@pytest.mark.parametrize('extra', [{'outdir': '/tmp/other'}, {'job': '../../escape'}, {'scene_id': 'injected'}, {'proof': {}}])
def test_browser_cannot_supply_paths_scene_ids_or_setup_proof(saved, extra):
    result = saved.client.post('/cr-renderer/recovery', json={**saved.payload, **extra})
    assert result.status_code == 400 and not saved.state.threads


@pytest.mark.parametrize('changed', [{'action': []}, {'project_id': '../other'}, {'selection_id': '../scene'},
                                   {'acknowledge_recovery': 'true'}])
def test_malformed_recovery_selection_cannot_start_work(saved, changed):
    assert saved.client.post('/cr-renderer/recovery', json={**saved.payload, **changed}).status_code == 400
    assert not saved.state.threads


@pytest.mark.parametrize('field,value', [('ready', False), ('renderer_ready', False), ('mode', 'channelrecipe'), ('stale', True)])
def test_recovery_requires_current_native_ready_and_matching_saved_evidence(saved, field, value):
    setattr(saved.state, field, value)
    result = saved.client.post('/cr-renderer/recovery', json=saved.payload)
    assert result.status_code == 409 and not saved.state.threads and not saved.activity.busy()
    if field != 'stale':
        assert saved.client.get('/cr-renderer/recovery').status_code == 409


def test_active_or_nonfailed_project_cannot_start_saved_recovery(saved):
    token = saved.activity.reserve()
    assert saved.client.post('/cr-renderer/recovery', json=saved.payload).status_code == 409
    saved.activity.release(token)
    saved.state.cloud['project']['status'] = 'running'
    assert saved.client.post('/cr-renderer/recovery', json=saved.payload).status_code == 409
    assert not saved.state.threads


def test_missing_or_duplicate_local_project_cannot_redirect_recovery(saved):
    other = saved.job.with_name('duplicate')
    other.mkdir()
    (other / 'cr-project.json').write_bytes(saved.marker.read_bytes())
    assert saved.client.post('/cr-renderer/recovery', json=saved.payload).status_code == 409
    assert not saved.state.threads


def test_validation_reserves_activity_without_generation_and_leaves_saved_project_unchanged(saved):
    before = saved.marker.read_bytes()
    response = saved.client.post('/cr-renderer/recovery', json=saved.payload)
    assert response.status_code == 200 and response.json['recovery']['state'] == 'running'
    assert saved.activity.busy() and not saved.state.calls
    assert saved.client.post('/cr-renderer', json={'action': 'install'}).status_code == 409
    assert saved.client.post('/cr-renderer/recovery', json=saved.payload).status_code == 409
    saved.state.threads[0].finish()
    status = saved.client.get('/cr-renderer').json
    assert status['recovery']['state'] == 'validated' and not status['busy']
    assert 'manually' not in status['recovery']['message'] or 'Create video' in status['recovery']['message']
    assert 'still stopped' in status['recovery']['message']
    assert saved.state.calls == [('validate',)] and saved.marker.read_bytes() == before
    assert 'private' not in json.dumps(status)
    assert saved.client.post('/cr-renderer/recovery', json=saved.payload).status_code == 409


@pytest.mark.parametrize('changed', ['stale', 'proof_revision'])
def test_worker_rechecks_evidence_and_setup_before_any_saved_action(saved, changed):
    assert saved.client.post('/cr-renderer/recovery', json=saved.payload).status_code == 200
    setattr(saved.state, changed, True if changed == 'stale' else 2)
    saved.state.threads[0].finish()
    assert saved.client.get('/cr-renderer').json['recovery']['state'] == 'failed'
    assert not saved.activity.busy() and not saved.state.calls


def test_failed_local_check_offers_separate_acknowledged_correction_without_auto_retry(saved):
    def invalid(*args, **kwargs):
        saved.state.calls.append(('validate',))
        saved.state.choice.update(state='source_invalid', can_validate=False, can_correct=True)
        raise RuntimeError('The recovered source did not pass the pinned local API check. No model was started.')
    saved.helper.recover = invalid
    assert saved.client.post('/cr-renderer/recovery', json=saved.payload).status_code == 200
    saved.state.threads[0].finish()
    assert saved.state.calls == [('validate',)]
    status = saved.client.get('/cr-renderer').json
    assert status['recovery']['state'] == 'source_invalid' and not status['busy']
    assert 'separate' in status['recovery']['message']
    assert saved.client.post('/cr-renderer/recovery', json=saved.payload).status_code == 409
    correct = {**saved.payload, 'action': 'correct'}
    assert saved.client.post('/cr-renderer/recovery', json=correct).status_code == 400
    correct['acknowledge_correction'] = True
    assert saved.client.post('/cr-renderer/recovery', json=correct).status_code == 200
    assert ('correct',) not in saved.state.calls
    saved.state.threads[-1].finish()
    assert saved.state.calls.count(('correct',)) == 1
    assert saved.state.calls.count(('validate',)) == 1
    assert saved.client.get('/cr-renderer').json['recovery']['state'] == 'validated'
    assert not saved.activity.busy()


@pytest.mark.parametrize('blocked', ['allowance', 'preset_ready'])
def test_correction_requires_current_allowance_and_saved_preset_checks(saved, blocked):
    saved.state.choice.update(state='source_invalid', can_validate=False, can_correct=True)
    setattr(saved.state, blocked, blocked == 'allowance')
    result = saved.client.post('/cr-renderer/recovery', json={**saved.payload, 'action': 'correct', 'acknowledge_correction': True})
    assert result.status_code == 409 and not saved.state.threads
    assert ('correct',) not in saved.state.calls


def test_failed_correction_cannot_reenable_the_spent_correction_action(saved):
    saved.state.choice.update(state='source_invalid', can_validate=False, can_correct=True)
    def stopped(*args, **kwargs):
        saved.state.calls.append(('correct',))
        # The immutable original validation receipt remains source_invalid;
        # the new correction journal makes another correction unavailable.
        saved.state.choice['can_correct'] = False
        raise RuntimeError('The one corrected source still failed local validation.')
    saved.helper.correct = stopped
    payload = {**saved.payload, 'action': 'correct', 'acknowledge_correction': True}
    assert saved.client.post('/cr-renderer/recovery', json=payload).status_code == 200
    saved.state.threads[-1].finish()
    status = saved.client.get('/cr-renderer').json
    assert status['recovery']['state'] == 'failed' and not status['busy']
    assert 'No further correction' in status['recovery']['message']
    assert 'separate, optional' not in status['recovery']['message']
    assert saved.client.post('/cr-renderer/recovery', json=payload).status_code == 409
    assert saved.state.calls.count(('correct',)) == 1


def test_worker_start_failure_releases_activity_and_keeps_saved_work(saved, monkeypatch):
    before = saved.marker.read_bytes()
    monkeypatch.setattr(routes.threading.Thread, 'start', lambda self: (_ for _ in ()).throw(OSError('fixture')))
    assert saved.client.post('/cr-renderer/recovery', json=saved.payload).status_code == 503
    assert not saved.activity.busy() and not saved.state.calls
    assert saved.marker.read_bytes() == before
    assert saved.client.get('/cr-renderer').json['recovery']['state'] == 'stopped'


def test_new_route_uses_real_studio_loopback_origin_and_token_guard(saved):
    app = saved.studio.app
    tree = ast.parse((RUNTIME / 'cr_studio.py').read_text())
    guard = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == 'local_access')
    guard.decorator_list = []
    namespace = {'request': request, 'jsonify': jsonify, 'token': 'fixture-token', 'g': g,
                 'studio': saved.studio, 'activity': saved.activity,
                 'deferred_editor_request': lambda *a: False, 'creative_request': lambda *a: False}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[guard], type_ignores=[])), str(RUNTIME / 'cr_studio.py'), 'exec'), namespace)
    app.before_request(namespace['local_access'])
    for headers in ({'Host': 'external.example', 'X-CR-Local-Token': 'fixture-token'},
                    {'Host': '127.0.0.1:7860', 'Origin': 'https://external.example', 'X-CR-Local-Token': 'fixture-token'},
                    {'Host': '127.0.0.1:7860', 'X-CR-Local-Token': 'wrong'}, {'Host': '127.0.0.1:7860'}):
        assert saved.client.post('/cr-renderer/recovery', json=saved.payload, headers=headers).status_code == 403
    assert not saved.state.threads
    headers = {'Host': '127.0.0.1:7860', 'X-CR-Local-Token': 'fixture-token'}
    assert saved.client.post('/cr-renderer/recovery', json=saved.payload, headers=headers).status_code == 200
