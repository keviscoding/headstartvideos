"""ChannelRecipe Studio inside Claude Code: the studio tool server, the folder settings, the gallery and notifications."""
import io
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "local_runtime"))
import cr_claude_code as ccc  # noqa: E402
import cr_studio_tools as tools  # noqa: E402


# ── a fake Studio behind tools.call ───────────────────────────────────────────────────────────────────────────

CATALOG = {
    "prices": {"features": {"ai_images": {"per_min": 0.05}, "youtube": {"per_min": 0.03}, "depth": {"per_min": 0.004}},
               "voice": {"per_min": 0.02}},
    "channels": [
        {"name": "documentary", "label": "Editorial Documentary", "tagline": "Real footage and maps. More words.",
         "family": "Documentary", "cost": "about $0.08 a minute", "custom": False,
         "features": {"offered": ["youtube", "ai_images", "depth"], "on": ["youtube"]},
         "preview_url": None, "art_url": "/cr-workspace/art/documentary.svg"},
        {"name": "cr-shorts-3d", "label": "3D Explainer Shorts", "tagline": "Weird facts.", "short_seconds": 25,
         "features": {"offered": [], "on": []}},
        {"name": "cr-shorts-ranking", "label": "Ranking Shorts", "tagline": "Clips to #1.", "short_seconds": 25,
         "ranking": True, "features": {"offered": [], "on": []}},
        {"name": "cr-manga-recaps", "label": "Manga Recaps", "tagline": "Panels.", "panels": True,
         "features": {"offered": [], "on": []}},
        {"name": "cr_clone_abc", "label": "My Style", "tagline": "Mine.", "custom": True,
         "features": {"offered": ["ai_images"], "on": ["ai_images"]}},
    ],
}


class FakeStudio:
    def __init__(self):
        self.calls = []
        self.home = None
        self.run_answers = []
        self.notes = {"cr-shorts-3d": "Shorts render on this computer.", "cr-manga-recaps": "Reads your own panels.",
                      "cr-shorts-ranking": "Shorts render on this computer.", "cr_clone_abc": ""}
        self.cloud_quote = {"available": True, "balance_usd": 7.71, "topup_url": "https://channelrecipe.com/app#billing",
                            "label": "Cloud machine", "expected_minutes": 15, "expected_cents": 29, "max_cents": 78}

    def __call__(self, method, path, body=None, **kw):
        self.calls.append((method, path, body))
        if path == "/cr-status":
            return {"local_version": "5.3-admin.89", "readiness": {"ready": True, "blockers": []}}
        if path.startswith("/cr-cloud"):
            return {"available": True, "balance_usd": 7.71}
        if path == "/cr-claude-code/home":
            return {"home": str(self.home or tools.HOME)}
        if path == "/cr-workspace/catalog":
            return json.loads(json.dumps(CATALOG))
        if path == "/cr-claude-code/styles":                      # Studio Cloud's own answer per style
            return {"styles": [{"id": r["name"], "cloud_note": self.notes.get(r["name"], ""), "jpg": ""}
                               for r in CATALOG["channels"]]}
        if path.startswith("/cr-claude-code/quote"):
            return dict(self.cloud_quote)
        if path == "/api/run":
            answer = self.run_answers.pop(0) if self.run_answers else {"queued": [{"slug": "my-video", "title": "T"}]}
            if isinstance(answer, Exception):
                raise answer
            return answer
        if path == "/cr-cloud/start":
            return {"started": True}
        if path == "/cr-claude/setup":
            return {"ok": True}
        if path == "/cr-gallery/picks":
            return {"pick_id": "pk_" + "a" * 24}
        if path == "/cr-claude-code/combine-preview":
            return {"preview_id": "pv_" + "c" * 64, "reference_urls": body["urls"], "candidates": [
                {"id": "a1aaaaaaaaa", "title": "A big", "views": 900, "eligible": True, "source": 0, "channel": "A"},
                {"id": "a2aaaaaaaaa", "title": "A small", "views": 800, "eligible": True, "source": 0, "channel": "A"},
                {"id": "b1bbbbbbbbb", "title": "B big", "views": 50, "eligible": True, "source": 1, "channel": "B"},
                {"id": "b2bbbbbbbbb", "title": "B short", "views": 9000, "eligible": False, "source": 1}]}
        if path == "/cr-style-clone/look":
            return {"look_id": "lk_" + "d" * 24, "state": "running", "message": "Looking"}
        if path == "/api/offers/pack":
            return {"pack": {"ideas": [{"title": "Pantry Book", "promise": "Cook from the cupboard", "kind": "cookbook"},
                                       {"title": "Money Basics", "promise": "Fix your money", "kind": "lesson-book"}],
                             "names": [{"name": "Cupboard Chef"}],
                             "domains": [{"domain": "cupboardchef.com", "free": True},
                                         {"domain": "pantry.com", "free": False}],
                             "pricing": {"launch_usd": 29}, "launch": ["Buy the domain"], "file": "/x/pack.json"}}
        if path == "/api/products/build":
            return {"slug": "pantry-book"}
        if path == "/api/products/build/pantry-book":
            return {"slug": "pantry-book", "running": True, "code": None, "lines": ["write: 12/60"]}
        if path == "/cr-workspace/stopped":
            return {"projects": [{"slug": "zombie--abc", "title": "Zombie Outbreak", "style": "stories", "mtime": 1}]}
        if path == "/api/queue":
            return {"jobs": [], "busy": False}
        if path == "/cr-workspace/activity":
            return {"operations": []}
        if path == "/cr-workspace/project/zombie--abc":
            return {"slug": "zombie--abc", "project_id": "crv_" + "1" * 24, "available_for_resume": True,
                    "title": "Zombie Outbreak", "status": "error"}
        if path == "/cr-workspace/project/done--abc":
            return {"slug": "done--abc", "project_id": "crv_" + "2" * 24, "available_for_resume": False,
                    "title": "Done", "status": "done"}
        if path == "/cr-submit-project":
            return {"queued": [{"slug": "zombie--abc"}], "project_id": body["project_id"]}
        if path == "/api/status/zombie--abc":
            return {"state": "error", "done": True, "progress": 0.08, "error": "Kie.ai failed this narration task.",
                    "lines": ["Writing the voiceover"]}
        if path == "/cr-workspace/voice/zombie--abc":
            if method == "POST":
                return {"saved": True, "choice": body["choice"]}
            return {"stopped_on_voice": True, "has_voice": False, "current": "again",
                    "failed": {"label": "Kie.ai", "reason": "Internal Error, Please try again later", "charged": 0},
                    "script": "The vault opened.", "script_file": "/studio/output/x/zombie--abc/script.txt",
                    "choices": [{"id": "again", "label": "Try the same voice again", "ready": True, "note": "n"},
                                {"id": "gemini-man", "label": "Gemini", "ready": True, "note": "n"},
                                {"id": "elevenlabs", "label": "ElevenLabs", "ready": False, "note": "n",
                                 "setup": "/cr-setup"},
                                {"id": "own", "label": "Own", "ready": True, "note": "n"}]}
        if path == "/api/products":
            return {"products": [{"slug": "pantry-book", "title": "Pantry Book", "secret": "no"}]}
        raise AssertionError("unexpected route " + path)

    def posted(self, path):
        return [body for method, p, body in self.calls if p == path and method == "POST"]


