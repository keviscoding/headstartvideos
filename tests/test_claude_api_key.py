"""Claude on the creator's own Anthropic API key (owner, 2026-11-06: a customer couldn't finish a single video on a
Claude Pro plan's limits). Setup saves the key; every Claude Code question Studio asks then carries it as
ANTHROPIC_API_KEY. An inherited ANTHROPIC_API_KEY still never bills."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from local_runtime import cr_claude

KEY = "sk-ant-api03-" + "a1B2_c3-" * 6
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(cr_claude, "HOME", tmp_path)
    monkeypatch.delenv(cr_claude.API_KEY_NAME, raising=False)
    return tmp_path


def test_an_inherited_key_never_bills_without_a_saved_one(home):
    env = cr_claude.clean_environment({"ANTHROPIC_API_KEY": "sk-ant-inherited-must-not-bill", "PATH": "/usr/bin"})
    assert "ANTHROPIC_API_KEY" not in env and env["PATH"] == "/usr/bin"


def test_the_saved_key_goes_to_claude_code_in_place_of_any_inherited_one(home):
    (home / ".env").write_text(f"KIE_API_KEY=kie\n{cr_claude.API_KEY_NAME}='{KEY}'\n", encoding="utf-8")
    env = cr_claude.clean_environment({"ANTHROPIC_API_KEY": "sk-ant-inherited-must-not-bill", "KIE_API_KEY": "kie"})
    assert env["ANTHROPIC_API_KEY"] == KEY
    assert cr_claude.API_KEY_NAME not in env and "KIE_API_KEY" not in env, "only Claude's key, under Claude's name"


def test_the_key_in_the_environment_counts_too_and_a_broken_one_never(home, monkeypatch):
    monkeypatch.setenv(cr_claude.API_KEY_NAME, KEY)
    assert cr_claude.clean_environment({})["ANTHROPIC_API_KEY"] == KEY
    monkeypatch.setenv(cr_claude.API_KEY_NAME, "not a key")
    assert "ANTHROPIC_API_KEY" not in cr_claude.clean_environment({})


def _claude(auth):
    calls = []

    def run(args, **kw):
        calls.append(kw["env"])
        return SimpleNamespace(returncode=0 if auth.get("loggedIn") else 1,
                               stdout="2.1.291" if "--version" in args else json.dumps(auth))
    return run, calls


def test_claude_code_on_the_saved_key_is_ready_and_says_who_bills(home):
    (home / ".env").write_text(f"{cr_claude.API_KEY_NAME}={KEY}\n", encoding="utf-8")
    run, calls = _claude({"loggedIn": True, "authMethod": "api_key", "apiProvider": "firstParty",
                          "apiKeySource": "ANTHROPIC_API_KEY"})
    found = cr_claude.status("/fake/claude", run=run, environment={})
    assert found["ready"] and found["api_key"] is True and found["auth_method"] == "api_key"
    assert found["message"] == cr_claude.API_KEY_BILLING and "no plan limits" in found["message"]
    assert found["account_fingerprint"] == hashlib.sha256(("api-key:" + KEY).encode()).hexdigest()
    assert KEY not in json.dumps(found), "the key itself never leaves cr_claude"
    assert all(env["ANTHROPIC_API_KEY"] == KEY for env in calls)


def test_a_key_claude_code_found_somewhere_else_is_not_studios(home):
    run, _ = _claude({"loggedIn": True, "authMethod": "api_key", "apiProvider": "firstParty",
                      "apiKeySource": "apiKeyHelper"})
    found = cr_claude.status("/fake/claude", run=run, environment={})
    assert not found["ready"] and not found["api_key"] and "API key" in found["message"]


def test_the_plan_sign_in_works_as_before(home):
    run, calls = _claude({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty",
                          "subscriptionType": "pro", "email": "a@example.com"})
    found = cr_claude.status("/fake/claude", run=run, environment={})
    assert found["ready"] and not found["api_key"] and "usage limits apply" in found["message"]
    assert all("ANTHROPIC_API_KEY" not in env for env in calls)


class _Answer:
    def __init__(self, code):
        self.status_code = code


def test_the_key_is_checked_with_anthropic_for_free_before_it_is_saved():
    seen = {}

    def get(url, **kw):
        seen.update(url=url, **kw)
        return _Answer(200)
    assert cr_claude.check_api_key(KEY, get=get) == ""
    assert seen["url"] == "https://api.anthropic.com/v1/models" and seen["headers"]["x-api-key"] == KEY
    assert "didn't accept" in cr_claude.check_api_key(KEY, get=lambda url, **kw: _Answer(401))
    assert "HTTP 529" in cr_claude.check_api_key(KEY, get=lambda url, **kw: _Answer(529))

    def down(url, **kw):
        raise requests.ConnectionError("no route")
    assert "didn't answer" in cr_claude.check_api_key(KEY, get=down)
    assert "sk-ant-" in cr_claude.check_api_key("sk-ant-short",
                                               get=lambda *a, **kw: pytest.fail("a broken key is never sent"))


def test_setup_saves_and_removes_the_key_through_its_own_routes():
    page = (ROOT / "local_runtime" / "cr_setup.html").read_text(encoding="utf-8")
    studio = (ROOT / "local_runtime" / "cr_studio.py").read_text(encoding="utf-8")
    assert 'id="claude-api-form"' in page and "/cr-claude/api-key'" in page and "/cr-claude/api-key/remove'" in page
    assert '@studio.app.post("/cr-claude/api-key")' in studio and '@studio.app.post("/cr-claude/api-key/remove")' in studio
    assert "console.anthropic.com" in page
