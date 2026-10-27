"""Import the actual private bundle and test its local boundary, without paid calls.

Requires the downloaded runtime's dependencies. The private source is extracted
into a temporary directory, never into Git or the web container.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="cr-admin-smoke-") as tmp:
        with zipfile.ZipFile(args.bundle) as archive:
            assert not any(Path(n).name in (".env", ".env.example") for n in archive.namelist())
            archive.extractall(tmp)
        root = Path(tmp) / "ChannelRecipe studio"
        manifest = json.loads((root / "cr-release.json").read_text())
        assert manifest["access"] == "subscriber" and manifest["public_release_allowed"]
        original_guide = (root / "FRONTIER_GUIDE.md").read_bytes()
        interactive_instructions = (root / "CLAUDE.md").read_bytes()
        operational_overrides = (Path(__file__).resolve().parents[1] / "local_runtime/CLAUDE.md").read_bytes()
        assert original_guide in interactive_instructions, "Interactive creative context must retain the complete original guide"
        assert interactive_instructions.endswith(operational_overrides), "CR account, setup and launch instructions must follow the older guide"
        sys.path.insert(0, str(root))
        import history_kit
        original_history_animation = history_kit.animate_paintings
        import cr_studio
        import cr_bridge
        import cr_claude
        import cr_mcp
        # Tests never contact the live MCP or use the operator's Claude login.
        cr_mcp.status=lambda cfg:{"connected":True,"why":""}
        cr_mcp.is_registered=lambda cfg:True
        cr_claude.status=lambda *a,**k:{"installed":True,"authenticated":True,"ready":True,"version":"2.1.291","api_provider":"firstParty","auth_method":"claude.ai"}
        studio = cr_studio.create_app()
        assert not Path(studio.mv.CC_CWD).resolve().is_relative_to(root.resolve()), "Native creative calls must keep their original neutral working directory"
        assert history_kit.animate_paintings.__cr_history_original__ is original_history_animation
        client = studio.app.test_client()
        local = {"Host": "127.0.0.1:7860"}
        page = client.get("/cr-setup", headers=local)
        assert page.status_code == 200
        token = re.search(r"const token='([^']+)'", page.text)
        assert token, "Setup needs its local request token"
        token = token.group(1)
        protected = {**local, "X-CR-Local-Token": token}
        assert client.get("/cr-setup", headers={"Host": "remote.example"}).status_code == 403
        assert client.get("/cr-status", headers={**local, "Origin": "https://remote.example"}).status_code == 403
        assert client.post("/cr-connect", headers=local, json={}).status_code == 403
        assert client.post("/api/run", headers=protected, json={"title": "Test"}).status_code == 409
        for route in ("/api/products/build","/api/farm/queue","/api/tl/test/chat"):
            assert client.post(route,headers=protected,json={}).status_code==409
        assert client.get("/cr-status", headers=local).status_code == 200
        workspace = client.get("/", headers=local)
        assert workspace.status_code == 200 and workspace.headers["X-CR-Workspace"] == "1"
        assert "ChannelRecipe" in workspace.text and "crw-form" in workspace.text
        assert "X-CR-Local-Token" in client.get("/cr-workspace/studio.js", headers=local).text
        assert client.get("/cr-workspace/studio.css", headers=local).status_code == 200
        catalogue = client.get("/cr-workspace/catalog", headers=local)
        assert catalogue.status_code == 200 and catalogue.json["channels"]
        assert all(row.get("art_url", "").startswith("/cr-workspace/art/") for row in catalogue.json["channels"])
        assert client.get("/cr-workspace/art/3d.svg", headers=local).mimetype == "image/svg+xml"

        calls = []
        def mock_api(path, payload=None, settings=None):
            calls.append((path, payload))
            if path == "projects": return {"id": "crv_" + "1" * 24}
            if path == "status": return {"version":manifest["version"],"brain": {"connected": True}}
            if path == "brain-key": return {"connected": True, "provider": payload["provider"]}
            return {"text": "Smoke decision", "request_id": payload["request_id"], "input_tokens": 5, "output_tokens": 5}
        cr_bridge.call = mock_api
        connected = client.post("/cr-connect", headers=protected,
                                json={"key": "cr_mcp_smoke_only", "costs_accepted": True})
        assert connected.status_code == 200
        # Exercise the explicit API alternative separately from the new default.
        cfg=cr_bridge.load_settings()
        cfg["brain_mode"]="channelrecipe"
        cr_bridge.save_settings(cfg)
        cr_claude.uninstall()
        cr_bridge.install(studio.mv)
        assert studio.mv.claude("Smoke outline", model="source-model") == "Smoke decision"
        assert studio.mv.claude_research("Smoke research") == "Smoke decision"
        from PIL import Image
        picture = root / "review.png"
        Image.new("RGB", (64, 64), "black").save(picture)
        assert studio.mv.claude_vision("Smoke review", [picture]) == "Smoke decision"
        assert [payload["kind"] for path, payload in calls if path == "brain"] == ["text", "research", "vision"]
        assert studio.mv.claude("Smoke 3D code", model="claude-opus-5-5", max_tokens=24000) == "Smoke decision"
        assert calls[-1][1]["model_role"] == "visual"
        assert calls[-1][1]["requested_model"] == "claude-opus-5-5"
        saved = client.post("/cr-provider", headers=protected,
                            json={"name": "PEXELS_API_KEY", "key": "test-local-provider-key"})
        assert saved.status_code == 200
        status = client.get("/cr-status", headers=local)
        assert status.json["providers"]["PEXELS_API_KEY"]
        assert "test-local-provider-key" not in status.text
        assert (root / ".env").stat().st_mode & 0o077 == 0
        # One form entry connects local media. API brains are closed (Studio 110): an old page's "use for brain" is
        # never sent to CR, and the media key is saved all the same (its read-only account check is stubbed here).
        import requests
        import cr_media
        real_get, real_data = requests.get, cr_media._data
        requests.get, cr_media._data = (lambda *a, **k: None), (lambda response, provider: {"data": 10})
        try:
            saved = client.post("/cr-hub", headers=protected, json={"provider": "atlas", "key": "test-hub-provider-key",
                                "tasks": ["images", "narration", "motion", "avatars"], "use_for_brain": True,
                                "model": "google/gemini-3.8-flash", "visual_model": "google/gemini-3.8-flash"})
        finally:
            requests.get, cr_media._data = real_get, real_data
        assert saved.status_code == 200 and "test-hub-provider-key" not in saved.text
        assert not any(path == "brain-key" for path, _ in calls)
        before_brain = len(calls)
        brain = client.post("/cr-brain", headers=protected, json={"provider": "kie", "key": "test-owned-brain-key",
                            "model": "claude-sonnet-5-5", "visual_model": "claude-opus-5-5"})
        assert brain.status_code == 410 and "test-owned-brain-key" not in brain.text
        assert len(calls) == before_brain
        assert os.getenv("KIE_API_KEY", "") != "test-owned-brain-key", "A brain key alone must not replace media keys"
        status = client.get("/cr-status", headers=local)
        assert status.json["media_hub"]["connected"] and "test-hub-provider-key" not in status.text
        assert studio.mv._image_provider() == "channelrecipe-hub"
        assert studio.features.capabilities()["base"]["claude"]["ok"]
        assert studio.features.capabilities()["base"]["voice"]["ok"]
        assert studio.features.capabilities()["features"]["ai_images"]["ok"]
        history_style = json.loads((root / "styles/history.json").read_text())
        all_on = {key: True for key in studio.features.KEYS}
        history_features = studio.features.resolve(history_style, all_on)
        assert {key for key, enabled in history_features.items() if enabled} == {"ai_images", "motion", "sound"}
        assert not any(studio.features.resolve(history_style, {key: False for key in all_on}).values())
        documentary_style = json.loads((root / "styles/documentary.json").read_text())
        documentary_features = studio.features.resolve(documentary_style, all_on)
        assert all(documentary_features[key] for key in ("stock", "motion", "ai_images", "maps", "headlines", "sound"))
        assert not any(studio.features.resolve(documentary_style, {key: False for key in all_on}).values())
        import cr_media
        cr_media.requests.get = lambda *a, **k: SimpleNamespace(status_code=200,
                                                               json=lambda: {"code": 200, "data": {"credit": 1}})
        voice_call_count = len(calls)
        assert client.post("/cr-voice", headers=protected,
                           json={"provider": "kie", "key": "test-kie-voice-key", "voice": "selected-voice"}).status_code == 200
        assert len(calls) == voice_call_count, "Voice setup must not change the cloud brain"
        assert cr_media.profile("images")["provider"] == "atlas"
        assert cr_media.profile("narration")["provider"] == "kie"
        assert client.post("/cr-voice", headers=protected, json={"provider": "kie"}).status_code == 200
        status = client.get("/cr-status", headers=local)
        assert status.json["narration"]["connected"] and status.json["narration"]["selection"] == "kie"
        assert status.json["local_version"] == manifest["version"]
        assert "test-kie-voice-key" not in status.text
        # Exercise the actual 5.3 entry points with stub task execution (no provider credits).
        media_calls = []
        tone = root / "test-tone.mp3"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=200:duration=1", str(tone)], check=True)
        wav = root / "test-tone.wav"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tone), "-ar", "24000", str(wav)], check=True)
        def mock_task(task, inputs, dest, **kwargs):
            media_calls.append((task, inputs, kwargs))
            Path(dest).write_bytes((wav if Path(dest).suffix == ".wav" else tone).read_bytes() if task == "narration" else b"stub-media" * 6000)
            return "https://files.example/output"
        cr_media.run = mock_task
        studio.mv.image_gen("Smoke image", root / "smoke.png", ref_urls=["https://files.example/ref"])
        assert studio.mv._kie_still_with_url("Smoke still", root / "still.png", "still") == "https://files.example/output"
        studio.mv._kie_video_gen("https://files.example/frame", "Smoke motion", root / "smoke.mp4", "motion")
        studio.avatar._kie("kling-standard", "https://files.example/face", "https://files.example/audio", root / "presenter.mp4")
        import wavespeed
        wavespeed.run("google/nano-banana-2/edit", {"prompt": "Smoke reference", "images": ["https://files.example/ref"]}, root / "ref.png")
        assert [t for t, _, _ in media_calls] == ["images", "images", "motion", "avatars", "images"]
        # Use actual FFmpeg + libass for a final mux, not just a -version check.
        assert cr_studio.video_tools_ready()
        mux = root / "smoke-final-mux"
        mux.mkdir()
        (mux / "style.txt").write_text("cr-smoke")
        studio.mv.STYLE_NO_DUST.add("cr-smoke")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=320x180:rate=30:duration=1", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", str(mux / "_visual.mp4")], check=True)
        subtitle = mux / "subs.srt"
        subtitle.write_text("1\n00:00:00,100 --> 00:00:00,800\nSubtitle smoke test.\n")
        mux_audio = mux / "audio.mp3"
        mux_audio.write_bytes(tone.read_bytes())
        studio.mv._final_mux(mux, mux_audio, subtitle, True)
        rendered = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(mux / "video.mp4")]))
        assert {s["codec_name"] for s in rendered["streams"]} == {"h264", "aac"}
        # Real engine subtitle correction, audio probing and split/concat helpers.
        words = [SimpleNamespace(word="Hello", start=0.1, end=0.4), SimpleNamespace(word="world.", start=0.5, end=0.8)]
        studio.mv._get_whisper = lambda: SimpleNamespace(transcribe=lambda *a, **k: ([SimpleNamespace(words=words)], None))
        job = root / "smoke-voice"
        job.mkdir()
        studio.mv.generate_voiceover("Hello world.", job, False)
        assert (job / "audio.mp3").is_file() and (job / "subs.srt").is_file()
        assert media_calls[-1][0] == "narration" and media_calls[-1][1]["voice"] == cr_media.PROVIDERS["kie"]["default_voice"]
        studio.mv._split_script_by_minutes = lambda *a: ["Hello world.", "Hello world."]
        before = len(media_calls)
        studio.mv.generate_voiceover("Hello world. Hello world.", job, True, avatar_min=1)
        assert len(media_calls) == before + 2, "The split narration must be joined, not recorded again"
        assert (job / "audio_p1.mp3").is_file() and (job / "audio_p2.mp3").is_file()
        # Single-speaker Gemini through Kie must become a real MP3, with timings.
        assert client.post("/cr-voice", headers=protected,
                           json={"provider": "kie", "model": "not-a-model"}).status_code == 400
        assert client.post("/cr-voice", headers=protected,
                           json={"provider": "kie", "model": "google/gemini-3-8-flash-tts", "voice": "not-a-voice"}).status_code == 400
        assert client.post("/cr-voice", headers=protected,
                           json={"provider": "kie", "model": "google/gemini-3-8-flash-tts"}).status_code == 200
        studio.mv._split_script_by_minutes = lambda script, *a: [script]
        gemini_job = root / "smoke-gemini-voice"
        gemini_job.mkdir()
        studio.mv.generate_voiceover("Hello world.", gemini_job, False)
        assert media_calls[-1][1]["speakers"] == [{"speaker_id": "Speaker 1", "voice_name": "Kore"}]
        assert media_calls[-1][1]["dialogue_turns"] == [{"speaker_id": "Speaker 1", "text": "Hello world."}]
        probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "json", str(gemini_job / "audio.mp3")]))
        assert probe["streams"][0]["codec_name"] == "mp3"
        # Image selection is independent, reuses a saved local Kie key, and
        # validates every paid model before saving or submitting anything.
        before = len(calls)
        assert client.post("/cr-images", headers=protected, json={"provider": "kie", "model": "missing", "fallbacks": []}).status_code == 400
        assert client.post("/cr-images", headers=protected, json={"provider": "kie", "model": "gpt-image-2", "fallbacks": ["missing"]}).status_code == 400
        assert client.post("/cr-images", headers=protected, json={"provider": "kie", "model": "gpt-image-2",
                           "fallbacks": ["nano-banana-2-1", "nano-banana-2", "gpt-image-2-5-flare"]}).status_code == 200
        assert len(calls) == before, "Image setup must not change the cloud brain"
        assert cr_media.account("images")[0] == "kie" and cr_media.account("narration")[0] == "kie"
        studio.mv.image_gen("Smoke GPT reference", root / "gpt-ref.png", ref_urls=["https://files.example/ref"])
        assert media_calls[-1][2]["model"] == "gpt-image-2-image-to-image"
        assert media_calls[-1][1]["input_urls"] == ["https://files.example/ref"]
        status = client.get("/cr-status", headers=local)
        assert status.json["images"]["model"] == "gpt-image-2" and "test-kie-voice-key" not in status.text
        # Account changes are blocked while a video is active.
        studio.RUNNING["slug"] = "busy"
        assert client.post("/cr-hub", headers=protected, json={"provider": "original"}).status_code == 409
        assert client.post("/cr-brain", headers=protected, json={"provider": "kie"}).status_code == 410   # closed
        assert client.post("/cr-voice", headers=protected, json={"provider": "original"}).status_code == 409
        assert client.post("/cr-images", headers=protected, json={"provider": "inherit"}).status_code == 409
        studio.RUNNING["slug"] = None
        studio.QUEUE.append({"slug": "queued"})
        assert client.post("/cr-voice", headers=protected, json={"provider": "original"}).status_code == 409
        assert client.post("/cr-images", headers=protected, json={"provider": "inherit"}).status_code == 409
        studio.QUEUE.clear()
        assert client.post("/cr-voice", headers=protected, json={"provider": "original"}).status_code == 200
        assert not cr_media.active("narration") and cr_media.active("images")
        assert client.post("/cr-voice", headers=protected, json={"provider": "inherit"}).status_code == 200
        assert cr_media.active("narration") and cr_media.profile("narration")["provider"] == "atlas"
        assert client.post("/cr-images", headers=protected, json={"provider": "inherit"}).status_code == 200
        assert client.post("/cr-hub", headers=protected, json={"provider": "original"}).status_code == 200
        studio.mv.IMAGE_PROVIDER = "kie"
        assert studio.mv._image_provider() == "channelrecipe-hub"
        studio.mv.image_gen("Native Kie without a create callback", root / "native.png")
        assert media_calls[-1][2]["model"] == "google/nano-banana"
        studio.mv.image_gen("Native Kie with a reference", root / "native-ref.png", ref_urls=["https://files.example/ref"])
        assert media_calls[-1][2]["model"] == "google/nano-banana-edit"
        studio.mv._kie_video_gen("https://files.example/frame", "Native motion", root / "native-motion.mp4", "motion")
        assert media_calls[-1][1] == {"prompt": "Native motion", "image_url": "https://files.example/frame", "resolution": "720p", "duration": "5"}
        assert studio.mv.KIE_VIDEO_MODEL == "bytedance/v1-pro-fast-image-to-video"
        assert not cr_media.active("motion")
        painting = root / "scene_00.png"
        Image.new("RGB", (64, 64), "black").save(painting)
        (root / "astro_scenes.json").write_text(json.dumps([{"prompt": "Original scene. Painted as a style suffix that belongs only to the still."}]))
        original_upload = cr_media.upload
        cr_media.upload = lambda *a, **k: "https://files.example/frame"
        assert history_kit.animate_paintings(studio.mv, root, [{"pic_path": str(painting), "g0": 0, "g1": 15}],
                                            {"max": 1, "model": "bytedance/v1-pro-image-to-video", "resolution": "1080p"}) == 1
        cr_media.upload = original_upload
        assert media_calls[-1][1]["duration"] == "5" and media_calls[-1][1]["resolution"] == "720p"
        assert media_calls[-1][1]["prompt"] == (history_kit.MOTION_PROMPT + " The scene: Original scene.")[:2400]
        call_count = len(media_calls)
        assert history_kit.animate_paintings(studio.mv, root, [{"pic_path": str(painting), "g0": 0, "g1": 15}], {"max": 1}) == 0
        assert len(media_calls) == call_count, "The original valid animation cache must skip paid work"
        studio.mv.IMAGE_PROVIDER = "wavespeed"
        studio.mv.KIE_API_KEY = ""
        assert not cr_media.active("images"), "A voice-only Kie choice must not replace another image provider"
        # Execute the actual pinned text, web research, vision, Folio and 3D
        # helpers through the native wrapper, retaining their exact tool/prompt
        # contracts. Only the Claude process result and MCP transport are mocked.
        import folio,threed,cr_readiness
        real_run=cr_claude._real_run
        native_calls=[]
        receipts={}
        def native_cli(command,**options):
            if command[0]!="/smoke/claude":
                return real_run(command,**options)
            native_calls.append((command,options))
            fmt=command[command.index("--output-format")+1]
            result={"type":"result","subtype":"success","result":"Native creative output",
                "usage":{"input_tokens":5,"output_tokens":8}}
            return SimpleNamespace(returncode=0,stdout="Native creative output" if fmt=="text" else json.dumps(result)+"\n",stderr="")
        def authorize(stage):
            if stage["stage_id"] in receipts:
                return {"reuse":True,"execute":False,"result_fingerprint":receipts[stage["stage_id"]]["result_fingerprint"]}
            return {"execute":True,"authorization_token":"s"*32,"selected_model":cr_claude.VISUAL_MODEL if stage["model_role"]=="visual" else cr_claude.GENERAL_MODEL}
        cr_claude.find_executable=lambda:"/smoke/claude"
        cr_claude._real_run=native_cli
        cr_claude.models_verified=lambda *a:True
        cr_bridge.authorize_stage=authorize
        cr_bridge.finish_stage=lambda receipt:receipts.setdefault(receipt["stage_id"],receipt)
        cfg=cr_bridge.load_settings()
        cfg.update(brain_mode="claudecode",local_version=manifest["version"],general_model=cr_claude.GENERAL_MODEL,visual_model=cr_claude.VISUAL_MODEL)
        cr_bridge.save_settings(cfg)
        cr_bridge._project={"id":"crv_"+"2"*24}
        cr_bridge.install(studio.mv)
        originals=cr_bridge._engine_originals[id(studio.mv)]
        assert studio.mv.claude is originals["claude"] and studio.mv.claude_vision is originals["claude_vision"]
        assert studio.mv.claude("Native text",model="claude-sonnet-5")=="Native creative output"
        assert studio.mv.claude_research("Native factual research")=="Native creative output"
        assert studio.mv.claude_vision("Native frame review",[picture])=="Native creative output"
        assert folio._call(studio.mv,"Original illustration instructions","Native Folio drawing","drawing",effort="high")=="Native creative output"
        assert folio._call(studio.mv,"Original illustration instructions","Native Folio contact sheet","review",images=[picture])=="Native creative output"
        assert folio._look(studio.mv,"Native Folio visual review",[picture])=="Native creative output"
        assert threed._cc(studio.mv,"Native 3D code",cr_claude.VISUAL_MODEL,[picture],what="smoke")=="Native creative output"
        assert len(native_calls)==7
        assert all(Path(options["cwd"]).resolve() == Path(studio.mv.CC_CWD).resolve() for _, options in native_calls)
        assert native_calls[1][0][native_calls[1][0].index("--tools")+1]=="WebSearch,WebFetch"
        assert native_calls[2][0][native_calls[2][0].index("--tools")+1]=="Read"
        folio_command=native_calls[3][0]
        assert folio_command[folio_command.index("--effort")+1]=="high"
        assert folio_command[folio_command.index("--system-prompt")+1]=="Original illustration instructions"
        assert json.loads(native_calls[4][1]["input"])["message"]["content"][0]["type"]=="image"
        assert native_calls[-1][0][native_calls[-1][0].index("--model")+1]==cr_claude.VISUAL_MODEL
        assert studio.mv.claude("Native text",model="claude-sonnet-5")=="Native creative output" and len(native_calls)==7
        # Requested stock is reported missing instead of silently disappearing.
        previous_caps=studio.features.capabilities
        cr_readiness.basic=lambda checks:{"checks":[],"claude_code":{},"ready":True}
        studio.features.capabilities=lambda:{"base":{"voice":{"ok":True}},"features":{"stock":{"ok":False,"why":"Connect Pexels."}}}
        missing=cr_readiness.preset(studio,{"local_version":manifest["version"]},{"style":"documentary","features":{"stock":True}})
        assert missing["features"]["stock"] and not missing["ready"] and "Connect Pexels." in missing["blockers"]
        studio.features.capabilities=previous_caps
        cr_claude.uninstall()
        print("Pinned native text/research/vision/Folio/3D contracts, cached recovery, setup gates, FFmpeg and media hooks passed.")
        print("No provider credits used by this package smoke check.")


if __name__ == "__main__":
    main()
