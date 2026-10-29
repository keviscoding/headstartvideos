"""Studio Cloud on the creator's own Modal account (local_runtime/cr_cloud_byo.py): the owner's 2026-10-29 decision
that creators who want cloud rendering add their own cloud keys. The farm keeps working unchanged, with its machines,
bucket and Claude all in the creator's Modal workspace."""
import contextlib
import io
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))

from tests.test_studio_cloud import studio  # noqa: E402,F401 - the same stand-in farm and Studio folder

TID, SECRET = "ak-" + "a1" * 8, "as-" + "b2" * 8


class FakeVolume:
    """A Modal Volume as far as Studio uses it."""
    def __init__(self):
        self.files = {}

    @contextlib.contextmanager
    def batch_upload(self, force=False):
        vol = self

        class Batch:
            def put_file(self, source, path):
                data = source.read() if hasattr(source, "read") else Path(source).read_bytes()
                vol.files[path.lstrip("/")] = data
        yield Batch()

    def read_file_into_fileobj(self, path, fh):
        if path not in self.files:
            raise FileNotFoundError(path)
        fh.write(self.files[path])
        return len(self.files[path])

    def listdir(self, path, recursive=False):
        folder = "" if path in ("/", "") else path.rstrip("/") + "/"
        if folder and not any(k.startswith(folder) for k in self.files):
            raise FileNotFoundError(path)
        import enum

        class FileEntryType(enum.IntEnum):                   # str() of Modal's own is "1", not its name
            FILE = 1
            DIRECTORY = 2
        dirs = sorted({k[:i] for k in self.files for i in range(len(k)) if k[i] == "/" and k.startswith(folder)
                       and len(k[:i]) > len(folder)})
        return [SimpleNamespace(path=d, type=FileEntryType.DIRECTORY, mtime=1_700_000_000, size=4096) for d in dirs
                if recursive] + \
            [SimpleNamespace(path=k, type=FileEntryType.FILE, mtime=1_700_000_000, size=len(v))
             for k, v in self.files.items() if k.startswith(folder) and (recursive or "/" not in k[len(folder):])]

    def remove_file(self, path, recursive=False):
        if path not in self.files:
            raise FileNotFoundError("not found")
        del self.files[path]


@pytest.fixture
def byo(studio, monkeypatch):
    import cr_cloud_byo
    monkeypatch.setattr(cr_cloud_byo, "HOME", studio.home)
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("CR_CLOUD_BYO", raising=False)
    vol = FakeVolume()
    monkeypatch.setattr(cr_cloud_byo, "volume", lambda home=None: vol)
    monkeypatch.setattr(cr_cloud_byo, "library_ready", lambda: True)
    return SimpleNamespace(home=studio.home, cloud=studio.cloud, byo=cr_cloud_byo, vol=vol)


def with_token(byo, monkeypatch):
    (byo.home / ".env").write_text(f"MODAL_TOKEN_ID={TID}\nMODAL_TOKEN_SECRET={SECRET}\n", encoding="utf-8")
    byo.cloud._STATUS.update(at=0.0, data=None)


def test_a_modal_token_is_two_parts_and_only_a_saved_one_switches_studio_cloud_to_it(byo, monkeypatch):
    assert byo.byo.valid_token(TID, SECRET)
    assert not byo.byo.valid_token(SECRET, TID) and not byo.byo.valid_token(TID, "")
    assert not byo.byo.configured(byo.home) and not byo.cloud.byo(byo.home)
    with_token(byo, monkeypatch)
    assert byo.byo.keys(byo.home) == (TID, SECRET) and byo.cloud.byo(byo.home)
    st = byo.cloud.status(home=byo.home)
    assert st["available"] and st["byo"] == "modal" and st["balance_usd"] is None
    assert st["machines"]["cpu"]["price_per_hr"] == 0.63 and st["machines"]["gpu"]["price_per_hr"] == 1.43
    summary = byo.byo.summary(byo.home)
    assert summary["connected"] and SECRET not in json.dumps(summary) and TID not in json.dumps(summary), \
        "Setup's card never gets the token back"


def test_without_a_modal_account_studio_cloud_says_where_to_add_one(studio, monkeypatch):
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: SimpleNamespace(
        ok=True, json=lambda: {"available": False, "why": "Studio Cloud is in testing with ChannelRecipe's team."}))
    st = studio.cloud.status(force=True, home=studio.home)
    assert not st["available"] and st["byo"] == "modal" and st["setup_url"] == "/cr-setup#cloud-render"
    assert "Modal" in st["why"]


