"""Renderer input facts feed original checks without choosing or fixing shots."""
import ast
import copy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local_runtime"))
import cr_renderer_contracts as contracts


@pytest.fixture
def contract():
    # Deliberately different limits prove diagnostics use the supplied source
    # contract rather than guessed production constants.
    return {"path": "/private/propbuild.js", "maximum_parts": 8, "maximum_depth": 2,
            "pair_keys": True, "translate_delta": True, "centered_grid": True,
            "segments": (11, 3, 40), "lathe_offset": 4, "torus_factor": 3,
            "copies": {"repeat": (2, 1, 6), "ring": (5, 1, 6),
                       "sphere": (7, 1, 9), "grid": (3, 1, 5)},
            "minimums": {"pole": {"r": 0.004}, "box": {"size": 0.001}}}


def board(parts=None, anim=None, **item):
    recipe = {"kind": "prop.build", "parts": parts or [], **item}
    if anim is not None:
        recipe["anim"] = anim
    return {"moments": [{"id": "moment", "scenes": [{"id": "shot", "objects": [recipe]}]}]}


def test_invalid_animation_keys_are_reported_without_converting_them(contract):
    data = board([{"id": "hero", "shape": "box", "size": [.1, .1, .1]}],
                 [{"part": "hero", "type": "translate", "keys": [{"t": 0, "pos": [0, 0, 0]}, [1, [1, 0, 0]]]}])
    before = copy.deepcopy(data)
    result = contracts.diagnose(data, contract)["shot"]
    assert "ignores 1/2 keys" in result[0] and "deltas" in result[0]
    assert data == before


def test_camera_object_keys_and_non_keyed_animation_are_valid(contract):
    data = board([{"shape": "box", "size": [.1, .1, .1]}],
                 [{"type": "translate", "keys": [[0, [0, 0, 0]], [1, [1, 0, 0]]]},
                  {"type": "hide", "t": 1, "keys": [{"t": 0}]}])
    data["moments"][0]["scenes"][0]["camera"] = {"move": "keys", "keys": [{"t": 0, "pos": [1, 2, 3]}]}
    assert contracts.diagnose(data, contract) == {}


def test_numeric_coercion_matches_renderer_for_null_and_supported_key_times(contract):
    data = board([{"shape": "box", "repeat": {"n": None}}],
                 [{"type": "translate", "keys": [[None, [0, 0, 0]], ["", [0, 0, 0]],
                     [True, [1, 0, 0]], [[2], [2, 0, 0]], ["0x3", [3, 0, 0]]]}])
    assert contracts.diagnose(data, contract) == {}
    assert contracts._budget(data["moments"][0]["scenes"][0]["objects"][0]["parts"], contract)["count"] == 2


@pytest.mark.parametrize("dimensions", [[], [3]])
def test_missing_grid_indices_use_source_default_not_null_or_one(contract, dimensions):
    data = board([{"shape": "box", "grid": {"n": dimensions}}, {"shape": "sphere"}])
    assert "reaches 9 instances" in contracts.diagnose(data, contract)["shot"][0]


def test_scalar_lists_and_unused_size_components_do_not_false_flag_clamps(contract):
    contract["minimums"].update({"cylinder": {"r": .004}, "plane": {"size": .001}})
    data = board([{"shape": "pole", "r": [.0001, .1]},
                  {"shape": "pole", "r": None},
                  {"shape": "cylinder", "r": [.1, .1, .0001]},
                  {"shape": "box", "size": [.1, .1, .1, .0001]},
                  {"shape": "box", "size": [.0001]},
                  {"shape": "box", "size": ".0001"},
                  {"shape": "plane", "size": [0, .1, .0001]}])
    assert contracts.diagnose(data, contract) == {}
    assert "pole.r 0.0001→0.004" in contracts.diagnose(board([{"shape": "pole", "r": [.0001]}]), contract)["shot"][0]


def test_budget_accounts_for_group_copies_and_reports_later_parts_skipped(contract):
    data = board([{"shape": "group", "repeat": {"n": 3}, "parts": [
        {"shape": "box", "grid": {"n": [2, 2]}, "size": [.1, .1, .1]}]},
        {"id": "later", "shape": "sphere"}])
    result = contracts.diagnose(data, contract)["shot"]
    assert len(result) == 1 and "reaches 8 instances" in result[0]
    assert "later parts are skipped" in result[0]


def test_one_instanced_batch_can_overshoot_limit_before_next_part(contract):
    batch = {"shape": "box", "grid": {"n": [4, 3]}}
    assert contracts.diagnose(board([batch]), contract) == {}
    result = contracts.diagnose(board([batch, {"shape": "sphere", "id": "later hero"}]), contract)["shot"]
    assert "reaches 12 instances" in result[0] and "later parts are skipped" in result[0]
    assert contracts.diagnose(board([{"shape": "box", "repeat": {"n": 8}}]), contract) == {}