@pytest.fixture(autouse=True)
def pointer(monkeypatch, tmp_path):
    """configure() also tells the plugin where Studio is; never the real ~/.channelrecipe in tests."""
    monkeypatch.setattr(ccc, "POINTER", tmp_path / "pointer" / "studio-home.json")
    return ccc.POINTER


@pytest.fixture
def studio(monkeypatch, tmp_path):
    fake = FakeStudio()
    monkeypatch.setattr(tools, "call", fake)
    monkeypatch.setattr(tools, "STATE", tmp_path / "state")
    monkeypatch.setattr(tools, "thumbnail", lambda row: "")
    return fake


def ok(name, args):
    result = tools.run_tool(name, args)
    assert result["isError"] is False, result
    return result["structuredContent"]


# ── money is shown before it is spent ─────────────────────────────────────────────────────────────────────────

def test_make_videos_starts_nothing_without_the_quote(studio):
    out = ok("make_videos", {"titles": ["Why Rome fell"], "style": "documentary", "minutes": 10, "where": "this_computer"})
    assert out["started"] is False and out["quote"]["quote_id"].startswith("q_")
    assert "Nothing started" in out["next"] and not studio.posted("/api/run")


def test_make_videos_with_a_fresh_quote_starts_like_the_create_page(studio):
    q = ok("quote", {"style": "documentary", "minutes": 10, "titles": 2})
    out = ok("make_videos", {"titles": ["One", "Two"], "style": "documentary", "minutes": 10,
                             "where": "this_computer", "quote_id": q["quote_id"]})
    assert out["started"] is True and out["videos"] == [{"id": "video:my-video", "title": "T"}]
    body = studio.posted("/api/run")[0]
    assert body["title"] == "One\nTwo" and body["style"] == "documentary" and body["minutes"] == 10
    assert body["features"] == {"youtube": True, "ai_images": False, "depth": False}
    assert body["steps"] == ["script", "voiceover", "images", "pexels", "video", "thumbnail"]
    assert body["burn_subs"] is True and body["desc"] is True and body["force"] is False


def test_a_quote_for_other_choices_or_an_old_one_starts_nothing(studio, monkeypatch):
    q = ok("quote", {"style": "documentary", "minutes": 10})
    out = ok("make_videos", {"titles": ["One"], "style": "documentary", "minutes": 12, "where": "this_computer",
                             "quote_id": q["quote_id"]})
    assert out["started"] is False
    quotes = json.loads((tools.STATE / "quotes.json").read_text())
    quotes[q["quote_id"]] = time.time() - tools.QUOTE_SECONDS - 5
    (tools.STATE / "quotes.json").write_text(json.dumps(quotes))
    out = ok("make_videos", {"titles": ["One"], "style": "documentary", "minutes": 10, "where": "this_computer",
                             "quote_id": q["quote_id"]})
    assert out["started"] is False and not studio.posted("/api/run")


def test_cloud_quote_and_start(studio):
    q = ok("quote", {"style": "documentary", "minutes": 1, "titles": 1, "where": "studio_cloud"})
    assert q["cloud"]["expected_usd_each"] == 0.29 and q["cloud"]["reserved_usd_each"] == 0.78
    assert "Studio Cloud" in q["summary"] and "$7.71" in q["summary"]
    out = ok("make_videos", {"titles": ["Pyramids"], "style": "documentary", "minutes": 1, "where": "studio_cloud",
                             "quote_id": q["quote_id"]})
    assert out["started"] is True and out["progress_id"] == "cloud"
    assert studio.posted("/cr-cloud/start")[0]["title"] == "Pyramids" and not studio.posted("/api/run")


def test_cloud_refuses_local_only_recipes_and_a_short_balance(studio):
    result = tools.run_tool("quote", {"style": "cr-shorts-3d", "where": "studio_cloud"})
    assert result["isError"] and "Shorts render on this computer" in result["structuredContent"]["error"]
    studio.cloud_quote["balance_usd"] = 1.0
    q = ok("quote", {"style": "documentary", "minutes": 1, "titles": 2, "where": "studio_cloud"})
    assert q["short_by_usd"] == 0.56 and "top up" in q["summary"]
    result = tools.run_tool("make_videos", {"titles": ["A", "B"], "style": "documentary", "minutes": 1,
                                            "where": "studio_cloud", "quote_id": q["quote_id"]})
    assert result["isError"] and "Top up" in result["structuredContent"]["error"]
    assert not studio.posted("/cr-cloud/start")


def test_titles_and_recipes_that_need_the_page(studio):
    assert tools.run_tool("make_videos", {"titles": list("ABCDEF"), "style": "documentary",
                                          "where": "this_computer"})["isError"]
    q = ok("quote", {"style": "cr-manga-recaps"})
    result = tools.run_tool("make_videos", {"titles": ["Recap"], "style": "cr-manga-recaps", "where": "this_computer",
                                            "quote_id": q["quote_id"]})
    assert result["isError"] and "panels" in result["structuredContent"]["error"]


def test_shorts_and_ranking_options(studio):
    q = ok("quote", {"style": "cr-shorts-ranking"})
    ok("make_videos", {"titles": ["Funniest dog moments"], "style": "cr-shorts-ranking", "where": "this_computer",
                       "quote_id": q["quote_id"], "options": {"short_seconds": 40, "ranking": {"ranks": 7,
                                                                                            "meme_sounds": "light"}}})
    body = studio.posted("/api/run")[0]
    assert body["minutes"] == 1 and body["extra"].startswith("[[short_seconds=40]] [[ranking=")
    ranking = json.loads(body["extra"].split("[[ranking=", 1)[1].split("]]", 1)[0])
    assert ranking["ranks"] == 7 and ranking["sfx"] == "light" and ranking["use_credits"] is False