def test_the_farms_bucket_is_the_creators_modal_volume(byo):
    b = byo.byo.VolumeBucket(byo.vol)
    b.put_bytes("status/a.json", b'{"state":"queued"}', "application/json")
    assert b.get_bytes("status/a.json") == b'{"state":"queued"}' and b.exists("status/a.json")
    assert not b.exists("status/b.json") and not b.exists("nothing/here.json")
    assert b.presign("jobs/a-assets.zip", 3600) == "modalvol://jobs/a-assets.zip"
    assert [r["key"] for r in b.list("status/")] == ["status/a.json"]
    b.put_bytes("out/a/a.mp4", b"video")
    assert [r["key"] for r in b.list("out/")] == ["out/a/a.mp4"], "folders aren't files (the library lists videos)"
    assert b.list("status/")[0]["modified"].endswith(".000Z"), "S3's own date format"
    from farm.s3 import S3Error
    with pytest.raises(S3Error):
        b.get_bytes("status/missing.json")
    b.delete("status/a.json")
    b.delete("status/a.json"), "deleting what's gone is fine"
    assert b.list("status/") == []


def test_the_farm_renders_on_the_creators_modal_account_when_it_is_saved(byo, monkeypatch):
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    with_token(byo, monkeypatch)
    assert byo.cloud.install(byo.home)
    import farm.config as fc
    import farm.farm as ff
    import farm.runpod as frp
    cfg = fc.Config()
    assert cfg.studio_cloud and cfg.missing_for_mac() == []
    assert [c["key"] for c in cfg.checklist()] == ["cloud", "balance", "render"]
    assert "Modal" in cfg.checklist()[0]["label"]
    assert isinstance(ff.bucket_from(cfg), byo.byo.VolumeBucket)
    assert isinstance(frp.RunPod(cfg.runpod_key), byo.byo.ModalPods), "machines on their own account, not ours"
    assert frp.RunPod("rp_theirs").api_key == "rp_theirs", "a creator's own RunPod key keeps RunPod"


def test_the_quote_is_the_modal_bill_with_no_balance_or_top_up(byo, monkeypatch):
    with_token(byo, monkeypatch)
    q = byo.cloud.quote({"style": "stories", "minutes": 1, "title": "A\nB"}, byo.home)
    assert q["available"] and q["byo"] == "modal" and q["titles"] == 2 and q["machine"] == "cpu"
    assert q["short_by_usd"] == 0 and q["expected_minutes"] == 14 and q["max_minutes"] == 39
    assert q["expected_usd"] == round(2 * q["expected_cents"] / 100, 2) and 0.1 < q["expected_usd"] < 0.4
    assert byo.cloud.quote({"style": "folio", "minutes": 1}, byo.home)["blocked"], "the same local-only list"


def test_the_machine_gets_the_creators_render_keys_but_never_an_anthropic_key(byo, monkeypatch):
    with_token(byo, monkeypatch)
    (byo.home / "cr-release.json").write_text(json.dumps({"version": "5.3-admin.112"}))
    env = {"FARM_CODE_URL": "modalvol://code/overlay-5.3-admin.112-abc.zip", "FARM_JOB_KEY": "jobs/a.json",
           "FARM_ASSETS_URL": "modalvol://jobs/a-assets.zip", "FARM_SLUG": "a", "KIE_API_KEY": "kie_x",
           "ANTHROPIC_API_KEY": "sk-ant-no", "RUNPOD_API_KEY": "studio-cloud", "FARM_BUCKET_SECRET": "studio-cloud"}
    out = byo.byo.machine_env(env, {"style": "stories", "minutes": 1}, byo.home, "https://signed/release.zip")
    assert out["KIE_API_KEY"] == "kie_x" and "ANTHROPIC_API_KEY" not in out and "RUNPOD_API_KEY" not in out
    assert out["CR_CODE_KEY"] == "code/overlay-5.3-admin.112-abc.zip" and out["CR_CODE_KIND"] == "overlay"
    assert out["CR_STUDIO_VERSION"] == "5.3-admin.112" and out["FARM_RELEASE_URL"] == "https://signed/release.zip"
    assert out["CR_CLOUD_BYO"] == "1" and len(out["CR_RELAY_PARTITION"]) == 16 and out["CLAUDE_FALLBACK"] == ""
    assert out["CR_RELAY_PARTITION"] == byo.byo.partition(byo.home), "one lane per Studio, kept"


