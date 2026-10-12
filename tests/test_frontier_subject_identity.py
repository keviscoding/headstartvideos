"""Animal compounds use original subject matching without demanding a human."""
import ast
import copy
import hashlib
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local_runtime"))
import cr_subject_identity as identity


@pytest.fixture
def original(monkeypatch):
    source = Path(os.environ.get("FRONTIER_TEST_ENGINE_DIR", Path.home() / "Downloads/ChannelRecipe studio")) / "threed/director/storyboard.py"
    if not source.is_file():
        pytest.skip("Owner's private subject checker is required; it is not published with these tests.")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    names = {"_stem", "_toks", "_canon", "_head", "_terms_of", "_match_thing", "_has", "_hits", "_tstr"}
    constants = {"THINGS", "_STOP", "_TERM"}
    parsed = ast.parse(source.read_text(encoding="utf-8"))
    nodes = [n for n in parsed.body if isinstance(n, ast.FunctionDef) and n.name in names
             or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constants for t in n.targets)]
    # Compile only read-only lexical helpers, with no creative prompt, API,
    # renderer, or job state involved.
    env = {"re": re}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), env)
    sb = SimpleNamespace(**env)
    old = sb._canon
    monkeypatch.setitem(sys.modules, "threed", SimpleNamespace(SB=sb))
    wrapped = identity.install()
    # Real functions refer to their module globals, not the test namespace.
    env["_canon"] = wrapped
    yield sb, old
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("name,head", [
    ("young worker bee", "bee"), ("worker bees", "bee"),
    ("soldier ant", "ant"), ("army ants", "ant"),
    ("soldier beetle", "beetle"), ("nurse shark", "shark"),
    ("passenger pigeon", "pigeon"), ("worker termite", "termite"),
    ("young worker bee with pollen", "bee"),
])
def test_animal_compounds_fall_back_to_actual_head(original, name, head):
    sb, old = original
    assert old(name) == ["people"]
    assert sb._terms_of(name) == ([], [head])


@pytest.mark.parametrize("name", [
    "human worker", "young worker", "factory workers", "the nurse", "a passenger",
    "worker with a bee", "worker holding a bee", "a worker and a bee",
    "a worker or a bee", "the worker named Bee", "worker beside a bee",
    "worker bee keeper", "a worker in a bee suit", "crew of the ship",
    "the man injured by a bee", "a queen", "human queen", "queen bee",
    "worker cobra", "worker dinosaur", "passenger plane", "army base",
    "soldier with a rifle", "worker robot", "worker with a snake",
])
def test_humans_known_canonicals_and_ambiguous_phrases_keep_original_meaning(original, name):
    sb, old = original
    assert sb._canon(name) == old(name)


def thing(sb, name, figure=False, label=False):
    return {"strong": sb._tstr(name), "weak": "  ", "sec": "objects",
            "figure": figure, "label": label}


def test_original_matching_still_requires_animal_identity_not_a_human_or_caption(original):
    sb, _ = original
    canon, heads = sb._terms_of("young worker bee")
    assert not canon and heads == ["bee"]
    bee = thing(sb, "young worker bee")
    before = copy.deepcopy(bee)
    assert sb._match_thing(bee, head=heads[0]) == "strong"
    assert sb._match_thing(thing(sb, "human worker", figure=True), head=heads[0]) == ""
    assert sb._match_thing(thing(sb, "worker ant"), head=heads[0]) == ""
    assert sb._match_thing(thing(sb, "bee", label=True), head=heads[0]) == ""
    assert bee == before


def test_human_worker_still_requires_a_figure_not_an_animal_recipe(original):
    sb, _ = original
    assert sb._terms_of("human worker") == (["people"], [])
    assert sb._match_thing(thing(sb, "worker bee"), canon="people") == ""
    assert sb._match_thing(thing(sb, "human worker", figure=True), canon="people") == "strong"


def test_wrapper_is_idempotent_and_does_not_mutate_original_lexicon(original):
    sb, _ = original
    before = copy.deepcopy(sb.THINGS)
    assert identity.install() is sb._canon
    sb._canon("army ant")
    assert sb.THINGS == before


def test_unavailable_or_incompatible_original_checker_is_unchanged(monkeypatch):
    monkeypatch.setitem(sys.modules, "threed", SimpleNamespace())
    assert identity.install() is None
    sb = SimpleNamespace(_canon=lambda text: ["people"], THINGS={})
    before = sb._canon
    monkeypatch.setitem(sys.modules, "threed", SimpleNamespace(SB=sb))
    assert identity.install() is None and sb._canon is before


def test_new_distinct_canonical_is_never_discarded(monkeypatch):
    # A future engine may gain an animal canonical; that more specific identity
    # must win without any changes to this adapter.
    sb = SimpleNamespace(_canon=lambda text: ["people", "insect"],
                         _toks=lambda text: text.split(), _head=lambda text: text.split()[-1],
                         THINGS={"people": {"aka": ["worker"]}})
    monkeypatch.setitem(sys.modules, "threed", SimpleNamespace(SB=sb))
    identity.install()
    assert sb._canon("worker bee") == ["people", "insect"]
