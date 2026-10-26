"""Why customers' videos stop: Studio sends a privacy-safe failure record with a stopped project, the server keeps it
and counts it for the team (/api/admin/failures), an older Studio's stop gets one plain line about the newer release,
and neither an older server nor an older Studio breaks over any of it."""
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_bridge  # noqa: E402
import cr_failure  # noqa: E402
import cr_mcp  # noqa: E402
import cr_studio  # noqa: E402
import cr_video_cancel  # noqa: E402
from webapp import frontier_studio as studio  # noqa: E402
from webapp import studio_failures  # noqa: E402
from tests.test_frontier_activity import runtime, timeline  # noqa: E402,F401 - the Studio app fixture

TITLE = "Why Ancient Rome Fell Apart"
HOME = "/Users/jane"


def clean(text, **kw):
    return cr_failure.sanitize(text, titles=kw.pop("titles", [TITLE]), secrets=kw.pop("secrets", []),
                               home=kw.pop("home", HOME), names=kw.pop("names", ()), **kw)


# ── the message, cleaned ──────────────────────────────────────────────────────────────────────────────────────────

def test_paths_keep_only_the_files_extension(monkeypatch):
    mac = clean("Error opening /Users/jane/Downloads/ChannelRecipe studio/output/clip-ab12/voice.mp3: No such file")
    assert mac == "Error opening [path].mp3: No such file"
    windows = clean(r"[Errno 28] No space left on device: 'C:\Users\Jane Doe\Videos\My Studio\output\frame.png'",
                    home=r"C:\Users\Jane Doe")
    assert windows == "[Errno 28] No space left on device: '[path].png'"
    other = clean("File /home/bob/studio/make_video.py and D:\\Work\\Studio\\output\\video.mp4 and /tmp/x/y.log")
    assert other == "File [path].py and [path].mp4 and [path].log"
    studio_home = Path(cr_failure.HOME)                         # the Studio's own folder, wherever it is
    assert clean(f"{studio_home}/output/a/b.mp4 failed", home="/nowhere") == "[path].mp4 failed"
    assert clean("/opt/homebrew/bin/ffmpeg: not found") == "…/ffmpeg: not found"    # the computer's own tools
    for words in ("1/3 Script", "and/or", "2/3 Voiceover + visuals"):
        assert clean(words) == words


def test_keys_tokens_and_this_computers_secrets_are_hidden(monkeypatch):
    kie_key = "0123456789abcdef0123456789abcdef"
    monkeypatch.setenv("CR_TEST_PROVIDER_KEY", "s3cret-value-from-env")
    said = cr_failure.sanitize(
        "auth failed: Authorization: Bearer cr_mcp_abcdefghijklmnop12 api_key=abc123 'apiKey': 'xyz789' "
        f"xi-api-key: sk_4f9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c kie {kie_key} anthropic sk-ant-api03-AbCdEf123456 "
        "env s3cret-value-from-env config my-own-secret-123 token=eyJhbGciOiJIUzI1NiJ9.e30.abc AIzaSyA1234567890abcd",
        secrets=["my-own-secret-123", "s3cret-value-from-env"], home=HOME)
    for leaked in ("cr_mcp_", "abc123", "xyz789", "sk_4f9a", kie_key, "sk-ant", "s3cret", "my-own-secret", "eyJhbG",
                   "AIzaSy"):
        assert leaked not in said, (leaked, said)
    assert "[hidden]" in said
    assert len(cr_failure._secrets()) >= 1                     # the environment's keys are found on their own


def test_emails_links_ids_and_addresses_are_taken_out():
    said = clean("Kie task 3f2a1b4c5d6e7f8091a2b3c4d5e6f708 for jane.doe+yt@example.co.uk failed: "
                 "https://tempfile.aiquickdraw.com/x/y.png?sig=1 www.example.com/page crv_0123456789abcdef01234567 "
                 "123e4567-e89b-12d3-a456-426614174000 from 192.168.1.20 aiquickdraw.com/files/a.png")
    for leaked in ("3f2a1b4c", "jane", "example.co.uk", "tempfile", "sig=", "example.com/page", "0123456789abcdef",
                   "123e4567", "192.168", "files/a.png"):
        assert leaked not in said, (leaked, said)
    assert "[email]" in said and "[link]" in said and "[id]" in said and "[ip]" in said and "aiquickdraw.com/…" in said


def test_titles_and_their_folder_names_are_taken_out():
    said = clean(f"Rendering {TITLE} (10 min) failed in /Users/jane/out/why-ancient-rome-fell-apart-1a2b/"
                 "why-ancient-rome-fell-apart.mp4 while writing 'why_ancient_rome_fell_apart' and Why Ancient Rome Fe…")
    assert said == "Rendering [title] (10 min) failed in [path].mp4 while writing '[title]' and [title]…", said
    assert clean(f"Title: {TITLE} (10 min)") == "Title: …"
    slug = clean("/opt/out/why-ancient-rome-fell-apart.mp4 is locked")        # outside a person's folders too
    assert slug == "…/[file].mp4 is locked"
    # a run of the title's short words alone is ordinary text; a longer run of it is the title
    drill = clean("TypeError: 'NoneType' is not a number; This Is Not A…", titles=["This Is Not A Drill"])
    assert drill == "TypeError: 'NoneType' is not a number; [title]…", drill
    assert clean(f"refused {TITLE} today") == "refused [title] today"   # the whole title, not a run of it plus a word


