"""A style study whose Studio was closed mid-study (JamesElyse20, 2026-10-29): it stops, says so, frees its clone,
offers Study again, and My styles shows it instead of "No styles of your own yet". No model, render or paid call."""
import json
import os
import subprocess
import sys
import time

import pytest

from tests.test_frontier_style_routes import PROJECT, RUNTIME, env  # noqa: F401 - the shared fixture
import cr_readiness
import cr_style_clone as clone


def _ended() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def _left_running(env, *, pid=None, launch="running", state="running"):
    """A study as a closed Studio leaves it: begun on ChannelRecipe, saved "running", no worker any more."""
    result = clone.prepare(env.home, env.approved(), cr_readiness.proof(env.checks, purpose="creative"), env.bridge)
    operation = result["operation_id"]
    folder, saved = clone._saved(env.home, operation, env.bridge)
    clone._write(folder / "launch.json", {"state": launch} if launch == "running" else {"state": launch, "returncode": -9})
    saved.update(state=state, phase="watch", project_id=PROJECT, started_at=time.time() - 600,
                 pid=_ended() if pid is None else pid)
    clone._write(folder / "operation.json", saved)
    return operation, folder


def _finishes(env):
    return [payload for name, payload in env.calls if name == "finish"]


def test_a_study_left_running_by_a_closed_studio_stops_and_frees_its_clone(env):
    operation, folder = _left_running(env)
    result = env.get(f"/cr-style-clone/{operation}/status").json
    assert result["state"] == "failed" and result["studio_closed"] is True
    assert result["stop_reason"].startswith("Studio was closed during this study")
    assert result["message"].startswith("Studio was closed during this study, so it stopped.")
    assert result["setup_review_required"] is False, "nothing to fix in setup: just study again"
    assert result["elapsed_seconds"] < 3600, "elapsed stops when the study last did anything, not now"
    # the stopped finish reached ChannelRecipe at once, so the clone stops counting
    [finish] = _finishes(env)
    assert finish["project_id"] == PROJECT and finish["status"] == "failed" and finish["error_code"] == "runtime_failed"
    assert finish["failure"]["category"] == "studio_closed" and finish["failure"]["source"] == "style_study"
    assert result["reconciliation_pending"] is False
    # Study again reuses the same links and choices
    spec = result["retry_spec"]
    assert [r["id"] for r in spec["references"]] == [r["id"] for r in clone._read(folder / "operation.json")["spec"]["references"]]
    assert spec["label"] == "My science stories" and spec["title"] == "How bees build homes"
    # settled once: a second look sends nothing more
    env.get(f"/cr-style-clone/{operation}/status")
    assert len(_finishes(env)) == 1


def test_a_worker_still_at_work_is_never_stopped(env):
    operation, folder = _left_running(env, pid=os.getpid())
    with clone._lock(folder):                         # the worker holds its folder for its whole run
        result = env.get(f"/cr-style-clone/{operation}/status").json
    assert result["state"] == "running" and "still working" in result["message"]
    assert not _finishes(env)
    # a pid reused by another program holds no lock: the study is settled
    assert env.get(f"/cr-style-clone/{operation}/status").json["state"] == "failed"


def test_a_worker_that_ended_while_studio_stayed_open_says_so(env):
    operation, _ = _left_running(env, launch="finished")
    result = env.get(f"/cr-style-clone/{operation}/status").json
    assert result["state"] == "failed" and result["studio_closed"] is False
    assert result["stop_reason"].startswith(clone.WORKER_ENDED)
    assert _finishes(env)[0]["failure"]["category"] == "crash"


def test_a_validated_study_left_behind_is_saved_as_the_style_not_stopped(env):
    operation, folder = _left_running(env, state="validated")
    saved = clone._read(folder / "operation.json")
    clone._write(folder / "ready-style.json", {"name": saved["spec"]["style_id"], "label": "My science stories",
                                               "cr_style_clone": {"operation_id": operation}})
    result = env.get(f"/cr-style-clone/{operation}/status").json
    assert result["state"] == "done" and result["style_ready"] is True
    assert (env.home / "styles" / (saved["spec"]["style_id"] + ".json")).is_file()
    assert _finishes(env)[0]["status"] == "done"


