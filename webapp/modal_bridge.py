"""
Bridge: web dyno spawns cooks on Modal (optional).

Requires:
  COOK_ON_MODAL=1
  MODAL_TOKEN_ID / MODAL_TOKEN_SECRET (or `modal token set`)
  Deployed app: `modal deploy modal_cook.py`
"""
from __future__ import annotations

import config


def spawn_cook(job_id: str) -> bool:
    """Fire-and-forget Modal cook. Returns False if disabled or spawn fails."""
    if not getattr(config, "COOK_ON_MODAL", False):
        return False
    try:
        import modal

        fn = modal.Function.from_name(
            getattr(config, "MODAL_APP_NAME", "channelrecipe-cook"),
            "run_cook",
        )
        fn.spawn(job_id)
        print(f"[modal] spawned cook {job_id}")
        return True
    except Exception as e:
        print(f"[modal] spawn failed for {job_id}: {e}")
        return False
