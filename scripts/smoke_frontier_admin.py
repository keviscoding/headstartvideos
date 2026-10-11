"""Import the actual private bundle and test its local boundary, without paid calls.

Requires the downloaded runtime's dependencies. The private source is extracted
into a temporary directory, never into Git or the web container.
"""
import argparse
import json
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
        assert manifest["access"] == "admin_only" and not manifest["public_release_allowed"]
        sys.path.insert(0, str(root))
        import cr_studio
        import cr_bridge
        studio = cr_studio.create_app()
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
        assert client.get("/cr-status", headers=local).status_code == 200
        assert "X-CR-Local-Token" in client.get("/", headers=local).text
        calls = []
        def mock_api(path, payload=None, settings=None):
            calls.append((path, payload))
            if path == "projects": return {"id": "crv_" + "1" * 24}
            if path == "status": return {"brain": {"connected": True}}
            if path == "brain-key": return {"connected": True, "provider": payload["provider"]}
            return {"text": "Smoke decision", "request_id": payload["request_id"], "input_tokens": 5, "output_tokens": 5}
        cr_bridge.call = mock_api
        connected = client.post("/cr-connect", headers=protected,
                                json={"key": "cr_mcp_smoke_only", "costs_accepted": True})
        assert connected.status_code == 200
        assert studio.mv.claude("Smoke outline", model="source-model") == "Smoke decision"
        assert studio.mv.claude_research("Smoke research") == "Smoke decision"
        from PIL import Image
        picture = root / "review.png"
        Image.new("RGB", (64, 64), "black").save(picture)
        assert studio.mv.claude_vision("Smoke review", [picture]) == "Smoke decision"
        assert [payload["kind"] for path, payload in calls if path == "brain"] == ["text", "research", "vision"]
        saved = client.post("/cr-provider", headers=protected,
                            json={"name": "PEXELS_API_KEY", "key": "test-local-provider-key"})
        assert saved.status_code == 200
        status = client.get("/cr-status", headers=local)
        assert status.json["providers"]["PEXELS_API_KEY"]
        assert "test-local-provider-key" not in status.text
        assert (root / ".env").stat().st_mode & 0o077 == 0
        # One form entry connects local media and, with explicit consent, the CR brain.
        saved = client.post("/cr-hub", headers=protected, json={"provider": "atlas", "key": "test-hub-provider-key",
                            "tasks": ["images", "narration", "motion", "avatars"], "use_for_brain": True})
        assert saved.status_code == 200 and "test-hub-provider-key" not in saved.text
        assert ("brain-key", {"provider": "atlas", "api_key": "test-hub-provider-key", "model": "google/gemini-3.8-flash"}) in calls
        status = client.get("/cr-status", headers=local)
        assert status.json["media_hub"]["connected"] and "test-hub-provider-key" not in status.text
        assert studio.mv._image_provider() == "channelrecipe-hub"
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
            Path(dest).write_bytes((wav if Path(dest).suffix == ".wav" else tone).read_bytes() if task == "narration" else b"stub-media")
            return "https://files.example/output"
        cr_media.run = mock_task
        studio.mv.image_gen("Smoke image", root / "smoke.png", ref_urls=["https://files.example/ref"])
        assert studio.mv._kie_still_with_url("Smoke still", root / "still.png", "still") == "https://files.example/output"
        studio.mv._kie_video_gen("https://files.example/frame", "Smoke motion", root / "smoke.mp4", "motion")
        studio.avatar._kie("kling-standard", "https://files.example/face", "https://files.example/audio", root / "presenter.mp4")
        import wavespeed
        wavespeed.run("google/nano-banana-2/edit", {"prompt": "Smoke reference", "images": ["https://files.example/ref"]}, root / "ref.png")
        assert [t for t, _, _ in media_calls] == ["images", "images", "motion", "avatars", "images"]
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
        # Account changes are blocked while a video is active.
        studio.RUNNING["slug"] = "busy"
        assert client.post("/cr-hub", headers=protected, json={"provider": "original"}).status_code == 409
        assert client.post("/cr-voice", headers=protected, json={"provider": "original"}).status_code == 409
        studio.RUNNING["slug"] = None
        studio.QUEUE.append({"slug": "queued"})
        assert client.post("/cr-voice", headers=protected, json={"provider": "original"}).status_code == 409
        studio.QUEUE.clear()
        assert client.post("/cr-voice", headers=protected, json={"provider": "original"}).status_code == 200
        assert not cr_media.active("narration") and cr_media.active("images")
        assert client.post("/cr-voice", headers=protected, json={"provider": "inherit"}).status_code == 200
        assert cr_media.active("narration") and cr_media.profile("narration")["provider"] == "atlas"
        assert client.post("/cr-hub", headers=protected, json={"provider": "original"}).status_code == 200
        assert studio.mv._image_provider() != "channelrecipe-hub"
        print("Pinned engine, onboarding, local security, CR brain and actual media/presenter hooks passed.")
        print("No provider credits used. Full-video quality and fresh-platform validation remain required.")


if __name__ == "__main__":
    main()
