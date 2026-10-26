"""Media retries that can't pay twice no longer stop a video (cr_media, recover-106, 2026-10-26): a task Kie is still
making, an answer Studio couldn't read, a finished file that didn't download, a clip Kie failed for 0 credits and a
request Kie refused before making anything. Anything that may have been charged still waits for the creator."""
import itertools
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_media as media
import cr_recover as recover

DONE = {"code": 200, "data": {"state": "success", "resultJson": json.dumps({"resultUrls": ["https://files.example/x.png"]})}}
WORKING = {"code": 200, "data": {"state": "generating"}}


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "_native_images", None)
    monkeypatch.setattr(media.cr_bridge, "SETTINGS", tmp_path / ".cr-connection.json")
    media.cr_bridge.save_settings({"costs_accepted": True, "media_hub": {"provider": "kie", "tasks": sorted(media.TASKS)}})
    monkeypatch.setenv("KIE_API_KEY", "customer-test-key")
    pauses = []
    monkeypatch.setattr(media.time, "sleep", pauses.append)
    monkeypatch.setattr(media, "download", lambda url, dest: Path(dest).write_bytes(b"generated media"))
    return SimpleNamespace(path=tmp_path, pauses=pauses)


def response(body, status=200):
    return SimpleNamespace(status_code=status, json=lambda: body)


def creating(monkeypatch, *, answer=None):
    creates = []

    def create(url, **kw):
        creates.append(kw["json"])
        return answer(len(creates)) if answer else response({"code": 200, "data": {"taskId": "task-" + str(len(creates))}})
    monkeypatch.setattr(media.requests, "post", create)
    return creates


def polling(monkeypatch, answers):
    """Each poll gets the next answer; the last one repeats."""
    seen = []

    def poll(url, **kw):
        seen.append(kw["params"]["taskId"])
        answer = answers[min(len(seen) - 1, len(answers) - 1)]
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(media.requests, "get", poll)
    return seen


def test_a_task_kie_is_still_making_after_30_minutes_is_checked_on_not_stopped(hub, monkeypatch):
    creates = creating(monkeypatch)
    clock = itertools.count(0, 300)                           # every check five minutes later
    monkeypatch.setattr(media.time, "monotonic", lambda: next(clock))
    polls = polling(monkeypatch, [response(WORKING)] * 10 + [response(DONE)])
    said = []
    assert media.run("motion", {"prompt": "slow push"}, hub.path / "clip.mp4", log=said.append)
    assert len(creates) == 1 and len(polls) == 11 and set(polls) == {"task-1"}
    told = [line for line in said if "still making task task-1" in line]
    assert len(told) == 1 and "nothing is paid again" in told[0]
    assert media.SLOW_POLL in hub.pauses, "checks less often once it's slow"


def test_a_task_still_pending_past_studios_patience_stops_and_resume_checks_the_same_task(hub, monkeypatch):
    creates = creating(monkeypatch)
    clock = itertools.count(0, 900)
    monkeypatch.setattr(media.time, "monotonic", lambda: next(clock))
    polling(monkeypatch, [response(WORKING)])
    with pytest.raises(media.AccountStop, match="still pending"):
        media.run("images", {"prompt": "a cat"}, hub.path / "cat.png")
    polls = polling(monkeypatch, [response(DONE)])
    media.run("images", {"prompt": "a cat"}, hub.path / "cat.png")
    assert len(creates) == 1 and polls == ["task-1"]


def test_a_callers_own_limit_is_kept(hub, monkeypatch):
    creating(monkeypatch)
    clock = itertools.count(0, 100)
    monkeypatch.setattr(media.time, "monotonic", lambda: next(clock))
    polls = polling(monkeypatch, [response(WORKING)])
    with pytest.raises(media.AccountStop, match="still pending"):
        media.run("narration", {"text": "Hi", "voice": "Rachel"}, hub.path / "v.mp3", timeout_s=180)
    assert len(polls) <= 2


