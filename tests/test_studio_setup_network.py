"""Setup finds network and speech-model problems before a video starts, not halfway through one.

Creators' networks cut hosts Studio depends on (DigitalOcean Spaces reset by one internet provider, Kie's file host
aiquickdraw.com cut during the handshake by another), and a worker died while the speech-timing model loaded
mid-video. Each was found only when a video stopped (2026-10-23/24)."""
import json
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local_runtime"))
import cr_network  # noqa: E402
import cr_readiness  # noqa: E402
import cr_relay  # noqa: E402
import cr_whisper  # noqa: E402

pytestmark = pytest.mark.real_setup_checks

EVERYTHING = {"kie": True, "speech_model": True, "youtube": True, "elevenlabs": False}


def probe_with(problems):
    asked = []

    def probe(host):
        asked.append(host)
        return problems.get(host, "")
    probe.asked = asked
    return probe


@pytest.fixture(autouse=True)
def blocked_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cr_relay, "BLOCKED_FILE", tmp_path / ".cr-relay-hosts.json")
    cr_network.forget()
    yield
    cr_network.forget()


# ── the network check ─────────────────────────────────────────────────────────────────────────────────────────────

def test_a_network_that_reaches_everything_passes_and_never_asks_elevenlabs_without_a_key():
    probe = probe_with({})
    result = cr_network.check(EVERYTHING, probe=probe)
    assert result["ready"] and result["message"] == "This computer reaches every service Studio uses."
    assert sorted(probe.asked) == sorted(h[0] for h in cr_network.HOSTS if h[0] != "api.elevenlabs.io")
    with_key = probe_with({})
    cr_network.check({**EVERYTHING, "elevenlabs": True}, probe=with_key)
    assert "api.elevenlabs.io" in with_key.asked


def test_blocked_hosts_studio_goes_around_are_explained_and_dont_block():
    result = cr_network.check(EVERYTHING, probe=probe_with({
        "tempfile.aiquickdraw.com": "cut", "channelrecipe-media.sfo3.digitaloceanspaces.com": "dns",
        "kieai.redpandaai.co": "cut"}))
    assert result["ready"] is True and result["blocked"] == []
    assert sorted(result["fallbacks"]) == ["channelrecipe-media.sfo3.digitaloceanspaces.com", "kieai.redpandaai.co",
                                           "tempfile.aiquickdraw.com"]
    row = next(r for r in result["hosts"] if r["host"] == "tempfile.aiquickdraw.com")
    assert "cuts the secure connection" in row["message"] and "through channelrecipe.com instead" in row["message"]
    assert cr_relay.blocked("tempfile.aiquickdraw.com") and cr_relay.blocked("kieai.redpandaai.co"), \
        "the next video's first file goes straight through the relay"


def test_a_blocked_host_with_no_way_around_blocks_with_what_to_do():
    result = cr_network.check(EVERYTHING, probe=probe_with({"api.kie.ai": "cut"}))
    assert result["ready"] is False and result["blocked"] == ["api.kie.ai"]
    assert "api.kie.ai" in result["message"] and "phone hotspot" in result["message"]
    assert "Kie.ai voice, pictures and animation" in result["message"]
    dns = cr_network.check(EVERYTHING, probe=probe_with({"api.kie.ai": "dns"}))
    assert "can't look up its address" in dns["message"] and "Check this computer is online" in dns["message"]
    inspected = cr_network.check(EVERYTHING, probe=probe_with({"api.kie.ai": "intercepted"}))
    assert "intercepts secure connections" in inspected["message"] and "Antivirus" in inspected["message"]


def test_without_channelrecipe_the_ways_around_dont_work_either():
    result = cr_network.check(EVERYTHING, probe=probe_with({"channelrecipe.com": "timeout",
                                                             "tempfile.aiquickdraw.com": "cut"}))
    assert result["ready"] is False
    assert result["blocked"] == ["channelrecipe.com", "tempfile.aiquickdraw.com"]
    assert "can't be reached either" in next(r for r in result["hosts"]
                                              if r["host"] == "tempfile.aiquickdraw.com")["message"]


