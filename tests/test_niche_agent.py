"""The niche agent (/niche-agent): the outlier maths, the teaching, YouTube reads (faked, no network), saved ideas."""
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "local_runtime"))
import cr_niche_agent as agent  # noqa: E402

TODAY = dt.date(2026, 10, 29)
LONG = agent.thresholds("")["long"]


def ts(day: str) -> int:
    return int(dt.datetime.fromisoformat(day).replace(tzinfo=dt.timezone.utc).timestamp())


def entry(i, views, day, seconds=540, title=None):
    return {"id": f"vid{i:08d}", "title": title or f"City {i} - 3D Timelapse", "view_count": views,
            "duration": seconds, "timestamp": ts(day), "channel": "CityEons", "channel_id": "UC" + "a" * 22}


def rows(*items):
    return [agent.entry_row(e) for e in items]


# ── the maths ─────────────────────────────────────────────────────────────────────────────────────────────────

def cityeons():
    # newest first: the CityEons shape from the 16K video (4 bangers, a brand-new upload still growing)
    return rows(entry(5, 23_000, "2026-10-24"), entry(4, 1_600_000, "2026-10-17"), entry(3, 921_000, "2026-10-15"),
                entry(2, 348_000, "2026-10-08"), entry(1, 668_000, "2026-10-05"))


def test_a_new_consistent_channel_is_a_proof_channel():
    n = agent.channel_numbers(cityeons(), 6_920, today=TODAY, exact={"vid00000001": "2026-10-05"}, th=LONG)
    assert n["passed"], n["failed"]
    assert n["first_upload"] == "2026-10-05" and n["first_upload_exact"] and n["channel_age_days"] == 24
    assert n["uploads_30d"] == 5
    assert n["banger_flop"] == "4:0" and n["too_new_to_judge"] == 1      # the 5-day-old upload isn't a flop
    assert n["best_video"]["views"] == 1_600_000 and n["best_video"]["views_per_sub"] == pytest.approx(231.2, 0.01)
    assert "started 24 days ago" in n["bonuses"] and any("hero outlier" in b for b in n["bonuses"])
    line = agent.one_line("CityEons", n)
    assert "first upload 2026-10-05 (24 d)" in line and "banger:flop 4:0" in line


def test_a_one_hit_fails():
    items = rows(entry(9, 900, "2026-10-25"), entry(8, 1_200, "2026-10-20"), entry(7, 100_000, "2026-10-12"),
                 entry(6, 800, "2026-10-08"), entry(5, 1_100, "2026-10-04"))
    n = agent.channel_numbers(items, 425, today=TODAY, th=LONG)
    assert not n["passed"] and n["one_hit"]
    assert any(f.startswith("not a one-hit") for f in n["failed"])
    assert any(f.startswith("banger:flop") for f in n["failed"])


def test_an_old_big_channel_fails_on_age_and_size():
    items = rows(*[entry(i, 60_000, (TODAY - dt.timedelta(days=4 * i + 8)).isoformat()) for i in range(40)])
    n = agent.channel_numbers(items, 3_200_000, today=TODAY, th=LONG, capped=True)
    assert n["first_upload"] is None and n["channel_age_days"] is None
    failed = " ".join(n["failed"])
    assert "first upload" in failed and "older: more than 40 uploads" in failed and "subscribers" in failed


def test_flops_are_measured_against_the_channels_own_median():
    hot = rows(entry(6, 40_000, "2026-10-20"), entry(5, 700_000, "2026-10-18"), entry(4, 800_000, "2026-10-15"),
               entry(3, 650_000, "2026-10-12"), entry(2, 900_000, "2026-10-08"), entry(1, 750_000, "2026-10-04"))
    n = agent.channel_numbers(hot, 9_000, today=TODAY, th=LONG)
    assert n["flops"] == 1                                      # 40K is under 10% of a 750K median
    assert n["banger_flop"] == "5:1" and not n["passed"]       # 5:1 is under the 7:1 bar


def test_videos_without_views_and_bad_entries_are_skipped():
    items = [agent.entry_row({"id": "short", "view_count": 5}), agent.entry_row({"id": "x" * 11, "live_status": "is_upcoming"}),
             agent.entry_row({**entry(1, None, "2026-10-20")})]
    assert items[:2] == [None, None] and items[2]["views"] is None
    n = agent.channel_numbers([items[2]], 10, today=TODAY, th=LONG)
    assert not n["passed"] and n["failed"] == ["no public uploads on this tab"]


