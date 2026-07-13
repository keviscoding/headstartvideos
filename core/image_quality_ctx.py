"""Process-local image quality for the active cook (standard | high)."""

from __future__ import annotations

from contextvars import ContextVar

_image_quality: ContextVar[str] = ContextVar("cr_image_quality", default="standard")


def set_image_quality(quality: str | None) -> None:
    q = (quality or "standard").strip().lower()
    if q in ("high", "hq", "pro"):
        q = "high"
    else:
        q = "standard"
    _image_quality.set(q)


def get_image_quality() -> str:
    return _image_quality.get()


def is_hq() -> bool:
    return get_image_quality() == "high"