def test_hugging_face_matters_only_until_the_speech_model_is_downloaded():
    missing = cr_network.check(EVERYTHING, probe=probe_with({"huggingface.co": "cut"}))
    assert missing["ready"] is False and missing["blocked"] == ["huggingface.co"]
    have = cr_network.check({**EVERYTHING, "speech_model": False}, probe=probe_with({"huggingface.co": "cut"}))
    assert have["ready"] is True and have["blocked"] == []
    assert "already on this computer" in have["hosts"][[r["host"] for r in have["hosts"]].index("huggingface.co")][
        "message"]


def test_youtube_only_stops_the_recipes_that_use_it():
    result = cr_network.check(EVERYTHING, probe=probe_with({"www.youtube.com": "refused"}))
    assert result["ready"] is True and result["warnings"] == ["www.youtube.com"]
    assert "Ranking Shorts" in result["message"]
    why = cr_network.youtube_problem(result)
    assert "can't reach www.youtube.com (the connection is refused)" in why and "turn YouTube footage off" in why
    assert cr_network.youtube_problem(cr_network.check(EVERYTHING, probe=probe_with({}))) == ""


def test_kie_hosts_dont_block_a_studio_that_doesnt_use_kie(monkeypatch):
    monkeypatch.delenv("KIE_API_KEY", raising=False)
    wants = cr_network.wants_from_env(speech_model_missing=False, hub_provider="atlascloud")
    assert wants["kie"] is False
    assert cr_network.check(wants, probe=probe_with({"api.kie.ai": "cut"}))["ready"] is True
    monkeypatch.setenv("KIE_API_KEY", "k")
    assert cr_network.wants_from_env(speech_model_missing=False, hub_provider="atlascloud")["kie"] is True


def test_one_dropped_handshake_is_tried_again_before_it_counts():
    tries = {"n": 0}

    def flaky(host):
        if host == "api.kie.ai":
            tries["n"] += 1
            return "timeout" if tries["n"] == 1 else ""
        return ""
    assert cr_network.check(EVERYTHING, probe=flaky)["ready"] and tries["n"] == 2


def test_status_runs_in_the_background_keeps_a_pass_and_checks_a_failure_again(monkeypatch):
    probe = probe_with({})
    first = cr_network.status(EVERYTHING, probe=probe)
    assert first["ready"] and len(probe.asked) == 7
    cr_network.status(EVERYTHING, probe=probe)
    assert len(probe.asked) == 7, "a pass is kept for ten minutes"
    cr_network.forget()
    failing = probe_with({"api.kie.ai": "cut"})
    assert cr_network.status(EVERYTHING, probe=failing)["ready"] is False
    monkeypatch.setattr(cr_network, "FAIL_SECONDS", 0)
    cr_network.status(EVERYTHING, probe=failing)
    assert failing.asked.count("api.kie.ai") == 4, "a failure is checked again (each check tries twice)"
    # what's needed changes without new handshakes: the speech model finished downloading
    assert cr_network.classify_all({"huggingface.co": "cut"}, {"speech_model": False})["ready"] is True


def test_status_answers_checking_while_the_first_check_runs():
    gate = threading.Event()

    def slow(host):
        gate.wait(5)
        return ""
    early = cr_network.status(EVERYTHING, probe=slow, wait=0.05)
    assert early["checking"] and early["ready"] is True and "Checking" in early["message"]
    gate.set()
    for _ in range(100):
        later = cr_network.status(EVERYTHING, probe=slow, wait=1)
        if not later.get("checking"):
            break
    assert later["ready"] and later["hosts"]