def test_starting_a_machine_spawns_it_on_the_creators_account(byo, monkeypatch):
    with_token(byo, monkeypatch)
    byo.vol.files["jobs/a.json"] = json.dumps({"minutes": 2, "style": "stories"}).encode()
    spawned = []

    class Fn:
        def spawn(self, job_id, env, max_seconds):
            spawned.append((job_id, env, max_seconds))
            return SimpleNamespace(object_id="fc-1")
    fake = SimpleNamespace(Function=SimpleNamespace(from_name=lambda app, name, client=None: spawned.append(name) or Fn()))
    monkeypatch.setattr(byo.byo, "_modal", lambda: fake)
    monkeypatch.setattr(byo.byo, "client", lambda home=None: None)
    monkeypatch.setattr(byo.byo, "release_link", lambda home=None, http=None: "")
    monkeypatch.setattr(byo.byo.Relay, "ensure", classmethod(lambda cls, home=None: spawned.append("relay")))
    pod = byo.byo.ModalPods(home=byo.home).create(
        name="channelrecipe-a", env={"FARM_JOB_KEY": "jobs/a.json", "FARM_SLUG": "a",
                                     "FARM_CODE_URL": "modalvol://code/channelrecipe-abc.zip"})
    assert spawned[0] == "render_cpu" and "relay" in spawned
    job_id, env, max_seconds = spawned[1]
    assert job_id.startswith("byo_") and env["CR_CODE_KEY"] == "code/channelrecipe-abc.zip"
    assert "CR_CODE_KIND" not in env, "a whole code bundle needs no release"
    assert max_seconds == byo.byo.estimate(2, "cpu")["max_minutes"] * 60
    assert pod["costPerHr"] == 0.63 and pod["desiredStatus"] == "RUNNING"


def test_a_stopped_machine_writes_the_farms_last_status(byo, monkeypatch):
    with_token(byo, monkeypatch)
    cancelled = []
    call = SimpleNamespace(cancel=lambda terminate_containers=False: cancelled.append(terminate_containers))
    fake = SimpleNamespace(FunctionCall=SimpleNamespace(from_id=lambda cid, client=None: call))
    monkeypatch.setattr(byo.byo, "_modal", lambda: fake)
    monkeypatch.setattr(byo.byo, "client", lambda home=None: None)
    pods = byo.byo.ModalPods(home=byo.home)
    pods.JOBS["byo_1"] = {"id": "byo_1", "call_id": "fc-1", "slug": "a", "title": "A", "status": "running",
                          "started_at": time.time(), "max_minutes": 30, "price_per_hr": 0.63, "error": ""}
    assert pods.terminate("byo_1") and cancelled == [True]
    assert json.loads(byo.vol.files["status/a.json"])["state"] == "failed", "the farm's loop stops waiting"
    pods.JOBS.clear()


def test_the_machines_claude_calls_come_back_as_anthropic_messages(byo, monkeypatch):
    asked = []

    def asker(request, wait=0):
        asked.append(request)
        return {"text": "Hello from your Claude Code", "error": ""}
    monkeypatch.setattr(byo.byo._Handler, "asker", staticmethod(asker))
    base = byo.byo.start_local_relay()
    body = json.dumps({"model": "claude-x", "max_tokens": 10, "messages": [{"role": "user", "content": "Hi"}]}).encode()
    with urllib.request.urlopen(urllib.request.Request(base + "/v1/messages", body,
                                                       {"Content-Type": "application/json"}), timeout=10) as r:
        reply = json.loads(r.read())
    assert reply["content"][0]["text"] == "Hello from your Claude Code" and reply["model"] == "claude-x"
    stream = json.dumps({"model": "claude-x", "stream": True, "messages": [{"role": "user", "content": "Hi"}]}).encode()
    with urllib.request.urlopen(urllib.request.Request(base + "/v1/messages", stream,
                                                       {"Content-Type": "application/json"}), timeout=10) as r:
        events = r.read().decode()
    assert "text_delta" in events and "Hello from your Claude Code" in events and "message_stop" in events
    yt = json.dumps({"argv": ["-j", "ytsearch3:cats"], "inputs": [], "prefix": "relay/0123456789abcdef"}).encode()
    monkeypatch.setattr(byo.byo._Handler, "asker", staticmethod(
        lambda request, wait=0: {"text": json.dumps({"rc": 0, "stdout": "{}", "files": []}), "error": ""}))
    with urllib.request.urlopen(urllib.request.Request(base + "/relay/ytdlp", yt), timeout=10) as r:
        assert json.loads(r.read())["rc"] == 0
    assert asked[0]["messages"][0]["content"] == "Hi"


def test_a_question_nobody_answers_is_an_error_the_engine_sees(byo, monkeypatch):
    monkeypatch.setattr(byo.byo._Handler, "asker", staticmethod(lambda request, wait=0: None))
    base = byo.byo.start_local_relay()
    body = json.dumps({"model": "m", "messages": [{"role": "user", "content": "Hi"}]}).encode()
    with urllib.request.urlopen(urllib.request.Request(base + "/v1/messages", body), timeout=10) as r:
        assert json.loads(r.read())["type"] == "error"