def test_the_daily_model_check_is_run_once_when_it_is_the_only_blocker(studio):
    studio.run_answers = [tools.Refused("Complete this video's setup before starting.", 409,
                                        {"blockers": ["Verify both Claude models in setup."]}),
                          {"queued": [{"slug": "v", "title": "T"}]}]
    q = ok("quote", {"style": "documentary"})
    out = ok("make_videos", {"titles": ["T"], "style": "documentary", "where": "this_computer",
                             "quote_id": q["quote_id"]})
    assert out["started"] and studio.posted("/cr-claude/setup") == [{"action": "verify"}]


def test_other_blockers_are_read_out_not_worked_around(studio):
    studio.run_answers = [tools.Refused("Complete this video's setup before starting.", 409,
                                        {"blockers": ["Connect a Kie.ai key."]})]
    q = ok("quote", {"style": "documentary"})
    result = tools.run_tool("make_videos", {"titles": ["T"], "style": "documentary", "where": "this_computer",
                                            "quote_id": q["quote_id"]})
    assert result["isError"] and "Kie.ai" in result["structuredContent"]["error"]
    assert not studio.posted("/cr-claude/setup")


def test_list_styles_gives_cards_and_a_picker(studio):
    out = ok("list_styles", {"kind": "cloud"})
    assert [s["id"] for s in out["styles"]] == ["documentary", "cr_clone_abc"]      # as Studio Cloud says
    assert out["gallery_url"] == "http://localhost:7860/cr-gallery?pick=pk_" + "a" * 24
    card = out["styles"][0]
    assert card["description"] == "Real footage and maps." and card["renders_on"] == ["this_computer", "studio_cloud"]
    mine = ok("list_styles", {"kind": "mine"})["styles"]
    assert [s["id"] for s in mine] == ["cr_clone_abc"] and mine[0]["renders_on"] == ["this_computer", "studio_cloud"]
    studio.notes["cr_clone_abc"] = "This recipe renders on this computer for now."
    mine = ok("list_styles", {"kind": "mine"})["styles"]
    assert mine[0]["renders_on"] == ["this_computer"] and mine[0]["cloud_note"].startswith("This recipe")


# ── talking to Studio ─────────────────────────────────────────────────────────────────────────────────────────

class Answer:
    def __init__(self, status, body):
        self.status, self.body = status, json.dumps(body).encode()

    def read(self, n=None):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_call_waits_out_a_busy_studio_and_sends_the_local_token(monkeypatch, tmp_path):
    (tmp_path / ".cr-runtime.json").write_text(json.dumps({"token": "tok"}))
    monkeypatch.setattr(tools, "HOME", tmp_path)
    monkeypatch.setattr(tools.time, "sleep", lambda s: None)
    seen, answers = [], [409, 409, 200]

    def urlopen(request, timeout=None):
        seen.append(request.get_header("X-cr-local-token"))
        status = answers.pop(0)
        if status == 409:
            raise tools.urllib.error.HTTPError(request.full_url, 409, "busy", {}, io.BytesIO(
                json.dumps({"error": "Finish the current studio request before starting more work."}).encode()))
        return Answer(200, {"queued": []})
    monkeypatch.setattr(tools.urllib.request, "urlopen", urlopen)
    assert tools.call("POST", "/api/run", {}) == {"queued": []}
    assert seen == ["tok", "tok", "tok"]


def test_a_closed_studio_says_to_open_the_launcher(monkeypatch):
    def urlopen(request, timeout=None):
        raise tools.urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
    monkeypatch.setattr(tools.urllib.request, "urlopen", urlopen)
    out = ok("setup_check", {})
    assert out["running"] is False and "launcher" in out["message"]
    result = tools.run_tool("list_videos", {})
    assert result["isError"] and "launcher" in result["structuredContent"]["error"]


def test_mcp_handshake_and_tool_list():
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {"protocolVersion": "2025-03-26", "capabilities": {}}}),
             json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
             json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
             json.dumps({"jsonrpc": "2.0", "id": 3, "method": "nope"})]
    out = io.StringIO()
    tools.serve(io.StringIO("\n".join(lines) + "\n"), out)
    replies = [json.loads(x) for x in out.getvalue().splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3]
    assert replies[0]["result"]["protocolVersion"] == "2025-03-26"
    names = {t["name"] for t in replies[1]["result"]["tools"]}
    assert set(ccc.FREE_TOOLS) | set(ccc.PAID_TOOLS) == names
    assert all(t["inputSchema"]["type"] == "object" for t in replies[1]["result"]["tools"])
    assert replies[2]["error"]["code"] == -32601


# ── the folder's Claude Code settings ─────────────────────────────────────────────────────────────────────────

def test_configure_adds_the_studio_server_beside_the_account_entry(tmp_path):
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("")
    account = {"type": "http", "url": "https://channelrecipe.com/mcp", "headers": {"Authorization": "Bearer cr_mcp_x"}}
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"channelrecipe": account}}))
    assert ccc.configure(tmp_path)["changed"] == [".mcp.json", ".claude/settings.json"]
    servers = json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]
    assert servers["channelrecipe"] == account
    assert servers["studio"]["command"] == str(tmp_path / ".venv" / "bin" / "python")
    assert servers["studio"]["args"] == [str(tmp_path / "cr_studio_tools.py")]
    if os.name != "nt":                         # POSIX permission bits; Windows keeps the file private by its ACL
        assert (tmp_path / ".mcp.json").stat().st_mode & 0o777 == 0o600
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert settings["enabledMcpjsonServers"] == ["channelrecipe", "studio"]
    assert "mcp__studio__make_videos" in settings["permissions"]["ask"]
    assert "mcp__studio__list_styles" in settings["permissions"]["allow"]
    assert "Read(./.env)" in settings["permissions"]["deny"]
    assert ccc.configure(tmp_path)["changed"] == []                       # idempotent


def test_configure_keeps_the_creators_settings_and_never_allows_paid_tools(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({
        "model": "opus", "enabledMcpjsonServers": ["mine"],
        "permissions": {"allow": ["Bash(ls:*)", "mcp__studio__make_videos"], "ask": ["Bash(rm:*)"]}}))
    ccc.configure(tmp_path)
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert settings["model"] == "opus" and settings["enabledMcpjsonServers"] == ["mine", "channelrecipe", "studio"]
    assert "Bash(ls:*)" in settings["permissions"]["allow"]
    assert "mcp__studio__make_videos" not in settings["permissions"]["allow"]
    assert settings["permissions"]["ask"][:1] == ["Bash(rm:*)"]


