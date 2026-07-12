"""
File storage abstraction — DigitalOcean Spaces (S3-compatible).

CRITICAL: boto3>=1.36 enables default request checksums that DO Spaces
rejects with SignatureDoesNotMatch. We pin boto3<1.36 in requirements and
also force when_required here as belt-and-suspenders.
"""
from __future__ import annotations

import mimetypes
import os
import re
from pathlib import Path

# Must be set before botocore clients are created in this process.
os.environ.setdefault("AWS_REQUEST_CHECKSUM_CALCULATION", "when_required")
os.environ.setdefault("AWS_RESPONSE_CHECKSUM_VALIDATION", "when_required")

import config

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"

_client = None


def _clean_url_part(value: str) -> str:
    return "".join((value or "").split())


def _clean_secret(value: str) -> str:
    # Trim ends + wrapping quotes. Never alter interior (+ / = are valid).
    return (value or "").strip().strip('"').strip("'")


def _normalize_endpoint(endpoint: str, region: str | None) -> str:
    """Force regional Spaces API host (not bucket vhost / CDN)."""
    ep = _clean_url_part(endpoint).rstrip("/")
    m = re.match(
        r"^https?://[^./]+\.([a-z0-9]+)\.digitaloceanspaces\.com",
        ep,
        re.I,
    )
    if m:
        return f"https://{m.group(1)}.digitaloceanspaces.com"
    m2 = re.match(r"^https?://([a-z0-9]+)\.digitaloceanspaces\.com", ep, re.I)
    if m2:
        return f"https://{m2.group(1)}.digitaloceanspaces.com"
    if "cdn.digitaloceanspaces.com" in ep.lower() or "media-cf" in ep.lower():
        reg = (region or "sfo3").lower()
        return f"https://{reg}.digitaloceanspaces.com"
    if region and "digitaloceanspaces.com" not in ep.lower():
        return f"https://{_clean_url_part(region)}.digitaloceanspaces.com"
    return ep or (f"https://{region}.digitaloceanspaces.com" if region else "")


def is_remote() -> bool:
    key, secret, bucket, _region, endpoint = _spaces_creds()
    return bool(key and secret and bucket and endpoint)


def _spaces_creds() -> tuple[str, str, str, str, str]:
    """Return cleaned (key, secret, bucket, region, endpoint)."""
    key = _clean_secret(config.SPACES_KEY)
    secret = _clean_secret(config.SPACES_SECRET)
    bucket = _clean_url_part(config.SPACES_BUCKET)
    region = (_clean_url_part(config.SPACES_REGION) or "sfo3").lower()
    endpoint = _normalize_endpoint(config.SPACES_ENDPOINT, region)
    if not endpoint and region:
        endpoint = f"https://{region}.digitaloceanspaces.com"
    return key, secret, bucket, region, endpoint


def spaces_fingerprint() -> str:
    """Safe debug string for logs (no secret material)."""
    key, secret, bucket, region, endpoint = _spaces_creds()
    return (
        f"bucket={bucket!r} region={region!r} endpoint={endpoint!r} "
        f"key_len={len(key)} secret_len={len(secret)} "
        f"key_prefix={(key[:6] + '…') if len(key) >= 6 else key!r} "
        f"key_suffix={(('…' + key[-4:]) if len(key) >= 4 else key!r)} "
        f"secret_ws={any(c.isspace() for c in secret)} "
        f"secret_has_dollar={('$' in secret)} "
        f"secret_len_ok={20 <= len(secret) <= 64}"
    )


def _boto_config():
    from botocore.config import Config as BotoConfig

    # Official DO guidance: virtual addressing.
    kwargs = dict(
        signature_version="s3v4",
        s3={"addressing_style": "virtual"},
        connect_timeout=15,
        read_timeout=300,
        retries={"max_attempts": 3, "mode": "standard"},
    )
    try:
        return BotoConfig(
            **kwargs,
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        )
    except TypeError:
        return BotoConfig(**kwargs)


def _make_client():
    """Fresh client every call — avoids stale cached creds across retries."""
    import boto3
    from boto3.session import Session

    access_key, secret, _bucket, region, endpoint = _spaces_creds()
    if not all([access_key, secret, endpoint]):
        raise RuntimeError("Spaces credentials/endpoint incomplete")
    # Match DO docs exactly: session.client with region_name = Spaces region.
    session = Session()
    return session.client(
        "s3",
        region_name=region,
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret,
        config=_boto_config(),
    )


def _get_client():
    global _client
    if _client is None:
        _client = _make_client()
    return _client