def test_names_and_account_numbers_are_taken_out():
    said = clean("Mail from \"Jane Doe\" <jane@example.com> and Jane Doe <jd@example.com>; user 4821, account_id=99 "
                 "on DESKTOP-AB12CD3 (Janes-MacBook-Pro) for jane", names=cr_failure._names(r"C:\Users\Jane Doe")
                 + ["Janes-MacBook-Pro"])
    for leaked in ("Jane", "jane", "Doe", "4821", "99", "AB12CD3", "MacBook"):
        assert leaked not in said, (leaked, said)
    assert said.count("[email]") == 2 and "user [id]" in said and "[computer]" in said and "[name]" in said
    names = cr_failure._names("/Users/max")                    # a short user name is an ordinary word of a message
    assert "max" not in names and clean("Max retries exceeded", names=names) == "Max retries exceeded"
    assert "admin" not in cr_failure._names("/Users/admin")
    assert cr_failure.classify("Max retries exceeded")["category"] == "blocked_network"


def test_a_providers_answer_keeps_its_status_fields_not_its_request():
    said = clean("kie task failed: {'code': 500, 'msg': 'Internal Error, please try again later', 'data': "
                 "{'prompt': 'A private prompt about my channel', 'callBackUrl': 'https://me.example/hook'}}")
    assert said == "kie task failed: (code: 500; msg: Internal Error, please try again later)"
    assert "prompt" not in clean('{"input": {"text": "my narration"}}')


def test_one_line_of_at_most_240_characters_without_the_studios_advice():
    long = "ElevenLabs refused the voice. " + "x " * 400
    assert len(clean(long)) <= 240 and clean(long).endswith("…")
    assert "\n" not in clean("line one\nline two")
    said = clean(cr_video_cancel.CANCEL_MESSAGE)
    assert said == "Cancelled on this computer. Saved media and caches are kept."
    crash = clean("The local video worker stopped: this computer ran out of memory. Saved work is kept: press Resume "
                  "saved project to carry on from where it stopped. Last message: images: 12/40 done (Copy diagnostics "
                  "in Setup includes the full log.)")
    assert crash == "The local video worker stopped: this computer ran out of memory. Last message: images: 12/40 done"


# ── adversarial messages (review, 2026-10-26): the Studio's cleaner and the server's ─────────────────────────────

def both(text, titles=(TITLE,)):
    """The message as a Studio sends it, and as the server keeps it had a Studio sent it raw."""
    return clean(text, titles=list(titles)), studio_failures.clean_message(text, list(titles))


@pytest.mark.parametrize("message,expected,private", [
    (r"C:\Users\Jane Doe\OneDrive - Contoso Ltd\Videos\cooking\a.mp4 is locked", "[path].mp4 is locked",
     ("Jane", "Contoso", "OneDrive", "cooking", "Videos")),
    ("open /Users/jane/Documents/Jane's Channel/scripts/x.txt failed", "open [path].txt failed",
     ("jane", "Jane", "Channel", "scripts", "x.txt")),
    ("/Users/jane/Movies/Grandma Rose Funeral Tribute Video/x.mp4: No such file", "[path].mp4: No such file",
     ("Grandma", "Rose", "Funeral", "Tribute")),
    ("Permission denied: '/Users/jane/Movies/Grandma Rose Funeral Tribute Video'", "Permission denied: '[path]'",
     ("Grandma", "Funeral", "Video")),
    ("read /Volumes/Jane's Drive/Big Trip 2024/clip.mov: Permission denied", "read [path].mov: Permission denied",
     ("Jane", "Drive", "Trip", "clip")),
    (r"copy \\JANES-PC\Share\Family Videos\x.mp4 failed", "copy [path].mp4 failed", ("JANES", "Share", "Family")),
    ("file:///Users/jane/My%20Videos/Secret Plan/x.mp4 missing", "[path].mp4 missing", ("Secret", "Videos", "jane")),
    ("failed (see /Users/jane/Videos/Channel (old)/x.log) retry", "failed (see [path].log) retry", ("Channel", "old")),
    ("Kie content flagged by the safety system for prompt 'Sarah Johnson at 12 Elm St, Leeds'",
     "Kie content flagged by the safety system for prompt '…'", ("Sarah", "Johnson", "Elm", "Leeds")),
    ("flagged for prompt 'Sarah Johnson' today", "flagged for prompt '…' today", ("Sarah", "Johnson")),
    ("ElevenLabs refused text: My name is John Smith. I live at 4 Park Road", "ElevenLabs refused text: …",
     ("John", "Smith", "Park")),
    ("KeyError: 'The secret recipe grandma never told anyone'", "KeyError: '…'", ("secret", "grandma")),
    ('bad scenes ["John Smith confessed", "the night it rained"] in batch', "bad scenes […] in batch",
     ("John", "confessed", "rained")),
    ("scene 4: Grandma's kitchen at dawn; images: 3/40", "scene 4: …; images: 3/40", ("Grandma", "kitchen")),
    ("Mail for jane doe <jane@example.com> via jane%40gmail.com", "Mail for [email] via [email]",
     ("jane", "doe", "gmail")),
    ("from 2001:db8::1 and fe80::1%en0 and 2001:0db8:85a3:0000:0000:8a2e:0370:7334", "from [ip] and [ip] and [ip]",
     ("2001", "fe80", "db8")),
    ("the reference channel @janedoe_cooks refused", "the reference channel [handle] refused", ("janedoe",)),
])
def test_private_words_never_stay_in_either_cleaner(message, expected, private):
    for said in both(message):
        assert said == expected, (said, message)
        for word in private:
            assert word not in said, (word, said)