def test_configure_leaves_unreadable_files_alone(tmp_path):
    (tmp_path / ".mcp.json").write_text("{not json")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("[]")
    assert ccc.configure(tmp_path)["changed"] == []
    assert (tmp_path / ".mcp.json").read_text() == "{not json"


def test_the_updater_configures_with_the_new_release_code(tmp_path):
    import cr_update
    (tmp_path / "cr_claude_code.py").write_text(
        "from pathlib import Path\ndef configure(home):\n    (Path(home) / 'configured').write_text('yes')\n")
    cr_update.configure_claude_code(tmp_path)
    assert (tmp_path / "configured").read_text() == "yes"
    cr_update.configure_claude_code(tmp_path / "missing")                   # never raises


def test_the_release_ships_the_skills():
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_frontier_admin as builder
    shipped = {relative for _, relative in builder.runtime_resources(ROOT)}
    for name in ("make-videos", "clone-style", "cloud-batch", "my-videos", "niche-strategy", "digital-product"):
        assert f".claude/skills/{name}/SKILL.md" in shipped
    assert {"cr_studio_tools.py", "cr_claude_code.py", "cr_gallery.html"} <= shipped


def test_skills_are_claudes_to_use_and_money_stays_with_the_tools():
    # every skill is Claude's to use when the creator asks in their own words ("let's make a video"): money is
    # guarded by the tools themselves (a fresh quote_id, and Claude Code's ask rule for the paid tools)
    for name in ("make-videos", "clone-style", "cloud-batch", "my-videos", "niche-strategy", "digital-product"):
        text = (ROOT / "local_runtime" / "claude_skills" / name / "SKILL.md").read_text()
        head = text.split("---", 2)[1]
        assert f"name: {name}" in head and "description:" in head
        assert "disable-model-invocation" not in head


# ── the gallery's picks ───────────────────────────────────────────────────────────────────────────────────────

def test_a_pick_is_chosen_on_the_page_and_final_once_claude_reads_it():
    picks = ccc.Picks()
    pick = picks.open()
    assert picks.get(pick)["state"] == "waiting"
    picks.choose(pick, {"action": "use", "style": "documentary"})
    picks.choose(pick, {"action": "use", "style": "atlas"})                # changed before Claude read it
    assert picks.get(pick)["style"] == "atlas" and not picks.get(pick).get("read")
    assert picks.get(pick, claim=True)["style"] == "atlas"
    with pytest.raises(PermissionError):
        picks.choose(pick, {"action": "use", "style": "jung"})
    picks.rows[pick]["at"] -= ccc.PICK_SECONDS + 1
    assert picks.get(pick)["state"] == "expired"


def test_gallery_routes(tmp_path):
    flask = pytest.importorskip("flask")
    app = flask.Flask("t")

    @app.get("/cr-workspace/catalog")
    def catalog():
        return flask.jsonify(CATALOG)
    studio = SimpleNamespace(app=app)
    (tmp_path / "cr_gallery.html").write_text((ROOT / "local_runtime" / "cr_gallery.html").read_text())
    ccc.install(studio, "tok", tmp_path)
    client = app.test_client()
    base = {"base_url": "http://127.0.0.1:7860"}
    page = client.get("/cr-gallery?pick=pk_" + "b" * 24, **base)
    assert page.status_code == 200 and b'"tok"' in page.data and b'"pk_' + b"b" * 24 + b'"' in page.data
    assert page.headers["X-CR-Workspace"] == "1"
    assert client.get("/cr-gallery", base_url="http://evil.example").status_code == 403
    pick = client.post("/cr-gallery/picks", **base).get_json()["pick_id"]
    assert client.post(f"/cr-gallery/picks/{pick}", json={"style": "nope"}, **base).status_code == 400
    chosen = client.post(f"/cr-gallery/picks/{pick}", json={"style": "documentary"}, **base).get_json()
    assert chosen["name"] == "Editorial Documentary" and chosen["state"] == "chosen"
    assert client.get(f"/cr-gallery/picks/{pick}?claim=1", **base).get_json()["read"] is True
    assert client.post(f"/cr-gallery/picks/{pick}", json={"action": "clone"}, **base).status_code == 403


# ── notifications ─────────────────────────────────────────────────────────────────────────────────────────────

def test_watcher_says_when_videos_and_cloud_renders_finish(tmp_path):
    said = []
    studio = SimpleNamespace(JOBS={"a": {"state": "queued", "title": "Rome"}, "old": {"state": "done", "title": "Old"}},
                             FARM={"state": "running"}, app=None)
    cloud = SimpleNamespace(TASK={"state": "rendering", "title": "Pyramids"})
    watcher = ccc.Watcher(studio, tmp_path, notify_fn=lambda t, m: said.append((t, m)), cloud=cloud)
    watcher.tick()
    assert said == []                                                     # what was already finished stays quiet
    studio.JOBS["a"]["state"] = "running"
    watcher.tick()
    studio.JOBS["a"]["state"] = "done"
    cloud.TASK["state"] = "done"
    watcher.tick()
    watcher.tick()
    assert said == [("Your video is ready", "Rome"), ("Your cloud video is ready", "Pyramids")]
    studio.JOBS["b"] = {"state": "running", "title": "Nile"}
    watcher.tick()
    studio.JOBS["b"].update(state="error", error="Claude's usage limit")
    watcher.tick()
    assert said[-1] == ("Your video stopped", "Nile: Claude's usage limit")


def test_watcher_says_when_a_style_study_is_ready(tmp_path):
    said = []
    folder = tmp_path / "output" / "cr-style-clone" / ("op_" + "c" * 24)
    folder.mkdir(parents=True)
    record = folder / "operation.json"
    record.write_text(json.dumps({"state": "running", "spec": {"label": "My History Style"}}))
    watcher = ccc.Watcher(SimpleNamespace(JOBS={}), tmp_path, notify_fn=lambda t, m: said.append((t, m)),
                          cloud=SimpleNamespace(TASK={"state": "idle"}))
    watcher.tick()
    record.write_text(json.dumps({"state": "complete", "spec": {"label": "My History Style"}}))
    watcher.tick()
    assert said == [("Your new style is ready", "My History Style")]