def test_unknown_copy_contract_never_guesses_an_overflow(contract):
    contract["copies"].pop("grid")
    assert contracts.diagnose(board([{"shape": "box", "grid": {"n": [5, 5]}}]), contract) == {}


def test_empty_nested_repeats_are_bounded_and_unknown_facts_fail_open(contract):
    contract["maximum_depth"] = 7
    contract["copies"]["repeat"] = (2, 1, 400)
    part = {"shape": "group", "parts": []}
    for _ in range(7):
        part = {"shape": "group", "repeat": {"n": 400}, "parts": [part]}
    assert contracts._budget([part], contract) is None
    assert contracts.diagnose(board([part]), contract) == {}


def test_deep_ignored_groups_and_children_of_primitives_never_create_diagnostics(contract):
    leaf = {"shape": "pole", "r": .00001}
    part = leaf
    for _ in range(1500):
        part = {"shape": "group", "parts": [part]}
    data = board([part, {"shape": "box", "size": [.1, .1, .1], "parts": [leaf]}])
    assert contracts.diagnose(data, contract) == {}
    assert data["moments"][0]["scenes"][0]["objects"][0]["parts"][0] is part
    assert leaf == {"shape": "pole", "r": .00001}


def test_positive_clamps_are_reported_before_scale_and_segments_not_autofixed(contract):
    data = board([{"shape": "pole", "r": .00035},
                  {"shape": "box", "size": [.1, .00008, -.2]},
                  {"shape": "lathe", "segments": 6}], scale=10)
    before = copy.deepcopy(data)
    lines = contracts.diagnose(data, contract)["shot"]
    assert "pole.r 0.00035→0.004 m" in lines[0]
    assert "box.size 8e-05→0.001 m" in lines[0] and "before part/prop scaling" in lines[0]
    assert "segments" in lines[1] and "seg" in lines[1]
    assert data == before


def test_endpoint_geometry_does_not_falsely_flag_unused_height(contract):
    contract["minimums"]["cylinder"] = {"h": .004, "r": .002}
    data = board([{"shape": "cylinder", "h": .00001, "r": .01,
                   "from": [0, 0, 0], "to": [0, 1, 0]}])
    assert contracts.diagnose(data, contract) == {}


def test_many_bad_animations_do_not_hide_budget_or_clamp_from_original_three_issue_limit(contract):
    data = board([{"shape": "pole", "r": .0001, "grid": {"n": [4, 3]}}, {"shape": "sphere"}],
                 [{"part": str(i), "type": "translate", "keys": [{"t": 0, "pos": [0, 0, 0]}]} for i in range(6)])
    lines = contracts.diagnose(data, contract)["shot"]
    assert [line.split(":")[0] for line in lines] == [
        "renderer_contract_parts", "renderer_contract_keys", "renderer_contract_clamp"]
    assert "ignores 6/6 keys" in lines[1]


def test_legitimate_centered_grid_and_tessellation_are_information_not_failures(contract):
    data = board([{"shape": "lathe", "seg": 6, "pos": [-1, 0, -1], "grid": {"n": [2, 2]}}])
    assert contracts.diagnose(data, contract) == {}
    note = contracts.compatibility_note(contract, "/private/camera.js")
    assert "CENTER" in note and "seg+4" in note and "seg×3" in note
    assert "reaches 8" in note and "final instance batch can exceed" in note


def test_notes_include_only_existing_original_lighting_read_paths(contract, tmp_path):
    engine = tmp_path / "engine"
    stage = engine / "lib/v3/stage.js"
    stage.parent.mkdir(parents=True)
    stage.write_text("Owner's original stage contract")
    note = contracts.compatibility_note(contract, engine / "core/camera.js")
    assert str(stage) in note and "lights.js" not in note


