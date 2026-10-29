"""Studio Cloud on the Studio's side (local_runtime/cr_cloud.py): the engine's farm keeps working unchanged, with
RunPod, its bucket and its Anthropic key swapped for ChannelRecipe's machines, signed storage and the creator's own
Claude Code."""
import base64
import importlib
import json
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))

FARM = {
    "__init__.py": "",
    "s3.py": "class S3Error(RuntimeError):\n    pass\n",
    "runpod.py": textwrap.dedent("""
        class RunPod:
            def __init__(self, api_key, session=None):
                self.api_key = api_key
        def price_of(pod):
            return float(pod.get("costPerHr") or 0)
    """),
    "config.py": textwrap.dedent("""
        import os
        class Config:
            def __init__(self):
                self.runpod_key = os.environ.get("RUNPOD_API_KEY", "")
                self.bucket_endpoint = self.bucket = self.bucket_key_id = self.bucket_secret = ""
                self.gpu, self.cloud, self.max_pods, self.budget_usd = "A4000", "COMMUNITY", 5, 10.0
            def checklist(self):
                return [{"key": "runpod", "ok": False}, {"key": "render", "label": "A voice", "ok": True}]
            def missing_for_mac(self):
                return ["RUNPOD_API_KEY"]
        def bucket_from(cfg):
            return "their own bucket"
    """),
    "farm.py": "from farm.config import Config, bucket_from\n"
               "def _skip(rel):\n    return rel.startswith('output/')\n"
               "def bundle(bucket, quiet=False, sink=None):\n    return 'code/channelrecipe-full.zip'\n",
    "runner.py": textwrap.dedent("""
        def main():
            from farm.config import bucket_from
            global USED
            USED = bucket_from(None)
            return 0
    """),
}


@pytest.fixture
def studio(tmp_path, monkeypatch):
    pkg = tmp_path / "farm"
    pkg.mkdir()
    for name, src in FARM.items():
        (pkg / name).write_text(src)
    (tmp_path / ".cr-connection.json").write_text(json.dumps({"key": "cr_mcp_abc", "url": "https://cr.test"}))
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in [m for m in sys.modules if m == "farm" or m.startswith("farm.")]:
        monkeypatch.delitem(sys.modules, name)
    import cr_cloud
    cr_cloud = importlib.reload(cr_cloud)
    monkeypatch.setattr(cr_cloud, "HOME", tmp_path)
    return SimpleNamespace(home=tmp_path, cloud=cr_cloud)


class Http:
    """Plays channelrecipe.com and the storage it signs."""
    def __init__(self, store=None, jobs=None):
        self.store = store if store is not None else {}
        self.sent = []

    def _r(self, status=200, body=None, content=b""):
        return SimpleNamespace(status_code=status, ok=status < 400, json=lambda: body, content=content, text="",
                               iter_content=lambda n: [content])

    def post(self, url, json=None, headers=None, timeout=None):
        self.sent.append(("POST", url, json, headers))
        op, key = json["op"], json["key"]
        if op == "presign":
            return self._r(body={"url": f"https://signed/{json['method']}/{key}"})
        if op == "exists":
            return self._r(body={"exists": key in self.store})
        if op == "list":
            return self._r(body={"items": [{"key": k, "size": len(v), "modified": 0} for k, v in self.store.items()
                                           if k.startswith(key)]})
        if op == "delete":
            self.store.pop(key, None)
            return self._r(body={"ok": True})

    def put(self, url, data=None, headers=None, timeout=None):
        key = url.split("/PUT/", 1)[1]
        self.store[key] = data if isinstance(data, bytes) else data.read()
        return self._r()

    def get(self, url, stream=False, timeout=None):
        key = url.split("/GET/", 1)[1]
        return self._r(content=self.store[key]) if key in self.store else self._r(404)


def test_the_bucket_is_signed_by_the_server_and_speaks_the_farms_language(studio):
    http = Http()
    b = studio.cloud.BrokerBucket("https://cr.test/api/studio/cloud", "Bearer cr_mcp_abc", http)
    b.put_bytes("status/a.json", b'{"state":"queued"}', "application/json")
    assert b.get_bytes("status/a.json") == b'{"state":"queued"}' and b.exists("status/a.json")
    assert http.sent[0][1] == "https://cr.test/api/studio/cloud/storage"
    assert http.sent[0][3] == {"Authorization": "Bearer cr_mcp_abc"}
    assert b.list("status/")[0]["modified"] == "1970-01-01T00:00:00.000Z", "S3's own date format"
    from farm.s3 import S3Error
    with pytest.raises(S3Error):
        b.get_bytes("status/missing.json"), "a missing object is the farm's own error, which it handles"


