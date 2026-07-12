"""
Bridge: web dyno starts a Fly Machine per cook (scale-to-zero / pay while running).

Requires on DigitalOcean web:
  COOK_ON_WEB=0
  COOK_ON_FLY=1
  FLY_API_TOKEN=...          # fly tokens create
  FLY_COOK_APP=channelrecipe-cook
  FLY_COOK_IMAGE=...         # from `fly image show -a channelrecipe-cook`

Deploy once (see README / guide):
  fly launch --name channelrecipe-cook --region sfo --no-deploy
  fly secrets set ...
  fly deploy -c fly.cook.toml

Note: we inject Spaces/DB/API env from the web process into each Machine.
Fly app secrets stay "staged" when the app is scaled to 0, so Machines API
one-shots would otherwise cook without Spaces and "complete" with dead
/api/files URLs.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import config

_API = "https://api.machines.dev"

# Copied from the web dyno into each ephemeral cook Machine.
_INJECT_ENV_KEYS = (
    "DATABASE_URL",
    "SPACES_KEY",
    "SPACES_SECRET",
    "SPACES_BUCKET",
    "SPACES_REGION",
    "SPACES_ENDPOINT",
    "SPACES_CDN_ENDPOINT",
    "GEMINI_KEY",
    "ATLASCLOUD_KEY",
    "ATLAS_TEXT_MODEL",
    "ATLAS_PREMIUM_IMAGE_MODEL",
    "GEMINI_TEXT_MODEL",
    "CONCEPT_SEGMENTER_MODEL",
    "GROQ_API_KEY",
    "PEXELS_KEY",
    "PIXABAY_KEY",
    "SECRETS_KEY",
    "POSTHOG_KEY",
    "POSTHOG_HOST",
    "SENTRY_DSN",
    "HEYGEN_KEY",
)


def _headers() -> dict:
    token = (getattr(config, "FLY_API_TOKEN", "") or "").strip()
    if not token:
        raise RuntimeError("FLY_API_TOKEN not set")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _request(method: str, path: str, body: dict | None = None) -> dict | list | None:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{_API}{path}",
        data=data,
        headers=_headers(),
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Fly API {method} {path} → {e.code}: {err[:500]}") from e


def _latest_app_image(app: str) -> str:
    """
    Resolve the newest release image for the cook app via Fly GraphQL.
    Avoids stale FLY_COOK_IMAGE env on DigitalOcean after `fly deploy`.
    """
    token = (getattr(config, "FLY_API_TOKEN", "") or "").strip()
    if not token:
        return ""
    query = {
        "query": (
            "query ($name: String!) {"
            "  app(name: $name) {"
            "    currentRelease { imageRef }"
            "  }"
            "}"
        ),
        "variables": {"name": app},
    }
    req = urllib.request.Request(
        "https://api.fly.io/graphql",
        data=json.dumps(query).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        ref = (
            ((payload.get("data") or {}).get("app") or {})
            .get("currentRelease") or {}
        ).get("imageRef") or ""
        return str(ref).strip()
    except Exception as e:
        print(f"[fly] latest image lookup failed: {e}")
        return ""


def _machine_env() -> dict[str, str]:
    env: dict[str, str] = {
        "COOK_ON_WEB": "0",
        # Groq often 403s datacenter IPs ("check your network settings").
        # Local whisper on an ephemeral Fly box is fine — it won't freeze the web dyno.
        "ALLOW_LOCAL_WHISPER": "1",
        "APP_ENV": "fly-cook",
        # boto3 1.36+ default checksums break DigitalOcean Spaces signatures.
        "AWS_REQUEST_CHECKSUM_CALCULATION": "when_required",
        "AWS_RESPONSE_CHECKSUM_VALIDATION": "when_required",
    }
    # URLs / ids: strip all whitespace. Secrets: trim ends only (preserve + / =).
    url_keys = {
        "DATABASE_URL",
        "SPACES_BUCKET",
        "SPACES_REGION",
        "SPACES_ENDPOINT",
        "SPACES_CDN_ENDPOINT",
        "POSTHOG_HOST",
        "SENTRY_DSN",
    }
    for key in _INJECT_ENV_KEYS:
        val = (os.getenv(key) or "").strip()
        if not val and hasattr(config, key):
            val = str(getattr(config, key) or "").strip()
        if not val:
            continue
        if key in url_keys or key.endswith("_ENDPOINT") or key.endswith("_HOST"):
            val = "".join(val.split())
        else:
            val = val.strip().strip('"').strip("'")
        if val:
            env[key] = val
    return env


def spawn_cook(job_id: str) -> bool:
    """Create + start an ephemeral Fly Machine that cooks job_id then exits."""
    if not getattr(config, "COOK_ON_FLY", False):
        return False
    app = (getattr(config, "FLY_COOK_APP", "") or "").strip()
    if not app:
        print("[fly] FLY_COOK_APP missing")
        return False
    # Prefer newest Fly release so cooks pick up code right after `fly deploy`
    # without manually bumping FLY_COOK_IMAGE on DigitalOcean.
    image = _latest_app_image(app) or (getattr(config, "FLY_COOK_IMAGE", "") or "").strip()
    if not image:
        print("[fly] no cook image (FLY_COOK_IMAGE unset and release lookup failed)")
        return False
    print(f"[fly] using image {image}")
    try:
        region = (getattr(config, "FLY_COOK_REGION", "") or "sjc").strip() or "sjc"
        cpus = max(1, int(getattr(config, "FLY_COOK_CPUS", 2) or 2))
        memory_mb = max(1024, int(getattr(config, "FLY_COOK_MEMORY_MB", 4096) or 4096))
        env = _machine_env()
        if not env.get("DATABASE_URL"):
            print("[fly] DATABASE_URL missing on web — cannot spawn cook")
            return False
        if not (env.get("SPACES_KEY") and env.get("SPACES_SECRET") and env.get("SPACES_BUCKET")):
            print("[fly] SPACES_* missing on web — cook would 404 after finish")
            return False
        body = {
            "region": region,
            "config": {
                "image": image,
                "auto_destroy": True,
                "restart": {"policy": "no"},
                "guest": {
                    "cpu_kind": "shared",
                    "cpus": cpus,
                    "memory_mb": memory_mb,
                },
                "env": env,
                "init": {
                    "cmd": ["python", "-m", "webapp.fly_oneshot", job_id],
                },
            },
        }
        created = _request("POST", f"/v1/apps/{app}/machines", body)
        mid = (created or {}).get("id")
        if not mid:
            print(f"[fly] spawn returned no machine id for cook {job_id}: {created}")
            return False
        # Suspended / scale-to-zero apps sometimes leave the VM in "created".
        # Explicit start unblocks the one-shot cook.
        try:
            _request("POST", f"/v1/apps/{app}/machines/{mid}/start", {})
        except Exception as start_err:
            print(f"[fly] start after create ({mid}): {start_err}")
        print(f"[fly] spawned machine {mid} for cook {job_id} (env keys={sorted(env)})")
        return True
    except Exception as e:
        print(f"[fly] spawn failed for {job_id}: {e}")
        return False