@pytest.mark.parametrize("unreadable", [
    response({"code": 500, "msg": "server error"}),             # Kie's error inside a 200
    response(ValueError("half a reply")),                       # not JSON
    response({"code": 200, "data": {"state": "success", "resultJson": "{}"}}),     # done, but no link yet
    response({"code": 200, "data": "oops"})])
def test_an_answer_studio_couldnt_read_is_asked_again(hub, monkeypatch, unreadable):
    if isinstance(unreadable.json(), Exception):
        bad = unreadable.json()
        unreadable = SimpleNamespace(status_code=200, json=lambda: (_ for _ in ()).throw(bad))
    creates = creating(monkeypatch)
    polls = polling(monkeypatch, [unreadable, unreadable, response(DONE)])
    said = []
    assert media.run("images", {"prompt": "a dog"}, hub.path / "dog.png", log=said.append)
    assert len(creates) == 1 and polls == ["task-1"] * 3
    assert hub.pauses[:2] == list(media.UNREADABLE_WAITS[:2]) and "couldn't read" in said[1]


def test_answers_that_stay_unreadable_stop_with_the_same_task_kept(hub, monkeypatch):
    creates = creating(monkeypatch)
    polls = polling(monkeypatch, [response({"code": 200, "data": {"state": "success", "resultJson": "{}"}})])
    with pytest.raises(media.AccountStop, match="returned no media"):
        media.run("images", {"prompt": "a dog"}, hub.path / "dog.png")
    assert len(polls) == len(media.UNREADABLE_WAITS) + 1 and len(creates) == 1
    receipt = json.loads(next((hub.path / ".cr-tasks").glob("*.json")).read_text())
    assert receipt["status"] == "pending" and receipt["id"] == "task-1"


def test_a_finished_file_that_didnt_download_is_fetched_again(hub, monkeypatch):
    creates = creating(monkeypatch)
    polls = polling(monkeypatch, [response(DONE)])
    tries = []

    def flaky(url, dest):
        tries.append(url)
        if len(tries) < 3:
            raise media.TransferFailed("The file transfer stopped. Resume to fetch the existing task.")
        Path(dest).write_bytes(b"generated media")
    monkeypatch.setattr(media, "download", flaky)
    media.run("images", {"prompt": "a fox"}, hub.path / "fox.png")
    assert len(creates) == 1 and len(tries) == 3 and len(polls) == 3, "each try asks for the task (a fresh link)"
    assert hub.pauses == list(media.DOWNLOAD_WAITS[:2])


def test_a_file_that_never_downloads_stops_without_sending_the_task_again(hub, monkeypatch):
    creates = creating(monkeypatch)
    polling(monkeypatch, [response(DONE)])
    monkeypatch.setattr(media, "download", lambda url, dest: (_ for _ in ()).throw(
        media.TransferFailed("The generated file could not be downloaded.")))
    with pytest.raises(media.TransferFailed):
        media.run("images", {"prompt": "a fox"}, hub.path / "fox.png")
    assert len(creates) == 1
    receipt = json.loads(next((hub.path / ".cr-tasks").glob("*.json")).read_text())
    assert receipt["status"] == "generated" and receipt["url"].startswith("https://")
    for problem in ("larger than Studio's 512 MB", "empty media file"):        # not a transfer: never fetched again
        monkeypatch.setattr(media, "download", lambda url, dest, p=problem: (_ for _ in ()).throw(media.AccountStop(p)))
        with pytest.raises(media.AccountStop, match=problem):
            media.run("images", {"prompt": "a fox"}, hub.path / "fox.png")


FAILED_FREE = {"state": "fail", "failCode": "500", "failMsg": "Internal Error, please try again later", "creditsConsumed": 0}
FAILED_UNKNOWN = {"state": "fail", "failCode": "500", "failMsg": "Internal Error, please try again later"}


