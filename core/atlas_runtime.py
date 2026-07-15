"""
Process-safe Atlas API key resolution.

Default: shared config.ATLASCLOUD_KEY.
Allowlisted BYOK users: override via contextvar for the duration of a request
or cook job so concurrent users never cross wires.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

import config

_atlas_override: ContextVar[str | None] = ContextVar("atlas_key_override", default=None)


def get_atlas_key() -> str:
    override = (_atlas_override.get() or "").strip()
    if override:
        return override
    return (getattr(config, "ATLASCLOUD_KEY", "") or "").strip()


@contextmanager
def use_atlas_key(key: str | None):
    """Temporarily use `key` for Atlas calls in this context (and child threads that copy context)."""
    cleaned = (key or "").strip() or None
    token = _atlas_override.set(cleaned)
    try:
        yield cleaned or get_atlas_key()
    finally:
        _atlas_override.reset(token)