def test_mac_notification_quotes_its_text(monkeypatch):
    runs = []
    monkeypatch.setattr(ccc.sys, "platform", "darwin")
    monkeypatch.delenv("CR_NOTIFY", raising=False)
    assert ccc.notify('Your video is ready', 'He said "go"', run=lambda args, **kw: runs.append(args))
    assert runs[0][:2] == ["osascript", "-e"] and '"He said \\"go\\""' in runs[0][2]
    monkeypatch.setenv("CR_NOTIFY", "0")
    assert ccc.notify("x", "y", run=lambda *a, **k: runs.append(a)) is False and len(runs) == 1


def test_configure_tells_the_plugin_where_studio_is(tmp_path, pointer):
    ccc.configure(tmp_path / "studio")
    assert json.loads(pointer.read_text()) == {"home": str((tmp_path / "studio").resolve()), "port": 7860}


def test_the_panels_get_cards_with_small_pictures(tmp_path):
    flask = pytest.importorskip("flask")
    image = pytest.importorskip("PIL.Image")
    import io
    buffer = io.BytesIO()
    image.new("RGB", (640, 360), (200, 40, 40)).save(buffer, "JPEG")
    app = flask.Flask("t")
    rows = [dict(CATALOG["channels"][0], preview_url="/cr-workspace/preview/recipe/documentary.jpg?v=abc123"),
            dict(CATALOG["channels"][1], preview_url=None, art_url="/cr-workspace/art/custom.svg")]

    @app.get("/cr-workspace/catalog")
    def catalog():
        return flask.jsonify({"channels": rows, "prices": CATALOG["prices"]})

    @app.get("/cr-workspace/preview/recipe/<name>.jpg")
    def preview(name):
        return flask.Response(buffer.getvalue(), mimetype="image/jpeg")
    (tmp_path / "workspace_art").mkdir()
    (tmp_path / "workspace_art" / "custom.svg").write_text("<svg/>")
    ccc.install(SimpleNamespace(app=app), "tok", tmp_path)
    data = app.test_client().get("/cr-claude-code/styles", base_url="http://127.0.0.1:7860").get_json()
    first, second = data["styles"]
    assert first["id"] == "documentary" and first["cloud_note"] == "" and first["features"]["on"] == ["youtube"]
    assert first["png"].endswith("documentary-abc123-s.png") and Path(first["png"]).read_bytes()[:4] == b"\x89PNG"
    assert image.open(first["jpg"]).size == (320, 180)
    assert second["png"] == "" and second["art"].endswith("custom.svg") and second["cloud_note"]
    assert data["prices"]["voice"]["per_min"] == 0.02


def test_cloud_notes_come_from_studio_clouds_own_rule(monkeypatch):
    import cr_cloud
    monkeypatch.setattr(cr_cloud, "blocked", lambda p: "" if p["style"].startswith("cr_clone") else
                        ("Shorts render on this computer." if p["style"].startswith("cr-shorts") else ""))
    assert ccc.cloud_note({"name": "cr_clone_abc"}) == ""
    assert ccc.cloud_note({"name": "cr-shorts-3d"}) == "Shorts render on this computer."
    assert ccc.cloud_note({"name": "cr-manga-recaps", "panels": True}).startswith("Reads your own panels")
    monkeypatch.setattr(cr_cloud, "blocked", lambda p: 1 / 0)
    assert ccc.cloud_note({"name": "documentary"}) == "Renders on this computer for now."      # in doubt: here


def test_an_older_studio_without_cloud_notes_keeps_cloud_off(studio, monkeypatch):
    real = studio.__call__

    def older(method, path, body=None, **kw):
        if path == "/cr-claude-code/styles":
            raise tools.Refused("not found", 404)
        return real(method, path, body, **kw)
    monkeypatch.setattr(tools, "call", older)
    assert all(s["renders_on"] == ["this_computer"] for s in ok("list_styles", {})["styles"])


def test_combining_two_channels_takes_references_in_turn(studio):
    out = ok("clone_style_look", {"youtube_urls": ["https://youtube.com/@a", "https://youtube.com/@b"]})
    assert studio.posted("/cr-claude-code/combine-preview")[0] == {"urls": ["https://youtube.com/@a",
                                                                       "https://youtube.com/@b"]}
    assert studio.posted("/cr-style-clone/look")[0]["reference_ids"] == ["a1aaaaaaaaa", "b1bbbbbbbbb", "a2aaaaaaaaa"]
    assert out["combining"] == ["https://youtube.com/@a", "https://youtube.com/@b"]
    assert {s["channel"] for s in out["studying"]} == {"A", "B"}
    saved = json.loads((tools.STATE / "looks.json").read_text())["lk_" + "d" * 24]
    assert saved["preview_id"] == "pv_" + "c" * 64


def test_combine_preview_merges_channels_into_one_saved_preview(tmp_path, monkeypatch):
    import cr_style_clone
    monkeypatch.setattr(cr_style_clone, "_folder", lambda home: tmp_path / "studies")
    lists = {"https://youtube.com/@a": [{"id": "a1aaaaaaaaa", "eligible": True}, {"id": "same0000000", "eligible": True}],
             "https://youtube.com/@b": [{"id": "b1bbbbbbbbb", "eligible": True}, {"id": "same0000000", "eligible": True}]}
    fake = lambda home, url: {"reference_url": url, "candidates": lists[url]}             # noqa: E731
    out = ccc.combine_preview(tmp_path, list(lists), preview=fake)
    assert [(c["id"], c["source"]) for c in out["candidates"]] == [("a1aaaaaaaaa", 0), ("same0000000", 0),
                                                                  ("b1bbbbbbbbb", 1)]
    saved = json.loads((tmp_path / "studies" / ".previews" / (out["preview_id"] + ".json")).read_text())
    assert saved["reference_url"] == "https://youtube.com/@a" and saved["combined"] is True and saved["created_at"]
    import re
    assert re.fullmatch(r"pv_[a-f0-9]{64}", out["preview_id"])                # the study's own preview check
    with pytest.raises(ValueError):
        ccc.combine_preview(tmp_path, ["https://youtube.com/@a"], preview=fake)


def test_product_ideas_then_build_by_number(studio):
    out = ok("product_ideas", {"about": "cheap cooking for students"})
    assert [i["title"] for i in out["ideas"]] == ["Pantry Book", "Money Basics"]
    assert out["free_domains"] == ["cupboardchef.com"] and out["taken_domains"] == ["pantry.com"]
    assert studio.posted("/api/offers/pack")[0]["instructions"] == "cheap cooking for students"
    assert tools.run_tool("build_product", {"idea_number": 3})["isError"]
    built = ok("build_product", {"idea_number": 1, "domain": "cupboardchef.com"})
    body = studio.posted("/api/products/build")[0]
    assert body["idea"]["title"] == "Pantry Book" and body["domain"] == "cupboardchef.com"
    assert body["niche"] == "cheap cooking for students" and body["pack_file"] == "/x/pack.json"
    assert built["id"] == "pantry-book" and "$0.30" in built["cost"]
    assert ok("products", {"id": "pantry-book"})["state"] == "building"
    assert ok("products", {})["products"] == [{"slug": "pantry-book", "title": "Pantry Book"}]


