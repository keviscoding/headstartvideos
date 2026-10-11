"""Provider I/O stays separate from the packaged owner's creative function."""
import builtins
import importlib
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_history_io
import cr_media as media


def owner_process(engine, job, shots, cfg, log=print):
    """A stand-in with deliberately different selection/cache rules.

    This tests delegation to supplied code, without publishing Frontier source.
    """
    if cfg.get("off") or cfg.get("limit") == 0:
        return 0
    import requests
    from concurrent.futures import ThreadPoolExecutor
    if not __import__("os").environ.get("KIE_API_KEY"):
        return 0
    selected = [row for row in shots if row["use"]][:cfg["limit"]]

    def work(row):
        source = Path(row["path"])
        dest = source.with_suffix(".anim.mp4")
        if dest.exists():
            return 0
        try:
            log("owner model: " + cfg["model"] + "; size: " + cfg["resolution"])
            image = engine._kie_upload_image(source)
            task = requests.post("https://api.kie.ai/api/v1/jobs/createTask",
                                 headers={"Authorization": "Bearer owner-readiness-signal"},
                                 json={"model": cfg["model"], "input": {
                                     "prompt": row["prompt"], "image_url": image,
                                     "resolution": cfg["resolution"], "duration": "10", "camera_fixed": True}},
                                 timeout=60).json()
            video = engine._kie_poll(task["data"]["taskId"], timeout_s=1200)
            dest.write_bytes(requests.get(video, timeout=300).content)
            return 1
        except Exception:
            return 0

    with ThreadPoolExecutor(max_workers=4) as pool:
        return sum(pool.map(work, selected))


@pytest.fixture
def routing(tmp_path, monkeypatch):
    monkeypatch.setattr(media.cr_bridge, "SETTINGS", tmp_path / ".cr-connection.json")
    media.cr_bridge.save_settings({"costs_accepted": True, "narration": {"provider": "kie"}})
    monkeypatch.setenv("KIE_API_KEY", "customer-test-key")
    monkeypatch.setenv("ATLASCLOUD_API_KEY", "atlas-test-key")
    monkeypatch.setattr(media, "_native_images", None)
    uploaded, generated = [], []
    lock = threading.Lock()

    def upload(path, **kwargs):
        with lock:
            uploaded.append((Path(path), media.account("motion")[0], kwargs))
        # Identical uploaded bytes can share a URL: destinations must still be
        # local to their worker, not looked up through a process-wide URL map.
        return "https://files.example/shared-frame"

    def run(task, inputs, dest, **kwargs):
        with lock:
            generated.append((task, inputs, Path(dest), media.account(task)[0]))
        Path(dest).write_bytes((Path(dest).stem + "\n").encode() * 6000)
        return "https://files.example/generated-video"

    monkeypatch.setattr(media, "upload", upload)
    monkeypatch.setattr(media, "run", run)
    return tmp_path, uploaded, generated


def row(root, name, *, use=True, prompt="An owner-directed shot."):
    path = root / name
    path.write_bytes(b"image")
    return {"path": str(path), "use": use, "prompt": prompt}


def test_original_code_controls_selection_order_cache_and_full_prompt(routing):
    root, uploads, calls = routing
    long_prompt = "A" * 2300
    shots = [row(root, "skip.png", use=False), row(root, "chosen.png", prompt=long_prompt),
             row(root, "cached.png"), row(root, "outside-limit.png")]
    (root / "cached.anim.mp4").write_bytes(b"owner-cache")
    history = SimpleNamespace(animate_paintings=owner_process)
    wrapper = cr_history_io.install(history)
    assert wrapper.__cr_history_original__.__code__ is owner_process.__code__
    assert wrapper(None, root, shots, {"limit": 2}) == 1
    assert [p.name for p, _, _ in uploads] == ["chosen.png"]
    assert calls[0][1]["prompt"] == long_prompt
    assert calls[0][1]["resolution"] == "720p" and calls[0][1]["duration"] == "5"
    assert calls[0][3] == "kie"
    assert (root / "cached.anim.mp4").read_bytes() == b"owner-cache"
    assert wrapper(None, root, shots, {"limit": 2}) == 0
    assert len(calls) == 1
    assert not media.active("motion")


def test_original_function_import_scope_does_not_change_other_modules(routing):
    root, _, _ = routing
    real_requests = importlib.import_module("requests")
    real_os = importlib.import_module("os")
    original_import = builtins.__import__
    original_globals = dict(owner_process.__globals__)
    history = SimpleNamespace(animate_paintings=owner_process)
    first = cr_history_io.install(history)
    assert cr_history_io.install(history) is first
    first(None, root, [row(root, "one.png")], {"limit": 1})
    assert builtins.__import__ is original_import
    assert importlib.import_module("requests") is real_requests
    assert importlib.import_module("os") is real_os
    assert real_os.environ["KIE_API_KEY"] == "customer-test-key"
    assert all(owner_process.__globals__[key] is value for key, value in original_globals.items())


def test_concurrent_worker_routing_keeps_distinct_destinations(routing):
    root, uploads, calls = routing
    history = SimpleNamespace(animate_paintings=owner_process)
    cr_history_io.install(history)
    shots = [row(root, f"picture-{i}.png", prompt=f"Owner shot {i}") for i in range(4)]
    assert history.animate_paintings(None, root, shots, {"limit": 4}) == 4
    assert len(uploads) == len(calls) == 4
    for _, inputs, dest, provider in calls:
        assert provider == "kie"
        assert inputs["prompt"] == "Owner shot " + dest.stem.removesuffix(".anim").split("-")[-1]
        assert dest.read_bytes().startswith((dest.stem + "\n").encode())
    assert not media.active("motion")


