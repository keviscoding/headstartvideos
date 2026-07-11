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
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import config

_API = "https://api.machines.dev"


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


def spawn_cook(job_id: str) -> bool:
    """Create + start an ephemeral Fly Machine that cooks job_id then exits."""
    if not getattr(config, "COOK_ON_FLY", False):
        return False
    app = (getattr(config, "FLY_COOK_APP", "") or "").strip()
    image = (getattr(config, "FLY_COOK_IMAGE", "") or "").strip()
    if not app or not image:
        print("[fly] FLY_COOK_APP / FLY_COOK_IMAGE missing")
        return False
    try:
        region = (getattr(config, "FLY_COOK_REGION", "") or "sfo").strip() or "sfo"
        cpus = max(1, int(getattr(config, "FLY_COOK_CPUS", 2) or 2))
        memory_mb = max(1024, int(getattr(config, "FLY_COOK_MEMORY_MB", 4096) or 4096))
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
                "env": {
                    "COOK_ON_WEB": "0",
                    "ALLOW_LOCAL_WHISPER": "0",
                },
                "init": {
                    "cmd": ["python", "-m", "webapp.fly_oneshot", job_id],
                },
            },
        }
        created = _request("POST", f"/v1/apps/{app}/machines", body)
        mid = (created or {}).get("id")
        print(f"[fly] spawned machine {mid} for cook {job_id}")
        return bool(mid)
    except Exception as e:
        print(f"[fly] spawn failed for {job_id}: {e}")
        return False