def test_niche_database_reads_are_allowed_and_building_a_product_is_asked(tmp_path):
    ccc.configure(tmp_path)
    permissions = json.loads((tmp_path / ".claude" / "settings.json").read_text())["permissions"]
    assert "mcp__channelrecipe__list_niche_channels" in permissions["allow"]
    assert "mcp__studio__product_ideas" in permissions["allow"]
    assert "mcp__studio__build_product" in permissions["ask"]


def test_the_download_has_the_tools_before_studio_ever_runs():
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_frontier_admin as builder
    files = builder.claude_code_files()
    server = json.loads(files[".mcp.json"])["mcpServers"]["studio"]
    assert server["command"] == "node" and server["args"] == ["${CLAUDE_PROJECT_DIR:-.}/cr_studio_launch.js"]
    settings = json.loads(files[".claude/settings.json"])
    assert settings == ccc.folder_settings({}) and "mcp__studio__make_videos" in settings["permissions"]["ask"]
    assert "cr_studio_launch.js" in {relative for _, relative in builder.runtime_resources(ROOT)}
    assert "channelrecipe" not in json.loads(files[".mcp.json"])["mcpServers"]          # no account, no key


def test_studio_start_replaces_the_first_run_entry_and_keeps_shipped_settings_unchanged(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_frontier_admin as builder
    for name, contents in builder.claude_code_files().items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(contents)
    shipped = (tmp_path / ".claude" / "settings.json").read_bytes()
    assert ccc.configure(tmp_path)["changed"] == [".mcp.json"]                     # settings already right
    assert (tmp_path / ".claude" / "settings.json").read_bytes() == shipped        # the updater sees it unedited
    assert json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]["studio"]["args"] == [
        str(tmp_path.resolve() / "cr_studio_tools.py")]


def test_tools_refuse_another_folders_studio(studio):
    studio.home = "/Users/someone/Downloads/ChannelRecipe studio"
    out = ok("setup_check", {})
    assert out["this_folder"] is False and "ChannelRecipe studio" in out["running_folder"]
    result = tools.run_tool("make_videos", {"titles": ["T"], "style": "documentary", "where": "this_computer"})
    assert result["isError"] and "not this folder" in result["structuredContent"]["error"]
    assert not studio.posted("/api/run")


def test_a_stopped_video_is_listed_and_resumed_by_its_saved_project(studio):
    out = ok("progress", {})
    assert out["stopped"][0]["id"] == "video:zombie--abc" and "resume_video" in out["stopped_note"]
    resumed = ok("resume_video", {"id": "video:zombie--abc"})
    assert resumed["resumed"] and studio.posted("/cr-submit-project") == [{"project_id": "crv_" + "1" * 24}]
    refused = tools.run_tool("resume_video", {"id": "video:done--abc"})
    assert refused["isError"] and "can't be resumed" in refused["structuredContent"]["error"]
    assert len(studio.posted("/cr-submit-project")) == 1


def test_a_video_that_stopped_on_its_voice_offers_the_ways_to_voice_it(studio):
    out = ok("progress", {"id": "video:zombie--abc"})
    assert out["stopped_on"] == "voice" and out["voice_failed"]["charged"] == 0
    assert [c["id"] for c in out["voice_choices"]] == ["again", "gemini-man", "elevenlabs", "own"]
    assert "never in chat" in out["voice_note"] and out["script_file"].endswith("script.txt")
    assert "setup" not in out["voice_choices"][2], "only what Claude needs to ask"
    resumed = ok("resume_video", {"id": "video:zombie--abc", "voice": "own", "recording": "/Users/me/take.m4a"})
    assert resumed["voice"] == "own"
    assert studio.posted("/cr-workspace/voice/zombie--abc") == [{"choice": "own", "recording": "/Users/me/take.m4a"}]
    assert studio.posted("/cr-submit-project") == [{"project_id": "crv_" + "1" * 24}], "the choice, then Resume"
    schema = tools.TOOLS["resume_video"][2]["properties"]["voice"]
    assert schema["enum"] == ["again", "gemini-man", "gemini-woman", "elevenlabs", "own"]


def test_setup_check_says_why_the_graphics_browser_isnt_ready_and_the_fix_runs_in_studio(studio, monkeypatch):
    calls = []
    real_call = tools.call

    def call(method, path, body=None, **kw):
        calls.append((method, path))
        if path == "/cr-setup/fix-browser":
            return {"state": "installing"}
        return real_call(method, path, body, **kw)
    monkeypatch.setattr(tools, "call", call)
    out = ok("fix_graphics_browser", {})
    assert out["state"] == "installing" and ("POST", "/cr-setup/fix-browser") in calls
    assert "fix_graphics_browser" in ccc.FREE_TOOLS, "free and pre-approved: it only installs Studio's own browser"


def test_setup_check_names_blocked_services_and_the_speech_model_and_the_fix_runs_in_studio(studio, monkeypatch):
    sys.path.insert(0, str(ROOT / "local_runtime"))
    import cr_network
    network = cr_network.check({"kie": True, "speech_model": True, "youtube": True}, probe=lambda host: {
        "api.kie.ai": "cut", "tempfile.aiquickdraw.com": "cut", "www.youtube.com": "refused"}.get(host, ""))
    real_call = tools.call
    calls = []

    def call(method, path, body=None, **kw):
        calls.append((method, path))
        if path == "/cr-status":
            return {"local_version": "5.3-admin.98", "readiness": {"ready": False, "blockers": [network["message"]]},
                    "network": network, "speech_model": {"ready": False, "state": "missing",
                                                         "reason": "The speech-timing model isn't on this computer yet."}}
        if path == "/cr-setup/fix-speech-model":
            return {"state": "checking"}
        return real_call(method, path, body, **kw)
    monkeypatch.setattr(tools, "call", call)
    out = ok("setup_check", {})
    net = out["network"]
    assert net["ready"] is False and [b["host"] for b in net["blocked"]] == ["api.kie.ai"]
    assert "phone hotspot" in net["blocked"][0]["message"]
    assert [g["host"] for g in net["goes_around"]] == ["tempfile.aiquickdraw.com"]
    assert [y["host"] for y in net["youtube_only"]] == ["www.youtube.com"] and "plain words" in net["what_to_do"]
    assert out["speech_model"]["ready"] is False and "fix_speech_model" in out["speech_model"]["fix"]
    fixed = ok("fix_speech_model", {})
    assert fixed["state"] == "checking" and ("POST", "/cr-setup/fix-speech-model") in calls
    assert "fix_speech_model" in ccc.FREE_TOOLS, "free and pre-approved: it only downloads Studio's own model"


