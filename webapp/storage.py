"""
File storage abstraction.

Uploads durable assets (finished videos, thumbnails) to DigitalOcean Spaces
(S3-compatible) when configured, and falls back to keeping them on the local
`output/` disk otherwise (fine for local dev, ephemeral in production).

Public API:
    is_remote()                      -> bool
    store_file(local_path, key, ...) -> public URL
    delete_key(key)                  -> None
"""
from __future__ import annotations

import mimetypes
import os
import shutil
from pathlib import Path

import config

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"

_SPACES_ENABLED = bool(
    config.SPACES_KEY and config.SPACES_SECRET and config.SPACES_BUCKET and config.SPACES_ENDPOINT
)

_client = None


def is_remote() -> bool:
    return _SPACES_ENABLED


def _get_client():
    global _client
    if _client is None:
        import boto3
        _client = boto3.client(
            "s3",
            region_name=config.SPACES_REGION or None,
            endpoint_url=config.SPACES_ENDPOINT,
            aws_access_key_id=config.SPACES_KEY,
            aws_secret_access_key=config.SPACES_SECRET,
        )
    return _client


def _public_url(key: str) -> str:
    if config.SPACES_CDN_ENDPOINT:
        base = config.SPACES_CDN_ENDPOINT.rstrip("/")
        return f"{base}/{key}"
    # Standard Spaces virtual-hosted URL: https://<bucket>.<region>.digitaloceanspaces.com/<key>
    endpoint = config.SPACES_ENDPOINT.replace("https://", "").rstrip("/")
    return f"https://{config.SPACES_BUCKET}.{endpoint}/{key}"


def store_file(local_path: str, key: str, content_type: str | None = None) -> str:
    """Persist a local file under `key` and return a URL that will serve it.

    Remote: uploads to Spaces (public-read) and returns the public URL.
    Local:  returns the existing /api/files/... URL (no copy needed).
    """
    if not content_type:
        content_type = mimetypes.guess_type(local_path)[0] or "application/octet-stream"

    if _SPACES_ENABLED:
        client = _get_client()
        client.upload_file(
            local_path,
            config.SPACES_BUCKET,
            key,
            ExtraArgs={"ACL": "public-read", "ContentType": content_type},
        )
        return _public_url(key)

    # Local fallback — serve straight from disk via the existing files route.
    rel = os.path.relpath(local_path, str(ROOT))
    return f"/api/files/{rel}"


def delete_key(key: str) -> None:
    """Best-effort delete of a stored object (remote) — no-op locally."""
    if not _SPACES_ENABLED:
        return
    try:
        _get_client().delete_object(Bucket=config.SPACES_BUCKET, Key=key)
    except Exception as e:
        print(f"[storage] delete failed for {key}: {e}")
