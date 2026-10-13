"""Kie footage route compatibility against the owner's actual watch functions."""
import ast
import builtins
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local_runtime"))
import cr_watch as watch


def payload():
    return {"messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "https://files.example.test/watch.mp4"}},
        {"type": "text", "text": "Read these frames and preserve their exact shot times."}]}],
        "temperature": 0.2, "max_tokens": 12000}


@pytest.fixture
def transport():
    calls = []
    result = object()
    original = SimpleNamespace(post=lambda *a, **k: (calls.append((a, k)), result)[1],
                               get=lambda *a, **k: (a, k), RequestException=ValueError)
    module = SimpleNamespace(requests=original)
    watch.install(module)
    return module, original, calls, result


def test_only_exact_original_watch_post_changes_route_and_never_request_objects(transport):
    module, original, calls, result = transport
    body = payload()
    headers = {"Authorization": "Bearer fixture-no-real-key", "Content-Type": "application/json"}
    assert module.requests.post(watch.LEGACY_ENDPOINT, headers=headers, json=body, timeout=900) is result
    args, options = calls[0]
    assert args == (watch.WATCH_ENDPOINT,)
    assert options["json"] is body and options["headers"] is headers and options["timeout"] == 900
    assert not hasattr(original, "_cr_kie_watch_route")
    assert module.requests.RequestException is ValueError
    assert module.requests.get("https://archive.example.test", timeout=45) == (("https://archive.example.test",), {"timeout": 45})


@pytest.mark.parametrize("endpoint", [
    "https://kieai.redpandaai.co/api/file-stream-upload",
    "https://api.kie.ai/api/v1/jobs/createTask",
    "https://api.kie.ai/gemini-3-8-flash-openai/v1/chat/completions",
    "https://api.kie.ai/gemini-2.5-flash/v1/chat/completions?custom=1",
    "https://api.kie.ai/gemini-3-5-flash-openai/v1/chat/completions",
    "https://generativelanguage.googleapis.com/v1beta/models/gemini:generateContent",
    "https://llm.wavespeed.ai/v1/chat/completions",
])
def test_all_other_endpoints_are_unchanged(transport, endpoint):
    module, _, calls, _ = transport
    module.requests.post(endpoint, json=payload(), timeout=(15, 180))
    assert calls[0][0] == (endpoint,)


@pytest.mark.parametrize("model", ["claude-opus-5-5", "custom-video-model", "gemini-2.5-flash", "gemini-3-8-flash", 42])
def test_explicit_model_choices_are_never_silently_replaced(transport, model):
    module, _, calls, _ = transport
    body = payload()
    body["model"] = model
    module.requests.post(watch.LEGACY_ENDPOINT, json=body)
    assert calls[0][0] == (watch.LEGACY_ENDPOINT,)
    assert calls[0][1]["json"] is body


@pytest.mark.parametrize("body", [None, [], {}, {"messages": []},
    {"messages": [{"role": "user", "content": "ordinary text chat"}]},
    {"messages": [{"role": "user", "content": [{"type": "text", "text": "one text"}]}]},
])
def test_unrecognised_payloads_are_unchanged(transport, body):
    module, _, calls, _ = transport
    module.requests.post(watch.LEGACY_ENDPOINT, json=body)
    assert calls[0][0] == (watch.LEGACY_ENDPOINT,)


def test_install_is_idempotent_and_missing_transport_is_unchanged(transport):
    module, _, _, _ = transport
    first = module.requests
    assert watch.install(module) is first
    empty = SimpleNamespace()
    assert watch.install(empty) is None and not hasattr(empty, "requests")


@pytest.fixture
def actual_watch(tmp_path):
    source = Path(os.environ.get("FRONTIER_TEST_ENGINE_DIR", Path.home() / "Downloads/ChannelRecipe studio")) / "youtube.py"
    if not source.is_file():
        pytest.skip("Owner's private watcher source is required; it is not published with these tests.")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names = {"_chat_log", "_kie_upload", "_kie_log"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == names
    clip = tmp_path / "already-cut-source.mp4"
    clip.write_bytes(b"fixture-video-file")
    calls, slices = [], []

    class Response:
        status_code, ok = 200, True

        def __init__(self, data):
            self.data = data
            self.text = json.dumps(data)

        def json(self):
            return self.data

        def raise_for_status(self):
            return None

    def post(url, **kwargs):
        if "files" in kwargs:
            file = kwargs["files"]["file"]
            calls.append((url, {**kwargs, "file_bytes": file[1].read()}))
            return Response({"code": 200, "data": {"downloadUrl": "https://files.example.test/watch.mp4"}})
        calls.append((url, kwargs))
        return Response({"choices": [{"message": {"content": json.dumps({"video": "Actual source clip", "shots": [
            {"start": "00:01.2", "end": "00:03.4", "shows": "Original shot"}]})}}]})

    def low_copy(vid, work, span):
        slices.append((vid, work, span))
        return clip

    requests = SimpleNamespace(post=post)
    env = {"Path": Path, "json": json, "requests": requests,
           "time": SimpleNamespace(sleep=lambda _: pytest.fail("Successful watcher must not retry")),
           "_env": lambda name: "fixture-no-real-key" if name == "KIE_API_KEY" else "",
           "_low_copy": low_copy}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), env)
    module = SimpleNamespace(requests=requests)
    env["requests"] = watch.install(module)
    yield SimpleNamespace(call=env["_kie_log"], calls=calls, slices=slices, work=tmp_path)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("vid,span", [("youtubeid1", None), ("youtubeid1", (34.5, 67.8)),
                                    ("ia:original-film/original-film.mp4", None)])