def test_setup_check_on_a_clear_network_has_nothing_to_say(studio, monkeypatch):
    real_call = tools.call
    monkeypatch.setattr(tools, "call", lambda method, path, body=None, **kw: {
        "local_version": "5.3-admin.98", "readiness": {"ready": True, "blockers": []},
        "network": {"ready": True, "hosts": [], "message": "This computer reaches every service Studio uses."},
        "speech_model": {"ready": True, "state": "ready"}} if path == "/cr-status" else real_call(method, path, body, **kw))
    out = ok("setup_check", {})
    assert out["network"]["ready"] and out["network"]["blocked"] == [] and out["network"]["what_to_do"] == ""
    assert out["speech_model"] == {"ready": True, "state": "ready", "why": "", "fix": ""}


def test_setup_check_says_an_antivirus_blocks_studio_and_what_to_allow(studio, monkeypatch):
    sys.path.insert(0, str(ROOT / "local_runtime"))
    import cr_network
    network = cr_network.check({"kie": True, "youtube": True}, probe=lambda host: "", local="refused",
                               antivirus=["Norton"])
    real_call = tools.call
    monkeypatch.setattr(tools, "call", lambda method, path, body=None, **kw: {
        "local_version": "5.3-admin.99", "readiness": {"ready": False, "blockers": [network["message"]]},
        "network": network, "speech_model": {"ready": True, "state": "ready"}}
        if path == "/cr-status" else real_call(method, path, body, **kw))
    net = ok("setup_check", {})["network"]
    here = net["on_this_computer"]
    assert net["ready"] is False and here["suspected"] and here["antivirus"] == "Norton"
    assert here["allow_programs"][0] == sys.executable and "127.0.0.1:7860" in net["what_to_do"]
    assert "Never suggest switching it off" in net["what_to_do"]


def test_a_medium_studio_cant_make_is_read_to_the_creator_and_accepted_by_them(studio, monkeypatch):
    """The look's 'animation' question (a channel that animates its drawn characters) reaches Claude, and a medium Studio
    can't make yet is said before the study starts, never accepted silently."""
    import cr_style_map
    look_id = "lk_" + "e" * 24
    gap = {"engine": "pictures", "why": "drawn as pictures instead", "medium_gap": cr_style_map.ANIMATION_GAP}
    question = {"id": "animation", "ask": "This channel animates its characters…",
                "options": list(cr_style_map.ANIMATIONS)}

    def fake(method, path, body=None, **kw):
        studio.calls.append((method, path, body))
        if path == "/cr-style-clone/look/" + look_id:
            return {"state": "done", "percent": 100, "message": "Here's what Studio saw.",
                    "mapping": {"questions": [question], "base": "stories", "base_why": "drawn", "route": gap}}
        if path.endswith("/plan"):
            told = (body.get("answers") or {}).get("animation") == cr_style_map.ANIMATED_PICTURES
            return {"mapping": {"base": "stories", "route": {"engine": "pictures", "why": "you chose pictures"}
                                if told else gap}}
        if path == "/cr-style-clone/start":
            return {"operation_id": "op_" + "f" * 24, "state": "running"}
        return studio(method, path, body, **kw)
    monkeypatch.setattr(tools, "call", fake)
    tools.STATE.mkdir(parents=True, exist_ok=True)
    (tools.STATE / "looks.json").write_text(json.dumps({look_id: {"preview_id": "pv_" + "c" * 64,
                                                                  "reference_ids": ["a1aaaaaaaaa"], "at": 1}}))
    shown = ok("progress", {"id": look_id})
    assert shown["questions"][0]["id"] == "animation" and shown["medium_gap"] == cr_style_map.ANIMATION_GAP
    assert shown["how_made"] == "drawn as pictures instead" and "medium_gap" in shown["next"]
    args = {"look_id": look_id, "style_name": "Stick fights", "example_title": "Gojo vs Sukuna",
            "answers": {"animation": cr_style_map.ANIMATED_CODE}}
    held = ok("clone_style", args)
    assert held["started"] is False and held["medium_gap"] == cr_style_map.ANIMATION_GAP
    assert "Never accept it for them" in held["next"] and not studio.posted("/cr-style-clone/start")
    assert ok("clone_style", {**args, "accept_medium": True})["started"] is True
    assert studio.posted("/cr-style-clone/start")[0]["accept_medium"] is True
    chose = ok("clone_style", {**args, "answers": {"animation": cr_style_map.ANIMATED_PICTURES}})
    assert chose["started"] is True and studio.posted("/cr-style-clone/start")[1]["accept_medium"] is False


def test_progress_on_an_animated_clone_says_the_animations_score_and_never_picks_pictures_for_them(studio, monkeypatch):
    real_call = tools.call
    op = "op_" + "f" * 24
    status = {"operation_id": op, "state": "done", "phase": "complete", "percent": 100, "style_ready": True,
              "message": "Your style is saved, but Claude's animation for this channel reached 5.2/10 ...",
              "renderer": {"state": "below_bar", "overall": 5.2, "bar": 6.5, "version": 1, "offer_pictures": True,
                           "say": "Your style is saved, but Claude's animation for this channel reached 5.2/10."}}
    monkeypatch.setattr(tools, "call", lambda method, path, body=None, **kw: status
                        if path == f"/cr-style-clone/{op}/status" else real_call(method, path, body, **kw))
    out = ok("progress", {"id": op})
    assert out["animation"]["state"] == "below_bar" and out["animation"]["bar"] == 6.5
    assert out["animation"]["what_to_tell_them"].startswith("Your style is saved, but")
    assert "Use pictures instead" in out["next"] and "never choose for them" in out["next"]
    status["renderer"] = {"state": "passed", "overall": 6.7, "bar": 5.5, "say": "Claude wrote it: 6.7/10."}
    passed = ok("progress", {"id": op})
    assert passed["animation"]["what_to_tell_them"] == "Claude wrote it: 6.7/10." and "list_styles" in passed["next"]


