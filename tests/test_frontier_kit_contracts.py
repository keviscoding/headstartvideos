"""Malformed text markup reaches the original browser review, without paid work."""
from __future__ import annotations

import ast
import copy
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from types import ModuleType, SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_kit_contracts as contracts

ORIGINAL = Path("/Users/workcomputer/Downloads/ChannelRecipe studio")


def diagnostic(text="Example text"):
    return {"kind": contracts.KIND, "text": text,
            "attributes": [{"name": 'display";color:#abc', "value": ""}],
            "style": "font:700 90px ", "color": "rgb(0, 0, 0)",
            "font_family": '"Times New Roman"'}


def test_parser_evidence_prioritized_for_original_repair_without_rewriting_palette():
    result = {"browser": True, "bad": ["already_broken"], "problems": [f"other issue {i}" for i in range(25)],
              "types": {"title": {"text": [diagnostic()]}, "number": {"text": [diagnostic("42")]},
                        "valid": {"text": [{"kind": "caption_zone", "text": "valid"}]}}}
    original = copy.deepcopy(result["types"])
    updated = contracts.add_feedback(result)
    assert updated["bad"] == ["already_broken", "number", "title"]
    assert updated["problems"][0].startswith("kit_html_style:")
    assert "font quote escaping" in updated["problems"][0]
    assert "title:" in updated["problems"][0] and "number:" in updated["problems"][0]
    assert updated["types"] == original
    before = copy.deepcopy(updated["problems"])
    assert contracts.add_feedback(updated)["problems"] == before


@pytest.mark.parametrize("browser", [False, None, "true"])
def test_mock_diagnostics_cannot_claim_actual_browser_parser_evidence(browser):
    result = {"browser": browser, "bad": [], "problems": [], "types": {"title": {"text": [diagnostic()]}}}
    before = copy.deepcopy(result)
    assert contracts.add_feedback(result) == before


def test_valid_dark_text_and_missing_attributes_are_never_inferred_as_errors():
    result = {"browser": True, "bad": [], "problems": [],
              "types": {"title": {"text": [{"kind": "ordinary", "color": "rgb(0, 0, 0)"},
                                           {"kind": contracts.KIND}]}}}
    assert contracts.add_feedback(result) == result
    assert "kit_contracts" not in result


def _assignment(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("Missing original contract " + name)


@pytest.fixture
def original_learner(monkeypatch):
    if not (ORIGINAL / "reflearn.py").exists():
        pytest.skip("Owner-only original runtime unavailable")
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    api = pytest.importorskip("playwright.sync_api")
    # Fail/skip here on an unavailable browser rather than silently letting the
    # original checker switch to its DOM mock. No user browser tab is opened.
    with api.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception as exc:
            pytest.skip("Chromium unavailable: " + str(exc)[:120])
        browser.close()
    page = _assignment(ast.parse((ORIGINAL / "kits.py").read_text()), "PAGE")
    tree = ast.parse((ORIGINAL / "reflearn.py").read_text())
    learner = ModuleType("test_private_learner")
    scene = {"type": "title", "duration": 5, "title": "Parser regression"}
    kit = SimpleNamespace(KITS={"fixture": {"types": {"title": 5}}})
    kit.js = ""
    def build_html(name, sc):
        import json
        return page.replace("__FONTS__", "").replace("__KITJS__", kit.js).replace("__SCENE__", json.dumps(sc))
    kit.build_html = build_html
    monkeypatch.setitem(sys.modules, "kits", kit)
    monkeypatch.setitem(sys.modules, "motion", SimpleNamespace(_PW_START=threading.Lock()))
    def register(path):
        if (Path(path) / "kit.js").is_file():
            kit.js = (Path(path) / "kit.js").read_text()
        return "fixture"
    def write(path, value):
        Path(path).write_text(value if isinstance(value, str) else json.dumps(value))
    learner.__dict__.update({"Path": Path, "tempfile": tempfile, "shutil": shutil, "io": io,
        "_LOCK": threading.Lock(), "RENDERER": None, "CONTRACT": "Original private contract",
        "OVERFLOW_JS": _assignment(tree, "OVERFLOW_JS"), "_register": register,
        "test_scenes": lambda *args: [scene], "gate": lambda log: (True, "test browser", None),
        "_release": lambda lock: None, "_times": lambda duration: [1.3, 2.9, 4.3],
        "_write": write, "_read_json": lambda path: json.loads(Path(path).read_text()),
        "_json_with": lambda value, key: json.loads(value), "_examples": lambda path: "{}",
        "REPAIR_PROMPT": _assignment(tree, "REPAIR_PROMPT")})
    names = {"render_check", "check", "validate", "apply_edits"}
    selected = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names],
                          type_ignores=[])
    exec(compile(ast.fix_missing_locations(selected), str(ORIGINAL / "reflearn.py"), "exec"), learner.__dict__)
    return learner, kit


