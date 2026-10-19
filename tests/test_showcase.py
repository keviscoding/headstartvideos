"""The admins' Showcase lists private videos with signed links, lets an admin hide one, and refuses everyone else."""
import io, json

from fastapi.testclient import TestClient

import webapp.server as server
from webapp import showcase


class FakeS3:
    def __init__(self, objects):
        self.objects = objects

    def list_objects_v2(self, Bucket, Prefix, **kw):
        return {"Contents": [{"Key": k} for k in sorted(self.objects) if k.startswith(Prefix)], "IsTruncated": False}

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objects[Key] = Body

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"https://signed.example/{Params['Key']}?exp={ExpiresIn}&cd={Params.get('ResponseContentDisposition', '')}"


def setup(monkeypatch, user):
    meta = {"id": "orca-vs-every-sea-monster-1a2b3c4d", "title": "The Orca vs Every Sea Monster", "recipe": "3D Explainer Shorts",
            "kind": "short", "duration": 46.7, "snippet_s": 30, "created": "2026-10-19T11:00:00", "hidden": False}
    fake = FakeS3({f"private/showcase/{meta['id']}/meta.json": json.dumps(meta).encode()})
    monkeypatch.setattr(showcase, "_client_bucket", lambda: (fake, "bucket"))
    showcase._cache["at"] = 0.0
    monkeypatch.setattr(server, "require_user", lambda request: user)
    return fake, meta


def test_admins_get_signed_private_links(monkeypatch):
    fake, meta = setup(monkeypatch, {"id": 1, "email": server.config.ADMIN_EMAILS[0]})
    res = TestClient(server.app).get("/api/admin/showcase")
    assert res.status_code == 200 and res.headers["cache-control"] == "private, no-store"
    item = res.json()["items"][0]
    assert item["title"] == meta["title"] and item["kind"] == "short" and item["hidden"] is False
    assert item["snippet"].startswith("https://signed.example/private/showcase/") and "exp=3600" in item["snippet"]
    assert "attachment" in item["full_download"] and "(30s).mp4" in item["snippet_download"]


def test_an_admin_can_hide_a_video(monkeypatch):
    fake, meta = setup(monkeypatch, {"id": 1, "email": server.config.ADMIN_EMAILS[0]})
    res = TestClient(server.app).post(f"/api/admin/showcase/{meta['id']}/hidden", json={"hidden": True})
    assert res.status_code == 200 and res.json() == {"id": meta["id"], "hidden": True}
    assert json.loads(fake.objects[f"private/showcase/{meta['id']}/meta.json"])["hidden"] is True
    assert TestClient(server.app).get("/api/admin/showcase").json()["items"][0]["hidden"] is True


def test_everyone_else_is_refused(monkeypatch):
    setup(monkeypatch, {"id": 2, "email": "customer@example.com"})
    client = TestClient(server.app)
    assert client.get("/api/admin/showcase").status_code == 403
    assert client.post("/api/admin/showcase/orca-vs-every-sea-monster-1a2b3c4d/hidden", json={"hidden": True}).status_code == 403


def test_the_showcase_tab_is_for_admins_only():
    page = (server.STATIC_DIR / "index.html").read_text()
    assert 'id="nav-showcase" class="nav-btn hidden"' in page and 'id="page-showcase"' in page
    js = (server.STATIC_DIR / "app.js").read_text()
    assert "nav-showcase')?.classList.toggle('hidden', !isAdmin)" in js