def test_a_clip_kie_failed_for_nothing_goes_again_once_by_itself(hub, monkeypatch):
    creates = creating(monkeypatch)

    def poll(url, **kw):
        return response({"code": 200, "data": FAILED_FREE}) if kw["params"]["taskId"] == "task-1" else response(DONE)
    monkeypatch.setattr(media.requests, "get", poll)
    assert media.run("motion", {"prompt": "push in"}, hub.path / "clip.mp4")
    assert len(creates) == 2 and hub.pauses == [30]


def test_a_clip_whose_cost_kie_didnt_say_is_never_sent_again(hub, monkeypatch):
    creates = creating(monkeypatch)
    polling(monkeypatch, [response({"code": 200, "data": FAILED_UNKNOWN})])
    for _ in range(2):                                          # the second is the creator's Resume
        with pytest.raises(media.TaskFailed):
            media.run("motion", {"prompt": "push in"}, hub.path / "clip.mp4")
    assert len(creates) == 1


def refused(code, msg):
    return lambda n: response({"code": int(code), "msg": msg})


@pytest.mark.parametrize("code,msg,again_on_resume", [("402", "Insufficient credits", True),
                                                      ("401", "Invalid API key", True),
                                                      ("400", "Flagged as sensitive content", False)])
def test_a_request_refused_before_anything_was_made_goes_again_only_when_the_creator_resumes(hub, monkeypatch, code,
                                                                                             msg, again_on_resume):
    """A top-up or a fixed key, then Resume: before this the refused receipt stopped every Resume at the same picture."""
    creates = creating(monkeypatch, answer=refused(code, msg))
    with pytest.raises(media.TaskFailed):
        media.run("images", {"prompt": "a hen"}, hub.path / "hen.png")
    assert len(creates) == 1 and hub.pauses == [], "Studio never sends it again by itself"
    creates = creating(monkeypatch)                             # topped up / key fixed
    polling(monkeypatch, [response(DONE)])
    if again_on_resume:
        media.run("images", {"prompt": "a hen"}, hub.path / "hen.png")
        assert len(creates) == 1
        kept = [p.name for p in (hub.path / ".cr-tasks").glob("*.json")]
        assert any(".failed-" in name for name in kept), "the refused receipt is kept under its own name"
    else:
        with pytest.raises(media.TaskFailed):
            media.run("images", {"prompt": "a hen"}, hub.path / "hen.png")
        assert creates == []


def test_studio_never_carries_on_by_itself_past_a_receipt_only_the_creator_may_send_again(tmp_path):
    """An automatic resume (cr_recover) must not be treated as the creator's Resume by cr_media's asked rule."""
    job = tmp_path / "output" / "stories" / "_work" / "v--abcd1234"
    job.mkdir(parents=True)
    (job / "cr-project.json").write_text(json.dumps({"id": "crv_" + "1" * 24, "brain_mode": "claudecode",
                                                     "job_spec": {"title": "V", "style": "stories", "minutes": 3}}))
    import os
    import time
    (job / recover.RUN_FILE).write_text(json.dumps({"started": time.time() - 60, "studio_pid": 1, "run_id": ""}))
    tasks = job / "images" / ".cr-tasks"
    tasks.mkdir(parents=True)
    ok = lambda: recover.safe_to_resume(job, home=tmp_path, server_stages=[])     # noqa: E731
    assert ok()[0]
    (tasks / "a.json").write_text(json.dumps({"provider": "kie", "model": "google/nano-banana", "status": "failed",
                                              "rejected": True, "failure_code": "402", "failure_category": "balance",
                                              "credits": 0}))
    assert ok() == (False, "a paid task's failure needs your yes to try it again")
    os.remove(tasks / "a.json")
    (tasks / "b.json").write_text(json.dumps({"provider": "kie", "model": "google/nano-banana", "status": "failed",
                                              "rejected": True, "failure_code": "429", "failure_category": "rate_limit",
                                              "credits": 0}))
    assert ok()[0], "a rate limit is Studio's own to wait out"