def test_the_studio_answers_queued_questions_with_its_claude_code(byo, monkeypatch):
    """The relay loop: a question from the queue is answered here and put back under its id."""
    with_token(byo, monkeypatch)
    import queue

    class Q:
        def __init__(self):
            self.items = [{"id": "r1", "deadline": time.time() + 60}, {"id": "old", "deadline": time.time() - 1}]

        def get(self, timeout=None, partition=None):
            if self.items:
                return self.items.pop(0)
            time.sleep(0.05)
            raise queue.Empty
    answers = {"q:r1": {"messages": [{"role": "user", "content": "Write"}]}, "q:old": {"messages": []}}
    store = SimpleNamespace(pop=lambda k, d=None: answers.pop(k, d), put=lambda k, v: answers.__setitem__(k, v))
    monkeypatch.setattr(byo.byo, "_queues", lambda home=None: (Q(), store))
    monkeypatch.setattr(byo.cloud, "answer", lambda request: "Done: " + request["messages"][0]["content"])
    monkeypatch.setattr(byo.cloud, "_farm_running", lambda: False)
    t = threading.Thread(target=byo.byo.Relay._loop, args=(byo.home,), daemon=True)
    t.start()
    for _ in range(100):
        if "a:r1" in answers:
            break
        time.sleep(0.05)
    assert answers["a:r1"] == {"text": "Done: Write", "error": ""}
    assert "a:old" not in answers, "a question past its deadline isn't answered"
    (byo.home / ".env").unlink()                             # the loop stops once the account is removed


def test_the_machines_app_is_deployed_once_and_again_when_it_changes(byo, monkeypatch):
    with_token(byo, monkeypatch)
    (byo.home / "cr_cloud_modal.py").write_text("# v1\n", encoding="utf-8")
    runs = []
    monkeypatch.setattr(byo.byo.subprocess, "run", lambda cmd, **kw: runs.append((cmd, kw["env"])) or
                        SimpleNamespace(returncode=0, stdout="", stderr=""))
    hydrated = SimpleNamespace(hydrate=lambda: None)
    fake = SimpleNamespace(Function=SimpleNamespace(from_name=lambda *a, **k: hydrated))
    monkeypatch.setattr(byo.byo, "_modal", lambda: fake)
    monkeypatch.setattr(byo.byo, "client", lambda home=None: None)
    byo.byo.ensure_deployed(byo.home)
    byo.byo.ensure_deployed(byo.home)
    assert len(runs) == 1 and runs[0][0][-2:] == ["deploy", "cr_cloud_modal.py"]
    assert runs[0][1]["MODAL_TOKEN_ID"] == TID, "deployed into the creator's own workspace"
    (byo.home / "cr_cloud_modal.py").write_text("# v2\n", encoding="utf-8")
    byo.byo.ensure_deployed(byo.home)
    assert len(runs) == 2


def test_a_failed_deploy_says_why_before_anything_is_made(byo, monkeypatch):
    with_token(byo, monkeypatch)
    monkeypatch.setattr(byo.byo.subprocess, "run", lambda cmd, **kw: SimpleNamespace(
        returncode=1, stdout="", stderr="Error: workspace billing limit reached\n"))
    with pytest.raises(RuntimeError, match="billing limit"):
        byo.byo.ensure_deployed(byo.home)


def test_old_leftovers_are_tidied_but_finished_videos_stay(byo):
    b = byo.byo.VolumeBucket(byo.vol)
    for key in ("code/channelrecipe-old.zip", "code/overlay-new.zip", "releases/channelrecipe-5.3-admin.111.zip",
                "releases/channelrecipe-5.3-admin.112.zip", "jobs/a-assets.zip", "jobs/a.json", "out/a/a.mp4",
                "relay/0123456789abcdef/out/x.mp4"):
        b.put_bytes(key, b"x")
    assert byo.byo.tidy(b, "overlay-new", "5.3-admin.112") == 4
    assert sorted(byo.vol.files) == ["code/overlay-new.zip", "jobs/a.json", "out/a/a.mp4",
                                     "releases/channelrecipe-5.3-admin.112.zip"]


def test_a_relayed_3d_director_gets_the_3d_engines_claude_time_not_15_minutes(byo, monkeypatch):
    """The first own-account GPU test (2026-10-29): the machine's Opus 3D director reached this Studio as a plain
    request, ran under the engine's 900 s Claude Code limit and timed out; at home threed allows 1500 s."""
    seen = {}

    def claude(prompt, model, max_tokens, provider=None):
        seen["timeout"] = engine.CC_TIMEOUT
        return "a plan"
    engine = SimpleNamespace(CC_TIMEOUT=900, SCRIPT_MODEL="m", claude=claude)
    monkeypatch.setitem(sys.modules, "make_video", engine)
    assert byo.cloud.answer({"model": "claude-opus", "messages": [{"role": "user", "content": "Direct"}]}) == "a plan"
    assert seen["timeout"] == byo.cloud.RELAYED_CC_TIMEOUT == 1500
    assert byo.byo.RELAY_WAIT_S > byo.cloud.RELAYED_CC_TIMEOUT, "the machine waits longer than the Studio may take"
