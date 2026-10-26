"""Kie narrates with Gemini voices only (owner, 2026-10-26: "We should never use that turbo model again because it keeps
failing ... Kie will just make them use the Gemini voice models ... If they don't want that, they can just add their
ElevenLabs key.").

Kie's ElevenLabs Turbo 2.5 was Kie's DEFAULT narration model: every Kie customer who never picked a model narrated
through it, it answered "Internal Error" three times in a row and stopped a customer's video (2026-10-25), and Choose
voice had no list for it, so the button sent him back to setup. Now Kie's narration is its Gemini TTS, a saved Kie
ElevenLabs choice is read by the Gemini voice of the same kind with one plain note (never a stop), and ElevenLabs is
the creator's own key."""
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "local_runtime"))
import cr_media as media

GEMINI = "google/gemini-3-8-flash-tts"
TURBO = "elevenlabs/text-to-speech-turbo-2-5"
JAMES = "EkK5I93UQWFDigLMpZcX"                    # Kie's old default ElevenLabs voice (a man's)
LAURA = "FGY2WhTYpPnrIDTdsKH5"                    # a woman's voice on Kie's ElevenLabs list


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "_native_images", None)
    monkeypatch.setattr(media.cr_bridge, "SETTINGS", tmp_path / ".cr-connection.json")
    monkeypatch.setenv("KIE_API_KEY", "customer-test-key")
    monkeypatch.setattr(media.time, "sleep", lambda *_: None)
    monkeypatch.setattr(media, "download", lambda url, dest: Path(dest).write_bytes(b"generated media"))
    return tmp_path


def save(**settings):
    media.cr_bridge.save_settings({"costs_accepted": True, **settings})


def test_the_catalog_has_no_kie_elevenlabs_narration_and_gemini_is_the_default():
    catalog = json.loads((ROOT / "webapp/frontier/providers.json").read_text(encoding="utf-8"))
    kie = catalog["kie"]
    assert kie["models"]["narration"] == GEMINI
    assert [m["id"] for m in kie["narration_models"]] == [GEMINI]
    assert not any(m["id"].startswith("elevenlabs/") for m in kie["narration_models"])
    gemini = kie["narration_models"][0]
    assert kie["default_voice"] in gemini["voice_names"] and gemini["default_voice"] in gemini["voice_names"]
    assert len(gemini["voice_names"]) == 70 and set(gemini["voice_groups"]) == set(gemini["voice_names"])
    # every voice has ChannelRecipe's own free sample (the account's voice_samples, channelrecipe.com/static/voices)
    hosted = json.loads((ROOT / "webapp/static/voices/manifest.json").read_text(encoding="utf-8"))[GEMINI]
    assert set(hosted) == set(gemini["voice_names"])
    assert set(media.GEMINI_STAND_INS.values()) <= set(gemini["voice_names"])


@pytest.mark.parametrize("voice,expected", [
    (JAMES, "Charon"), ("", "Charon"), (LAURA, "Kore"), ("Rachel", "Kore"), ("21m00Tcm4TlvDq8ikWAM", "Kore"),
    ("TX3LPaxmHKxFdv7VOQHJ", "Charon"), ("someOwnCloneId1234567", "Charon")])
def test_a_saved_kie_elevenlabs_voice_is_read_by_the_gemini_voice_of_its_kind(hub, voice, expected):
    save(narration={"provider": "kie", "model": TURBO, **({"voice": voice} if voice else {})})
    got = media.profile("narration")
    assert got["provider"] == "kie" and got["model"] == GEMINI and got["voice"] == expected
    assert got["moved_from"]["model"] == TURBO
    note = media.kie_move_note(got)
    assert note.startswith("Kie now narrates with Gemini voices only") and f"Gemini voice {expected}" in note
    assert "your own ElevenLabs key" in note


def test_a_media_account_voice_saved_as_an_elevenlabs_id_moves_too(hub):
    save(media_hub={"provider": "kie", "tasks": sorted(media.TASKS), "voice": JAMES})
    got = media.profile("narration")
    assert got["voice"] == "Charon" and "model" not in got and "Your voice James" in media.kie_move_note(got)
    assert media.profile("images")["provider"] == "kie", "images keep the media account"