def test_the_farm_switches_to_studio_cloud_only_when_the_account_has_it(studio, monkeypatch):
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    status = {"available": False, "balance_usd": 0, "why": "not yet"}
    monkeypatch.setattr(studio.cloud, "status", lambda force=False, home=None: status)
    assert studio.cloud.install(studio.home)
    import farm.config as fc
    import farm.farm as ff
    import farm.runpod as frp
    cfg = fc.Config()
    assert not cfg.studio_cloud and fc.bucket_from(cfg) == "their own bucket", "without it, the farm is untouched"

    status.update(available=True, balance_usd=4.5, why="")
    cfg = fc.Config()
    assert cfg.studio_cloud and cfg.runpod_key == "studio-cloud" and cfg.missing_for_mac() == []
    assert [c["key"] for c in cfg.checklist()] == ["cloud", "balance", "render"]
    assert isinstance(ff.bucket_from(cfg), studio.cloud.BrokerBucket), "farm.farm's own import is switched too"
    assert isinstance(frp.RunPod(cfg.runpod_key), studio.cloud.CloudPods)
    assert ff.bundle("their own bucket") == "code/channelrecipe-full.zip", "their own bucket keeps the full bundle"
    assert frp.RunPod("rp_theirs").api_key == "rp_theirs", "a creator's own RunPod key keeps RunPod"

    status.update(balance_usd=0.1)
    assert fc.Config().missing_for_mac() == ["Studio Cloud balance ($0.10)"], "an empty balance asks for a top-up"


def test_starting_a_machine_asks_channelrecipe_with_the_jobs_length(studio, monkeypatch):
    store = {"jobs/a.json": json.dumps({"minutes": 12}).encode()}
    monkeypatch.setattr(studio.cloud.requests, "Session", lambda: Http(store))
    calls = []

    class Api:
        def request(self, method, url, json=None, headers=None, timeout=None):
            calls.append((method, url, json))
            return SimpleNamespace(status_code=200, json=lambda: {"id": "cj_1", "slug": "a", "status": "running",
                                                                   "price_per_hr": 1.2})
    monkeypatch.setattr(studio.cloud.Relay, "ensure", classmethod(lambda cls, home=None: calls.append("relay")))
    monkeypatch.setattr(studio.cloud, "status", lambda force=False, home=None: {"available": True})
    pods = studio.cloud.CloudPods(home=studio.home, session=Api())
    pod = pods.create(name="channelrecipe-a", env={"FARM_JOB_KEY": "jobs/a.json", "FARM_SLUG": "a"})
    method, url, body = calls[0]
    assert (method, url) == ("POST", "https://cr.test/api/studio/cloud/jobs") and body["minutes"] == 12.0
    assert body["machine"] == "cpu" and "relay" in calls, "the Studio starts answering the machine's questions"
    assert pod["costPerHr"] == 1.2 and pod["name"].startswith("channelrecipe")


def test_a_relayed_question_becomes_one_prompt_with_its_pictures(studio, tmp_path):
    png = base64.b64encode(b"\x89PNG fake").decode()
    prompt, images = studio.cloud._prompt_parts({
        "system": [{"type": "text", "text": "You choose footage."}],
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png}},
            {"type": "text", "text": "Which frame is sharper?"}]}]}, tmp_path)
    assert prompt == "You choose footage.\n\nWhich frame is sharper?"
    assert len(images) == 1 and Path(images[0]).read_bytes() == b"\x89PNG fake"


def test_answers_go_through_this_computers_claude_code(studio, monkeypatch):
    seen = []
    fake = SimpleNamespace(SCRIPT_MODEL="claude-opus",
                           claude=lambda p, m, n, provider=None: seen.append((p, m, n, provider)) or "text answer",
                           _claude_code_vision=lambda p, imgs: seen.append(("vision", p, len(imgs))) or "seen it")
    monkeypatch.setitem(sys.modules, "make_video", fake)
    assert studio.cloud.answer({"model": "claude-sonnet-5", "max_tokens": 900,
                                "messages": [{"role": "user", "content": "Title?"}]}) == "text answer"
    assert seen[-1] == ("Title?", "claude-sonnet-5", 900, "claudecode")
    png = base64.b64encode(b"x").decode()
    assert studio.cloud.answer({"messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": png}},
        {"type": "text", "text": "Describe"}]}]}) == "seen it"


def test_the_machine_runs_the_farms_runner_with_the_signed_broker(studio, monkeypatch):
    monkeypatch.setenv("FARM_BROKER_URL", "https://cr.test/api/studio/cloud/pod")
    monkeypatch.setenv("FARM_JOB_TOKEN", "crj_job")
    assert studio.cloud.pod() == 0
    from farm import runner
    assert isinstance(runner.USED, studio.cloud.BrokerBucket)
    assert runner.USED.base == "https://cr.test/api/studio/cloud/pod"
    assert runner.USED.headers == {"Authorization": "Bearer crj_job"}
    monkeypatch.delenv("FARM_JOB_TOKEN")
    assert studio.cloud.pod() == 2, "not a Studio Cloud machine without its token"


def test_a_network_failure_is_a_storage_error_the_farm_survives(studio):
    import requests

    class Down(Http):
        def put(self, *a, **kw):
            raise requests.ConnectionError("no route")
    b = studio.cloud.BrokerBucket("https://cr.test/api/studio/cloud", "Bearer x", Down())
    from farm.s3 import S3Error
    with pytest.raises(S3Error):
        b.put_bytes("status/a.json", b"{}", "application/json")