def test_actual_original_upload_window_prompt_and_parser_are_preserved(actual_watch, vid, span):
    original = actual_watch
    prompt = "Watch this exact source and retain its source clock."
    value = original.call(vid, prompt, original.work, span)
    assert json.loads(value)["shots"][0]["start"] == "00:01.2"
    assert original.slices == [(vid, original.work, span)]
    assert len(original.calls) == 2
    upload, request = original.calls
    assert upload[0] == "https://kieai.redpandaai.co/api/file-stream-upload"
    assert upload[1]["file_bytes"] == b"fixture-video-file"
    # Installed studios since admin43 carry the ChannelRecipe brand in every identifier.
    assert upload[1]["data"] == {"uploadPath": "channelrecipe", "fileName": "already-cut-source.mp4"}
    assert request[0] == watch.WATCH_ENDPOINT
    assert request[1]["timeout"] == 900
    content = request[1]["json"]["messages"][0]["content"]
    assert content == [{"type": "image_url", "image_url": {"url": "https://files.example.test/watch.mp4"}},
                       {"type": "text", "text": prompt}]
    assert request[1]["headers"]["Authorization"] == "Bearer fixture-no-real-key"


def test_optional_original_module_absence_is_a_noop(monkeypatch):
    original = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name == "youtube":
            raise ModuleNotFoundError("fixture absent original module", name="youtube")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", unavailable)
    assert watch.install() is None


def test_original_dependency_absence_is_not_hidden(monkeypatch):
    original = builtins.__import__

    def dependency_missing(name, *args, **kwargs):
        if name == "youtube":
            raise ModuleNotFoundError("fixture missing dependency", name="numpy")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", dependency_missing)
    with pytest.raises(ModuleNotFoundError) as error:
        watch.install()
    assert error.value.name == "numpy"


@pytest.fixture
def actual_search(tmp_path):
    source = Path(os.environ.get("FRONTIER_TEST_ENGINE_DIR", Path.home() / "Downloads/ChannelRecipe studio")) / "youtube.py"
    if not source.is_file():
        pytest.skip("Owner's private search source is required; it is not published with these tests.")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names, constants = {"search", "_official"}, {"SKIP_WORDS", "OFFICIAL", "MAX_VIDEO_S"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names
             or isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in constants for target in node.targets)]
    calls = []
    state = SimpleNamespace(result=subprocess.CompletedProcess([], 0, "", ""), timeout=False)
    command = ["fixture-yt-dlp", "--cookies", "/private/secret/cookies.txt"]

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        if state.timeout:
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
        return state.result

    original_subprocess = SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired)
    requests = SimpleNamespace(post=lambda *_a, **_k: pytest.fail("Search must never call a paid watcher"))
    env = {"json": json, "re": re, "os": os, "subprocess": original_subprocess, "requests": requests,
           "_env": lambda _: "", "ytdlp": lambda: command,
           "blocked": lambda *_: False}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), env)
    module = SimpleNamespace(requests=requests, subprocess=original_subprocess, search=env["search"])
    watch.install(module, home=tmp_path)
    env["subprocess"] = module.subprocess
    yield SimpleNamespace(module=module, state=state, calls=calls, command=command,
                          original_subprocess=original_subprocess,
                          logfile=tmp_path / ".cr-logs" / "footage-search-diagnostics.jsonl")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


def _results(rows, *, code=0, stderr=""):
    return subprocess.CompletedProcess([], code, "\n".join(json.dumps(row) for row in rows), stderr)


def _last_diagnostic(original):
    return json.loads(original.logfile.read_text().splitlines()[-1])


def test_original_empty_search_is_explained_without_another_request(actual_search):
    original = actual_search
    assert original.module.search("private query", n=7, official=("Original Creator",)) == []
    assert len(original.calls) == 1
    assert original.calls[0] == ((original.command + ["--flat-playlist", "--no-warnings", "-j", "ytsearch7:private query"],),
                                 {"capture_output": True, "text": True, "timeout": 120})
    entry = _last_diagnostic(original)
    assert entry["returncode"] == 0 and entry["raw_entries"] == 0 and entry["accepted_entries"] == 0
    assert entry["outcome"] == "no_raw_entries"
    assert original.logfile.stat().st_mode & 0o777 == 0o600


def test_original_filters_are_preserved_and_distinguished_from_no_results(actual_search):
    original = actual_search
    original.state.result = _results([
        {"id": "one", "duration": 12, "title": "Short film", "channel": "A"},
        {"id": "two", "duration": 60, "title": "A reaction #shorts", "channel": "B"},
        {"id": "three", "duration": None, "title": "Unknown duration", "channel": "C"},
    ])
    assert original.module.search("private query") == []
    assert len(original.calls) == 1
    entry = _last_diagnostic(original)
    assert entry["raw_entries"] == 3 and entry["accepted_entries"] == 0 and entry["outcome"] == "filtered_out"


