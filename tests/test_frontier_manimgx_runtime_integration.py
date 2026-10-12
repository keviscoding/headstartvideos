"""Saved-response corrections use the production compiler and cache hooks."""
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_frontier_manimgx_recovery import saved, runtime_saved, invoke, SOURCE
import cr_manimgx as gx
import cr_manimgx_recovery as recovery


def test_actual_runtime_corrector_keeps_parent_and_one_shot_budget(runtime_saved, monkeypatch):
    s = runtime_saved
    originals = {p.name: p.read_bytes() for p in s.old.iterdir() if p.is_file()}
    invoke(s, acknowledge_recovery=True)
    plan = recovery._prepare(s.home, s.cloud['project_id'], s.out, 'original', s.cloud)
    monkeypatch.setattr(gx, '_api_reference', lambda docs: 'Pinned local API')
    monkeypatch.setattr(recovery.refs, 'save_reference', lambda path, bundle: gx._write(path, 'exact original private reference', text=True))
    calls = []
    def compiler(prompt, maximum):
        calls.append(prompt)
        value = dict(plan['result']); value['source'] = SOURCE
        return json.dumps(value)
    result, source = gx.correct_recovered_source(SimpleNamespace(), plan['context'], plan['assets'], plan['ready'],
        s.old, s.key, reference_bundle=s.bundle, recovery_directory=s.work, compiler=compiler)
    assert len(calls) == 1 and source == s.work / 'scene-corrected.py'
    assert source.read_text() == SOURCE
    assert all((s.old / name).read_bytes() == text for name, text in originals.items())
    with pytest.raises(RuntimeError, match='no longer matches|already started'):
        gx.correct_recovered_source(SimpleNamespace(), plan['context'], plan['assets'], plan['ready'],
            s.old, s.key, reference_bundle=s.bundle, recovery_directory=s.work, compiler=compiler)
    assert len(calls) == 1

from test_frontier_manimgx_recovery import coverage_saved, manifest_saved


def test_actual_manifest_corrector_publishes_once_and_reuses_saved_code(manifest_saved, monkeypatch):
    import cr_bridge
    s = manifest_saved
    invoke(s, acknowledge_recovery=True)
    plan = recovery._prepare(s.home, s.cloud['project_id'], s.out, 'original', s.cloud)
    monkeypatch.setattr(gx, '_api_reference', lambda docs: 'Pinned local API')
    monkeypatch.setattr(recovery.refs, 'save_reference', lambda path, bundle: gx._write(path, 'exact original private reference', text=True))
    monkeypatch.setattr(cr_bridge, 'HOME', s.home)
    monkeypatch.setattr(cr_bridge, 'begin_project', lambda *a, **kw: None)
    monkeypatch.setattr(cr_bridge, 'finish_project', lambda *a, **kw: None)
    original_corrector = gx.correct_recovered_source
    calls = []
    def compiler(prompt, maximum):
        calls.append(prompt)
        return json.dumps(s.complete_result)
    monkeypatch.setattr(gx, 'correct_recovered_source', lambda *a, **kw: original_corrector(*a, **kw, compiler=compiler))
    accepted = recovery.correct(s.home, s.cloud['project_id'], s.out, 'original', s.cloud,
        acknowledge_correction=True, engine=SimpleNamespace(CLAUDE_PROVIDER='claudecode'),
        setup_proof={'brain_mode':'claudecode', 'models_verified':True, 'costs_accepted':True})
    assert accepted['accepted'] and accepted['corrected'] and len(calls) == 1
    assert (s.old / 'compiler-response-1.txt').read_text() == s.original_raw
    result, source = gx._compiled_source(SimpleNamespace(), lambda *a: pytest.fail('No repeat compiler'),
        plan['context'], plan['assets'], plan['ready'], s.old, s.key, reference_bundle=s.bundle)
    assert result == s.complete_result and source.read_text() == s.complete_result['source']
    acceptance = gx._accepted_response(s.old, gx._read(s.old / 'compiler.json'), plan['context'], plan['assets'])
    assert acceptance['kind'] == 'completed_native_recovery_correction'

from test_frontier_manimgx_recovery import runtime_correction, completed_runtime_correction


@pytest.mark.parametrize('saved_fixture', ['runtime_correction', 'completed_runtime_correction'])
def test_runtime_corrected_source_reuses_final_format_without_touching_parent_log(request, saved_fixture, monkeypatch):
    import math
    from PIL import Image
    s = request.getfixturevalue(saved_fixture)
    plan = recovery._prepare(s.home, s.cloud['project_id'], s.out, 'original', s.cloud)
    if saved_fixture == 'completed_runtime_correction':
        invoke(s, acknowledge_recovery=True)
    else:
        recovery.correct(s.home, s.cloud['project_id'], s.out, 'original', s.cloud,
            acknowledge_correction=True, engine=s.engine, setup_proof=s.setup)
    expected_source = recovery.runtime_override(s.old, plan['context'], plan['assets'], plan['ready'], s.key, s.bundle)[1]
    originals = {p.name: p.read_bytes() for p in s.old.iterdir() if p.is_file()}
    invoked = []
    def invoke_render(python, payload, directory, **kwargs):
        invoked.append((payload, Path(directory)))
        gx._write(Path(directory) / 'render_stdout.log', 'new target render', text=True)
        stills = []
        for i,t in enumerate(payload['times']):
            p = Path(payload['still_dir']) / f'original_{i}.jpg'
            Image.new('RGB', (payload['width'],payload['height']), 'blue').save(p)
            stills.append({'t':t,'file':str(p)})
        frames = math.ceil(payload['duration'] * payload['fps']) + 1
        return {'frames':frames,'duration':frames/payload['fps'],'width':payload['width'],
            'height':payload['height'],'fps':payload['fps'],'stills':stills,'issues':[],
            'layout_checked':True,'elapsed':.1,'exported':True}
    monkeypatch.setattr(gx, '_invoke', invoke_render)
    monkeypatch.setattr(gx, '_validate_output', lambda *a: None)
    (s.home / 'threed/catalog.json').write_text('{}')
    output = s.out.parent.parent / 'final'
    report = gx.render(SimpleNamespace(), gx._read(s.out / 'video.json'), output,
        ['--w','1280','--h','720','--quality','final'], 'final', root=s.home,
        compiler=lambda *a: pytest.fail('No repeat compiler'))
    target = s.old.parent / report['scenes'][0]['compiler_key']
    assert invoked[0][1] == target
    assert Path(invoked[0][0]['source']) == expected_source
    assert gx._read(target / 'reuse.json')['acceptance']['kind'] == 'completed_native_runtime_correction'
    assert all((s.old / name).read_bytes() == data for name,data in originals.items())
