"""Import the actual private bundle and test its local boundary, without paid calls.

Requires the downloaded runtime's dependencies. The private source is extracted
into a temporary directory, never into Git or the web container.
"""
import argparse
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path


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
        print("Pinned engine import, onboarding, local access checks, provider saving and CR text/research/vision bridge passed.")
        print("No provider credits used. Full-video quality and fresh-platform validation remain required.")


if __name__ == "__main__":
    main()