def test_only_what_the_cloud_can_make_goes_there(studio):
    b = studio.cloud.blocked
    assert b({"style": "documentary", "features": {"youtube": True}}) == ""
    assert "Shorts" in b({"style": "cr-shorts-ranking"}) and "Shorts" in b({"style": "cr-shorts-3d"})
    assert b({"style": "cr_clone_abc"}) == "" and b({"style": "cr-stick-stories"}) == "", "clones and our recipes go"
    assert "panels" in b({"style": "cr-manga-recaps"}) and "panels" in b({"style": "documentary", "panels": "set1"})
    assert b({"style": "3d", "features": {"threed": True}}) == "", "3D renders on the GPU machine"
    assert "FOLIO" in b({"style": "folio"})
    assert "recording" in b({"style": "documentary", "byo_audio": "uploads/me.wav"})
    assert "Fast mode" in b({"style": "documentary", "render_backend": "manimgx"})
    p = studio.cloud.farm_payload({"title": "T", "style": "documentary", "minutes": 8, "steps": ["video", "thumbnail"],
                                   "byo_audio": "", "desc": True, "features": {"youtube": True}})
    assert p == {"title": "T", "style": "documentary", "minutes": 8, "features": {"youtube": True}, "thumb": True,
                 "cr_run_spec": {"style": "documentary", "minutes": 8, "features": {"youtube": True},
                                 "meta": {"chapters": 0, "sources": 0}}}


def test_starting_prepares_here_then_starts_the_machine_through_the_studios_routes(studio):
    calls = []

    class Client:
        def __init__(self, replies):
            self.replies = replies

        def post(self, path, json=None, base_url=None, headers=None):
            calls.append((path, json, base_url, headers))
            status, body = self.replies[path]
            return SimpleNamespace(status_code=status, get_json=lambda silent=True: body)

    ok = Client({"/api/farm/queue": (200, {"queued": [{"slug": "t"}]}), "/api/farm/run": (200, {"started": 1})})
    studio.cloud.start(ok, "tok", {"title": "T", "style": "documentary", "steps": []})
    assert [c[0] for c in calls] == ["/api/farm/queue", "/api/farm/run"]
    assert calls[0][2] == "http://127.0.0.1:7860" and calls[0][3] == {"X-CR-Local-Token": "tok"}, \
        "through the studio's own guard, as the creator's page would"
    assert studio.cloud.TASK["state"] == "rendering"

    calls.clear()
    bad = Client({"/api/farm/queue": (400, {"error": "3D videos render on this computer"})})
    studio.cloud.start(bad, "tok", {"title": "T", "style": "documentary"})
    assert [c[0] for c in calls] == ["/api/farm/queue"], "nothing is started when preparing fails"
    assert studio.cloud.TASK["state"] == "failed" and "3D videos" in studio.cloud.TASK["error"]


def test_a_step_that_exits_is_reported_not_left_preparing(studio):
    class Client:
        def post(self, *a, **kw):
            raise SystemExit("Claude API error: timeout")
    studio.cloud.start(Client(), "tok", {"title": "T", "style": "documentary"})
    assert studio.cloud.TASK["state"] == "failed" and "timeout" in studio.cloud.TASK["error"]


ENGINE_CALLS = [   # the shapes of the engine's own yt-dlp runs (youtube.py, footjudge.py, soundbites.py, make_video.py)
    ["--js-runtimes", "node", "--flat-playlist", "--no-warnings", "-j", "ytsearch8:eagle eyesight close up"],
    ["--js-runtimes", "node", "-q", "--no-warnings", "-f", "bv*[height<=360]+ba/b[height<=360]/worst",
     "--download-sections", "*12.0-40.0", "--force-keyframes-at-cuts", "--merge-output-format", "mp4",
     "-o", "@@ROOT@@/output/documentary/_work/x/youtube/low/abc.%(ext)s", "https://www.youtube.com/watch?v=abcdefghijk"],
    ["-q", "--no-warnings", "--skip-download", "--write-info-json", "-o", "@@ROOT@@/output/w/info/abc",
     "https://www.youtube.com/watch?v=abcdefghijk"],
    ["-q", "--no-warnings", "-f", "bv*[height<=1080]/b", "--download-sections", "*1.00-5.00", "--force-keyframes-at-cuts",
     "-o", "@@ROOT@@/output/w/sections/abc_1.0_5.0.%(ext)s", "--load-info-json", "@@ROOT@@/output/w/info/abc.info.json"],
    ["-q", "--no-warnings", "--skip-download", "--print", "%(title)s\t%(channel)s", "https://youtu.be/abcdefghijk"],
    ["--flat-playlist", "-J", "--playlist-end", "120", "--no-warnings", "https://www.youtube.com/@somechannel/videos"],
]


