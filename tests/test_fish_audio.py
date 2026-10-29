"""Fish Audio narration on the creator's own key (local_runtime/cr_fish.py). Customer Swapnil, Discord 2026-10-29:
"Please add Fish Audio integration". Every Fish Audio call here is mocked: no key, no network, nothing paid."""
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
from flask import Flask, jsonify

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_bridge
import cr_fish
import cr_media
import cr_network
import cr_studio_tools as tools
import cr_claude_code

VOICE = "a" * 32
OTHER = "b" * 32
KEY = "fish-test-key-0123456789"
AUDIO = b"ID3" + b"\x00" * 4000


class Answer:
    def __init__(self, status=200, body=None, audio=AUDIO, kind="audio/mpeg", broken=False):
        self.status_code, self._body, self._audio, self._broken = status, body, audio, broken
        self.headers = {"content-type": kind if status == 200 else "application/json"}
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        return self._body

    def iter_content(self, size):
        yield self._audio[:2000]
        if self._broken:
            raise requests.exceptions.ChunkedEncodingError("connection broken mid-answer")
        yield self._audio[2000:]


class Poster:
    """POST /v1/tts answers in order; records each request."""
    def __init__(self, *answers):
        self.answers, self.sent = list(answers), []

    def __call__(self, url, headers=None, json=None, stream=None, timeout=None):
        self.sent.append({"url": url, "headers": headers, "json": json, "stream": stream})
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(cr_bridge, "SETTINGS", tmp_path / ".cr-connection.json")
    cr_bridge.save_settings({"key": "cr", "costs_accepted": True,
                             "narration": {"provider": "fish", "model": "s2.1-pro", "voice": VOICE,
                                           "voice_name": "Narrator"}})
    monkeypatch.setenv(cr_fish.KEY_ENV, KEY)
    monkeypatch.delenv("KIE_API_KEY", raising=False)
    monkeypatch.setattr(cr_fish, "_language", lambda: "")
    job = tmp_path / "job"
    job.mkdir()
    return job


def engine():
    lines = []
    return types.SimpleNamespace(log=lines.append, lines=lines)


# ── prices ─────────────────────────────────────────────────────────────────────────────────────────────────────

def test_prices_follow_fish_audios_price_list():
    assert cr_fish.cost_usd("x" * 1_000_000, "s2.1-pro") == pytest.approx(15.0)
    assert cr_fish.cost_usd("é" * 500_000, "s2-pro") == pytest.approx(15.0)           # priced by UTF-8 bytes
    assert cr_fish.cost_usd("x" * 1000, "s2.1-pro-free") == 0
    before = datetime(2026, 11, 1, tzinfo=timezone.utc)
    after = datetime(2026, 11, 13, tzinfo=timezone.utc)
    assert cr_fish.usd_per_million("drama-3", before) == 10.0 and cr_fish.usd_per_million("drama-3", after) == 50.0
    assert cr_fish.usd_per_minute("s2.1-pro") == pytest.approx(0.0135)
    assert cr_fish.model_info("s1")["id"] == "s2.1-pro"                                  # deprecated: never chosen


def test_long_scripts_are_cut_into_parts_at_sentences():
    text = ("This is one sentence of the story. " * 200).strip()
    parts = list(cr_fish.chunks(text))
    assert len(parts) > 1 and all(len(p) <= cr_fish.PART_CHARS for p in parts)
    assert all(p.endswith(".") for p in parts) and " ".join(parts) == text


# ── the key check and the voice lists ──────────────────────────────────────────────────────────────────────────

def test_key_check_reads_the_free_balance_and_explains_a_bad_key():
    asked = []

    def get(url, headers=None, **k):
        asked.append((url, headers))
        return Answer(200, {"credit": "4.25"})
    assert cr_fish.check_key(KEY, get=get) == {"credit": 4.25}
    assert asked[0] == ("https://api.fish.audio/wallet/self/api-credit", {"Authorization": "Bearer " + KEY})
    with pytest.raises(cr_fish.Stop, match="didn't accept this key"):
        cr_fish.check_key(KEY, get=lambda *a, **k: Answer(401, {"message": "Invalid Token", "status": 401}))


def model(i, **extra):
    return {"_id": i, "type": "tts", "title": "Voice " + i[:2], "state": "trained", "languages": ["en"],
            "description": "Warm and clear", "author": {"nickname": "someone"},
            "samples": [{"title": "s", "text": "t", "task_id": "x", "audio": "https://cdn.fish.audio/s.mp3"}], **extra}