def _public_url(key: str) -> str:
    key = (key or "").lstrip("/")
    if config.SPACES_CDN_ENDPOINT:
        base = _clean_url_part(config.SPACES_CDN_ENDPOINT).rstrip("/")
        return f"{base}/{key}"
    _key, _secret, bucket, region, endpoint = _spaces_creds()
    host = endpoint.replace("https://", "").replace("http://", "").rstrip("/")
    return f"https://{bucket}.{host}/{key}"


def _friendly_spaces_error(exc: Exception) -> RuntimeError:
    msg = str(exc)
    if "SignatureDoesNotMatch" in msg:
        fp = spaces_fingerprint()
        dollar_hint = ""
        if "secret_has_dollar=True" in fp:
            dollar_hint = (
                " SPACES_SECRET contains '$' — DigitalOcean App Platform may "
                "have interpolated/truncated it. Escape as $$ or regenerate "
                "a secret without '$'."
            )
        return RuntimeError(
            "Spaces rejected our upload signature (SignatureDoesNotMatch). "
            "This is the access key/secret pair on the web app — not a "
            "cook-code bug. DigitalOcean → API → Spaces Keys → Generate "
            "New Key → set BOTH SPACES_KEY and SPACES_SECRET on the web "
            "app → Redeploy."
            f"{dollar_hint} Debug: {fp}"
        )
    return RuntimeError(msg)


def store_file(local_path: str, key: str, content_type: str | None = None) -> str:
    """Persist a local file under `key` and return a public URL."""
    if not content_type:
        content_type = mimetypes.guess_type(local_path)[0] or "application/octet-stream"

    if not is_remote():
        rel = os.path.relpath(local_path, str(ROOT))
        return f"/api/files/{rel}"

    key = (key or "").lstrip("/")
    size = os.path.getsize(local_path)
    access_key, _secret, bucket, region, endpoint = _spaces_creds()

    # Low-level put_object only (upload_file / transfer manager re-enables
    # checksums even when when_required is set — see boto/boto3#4400).
    try:
        with open(local_path, "rb") as f:
            body = f.read()
        client = _make_client()
        # No ACL in the signed request — bucket should be public-read via
        # Spaces CDN / bucket policy. ACL in signature is a common DO fail.
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
        )
        print(
            f"[storage] Spaces ok key={key!r} size={size} "
            f"endpoint={endpoint!r} region={region!r} "
            f"key_prefix={access_key[:6]}…"
        )
        return _public_url(key)
    except Exception as e:
        print(
            f"[storage] Spaces upload failed key={key!r} size={size} "
            f"{spaces_fingerprint()} err={e}"
        )
        raise _friendly_spaces_error(e) from e


def probe_spaces_write() -> None:
    """Upload a tiny object to verify credentials. Raises on failure."""
    if not is_remote():
        raise RuntimeError("Spaces not configured")
    import time as _time

    key = f"tests/probe-{int(_time.time())}-{os.getpid()}.txt"
    try:
        client = _make_client()
        body = b"channelrecipe-spaces-probe"
        client.put_object(
            Bucket=_spaces_creds()[2],
            Key=key,
            Body=body,
            ContentType="text/plain",
        )
        try:
            client.delete_object(Bucket=_spaces_creds()[2], Key=key)
        except Exception:
            pass
    except Exception as e:
        print(f"[storage] Spaces probe failed ({spaces_fingerprint()}) err={e}")
        raise _friendly_spaces_error(e) from e
    print(f"[storage] Spaces probe ok ({spaces_fingerprint()})")


def fetch_to_local(path_or_url: str, dest_dir: str | Path | None = None) -> str:
    """Resolve a local path or remote HTTPS URL to a local filesystem path."""
    raw = (path_or_url or "").strip()
    if not raw:
        raise ValueError("Empty media path")

    if not raw.startswith("http://") and not raw.startswith("https://"):
        local = raw
        if local.startswith("/api/files/"):
            local = str(ROOT / local[len("/api/files/"):])
        if os.path.isfile(local):
            return local
        raise FileNotFoundError(f"Local media not found: {raw}")

    raw = "".join(raw.split())

    dest_root = Path(dest_dir) if dest_dir else (OUTPUT_DIR / "remote_cache")
    dest_root.mkdir(parents=True, exist_ok=True)
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
    """Prefer Spaces URL for cook inputs. Falls back to local path on failure."""
    if not local_path or not os.path.isfile(local_path):
        return local_path
    if not is_remote():
        return local_path
    try:
        url = store_file(local_path, key, content_type=content_type)
        print(f"[storage] staged input {key} → {url}")
        return url
    except Exception as e:
        print(f"[storage] stage_input failed, keeping local path: {e}")
        return local_path


def delete_key(key: str) -> None:
    if not is_remote():
        return
    try:
        _make_client().delete_object(Bucket=_spaces_creds()[2], Key=key)
    except Exception as e:
        print(f"[storage] delete failed for {key}: {e}")