def test_the_creators_studio_runs_only_the_downloads_the_engine_asks_for(studio):
    for argv in ENGINE_CALLS:
        assert studio.cloud._safe_ytdlp_args(argv) == argv
    for bad in (["--exec", "rm -rf ~", "https://www.youtube.com/watch?v=abcdefghijk"],
                ["-o", "/Users/me/.ssh/x", "https://www.youtube.com/watch?v=abcdefghijk"],
                ["-o", "@@ROOT@@/../../escape", "https://www.youtube.com/watch?v=abcdefghijk"],
                ["--cookies-from-browser", "chrome", "https://www.youtube.com/watch?v=abcdefghijk"],
                ["--batch-file", "@@ROOT@@/list.txt"], ["https://evil.example/x"],
                ["--js-runtimes", "sh", "ytsearch1:x"], ["--extractor-args", "generic:impersonate", "ytsearch1:x"]):
        with pytest.raises(ValueError):
            studio.cloud._safe_ytdlp_args(bad)
    out = studio.cloud.run_ytdlp({"argv": ["--exec", "touch /tmp/x", "ytsearch1:x"], "prefix": "relay/0123456789abcdef"})
    assert out["rc"] == 2 and "refused" in out["stderr"] and out["files"] == []


def test_a_youtube_download_runs_on_this_computer_and_comes_back(studio, monkeypatch):
    store = {"relay/0123456789abcdef/in/output/w/info/abc.info.json": b'{"extractor": "youtube"}'}
    monkeypatch.setattr(studio.cloud.requests, "Session", lambda: Http(store))
    ran = []

    def fake_run(cmd, **kw):
        ran.append(cmd)
        out = next(Path(cmd[i + 1]) for i, a in enumerate(cmd) if a == "-o")
        Path(str(out).replace(".%(ext)s", ".mp4")).write_bytes(b"clip")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")
    monkeypatch.setattr(studio.cloud.subprocess, "run", fake_run)
    monkeypatch.setitem(sys.modules, "youtube", SimpleNamespace(ytdlp=lambda: ["/usr/local/bin/yt-dlp", "--cookies", "c.txt"]))
    out = studio.cloud.run_ytdlp({"argv": ENGINE_CALLS[3], "prefix": "relay/0123456789abcdef",
                                  "inputs": ["output/w/info/abc.info.json"]}, studio.home)
    assert out["rc"] == 0 and out["files"] == ["output/w/sections/abc_1.0_5.0.mp4"]
    assert store["relay/0123456789abcdef/out/output/w/sections/abc_1.0_5.0.mp4"] == b"clip"
    cmd = ran[0]
    assert cmd[:3] == ["/usr/local/bin/yt-dlp", "--cookies", "c.txt"], "this computer's own yt-dlp settings"
    assert not any("@@ROOT@@" in a for a in cmd), "the machine's paths become a private folder here"


def test_the_machine_sends_youtube_to_the_studio_and_archive_org_direct(studio, monkeypatch, tmp_path):
    root = tmp_path / "pod"
    (root / "output/w/info").mkdir(parents=True)
    (root / "output/w/info/abc.info.json").write_text('{"extractor": "youtube"}')
    monkeypatch.setenv("CHANNELRECIPE_HOME", str(root))
    monkeypatch.setenv("FARM_BROKER_URL", "https://cr.test/api/studio/cloud/pod")
    monkeypatch.setenv("FARM_JOB_TOKEN", "crj_job")
    store = {}
    monkeypatch.setattr(studio.cloud.requests, "Session", lambda: Http(store))
    sent = []

    def relay(url, json=None, headers=None, timeout=None):
        sent.append((url, json, headers))
        store[f"{json['prefix']}/out/output/w/sections/abc_1.0_5.0.mp4"] = b"clip"
        body = '  {"rc": 0, "stdout": "", "stderr": "", "files": ["output/w/sections/abc_1.0_5.0.mp4"]}'
        return SimpleNamespace(ok=True, status_code=200, text=body)
    monkeypatch.setattr(studio.cloud.requests, "post", relay)
    argv = ["-q", "-o", f"{root}/output/w/sections/abc_1.0_5.0.%(ext)s", "--load-info-json",
            f"{root}/output/w/info/abc.info.json"]
    assert studio.cloud.ytdlp_main(argv) == 0
    url, body, headers = sent[0]
    assert url == "https://cr.test/api/studio/cloud/relay/ytdlp" and headers == {"x-api-key": "crj_job"}
    assert body["argv"][2] == "@@ROOT@@/output/w/sections/abc_1.0_5.0.%(ext)s"
    assert body["inputs"] == ["output/w/info/abc.info.json"] and f"{body['prefix']}/in/output/w/info/abc.info.json" in store
    assert (root / "output/w/sections/abc_1.0_5.0.mp4").read_bytes() == b"clip"

    calls = []
    monkeypatch.setattr(studio.cloud.subprocess, "call", lambda cmd: calls.append(cmd) or 0)
    assert studio.cloud.ytdlp_main(["-q", "https://archive.org/details/film"]) == 0
    assert calls and calls[0][1:3] == ["-m", "yt_dlp"] and len(sent) == 1, "Archive.org downloads on the machine"