def test_original_search_returns_same_ranked_candidates_and_keeps_other_subprocesses_untouched(actual_search):
    original = actual_search
    original.state.result = _results([
        {"id": "independent", "duration": 70, "title": "Source film", "channel": "Independent"},
        {"id": "official", "duration": 90, "title": "Source film", "channel": "Original Creator"},
    ])
    assert [row["id"] for row in original.module.search("private query", official=("Original Creator",))] == ["official", "independent"]
    before = original.logfile.read_bytes()
    result = original.module.subprocess.run(["ffmpeg", "-i", "source.mp4"], capture_output=True)
    assert result is original.state.result
    assert original.logfile.read_bytes() == before
    assert len(original.calls) == 2
    entry = _last_diagnostic(original)
    assert entry["raw_entries"] == entry["accepted_entries"] == 2 and entry["outcome"] == "accepted"
    assert original.module.subprocess.TimeoutExpired is subprocess.TimeoutExpired
    assert original.original_subprocess.run is not original.module.subprocess.run


@pytest.mark.parametrize("stderr,category", [
    ("ERROR: Sign in to confirm you're not a bot", "bot_check"),
    ("ERROR: Sign in to access this content", "sign_in_required"),
    ("ERROR: HTTP Error 429: Too many requests", "rate_limited"),
    ("ERROR: Connection timed out", "network"),
    ("ERROR: Unexpected extractor failure", "unknown"),
])
def test_original_failed_search_stores_only_safe_category_and_counts(actual_search, stderr, category):
    original = actual_search
    secret = " https://private.example.test/query?token=secret /private/secret/cookies.txt key-secret"
    original.state.result = _results([], code=1, stderr=stderr + secret)
    assert original.module.search("private query key-secret") == []
    assert len(original.calls) == 1
    entry = _last_diagnostic(original)
    assert entry["outcome"] == "failed_exit" and entry["category"] == category and entry["returncode"] == 1
    assert entry["raw_entries"] == entry["accepted_entries"] == 0
    saved = original.logfile.read_text()
    assert not any(value in saved for value in ("private query", "private.example", "cookies", "key-secret", "Unexpected extractor"))


def test_original_search_timeout_stays_an_empty_result_without_retry(actual_search):
    original = actual_search
    original.state.timeout = True
    assert original.module.search("private query") == []
    assert len(original.calls) == 1
    entry = _last_diagnostic(original)
    assert entry["outcome"] == "timeout" and entry["category"] == "network" and entry["returncode"] is None


def test_optional_log_failure_never_changes_original_search(actual_search):
    original = actual_search
    original.logfile.parent.write_text("not a directory")
    assert original.module.search("private query") == []
    assert len(original.calls) == 1


def test_parallel_original_searches_keep_separate_counts_without_query_storage(actual_search):
    original = actual_search
    gate = threading.Barrier(2)

    def run(command, **kwargs):
        gate.wait(timeout=5)
        rows = [] if command[-1].endswith(":secret-one") else [
            {"id": "private-result", "duration": 90, "title": "Source film", "channel": "Creator"}]
        return _results(rows)
    original.original_subprocess.run = run
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(original.module.search, ["secret-one", "secret-two"]))
    assert [len(rows) for rows in results] == [0, 1]
    entries = [json.loads(line) for line in original.logfile.read_text().splitlines()]
    assert sorted((entry["raw_entries"], entry["accepted_entries"]) for entry in entries) == [(0, 0), (1, 1)]
    assert "secret" not in original.logfile.read_text() and "private-result" not in original.logfile.read_text()


def test_search_diagnostics_do_not_follow_an_existing_log_symlink(actual_search):
    original = actual_search
    original.logfile.parent.mkdir()
    target = original.logfile.parent.parent / "private-connection.json"
    target.write_text("existing private account")
    original.logfile.symlink_to(target)
    assert original.module.search("private query") == []
    assert target.read_text() == "existing private account"


def test_full_original_module_install_is_scoped_and_idempotent(tmp_path, monkeypatch):
    source = Path(os.environ.get("FRONTIER_TEST_ENGINE_DIR", Path.home() / "Downloads/ChannelRecipe studio")) / "youtube.py"
    if not source.is_file():
        pytest.skip("Owner's private source is required for the runtime installation smoke.")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    spec = importlib.util.spec_from_file_location("fixture_original_youtube", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setitem(sys.modules, "youtube", module)
    import requests as global_requests
    old_post = global_requests.post
    old_run = subprocess.run
    old_search = module.search
    first = watch.install(home=tmp_path)
    assert first is module.requests and module.requests is not global_requests
    assert module.search is not old_search and module.search._cr_search_diagnostics
    assert watch.install(home=tmp_path) is first
    assert global_requests.post is old_post and subprocess.run is old_run
    assert not (tmp_path / ".cr-logs").exists()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