# ── the teaching ──────────────────────────────────────────────────────────────────────────────────────────────

def test_the_rules_numbers_are_read_from_rules_md():
    text = (ROOT / "niche_agent" / "teaching" / "rules.md").read_text(encoding="utf-8")
    th = agent.thresholds(text)
    assert th["long"]["min_banger_flop_ratio"] == 7 and th["long"]["banger_views"] == 50_000
    assert th["long"]["max_first_upload_days"] == 60 and th["shorts"]["confirmed"] is False
    # the file's numbers and the code's fallback agree, so a missing file changes nothing
    assert th == agent.DEFAULTS
    changed = agent.thresholds('```json\n{"long": {"min_banger_flop_ratio": 5, "nope": 1, "max_subs": "x"}}\n```')
    assert changed["long"]["min_banger_flop_ratio"] == 5 and changed["long"]["max_subs"] == 50_000
    assert "nope" not in changed["long"]


def test_every_seed_example_is_well_formed():
    lines = (ROOT / "niche_agent" / "teaching" / "examples.jsonl").read_text(encoding="utf-8").splitlines()
    examples = agent.parse_examples("\n".join(lines))
    assert len(examples) == len([l for l in lines if l.strip()]) >= 20
    for e in examples:
        assert e["kind"] in ("niche", "channel", "idea") and e["verdict"] in ("good", "bad")
        assert e["format"] in ("long", "shorts") and e["why"] and e["lesson"] and e["added"]
        assert e["link"] == "" or e["link"].startswith("https://")
    names = {e["name"] for e in examples}
    assert {"CityEons", "Through the Ages", "Map Mayhem"} <= names
    assert {e["verdict"] for e in examples} == {"good", "bad"}


def test_teaching_falls_back_to_the_saved_copy_then_the_shipped_one(tmp_path):
    (tmp_path / "niche_teaching").mkdir()
    (tmp_path / "niche_teaching" / "rules.md").write_text("# shipped", encoding="utf-8")
    (tmp_path / "niche_teaching" / "examples.jsonl").write_text('{"kind": "niche"}\n', encoding="utf-8")
    assert agent.local_teaching(tmp_path)["source"] == "bundled"
    agent._TEACHING_MEMO.update(at=0, data=None)
    fresh = agent.server_teaching(tmp_path, lambda path: {"rules": "# newest", "examples": [{"kind": "idea"}, 3]})
    assert fresh["rules"] == "# newest" and fresh["examples"] == [{"kind": "idea"}]
    assert agent.local_teaching(tmp_path)["rules"] == "# newest"            # kept for when the server is away
    agent._TEACHING_MEMO.update(at=0, data=None)

    def offline(path):
        raise RuntimeError("Could not reach ChannelRecipe.")
    assert agent.server_teaching(tmp_path, offline)["rules"] == "# newest"
    agent._TEACHING_MEMO.update(at=0, data=None)


# ── reading YouTube, faked ────────────────────────────────────────────────────────────────────────────────────

class FakeYouTube:
    """Answers yt-dlp commands from fixtures; counts the reads."""

    def __init__(self, tab=None, dates=None, search=None, stderr=""):
        self.tab, self.dates, self.search, self.stderr, self.commands = tab or {}, dates or {}, search or {}, stderr, []

    def __call__(self, command):
        self.commands.append(command)
        url = command[-1]
        if self.stderr:
            return SimpleNamespace(returncode=1, stdout="", stderr=self.stderr)
        if "/results?" in url:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"entries": self.search.get("entries", [])}), stderr="")
        if "watch?v=" in url:
            return SimpleNamespace(returncode=0, stdout=self.dates.get(url.rsplit("=", 1)[1], "NA") + "\n", stderr="")
        return SimpleNamespace(returncode=0, stdout=json.dumps(self.tab), stderr="")


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(agent, "PACE_SECONDS", 0)