def test_the_machine_uses_the_claude_relay_without_pretending_to_have_openlux(studio, monkeypatch, tmp_path):
    (tmp_path / "make_video.py").write_text("def _claude_endpoint(provider):\n    return None\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "make_video", raising=False)
    monkeypatch.setenv("OPENLUX_BASE_URL", "https://cr.test/api/studio/cloud/relay")
    monkeypatch.setenv("OPENLUX_API_KEY", "crj_job")
    monkeypatch.setenv("CLAUDE_RELAY", "openlux")
    studio.cloud._relay_claude()
    import os
    assert not any(os.environ.get(k) for k in ("OPENLUX_BASE_URL", "OPENLUX_API_KEY", "CLAUDE_RELAY")), \
        "the engine's video watchers don't see an OpenLux key"
    import make_video
    url, headers = make_video._claude_endpoint("anthropic")
    assert url == "https://cr.test/api/studio/cloud/relay/v1/messages" and headers["x-api-key"] == "crj_job"
    assert make_video._claude_endpoint("kie") is None


def test_only_this_studios_own_files_are_uploaded(studio):
    import hashlib
    home = studio.home
    (home / "make_video.py").write_text("engine")
    (home / "styles").mkdir()
    (home / "styles" / "mine.json").write_text("{}")
    (home / "edited.py").write_text("changed")
    (home / "shorts").mkdir()
    (home / "shorts" / "pack.bin").write_text("big")
    (home / ".cr-logs").mkdir()
    (home / ".cr-logs" / "x.log").write_text("log")
    (home / "cr-release.json").write_text('{"version": "5.3-admin.78"}')
    sha = lambda s: hashlib.sha256(s.encode()).hexdigest()
    (home / "cr-files.json").write_text(json.dumps({"files": {"make_video.py": sha("engine"), "edited.py": sha("old"),
                                                              "cr-release.json": sha('{"version": "5.3-admin.78"}')}}))
    rels = [rel for rel, _ in studio.cloud.overlay_files(home, lambda rel: rel.startswith("farm/"))]
    assert sorted(rels) == ["cr-files.json", "edited.py", "styles/mine.json"], \
        "unchanged release files, the Shorts pack, models and dot-folders stay home"
    store = {}
    b = studio.cloud.BrokerBucket("https://cr.test/api/studio/cloud", "Bearer x", Http(store))
    key = studio.cloud.overlay_bundle(b, home, lambda rel: rel.startswith("farm/"), sink=lambda line: None)
    assert key.startswith("code/overlay-5.3-admin.78-") and key in store
    assert studio.cloud.overlay_bundle(b, home, lambda rel: rel.startswith("farm/"), sink=lambda line: None) == key



def test_studio_cloud_takes_a_few_titles_and_names_videos_readably(studio, monkeypatch):
    from flask import Flask
    app = Flask(__name__)
    started = []
    monkeypatch.setattr(studio.cloud.threading, "Thread",
                        lambda target=None, args=(), **kw: SimpleNamespace(start=lambda: started.append(args)))
    monkeypatch.setattr(studio.cloud, "active", lambda home=None: True)
    monkeypatch.setitem(studio.cloud.TASK, "state", "idle")
    studio.cloud.install_routes(SimpleNamespace(app=app), "tok", studio.home)
    c = app.test_client()
    monkeypatch.setattr(studio.cloud, "quote", lambda p, home=None: {"available": True, "short_by_usd": 0})
    ok = c.post("/cr-cloud/start", json={"title": "One\nTwo\nThree", "style": "documentary"},
                headers={"X-CR-Local-Token": "tok"})
    assert ok.status_code == 202 and len(started) == 1
    too_many = c.post("/cr-cloud/start", json={"title": "\n".join(str(i) for i in range(6)), "style": "documentary"},
                      headers={"X-CR-Local-Token": "tok"})
    assert too_many.status_code == 400 and "up to 5" in too_many.get_json()["error"]
    assert c.post("/cr-cloud/start", json={"title": "x"}, headers={"X-CR-Local-Token": "nope"}).status_code == 403



def test_a_moment_of_busy_studio_is_waited_out(studio, monkeypatch):
    monkeypatch.setattr(studio.cloud.time, "sleep", lambda s: None)
    replies = [(409, {"error": "Finish the current studio request before starting more work or changing accounts."}),
               (200, {"started": 1})]
    calls = []

    class Client:
        def post(self, path, json=None, base_url=None, headers=None):
            calls.append(path)
            status, body = (200, {"queued": [{"slug": "t"}]}) if path == "/api/farm/queue" else replies.pop(0)
            return SimpleNamespace(status_code=status, get_json=lambda silent=True: body)
    studio.cloud.start(Client(), "tok", {"title": "T", "style": "documentary"})
    assert calls == ["/api/farm/queue", "/api/farm/run", "/api/farm/run"]
    assert studio.cloud.TASK["state"] == "rendering"


def test_the_machine_gets_provider_choices_never_the_account_key(studio):
    (studio.home / ".cr-connection.json").write_text(json.dumps({
        "key": "cr_mcp_secret", "url": "https://cr.test", "costs_accepted": True, "brain_mode": "claudecode",
        "media_hub": {"provider": "kie", "tasks": ["images", "motion"], "api_key": "kie-secret"},
        "narration": {"provider": "original", "voice": "Kore"}, "images": {"provider": "kie", "token": "t"}}))
    s = studio.cloud.studio_settings(studio.home)
    assert s == {"media_hub": {"provider": "kie", "tasks": ["images", "motion"]}, "images": {"provider": "kie"},
                 "narration": {"provider": "original", "voice": "Kore"}, "costs_accepted": True,
                 "brain_mode": "claudecode"}
    assert "secret" not in json.dumps(s)


def test_the_machine_makes_the_engine_adjustments_the_studio_makes(studio, monkeypatch, tmp_path):
    (tmp_path / "make_video.py").write_text("def _claude_endpoint(provider):\n    return None\n")
    applied = []
    for name in studio.cloud.ENGINE_ADJUSTMENTS:
        monkeypatch.setitem(sys.modules, name, SimpleNamespace(install=lambda engine, n=name: applied.append(n)))
    monkeypatch.setitem(sys.modules, "cr_shorts", SimpleNamespace(install=lambda engine: 1 / 0))
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "make_video", raising=False)
    for k in ("OPENLUX_BASE_URL", "OPENLUX_API_KEY", "CLAUDE_RELAY"):
        monkeypatch.delenv(k, raising=False)
    studio.cloud._relay_claude()
    import make_video
    assert applied == ["cr_media", "cr_render", "cr_style_delivery"], "one failing adjustment doesn't stop the rest"
    assert make_video._claude_endpoint("anthropic") is None, "no relay given: the engine's own endpoint"



