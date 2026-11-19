"""Claude through another provider that speaks Anthropic's Messages API (Claude Code's own "Other provider" sign-in;
owner, 2026-11-19 "can we fix this?"). Setup saves its address and key; every Claude Code question Studio asks then
carries them as ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN. Inherited ones still never bill."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from local_runtime import cr_claude

TOKEN = "kie-token-" + "a1B2c3" * 4
KEY = "sk-ant-api03-" + "a1B2_c3-" * 6
KIE = "https://api.kie.ai/claude"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(cr_claude, "HOME", tmp_path)
    for name in (cr_claude.API_KEY_NAME, cr_claude.PROVIDER_URL_NAME, cr_claude.PROVIDER_TOKEN_NAME):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def save(home, url=KIE, token=TOKEN, extra=""):
    (home / ".env").write_text(f"{extra}{cr_claude.PROVIDER_URL_NAME}={url}\n{cr_claude.PROVIDER_TOKEN_NAME}='{token}'\n",
                               encoding="utf-8")


def test_inherited_provider_settings_never_bill(home):
    env = cr_claude.clean_environment({"ANTHROPIC_BASE_URL": "https://elsewhere.example", "ANTHROPIC_AUTH_TOKEN": "x" * 20,
                                       "PATH": "/usr/bin"})
    assert "ANTHROPIC_BASE_URL" not in env and "ANTHROPIC_AUTH_TOKEN" not in env and env["PATH"] == "/usr/bin"


def test_the_saved_provider_goes_to_claude_code_ahead_of_a_saved_anthropic_key(home):
    save(home, extra=f"{cr_claude.API_KEY_NAME}={KEY}\n")
    env = cr_claude.clean_environment({"ANTHROPIC_BASE_URL": "https://elsewhere.example"})
    assert env["ANTHROPIC_BASE_URL"] == KIE and env["ANTHROPIC_AUTH_TOKEN"] == TOKEN
    assert "ANTHROPIC_API_KEY" not in env, "one account bills, never two"
    assert cr_claude.PROVIDER_TOKEN_NAME not in env and cr_claude.PROVIDER_URL_NAME not in env


def test_half_a_provider_or_a_broken_address_is_never_used(home):
    save(home, url="http://api.kie.ai/claude")
    assert "ANTHROPIC_BASE_URL" not in cr_claude.clean_environment({})
    save(home, token="has space in it")
    assert "ANTHROPIC_AUTH_TOKEN" not in cr_claude.clean_environment({})
    (home / ".env").write_text(f"{cr_claude.PROVIDER_URL_NAME}={KIE}\n", encoding="utf-8")
    assert cr_claude.other_provider() == {}


def test_the_address_is_given_without_a_trailing_v1():
    assert cr_claude.provider_url("https://api.kie.ai/claude/v1/") == KIE
    assert cr_claude.provider_url("https://gw.example.com/v1/messages") == "https://gw.example.com"
    assert cr_claude.provider_url("https://gw.example.com:8443/claude") == "https://gw.example.com:8443/claude"
    assert cr_claude.provider_url("https://gw.example.com/?key=1") == ""
    assert cr_claude.provider_label(KIE) == "Kie.ai" and cr_claude.provider_label("https://gw.example.com/x") == "gw.example.com"


def _claude(auth):
    calls = []

    def run(args, **kw):
        calls.append(kw["env"])
        return SimpleNamespace(returncode=0 if auth.get("loggedIn") else 1,
                               stdout="2.1.291" if "--version" in args else json.dumps(auth))
    return run, calls


def test_claude_code_on_the_saved_provider_is_ready_and_says_who_bills(home):
    save(home)
    # what Claude Code 2.1.291 answers with ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN set
    run, calls = _claude({"loggedIn": True, "authMethod": "oauth_token", "apiProvider": "firstParty"})
    found = cr_claude.status("/fake/claude", run=run, environment={})
    assert found["ready"] and found["other_provider"] == "Kie.ai" and not found["api_key"]
    assert found["message"] == cr_claude.provider_billing(KIE) and "Kie.ai bills you" in found["message"]
    assert found["account_fingerprint"] == hashlib.sha256(("provider:" + KIE + "\n" + TOKEN).encode()).hexdigest()
    assert TOKEN not in json.dumps(found), "the key itself never leaves cr_claude"
    assert all(env["ANTHROPIC_AUTH_TOKEN"] == TOKEN and env["ANTHROPIC_BASE_URL"] == KIE for env in calls)


def test_an_oauth_token_from_somewhere_else_is_still_not_ready(home):
    run, _ = _claude({"loggedIn": True, "authMethod": "oauth_token", "apiProvider": "firstParty"})
    found = cr_claude.status("/fake/claude", run=run, environment={})
    assert not found["ready"] and not found["other_provider"] and "another provider" in found["message"]


def test_bedrock_or_vertex_through_the_provider_is_not_firstparty(home):
    save(home)
    run, _ = _claude({"loggedIn": True, "authMethod": "oauth_token", "apiProvider": "bedrock"})
    assert not cr_claude.status("/fake/claude", run=run, environment={})["ready"]


class _Answer:
    def __init__(self, code, body=None):
        self.status_code, self.body = code, body

    def json(self):
        if self.body is None:
            raise ValueError("no json")
        return self.body


def test_the_provider_is_checked_for_free_before_it_is_saved():
    seen = {}

    def get(url, **kw):
        seen.update(url=url, **kw)
        return _Answer(200, {"data": [{"id": "claude-sonnet-5-5"}, {"id": "claude-opus-5-5"}]})
    assert cr_claude.check_other_provider(KIE + "/v1", TOKEN, get=get) == ""
    # Kie's model list lives under /anthropic, as the website checks it
    assert seen["url"] == "https://api.kie.ai/anthropic/v1/models" and seen["headers"]["Authorization"] == "Bearer " + TOKEN
    assert cr_claude.check_other_provider("https://gw.example.com", TOKEN, get=get) == ""
    assert seen["url"] == "https://gw.example.com/v1/models"
    assert "didn't accept" in cr_claude.check_other_provider(KIE, TOKEN, get=lambda url, **kw: _Answer(401))
    assert "HTTP 502" in cr_claude.check_other_provider(KIE, TOKEN, get=lambda url, **kw: _Answer(502))
    # a provider without a model list is left to Setup's tiny Claude check
    assert cr_claude.check_other_provider(KIE, TOKEN, get=lambda url, **kw: _Answer(404)) == ""
    assert "claude-sonnet-5-5" in cr_claude.check_other_provider(
        KIE, TOKEN, get=lambda url, **kw: _Answer(200, {"data": [{"id": "gpt-6"}]}))

    def down(url, **kw):
        raise requests.ConnectionError("no route")
    assert "didn't answer" in cr_claude.check_other_provider(KIE, TOKEN, get=down)
    never = lambda *a, **kw: pytest.fail("a broken address or key is never sent")
    assert "https" in cr_claude.check_other_provider("http://api.kie.ai/claude", TOKEN, get=never)
    assert "whole API key" in cr_claude.check_other_provider(KIE, "short", get=never)


def test_setup_saves_and_removes_the_provider_through_its_own_routes():
    page = (ROOT / "local_runtime" / "cr_setup.html").read_text(encoding="utf-8")
    studio = (ROOT / "local_runtime" / "cr_studio.py").read_text(encoding="utf-8")
    assert 'id="claude-provider-form"' in page and "/cr-claude/provider'" in page
    assert "/cr-claude/provider/remove'" in page and '<option value="kie">' in page
    assert '@studio.app.post("/cr-claude/provider")' in studio
    assert '@studio.app.post("/cr-claude/provider/remove")' in studio
    assert cr_claude.PROVIDER_PRESETS["kie"] == KIE
