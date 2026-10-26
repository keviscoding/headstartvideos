"""Actual media previews are local, exact-style, bounded and non-generating."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import threading
from types import SimpleNamespace

from flask import Flask, jsonify
from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "local_runtime"))
import cr_preview as preview
import cr_style_clone as clone
import cr_workspace as workspace

BASE = "http://127.0.0.1:7860"
STYLE = "cr_clone_" + "a" * 12
OPERATION = "op_" + "a" * 24


def jpeg():
    buffer = io.BytesIO()
    Image.new("RGB", (160, 90), "#415573").save(buffer, "JPEG")
    return buffer.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    bridge = SimpleNamespace(load_settings=lambda: {"key": "cr_mcp_synthetic", "url": "https://channelrecipe.com"})
    def forbidden(*args, **kwargs):
        pytest.fail("A media preview requested a model/cloud/download/scene render.")
    bridge.call = bridge._mcp_tool = bridge.prepare_runtime = forbidden
    monkeypatch.setattr(workspace, "cr_bridge", bridge)
    app = Flask(__name__)
    rows = [{"name": "3d", "sample": False}, {"name": STYLE, "custom": True, "label": "My animation"}]
    styles = {"3d": {"look": {"reference": "unrelated.jpg"}}, STYLE: {"cr_style_clone": {"operation_id": OPERATION}}}
    jobs, videos = {}, {}
    @app.get("/")
    def root():
        return "Original UI"
    @app.get("/api/channels")
    def channels():
        return jsonify(channels=rows, features=[], caps={})
    engine = SimpleNamespace(find_job=lambda slug: jobs.get(slug), final_video=lambda job: videos.get(Path(job), Path(job) / "video.mp4"),
        list_jobs=lambda: list(jobs.values()))
    studio = SimpleNamespace(app=app, styles=SimpleNamespace(STYLES=styles), mv=engine)
    activity = SimpleNamespace(lock=threading.RLock(), busy=lambda: False)
    workspace.install(studio, activity, token="synthetic-local-token", home=tmp_path)
    return SimpleNamespace(home=tmp_path, bridge=bridge, studio=studio, app=app, jobs=jobs, videos=videos, rows=rows, styles=styles,
        get=lambda path, **kwargs: app.test_client().get(path, base_url=BASE, **kwargs))


def packaged(env, style="3d", *, data=None):
    root = env.home / "cr_recipe_frames"
    root.mkdir(exist_ok=True)
    image = root / (style + ".jpg")
    image.write_bytes(jpeg() if data is None else data)
    manifest = root / "manifest.json"
    saved = json.loads(manifest.read_text()) if manifest.exists() else {"schema": 2, "kind": "style_reference", "frames": {}}
    saved["frames"][style] = {"file": image.name, "kind": "style_reference", "channel": "Reference Channel",
        "title": "A reference video", "url": "https://www.youtube.com/watch?v=reference",
        "sha256": hashlib.sha256(image.read_bytes()).hexdigest()}
    manifest.write_text(json.dumps(saved))
    return image


def project(env, slug="my-film", style="3d", *, modified=None):
    job = env.home / "output" / style / "_work" / slug
    job.mkdir(parents=True)
    (job / "style.txt").write_text(style)
    video = env.home / "output" / style / "My published film" / (slug + ".mp4")
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"local-video-test")
    if modified is not None:
        import os
        os.utime(video, (modified, modified))
    env.jobs[slug], env.videos[job] = job, video
    return job, video


def completed_study(env, *, foreign=False, reverted=False):
    folder = clone._operation(env.home, OPERATION)
    (folder / "genkit").mkdir(parents=True)
    (folder / "genkit/kit.js").write_text("a validated animation kit")
    (folder / "genkit/kit.json").write_text('{"name":"synthetic-kit"}')
    style_path = env.home / "styles" / (STYLE + ".json")
    style_path.parent.mkdir(exist_ok=True)
    style_path.write_text(json.dumps(env.styles[STYLE]))
    spec = {"style_id": STYLE, "brief": "private user brief"}
    saved = {"operation_id": OPERATION, "owner": "foreign" if foreign else clone._owner(env.bridge), "spec": spec,
        "state": "complete", "style_sha256": clone._file_hash(style_path)}
    (folder / "operation.json").write_text(json.dumps(saved))
    record = {"browser": True, "types": ["timecard", "diorama"],
        "critique": {"after": {"score": 8}, "reverted": reverted}}
    (folder / "validated-checkpoint.json").write_text(json.dumps({"spec_sha256": clone._hash(spec), "record": record,
        "assets": {name: clone._file_hash(folder / name) for name in ("genkit/kit.js", "genkit/kit.json")}}))
    target = folder / "reflearn/frames_v3/diorama_1.jpg"
    target.parent.mkdir(parents=True)
    target.write_bytes(jpeg())
    reference = folder / "reflearn/references/external/ref_frames/real-reference.jpg"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(jpeg())
    return folder, target


def test_real_packaged_screenshot_catalog_and_route_with_no_decode(env, monkeypatch):
    target = packaged(env)
    monkeypatch.setattr(preview.subprocess, "run", lambda *a, **k: pytest.fail("A packaged JPEG launched FFmpeg."))
    row = env.get("/cr-workspace/catalog").json["channels"][0]
    assert row["preview_available"] is True
    assert row["preview_url"].startswith("/cr-workspace/preview/recipe/3d.jpg?v=")
    # Recipe cards show a labelled style reference that credits its channel.
    assert row["preview_video_url"] is None and row["preview_label"] == "Style reference · Reference Channel"
    response = env.get(row["preview_url"])
    assert response.status_code == 200 and response.data == target.read_bytes()
    assert response.mimetype == "image/jpeg" and response.headers["X-CR-Preview"] == "Style reference · Reference Channel"
    # The URL carries the picture's version, so the browser keeps it.
    assert response.headers["Cache-Control"] == "private, max-age=31536000, immutable"
    assert str(env.home) not in env.get("/cr-workspace/catalog").text
    # A changed picture gets a new URL; the old one is never shown again.
    changed = packaged(env, data=jpeg() + b"changed")
    os.utime(changed, (changed.stat().st_atime, changed.stat().st_mtime + 5))
    assert env.get("/cr-workspace/catalog").json["channels"][0]["preview_url"] != row["preview_url"]


def test_no_video_sample_does_not_substitute_art_or_reference_picture(env):
    reference = env.home / "assets/unrelated.jpg"
    reference.parent.mkdir()
    reference.write_bytes(jpeg())
    env.rows[0]["reference"] = True
    row = env.get("/cr-workspace/catalog").json["channels"][0]
    assert row["preview_available"] is False and row["preview_url"] is None and row["preview_video_url"] is None
    assert env.get("/cr-workspace/preview/recipe/3d.jpg").status_code == 404


def test_retired_engine_samples_and_old_frames_are_never_previews(env):
    # The old brand's sample films and schema 1 sample frames are retired.
    sample = env.home / "assets/samples/actual.webm"
    sample.parent.mkdir(parents=True)
    sample.write_bytes(b"sample-test")
    env.styles["3d"]["look"]["sample"] = "samples/actual.webm"
    (env.home / "samples").mkdir()
    (env.home / "samples/3d.mp4").write_bytes(b"old sample")
    old = env.home / "cr_recipe_frames"
    old.mkdir()
    (old / "3d.jpg").write_bytes(jpeg())
    (old / "manifest.json").write_text(json.dumps({"schema": 1, "frames": {"3d": {"file": "3d.jpg", "kind": "video_frame",
        "sha256": hashlib.sha256((old / "3d.jpg").read_bytes()).hexdigest(), "source_sha256": "b" * 64}}}))
    assert preview.recipe_source(env.studio, env.home, "3d", env.bridge) is None
    row = env.get("/cr-workspace/catalog").json["channels"][0]
    assert row["preview_available"] is False and row["preview_video_url"] is None


def test_latest_finished_output_for_exact_style_wins_and_uses_published_movie(env):
    packaged(env)
    _, old = project(env, "old", modified=1)
    _, current = project(env, "current", modified=2)
    _, unrelated = project(env, "unrelated", style="other", modified=3)
    result = preview.recipe_source(env.studio, env.home, "3d", env.bridge)
    assert result.path == current and result.path != unrelated and result.path != old
    assert result.video_url == "/f/video/current"
    assert preview.project_source(env.studio, env.home, "old").path == old


def test_completed_owned_study_uses_accepted_own_animation_frames(env):
    _, target = completed_study(env)
    source = preview.recipe_source(env.studio, env.home, STYLE, env.bridge)
    assert source.path == target and source.label == "Animation preview" and source.video_url is None
    row = env.get("/cr-workspace/catalog").json["channels"][1]
    assert row["preview_label"] == "Animation preview" and row["preview_available"] is True
    assert env.get(row["preview_url"]).data == target.read_bytes()


@pytest.mark.parametrize("changed", ["owner", "state", "kit", "published", "reverted"])
def test_foreign_incomplete_replaced_or_reverted_study_does_not_expose_frames(env, changed):
    folder, _ = completed_study(env, foreign=changed == "owner", reverted=changed == "reverted")
    if changed == "state":
        path = folder / "operation.json"
        data = json.loads(path.read_text()); data["state"] = "failed"; path.write_text(json.dumps(data))
    elif changed == "kit":
        (folder / "genkit/kit.js").write_text("a changed, unreviewed kit")
    elif changed == "published":
        (env.home / "styles" / (STYLE + ".json")).write_text('{"private":"other style"}')
    assert preview.recipe_source(env.studio, env.home, STYLE, env.bridge) is None
    assert env.get("/cr-workspace/preview/recipe/" + STYLE + ".jpg").status_code == 404


def test_preview_routes_reject_remote_host_origin_hidden_styles_and_arbitrary_paths(env):
    packaged(env)
    env.styles["hidden"] = {"look": {}}
    for path in ("/cr-workspace/preview/recipe/3d.jpg", "/cr-workspace/preview/project/my-film.jpg"):
        assert env.app.test_client().get(path, base_url="http://evil.example:7860").status_code == 403
        assert env.get(path, headers={"Origin": "https://evil.example"}).status_code == 403
    assert env.get("/cr-workspace/preview/recipe/hidden.jpg").status_code == 404
    assert env.get("/cr-workspace/preview/project/..jpg").status_code == 400
    assert env.get("/cr-workspace/preview/project/%2e%2e%2fprivate.jpg").status_code in {400, 404}
    assert env.get("/cr-workspace/preview/recipe/%2e%2e%2fprivate.jpg").status_code in {400, 404}


def test_tampered_manifest_unknown_kind_wrong_identity_or_symlink_never_serves(env):
    target = packaged(env)
    target.write_bytes(b"private credential")
    assert env.get("/cr-workspace/preview/recipe/3d.jpg").status_code == 404
    packaged(env)
    data = json.loads((target.parent / "manifest.json").read_text())
    data["frames"]["3d"]["file"] = "../private.jpg"
    (target.parent / "manifest.json").write_text(json.dumps(data))
    assert env.get("/cr-workspace/preview/recipe/3d.jpg").status_code == 404
    packaged(env)
    outside = env.home / "private.jpg"; outside.write_bytes(jpeg())
    target.unlink(); target.symlink_to(outside)
    assert env.get("/cr-workspace/preview/recipe/3d.jpg").status_code == 404


def test_project_escape_or_symlink_cannot_feed_decoder(env, monkeypatch):
    job, video = project(env)
    outside = env.home / "private.mp4"; outside.write_bytes(b"private")
    video.unlink(); video.symlink_to(outside)
    monkeypatch.setattr(preview.subprocess, "run", lambda *a, **k: pytest.fail("A private file reached FFmpeg."))
    assert env.get("/cr-workspace/preview/project/my-film.jpg").status_code == 404
    env.jobs["escape"] = env.home.parent
    assert env.get("/cr-workspace/preview/project/escape.jpg").status_code == 404


def test_decode_is_local_bounded_cached_and_invalidated_by_movie_change(env):
    _, video = project(env)
    source = preview.project_source(env.studio, env.home, "my-film")
    calls = []
    def decode(command, **kwargs):
        calls.append((command, kwargs))
        Path(command[-1]).write_bytes(jpeg())
        return SimpleNamespace(returncode=0)
    first = preview.screenshot(env.home, source, run=decode)
    second = preview.screenshot(env.home, source, run=decode)
    assert first == second and len(calls) == 1 and first.read_bytes() == jpeg()
    command, kwargs = calls[0]
    assert command[command.index("-i") + 1] == str(video)
    assert command[command.index("-protocol_whitelist") + 1] == "file,pipe"
    assert command[command.index("-frames:v") + 1] == "1"
    assert command.count("-threads") == 2 and 0 < kwargs["timeout"] <= 10
    assert kwargs["stderr"] == subprocess.DEVNULL and kwargs["stdin"] == subprocess.DEVNULL
    video.write_bytes(b"changed local movie")
    third = preview.screenshot(env.home, source, run=decode)
    assert third != first and len(calls) == 2
    assert not list((env.home / ".cr-workspace-previews").glob(".*.jpg"))


def test_decoder_timeout_short_video_busy_and_cache_limits_are_bounded(env, monkeypatch):
    _, _ = project(env)
    source = preview.project_source(env.studio, env.home, "my-film")
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("ffmpeg", kwargs["timeout"])
    with pytest.raises(preview.Unavailable):
        preview.screenshot(env.home, source, run=timeout)
    assert not list((env.home / ".cr-workspace-previews").glob("*.jpg"))
    calls = []
    def short(command, **kwargs):
        calls.append(command[command.index("-ss") + 1])
        if calls[-1] == "0":
            Path(command[-1]).write_bytes(jpeg())
        return SimpleNamespace(returncode=0)
    assert preview.screenshot(env.home, source, run=short).is_file() and calls == ["8", "1", "0"]
    source.path.write_bytes(b"new movie identity")
    monkeypatch.setattr(preview, "_WORKERS", SimpleNamespace(acquire=lambda **kwargs: False))
    with pytest.raises(preview.Busy):
        preview.screenshot(env.home, source, run=short)
    root = env.home / ".cr-workspace-previews"
    for index in range(170):
        (root / (str(index) + ".jpg")).write_bytes(jpeg())
    preview._prune(root)
    assert len(list(root.glob("*.jpg"))) <= preview._CACHE_FILES


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="Local FFmpeg is unavailable")
def test_actual_ffmpeg_extracts_finished_movie_frame_without_changing_project(env):
    job, video = project(env)
    result = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
        "-i", "color=c=0x255b99:s=160x90:r=5", "-t", "1.4", "-threads", "1", "-c:v", "libx264", str(video)],
        timeout=10, capture_output=True)
    assert result.returncode == 0
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    before = sorted(p.name for p in job.iterdir())
    response = env.get("/cr-workspace/preview/project/my-film.jpg")
    assert response.status_code == 200 and response.mimetype == "image/jpeg"
    image = Image.open(io.BytesIO(response.data))
    assert image.size == (640, 360)
    red, green, blue = image.convert("RGB").getpixel((320, 180))
    assert blue > green > red
    assert hashlib.sha256(video.read_bytes()).hexdigest() == digest
    assert sorted(p.name for p in job.iterdir()) == before


def test_a_learned_style_shows_the_sample_its_creator_chose(env):
    sample = env.home / "assets/styles/learned" / STYLE / "style.jpg"
    sample.parent.mkdir(parents=True)
    sample.write_bytes(jpeg())
    env.styles[STYLE]["look"] = {"style_image": f"styles/learned/{STYLE}/style.jpg"}
    source = preview.recipe_source(env.studio, env.home, STYLE, env.bridge)
    assert source.path == sample.resolve() and source.label == "Your style sample"
    env.styles[STYLE]["look"] = {"style_image": "styles/learned/../../../outside.jpg"}
    assert preview.recipe_source(env.studio, env.home, STYLE, env.bridge) is None


def test_a_ready_made_recipe_shows_a_picture_studio_drew(env):
    image = packaged(env)
    manifest = env.home / "cr_recipe_frames/manifest.json"
    saved = json.loads(manifest.read_text())
    saved["frames"]["3d"] = {"file": "3d.jpg", "kind": "studio_sample", "sha256": hashlib.sha256(image.read_bytes()).hexdigest()}
    manifest.write_text(json.dumps(saved))
    assert preview.recipe_source(env.studio, env.home, "3d", env.bridge).label == "Made with ChannelRecipe Studio"


def test_a_style_written_in_code_shows_its_own_renderers_test_shot_never_the_kits_title_card(env):
    """The owner's Windows test: the recipe card's "Animation preview" of a coded style was the graphics kit's photo
    title card, which its videos never show. A coded style's card shows its renderer's own test shot."""
    folder, kit_frame = completed_study(env)               # a 108 study also wrote a kit and its frames
    coded = {"look": {"visuals": "cr_animated2d", "animated2d": {"renderer": {"file": "styles/learned/x/renderer-v1.js",
                                                                              "state": "passed"}}},
             "cr_style_clone": {"operation_id": OPERATION}}
    env.styles[STYLE] = coded
    style_path = env.home / "styles" / (STYLE + ".json")
    style_path.write_text(json.dumps(coded))
    saved = json.loads((folder / "operation.json").read_text())
    saved["style_sha256"] = clone._file_hash(style_path)
    (folder / "operation.json").write_text(json.dumps(saved))
    assert preview.recipe_source(env.studio, env.home, STYLE, env.bridge) is None, "never the kit's frame"
    shot = folder / "renderer/round-2/shot_0.jpg"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(jpeg())
    (folder / "renderer/build.json").write_text(json.dumps({"state": "passed", "stills": ["round-2/shot_0.jpg"]}))
    source = preview.recipe_source(env.studio, env.home, STYLE, env.bridge)
    assert source.path == shot.resolve() and source.path != kit_frame and source.label == "Animation preview"
    row = env.get("/cr-workspace/catalog").json["channels"][1]
    assert row["preview_label"] == "Animation preview" and env.get(row["preview_url"]).data == shot.read_bytes()
    (env.home / "styles" / (STYLE + ".json")).write_text('{"private":"other style"}')
    assert preview.recipe_source(env.studio, env.home, STYLE, env.bridge) is None, "only the study's own saved style"