def test_gemini_choices_are_left_as_they_are(hub):
    save(narration={"provider": "kie", "model": GEMINI, "voice": "Puck"})
    assert media.profile("narration") == {"provider": "kie", "model": GEMINI, "voice": "Puck", "tasks": ["narration"]}
    save(media_hub={"provider": "kie", "tasks": ["narration"]})
    assert media.profile("narration") == {"provider": "kie", "tasks": ["narration"]}
    save(narration={"provider": "kie", "model": GEMINI, "voice": "puck"})
    assert media.profile("narration")["voice"] == "Puck" and "moved_from" not in media.profile("narration")
    assert media.migrate_saved_voice() == "", "a Gemini name in another case is no move and needs no note"


def test_studio_saves_the_move_once_with_one_plain_note(hub):
    save(narration={"provider": "kie", "model": TURBO, "voice": LAURA},
         media_hub={"provider": "kie", "tasks": ["images", "narration"], "voice": JAMES})
    note = media.migrate_saved_voice()
    saved = media.cr_bridge.load_settings()
    assert saved["narration"] == {"provider": "kie", "model": GEMINI, "voice": "Kore"}
    assert saved["media_hub"] == {"provider": "kie", "tasks": ["images", "narration"], "voice": "Charon"}
    assert saved["voice_notice"] == note and "Gemini voice Kore" in note
    assert media.migrate_saved_voice() == "", "moved once"
    assert media.cr_bridge.load_settings()["voice_notice"] == note, "the note stays until the creator picks a voice"


OTHER = {"key": "cr_mcp_" + "k" * 30, "url": "https://channelrecipe.com", "brain_mode": "claudecode",
         "costs_accepted": True, "images": {"provider": "kie", "model": "google/nano-banana", "fallbacks": ["x"]},
         "setup_proof": {"style": "3d", "nested": [1, 2, {"a": "b"}]}, "a_key_from_the_future": "kept"}


def test_the_move_is_idempotent_and_keeps_every_other_key(hub):
    settings = json.loads(json.dumps(OTHER))
    settings["narration"] = {"provider": "kie", "model": TURBO, "voice": JAMES, "extra": "kept too"}
    settings["media_hub"] = {"provider": "kie", "tasks": ["images", "narration"], "voice": LAURA, "note": 1}
    media.cr_bridge.SETTINGS.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    note = media.migrate_saved_voice()
    after = json.loads(media.cr_bridge.SETTINGS.read_text(encoding="utf-8"))
    assert {k: v for k, v in after.items() if k not in ("narration", "media_hub", "voice_notice")} == OTHER
    assert after["narration"] == {"provider": "kie", "model": GEMINI, "voice": "Charon", "extra": "kept too"}
    assert after["media_hub"] == {"provider": "kie", "tasks": ["images", "narration"], "voice": "Kore", "note": 1}
    assert after["voice_notice"] == note
    first = media.cr_bridge.SETTINGS.read_bytes()
    assert media.migrate_saved_voice() == "" and media.cr_bridge.SETTINGS.read_bytes() == first, "twice: no change"
    assert [p.name for p in hub.iterdir() if p.name.endswith(".tmp")] == []


def test_a_file_that_cannot_be_replaced_stays_exactly_as_it_was(hub, monkeypatch):
    media.cr_bridge.SETTINGS.write_text(json.dumps({**OTHER, "narration": {"provider": "kie", "model": TURBO}}),
                                        encoding="utf-8")
    before = media.cr_bridge.SETTINGS.read_bytes()
    tries = []

    def locked(src, dst):
        tries.append(dst)
        raise PermissionError(13, "The process cannot access the file because it is being used")
    monkeypatch.setattr(media.os, "replace", locked)
    assert media.migrate_saved_voice() == ""
    assert media.cr_bridge.SETTINGS.read_bytes() == before and len(tries) == 8
    assert [p.name for p in hub.iterdir() if p.name.endswith(".tmp")] == [], "no temp file left behind"
    assert media.profile("narration")["voice"] == "Charon", "read the same way until the file can be written"