def test_studio_start_settles_every_left_study_and_sends_waiting_finishes(env):
    first, _ = _left_running(env)
    payload = env.approved()
    payload["attempt_id"] = "a" * 32
    second = clone.prepare(env.home, payload, cr_readiness.proof(env.checks, purpose="creative"), env.bridge)["operation_id"]
    folder, saved = clone._saved(env.home, second, env.bridge)
    saved.update(state="running", project_id="crv_" + "2" * 24, started_at=time.time(), pid=_ended())
    clone._write(folder / "operation.json", saved)
    # one already settled while offline: its finish still waits
    assert clone.settle_study(env.home, first, env.bridge, send=False)["finish_receipt"]
    assert clone.settle_all(env.home, env.bridge) == 1
    assert sorted(f["project_id"] for f in _finishes(env)) == [PROJECT, "crv_" + "2" * 24]
    for operation in (first, second):
        record = clone._read(clone._operation(env.home, operation) / "operation.json")
        assert record["state"] == "failed" and "finish_receipt" not in record
    assert clone.settle_all(env.home, env.bridge) == 0 and len(_finishes(env)) == 2


def test_study_again_works_the_day_after_with_the_same_choices(env):
    operation, _ = _left_running(env)
    spec = env.get(f"/cr-style-clone/{operation}/status").json["retry_spec"]
    preview = clone._folder(env.home) / ".previews" / (spec["preview_id"] + ".json")
    stale = json.loads(preview.read_text(encoding="utf-8"))
    stale["created_at"] = time.time() - 2 * 86400            # the customer comes back the next day
    preview.write_text(json.dumps(stale), encoding="utf-8")
    again_payload = {**{k: spec[k] for k in ("preview_id", "base_style", "label", "title", "brief", "budget_minutes",
                                             "max_calls", "minutes", "visual_mode")},
                     "reference_ids": [r["id"] for r in spec["references"]], "acknowledge_costs": True,
                     "attempt_id": "b" * 32}
    assert env.post("/cr-style-clone/start", again_payload).status_code == 400, "a day-old listing is refused"
    assert env.post(f"/cr-style-clone/{operation}/again", headers={}).status_code == 403
    refreshed = env.post(f"/cr-style-clone/{operation}/again").json
    assert refreshed["retry_spec"]["preview_id"] == spec["preview_id"]
    assert [name for name, _ in env.calls].count("preview") == 1, "no YouTube listing again"
    started = env.post("/cr-style-clone/start", again_payload)
    assert started.status_code == 200 and started.json["operation_id"] != operation
    # a listing that is gone is rebuilt from the study's own references
    preview.unlink()
    rebuilt = env.post(f"/cr-style-clone/{operation}/again").json["retry_spec"]
    assert (clone._folder(env.home) / ".previews" / (rebuilt["preview_id"] + ".json")).is_file()


def test_only_a_stopped_study_can_be_studied_again(env):
    result = clone.prepare(env.home, env.approved(), cr_readiness.proof(env.checks, purpose="creative"), env.bridge)
    assert env.post(f"/cr-style-clone/{result['operation_id']}/again").status_code == 409


def test_pid_alive_is_windows_safe():
    assert clone.pid_alive(os.getpid()) is True
    assert clone.pid_alive(_ended()) is False
    assert not any(clone.pid_alive(value) for value in (None, True, 0, 1, -5, "123"))
    source = (RUNTIME / "cr_style_clone.py").read_text(encoding="utf-8")
    body = source[source.index("def pid_alive"):source.index("def worker_alive")]
    # os.kill(pid, 0) ENDS a process on Windows: it is only reached after the Windows branch has returned
    assert body.index('if os.name == "nt"') < body.index("        os.kill(pid, 0)")


def test_the_pages_name_the_launcher_window_and_say_what_the_bar_is():
    page = (RUNTIME / "cr_style_clone.html").read_text(encoding="utf-8")
    # the study runs in the launcher's window, not the browser tab: no "Leave site?" false alarm
    assert "beforeunload" not in page
    assert "Keep the ChannelRecipe launcher window open until this study finishes" in page
    assert "You can close this browser tab." in page
    assert "/again'" in page and "'Study again'" in page
    script = (RUNTIME / "cr_workspace.js").read_text(encoding="utf-8")
    assert "Stopped: Studio was closed. Study again" in script and "style clones used on your plan" in script
    assert "not a study's progress" in script
    assert "customs.length || studies.length" in script, "no 'No styles of your own yet' while studies exist"
    assert "state.family === 'custom'" in script and "studies.map(studyCard)" in script, "the library's My styles tab"
    routes = (RUNTIME / "cr_style_routes.py").read_text(encoding="utf-8")
    line = routes[routes.index('print("A style study is running.'):].split("flush=True")[0]
    assert line.isascii(), "the launcher window may be a cp1252 Windows console"
    assert "launcher window" in line and "browser tab does not" in line
    assert "Open to save the finished style" in script and "Finished. Open to save the style" in script
    assert "Keep the ChannelRecipe launcher window open" in script
    studio = (RUNTIME / "cr_studio.py").read_text(encoding="utf-8")
    assert "cr_style_clone.settle_all" in studio