def test_channel_stats_read_the_tab_once_and_exact_dates_for_the_first_and_best(tmp_path):
    tab = {"channel": "CityEons", "channel_id": "UC" + "a" * 22, "uploader_id": "@CityEons",
           "channel_follower_count": 6_920,
           "entries": [entry(5, 23_000, "2026-10-24"), entry(4, 1_600_000, "2026-10-19"),
                       entry(3, 921_000, "2026-10-15"), entry(2, 348_000, "2026-10-08"), entry(1, 668_000, "2026-10-08")]}
    yt = FakeYouTube(tab=tab, dates={"vid00000001": "20261005", "vid00000004": "20261017"})
    out = agent.channel_stats(["https://www.youtube.com/@CityEons"], home=tmp_path, today=TODAY, runner=yt)
    c = out["channels"][0]
    assert c["channel"] == "CityEons" and c["url"] == "https://www.youtube.com/@CityEons" and c["passed"]
    assert c["first_upload"] == "2026-10-05" and c["best_video"]["date"] == "2026-10-17" and c["best_video"]["date_exact"]
    assert len(yt.commands) == 3 and yt.commands[0][-1] == "https://www.youtube.com/@CityEons/videos"
    assert all(cmd[:2] == [sys.executable, str(agent.HOME / "cr_ytdlp.py")] for cmd in yt.commands)
    again = agent.channel_stats(["https://www.youtube.com/@CityEons"], home=tmp_path, today=TODAY, runner=yt)
    assert len(yt.commands) == 3 and again["channels"][0]["banger_flop"] == c["banger_flop"]     # cached


def test_youtubes_bot_check_stops_every_read_for_a_while(tmp_path):
    yt = FakeYouTube(stderr="ERROR: [youtube] x: Sign in to confirm you're not a bot.")
    with pytest.raises(agent.Wall):
        agent.run_ytdlp(["--flat-playlist", "https://www.youtube.com/@x/videos"], home=tmp_path, runner=yt)
    assert agent.walled(tmp_path)
    quiet = FakeYouTube()
    with pytest.raises(agent.Wall):
        agent.run_ytdlp(["x"], home=tmp_path, runner=quiet)
    assert quiet.commands == []                                  # never asked again during the pause
    out = agent.channel_stats(["@x"], home=tmp_path, runner=quiet)
    assert out["channels"] == [] and agent.WALL in out["notes"]


def test_search_keeps_long_videos_with_views_and_groups_channels(tmp_path):
    entries = [{**entry(1, 1_650_000, "2026-10-17"), "channel": "CityEons"},
               {**entry(2, 900_000, "2026-10-15"), "channel": "CityEons"},
               {**entry(3, 2_000_000, "2026-10-10", seconds=40), "channel": "Shorty"},
               {**entry(4, 12_000, "2026-10-10"), "channel": "Tiny"},
               {**entry(5, 500_000, "2026-10-12"), "channel": "Latos", "channel_id": "UC" + "b" * 22}]
    yt = FakeYouTube(search={"entries": entries})
    out = agent.search(["3d city timelapse", "evolution of london 3d"], home=tmp_path, today=TODAY, runner=yt)
    assert [v["views"] for v in out["videos"]] == [1_650_000, 900_000, 500_000]
    assert out["videos"][0]["queries"] == ["3d city timelapse", "evolution of london 3d"]
    assert out["channels"][0]["channel"] == "CityEons" and out["channels"][0]["videos"] == 2
    assert "sp=CAMSBAgEEAE%3D" in yt.commands[0][-1]               # this month, by views
    shorts = agent.search(["3d city timelapse"], fmt="shorts", home=tmp_path, today=TODAY, runner=yt)
    assert shorts["videos"] == [{**shorts["videos"][0], "channel": "Shorty"}] and shorts["min_views"] == 300_000


@pytest.mark.parametrize("given,base,video", [
    ("UC" + "c" * 22, "https://www.youtube.com/channel/UC" + "c" * 22, None),
    ("@CityEons", "https://www.youtube.com/@CityEons", None),
    ("https://www.youtube.com/@CityEons/videos", "https://www.youtube.com/@CityEons", None),
    ("youtube.com/channel/UC" + "d" * 22, "https://www.youtube.com/channel/UC" + "d" * 22, None),
    ("https://youtu.be/Xn1s4JvDKYY", None, "Xn1s4JvDKYY"),
    ("https://www.youtube.com/watch?v=Xn1s4JvDKYY&t=3", None, "Xn1s4JvDKYY"),
    ("https://www.youtube.com/shorts/Xn1s4JvDKYY", None, "Xn1s4JvDKYY"),
    ("City Eons", None, None),
])
def test_channel_links_in_every_shape(given, base, video):
    assert agent.channel_base(given) == (base, video)