# Same class of defect as the real learned kit: quotes legal in a JavaScript
# string become unescaped quotes in the resulting HTML attribute.
BROKEN_KIT = r"""
function build(){const css='font:700 90px "Barlow Condensed";color:#91c4f0';
 scene.style.background='linear-gradient(#09122b,#223455)';
 parts.title=$('<div style="position:absolute;left:100px;top:100px;'+css+'"><span>'+esc(S.title)+'</span></div>');
 scene.appendChild(parts.title);}
function frame(t){parts.title.style.transform='translateX('+t+'px)';}
"""

CORRECT_KIT = r"""
function build(){const css='font:700 90px "Barlow Condensed";color:#91c4f0';
 scene.style.background='linear-gradient(#09122b,#223455)';
 parts.title=document.createElement('div');parts.title.style.cssText='position:absolute;left:100px;top:100px;'+css;
 parts.title.textContent=S.title;scene.appendChild(parts.title);}
function frame(t){parts.title.style.transform='translateX('+t+'px)';}
"""


def test_actual_original_browser_parser_defect_is_reported_and_fixed_markup_passes(original_learner, tmp_path):
    learner, kit = original_learner
    original_contract = learner.CONTRACT
    original_render = learner.render_check
    kit.js = BROKEN_KIT
    # Original visual/content check misses this class of lost CSS: nonblank,
    # valid JavaScript, unclipped but black fallback text on a black page.
    baseline = learner.check(tmp_path, {}, tmp_path / "baseline", log=lambda value: None)
    assert baseline["browser"] and baseline["bad"] == []
    assert baseline["types"]["title"]["frames"]
    contracts.install(learner)
    assert learner.render_check.__code__ is original_render.__code__
    assert learner.CONTRACT.startswith(original_contract)
    assert contracts.install(learner) is learner.check
    failed = learner.check(tmp_path, {}, tmp_path / "diagnosed", log=lambda value: None)
    assert failed["browser"] and failed["bad"] == ["title"]
    evidence = [row for row in failed["types"]["title"]["text"] if row.get("kind") == contracts.KIND]
    assert evidence and evidence[0]["color"] == "rgb(0, 0, 0)"
    assert 'condensed";color:' in str(evidence[0]["attributes"])
    assert failed["problems"][0].startswith("kit_html_style:")
    assert kit.js == BROKEN_KIT  # Diagnostic never performs a kit repair.
    # A fixture repair demonstrates the browser contract; production repairs
    # must still be authored and re-rendered by the original Claude review.
    kit.js = CORRECT_KIT
    repaired = learner.check(tmp_path, {}, tmp_path / "correct", log=lambda value: None)
    assert repaired["browser"] and repaired["bad"] == []
    assert not any(p.startswith("kit_html_style:") for p in repaired["problems"])


@pytest.mark.parametrize("style", [
    "font:700 90px &quot;Barlow Condensed&quot;;color:#000",
    "font:700 90px 'Barlow Condensed';color:#fff",
    "font:700 90px serif;color:#000;background:#fff",
])
def test_actual_browser_accepts_escaped_quotes_valid_dark_type_and_data_attributes(original_learner, tmp_path, style):
    learner, kit = original_learner
    markup = '<div style="position:absolute;left:100px;top:100px;' + style + \
        '" data-note="font:700;colour:dark">Parser regression</div>'
    kit.js = "function build(){scene.style.background='linear-gradient(#09122b,#223455)';parts.title=$(" + \
        json.dumps(markup) + ");scene.appendChild(parts.title);}"
    kit.js += "function frame(t){parts.title.style.transform='translateX('+t+'px)';}"
    contracts.install(learner)
    result = learner.check(tmp_path, {}, tmp_path / "valid", log=lambda value: None)
    assert result["browser"] and result["bad"] == []
    assert not any(row.get("kind") == contracts.KIND for row in result["types"]["title"]["text"])


def test_unavailable_or_unknown_private_contract_is_not_guessed():
    assert contracts.install(SimpleNamespace()) is None


def test_original_validate_repairs_from_diagnostic_and_rechecks_original_browser(original_learner, tmp_path):
    learner, kit = original_learner
    (tmp_path / "kit.js").write_text(BROKEN_KIT)
    (tmp_path / "kit.json").write_text(json.dumps({"types": {"title": 5}}))
    requests = []
    # No model is called. This fixture supplies the edit the original learner
    # applies; the adapter itself only reports actual parser evidence.
    def ask(engine, clock, stage, prompt, **kwargs):
        requests.append((stage, prompt))
        return json.dumps({"edits": [{"find": BROKEN_KIT, "replace": CORRECT_KIT}]})
    learner._ask = ask
    contracts.install(learner)
    result = learner.validate(SimpleNamespace(SCRIPT_MODEL="fixture-model"), tmp_path, {},
                              SimpleNamespace(log=lambda value: None, left=lambda: 360), tmp_path)
    assert len(requests) == 1 and requests[0][0] == "validate"
    assert "kit_html_style: malformed inline CSS" in requests[0][1]
    assert result["browser"] and result["bad"] == [] and result["kept"] == ["title"]
    assert (tmp_path / "kit.js").read_text() == CORRECT_KIT
    assert result["types"]["title"]["frames"]