def test_3d_goes_to_the_gpu_machine_and_the_farm_no_longer_refuses_it(studio, monkeypatch):
    from flask import Flask, jsonify
    app = Flask(__name__)
    asked = []

    def threed_on(style, asked_features=None):
        return style == "3d"

    engine = SimpleNamespace(app=app, _threed_on=threed_on)

    @app.post("/api/farm/queue")
    def api_farm_queue():
        # the engine's own route checks the module-level function at call time
        asked.append(engine._threed_on("3d"))
        return jsonify(ok=True)
    monkeypatch.setattr(studio.cloud, "_THREED", [])
    monkeypatch.setattr(studio.cloud, "active", lambda home=None: True)
    studio.cloud.install_routes(engine, "tok", studio.home)
    app.test_client().post("/api/farm/queue", json={})
    assert asked == [False], "inside a Studio Cloud queue a 3D job is not refused"
    assert engine._threed_on("3d") is True, "everywhere else the engine's own answer stands"
    assert studio.cloud.wants_gpu({"style": "3d", "features": {"threed": True}})
    assert not studio.cloud.wants_gpu({"style": "documentary"})
    monkeypatch.setattr(studio.cloud, "active", lambda home=None: False)
    app.test_client().post("/api/farm/queue", json={})
    assert asked[-1] is True, "without Studio Cloud (a RunPod farm) the engine still refuses 3D"



def test_stopping_a_machine_ends_the_farms_wait(studio, monkeypatch):
    store = {}
    monkeypatch.setattr(studio.cloud.requests, "Session", lambda: Http(store))

    class Api:
        def request(self, method, url, json=None, headers=None, timeout=None):
            return SimpleNamespace(status_code=200, json=lambda: {"id": "cj_1", "slug": "a", "title": "A",
                                                                   "status": "cancelled"})
    pods = studio.cloud.CloudPods(home=studio.home, session=Api())
    assert pods.terminate("cj_1")
    status = json.loads(store["status/a.json"])
    assert status["state"] == "failed", "the farm's loop sees the end and the Studio is free again"


class Priced:
    """channelrecipe.com's quote and top-up routes."""
    def __init__(self, each_most=84, each_expected=37):
        self.sent, self.each_most, self.each_expected = [], each_most, each_expected

    def post(self, url, json=None, headers=None, timeout=None):
        self.sent.append((url, json, headers))
        if url.endswith("/quote"):
            body = {"machine": json["machine"], "label": "Cloud machine (16 vCPU)", "cents_per_min": 2,
                    "expected_minutes": 37, "max_minutes": 84, "expected_cents": self.each_expected,
                    "max_cents": self.each_most}
        else:
            body = {"url": f"https://checkout.stripe.test/{json['usd']}"}
        return SimpleNamespace(ok=True, status_code=200, json=lambda: body, text="")


