"""Expose a useful local diagnostic when the private engine's final mux fails."""
from __future__ import annotations
import os
import re
import subprocess
from pathlib import Path


def install(engine):
    # Preserve the private renderer; add contract feedback to its own reviews.
    import cr_renderer_contracts
    cr_renderer_contracts.install(engine)
    import cr_manimgx, cr_bridge
    cr_manimgx.install(engine, cr_bridge)
    original = engine._final_mux
    def final_mux(job, *args, **kwargs):
        try:
            return original(job, *args, **kwargs)
        except subprocess.CalledProcessError as error:
            detail = error.stderr or b"No diagnostic was returned."
            if isinstance(detail, bytes):
                detail = detail.decode("utf-8", errors="replace")
            detail = re.sub(r"https?://\S+", "[media URL]", detail)
            for name, value in os.environ.items():
                if value and len(value) >= 8 and (name.endswith("_KEY") or "TOKEN" in name):
                    detail = detail.replace(value, "[redacted]")
            report = Path(job) / "cr-render-error.log"
            report.touch(mode=0o600, exist_ok=True)
            report.chmod(0o600)
            report.write_text(detail[-16000:], encoding="utf-8")
            summary = " ".join(detail.splitlines()[-6:])[-1200:]
            raise RuntimeError(f"The final render failed (FFmpeg {error.returncode}). Your generated scenes and voice are saved. "
                               f"Check Video tools in Setup & accounts. Details: {summary}") from None
    engine._final_mux = final_mux