def test_explicit_atlas_motion_uses_atlas_even_without_a_kie_key(routing, monkeypatch):
    root, uploads, calls = routing
    monkeypatch.delenv("KIE_API_KEY")
    media.cr_bridge.save_settings({"costs_accepted": True, "media_hub": {"provider": "atlas", "tasks": ["motion"]}})
    history = SimpleNamespace(animate_paintings=owner_process)
    cr_history_io.install(history)
    assert history.animate_paintings(None, root, [row(root, "atlas.png")], {"limit": 1}) == 1
    assert uploads[0][1] == calls[0][3] == "atlas"
    assert calls[0][1]["image"] == "https://files.example/shared-frame"
    assert media.profile("motion")["provider"] == "atlas"


def test_original_disabled_setting_does_not_require_an_account(routing, monkeypatch):
    root, uploads, calls = routing
    monkeypatch.delenv("KIE_API_KEY")
    history = SimpleNamespace(animate_paintings=owner_process)
    cr_history_io.install(history)
    assert history.animate_paintings(None, root, [], {"off": True}) == 0
    assert uploads == calls == []


def test_provider_stop_is_not_swallowed_by_original_exception_fallback(routing, monkeypatch):
    root, _, _ = routing
    history = SimpleNamespace(animate_paintings=owner_process)
    cr_history_io.install(history)
    def stop(*args, **kwargs):
        raise media.AccountStop("Saved task is pending; do not submit again.")
    monkeypatch.setattr(media, "motion", stop)
    with pytest.raises(media.AccountStop, match="Saved task"):
        history.animate_paintings(None, root, [row(root, "pending.png")], {"limit": 1})
    assert not (root / "pending.anim.mp4").exists()
    assert not media.active("motion")


def test_original_logs_the_authorized_model_without_changing_creative_config(routing):
    root, _, _ = routing
    history = SimpleNamespace(animate_paintings=owner_process)
    cr_history_io.install(history)
    cfg = {"limit": 1, "model": "owner-motion", "resolution": "1080p"}
    original_cfg = dict(cfg)
    logs = []
    assert history.animate_paintings(None, root, [row(root, "logged.png")], cfg, log=logs.append) == 1
    assert logs == ["owner model: bytedance/v1-pro-fast-image-to-video; size: 720p"]
    assert cfg == original_cfg
    assert history.animate_paintings._cr_original.__code__ is owner_process.__code__


def test_native_missing_key_preserves_original_skip(routing, monkeypatch):
    root, uploads, calls = routing
    monkeypatch.delenv("KIE_API_KEY")
    history = SimpleNamespace(animate_paintings=owner_process)
    cr_history_io.install(history)
    assert history.animate_paintings(None, root, [row(root, "disconnected.png")], {"limit": 1}) == 0
    assert uploads == calls == []


@pytest.mark.parametrize("cfg,cached", [({"off": True}, False), ({"limit": 0}, False), ({"limit": 1}, True)])
def test_disabled_zero_and_cached_original_runs_do_not_validate_or_bill_accounts(routing, monkeypatch, cfg, cached):
    root, uploads, calls = routing
    def unexpected_account(*args, **kwargs):
        raise AssertionError("No provider account should be used for this original no-op.")
    monkeypatch.setattr(media, "account", unexpected_account)
    history = SimpleNamespace(animate_paintings=owner_process)
    cr_history_io.install(history)
    shot = row(root, "no-op.png")
    if cached:
        (root / "no-op.anim.mp4").write_bytes(b"owner-cache")
    assert history.animate_paintings(None, root, [shot], cfg) == 0
    assert uploads == calls == []


@pytest.mark.parametrize("provider,key_env", [("kie", "KIE_API_KEY"), ("atlas", "ATLASCLOUD_API_KEY")])
def test_explicit_motion_missing_key_stops_with_setup_guidance(routing, monkeypatch, provider, key_env):
    root, uploads, calls = routing
    monkeypatch.delenv(key_env)
    media.cr_bridge.save_settings({"costs_accepted": True, "media_hub": {"provider": provider, "tasks": ["motion"]}})
    history = SimpleNamespace(animate_paintings=owner_process)
    cr_history_io.install(history)
    with pytest.raises(media.AccountStop, match="Reconnect"):
        history.animate_paintings(None, root, [row(root, "needs-account.png")], {"limit": 1})
    assert uploads == calls == []
    assert media.profile("motion")["provider"] == provider


@pytest.mark.parametrize("provider,key_env", [("kie", "KIE_API_KEY"), ("atlas", "ATLASCLOUD_API_KEY")])
@pytest.mark.parametrize("cfg,cached", [({"off": True}, False), ({"limit": 0}, False), ({"limit": 1}, True)])
def test_explicit_missing_key_does_not_validate_disabled_or_cached_runs(routing, monkeypatch, provider, key_env, cfg, cached):
    root, uploads, calls = routing
    monkeypatch.delenv(key_env)
    media.cr_bridge.save_settings({"costs_accepted": True, "media_hub": {"provider": provider, "tasks": ["motion"]}})
    def unexpected_account(*args, **kwargs):
        raise AssertionError("No provider account should be used for this original no-op.")
    monkeypatch.setattr(media, "account", unexpected_account)
    history = SimpleNamespace(animate_paintings=owner_process)
    cr_history_io.install(history)
    shot = row(root, "selected-no-op.png")
    if cached:
        (root / "selected-no-op.anim.mp4").write_bytes(b"x" * 60_000)
    assert history.animate_paintings(None, root, [shot], cfg) == 0
    assert uploads == calls == []