# ── saved ideas ───────────────────────────────────────────────────────────────────────────────────────────────

IDEA = {"title": "Watch MANILA Grow for 450 Years - 3D Timelapse", "niche": "3D city growth timelapses",
        "minutes": "9", "style": "cr_clone_cityeons", "twist": "A city nobody has done yet.",
        "thumbnail": "Bright daylight skyline, 1571 → 2026 in big type", "risk": "Crowding fast.",
        "proof": {"video_url": "https://youtu.be/Xn1s4JvDKYY", "video_title": "NEW YORK - 3D Timelapse",
                  "views": "1,650,000", "channel": "CityEons", "subs": 6920, "views_per_sub": 231.2,
                  "channel_age_days": 24, "banger_flop": "4:0", "evil": "<script>"}}


def test_ideas_are_saved_once_listed_used_and_dismissed(tmp_path):
    saved = agent.save_ideas([IDEA, {**IDEA, "title": "  Watch manila grow for 450 years - 3d timelapse "}], home=tmp_path)
    assert saved["total"] == 1                                    # the same title twice is one idea
    rows_ = agent.list_ideas(home=tmp_path)
    idea = rows_[0]
    assert idea["minutes"] == 9 and idea["proof"]["views"] == 1_650_000 and "evil" not in idea["proof"]
    assert idea["status"] == "saved" and idea["source"] == "niche-agent"
    used = agent.set_idea_status(idea["id"], "used", home=tmp_path)
    assert used["status"] == "used" and agent.list_ideas("used", home=tmp_path)[0]["id"] == idea["id"]
    agent.save_ideas([IDEA], home=tmp_path)
    assert agent.list_ideas(home=tmp_path)[0]["status"] == "used"          # saving again keeps it used
    with pytest.raises(LookupError):
        agent.set_idea_status("id_" + "0" * 12, "used", home=tmp_path)
    with pytest.raises(ValueError):
        agent.save_ideas([{"niche": "no title"}], home=tmp_path)
    text = agent.extra_instructions(idea)
    assert "Angle: A city nobody has done yet." in text and "1.6M views" in text and "https://youtu.be/Xn1s4JvDKYY" in text
    raw = (tmp_path / "ideas" / "ideas.json").read_bytes()
    assert b"\r\n" not in raw and "→".encode() in raw                  # UTF-8, same bytes on every computer


def test_the_studio_tools_save_and_use_ideas_without_spending(tmp_path, monkeypatch):
    import cr_studio_tools as tools
    import cr_claude_code as ccc
    monkeypatch.setattr(tools, "HOME", tmp_path)
    monkeypatch.setattr(tools, "same_studio", lambda: None)
    opened = []
    monkeypatch.setattr(tools, "open_url", lambda url: opened.append(url) or True)
    out = tools.run_tool("save_ideas", {"ideas": [IDEA]})["structuredContent"]
    ident = out["saved"][0]["id"]
    listed = tools.run_tool("list_ideas", {})["structuredContent"]
    assert listed["total"] == 1
    used = tools.run_tool("use_idea", {"id": ident, "open_browser": True})["structuredContent"]
    assert used["create_url"] == f"http://localhost:7860/?idea={ident}" and opened == [used["create_url"]]
    assert tools.run_tool("use_idea", {"id": "id_" + "1" * 12})["isError"]
    for name in ("niche_teaching", "niche_breakouts", "yt_search", "yt_channel_stats", "yt_look", "save_ideas",
                 "list_ideas", "use_idea"):
        assert name in tools.TOOLS and name in ccc.FREE_TOOLS and name not in ccc.PAID_TOOLS