def test_a_finish_the_list_saved_is_sent_by_the_studys_own_status_whoever_settled_it(env, monkeypatch):
    operation, _ = _left_running(env)
    record = clone.settle_study(env.home, operation, env.bridge, send=False)   # as My styles' list does
    assert record["state"] == "failed" and record["finish_receipt"] and not _finishes(env)
    offline = []
    def finish(payload):
        offline.append(payload)
        raise RuntimeError("offline")
    monkeypatch.setattr(env.bridge, "finish_project", finish)
    assert env.get(f"/cr-style-clone/{operation}/status").json["reconciliation_pending"] is True
    env.get(f"/cr-style-clone/{operation}/status")
    assert len(offline) == 1, "tried at most once a minute"
    monkeypatch.setattr(env.bridge, "finish_project", lambda payload: env.calls.append(("finish", payload)))
    import cr_style_routes
    real = time.monotonic
    monkeypatch.setattr(cr_style_routes.time, "monotonic", lambda: real() + 120)
    assert env.get(f"/cr-style-clone/{operation}/status").json["reconciliation_pending"] is False
    assert [f["status"] for f in _finishes(env)] == ["failed"]


def test_studio_start_waits_for_a_list_holding_the_study_for_a_moment(env):
    import threading
    operation, folder = _left_running(env)
    held = clone._lock(folder)
    held.__enter__()                                    # the workspace list, reading it as Studio starts
    threading.Timer(0.8, lambda: held.__exit__(None, None, None)).start()
    assert clone.settle_all(env.home, env.bridge) == 1
    assert clone._read(folder / "operation.json")["state"] == "failed" and len(_finishes(env)) == 1


def test_a_study_whose_claude_work_finished_offers_save_completed_not_study_again(env, monkeypatch):
    monkeypatch.setattr(clone, "_recovery_checkpoint_ready", lambda folder, saved: True)
    operation, _ = _left_running(env)
    result = env.get(f"/cr-style-clone/{operation}/status").json
    assert result["state"] == "failed" and result["metadata_recovery_available"] is True
    assert "retry_spec" not in result and result["failure_code"] == "metadata_handoff_failed"
    # its server project must end failed for Save completed style to start; that save is what counts as the clone
    assert _finishes(env)[0]["status"] == "failed"
    import cr_studio_tools
    monkeypatch.setattr(cr_studio_tools, "call", lambda method, path, timeout=30: result)
    told = cr_studio_tools.clone_progress(operation)
    assert told["can_save_completed"] is True and "Save completed style" in told["next"]
    assert "Study again" not in told["next"]


def test_a_worker_this_studio_started_is_never_blamed_on_a_closed_studio(env):
    operation, folder = _left_running(env)
    clone._write(folder / "launch.json", {"state": "running", "studio": clone.PROCESS, "studio_pid": os.getpid()})
    assert env.get(f"/cr-style-clone/{operation}/status").json["state"] == "running", "its end isn't marked yet"
    clone._write(folder / "launch.json", {"state": "finished", "returncode": -9})
    result = env.get(f"/cr-style-clone/{operation}/status").json
    assert result["state"] == "failed" and result["stop_reason"].startswith(clone.WORKER_ENDED)


def test_another_running_studio_gets_a_short_grace_then_the_study_settles(env):
    operation, folder = _left_running(env)
    clone._write(folder / "launch.json", {"state": "running", "studio": "another-process", "studio_pid": os.getpid()})
    assert clone.settle_study(env.home, operation, env.bridge, send=False)["state"] == "running"
    old = time.time() - clone.LAUNCH_GRACE_S - 60
    for name in ("operation.json", "worker.log", "launch.json"):
        path = folder / name
        path.touch()
        os.utime(path, (old, old))
    record = clone.settle_study(env.home, operation, env.bridge, send=False)
    assert record["state"] == "failed" and record["stopped_by"] == "studio_closed"
