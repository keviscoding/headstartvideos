"""The admins' Showcase: the newest videos made with Studio, ready to post (a full copy and a 30-second snippet).

An uploader on the owner's Mac puts each video in private storage under private/showcase/<id>/ (full.mp4,
snippet.mp4, poster.jpg and meta.json). This page lists them for admins only, hands out short-lived links, and lets an
admin hide one. Nothing here is public: the objects stay private and every link is signed per request.
"""
from __future__ import annotations

import json
import re
import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

PREFIX = "private/showcase/"
LINK_SECONDS = 3600
NO_STORE = {"Cache-Control": "private, no-store"}   # the answer carries signed links
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,80}$")
_cache = {"at": 0.0, "items": []}
_lock = threading.Lock()


def _client_bucket():
    from webapp import storage
    if not storage.is_remote():
        raise HTTPException(503, "Showcase storage is not set up on this server.")
    return storage._get_client(), storage._spaces_creds()[2]


def _metas(client, bucket) -> list[dict]:
    """Every item's meta.json, newest first (read once a minute: a list page must not re-read every file)."""
    with _lock:
        if time.time() - _cache["at"] < 60:
            return _cache["items"]
        keys, token = [], None
        while True:
            kw = {"Bucket": bucket, "Prefix": PREFIX}
            if token:
                kw["ContinuationToken"] = token
            page = client.list_objects_v2(**kw)
            keys += [o["Key"] for o in page.get("Contents", []) if o["Key"].endswith("/meta.json")]
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
        items = []
        for key in keys:
            try:
                meta = json.loads(client.get_object(Bucket=bucket, Key=key)["Body"].read())
            except Exception:
                continue
            if isinstance(meta, dict) and _ID.match(str(meta.get("id") or "")):
                items.append(meta)
        items.sort(key=lambda m: str(m.get("created") or ""), reverse=True)
        _cache.update(at=time.time(), items=items)
        return items


def _link(client, bucket, item_id: str, name: str, download: str, ctype: str) -> str:
    params = {"Bucket": bucket, "Key": f"{PREFIX}{item_id}/{name}", "ResponseContentType": ctype}
    if download:
        params["ResponseContentDisposition"] = 'attachment; filename="%s"' % download.replace('"', "")
    return client.generate_presigned_url("get_object", Params=params, ExpiresIn=LINK_SECONDS)


def _safe_name(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9 ._-]+", "", title or "video").strip()[:80] or "video"


def listing() -> list[dict]:
    client, bucket = _client_bucket()
    out = []
    for m in _metas(client, bucket):
        i, name = m["id"], _safe_name(m.get("title"))
        out.append({
            "id": i, "title": m.get("title") or "", "recipe": m.get("recipe") or "", "kind": m.get("kind") or "long",
            "duration": m.get("duration"), "snippet_s": m.get("snippet_s"), "created": m.get("created"),
            "hidden": bool(m.get("hidden")), "full_mb": m.get("full_mb"), "snippet_mb": m.get("snippet_mb"),
            "poster": _link(client, bucket, i, "poster.jpg", "", "image/jpeg"),
            "snippet": _link(client, bucket, i, "snippet.mp4", "", "video/mp4"),
            "snippet_download": _link(client, bucket, i, "snippet.mp4", f"{name} (30s).mp4", "video/mp4"),
            "full_download": _link(client, bucket, i, "full.mp4", f"{name}.mp4", "video/mp4"),
        })
    return out


def set_hidden(item_id: str, hidden: bool) -> dict:
    if not _ID.match(item_id or ""):
        raise HTTPException(404, "No such video.")
    client, bucket = _client_bucket()
    key = f"{PREFIX}{item_id}/meta.json"
    try:
        meta = json.loads(client.get_object(Bucket=bucket, Key=key)["Body"].read())
    except Exception:
        raise HTTPException(404, "No such video.")
    meta["hidden"] = bool(hidden)
    client.put_object(Bucket=bucket, Key=key, Body=json.dumps(meta).encode(), ContentType="application/json",
                      ACL="private", CacheControl="private, no-store")
    with _lock:
        _cache["at"] = 0.0
    return {"id": item_id, "hidden": meta["hidden"]}


class HiddenRequest(BaseModel):
    hidden: bool


def make_router(require_admin) -> APIRouter:
    router = APIRouter(prefix="/api/admin/showcase", tags=["admin"])

    @router.get("")
    def get_showcase(user=Depends(require_admin)):
        return JSONResponse({"items": listing()}, headers=NO_STORE)

    @router.post("/{item_id}/hidden")
    def post_hidden(item_id: str, req: HiddenRequest, user=Depends(require_admin)):
        return JSONResponse(set_hidden(item_id, req.hidden), headers=NO_STORE)

    return router