def test_the_cloud_quote_says_what_a_batch_reserves_and_the_top_up_that_covers_it(studio, monkeypatch):
    monkeypatch.setattr(studio.cloud, "status", lambda force=False, home=None: {
        "available": True, "balance_usd": 0.2, "chunks_usd": [5, 10, 25, 50], "topup_url": "https://cr.test/app"})
    http = Priced(each_most=168)
    q = studio.cloud.quote({"title": "One\nTwo", "style": "documentary", "minutes": 10}, studio.home, http=http)
    assert http.sent[0][0] == "https://cr.test/api/studio/cloud/quote" and http.sent[0][1] == {"minutes": 10,
                                                                                               "machine": "cpu"}
    assert http.sent[0][2] == {"Authorization": "Bearer cr_mcp_abc"}
    assert (q["titles"], q["most_reserved_usd"], q["short_by_usd"], q["topup_usd"]) == (2, 3.36, 3.16, 5)
    assert "top up first" in studio.cloud.short_message(q).lower() and "nothing has started" in \
        studio.cloud.short_message(q)
    big = studio.cloud.quote({"titles": 5, "style": "3d", "minutes": 20}, studio.home, http=Priced(each_most=960))
    assert big["short_by_usd"] == 47.8 and big["topup_usd"] == 50, "the smallest chunk that covers it"
    assert studio.cloud.quote({"style": "folio"}, studio.home, http=http)["blocked"], "a local-only recipe isn't priced"


def test_a_balance_that_cant_cover_the_batch_starts_nothing(studio, monkeypatch):
    from flask import Flask
    app = Flask(__name__)
    started = []
    monkeypatch.setattr(studio.cloud.threading, "Thread",
                        lambda target=None, args=(), **kw: SimpleNamespace(start=lambda: started.append(args)))
    monkeypatch.setattr(studio.cloud, "active", lambda home=None: True)
    monkeypatch.setitem(studio.cloud.TASK, "state", "idle")
    balance = {"usd": 0.0}
    monkeypatch.setattr(studio.cloud, "status", lambda force=False, home=None: {
        "available": True, "balance_usd": balance["usd"], "chunks_usd": [5, 10, 25, 50]})
    monkeypatch.setattr(studio.cloud, "requests", SimpleNamespace(post=Priced().post,
                                                                  RequestException=Exception))
    studio.cloud.install_routes(SimpleNamespace(app=app), "tok", studio.home)
    c = app.test_client()
    body = {"title": "One\nTwo", "style": "documentary", "minutes": 10}
    r = c.post("/cr-cloud/start", json=body, headers={"X-CR-Local-Token": "tok"})
    assert r.status_code == 402 and started == [], "no script, voice or picture is made for machines that can't start"
    assert r.get_json()["top_up"] and r.get_json()["quote"]["topup_usd"] == 5
    q = c.post("/cr-cloud/quote", json=body, headers={"X-CR-Local-Token": "tok"}).get_json()
    assert q["short_by_usd"] == 1.68 and q["expected_usd"] == 0.74, "Create shows the same numbers first"
    balance["usd"] = 1.68
    assert c.post("/cr-cloud/start", json=body, headers={"X-CR-Local-Token": "tok"}).status_code == 202
    assert len(started) == 1
    assert c.post("/cr-cloud/quote", json=body, headers={"X-CR-Local-Token": "nope"}).status_code == 403


def test_a_top_up_page_is_made_with_the_studios_account_connection(studio, monkeypatch):
    from flask import Flask
    app = Flask(__name__)
    http = Priced()
    monkeypatch.setattr(studio.cloud, "status", lambda force=False, home=None: {"available": True,
                                                                                 "chunks_usd": [5, 10, 25, 50]})
    monkeypatch.setattr(studio.cloud, "requests", SimpleNamespace(post=http.post, RequestException=Exception))
    studio.cloud.install_routes(SimpleNamespace(app=app), "tok", studio.home)
    c = app.test_client()
    r = c.post("/cr-cloud/topup", json={"usd": 10}, headers={"X-CR-Local-Token": "tok"})
    assert r.status_code == 200 and r.get_json()["url"] == "https://checkout.stripe.test/10"
    assert http.sent == [("https://cr.test/api/studio/cloud/topup", {"usd": 10}, {"Authorization": "Bearer cr_mcp_abc"})]
    assert c.post("/cr-cloud/topup", json={"usd": 7}, headers={"X-CR-Local-Token": "tok"}).status_code == 400
    assert c.post("/cr-cloud/topup", json={"usd": 10}, headers={"X-CR-Local-Token": "x"}).status_code == 403


def test_a_learned_styles_own_files_go_to_the_machine_but_never_videos(studio):
    farm_skip = lambda rel: any(p in ("learned", ".DS_Store") for p in rel.split("/")) or rel.endswith(".zip")
    keep = studio.cloud.ship_learned(farm_skip)
    assert not keep("assets/styles/learned/cr_clone_1/renderer-v3.js"), "a coded clone's renderer"
    assert not keep("assets/styles/learned/cr_clone_1/style.jpg") and not keep("learned/kit_a/kit/scenes.js")
    assert keep("learned/kit_a/reference.mp4") and keep("assets/styles/learned/x/clip.MOV"), "videos stay here"
    assert keep("learned/kit_a/.DS_Store") and keep("assets/styles/learned/x/old.zip"), "the farm's other rules hold"
    assert keep("output/learned/x.json"), "elsewhere the farm's rule is unchanged"
    home = studio.home
    (home / "assets/styles/learned/cr_clone_1").mkdir(parents=True)
    (home / "assets/styles/learned/cr_clone_1/renderer-v1.js").write_text("draw()")
    (home / "assets/styles/learned/cr_clone_1/study.mp4").write_bytes(b"0")
    files = [rel for rel, _ in studio.cloud.overlay_files(home, keep)]
    assert "assets/styles/learned/cr_clone_1/renderer-v1.js" in files
    assert "assets/styles/learned/cr_clone_1/study.mp4" not in files