def test_my_voices_list_own_clones_then_picked_library_voices(home):
    seen = []

    def get(url, headers=None, params=None, **k):
        seen.append((url, params))
        return Answer(200, {"total": 2, "items": [model(VOICE), model("c" * 32, state="training")]})
    got = cr_fish.list_voices(KEY, "mine", "", 1, get=get,
                              saved=[{"id": OTHER, "name": "Library pick", "preview": "http://insecure"}])
    assert seen[0][0] == "https://api.fish.audio/model" and seen[0][1]["self"] == "true"
    assert [v["id"] for v in got["voices"]] == [VOICE, OTHER]                  # the training clone isn't offered yet
    assert got["voices"][0]["preview"] == "https://cdn.fish.audio/s.mp3" and got["voices"][0]["mine"] is True
    assert got["voices"][1]["preview"] == ""                                     # only https samples play


def test_the_library_is_searched_and_a_pasted_link_finds_its_voice(home):
    seen = []

    def get(url, headers=None, params=None, **k):
        seen.append((url, params))
        return Answer(200, model(OTHER) if url.endswith(OTHER) else {"total": 50, "items": [model(VOICE)]})
    got = cr_fish.list_voices(KEY, "shared", "deep narrator", 1, get=get)
    assert seen[-1][1]["title"] == "deep narrator" and "self" not in seen[-1][1] and got["more"] is True
    got = cr_fish.list_voices(KEY, "shared", f"https://fish.audio/m/{OTHER}/", 1, get=get)
    assert seen[-1][0] == f"https://api.fish.audio/model/{OTHER}" and [v["id"] for v in got["voices"]] == [OTHER]


# ── reading a part: receipts, refusals, uncertainty ────────────────────────────────────────────────────────────

def test_a_part_is_read_once_and_reused_from_its_receipt(home):
    post = Poster(Answer())
    part = home / "v" / "part-000.mp3"
    part.parent.mkdir()
    cr_fish.synthesize(KEY, "Hello there.", VOICE, "s2.1-pro", part, post=post, sleep=lambda s: None)
    sent = post.sent[0]
    assert sent["url"] == "https://api.fish.audio/v1/tts" and sent["stream"] is True
    assert sent["headers"]["model"] == "s2.1-pro" and sent["headers"]["Authorization"] == "Bearer " + KEY
    assert sent["json"] == {"text": "Hello there.", "reference_id": VOICE, "format": "mp3", "mp3_bitrate": 192,
                            "latency": "normal", "normalize": True}
    assert part.read_bytes() == AUDIO and cr_fish.read_receipt(part)["state"] == "done"
    cr_fish.synthesize(KEY, "Hello there.", VOICE, "s2.1-pro", part, post=Poster(), sleep=lambda s: None)   # no POST


def test_busy_answers_are_waited_out_and_a_balance_refusal_stops_without_charge(home):
    part = home / "part-000.mp3"
    waits = []
    post = Poster(Answer(429, {"status": 429, "message": "Too many requests"}), Answer())
    cr_fish.synthesize(KEY, "Hi.", VOICE, "s2.1-pro", part, post=post, sleep=waits.append)
    assert waits == [30] and len(post.sent) == 2 and part.is_file()
    other = home / "part-001.mp3"
    with pytest.raises(cr_fish.Stop, match="API balance") as stop:
        cr_fish.synthesize(KEY, "Hi again.", VOICE, "s2.1-pro", other,
                           post=Poster(Answer(402, {"status": 402, "message": "No payment"})), sleep=lambda s: None)
    assert stop.value.category == "balance" and isinstance(stop.value, cr_media.AccountStop)
    assert cr_fish.read_receipt(other)["state"] == "refused"
    post = Poster(Answer())                                  # after a top-up, Resume sends it: nothing was made before
    cr_fish.synthesize(KEY, "Hi again.", VOICE, "s2.1-pro", other, post=post, sleep=lambda s: None)
    assert len(post.sent) == 1 and other.is_file()