@pytest.mark.parametrize("message", [
    "kie: 422 task id is blank", "insufficient credits", "KeyError: 'beats'", "ModuleNotFoundError: No module named 'cv2'",
    "ERROR: [youtube] abc: Sign in to confirm you're not a bot.", "[Errno 28] No space left on device",
    "std::bad_alloc at 00:01:23.45 with -map 0:a:0", "Cannot find module '@anthropic-ai/claude-code'",
    "ConnectionError: HTTPSConnectionPool(host='api.kie.ai', port=443): Max retries exceeded",
    "Command '[…]' returned non-zero exit status 1.", "1/3 Script and/or 2/3 Voiceover + visuals"])
def test_codes_and_short_technical_messages_stay_readable(message):
    assert both(message) == (message, message)


def test_a_providers_answer_keeps_its_code_and_the_server_keeps_what_the_studio_sent():
    sent = clean("kie task failed: {'code': 422, 'msg': 'task id is blank', 'data': ['A prompt about Jane', 'x']}")
    assert sent == "kie task failed: (code: 422; msg: task id is blank)"
    assert clean("Command '['ffmpeg', '-y', '-i', '/Users/jane/My Trip/in.mp4']' returned non-zero exit status 1."
                 ) == "Command '[…]' returned non-zero exit status 1."
    for message in ("Error opening /Users/jane/Jane's Channel/voice.mp3: No such file", "/opt/homebrew/bin/ffmpeg: x",
                    f"Rendering {TITLE} for 'Sarah Johnson at Elm St' at fe80::1 for @jane", sent):
        once = clean(message)
        assert studio_failures.clean_message(once, [TITLE]) == once      # the server never undoes the Studio's work


def test_the_worker_logs_last_line_keeps_only_an_exception(tmp_path):
    log = tmp_path / "worker.log"
    log.write_text("images: 3/40\n  -> scene 4: The old lighthouse keeper looks out to sea\n")
    assert cr_video_cancel.crash_failure(log, 1) == {
        "category": "", "message": "The video worker stopped: it ended unexpectedly (exit code 1)."}
    log.write_text("Traceback (most recent call last):\nplaywright._impl._errors.TargetClosedError: Target page, context "
                   "or browser has been closed\n")
    assert cr_video_cancel.crash_failure(log, 1)["message"].endswith(
        "Last message: TargetClosedError: Target page, context or browser has been closed")
    log.write_text("ValueError: " + "lighthouse " * 40 + "\n")
    said = cr_video_cancel.crash_failure(log, 1)["message"].split("Last message: ")[1]
    assert said.startswith("ValueError: lighthouse") and len(said.split()) <= 13


def test_a_long_title_and_a_long_traceback_are_cleaned_quickly(tmp_path):
    title = " ".join(f"Story{n} About Ancient Engineers" for n in range(20))[:300]           # 300 characters, ~54 words
    frames = "".join(f'  File "/Users/jane/Downloads/ChannelRecipe studio/engine/{title[:40]} {n}.py", line {n}, in '
                     f"step{n}\n    render(scene, '{title[n:n + 60]}')\n" for n in range(120))
    traceback = (f"Traceback for {title} (most recent call last):\n" + frames * 3)[:16000]
    started = time.perf_counter()
    said = cr_failure.sanitize(traceback, titles=[title, title.upper()], secrets=[], home=HOME, names=())
    assert time.perf_counter() - started < 0.5
    assert "Story" not in said and "Engineers" not in said and said.startswith("Traceback for [title]"), said
    started = time.perf_counter()
    job, state = job_state(tmp_path, error=traceback + "\nRuntimeError: boom", title=title)
    found = cr_failure.for_job(tmp_path, state, {"style": "history", "title": title}, job, "", "runtime_failed")
    assert time.perf_counter() - started < 0.5 and found["category"] == "crash"
    started = time.perf_counter()
    assert len(studio_failures.clean_message("[" * 9000 + traceback, [title])) <= 240
    assert time.perf_counter() - started < 0.5


# ── what kind of stop ─────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("message,step,category,provider", [
    ("Kie.ai failed this narration task. Internal Error, please try again later Kie charged nothing for it. Wait a few "
     "minutes and press Resume saved project, or voice this video another way (Gemini through Kie, your own ElevenLabs "
     "account, or your own recording).", "voice", "provider_error", "kie"),
    ("kie image failed after 3 attempts: kie task failed: {'code': 500, 'msg': 'Internal Error'}", "pictures",
     "provider_error", "kie"),
    ("The final render failed (FFmpeg 1). Your generated scenes and voice are saved. Details: Conversion failed!",
     "mux", "tool_error", "none"),
    ("OSError: [Errno 28] No space left on device: '/Users/jane/Downloads/ChannelRecipe studio/output/a.png'",
     "unknown", "disk_full", "none"),
    ("Your Claude account reached its usage limit. Check its reset or usage allowance, then review this saved stage "
     "in setup.", "script", "usage_limit", "claude"),
    ("ERROR: [youtube] abc: Sign in to confirm you're not a bot.", "youtube", "provider_error", "youtube"),
    ("ConnectionError: HTTPSConnectionPool(host='api.kie.ai', port=443): Max retries exceeded (Failed to resolve "
     "'api.kie.ai')", "unknown", "blocked_network", "kie"),
    ("ElevenLabs refused the voice (401 Unauthorized)", "voice", "auth", "elevenlabs"),
    ("HeyGen avatar render timed out", "presenter", "timeout", "other"),
    ("KeyError: 'beats'", "unknown", "crash", "none"),
])
def test_stops_are_classified_by_step_kind_and_service(message, step, category, provider):
    assert cr_failure.classify(message) == {"failed_step": step, "category": category, "provider": provider}


