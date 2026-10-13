"""A long local job prevents idle sleep only until its last reservation ends."""
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_studio


@pytest.fixture
def awake(monkeypatch):
    calls = []
    class Process:
        ended = False
        def poll(self): return 0 if self.ended else None
        def terminate(self): self.ended = True
        def wait(self, timeout): return 0
    def launch(args, **kwargs):
        p = Process()
        calls.append((args, kwargs, p))
        return p
    monkeypatch.setattr(cr_studio.sys, "platform", "darwin")
    monkeypatch.setattr(cr_studio.subprocess, "Popen", launch)
    return cr_studio.CreativeActivity(SimpleNamespace()), calls


def test_nested_work_shares_one_assertion_and_last_release_restores_sleep(awake):
    activity, calls = awake
    first, second = activity.reserve(), activity.reserve()
    assert len(calls) == 1 and calls[0][0][1:4] == ["-i", "-s", "-w"]
    assert calls[0][0][-1] == str(cr_studio.os.getpid())
    activity.release(first)
    assert not calls[0][2].ended
    activity.release(second)
    assert calls[0][2].ended and activity.awake is None
    third = activity.reserve()
    assert len(calls) == 2
    activity.release(third)


def test_assertion_restarts_if_its_process_exits_during_work(awake):
    activity, calls = awake
    first = activity.reserve()
    calls[0][2].ended = True
    second = activity.reserve()
    assert len(calls) == 2
    activity.release(first)
    activity.release(second)


def test_missing_awake_utility_does_not_stop_work(awake, monkeypatch):
    activity, _ = awake
    def fail(*args, **kwargs): raise OSError("unavailable")
    monkeypatch.setattr(cr_studio.subprocess, "Popen", fail)
    token = activity.reserve()
    assert token in activity.active and activity.awake is None
    activity.release(token)


def test_other_platforms_never_start_the_mac_utility(awake, monkeypatch):
    activity, calls = awake
    monkeypatch.setattr(cr_studio.sys, "platform", "linux")
    token = activity.reserve()
    activity.release(token)
    assert not calls