def test_an_answer_cut_off_midway_is_never_sent_again_without_a_yes(home):
    part = home / "part-000.mp3"
    with pytest.raises(cr_fish.Stop) as stop:
        cr_fish.synthesize(KEY, "Hi.", VOICE, "s2.1-pro", part, post=Poster(Answer(broken=True)), sleep=lambda s: None)
    assert stop.value.uncertain and not part.exists() and not part.with_name(part.name + ".download").exists()
    with pytest.raises(cr_fish.Stop, match="may already have read"):
        cr_fish.synthesize(KEY, "Hi.", VOICE, "s2.1-pro", part, post=Poster(), sleep=lambda s: None)   # no POST
    assert cr_fish.uncertain_parts(home) == [] and part.parent == home     # only *-fish-voice folders are listed
    folder = home / "audio-fish-voice"
    folder.mkdir()
    part2 = folder / "part-000.mp3"
    with pytest.raises(cr_fish.Stop):
        cr_fish.synthesize(KEY, "Hi.", VOICE, "s2.1-pro", part2, post=Poster(requests.exceptions.ReadTimeout()),
                           sleep=lambda s: None)
    assert cr_fish.uncertain_parts(home) == [part2]
    assert cr_fish.release_uncertain(home) == 1 and cr_fish.uncertain_parts(home) == []
    post = Poster(Answer())
    cr_fish.synthesize(KEY, "Hi.", VOICE, "s2.1-pro", part2, post=post, sleep=lambda s: None)
    assert len(post.sent) == 1 and part2.is_file()


def test_a_connection_that_never_opened_is_tried_again(home):
    part = home / "part-000.mp3"
    never = requests.exceptions.ConnectTimeout("connect timed out")
    post = Poster(never, Answer())
    cr_fish.synthesize(KEY, "Hi.", VOICE, "s2.1-pro", part, post=post, sleep=lambda s: None)
    assert len(post.sent) == 2 and part.is_file()


# ── a video's narration ────────────────────────────────────────────────────────────────────────────────────────

def test_a_video_reads_with_fish_audio_in_parts_and_is_timed_on_this_computer(home, monkeypatch):
    joined, timed = [], []
    monkeypatch.setattr(cr_fish.subprocess, "run", lambda args, **k: joined.append(args) or Path(args[-1]).write_bytes(b"mp3"))
    text = ("A sentence to read aloud here. " * 150).strip()
    post = Poster(*[Answer() for _ in range(5)])
    e = engine()
    cfg = cr_media.fish_voice(home, "jung")
    assert cfg["voice"] == VOICE and cr_media.profile("narration") == {}      # not a hub: Kie never reads it
    cr_fish.narrate(e, text, home / "audio.mp3", home / "subs.srt", cfg, post=post, sleep=lambda s: None,
                    time_narration=lambda eng, mp3, srt, words: timed.append(words))
    assert len(post.sent) == len(list(cr_fish.chunks(text))) > 1 and timed == [text] and joined
    assert json.loads((home / cr_fish.PIN).read_text())["voice"] == VOICE
    assert any("Fish Audio" in line and "$" in line for line in e.lines)
    # a Resume after the parts were made pays for nothing
    cr_fish.narrate(e, text, home / "audio.mp3", home / "subs.srt", cfg, post=Poster(), sleep=lambda s: None,
                    time_narration=lambda *a: None)


def test_a_stop_is_kept_for_the_voice_choices(home):
    with pytest.raises(cr_media.AccountStop):
        cr_fish.narrate(engine(), "Hello.", home / "audio.mp3", home / "subs.srt", cr_media.fish_voice(home),
                        post=Poster(Answer(404, {"status": 404, "message": "Model not found"})), sleep=lambda s: None,
                        time_narration=lambda *a: None)
    failed = json.loads((home / cr_media.VOICE_FAILED).read_text())
    assert failed["provider"] == "fish" and failed["label"] == "Fish Audio" and failed["uncertain"] is False


def test_nothing_is_read_before_the_spending_agreement(home):
    cr_bridge.save_settings({"narration": {"provider": "fish", "voice": VOICE}})
    with pytest.raises(cr_media.AccountStop, match="spending agreement"):
        cr_fish.narrate(engine(), "Hello.", home / "audio.mp3", home / "subs.srt", {"voice": VOICE},
                        post=Poster(), time_narration=lambda *a: None)