def test_folder_names_and_titles_never_steer_the_classification():
    found = cr_failure.classify("No space left on device: /Users/jane/YouTube Kie ideas/ChannelRecipe studio/x.png",
                                titles=["Why YouTube Videos Fail"])
    assert found["category"] == "disk_full" and found["provider"] == "none"
    titled = cr_failure.classify("Why YouTube Videos Fail stopped: KeyError: 'x'", titles=["Why YouTube Videos Fail"])
    assert titled["provider"] == "none"


def test_the_engines_last_step_names_a_stop_its_message_doesnt():
    lines = ["=== Title: A video  (10 min) ===", "  cached", "=== 3/4  Graphics (Claude designs the scenes) ===  [1:02]",
             "=== ⚠ thumbnail failed, continuing without one ===", "ERROR: boom"]
    assert cr_failure.last_header(lines) == "3/4  Graphics (Claude designs the scenes)"
    assert cr_failure.classify("boom", header=cr_failure.last_header(lines))["failed_step"] == "graphics"
    assert cr_failure.header_step("2/3  Voiceover + documentary visuals — in parallel") == ""


# ── a video job's record ──────────────────────────────────────────────────────────────────────────────────────────

def job_state(tmp_path, **values):
    job = tmp_path / "output" / "a-job"
    job.mkdir(parents=True)
    state = {"state": "error", "done": True, "started": time.time() - 60, "lines": [], "title": TITLE, **values}
    return job, state


def test_a_record_says_the_studio_version_computer_and_recipe(tmp_path, monkeypatch):
    (tmp_path / "cr-release.json").write_text(json.dumps({"version": "5.3-admin.99"}))
    (tmp_path / "styles").mkdir()
    (tmp_path / "styles" / "my-look.json").write_text(json.dumps({"builtin": False}))
    found = cr_failure.record("boom", recipe="history", home=tmp_path)
    assert set(found) == {"failed_step", "category", "provider", "message", "version", "os", "recipe", "source"}
    assert found["version"] == "5.3-admin.99" and found["os"] in ("windows", "mac", "linux") and found["recipe"] == "history"
    assert cr_failure.record("x", recipe="cr_clone_my_channel", home=tmp_path)["recipe"] == "cloned_style"
    assert cr_failure.record("x", recipe="my-look", home=tmp_path)["recipe"] == "custom_style"
    assert [cr_failure.os_family(p) for p in ("win32", "darwin", "linux")] == ["windows", "mac", "linux"]


def test_a_voice_stop_is_read_from_the_voices_own_note(tmp_path):
    job, state = job_state(tmp_path, error="Kie.ai failed this narration task. Internal Error")
    (job / "cr-voice-failed.json").write_text(json.dumps({"provider": "kie", "category": "model_failure",
                                                          "reason": "Internal Error", "at": int(time.time())}))
    found = cr_failure.for_job(tmp_path, state, {"style": "history", "title": TITLE}, job, "crv_" + "a" * 24,
                               "runtime_failed")
    assert (found["failed_step"], found["category"], found["provider"], found["source"]) == (
        "voice", "provider_error", "kie", "video")


def test_an_old_voice_note_never_explains_a_new_stop(tmp_path):
    job, state = job_state(tmp_path, error="KeyError: 'beats'")
    note = job / "cr-voice-failed.json"
    note.write_text(json.dumps({"provider": "kie", "category": "balance", "at": 1}))
    os.utime(note, (1, 1))
    assert cr_failure.for_job(tmp_path, state, {"style": "history"}, job)["category"] == "crash"


def test_a_shorts_stop_uses_its_pipelines_code(tmp_path):
    job, state = job_state(tmp_path, error="Claude's usage limit is reached for now.")
    (job / "short").mkdir()
    (job / "short" / "error.json").write_text(json.dumps({"code": "claude_usage_limit",
                                                          "message": "Claude's usage limit is reached for now."}))
    found = cr_failure.for_job(tmp_path, state, {"style": "cr-shorts-ranking", "title": TITLE}, job)
    assert (found["failed_step"], found["category"], found["provider"], found["source"]) == (
        "script", "usage_limit", "claude", "short")


def test_a_failed_final_render_is_the_mux_step(tmp_path):
    job, state = job_state(tmp_path, error="RuntimeError: Error while filtering",
                           lines=["=== 4/4  Assemble the video (ffmpeg) ==="])
    (job / "cr-render-error.log").write_text("ffmpeg said no")
    assert cr_failure.for_job(tmp_path, state, {"style": "history"}, job)["failed_step"] == "mux"


def test_a_failed_claude_stage_names_claude_and_its_reason(tmp_path):
    project = "crv_" + "b" * 24
    job, state = job_state(tmp_path, error="Claude's connection or service failed. It will not run again automatically.",
                           lines=["=== 3/4  Graphics (Claude designs the scenes) ==="])
    cache = tmp_path / "output" / "cr-claude-cache" / project
    cache.mkdir(parents=True)
    (cache / ("cc_" + "c" * 64 + ".json")).write_text(json.dumps({"state": "failed", "error_code": "network_interrupted"}))
    found = cr_failure.for_job(tmp_path, state, {"style": "documentary"}, job, project)
    assert (found["failed_step"], found["category"], found["provider"]) == ("graphics", "blocked_network", "claude")