def test_handshake_tells_a_cut_connection_from_a_refused_one(monkeypatch):
    """A real local server that accepts and hangs up during the handshake, like the network that cut aiquickdraw.com."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    port = server.getsockname()[1]

    def hang_up():
        for _ in range(2):
            try:
                conn, _ = server.accept()
                conn.close()
            except OSError:
                return
    threading.Thread(target=hang_up, daemon=True).start()
    monkeypatch.setattr(cr_network, "PORT", port)
    monkeypatch.setattr(cr_network, "_proxy_for", lambda host: "")
    assert cr_network.handshake("127.0.0.1", timeout=3) == "cut"
    server.close()
    assert cr_network.handshake("127.0.0.1", timeout=3) in ("refused", "cut")
    assert cr_network.classify(socket.gaierror(8, "nodename nor servname provided")) == "dns"
    assert cr_network.classify(ssl.SSLCertVerificationError("certificate verify failed")) == "intercepted"
    assert cr_network.classify(socket.timeout("timed out")) == "timeout"
    assert cr_network.classify(ConnectionRefusedError()) == "refused"
    assert cr_network.classify(ssl.SSLEOFError("EOF occurred in violation of protocol")) == "cut"
    assert cr_network.classify(ConnectionResetError()) == "cut"


def test_diagnostics_say_which_hosts_and_why():
    result = cr_network.check(EVERYTHING, probe=probe_with({"api.kie.ai": "cut", "tempfile.aiquickdraw.com": "cut"}))
    line = cr_network.diagnostics_line(result)
    assert line.startswith("Network: can't reach api.kie.ai (the network cuts the secure connection as it starts, "
                           "blocks setup)")
    assert "tempfile.aiquickdraw.com (the network cuts the secure connection as it starts, goes around" in line
    assert cr_network.diagnostics_line(cr_network.check(EVERYTHING, probe=probe_with({}))) == \
        "Network: every service reachable"


# ── readiness: setup blocks only what has no way around it ────────────────────────────────────────────────────────

def rows(network=None, speech=None):
    checks = {}
    if network is not None:
        checks["network"] = network
    if speech is not None:
        checks["speech_model"] = speech
    return {r["key"]: r for r in cr_readiness.setup_rows(checks)}


def test_setup_rows_block_a_cut_kie_and_a_missing_speech_model_but_not_a_way_around():
    blocked = cr_network.check(EVERYTHING, probe=probe_with({"api.kie.ai": "cut"}))
    around = cr_network.check(EVERYTHING, probe=probe_with({"tempfile.aiquickdraw.com": "cut"}))
    clear = cr_network.check(EVERYTHING, probe=probe_with({}))
    assert rows(blocked)["network"]["ready"] is False and "api.kie.ai" in rows(blocked)["network"]["message"]
    assert rows(around)["network"]["ready"] is True and "through channelrecipe.com" in rows(around)["network"]["message"]
    assert rows(clear)["network"] == {"key": "network", "label": "Network", "ready": True, "message": ""}
    downloading = {"ready": False, "state": "downloading", "reason": "Downloading the speech-timing model: 63 of 141 MB."}
    assert rows(speech=downloading)["speech_model"]["ready"] is False
    assert "63 of 141 MB" in rows(speech=downloading)["speech_model"]["message"]
    assert rows(speech={"ready": True, "state": "ready"})["speech_model"]["ready"] is True
    assert rows() == {}, "older callers without these checks see no new rows"


def preset_studio(features):
    info = {"features": {"offered": list(features), "on": list(features)}}
    resolve = lambda info, asked: {k: True for k in features}
    return SimpleNamespace(
        mv=SimpleNamespace(prompts=SimpleNamespace(PROMPT_SETS={"doc": {}}), STYLE_INFO={"doc": info}),
        styles=SimpleNamespace(load_all=lambda: None, STYLES={"doc": info}),
        features=SimpleNamespace(capabilities=lambda: {"base": {"voice": {"ok": True}}, "features": {}},
                                 KEYS=list(features), EXPLICIT=[], BY_KEY={}, resolve=resolve))


@pytest.mark.parametrize("features,stopped", [(["youtube"], True), ([], False)])
def test_a_recipe_with_youtube_footage_stops_before_it_starts_when_youtube_is_cut(monkeypatch, features, stopped):
    monkeypatch.setattr(cr_readiness, "basic", lambda checks: {"checks": [], "claude_code": {}, "brain_mode": "claudecode"})
    monkeypatch.setattr(cr_readiness, "proof", lambda *a, **k: {})
    monkeypatch.setattr(cr_readiness.cr_media, "active", lambda task: False)
    network = cr_network.check(EVERYTHING, probe=probe_with({"www.youtube.com": "cut"}))
    result = cr_readiness.preset(preset_studio(features), {"network": network}, {"style": "doc"})
    youtube = [c for c in result["checks"] if c["key"] == "youtube_network"]
    assert bool(youtube) is stopped and result["ready"] is (not stopped)
    if stopped:
        assert "can't reach www.youtube.com" in youtube[0]["message"]


# ── the speech-timing model ───────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def speech(tmp_path, monkeypatch):
    """A Studio folder and a fake Hugging Face cache; nothing is downloaded or loaded for real."""
    monkeypatch.setattr(cr_whisper, "MARKER", tmp_path / ".cr-speech-model.json")
    monkeypatch.delenv("WHISPER_MODEL", raising=False)
    monkeypatch.setattr(cr_whisper, "HOME", tmp_path)
    folder = tmp_path / "hub" / "snapshot"
    state = {"have": False}
    monkeypatch.setattr(cr_whisper, "local_path", lambda name=None: str(folder) if state["have"] else "")
    cr_whisper.FIX.update(state="idle", done=0, total=0, tail="")

    def put_model():
        folder.mkdir(parents=True, exist_ok=True)
        for name in cr_whisper.NEEDED:
            (folder / name).write_bytes(b"x" * 10)
        state["have"] = True
    return SimpleNamespace(folder=folder, state=state, put_model=put_model)


class FakeDownload:
    """subprocess.Popen for the download child: writes the model files and prints where they are."""
    def __init__(self, speech, output=None, code=0):
        self.speech, self.output, self.code, self.started = speech, output, code, []

    def __call__(self, argv, **kw):
        self.started.append((argv, kw))
        if self.code == 0:
            self.speech.put_model()
        text = self.output if self.output is not None else f"CR-MODEL-PATH {self.speech.folder}\n"
        return SimpleNamespace(stdout=iter(text.splitlines(True)), poll=lambda: self.code, returncode=self.code,
                               kill=lambda: None)


def wait_fix():
    for _ in range(200):
        if cr_whisper.FIX["state"] not in ("downloading", "checking"):
            return
        time.sleep(0.02)


def test_a_missing_model_is_downloaded_in_setup_then_loaded_once_and_ready(speech, monkeypatch):
    assert cr_whisper.status()["state"] == "missing" and "Press Fix it" in cr_whisper.status()["reason"]
    download = FakeDownload(speech)
    monkeypatch.setattr(cr_whisper.subprocess, "Popen", download)
    loads = []
    monkeypatch.setattr(cr_whisper.subprocess, "run", lambda argv, **kw: loads.append((argv, kw)) or
                        SimpleNamespace(returncode=0, stdout="CR-MODEL-LOADED\n", stderr=""))
    cr_whisper.ensure()
    wait_fix()
    argv, kw = download.started[0]
    assert argv[:3] == [sys.executable, "-I", "-c"] and argv[4] == "Systran/faster-whisper-base"
    assert kw["env"]["HF_HUB_DISABLE_XET"] == "1", "a plain download writes a file setup can measure"
    load_argv, load_kw = loads[0]
    assert load_argv[:3] == [sys.executable, "-I", "-c"] and load_argv[4] == str(speech.folder)
    assert 'device="cpu", compute_type="int8"' in load_argv[3], "loaded the way the engine loads it"
    assert load_kw["env"]["KMP_DUPLICATE_LIB_OK"] == "TRUE"
    assert cr_whisper.status() == {"ready": True, "state": "ready", "reason": "", "path": str(speech.folder)}
    assert json.loads(cr_whisper.MARKER.read_text())["ok"] is True


def test_the_progress_line_counts_megabytes(speech, monkeypatch):
    cr_whisper.FIX.update(state="downloading", done=63 << 20, total=141 << 20)
    assert cr_whisper.status()["reason"].startswith("Downloading the speech-timing model: 63 of 141 MB.")
    cr_whisper.FIX.update(state="checking")
    assert "Checking the speech-timing model loads" in cr_whisper.status()["reason"]


def test_a_model_that_cant_load_shows_in_setup_and_fix_it_downloads_it_again(speech, monkeypatch):
    speech.put_model()
    monkeypatch.setattr(cr_whisper.subprocess, "run", lambda argv, **kw: SimpleNamespace(
        returncode=-4, stdout="whisper: loading\n", stderr="Illegal instruction\n"))
    cr_whisper.ensure(background=False)
    status = cr_whisper.status()
    assert status["ready"] is False and status["state"] == "broken"
    assert "processor instruction this computer doesn't have" in status["reason"] and "Fix it" in status["reason"]
    import cr_studio
    forced = []
    monkeypatch.setattr(cr_studio.cr_whisper, "ensure", lambda force=False: forced.append(force) or {"state": "checking"})
    assert cr_studio.fix_speech_model() == {"state": "checking"} and forced == [True]


def test_a_crash_while_loading_never_takes_studio_with_it(speech, monkeypatch):
    """The load runs in its own small process: a segfault there is a message, not a dead Studio."""
    speech.put_model()
    real = subprocess.run
    monkeypatch.setattr(cr_whisper, "LOAD", "import os, signal; os.kill(os.getpid(), signal.SIGSEGV)")
    if sys.platform == "win32":
        # 0xC0000005, an access violation: os._exit takes a C int, so it's written as a negative number
        monkeypatch.setattr(cr_whisper, "LOAD", "import os; os._exit(-1073741819)")
    monkeypatch.setattr(cr_whisper.subprocess, "run", real)
    record = cr_whisper.load_check(str(speech.folder))
    assert record["ok"] is False and "a library it uses crashed" in record["error"]


def test_a_blocked_hugging_face_says_what_to_do(speech, monkeypatch):
    monkeypatch.setattr(cr_whisper.subprocess, "Popen", FakeDownload(
        speech, code=1, output="httpx.ConnectError: [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred\n"))
    cr_whisper.ensure(background=False)
    status = cr_whisper.status()
    assert status["state"] == "missing" and "can't reach huggingface.co" in status["reason"]
    assert "phone hotspot" in status["reason"] and "Fix it" in status["reason"]
    assert "disk is full" in cr_whisper.download_problem("OSError: [Errno 28] No space left on device")


def test_studio_start_checks_a_model_downloaded_earlier_once(speech, monkeypatch):
    speech.put_model()
    loads = []
    monkeypatch.setattr(cr_whisper.subprocess, "run", lambda argv, **kw: loads.append(argv) or
                        SimpleNamespace(returncode=0, stdout="CR-MODEL-LOADED", stderr=""))
    cr_whisper.start()
    wait_fix()
    cr_whisper.start()
    wait_fix()
    assert len(loads) == 1, "checked once, then remembered for these exact files"
    (speech.folder / "model.bin").write_bytes(b"y" * 99)
    assert cr_whisper.status()["state"] == "unchecked", "new files are checked again"


def test_videos_load_the_downloaded_copy_never_hugging_face(speech):
    engine = SimpleNamespace(WHISPER_MODEL="base", _WHISPER=None)
    cr_whisper.point_engine(engine)
    assert engine.WHISPER_MODEL == "base", "nothing downloaded: the engine loads it as before"
    speech.put_model()
    cr_whisper.point_engine(engine)
    assert engine.WHISPER_MODEL == str(speech.folder)
    loaded = SimpleNamespace(WHISPER_MODEL="base", _WHISPER=object())
    cr_whisper.point_engine(loaded)
    assert loaded.WHISPER_MODEL == "base", "a model already loaded is left alone"


def test_the_engine_hook_points_whisper_at_the_local_copy_before_it_loads(speech):
    import cr_media
    speech.put_model()
    seen = []
    engine = SimpleNamespace(WHISPER_MODEL="base", _WHISPER=None)
    engine._get_whisper = lambda: seen.append(engine.WHISPER_MODEL) or SimpleNamespace(transcribe=lambda *a, **k: "ok")
    cr_media.wrap_whisper(engine)
    assert engine._get_whisper().transcribe([0.0]) == "ok"
    assert seen == [str(speech.folder)]


def test_local_path_finds_a_folder_model_and_rejects_an_incomplete_one(tmp_path, monkeypatch):
    folder = tmp_path / "my-model"
    folder.mkdir()
    assert cr_whisper.local_path(str(folder)) == ""
    for name in cr_whisper.NEEDED:
        (folder / name).write_text("x")
    assert cr_whisper.local_path(str(folder)) == str(folder)
    monkeypatch.setenv("WHISPER_MODEL", "small")
    assert cr_whisper.model_name(tmp_path) == "small" and cr_whisper.repo_for("small") == "Systran/faster-whisper-small"