def test_the_voiceover_step_routes_to_fish_audio(home, monkeypatch):
    calls = []
    monkeypatch.setattr(cr_fish, "narrate", lambda eng, text, mp3, srt, cfg, **k: calls.append(cfg) or
                        (Path(mp3).write_bytes(b"mp3"), Path(srt).write_text("1\n")))
    eng = types.SimpleNamespace(log=print, _split_script_by_minutes=lambda s, m: [s],
                                _speed_voice=lambda mp3, srt, style: calls.append(("speed", style)),
                                _regroup_word_srt=lambda srt: None, _denoise_voice=lambda mp3, style: mp3)
    voiceover = cr_media.make_voiceover(eng, lambda *a, **k: pytest.fail("the engine's own voice"))
    voiceover("Hello.", home, style="jung")
    assert calls[0]["voice"] == VOICE and calls[1] == ("speed", "jung")      # Studio's voice speed still applies


def test_a_video_can_ask_for_its_own_fish_voice_and_keeps_it(home):
    (home / "run_spec.json").write_text(json.dumps({"cr_voice": {"provider": "fish", "voice": OTHER}}))
    assert cr_media.job_voice(home)["voice"] == OTHER and cr_media.fish_voice(home)["voice"] == OTHER
    (home / cr_media.VOICE_CHOICE).write_text(json.dumps({"provider": "kie", "model": "google/gemini-3-8-flash-tts",
                                                          "voice": "Charon"}))
    assert cr_media.fish_voice(home) == {}                                     # chosen after a stop: Gemini wins
    (home / cr_media.VOICE_CHOICE).unlink()
    (home / cr_media.STYLE_VOICE_PIN).write_text(json.dumps({"provider": "kie", "voice": "Kore"}))
    assert cr_media.fish_voice(home)["voice"] == OTHER                         # its own ask beats the old pin


def test_a_video_keeps_the_fish_voice_it_started_with(home):
    (home / cr_fish.PIN).write_text(json.dumps({"voice": OTHER, "model": "s2-pro"}))
    assert cr_media.fish_voice(home) == {"provider": "fish", "model": "s2-pro", "voice": OTHER, "voice_name": ""}


# ── Setup and Choose voice routes ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def studio(home, monkeypatch):
    app = Flask("fish-test")
    saved_keys = []

    @app.get("/cr-voices", endpoint="voices_list")
    def voices_list():
        return jsonify(connected=True, hub=True)

    @app.post("/cr-voices/select", endpoint="voices_select")
    def voices_select():
        return jsonify(hub=True)

    @app.get("/cr-voices/library", endpoint="voices_library")
    def voices_library():
        return jsonify(eleven=True)

    styles = types.SimpleNamespace(STYLES={"jung": {"label": "Jung"}})
    s = types.SimpleNamespace(app=app, styles=styles)
    busy = types.SimpleNamespace(busy=lambda: False)
    cr_fish.install(s, busy, lambda name, value: saved_keys.append(name) or monkeypatch.setenv(name, value))
    monkeypatch.setattr(cr_fish, "check_key", lambda k, **kw: {"credit": 0.0})
    monkeypatch.setattr(cr_fish, "get_voice", lambda k, v, **kw: model(v))
    monkeypatch.setattr(cr_fish, "list_voices", lambda k, src, q, page, **kw: {"voices": [{"id": VOICE, "src": src}],
                                                                             "more": False})
    return app.test_client(), saved_keys


def test_setup_card_checks_and_keeps_the_key_and_model(studio, home):
    client, saved = studio
    got = client.post("/cr-voice/fish", json={"key": "new-fish-key-0123456789", "model": "s2.1-pro-free"}).get_json()
    assert got["saved"] is True and saved == [cr_fish.KEY_ENV] and "balance" in got["message"]
    narration = cr_bridge.load_settings()["narration"]
    assert narration["provider"] == "fish" and narration["model"] == "s2.1-pro-free" and narration["voice"] == VOICE
    assert client.post("/cr-voice/fish", json={"model": "s1"}).status_code == 400
    assert "key" not in json.dumps(cr_fish.setup_row()).lower().replace("voice", "")


def test_choose_voice_lists_and_keeps_a_fish_voice_per_recipe(studio, home):
    client, _ = studio
    got = client.get("/cr-voices?style=jung").get_json()
    assert got["library"] is True and got["provider"] == "fish" and got["voice_id"] == VOICE
    assert client.get("/cr-voices/library?source=shared&search=x").get_json()["voices"][0]["src"] == "shared"
    picked = client.post("/cr-voices/select", json={"voice_id": OTHER, "style": "jung"}).get_json()
    assert picked["saved"] is True and "Jung" in picked["message"]
    cfg = cr_bridge.load_settings()
    assert cfg[cr_fish.PICKS]["jung"]["id"] == OTHER and cfg[cr_fish.SAVED][0]["id"] == OTHER
    assert cr_fish.voice_for_style("jung")["voice"] == OTHER
    assert client.post("/cr-voices/select", json={"voice_id": "nope"}).status_code == 400