SIMILAR = [{"id": "cr_clone_abc", "name": "My Style", "closeness": 93, "why": "both are mostly 3D scenes",
            "kind": "video", "mine": True, "recommended": False, "same_references": False,
            "preview_url": "/cr-workspace/preview/recipe/cr_clone_abc.jpg", "use_url": "/?cr-preset=cr_clone_abc",
            "sample": {"can": True, "pictures": 2, "seconds": 5.0, "cost_usd": 0.05}},
           {"id": "cr-shorts-3d", "name": "3D Explainer Shorts", "closeness": 90, "why": "both are mostly 3D scenes",
            "kind": "short", "mine": False, "recommended": False, "same_references": False, "preview_url": "",
            "use_url": "/?cr-preset=cr-shorts-3d", "sample": {"can": False, "why_not": "a whole Short"}}]


@pytest.fixture
def more(studio, monkeypatch):
    """The fake Studio with the look's suggestions, the style editor and the sample routes."""
    edits = []

    def call(method, path, body=None, **kw):
        if path == "/cr-style-clone/look/lk_" + "d" * 24:
            return {"look_id": "lk_" + "d" * 24, "state": "done", "shorts": True, "similar": SIMILAR,
                    "mapping": {"base": "3d", "questions": [], "route": {}}}
        if path == "/cr-style-clone/style/cr_clone_abc":
            edits.append((method, body))
            return {"style": "cr_clone_abc", "name": (body or {}).get("changes", {}).get("name", "My Style"),
                    "features": {"offered": [{"key": "ai_images", "label": "Generated pictures"}], "on": ["ai_images"]},
                    "study": {"title": "What if the sun went out", "brief": "Light", "base_style": "stories",
                              "reference_urls": ["https://www.youtube.com/@ifology/videos"]},
                    "saved": method == "POST"}
        if path == "/cr-style-clone/sample":
            studio.calls.append((method, path, body))
            return {"sample_id": "sm_" + "e" * 24, "state": "running", "cost_usd": 0.05, "message": "Drawing"}
        if path == "/cr-style-clone/sample/sm_" + "e" * 24 + "/status":
            return {"sample_id": "sm_" + "e" * 24, "state": "done", "video": "/studio/output/sample.mp4",
                    "video_url": "/cr-style-clone/sample/sm_" + "e" * 24 + "/video.mp4", "cost_usd": 0.05}
        return studio(method, path, body, **kw)
    monkeypatch.setattr(tools, "call", call)
    return SimpleNamespace(edits=edits)


def test_a_finished_look_suggests_the_closest_styles_and_a_paid_try_first_without_blocking_the_study(studio, more):
    out = ok("progress", {"id": "lk_" + "d" * 24})
    assert out["shorts"] is True and [s["id"] for s in out["similar_styles"]] == ["cr_clone_abc", "cr-shorts-3d"]
    assert out["similar_styles"][0]["try_first"] == {"tool": "style_sample", "cost_usd": 0.05, "seconds": 5.0}
    assert out["similar_styles"][1]["try_first"] == {"not_offered": "a whole Short"}
    assert out["similar_styles"][0]["picture_url"].startswith("http://localhost:7860/cr-workspace/preview/")
    assert "suggestions only, never a reason not to study" in out["next"]
    assert "regular landscape (16:9) videos" in out["shorts_note"], "a Shorts study says what its style makes"


def test_clone_style_takes_a_bounded_study_budget_and_says_what_a_shorts_study_makes(studio, more, monkeypatch):
    look_id = "lk_" + "d" * 24
    looked = tools.call

    def call(method, path, body=None, **kw):
        if path.endswith("/plan"):
            return {"mapping": {"base": "3d", "route": {}}}
        if path == "/cr-style-clone/start":
            studio.calls.append((method, path, body))
            return {"operation_id": "op_" + "f" * 24, "state": "running"}
        return looked(method, path, body, **kw)
    monkeypatch.setattr(tools, "call", call)
    tools.STATE.mkdir(parents=True, exist_ok=True)
    (tools.STATE / "looks.json").write_text(json.dumps({look_id: {"preview_id": "pv_" + "c" * 64,
                                                                  "reference_ids": ["a1aaaaaaaaa"], "at": 1}}))
    args = {"look_id": look_id, "style_name": "Beasts", "example_title": "Every city has a beast"}
    for wrong in (14, 31, "25", True):
        assert tools.run_tool("clone_style", {**args, "budget_minutes": wrong})["isError"]
    assert not studio.posted("/cr-style-clone/start")
    started = ok("clone_style", {**args, "budget_minutes": 30})
    assert studio.posted("/cr-style-clone/start")[0]["budget_minutes"] == 30 and "16:9" in started["shorts_note"]
    ok("clone_style", args)
    assert studio.posted("/cr-style-clone/start")[1]["budget_minutes"] == 22, "the page's default"


def test_style_sample_is_a_paid_tool_that_says_its_cost_and_brings_back_the_video(studio, more):
    assert "style_sample" in ccc.PAID_TOOLS and "edit_style" in ccc.FREE_TOOLS
    started = ok("style_sample", {"style": "My Style", "topic": "How bees build homes"})
    assert started["id"] == "sm_" + "e" * 24 and started["cost_usd"] == 0.05
    assert studio.posted("/cr-style-clone/sample") == [{"style": "cr_clone_abc", "topic": "How bees build homes",
                                                        "line": "", "acknowledge_costs": True}]
    done = ok("progress", {"id": "sm_" + "e" * 24})
    assert done["file"] == "/studio/output/sample.mp4" and done["watch_url"].endswith("/video.mp4")
    assert tools.run_tool("style_sample", {"style": "My Style"})["isError"], "a topic first"


def test_edit_style_shows_changes_and_says_how_to_study_it_again(studio, more):
    shown = ok("edit_style", {"style": "My Style"})
    assert shown["saved"] is False and more.edits[-1] == ("GET", None)
    assert shown["study_again"]["example_title"] == "What if the sun went out" and "clone_style_look" in shown["next"]
    saved = ok("edit_style", {"style": "cr_clone_abc", "name": "Bees", "script_notes": ["Be warm."]})
    assert saved["saved"] and saved["name"] == "Bees"
    assert more.edits[-1] == ("POST", {"changes": {"name": "Bees", "script_notes": ["Be warm."]}})
    assert tools.run_tool("edit_style", {"style": "documentary"})["isError"], "a recipe Studio ships isn't edited here"
