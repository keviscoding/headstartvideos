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
    key_prefix = (key[:6] + "…") if len(key) >= 6 else repr(key)
    key_suffix = ("…" + key[-4:]) if len(key) >= 4 else repr(key)
    return (
        f"bucket={bucket!r} region={region!r} endpoint={endpoint!r} "
        f"key_len={len(key)} secret_len={len(secret)} "
        f"key_prefix={key_prefix} key_suffix={key_suffix} "
        f"secret_ws={any(c.isspace() for c in secret)} "
        f"secret_has_dollar={'$' in secret} "
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


def _object_key_from_url(url: str) -> str | None:
    """If url is one of our Spaces/CDN public URLs, return the object key."""
    raw = "".join((url or "").split())
    if not raw.startswith("http"):
        return None
    from urllib.parse import urlparse

    path = urlparse(raw).path.lstrip("/")
    if not path:
        return None
    _, _, bucket, _region, endpoint = _spaces_creds()
    cdn = _clean_url_part(config.SPACES_CDN_ENDPOINT or "").rstrip("/")
    host = urlparse(raw).netloc.lower()
    ours = {
        f"{bucket}.{endpoint.replace('https://', '').replace('http://', '').rstrip('/')}".lower(),
        f"{_clean_url_part(config.SPACES_REGION) or 'sfo3'}.digitaloceanspaces.com",
    }
    if cdn:
        ours.add(urlparse(cdn).netloc.lower())
    # channelrecipe-media.sfo3.cdn.digitaloceanspaces.com etc.
    if host not in ours and bucket not in host and "digitaloceanspaces.com" not in host:
        return None
    # Path-style: /bucket/key → strip bucket prefix
    if path.startswith(bucket + "/"):
        return path[len(bucket) + 1 :]
    return path


def _ensure_public_read(client, bucket: str, key: str) -> None:
    """Objects must be world-readable for <video src> / CDN playback."""
    try:
        client.put_object_acl(Bucket=bucket, Key=key, ACL="public-read")
    except Exception as e:
        print(f"[storage] put_object_acl public-read failed key={key!r}: {e}")


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
        # Prefer ACL on put. If Spaces rejects the signed ACL header, upload
        # private then set ACL in a second call (still required for browser play).
        try:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
                ACL="public-read",
            )
        except Exception as acl_err:
            if "SignatureDoesNotMatch" not in str(acl_err) and "InvalidArgument" not in str(acl_err):
                raise
            print(f"[storage] put with ACL failed, retrying then ACL separately: {acl_err}")
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
            )
            _ensure_public_read(client, bucket, key)
        else:
            # Some DO configs ignore ACL on put — belt-and-suspenders.
            _ensure_public_read(client, bucket, key)
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
    """Upload a tiny public object to verify credentials + public-read."""
    if not is_remote():
        raise RuntimeError("Spaces not configured")
    import time as _time
    import httpx

    key = f"tests/probe-{int(_time.time())}-{os.getpid()}.txt"
    try:
        client = _make_client()
        bucket = _spaces_creds()[2]
        body = b"channelrecipe-spaces-probe"
        try:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=body,
                ContentType="text/plain",
                ACL="public-read",
            )
        except Exception:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=body,
                ContentType="text/plain",
            )
            _ensure_public_read(client, bucket, key)
        url = _public_url(key)
        try:
            r = httpx.get(url, timeout=15, follow_redirects=True)
            if r.status_code == 403:
                raise RuntimeError(
                    "Spaces upload works but files are not publicly readable "
                    f"(HTTP 403 on {url}). Enable public-read on objects or "
                    "turn off file restriction on the Spaces bucket."
                )
            if r.status_code >= 400:
                raise RuntimeError(f"Spaces probe GET failed HTTP {r.status_code} for {url}")
        finally:
            try:
                client.delete_object(Bucket=bucket, Key=key)
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

    # Prefer authenticated Spaces GET — public CDN URLs 403 when objects
    # were uploaded without public-read (browser shows MIME-type errors).
    obj_key = _object_key_from_url(raw) if is_remote() else None
    if obj_key:
        try:
            client = _make_client()
            bucket = _spaces_creds()[2]
            tmp = dest.with_suffix(dest.suffix + ".part")
            # get_object (not download_file) — avoids transfer-manager checksums.
            obj = client.get_object(Bucket=bucket, Key=obj_key)
            with open(tmp, "wb") as f:
                body = obj["Body"]
                while True:
                    chunk = body.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
            tmp.replace(dest)
            print(f"[storage] fetched Spaces key={obj_key!r} → {dest}")
            return str(dest)
        except Exception as e:
            print(f"[storage] authenticated Spaces fetch failed ({obj_key}): {e}")

    with httpx.stream("GET", raw, timeout=120, follow_redirects=True) as resp:
        resp.raise_for_status()
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)
        tmp.replace(dest)
    print(f"[storage] fetched remote media → {dest}")
    return str(dest)


def make_key_public(key: str) -> str:
    """Set public-read on an existing object; return its public URL."""
    key = (key or "").lstrip("/")
    if not is_remote() or not key:
        return _public_url(key) if key else ""
    client = _make_client()
    bucket = _spaces_creds()[2]
    _ensure_public_read(client, bucket, key)
    return _public_url(key)


def playable_url(
    key_or_url: str,
    expires: int = 86400,
    *,
    ensure_public: bool = True,
) -> str:
    """
    URL the browser can actually play/download.
    By default flips ACL then returns a signed GET. For list endpoints pass
    ensure_public=False so History refresh stays fast (presign only).
    """
    raw = (key_or_url or "").strip()
    if not raw:
        return raw
    if not is_remote():
        return raw
    key = raw.lstrip("/")
    if raw.startswith("http://") or raw.startswith("https://"):
        extracted = _object_key_from_url(raw)
        if not extracted:
            return raw
        key = extracted
    if ensure_public:
        try:
            make_key_public(key)
        except Exception as e:
            print(f"[storage] make_key_public failed: {e}")
    try:
        return presigned_get_url(key, expires=expires)
    except Exception as e:
        print(f"[storage] presign failed, returning public URL: {e}")
        return _public_url(key)


def presigned_get_url(key_or_url: str, expires: int = 86400) -> str:
    """Temporary signed GET URL — works even when the object is private."""
    key = (key_or_url or "").lstrip("/")
    if key.startswith("http://") or key.startswith("https://"):
        extracted = _object_key_from_url(key)
        if not extracted:
            return key_or_url
        key = extracted
    if not is_remote() or not key:
        return key_or_url
    client = _make_client()
    bucket = _spaces_creds()[2]
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=max(60, int(expires)),
    )


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