def test_contract_extracts_limits_from_source_and_unknown_source_is_safe(tmp_path):
    path = tmp_path / "propbuild.js"
    # Newly authored parser fixture, not copied private renderer source.
    path.write_text("""const MAX_PARTS = 17;
if (depth > 2) stop();
const keys = arr.filter((k) => Array.isArray(k) && k.length >= 2);
n.position.add(delta);
(i - (nx - 1) / 2);
const seg = Math.round(clamp(num(p.seg, 11), 3, 40));
Math.round(clamp(num(p.repeat.n, 2), 1, 6));
Math.round(clamp(num(n[0], 3), 1, 5));
case 'pole': case 'post': {
 const r = Math.max(0.004, num(p.r, 0.03));
}
case 'lathe': {
 new T.LatheGeometry(pts, seg + 4);
}
case 'torus': {
 new T.TorusGeometry(Math.max(0.002, num(p.r, 0.1)), 0.01, 4, seg * 3, arc);
}
default: fallback();
""")
    actual = contracts.read_contract(path)
    assert actual["maximum_parts"] == 17 and actual["maximum_depth"] == 2
    assert actual["minimums"]["pole"]["r"] == .004
    assert actual["minimums"]["post"]["r"] == .004
    assert actual["segments"] == (11, 3, 40)
    assert actual["lathe_offset"] == 4 and actual["torus_factor"] == 3
    assert actual["copies"]["repeat"] == (2, 1, 6)
    assert actual["pair_keys"] and actual["centered_grid"]
    assert contracts.read_contract(tmp_path / "absent") == {}
    path.write_text("A different renderer with no matching fields.")
    assert contracts.diagnose(board([{"shape": "pole", "r": 0}]), contracts.read_contract(path)) == {}


@pytest.fixture
def hooked(tmp_path, monkeypatch, contract):
    original_result = {"scenes": {"shot": ["original_preset: keep this"]},
                       "metrics": {"shot": {"kind": "original"}}, "moments": {},
                       "board": ["existing board detail"], "stats": {"violations": 2},
                       "private_extra": object()}
    calls = []
    def original(board, *args, **kwargs):
        calls.append((board, args, kwargs))
        return original_result
    sb = SimpleNamespace(board_checks=original, SPEC_HARD=("original_preset", "cheap_hero"))
    module = SimpleNamespace(ROOT=tmp_path / "threed", SB=sb, _notes_of=lambda job: "Original user notes")
    monkeypatch.setitem(sys.modules, "threed", module)
    monkeypatch.setattr(contracts, "read_contract", lambda path: contract)
    wrapper = contracts.install(SimpleNamespace())
    return module, wrapper, original_result, calls


def test_hook_preserves_original_fields_args_and_registers_only_definite_codes(hooked):
    module, wrapper, original, calls = hooked
    data = board([], [{"type": "translate", "keys": [{"t": 0, "pos": [0, 0, 0]}]}])
    result = wrapper(data, ["original meta"], catalog={"old": "catalog"})
    assert calls == [(data, (["original meta"],), {"catalog": {"old": "catalog"}})]
    assert result["metrics"] is original["metrics"] and result["private_extra"] is original["private_extra"]
    assert result["scenes"]["shot"][0] == "original_preset: keep this"
    assert result["stats"]["violations"] == 3 and original["stats"]["violations"] == 2
    assert original["scenes"]["shot"] == ["original_preset: keep this"]
    assert module.SB.SPEC_HARD[:2] == ("original_preset", "cheap_hero")
    assert "renderer_contract_segments" not in module.SB.SPEC_HARD
    assert contracts.install(SimpleNamespace()) is wrapper
    assert module._notes_of(Path("job")).count("Renderer compatibility") == 1
    assert module._notes_of(Path("job")).startswith("Original user notes")


def test_original_redo_verdict_receives_contract_hard_codes_without_paid_work(hooked):
    source = Path("/Users/workcomputer/Downloads/ChannelRecipe studio/threed.py")
    if not source.exists():
        pytest.skip("Owner's private engine unavailable; no private source is published")
    module, _, _, _ = hooked
    parsed = ast.parse(source.read_text())
    function = next(node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name == "_redo_verdict")
    environment = {"SB": module.SB, "_preview_ok": lambda rep: {}, "_issues": lambda rep: {}, "_hard": lambda lines: set()}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), str(source), "exec"), environment)
    data = board([{"shape": "box", "grid": {"n": [4, 3]}}, {"shape": "sphere"}], [{"type": "translate", "keys": [{"t": 0, "pos": [0, 0, 0]}]}])
    result = environment["_redo_verdict"]({}, data, {}, {"shot"})
    assert result["shot"][0].startswith("original_preset:")
    assert any(line.startswith("renderer_contract_keys:") for line in result["shot"])
    assert any(line.startswith("renderer_contract_parts:") for line in result["shot"])


def test_missing_private_engine_is_an_optional_noop(monkeypatch):
    def absent(name):
        raise ModuleNotFoundError(name)
    monkeypatch.setattr(contracts.importlib, "import_module", absent)
    assert contracts.install(SimpleNamespace()) is None
    monkeypatch.setattr(contracts.importlib, "import_module", lambda name: SimpleNamespace())
    assert contracts.install(SimpleNamespace()) is None