def test_a_worker_crash_and_a_cancel_say_what_happened(tmp_path):
    log = tmp_path / "worker.log"
    log.write_text("images: 3/40\nMemoryError\n")
    hint = cr_video_cancel.crash_failure(log, 1)
    assert hint == {"category": "out_of_memory", "message": "The video worker stopped: this computer ran out of memory. "
                                                             "Last message: MemoryError"}
    log.write_text("The Studio that started this video closed, so the video stopped with it.\n")
    assert cr_video_cancel.crash_failure(log, -9)["category"] == "studio_closed"
    assert cr_video_cancel.crash_failure(tmp_path / "missing.log", 1)["category"] == ""
    job, state = job_state(tmp_path, error="worker", cr_failure=hint, lines=["=== 2/3  Voiceover ==="])
    crashed = cr_failure.for_job(tmp_path, state, {}, job)
    assert (crashed["category"], crashed["failed_step"]) == ("out_of_memory", "voice")
    assert crashed["message"] == hint["message"]
    cancelled = cr_failure.for_job(tmp_path, {**state, "state": "cancelled"}, {}, job, error_code="cancelled")
    assert cancelled["category"] == "cancelled"


def test_a_whole_record_holds_nothing_private(tmp_path, monkeypatch):
    project = "crv_" + "d" * 24
    monkeypatch.setenv("CR_TEST_ELEVENLABS_KEY", "el-live-key-0099887766")
    message = (f"ElevenLabs refused {TITLE} for Jane Doe <jane.doe@example.com>: 401 xi-api-key=el-live-key-0099887766 "
               rf"at C:\Users\Jane Doe\Downloads\ChannelRecipe studio\output\{project}\voice.mp3 "
               f"(project {project}, user 4821, op_0123456789abcdef) https://api.elevenlabs.io/v1/tts?key=abc")
    job, state = job_state(tmp_path, error=message, lines=["=== 2/3  Voiceover ==="])
    found = cr_failure.for_job(tmp_path, state, {"style": "cr_clone_janes_channel", "title": TITLE}, job, project)
    text = json.dumps(found, ensure_ascii=False)
    for private in ("Jane", "jane", "Doe", "example.com", "el-live-key", project, "dddd", "op_0123", "4821", "Rome",
                    "Apart", "Downloads", "ChannelRecipe studio", "elevenlabs.io", "janes_channel", "key=abc"):
        assert private not in text, (private, text)
    assert (found["failed_step"], found["category"], found["provider"], found["recipe"]) == (
        "voice", "auth", "elevenlabs", "cloned_style")
    assert "[path].mp3" in found["message"] and "[email]" in found["message"] and "[title]" in found["message"]


def test_a_record_never_stops_the_finish(tmp_path):
    found = cr_failure.for_job(tmp_path, {"lines": 5, "started": "soon", "error": {"odd": 1}}, None, 42)
    assert set(found) >= {"failed_step", "category", "provider", "message"}
    assert cr_failure.from_exception(SystemExit())["category"] == "cancelled"


def test_the_servers_line_is_taken_only_when_its_plain_text():
    assert cr_failure.update_note({"update_note": " A newer   Studio is out. "}) == "A newer Studio is out."
    assert cr_failure.update_note({"update_note": "<b>x</b>"}) == "" and cr_failure.update_note(None) == ""
    assert cr_failure.update_note({"saved": True}) == ""


# ── an older server, a newer Studio ───────────────────────────────────────────────────────────────────────────────

class OldFinish(studio.SafeContract):
    """NativeFinishProject as servers before the failure records had it (extra fields forbidden)."""
    project_id: str
    status: str
    artifacts: list = []
    error_code: str = ""


def mcp_like(monkeypatch, contract, answer=None):
    """cr_bridge's MCP call answered the way the server's MCP tool answers (webapp.mcp_server._studio_production)."""
    from webapp import mcp_server
    sent = []

    def finish(user, req):
        return answer or {"saved": True, "reused": False, "status": req.status, "project_id": req.project_id}

    def rpc(method, params, settings):
        sent.append(params["arguments"]["payload"])
        text = mcp_server._studio_production(None, finish, params["arguments"]["payload"], contract)
        return {"structuredContent": {"result": text}}
    monkeypatch.setattr(mcp_server, "_user_from_request", lambda ctx: {"id": 1, "email": "owner@example.com"})
    monkeypatch.setattr(studio, "require_studio_user", lambda user: user)
    monkeypatch.setattr(cr_mcp, "rpc", rpc)
    monkeypatch.setattr(cr_bridge, "load_settings", lambda: {})
    monkeypatch.setattr(cr_bridge, "workflow_view", lambda result: result)
    return sent


REPORT = {"project_id": "crv_" + "a" * 24, "status": "failed", "artifacts": [], "error_code": "runtime_failed",
          "failure": {"failed_step": "voice", "category": "provider_error", "provider": "kie", "message": "m",
                      "version": "5.3-admin.104", "os": "mac", "recipe": "history", "source": "video"}}


def test_an_older_server_gets_the_same_finish_without_the_record(monkeypatch):
    sent = mcp_like(monkeypatch, OldFinish)
    answer = cr_bridge.finish_project(dict(REPORT))
    assert answer["saved"] and len(sent) == 2
    assert "failure" in sent[0] and sent[1] == {k: v for k, v in REPORT.items() if k != "failure"}