def test_the_teaching_tool_works_with_studio_closed(tmp_path, monkeypatch):
    import cr_studio_tools as tools

    def closed(*args, **kwargs):
        raise tools.Closed(tools.CLOSED)
    monkeypatch.setattr(tools, "call", closed)
    out = tools.run_tool("niche_teaching", {"kind": "niche"})["structuredContent"]
    assert out["rules"].startswith("# Niche agent") and out["examples"]
    assert {e["kind"] for e in out["examples"]} == {"niche"}
    assert out["thresholds"]["long"]["banger_views"] == 50_000
    refused = tools.run_tool("niche_breakouts", {"q": "city"})
    assert refused["isError"] and "start Studio" in refused["structuredContent"]["error"]


def test_breakout_queries_pass_only_known_short_values():
    q = agent.breakout_query({"q": "3d city", "max_subs": "50000", "evil": "x", "limit": "25" * 10})
    assert q == "q=3d+city&max_subs=50000&limit=252"


# ── shipping ──────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_release_ships_the_skill_and_the_teaching():
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_frontier_admin as builder
    shipped = {relative for _, relative in builder.runtime_resources(ROOT)}
    assert {".claude/skills/niche-agent/SKILL.md", "niche_teaching/rules.md", "niche_teaching/examples.jsonl",
            "cr_niche_agent.py"} <= shipped
    skill = (ROOT / "local_runtime" / "claude_skills" / "niche-agent" / "SKILL.md").read_text(encoding="utf-8")
    head = skill.split("---", 2)[1]
    assert "name: niche-agent" in head and "disable-model-invocation" not in head
    for tool in ("niche_teaching", "niche_breakouts", "yt_search", "yt_channel_stats", "yt_look", "save_ideas",
                 "use_idea"):
        assert tool in skill


def test_create_has_saved_ideas():
    html = (ROOT / "local_runtime" / "cr_workspace.html").read_text(encoding="utf-8")
    js = (ROOT / "local_runtime" / "cr_workspace.js").read_text(encoding="utf-8")
    assert 'id="crw-ideas"' in html and 'id="crw-ideas-list"' in html
    assert "api('/cr-ideas')" in js and "get('idea')" in js and "function useIdea" in js


def test_studio_routes_list_and_mark_ideas(tmp_path):
    flask = pytest.importorskip("flask")
    app = flask.Flask("t")
    agent.install(SimpleNamespace(app=app), "tok", tmp_path)
    agent.save_ideas([IDEA], home=tmp_path)
    client = app.test_client()
    data = client.get("/cr-ideas", base_url="http://127.0.0.1:7860").get_json()
    ident = data["ideas"][0]["id"]
    assert data["ideas"][0]["extra"].startswith("Angle:")
    r = client.post(f"/cr-ideas/{ident}", json={"status": "dismissed"}, base_url="http://127.0.0.1:7860")
    assert r.status_code == 200
    assert client.get("/cr-ideas", base_url="http://127.0.0.1:7860").get_json()["ideas"] == []
    assert client.get("/cr-ideas", base_url="http://evil.example").status_code == 403
    assert client.post("/cr-ideas/id_bad", json={"status": "used"}, base_url="http://127.0.0.1:7860").status_code == 400


def test_thumbnails_use_their_own_certificate_context(tmp_path, monkeypatch):
    # urllib's shared opener keeps the context of its first use (a python.org Python has no certificates): each
    # fetch builds its own opener with this computer's and certifi's certificates (the 2026-10-29 live run failed)
    seen = []

    class Opener:
        def open(self, request, timeout):
            seen.append(request.full_url)
            return __import__("io").BytesIO(b"jpeg")
    monkeypatch.setattr(agent.urllib.request, "build_opener", lambda *handlers: seen.append(handlers) or Opener())
    assert agent._fetch("https://i.ytimg.com/vi/Xn1s4JvDKYY/mqdefault.jpg") == b"jpeg"
    assert isinstance(seen[0][0], agent.urllib.request.HTTPSHandler) and seen[1].endswith("/mqdefault.jpg")
    out = agent.look_sheet("", ["Xn1s4JvDKYY", "bad id"], home=tmp_path, fetch=lambda url: b"not a picture")
    assert out["pictures"] and out["pictures"][0].endswith("Xn1s4JvDKYY.jpg")

    def broken(url):
        raise OSError("certificate verify failed")
    with pytest.raises(agent.YouTubeError, match="certificate verify failed"):
        agent.look_sheet("", ["Ktjk8s3xyGY"], home=tmp_path, fetch=broken)