def test_vertical_and_coded_3d_styles_get_their_size_and_the_gpu(studio, monkeypatch):
    looks = {"tall": {"look": {"aspect": "9:16", "visuals": "cr_animated2d"}},
             "coded3d": {"look": {"visuals": "cr_animated3d"}},
             "old3d": {"look": {"visuals": "cr_animated2d", "animated2d": {"dims": 3}}},
             "documentary": {"look": {}}}
    monkeypatch.setattr(studio.cloud, "_STUDIO", [SimpleNamespace(styles=SimpleNamespace(STYLES=looks))])
    monkeypatch.setattr(studio.cloud, "_THREED", [])
    assert studio.cloud.video_size("tall") == {"VIDEO_W": "1080", "VIDEO_H": "1920"}
    assert studio.cloud.video_size("documentary") == {} and studio.cloud.video_size("nope") == {}
    assert studio.cloud.wants_gpu({"style": "coded3d"}) and studio.cloud.wants_gpu({"style": "old3d"})
    assert not studio.cloud.wants_gpu({"style": "tall"}) and not studio.cloud.wants_gpu({"style": "documentary"})

    store = {"jobs/a.json": json.dumps({"minutes": 1, "style": "tall"}).encode()}
    monkeypatch.setattr(studio.cloud.requests, "Session", lambda: Http(store))
    sent = []

    class Api:
        def request(self, method, url, json=None, headers=None, timeout=None):
            sent.append(json)
            return SimpleNamespace(status_code=200, json=lambda: {"id": "cj_1", "slug": "a", "status": "running"})
    monkeypatch.setattr(studio.cloud.Relay, "ensure", classmethod(lambda cls, home=None: None))
    monkeypatch.setattr(studio.cloud, "status", lambda force=False, home=None: {"available": True})
    studio.cloud.CloudPods(home=studio.home, session=Api()).create(name="a", env={"FARM_JOB_KEY": "jobs/a.json"})
    assert sent[0]["env"]["VIDEO_W"] == "1080" and sent[0]["env"]["VIDEO_H"] == "1920", "a 9:16 style renders 9:16"


def test_a_cloud_videos_own_choices_reach_its_run_spec_here_and_on_the_machine(studio, monkeypatch, tmp_path):
    from flask import Flask, jsonify, request
    app = Flask(__name__)
    seen, offers = {}, []
    engine = SimpleNamespace(job_dir=lambda title, style: tmp_path / "output" / style / title.lower(),
                             set_offer=lambda slug, plugs=0: offers.append((slug, plugs)))
    studio_mod = SimpleNamespace(app=app, mv=engine, _threed_on=lambda style, asked=None: False)

    @app.post("/api/farm/queue")
    def api_farm_queue():
        folder = engine.job_dir("One", "documentary")
        seen["while_preparing"] = json.loads((folder / "run_spec.json").read_text())
        seen["offer"] = list(offers)
        return jsonify(queued=[{"slug": "one"}])
    monkeypatch.setattr(studio.cloud, "_THREED", [])
    monkeypatch.setattr(studio.cloud, "active", lambda home=None: True)
    studio.cloud.install_routes(studio_mod, "tok", studio.home)
    p = {"title": "One", "style": "documentary", "minutes": 4, "burn_subs": False, "frame_check": False,
         "threed_quick": True, "desc": True, "chapters": 6, "sources": 2, "offer": "my-course", "offer_plugs": 2,
         "cta": {"what": "My free guide", "link": "https://example.com/guide"}}
    app.test_client().post("/api/farm/queue", json=studio.cloud.farm_payload(p))
    spec = seen["while_preparing"]
    assert spec["cr_captions"] == "off" and spec["cr_frame_check"] == "off" and spec["cr_threed_quick"] is True
    assert spec["meta"] == {"chapters": 6, "sources": 2} and spec["cr_cta"]["link"] == "https://example.com/guide"
    assert seen["offer"] == [("my-course", 2)] and offers[-1] == ("", 0), "the script sells it; the Studio is reset"

    calls = []
    machine = SimpleNamespace(job_dir=engine.job_dir, set_offer=lambda slug, plugs=0: calls.append(("offer", slug)),
                              run_custom=lambda **kw: calls.append(("run", kw.get("meta"))))
    studio.cloud._from_run_spec(machine)
    machine.run_custom(title="One", style="documentary", meta=None)
    assert calls == [("offer", "my-course"), ("run", {"chapters": 6, "sources": 2})], "the machine reads it too"
    with pytest.raises(ValueError):
        studio.cloud.run_spec_choices({"cta": {"what": "x", "link": "nope"}})