def test_a_current_server_takes_the_record_in_one_call(monkeypatch):
    sent = mcp_like(monkeypatch, studio.NativeFinishProject, {"saved": True, "update_note": "A newer Studio."})
    assert cr_bridge.finish_project(dict(REPORT))["update_note"] == "A newer Studio." and len(sent) == 1


def test_only_the_old_servers_refusal_is_sent_again(monkeypatch):
    calls = []

    def refuse(name, payload):
        calls.append(payload)
        raise cr_mcp.WorkflowError("This project already has its final output metadata.", 409)
    monkeypatch.setattr(cr_bridge, "_mcp_tool", refuse)
    with pytest.raises(cr_mcp.WorkflowError):
        cr_bridge.finish_project(dict(REPORT))
    assert len(calls) == 1
    monkeypatch.setattr(cr_bridge, "_mcp_tool", lambda *a: (_ for _ in ()).throw(RuntimeError("offline")))
    with pytest.raises(RuntimeError, match="offline"):
        cr_bridge.finish_project(dict(REPORT))
    monkeypatch.setattr(cr_bridge, "_mcp_tool", lambda name, payload: calls.append(payload) or
                        (_ for _ in ()).throw(cr_mcp.WorkflowError("Invalid details.")))
    with pytest.raises(cr_mcp.WorkflowError):
        cr_bridge.finish_project({k: v for k, v in REPORT.items() if k != "failure"})


# ── Studio: a stopped video's finish ──────────────────────────────────────────────────────────────────────────────

def test_a_stopped_video_sends_its_record_and_shows_the_newer_studio_line(runtime, monkeypatch):  # noqa: F811
    r = runtime
    r.cfg.update(brain_mode="claudecode", setup_proof={})
    r.studio.mv.job_dir = lambda *a: r.job
    r.studio.mv.final_video = lambda *a: r.job / "video.mp4"
    r.studio.JOBS["example"] = {"state": "queued", "lines": []}
    r.studio.RUNNING["slug"] = "example"
    finished = []
    note = "A newer Studio, Studio 104, is out and fixes many problems like this one."
    monkeypatch.setattr(cr_studio.cr_bridge, "begin_project", lambda *a, **kw: {"id": "crv_" + "1" * 24})
    monkeypatch.setattr(cr_studio.cr_bridge, "finish_project",
                        lambda receipt: finished.append(receipt) or {"saved": True, "update_note": note})
    cells = dict(zip(r.studio._run_spec.__code__.co_freevars, r.studio._run_spec.__closure__))
    if "run_owned_spec" in cells:
        owned = cells["run_owned_spec"].cell_contents
        cells = dict(zip(owned.__code__.co_freevars, owned.__closure__))

    def stop(spec):
        r.studio.JOBS["example"].update(state="error", done=True, started=time.time(),
                                        error="Kie.ai failed this narration task. Internal Error",
                                        lines=["=== 2/3  Voiceover ==="])
        r.studio.RUNNING["slug"] = None
    monkeypatch.setattr(cells["original_run"], "cell_contents", stop)
    r.studio._run_spec({"slug": "example", "title": TITLE, "style": "history", "minutes": 5, "steps": ["video"]})
    report = finished[0]
    assert report["status"] == "failed" and report["error_code"] == "runtime_failed"
    assert report["failure"]["failed_step"] == "voice" and report["failure"]["provider"] == "kie"
    assert report["failure"]["recipe"] == "history" and TITLE not in json.dumps(report)
    state = r.studio.JOBS["example"]
    assert state["error"].endswith(note) and state["lines"][-1] == note
    assert json.loads((r.job / ".cr-finish.json").read_text(encoding="utf-8"))["failure"] == report["failure"]


# ── the server ────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def native(tmp_path, monkeypatch):
    monkeypatch.setattr(studio.db, "IS_PG", False)
    monkeypatch.setattr(studio.db, "DB_PATH", tmp_path / "failures.db")
    monkeypatch.setattr(studio.config, "ADMIN_EMAILS", ["owner@example.com"])
    monkeypatch.setattr(studio, "release_ready", lambda: True)
    owner = {"id": 1, "email": "owner@example.com"}
    proof = {"local_version": "5.3-admin.90", "general_model": "claude-sonnet-5-5", "visual_model": "claude-opus-5-5",
             "claude_installed": True, "claude_authenticated": True, "api_provider": "firstParty", "ffmpeg_ready": True,
             "browser_ready": True, "node_ready": True, "costs_accepted": True, "models_verified": True,
             "media_ready": True}

    def begin(name, title=TITLE, version="5.3-admin.90"):
        req = studio.NativeBegin(begin_id="begin_" + name + "_x" * 4, title=title, style="history",
                                 setup={**proof, "local_version": version})
        return studio.native_begin(owner, req)["project"]["id"]
    return SimpleNamespace(owner=owner, begin=begin, tmp=tmp_path)


def failed(project_id, failure=None, error_code="runtime_failed"):
    return studio.NativeFinishProject(project_id=project_id, status="failed", error_code=error_code,
                                      **({"failure": failure} if failure is not None else {}))