def test_other_accounts_keep_their_own_picker(studio, home):
    client, _ = studio
    cr_bridge.save_settings({"narration": {"provider": "original"}})
    assert client.get("/cr-voices").get_json() == {"connected": True, "hub": True}
    assert client.get("/cr-voices/library").get_json() == {"eleven": True}


# ── tools, network, readiness ──────────────────────────────────────────────────────────────────────────────────

def test_tools_quote_fish_audio_and_take_a_voice_for_these_videos():
    row = {"name": "jung", "label": "Jung", "features": {"offered": [], "on": []}}
    cat = {"prices": {"voice": {"per_min": 0.05}}, "narration_voice": {"provider": "fish", "model": "s2.1-pro",
                                                                     "usd_per_min": 0.0135,
                                                                     "usd_per_million_bytes": 15.0}}
    media = tools.media_estimate(cat, row, 10, {})
    assert media["per_video_usd"] == 0.14 and media["voice"]["provider"] == "fish"
    payload = tools.video_payload(row, ["A title"], 10, {}, {"voice": f"https://fish.audio/m/{VOICE}"})
    assert payload["cr_voice"] == {"provider": "fish", "voice": VOICE}
    with pytest.raises(tools.Refused):
        tools.video_payload(row, ["A title"], 10, {}, {"voice": "Charon"})
    assert {"voices", "choose_voice"} <= set(cr_claude_code.FREE_TOOLS) and {"voices", "choose_voice"} <= set(tools.TOOLS)
    enum = tools.TOOLS["resume_video"][2]["properties"]["voice"]["enum"]
    assert "fish" in enum


def test_the_network_check_asks_fish_audio_only_with_its_key(monkeypatch):
    monkeypatch.setenv(cr_fish.KEY_ENV, KEY)
    assert cr_network.wants_from_env(speech_model_missing=False)["fish"] is True
    assert "api.fish.audio" in [h[0] for h in cr_network._hosts({"fish": True})]
    assert "api.fish.audio" not in [h[0] for h in cr_network._hosts({"fish": False})]


def test_the_narration_check_knows_fish_audio(home):
    import cr_readiness
    studio = types.SimpleNamespace(voices=None)
    assert cr_readiness.own_narration(studio, "jung", {}, "needs a key") == (True, "")
    cr_bridge.save_settings({"narration": {"provider": "fish"}})
    ok, why = cr_readiness.own_narration(studio, "jung", {}, "needs a key")
    assert not ok and "Fish Audio voice" in why


def test_a_video_doesnt_start_on_fish_audio_without_a_chosen_voice(home, monkeypatch):
    """With a Kie key the engine's own voice check passes, so the missing Fish Audio voice is said before the start."""
    import cr_readiness
    from tests.test_machine_requirements import preset_studio
    monkeypatch.setattr(cr_readiness, "basic", lambda checks: {"checks": [], "claude_code": {}, "brain_mode": "claudecode"})
    monkeypatch.setattr(cr_readiness, "proof", lambda *a, **k: {})
    voice_row = lambda spec: next(c for c in cr_readiness.preset(preset_studio(()), {}, {"style": "doc", **spec})
                                  ["checks"] if c["key"] == "voice")
    assert voice_row({"title": "T", "minutes": 1})["ready"] is True                       # Setup's voice is chosen
    cr_bridge.save_settings({"costs_accepted": True, "narration": {"provider": "fish"}})
    row = voice_row({"title": "T", "minutes": 1})
    assert row["ready"] is False and "Fish Audio voice" in row["message"]
    assert voice_row({"title": "T", "minutes": 1, "cr_voice": {"provider": "fish", "voice": VOICE}})["ready"] is True


# ── review fixes (2026-10-29) ──────────────────────────────────────────────────────────────────────────────────

