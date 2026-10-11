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
        # Exercise the actual 5.3 entry points with stub task execution (no provider credits).
        import cr_media
        media_calls = []
        tone = root / "test-tone.mp3"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=200:duration=1", str(tone)], check=True)
        def mock_task(task, inputs, dest, **kwargs):
            media_calls.append((task, inputs, kwargs))
            Path(dest).write_bytes(tone.read_bytes() if task == "narration" else b"stub-media")
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
        assert media_calls[-1][0] == "narration" and media_calls[-1][1]["voice"] == cr_media.PROVIDERS["atlas"]["default_voice"]
        studio.mv._split_script_by_minutes = lambda *a: ["Hello world.", "Hello world."]
        before = len(media_calls)
        studio.mv.generate_voiceover("Hello world. Hello world.", job, True, avatar_min=1)
        assert len(media_calls) == before + 2, "The split narration must be joined, not recorded again"
        assert (job / "audio_p1.mp3").is_file() and (job / "audio_p2.mp3").is_file()
        # Account changes are blocked while a video is active.
        studio.RUNNING["slug"] = "busy"
        assert client.post("/cr-hub", headers=protected, json={"provider": "original"}).status_code == 409
        studio.RUNNING["slug"] = None
        assert client.post("/cr-hub", headers=protected, json={"provider": "original"}).status_code == 200
        assert studio.mv._image_provider() != "channelrecipe-hub"
        print("Pinned engine, onboarding, local security, CR brain and actual media/presenter hooks passed.")
        print("No provider credits used. Full-video quality and fresh-platform validation remain required.")


if __name__ == "__main__":
    main()