def test_a_stopped_projects_record_is_kept_and_counted_without_ids_emails_or_titles(native):
    one, two, three = native.begin("one"), native.begin("two", "Another title"), native.begin("three", "Third")
    answer = studio.native_finish_project(native.owner, failed(one, {
        "failed_step": "voice", "category": "provider_error", "provider": "kie", "version": "5.3-admin.90",
        "os": "windows", "recipe": "history", "source": "video",
        "message": f"Kie.ai failed this narration task for {TITLE} at C:\\Users\\Jane Doe\\x\\y.mp3 (owner@example.com)"}))
    assert answer["saved"] and answer["status"] == "failed"
    studio.native_finish_project(native.owner, failed(two, {"failed_step": "mux", "category": "tool_error",
                                                           "version": "5.3-admin.104", "os": "mac", "message": "x 12"}))
    studio.native_finish_project(native.owner, failed(two, {"failed_step": "mux", "category": "tool_error",
                                                           "version": "5.3-admin.104", "os": "mac", "message": "x 13"}))
    studio.native_finish_project(native.owner, failed(three))              # an older Studio: no record
    report = studio_failures.summary(14)
    assert report["stopped_projects"] == 2 and report["stops_in_total"] == 3
    assert report["by_step"] == {"mux": 1, "voice": 1} and report["by_provider"] == {"kie": 1, "none": 1}
    assert report["by_os"] == {"windows": 1, "mac": 1} and report["by_version"] == {"5.3-admin.90": 1, "5.3-admin.104": 1}
    assert report["by_recipe"] == {"history": 1, "unknown": 1} and report["by_error_code"] == {"runtime_failed": 2}
    assert report["failed_without_reason"]["projects"] == 1
    assert report["top_messages"][0]["count"] == 1 and len(report["top_messages"]) == 2
    text = json.dumps(report, ensure_ascii=False)
    for private in (one, two, three, "owner@example.com", TITLE, "Jane", '"user_id"', "Another title"):
        assert private not in text, private
    assert "[path].mp3" in text and "[title]" in text and "[email]" in text


def test_numbers_alone_dont_split_a_message_and_a_resumed_project_is_counted(native):
    ids = [native.begin(str(n), f"Title {n}") for n in range(3)]
    for n, project in enumerate(ids):
        studio.native_finish_project(native.owner, failed(project, {"message": f"The video worker stopped: exit code {n}"}))
    studio.native_finish_project(native.owner, studio.NativeFinishProject(
        project_id=ids[0], status="done", artifacts=[{"name": "video.mp4", "sha256": "e" * 64, "media_type": "video/mp4"}]))
    report = studio_failures.summary(14)
    assert report["top_messages"][0]["count"] == 3 and report["resumed_and_finished_later"] == 1


def test_an_odd_or_newer_record_never_refuses_the_finish(native):
    project = native.begin("odd")
    weird = {"failed_step": "teleport", "category": "x" * 300, "provider": 7, "message": "m" * 5000, "version": "../..",
             "os": "beos", "recipe": "Has Spaces!", "source": "moon", "something_new": {"a": 1}}
    assert studio.native_finish_project(native.owner, failed(project, weird))["saved"]
    row = studio_failures.summary(14)
    assert row["by_step"] == {"other": 1} and row["by_category"] == {"other": 1} and row["by_provider"] == {"other": 1}
    assert row["by_os"] == {"other": 1} and row["by_recipe"] == {"other": 1} and row["by_source"] == {"other": 1}
    assert row["by_version"] == {"unknown": 1} and len(row["top_messages"][0]["message"]) <= 240
    assert studio.NativeFinishProject(project_id=project, status="failed", failure="not a record").failure.category == "unknown"


def test_an_older_studio_without_records_still_finishes_and_succeeds(native):
    project = native.begin("plain", version=studio.VERSION)
    answer = studio.native_finish_project(native.owner, failed(project))
    assert answer == {"saved": True, "reused": False, "status": "failed", "project_id": project}
    done = native.begin("done", "Done title", studio.VERSION)
    answer = studio.native_finish_project(native.owner, studio.NativeFinishProject(
        project_id=done, status="done", artifacts=[{"name": "video.mp4", "sha256": "e" * 64, "media_type": "video/mp4"}]))
    assert "update_note" not in answer
    with pytest.raises(HTTPException):
        studio.native_finish_project(native.owner, studio.NativeFinishProject(project_id=done, status="done", artifacts=[],
                                                                              error_code="runtime_failed"))


def test_the_newer_studio_line_follows_the_release_rules(native, monkeypatch):
    supported = studio.SUPPORTED_RELEASES[0]
    older = studio.APPROVED_RELEASES[studio.APPROVED_RELEASES.index(studio.MIN_SUPPORTED_RELEASE) - 1]
    assert studio.update_note(studio.VERSION) == "" and studio.update_note("5.3-admin.9999") == ""
    assert studio.update_note(None) == "" and studio.update_note("") == ""
    if supported != studio.VERSION:
        line = studio.update_note(supported)
        assert "can update between videos" in line and "Studio " + studio.VERSION.rsplit(".", 1)[-1] in line
        assert "\n" not in line and "must" not in line.lower()
    assert "can finish its saved videos" in studio.update_note(older)
    project = native.begin("note", version=supported)
    if supported != studio.VERSION:
        assert studio.native_finish_project(native.owner, failed(project))["update_note"] == studio.update_note(supported)
    cancelled = native.begin("cancelled", "Cancelled title", supported)
    assert "update_note" not in studio.native_finish_project(native.owner, failed(cancelled, error_code="cancelled"))


def test_the_record_version_wins_over_the_projects_pinned_one(native):
    project = native.begin("updated", version=studio.SUPPORTED_RELEASES[0])
    answer = studio.native_finish_project(native.owner, failed(project, {"version": studio.VERSION}))
    assert "update_note" not in answer