def test_text_without_spaces_is_cut_after_its_sentences_and_never_inside_a_character():
    zh = "这是一个关于历史的故事，" * 40 + "它很长。" + "这是一个关于历史的故事，" * 300      # Chinese: no spaces at all
    parts = list(cr_fish.chunks(zh))
    assert len(parts) > 1 and all(len(p) <= cr_fish.PART_CHARS for p in parts) and "".join(parts) == zh
    assert all(p.endswith(("。", "，")) for p in parts[:-1])                 # after a sentence or clause mark
    accents = "x" + "e\u0301" * 1500                                           # é as e + a combining accent
    parts = list(cr_fish.chunks(accents))
    assert "".join(parts) == accents and all(p.startswith("e") for p in parts[1:])
    emoji = "\U0001F469\u200d\U0001F52C" * 1000                               # a woman scientist: three code points
    parts = list(cr_fish.chunks(emoji))
    assert "".join(parts) == emoji and all(p.startswith("\U0001F469") and p.endswith("\U0001F52C") for p in parts)
    hindi = "यह एक कहानी है। " * 300                                              # 3 UTF-8 bytes a letter
    parts = list(cr_fish.chunks(hindi))
    assert all(p.endswith("।") for p in parts)
    assert sum(cr_fish.cost_usd(p, "s2.1-pro") for p in parts) == pytest.approx(     # priced by bytes, not letters
        cr_fish.cost_usd(hindi, "s2.1-pro"), rel=0.01) and cr_fish.cost_usd(hindi, "s2.1-pro") > 2 * len(hindi) * 15e-6


def test_audio_that_arrived_before_studio_closed_is_kept_not_stuck(home):
    """Studio closed between the download's rename and its receipt: the receipt still says sending. The whole audio
    is there, so it is reused (no POST), never an uncertain stop that the creator's yes can't release."""
    folder = home / "audio-fish-voice"
    folder.mkdir()
    part = folder / "part-000.mp3"
    sign = cr_fish.fingerprint("Hi.", VOICE, "s2.1-pro")
    cr_fish.write_receipt(part, {"fingerprint": sign, "state": "sending"})
    part.write_bytes(AUDIO)
    assert cr_fish.synthesize(KEY, "Hi.", VOICE, "s2.1-pro", part, post=Poster(), sleep=lambda s: None)["state"] == "done"
    assert cr_fish.uncertain_parts(home) == []


def test_old_audio_never_stands_in_for_a_new_request(home):
    """A part's earlier audio (another text) is removed before its new request, so if the new answer never arrives
    the old file can't be taken for it, and the uncertain part can be released."""
    folder = home / "audio-fish-voice"
    folder.mkdir()
    part = folder / "part-000.mp3"
    cr_fish.synthesize(KEY, "Old words.", VOICE, "s2.1-pro", part, post=Poster(Answer()), sleep=lambda s: None)
    with pytest.raises(cr_fish.Stop) as stop:
        cr_fish.synthesize(KEY, "New words.", VOICE, "s2.1-pro", part,
                           post=Poster(requests.exceptions.ReadTimeout()), sleep=lambda s: None)
    assert stop.value.uncertain and not part.exists() and cr_fish.uncertain_parts(home) == [part]
    assert cr_fish.release_uncertain(home) == 1


def test_an_answer_that_never_finishes_stops_instead_of_hanging(home, monkeypatch):
    monkeypatch.setattr(cr_fish, "DOWNLOAD_SECONDS", -1)
    part = home / "part-000.mp3"
    with pytest.raises(cr_fish.Stop) as stop:
        cr_fish.synthesize(KEY, "Hi.", VOICE, "s2.1-pro", part, post=Poster(Answer()), sleep=lambda s: None)
    assert stop.value.uncertain and not part.exists()


def test_an_unexpected_error_stops_like_a_voice_stop(home, monkeypatch):
    """Never a plain exception (the engine answers some by trying another paid voice): a Stop, kept for the choices."""
    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(cr_fish, "synthesize", broken)
    with pytest.raises(cr_media.AccountStop, match="disk full"):
        cr_fish.narrate(engine(), "Hello.", home / "audio.mp3", home / "subs.srt", cr_media.fish_voice(home),
                        post=Poster(), sleep=lambda s: None, time_narration=lambda *a: None)
    assert json.loads((home / cr_media.VOICE_FAILED).read_text())["provider"] == "fish"


def test_a_video_with_its_own_fish_voice_is_ready_before_any_recipe_voice(home):
    import cr_readiness
    studio = types.SimpleNamespace(voices=None)
    cr_bridge.save_settings({"narration": {"provider": "fish"}})                 # no Fish voice chosen yet
    assert cr_readiness.own_narration(studio, "jung", {}, "x")[0] is False
    asked = {"cr_voice": {"provider": "fish", "voice": OTHER}}
    assert cr_readiness.own_narration(studio, "jung", {}, "x", asked) == (True, "")