def test_a_file_windows_holds_for_a_moment_is_written_on_a_later_try(hub, monkeypatch):
    media.cr_bridge.SETTINGS.write_text(json.dumps({**OTHER, "narration": {"provider": "kie", "model": TURBO}}),
                                        encoding="utf-8")
    real, tries = media.os.replace, []

    def busy_then_free(src, dst):
        tries.append(1)
        if len(tries) < 3:
            raise PermissionError(13, "being used by another process")
        return real(src, dst)
    monkeypatch.setattr(media.os, "replace", busy_then_free)
    assert media.migrate_saved_voice() and len(tries) == 3
    assert json.loads(media.cr_bridge.SETTINGS.read_text(encoding="utf-8"))["narration"]["model"] == GEMINI


def test_a_video_whose_narration_was_done_before_the_update_keeps_it(hub, monkeypatch):
    """Studio updates and restarts only while idle, so no video narrates across the switch. A stopped video whose
    voice was finished (audio.mp3 + subs.srt) keeps it: nothing is made again."""
    save(narration={"provider": "kie", "model": TURBO, "voice": JAMES})
    media.migrate_saved_voice()
    monkeypatch.setattr(media.requests, "post", lambda *a, **k: pytest.fail("no new narration task"))
    job = hub / "stopped-video"
    job.mkdir()
    (job / "audio.mp3").write_bytes(b"james, made before the update")
    (job / "subs.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    engine = SimpleNamespace(log=lambda *_: None)
    voiceover = media.make_voiceover(engine, lambda *a: pytest.fail("not the engine's own voice"))
    assert voiceover("Hello world.", job) == (job / "audio.mp3", job / "subs.srt")
    assert (job / "audio.mp3").read_bytes() == b"james, made before the update"


def test_a_video_stopped_halfway_through_its_voice_is_voiced_wholly_by_gemini(hub, monkeypatch):
    """A video stopped while its Turbo voice was half made: on Resume every part is read by the Gemini voice, so a
    video never mixes two voices (the old part is left unused)."""
    save(narration={"provider": "kie", "model": TURBO, "voice": JAMES})
    media.migrate_saved_voice()
    creates = kie_tasks(monkeypatch)
    monkeypatch.setattr(media, "narration_audio", lambda path: [0.0])
    joined = []

    def ffmpeg(args, **kwargs):
        if "concat" in args:
            joined.append((Path(args[args.index("-i") + 1])).read_text(encoding="utf-8"))
        Path(args[-1]).write_bytes(b"mp3")
    monkeypatch.setattr(media.subprocess, "run", ffmpeg)
    folder = hub / "audio-hub-voice"
    folder.mkdir()
    (folder / "part-000.mp3").write_bytes(b"james, before the stop")
    script = ("A sentence of the story. " * 120).strip()            # about 3,000 characters: two parts
    media.narrate(voice_engine([]), script, hub / "audio.mp3", hub / "subs.srt")
    assert [c["model"] for c in creates] == [GEMINI, GEMINI]
    assert {c["input"]["speakers"][0]["voice_name"] for c in creates} == {"Charon"}
    assert joined == ["file 'part-000.wav'\nfile 'part-001.wav'\n"]


def test_other_accounts_are_never_touched(hub):
    save(narration={"provider": "atlas", "model": "elevenlabs/v3/text-to-speech", "voice": JAMES})
    assert media.migrate_saved_voice() == ""
    assert media.profile("narration")["voice"] == JAMES
    save(narration={"provider": "original"})
    assert media.migrate_saved_voice() == "" and media.profile("narration") == {}


def kie_tasks(monkeypatch):
    creates = []

    def post(url, json=None, **kwargs):
        creates.append(json)
        return SimpleNamespace(status_code=200, json=lambda: {"code": 200, "data": {"taskId": f"task-{len(creates)}"}})

    def get(url, params=None, **kwargs):
        body = {"code": 200, "data": {"state": "success", "creditsConsumed": 0.1,
                                      "resultJson": json.dumps({"resultUrls": ["https://files.example/voice.wav"]})}}
        return SimpleNamespace(status_code=200, json=lambda: body)
    monkeypatch.setattr(media.requests, "post", post)
    monkeypatch.setattr(media.requests, "get", get)
    return creates


def voice_engine(lines):
    words = [SimpleNamespace(word="Hello", start=0.1, end=0.4), SimpleNamespace(word="world.", start=0.5, end=0.8)]
    return SimpleNamespace(log=lines.append,
                           _get_whisper=lambda: SimpleNamespace(transcribe=lambda *a, **k: ([SimpleNamespace(words=words)], None)),
                           _srt_ts=lambda n: "00:00:00," + str(int(n * 1000)).zfill(3),
                           _script_words_into_srt=lambda *a: None)


def test_the_next_video_narrates_with_gemini_and_says_so_never_a_stop(hub, monkeypatch):
    """The customer's saved Turbo choice: his next video must not fail on it."""
    save(narration={"provider": "kie", "model": TURBO, "voice": JAMES})
    creates = kie_tasks(monkeypatch)
    monkeypatch.setattr(media, "narration_audio", lambda path: [0.0])
    monkeypatch.setattr(media.subprocess, "run", lambda args, **k: Path(args[-1]).write_bytes(b"mp3"))   # Gemini: wav
    lines = []
    media.narrate(voice_engine(lines), "Hello world.", hub / "audio.mp3", hub / "subs.srt")
    assert [c["model"] for c in creates] == [GEMINI]
    assert creates[0]["input"]["speakers"][0]["voice_name"] == "Charon"
    assert any(line.startswith("Kie now narrates with Gemini voices only") for line in lines)
    assert (hub / "audio.mp3").read_bytes() == b"mp3" and "00:00:00,100" in (hub / "subs.srt").read_text()


def test_a_stopped_videos_own_kie_elevenlabs_choice_is_read_by_gemini(hub, monkeypatch):
    save(narration={"provider": "original"})
    job = hub / "video"
    job.mkdir()
    (job / media.VOICE_CHOICE).write_text(json.dumps({"provider": "kie", "model": TURBO, "voice": LAURA}))
    chosen = media.job_voice(job)
    assert chosen["model"] == GEMINI and chosen["voice"] == "Kore" and chosen["moved_from"]["model"] == TURBO


def test_no_new_task_is_ever_sent_to_kies_elevenlabs(hub, monkeypatch):
    """A last guard in run(): even a caller that names the old model gets the Gemini voice of its kind."""
    save(narration={"provider": "kie", "model": GEMINI})
    creates = kie_tasks(monkeypatch)
    media.run("narration", {"text": "Hello there.", "voice": LAURA}, hub / "voice.mp3", model=TURBO)
    assert creates[0]["model"] == GEMINI
    assert creates[0]["input"]["speakers"][0]["voice_name"] == "Kore"
    assert creates[0]["input"]["dialogue_turns"][0]["text"] == "Hello there."
    assert media.narration_model("kie", TURBO)["id"] == GEMINI


def test_no_public_code_names_a_kie_elevenlabs_model():
    """Confirms at the source that no Studio path still sends narration to Kie's ElevenLabs."""
    pattern = re.compile(r"elevenlabs/(text-to-speech|text-to-dialogue|eleven)", re.I)
    found = []
    for path in sorted((ROOT / "local_runtime").rglob("*")):
        if path.suffix in (".py", ".js", ".html", ".json") and path.is_file():
            for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if pattern.search(line):
                    found.append(f"{path.name}:{n}")
    assert found == []
    catalog = (ROOT / "webapp/frontier/providers.json").read_text(encoding="utf-8")
    kie = json.loads(catalog)["kie"]
    assert "elevenlabs/" not in json.dumps({k: kie[k] for k in ("models", "narration_models", "default_voice")})


def test_setup_and_create_say_kie_narrates_with_gemini():
    setup = (ROOT / "local_runtime/cr_setup.html").read_text(encoding="utf-8")
    assert "Turbo 2.5" not in setup and "ElevenLabs through this model account" not in setup
    assert "Kie narrates with Gemini voices. For ElevenLabs voices, add your own ElevenLabs key" in setup
    assert media.KIE_NOTE == "Kie narrates with Gemini voices. For ElevenLabs voices, add your own ElevenLabs key."
    page = (ROOT / "local_runtime/cr_workspace.js").read_text(encoding="utf-8")
    assert "location.href = '/cr-setup'" not in page, "Choose voice never sends a connected creator back to setup"
    assert 'id="crw-narration-note"' in (ROOT / "local_runtime/cr_workspace.html").read_text(encoding="utf-8")