def test_a_stop_without_a_project_is_kept_a_few_times_an_hour(native, monkeypatch):
    monkeypatch.setattr(studio_failures, "REPORTS_PER_HOUR", 2)
    record = studio.FailureReport(failure={"failed_step": "cloud", "source": "cloud", "message": "Preparing stopped"})
    assert [studio_failures.record_report(1, record.failure) for _ in range(3)] == [True, True, False]
    assert studio_failures.summary(14)["by_source"] == {"cloud": 2}


def test_studio_clouds_own_failures_are_counted_and_cleaned(native):
    from webapp import cloud_jobs
    cloud_jobs.initialize()
    with studio.db._conn() as conn:
        for n, error in enumerate(("stopped at its time limit", f"render of {TITLE} failed at /home/u/x/y.mp4",
                                   "stopped at its time limit")):
            conn.execute("""INSERT INTO cloud_jobs (id,user_id,slug,title,machine,status,token_hash,reserved_cents,
                max_minutes,cents_per_min,error,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (f"job{n}", 1, "s", TITLE, "gpu", "failed", "h", 0, 1, 1, error, time.time()))
    cloud = studio_failures.summary(14)["cloud"]
    assert cloud["failed"] == 3 and cloud["by_category"] == {"timeout": 2, "provider_error": 1}
    assert cloud["top_messages"][0] == {"message": "stopped at its time limit", "count": 2}
    assert TITLE not in json.dumps(cloud) and "/home/u" not in json.dumps(cloud)


def test_only_admins_read_the_counts(native, monkeypatch):
    from fastapi.testclient import TestClient
    import webapp.server as server
    project = native.begin("admin")
    studio.native_finish_project(native.owner, failed(project, {"failed_step": "voice", "message": "boom"}))
    monkeypatch.setattr(server.config, "ADMIN_EMAILS", ["owner@example.com"])
    monkeypatch.setattr(server, "require_user", lambda request: {"id": 2, "email": "customer@example.com"})
    assert TestClient(server.app).get("/api/admin/failures").status_code == 403
    monkeypatch.setattr(server, "require_user", lambda request: native.owner)
    res = TestClient(server.app).get("/api/admin/failures?days=400")
    assert res.status_code == 200 and res.headers["cache-control"] == "private, no-store"
    assert res.json()["days"] == 90 and res.json()["by_step"] == {"voice": 1}
    assert "owner@example.com" not in res.text and project not in res.text


def test_a_studio_reports_a_stop_without_a_project_over_its_own_route(native, monkeypatch):
    from fastapi.testclient import TestClient
    import webapp.server as server
    monkeypatch.setattr(studio.db, "get_user_by_mcp_api_key",
                        lambda key: {**native.owner, "plan": "max"} if key == "cr_mcp_fixture" else None)
    monkeypatch.setattr(studio.milestones, "seen", lambda user_id: None)
    res = TestClient(server.app).post("/api/studio/failures", headers={"Authorization": "Bearer cr_mcp_fixture"},
                                      json={"failure": {
        "failed_step": "cloud", "category": "provider_error", "source": "cloud", "version": studio.SUPPORTED_RELEASES[0],
        "message": "Preparing it here stopped"}})
    assert res.status_code == 200 and res.json()["saved"] is True
    assert studio_failures.summary(14)["by_source"] == {"cloud": 1}


def test_the_privacy_page_says_what_a_failure_report_holds():
    page = (Path(__file__).parents[1] / "webapp" / "static" / "privacy.html").read_text(encoding="utf-8")
    said = page.split("Studio failure reports")[1].split("</p>")[0]
    assert "file paths" in said and "keys" in said and "never contains" in said and "Windows, Mac or Linux" in said


def test_records_older_than_the_longest_report_are_taken_out(native, monkeypatch):
    project = native.begin("old")
    studio.native_finish_project(native.owner, failed(project, {"message": "an old stop"}))
    with studio.db._conn() as conn:
        conn.execute("UPDATE frontier_failures SET updated_at=?", (time.time() - (studio_failures.MAX_DAYS + 1) * 86400,))
    monkeypatch.setattr(studio_failures, "_pruned", {})                     # an hour later
    assert studio_failures.record_report(1, studio_failures.FailureRecord(message="a new stop"))
    with studio.db._conn() as conn:
        kept = [r["message"] for r in conn.execute("SELECT message FROM frontier_failures").fetchall()]
    assert kept == ["a new stop"]


def test_the_table_is_made_once_at_startup_and_postgres_workers_take_turns(monkeypatch):
    import inspect
    from contextlib import contextmanager
    import webapp.server as server
    assert "_studio_failures.initialize()" in inspect.getsource(server._startup_tasks)
    said = []

    class Conn:                                                 # no database: what would be said to one
        def execute(self, sql, params=()):
            said.append(" ".join(sql.split()))
            return self

    @contextmanager
    def conn():
        yield Conn()
    monkeypatch.setattr(studio_failures, "_ready", set())
    monkeypatch.setattr(studio_failures, "_pruned", {})
    monkeypatch.setattr(studio_failures.db, "IS_PG", True)
    monkeypatch.setattr(studio_failures.db, "DATABASE_URL", "postgresql://example.invalid/none")
    monkeypatch.setattr(studio_failures.db, "_conn", conn)
    studio_failures.initialize()
    studio_failures.initialize()
    assert said[0] == "SELECT pg_advisory_xact_lock(726346123)"
    assert sum(s.startswith("CREATE TABLE IF NOT EXISTS frontier_failures") for s in said) == 1
    assert said[-1] == "DELETE FROM frontier_failures WHERE updated_at<%s"
