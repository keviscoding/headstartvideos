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


def _clean_url_part(value: str) -> str:
    """Strip whitespace/newlines that sneak in via DO env paste."""
    return "".join((value or "").split())


def _public_url(key: str) -> str:
    key = (key or "").lstrip("/")
    if config.SPACES_CDN_ENDPOINT:
        base = _clean_url_part(config.SPACES_CDN_ENDPOINT).rstrip("/")
        return f"{base}/{key}"
    # Standard Spaces virtual-hosted URL: https://<bucket>.<region>.digitaloceanspaces.com/<key>
    endpoint = _clean_url_part(config.SPACES_ENDPOINT).replace("https://", "").rstrip("/")
    bucket = _clean_url_part(config.SPACES_BUCKET)
    return f"https://{bucket}.{endpoint}/{key}"


def store_file(local_path: str, key: str, content_type: str | None = None) -> str:
    """Persist a local file under `key` and return a URL that will serve it.

    Remote: uploads to Spaces (public-read) and returns the public URL.
    Local:  returns the existing /api/files/... URL (no copy needed).
    """
    if not content_type:
        content_type = mimetypes.guess_type(local_path)[0] or "application/octet-stream"

    if _SPACES_ENABLED:
        from botocore.config import Config as BotoConfig
        import boto3

        # Hard timeout so a hung Spaces upload can't leave cooks stuck forever
        client = boto3.client(
            "s3",
            region_name=config.SPACES_REGION or None,
            endpoint_url=config.SPACES_ENDPOINT,
            aws_access_key_id=config.SPACES_KEY,
            aws_secret_access_key=config.SPACES_SECRET,
            config=BotoConfig(
                connect_timeout=15,
                read_timeout=120,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )
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


def fetch_to_local(path_or_url: str, dest_dir: str | Path | None = None) -> str:
    """
    Resolve a local path or remote HTTPS URL to a local filesystem path.
    Workers use this for Spaces-hosted voiceovers/thumbnails.
    """
    raw = (path_or_url or "").strip()
    if not raw:
        raise ValueError("Empty media path")

    # Already local and exists
    if not raw.startswith("http://") and not raw.startswith("https://"):
        # Strip /api/files/ prefix if present
        local = raw
        if local.startswith("/api/files/"):
            local = str(ROOT / local[len("/api/files/"):])
        if os.path.isfile(local):
            return local
        raise FileNotFoundError(f"Local media not found: {raw}")

    # Defense: DO env paste can leave \n mid-URL (host\n/key) which .strip() misses
    raw = "".join(raw.split())

    dest_root = Path(dest_dir) if dest_dir else (OUTPUT_DIR / "remote_cache")
    dest_root.mkdir(parents=True, exist_ok=True)
    # Stable-ish name from URL path
    from urllib.parse import urlparse
    import hashlib
    import httpx

    parsed = urlparse(raw)
    ext = Path(parsed.path).suffix or ".bin"
    name = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16] + ext
    dest = dest_root / name
    if dest.is_file() and dest.stat().st_size > 0:
        return str(dest)

    with httpx.stream("GET", raw, timeout=120, follow_redirects=True) as resp:
        resp.raise_for_status()
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)
        tmp.replace(dest)
    print(f"[storage] fetched remote media → {dest}")
    return str(dest)


def stage_input(local_path: str, key: str, content_type: str | None = None) -> str:
    """
    Prefer Spaces URL for cook inputs (worker-safe). Falls back to local path
    when Spaces is not configured.
    """
    if not local_path or not os.path.isfile(local_path):
        return local_path
    if not _SPACES_ENABLED:
        return local_path
    try:
        url = store_file(local_path, key, content_type=content_type)
        print(f"[storage] staged input {key} → {url}")
        return url
    except Exception as e:
        print(f"[storage] stage_input failed, keeping local path: {e}")
        return local_path


def delete_key(key: str) -> None:
    """Best-effort delete of a stored object (remote) — no-op locally."""
    if not _SPACES_ENABLED:
        return
    try:
        _get_client().delete_object(Bucket=config.SPACES_BUCKET, Key=key)
    except Exception as e:
        print(f"[storage] delete failed for {key}: {e}")
